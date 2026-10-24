"""Compiler: pure functions from a pipeline spec to Hera objects. See DESIGN.md section 7.

No I/O, no environment reads, no globals. Hera objects are constructed explicitly rather than
with Hera's context managers, which keep module-level state.
"""

import dataclasses
from collections.abc import Callable, Mapping

from hera.workflows import DAG, Parameter, Task, WorkflowTemplate
from hera.workflows.models import Arguments, LifecycleHook
from hera.workflows.models import Parameter as ParameterModel

from stagecraft.compile.placeholders import lower, lower_model
from stagecraft.errors import CompileError
from stagecraft.spec import HANDLER_FIELDS, Block, Param, Pipeline, Stage

ENTRYPOINT = "main"
EXIT_HANDLER = "on-exit"

# `{{ step.* }}` in pipeline-level handlers refers to the workflow itself.
_WORKFLOW_STEP = {"name": "{{workflow.name}}", "status": "{{workflow.status}}"}

# Handlers run from a generated DAG whose tasks are guarded by a `when` on the step's status: the
# workflow's `onExit` for pipeline handlers, a task `exit` hook for stage and block handlers.
# Verified on Argo 4.1.6: expression hooks on a sub-DAG task never fire when another task depends
# on it, while `exit` hooks fire and can pass `{{tasks.<name>.status}}`. A handler template is
# compiled per outcome so `{{ step.status }}` can be fixed in it; Error is reported as Failed.
_CONDITIONS = {
    "succeeded": "{status} == Succeeded",
    "failed": "{status} == Failed || {status} == Error",
}
_OUTCOME_STATUS = {"succeeded": "Succeeded", "failed": "Failed"}
_HANDLER_OUTCOMES = {
    "on_success": ["succeeded"],
    "on_failure": ["failed"],
    "finally_": ["succeeded", "failed"],
}
_STATUS_INPUT = "status"


def compile_pipeline(pipeline: Pipeline) -> WorkflowTemplate:
    """Compile a pipeline to a WorkflowTemplate.

    Each stage becomes a sub-DAG template of parallel block tasks, invoked as one task in the
    entrypoint DAG. Stage N depends on stage N-1, one edge per stage. Stage and block handlers run
    from task `exit` hooks, pipeline handlers from the workflow's `onExit`.
    """
    params = {p.name for p in pipeline.params}
    templates = []
    stage_tasks = []
    previous: Stage | None = None
    for stage in pipeline.stages:
        where = f"pipeline {pipeline.name!r}, stage {stage.name!r}"
        stage_dag, block_templates = _compile_stage(pipeline, stage, params)
        hooks, hook_templates = _compile_exit_hook(
            stage, task=stage.name, path=stage.name, params=params, where=where
        )
        templates += [stage_dag, *block_templates, *hook_templates]
        stage_tasks.append(
            Task(
                name=stage.name,
                template=stage_dag,
                depends=previous.name if previous else None,
                when=_lower_when(stage.when, params, where),
                hooks=hooks,
            )
        )
        previous = stage
    templates.insert(0, DAG(name=ENTRYPOINT, tasks=stage_tasks))

    exit_handler = _compile_exit_handler(pipeline, params)
    templates += exit_handler
    _check_unique_template_names(pipeline, templates)

    return WorkflowTemplate(
        name=pipeline.name,
        entrypoint=ENTRYPOINT,
        on_exit=EXIT_HANDLER if exit_handler else None,
        arguments=[_compile_param(p) for p in pipeline.params],
        templates=templates,
    )


def _compile_param(param: Param) -> Parameter:
    # Hera serializes an explicit value=None as the string "null", so unset fields are omitted.
    fields = {"value": param.default, "enum": param.enum, "description": param.description}
    return Parameter(name=param.name, **{k: v for k, v in fields.items() if v is not None})


def _compile_stage(pipeline: Pipeline, stage: Stage, params: set[str]):
    tasks = []
    templates = []
    for block in stage.blocks:
        name = f"{stage.name}-{block.block_name}"
        where = f"pipeline {pipeline.name!r}, stage {stage.name!r}, block {block.block_name!r}"
        template = _compile_block(block, name=name, params=params, where=where)
        hooks, hook_templates = _compile_exit_hook(
            block,
            task=block.block_name,
            path=f"{stage.name}/{block.block_name}",
            params=params,
            where=where,
        )
        templates += [template, *hook_templates]
        tasks.append(
            Task(
                name=block.block_name,
                template=template,
                when=_lower_when(block.when, params, where),
                hooks=hooks,
            )
        )
    return DAG(name=stage.name, tasks=tasks), templates


def _compile_exit_hook(step: Block | Stage, task: str, path: str, params: set[str], where: str):
    """An `exit` hook running the step's handlers, plus the templates it needs.

    `task` is the step's task name in its DAG and `path` is its `{{ step.name }}`. Generated
    templates are named after the step's own template: "<stage>[-<block>]-on-exit" for the
    handler DAG and "<stage>[-<block>]-<handler task>" for each handler.
    """
    prefix = path.replace("/", "-")
    templates = _compile_handler_dag(
        step,
        HANDLER_FIELDS,
        dag_name=f"{prefix}-on-exit",
        prefix=f"{prefix}-",
        status=f"{{{{inputs.parameters.{_STATUS_INPUT}}}}}",
        step_context=lambda outcome: {"name": path, "status": _OUTCOME_STATUS[outcome]},
        params=params,
        where=where,
    )
    if not templates:
        return None, []
    status = ParameterModel(name=_STATUS_INPUT, value=f"{{{{tasks.{task}.status}}}}")
    hook = LifecycleHook(template=templates[0].name, arguments=Arguments(parameters=[status]))
    return {"exit": hook}, templates


def _compile_exit_handler(pipeline: Pipeline, params: set[str]) -> list:
    """The onExit DAG and its handler templates, or [] when the pipeline has no handlers.

    A failing handler fails the pipeline: that is Argo's default for onExit (verified on 4.1.6)
    and intended, since handler failures are unexpected.
    """
    return _compile_handler_dag(
        pipeline,
        ("on_success", "on_failure"),
        dag_name=EXIT_HANDLER,
        prefix="",
        status="{{workflow.status}}",
        step_context=lambda outcome: _WORKFLOW_STEP,
        params=params,
        where=f"pipeline {pipeline.name!r}",
    )


def _compile_handler_dag(
    owner: Pipeline | Stage | Block,
    fields: tuple[str, ...],
    dag_name: str,
    prefix: str,
    status: str,
    step_context: Callable[[str], Mapping[str, str]],
    params: set[str],
    where: str,
) -> list:
    """A DAG running `owner`'s handlers, each guarded by `when` on `status`, followed by the
    handler templates. Returns [] when there are no handlers."""
    tasks = []
    templates = []
    for field in fields:
        outcomes = _HANDLER_OUTCOMES[field]
        kind = field.rstrip("_").replace("_", "-")
        for handler in getattr(owner, field):
            for outcome in outcomes:
                key = f"{kind}-{handler.block_name}"
                if len(outcomes) > 1:
                    key += f"-{outcome}"
                template = _compile_block(
                    handler,
                    name=f"{prefix}{key}",
                    params=params,
                    where=f"{where}, {field} block {handler.block_name!r}",
                    step=step_context(outcome),
                )
                templates.append(template)
                when = _CONDITIONS[outcome].format(status=status)
                tasks.append(Task(name=key, template=template, when=when))
    if not tasks:
        return []
    inputs = [Parameter(name=_STATUS_INPUT)] if "inputs.parameters" in status else None
    return [DAG(name=dag_name, inputs=inputs, tasks=tasks), *templates]


def _compile_block(
    block: Block,
    name: str,
    params: set[str],
    where: str,
    step: Mapping[str, str] | None = None,
):
    try:
        template = lower_model(block, params, step, exclude=HANDLER_FIELDS).compile()
    except CompileError as e:
        raise CompileError(f"{where}: {e}") from e
    return dataclasses.replace(template, name=name)


def _lower_when(when: str | None, params: set[str], where: str) -> str | None:
    if when is None:
        return None
    try:
        return lower(when, params, quote_params=True)
    except CompileError as e:
        raise CompileError(f"{where}: when: {e}") from e


def _check_unique_template_names(pipeline: Pipeline, templates) -> None:
    seen = set()
    for template in templates:
        if template.name in seen:
            raise CompileError(
                f"pipeline {pipeline.name!r}: template name {template.name!r} is generated twice; "
                f"rename a stage or block ({ENTRYPOINT!r} and {EXIT_HANDLER!r} are reserved, and "
                "generated '<stage>-<block>' names must not collide with other template names)"
            )
        seen.add(template.name)

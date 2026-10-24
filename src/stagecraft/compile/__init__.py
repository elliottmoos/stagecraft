"""Compiler: pure functions from a pipeline spec to Hera objects. See DESIGN.md section 7.

No I/O, no environment reads, no globals. Hera objects are constructed explicitly rather than
with Hera's context managers, which keep module-level state.
"""

import dataclasses
from collections.abc import Mapping

from hera.workflows import DAG, Parameter, Task, WorkflowTemplate

from stagecraft.compile.placeholders import lower_model
from stagecraft.errors import CompileError
from stagecraft.spec import Block, Param, Pipeline, Stage

ENTRYPOINT = "main"
EXIT_HANDLER = "on-exit"

# `{{ step.* }}` in pipeline-level handlers refers to the workflow itself.
_WORKFLOW_STEP = {"name": "{{workflow.name}}", "status": "{{workflow.status}}"}


def compile_pipeline(pipeline: Pipeline) -> WorkflowTemplate:
    """Compile a pipeline to a WorkflowTemplate.

    Each stage becomes a sub-DAG template of parallel block tasks, invoked as one task in the
    entrypoint DAG. Stage N depends on stage N-1, one edge per stage. Pipeline handlers run from
    an `onExit` DAG whose tasks branch on `{{workflow.status}}`.
    """
    params = {p.name for p in pipeline.params}
    templates = []
    stage_tasks = []
    previous: Stage | None = None
    for stage in pipeline.stages:
        stage_dag, block_templates = _compile_stage(pipeline, stage, params)
        templates += [stage_dag, *block_templates]
        stage_tasks.append(
            Task(
                name=stage.name,
                template=stage_dag,
                depends=previous.name if previous else None,
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
    block_templates = []
    for block in stage.blocks:
        template = _compile_block(
            block,
            name=f"{stage.name}-{block.block_name}",
            params=params,
            where=f"pipeline {pipeline.name!r}, stage {stage.name!r}, block {block.block_name!r}",
        )
        block_templates.append(template)
        tasks.append(Task(name=block.block_name, template=template))
    return DAG(name=stage.name, tasks=tasks), block_templates


def _compile_exit_handler(pipeline: Pipeline, params: set[str]) -> list:
    """The onExit DAG and its handler templates, or [] when the pipeline has no handlers."""
    tasks = []
    handler_templates = []
    branches = [
        ("on_success", pipeline.on_success, "{{workflow.status}} == Succeeded"),
        ("on_failure", pipeline.on_failure, "{{workflow.status}} != Succeeded"),
    ]
    for field, blocks, when in branches:
        for block in blocks:
            name = f"{field.replace('_', '-')}-{block.block_name}"
            template = _compile_block(
                block,
                name=name,
                params=params,
                step=_WORKFLOW_STEP,
                where=f"pipeline {pipeline.name!r}, {field} block {block.block_name!r}",
            )
            handler_templates.append(template)
            # No continueOn: a failing handler is unexpected and fails the pipeline, which is
            # Argo's default for onExit (verified on 4.1.6).
            tasks.append(Task(name=name, template=template, when=when))
    if not tasks:
        return []
    return [DAG(name=EXIT_HANDLER, tasks=tasks), *handler_templates]


def _compile_block(
    block: Block,
    name: str,
    params: set[str],
    where: str,
    step: Mapping[str, str] | None = None,
):
    try:
        template = lower_model(block, params, step).compile()
    except CompileError as e:
        raise CompileError(f"{where}: {e}") from e
    return dataclasses.replace(template, name=name)


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

"""Compiler: pure functions from a pipeline spec to Hera objects. See DESIGN.md section 7.

No I/O, no environment reads, no globals. Hera objects are constructed explicitly rather than
with Hera's context managers, which keep module-level state.
"""

import dataclasses
from collections.abc import Callable, Mapping

from hera.workflows import DAG, CronWorkflow, Parameter, Task, WorkflowTemplate
from hera.workflows.models import Arguments, ContinueOn, LifecycleHook, WorkflowTemplateRef
from hera.workflows.models import Parameter as ParameterModel

from stagecraft.compile import outputs, setup, source
from stagecraft.compile.placeholders import Scopes, lower, lower_model
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import HANDLER_FIELDS, Action, Param, Pipeline, Stage, Step, TemplateRef

ENTRYPOINT = "main"
EXIT_HANDLER = "on-exit"
STATUS_REPORTER = "status-reporter"
STATUS_REPORT = "status-report"


# Handlers run from a generated DAG whose tasks are guarded by a `when` on the step's status: the
# workflow's `onExit` for pipeline handlers, a task `exit` hook for stage and step handlers.
# Verified on Argo 4.1.6: expression hooks on a sub-DAG task never fire when another task depends
# on it, while `exit` hooks fire and can pass `{{tasks.<name>.status}}`. A handler template is
# compiled per outcome so `{{ step.outcome }}` can be fixed in it; Argo's Error counts as failed.
_CONDITIONS = {
    "succeeded": "{status} == Succeeded",
    "failed": "{status} == Failed || {status} == Error",
}
_HANDLER_OUTCOMES = {
    "on_success": ["succeeded"],
    "on_failure": ["failed"],
    "finally_": ["succeeded", "failed"],
}
_STATUS_INPUT = "status"

# Commit statuses. "pending" is posted by a task the step depends on, so it always lands before
# the final state, which the step's exit hook (or the pipeline's onExit) posts. Verified on Argo
# 4.1.6: `running` hooks can miss short steps, so they could post "pending" late or not at all.
# Reports are best-effort: every report task calls the `status-report` DAG, which runs the
# reporter with continueOn, so a report never fails. (continueOn cannot go on the report tasks
# themselves: Argo rejects it in DAGs that use `depends`.)
_REPORT_INPUTS = ("context", "state", "description")
_REPORT_SCOPE = {"status": {f: f"{{{{inputs.parameters.{f}}}}}" for f in _REPORT_INPUTS}}
_FINAL_STATES = [("Succeeded", "success"), ("Failed", "failure"), ("Error", "error")]
_BEST_EFFORT = ContinueOn(failed=True, error=True)


def compile_pipeline(pipeline: Pipeline, ctx: CompileContext) -> WorkflowTemplate:
    """Compile a pipeline to a WorkflowTemplate.

    Each stage becomes a sub-DAG template of parallel step tasks, invoked as one task in the
    entrypoint DAG. Stage N depends on stage N-1, one edge per stage. Stage and step handlers run
    from task `exit` hooks, pipeline handlers from the workflow's `onExit`. Commit statuses are
    report tasks around each step and in those exit handlers. Helm values come from `ctx`, which
    records every reference for the generated values schema.
    """
    lowering = _Lowering({p.name for p in pipeline.params}, ctx)
    templates = []
    main_tasks = []
    depends = None
    if pipeline.setup:
        lowering, setup_templates = _compile_setup(pipeline, lowering)
        templates += setup_templates
        main_tasks.append(Task(name=setup.TASK, template=setup_templates[0]))
        depends = setup.TASK
    if pipeline.source:
        lowering, source_template = _compile_source(pipeline, lowering)
        templates.append(source_template)
        main_tasks.append(Task(name=source.TASK, template=source_template, depends=depends))
        depends = source.TASK
    if pipeline.status:
        main_tasks.append(
            _report(
                "pipeline-status-pending", pipeline.status, "pending", "Running", depends=depends
            )
        )
    # Step outputs published so far: a stage's steps, conditions, and handlers see those of
    # earlier stages; pipeline handlers see all of them. The status reporter sees none.
    produced: dict[outputs.Key, outputs.Produced] = {}
    for stage in pipeline.stages:
        where = f"pipeline {pipeline.name!r}, stage {stage.name!r}"
        stage_lowering = dataclasses.replace(lowering, steps=outputs.scope(produced))
        stage_dag, step_templates, stage_produced, inner_lowering = _compile_stage(
            pipeline, stage, stage_lowering, produced
        )
        # The stage's handlers run after it, so they see its sources; its `when` doesn't.
        hooks, hook_templates = _compile_exit_hook(
            stage, task=stage.name, path=stage.name, lowering=inner_lowering, where=where
        )
        templates += [stage_dag, *step_templates, *hook_templates]
        when = _lower_when(stage.when, stage_lowering, where)
        produced |= stage_produced
        reports, stage_depends = _status_tasks(stage, stage.name, depends=depends, when=when)
        main_tasks += reports
        main_tasks.append(
            Task(name=stage.name, template=stage_dag, depends=stage_depends, when=when, hooks=hooks)
        )
        depends = stage.name
    templates.insert(0, DAG(name=ENTRYPOINT, tasks=main_tasks))
    if pipeline.status_contexts:
        templates[1:1] = _compile_status_reporter(pipeline, lowering)

    exit_handler = _compile_exit_handler(
        pipeline, dataclasses.replace(lowering, steps=outputs.scope(produced))
    )
    templates += exit_handler
    _check_unique_template_names(pipeline, templates)

    return WorkflowTemplate(
        name=pipeline.name,
        entrypoint=ENTRYPOINT,
        on_exit=EXIT_HANDLER if exit_handler else None,
        arguments=[_compile_param(p) for p in pipeline.params],
        templates=templates,
    )


def compile_triggers(pipeline: Pipeline, ctx: CompileContext) -> list[CronWorkflow]:
    """One CronWorkflow per trigger, submitting the pipeline's WorkflowTemplate with the
    trigger's params. Param values may reference Helm values through `ctx`."""
    crons = []
    for trigger in pipeline.triggers:
        arguments = []
        for name, value in trigger.params.items():
            try:
                lowered = lower(value, params=(), values=ctx.value)
            except CompileError as e:
                where = f"pipeline {pipeline.name!r}, trigger {trigger.name!r}, param {name!r}"
                raise CompileError(f"{where}: {e}") from e
            arguments.append(Parameter(name=name, value=lowered))
        crons.append(
            CronWorkflow(
                name=f"{pipeline.name}-{trigger.name}",
                schedules=[trigger.schedule],
                timezone=trigger.timezone,
                workflow_template_ref=WorkflowTemplateRef(name=pipeline.name),
                arguments=arguments or None,
            )
        )
    return crons


@dataclasses.dataclass(frozen=True)
class _Lowering:
    """What lowering placeholders needs: the declared param names, the compile context, and the
    `{{ setup.* }}` and `{{ source.* }}` fields when the pipeline has a setup step or a source."""

    names: set[str]
    ctx: CompileContext
    setup: Mapping[str, str] | None = None
    source: Mapping[str, str] | None = None
    steps: Mapping[str, str] | None = None

    @property
    def scopes(self) -> Scopes:
        scopes = {"setup": self.setup, "source": self.source, "steps": self.steps}
        return {name: fields for name, fields in scopes.items() if fields is not None}


def _compile_setup(pipeline: Pipeline, lowering: _Lowering):
    """The lowering with `{{ setup.* }}` fields, and the setup's templates (its wrapper first)."""
    where = f"pipeline {pipeline.name!r}, setup"
    spec = pipeline.setup
    if isinstance(spec, TemplateRef):
        arguments = {}
        for name, value in spec.arguments.items():
            try:
                arguments[name] = lower(value, lowering.names, values=lowering.ctx.value)
            except CompileError as e:
                raise CompileError(f"{where} argument {name!r}: {e}") from e
        step = setup.template_ref_step(spec.template, spec.name, spec.template, arguments)
        outputs, extra = list(spec.outputs), []
    else:
        assert isinstance(spec, Step)
        template = _compile_action(
            spec.action, name=f"{setup.TASK}-{spec.name}", lowering=lowering,
            where=f"{where} step {spec.name!r}",
        )  # fmt: skip
        declared = template.outputs or []
        if not isinstance(declared, list):
            raise CompileError(f"{where} step {spec.name!r}: outputs must be a list")
        outputs = [o.name for o in declared if isinstance(o, Parameter)]
        step, extra = setup.action_step(spec.name, template.name), [template]
    fields = {output: setup.reference(output) for output in outputs}
    return dataclasses.replace(lowering, setup=fields), [setup.wrapper(step, outputs), *extra]


def _compile_source(pipeline: Pipeline, lowering: _Lowering):
    """The lowering with `{{ source.* }}` fields, and the source step's template."""
    assert pipeline.source is not None
    fields = {}
    for field in ("url", "revision"):
        try:
            fields[field] = lower(
                getattr(pipeline.source, field),
                lowering.names,
                lowering.scopes,
                values=lowering.ctx.value,
            )
        except CompileError as e:
            raise CompileError(f"pipeline {pipeline.name!r}, source {field}: {e}") from e
    template = source.source_step(
        source.MAIN, source.TASK, fields["url"], fields["revision"], lowering.ctx
    )
    return dataclasses.replace(lowering, source={**fields, "sha": source.SHA}), template


def _compile_param(param: Param) -> Parameter:
    # Hera serializes an explicit value=None as the string "null", so unset fields are omitted.
    fields = {"value": param.default, "enum": param.enum, "description": param.description}
    return Parameter(name=param.name, **{k: v for k, v in fields.items() if v is not None})


def _compile_stage(
    pipeline: Pipeline,
    stage: Stage,
    lowering: _Lowering,
    produced: Mapping[outputs.Key, outputs.Produced],
):
    """The stage's DAG, its templates, what its steps publish for later stages, and the lowering
    inside the stage (with its sources' placeholders)."""
    tasks = []
    templates = []
    stage_produced = {}
    has_main_source = bool(lowering.source)
    checkouts = [source.MAIN] if has_main_source else []
    stage_fields = {}
    for src in stage.sources:
        assert src.name is not None  # guaranteed by Stage validation
        checkout = source.for_stage(stage.name, src.name)
        fields = {}
        for field in ("url", "revision"):
            try:
                fields[field] = lower(
                    getattr(src, field), lowering.names, lowering.scopes, values=lowering.ctx.value
                )
            except CompileError as e:
                raise CompileError(
                    f"pipeline {pipeline.name!r}, stage {stage.name!r}, source {src.name!r} "
                    f"{field}: {e}"
                ) from e
        fetch = source.source_step(
            checkout, f"{stage.name}-{checkout.task}", fields["url"], fields["revision"],
            lowering.ctx,
        )  # fmt: skip
        templates.append(fetch)
        tasks.append(Task(name=checkout.task, template=fetch))
        checkouts.append(checkout)
        stage_fields |= {f"{src.name}.{k}": v for k, v in {**fields, "sha": checkout.sha}.items()}
    if stage_fields:
        lowering = dataclasses.replace(lowering, source={**(lowering.source or {}), **stage_fields})
    step_depends = " && ".join(c.task for c in checkouts if c is not source.MAIN) or None
    for step in stage.steps:
        name = f"{stage.name}-{step.name}"
        where = f"pipeline {pipeline.name!r}, stage {stage.name!r}, step {step.name!r}"
        template = _compile_action(step.action, name=name, lowering=lowering, where=where)
        conditional = stage.when is not None or step.when is not None
        template, stage_produced[stage.name, step.name] = outputs.publish(
            template, (stage.name, step.name), has_main_source, conditional, where
        )
        arguments = outputs.bind(template, step.inputs, produced, where)
        if checkouts:
            template = source.inject(template, checkouts, where)
            arguments += [source.argument(c) for c in checkouts]
        hooks, hook_templates = _compile_exit_hook(
            step,
            task=step.name,
            path=f"{stage.name}/{step.name}",
            lowering=lowering,
            where=where,
        )
        templates += [template, *hook_templates]
        when = _lower_when(step.when, lowering, where)
        reports, depends = _status_tasks(step, step.name, depends=step_depends, when=when)
        tasks += reports
        tasks.append(
            Task(
                name=step.name,
                template=template,
                arguments=arguments or None,
                depends=depends,
                when=when,
                hooks=hooks,
            )
        )
    return DAG(name=stage.name, tasks=tasks), templates, stage_produced, lowering


def _compile_exit_hook(step: Step | Stage, task: str, path: str, lowering: _Lowering, where: str):
    """An `exit` hook running the step's handlers and final status report, plus the templates
    it needs.

    `task` is the step's task name in its DAG and `path` is its `{{ step.name }}`. Generated
    templates are named after the step's own template: "<stage>[-<step>]-on-exit" for the
    handler DAG and "<stage>[-<step>]-<handler task>" for each handler.
    """
    prefix = path.replace("/", "-")
    templates = _compile_handler_dag(
        step,
        HANDLER_FIELDS,
        dag_name=f"{prefix}-on-exit",
        prefix=f"{prefix}-",
        status=f"{{{{inputs.parameters.{_STATUS_INPUT}}}}}",
        step_context=lambda outcome: {"name": path, "outcome": outcome},
        report=step.status,
        lowering=lowering,
        where=where,
    )
    if not templates:
        return None, []
    status = ParameterModel(name=_STATUS_INPUT, value=f"{{{{tasks.{task}.status}}}}")
    hook = LifecycleHook(template=templates[0].name, arguments=Arguments(parameters=[status]))
    return {"exit": hook}, templates


def _compile_exit_handler(pipeline: Pipeline, lowering: _Lowering) -> list:
    """The onExit DAG and its handler templates, or [] when there is nothing to run on exit.

    A failing handler fails the pipeline: that is Argo's default for onExit (verified on 4.1.6)
    and intended, since handler failures are unexpected. Status reports stay best-effort.
    """
    return _compile_handler_dag(
        pipeline,
        ("on_success", "on_failure"),
        dag_name=EXIT_HANDLER,
        prefix="",
        status="{{workflow.status}}",
        step_context=lambda outcome: {"name": "{{workflow.name}}", "outcome": outcome},
        report=pipeline.status,
        lowering=lowering,
        where=f"pipeline {pipeline.name!r}",
        # Without a commit (the source step failed) there's nothing to report on.
        report_guard=source.HAS_COMMIT,
    )


def _compile_handler_dag(
    owner: Pipeline | Stage | Step,
    fields: tuple[str, ...],
    dag_name: str,
    prefix: str,
    status: str,
    step_context: Callable[[str], Mapping[str, str]],
    report: str | None,
    lowering: _Lowering,
    where: str,
    report_guard: str | None = None,
) -> list:
    """A DAG that reports the final commit status for `report` (if set) and runs `owner`'s
    handlers, each guarded by `when` on `status`, followed by the handler templates. Returns []
    when there is nothing to run."""
    tasks = []
    templates = []
    if report:
        for argo_status, state in _FINAL_STATES:
            when = f"{status} == {argo_status}" + (f" && {report_guard}" if report_guard else "")
            tasks.append(_report(f"status-{state}", report, state, argo_status, when=when))
    for field in fields:
        outcomes = _HANDLER_OUTCOMES[field]
        kind = field.rstrip("_").replace("_", "-")
        for handler in getattr(owner, field):
            for outcome in outcomes:
                key = f"{kind}-{handler.name}"
                if len(outcomes) > 1:
                    key += f"-{outcome}"
                template = _compile_action(
                    handler.action,
                    name=f"{prefix}{key}",
                    lowering=lowering,
                    where=f"{where}, {field} step {handler.name!r}",
                    scopes={"step": step_context(outcome)},
                )
                templates.append(template)
                when = _CONDITIONS[outcome].format(status=status)
                tasks.append(Task(name=key, template=template, when=when))
    if not tasks:
        return []
    inputs = [Parameter(name=_STATUS_INPUT)] if "inputs.parameters" in status else None
    return [DAG(name=dag_name, inputs=inputs, tasks=tasks), *templates]


def _status_tasks(step: Step | Stage, task: str, depends: str | None, when: str | None):
    """Report tasks that run before a step with a status context, and the step's new depends.

    "pending" shares the step's depends and when, and the step depends on it. A step with
    `when` also gets a "success: Skipped" report with the negated condition.
    """
    if step.status is None:
        return [], depends
    pending = _report(
        f"{task}-status-pending", step.status, "pending", "Running", depends=depends, when=when
    )
    reports = [pending]
    if when:
        reports.append(
            _report(
                f"{task}-status-skipped",
                step.status,
                "success",
                "Skipped",
                depends=depends,
                when=f"!({when})",
            )
        )
    return reports, f"{depends} && {pending.name}" if depends else pending.name


def _report(name: str, context: str, state: str, description: str, **task_fields) -> Task:
    arguments = {"context": context, "state": state, "description": description}
    return Task(
        name=name,
        template=STATUS_REPORT,
        arguments=[Parameter(name=k, value=v) for k, v in arguments.items()],
        **task_fields,
    )


def _compile_status_reporter(pipeline: Pipeline, lowering: _Lowering) -> list:
    """The reporter's template and the best-effort `status-report` DAG that calls it."""
    reporter = pipeline.status_reporter
    assert reporter is not None  # guaranteed by Pipeline validation when statuses exist
    where = f"pipeline {pipeline.name!r}, status_reporter"
    template = _compile_action(
        reporter, name=STATUS_REPORTER, lowering=lowering, where=where, scopes=_REPORT_SCOPE
    )
    if template.inputs:
        raise CompileError(f"{where}: a status reporter cannot declare its own inputs")
    inputs = [Parameter(name=f) for f in _REPORT_INPUTS]
    report = Task(
        name="report",
        template=STATUS_REPORTER,
        arguments=[
            Parameter(name=f, value=f"{{{{inputs.parameters.{f}}}}}") for f in _REPORT_INPUTS
        ],
        continue_on=_BEST_EFFORT,
    )
    return [
        DAG(name=STATUS_REPORT, inputs=inputs, tasks=[report]),
        dataclasses.replace(template, inputs=inputs),
    ]


def _compile_action(
    action: Action,
    name: str,
    lowering: _Lowering,
    where: str,
    scopes: Scopes | None = None,
):
    try:
        scopes = {**lowering.scopes, **(scopes or {})}
        lowered = lower_model(action, lowering.names, scopes, lowering.ctx.value)
        template = lowered.compile(lowering.ctx)
    except CompileError as e:
        raise CompileError(f"{where}: {e}") from e
    return dataclasses.replace(template, name=name)


def _lower_when(when: str | None, lowering: _Lowering, where: str) -> str | None:
    if when is None:
        return None
    try:
        return lower(
            when, lowering.names, lowering.scopes, quote_params=True, values=lowering.ctx.value
        )
    except CompileError as e:
        raise CompileError(f"{where}: when: {e}") from e


def _check_unique_template_names(pipeline: Pipeline, templates) -> None:
    seen = set()
    for template in templates:
        if template.name in seen:
            raise CompileError(
                f"pipeline {pipeline.name!r}: template name {template.name!r} is generated twice; "
                f"rename a stage or step ({ENTRYPOINT!r}, {EXIT_HANDLER!r}, {STATUS_REPORT!r}, "
                f"{STATUS_REPORTER!r}, {setup.TASK!r}, and {source.TASK!r} are reserved, and "
                "generated '<stage>-<step>' names must not collide with other template names)"
            )
        seen.add(template.name)

"""Spec layer: the models pipeline authors write. See DESIGN.md sections 5 and 6."""

import re
from abc import abstractmethod
from typing import Annotated, Self

from hera.workflows import Container, Script
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from stagecraft.conditions import Condition, Operand
from stagecraft.context import CompileContext

_DNS_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# A CronWorkflow schedule, as Argo 4.1.6's CRD validates it.
_CRON = re.compile(
    r"@(yearly|annually|monthly|weekly|daily|midnight|hourly)"
    r"|@every\s+([0-9]+(ns|us|µs|ms|s|m|h))+"
    r"|([A-Za-z0-9*,/?-]+\s+){4}[A-Za-z0-9*,/?-]+"
)
# Argo rejects longer CronWorkflow names: it appends a timestamp to name each run.
_CRON_NAME_MAX = 52
# The tasks of the generated steps that run the pipeline's setup and check out its source.
_SETUP_STEP = "setup"
_SOURCE_STEP = "source"
# Output parameter names, as `{{ setup.<name> }}` references them.
_OUTPUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# An earlier step's output artifact, as a step's `inputs` refer to it.
_ARTIFACT_REF = re.compile(rf"{_DNS_LABEL.pattern}\.{_DNS_LABEL.pattern}\.{_OUTPUT_NAME.pattern}")


def _check_name(value: str) -> str:
    if not _DNS_LABEL.fullmatch(value):
        raise ValueError(
            f"{value!r} must be a lowercase DNS label: a-z, 0-9 and '-', "
            "starting and ending alphanumeric, at most 63 characters"
        )
    return value


def _check_param_name(value: str) -> str:
    if not _PARAM_NAME.fullmatch(value):
        raise ValueError(f"param name {value!r} must match {_PARAM_NAME.pattern}")
    return value


Name = Annotated[str, AfterValidator(_check_name)]
ParamName = Annotated[str, AfterValidator(_check_param_name)]


def _kebab(class_name: str) -> str:
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", class_name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", words).lower()


def _duplicates(names: list[str]) -> list[str]:
    seen: set[str] = set()
    return [n for n in names if n in seen or seen.add(n)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Param(_Model):
    """A workflow parameter. Params become the submit form in the Argo UI."""

    name: ParamName
    required: bool = False
    default: str | None = None
    enum: list[str] | None = None
    description: str | None = None

    def __init__(self, name: str, /, **data) -> None:
        super().__init__(name=name, **data)

    @model_validator(mode="after")
    def _check_default(self) -> Self:
        if self.required and self.default is not None:
            raise ValueError(f"param {self.name!r}: required param cannot have a default")
        if not self.required and self.default is None:
            raise ValueError(f"param {self.name!r}: optional param needs a default")
        if self.enum is not None and self.default is not None and self.default not in self.enum:
            raise ValueError(f"param {self.name!r}: default {self.default!r} is not in enum")
        return self


HANDLER_FIELDS = ("on_success", "on_failure", "finally_")


class Action(_Model):
    """Base type for actions: reusable work that runs as one container. A subclass declares its
    fields and compiles to one Hera template. Where and when it runs is up to the Step that
    places it.

    String fields may contain placeholders such as `{{ param.repo }}` or `{{ values.a.b }}`; the
    compiler lowers them before calling `compile`. Inside `compile`, Helm values come only from
    `ctx.value(path, type)`. The compiler also names the returned template.
    """

    @abstractmethod
    def compile(self, ctx: CompileContext) -> Container | Script: ...


def _as_steps(items: object) -> object:
    """Wrap bare actions in a list as steps named after their class (Make -> "make")."""
    if not isinstance(items, list):
        return items
    return [Step(_kebab(type(i).__name__), i) if isinstance(i, Action) else i for i in items]


class _Node(_Model):
    """What steps and stages share: a condition, a commit status, and handlers that run after
    they finish.

    `when` is a condition from stagecraft.conditions (`when=param("deploy") == "true"`), or a raw
    Argo expression in which placeholders become quoted strings, so compare them with quoted
    values: `when="{{ param.deploy }} == 'true'"`. A condition is stored as that raw form.

    Handlers are steps run from the node's Argo `exit` hook (a bare action is a step named after
    its class). `on_success` runs when the node succeeds, `on_failure` when it fails or errors,
    `finally_` in both cases; none run when `when` skips the node.

    `status` is a commit-status context (for example "ci/unit-tests"). The pipeline's
    status_reporter posts "pending" just before the node starts, then "success", "failure", or
    "error" when it finishes, or "success" with the description "Skipped" when `when` skips it.
    Reporting is best-effort and never fails the pipeline.

    In handlers, `{{ step.name }}` is the node's path ("stage" or "stage/step") and
    `{{ step.outcome }}` is "succeeded" or "failed". A failing handler is shown in the Argo UI
    but, unlike a pipeline-level handler, does not fail the pipeline (an Argo limitation).
    """

    when: str | None = None
    status: str | None = None
    on_success: list["Step"] = []
    on_failure: list["Step"] = []
    finally_: list["Step"] = []

    def _label(self) -> str:
        raise NotImplementedError

    @field_validator(*HANDLER_FIELDS, mode="before")
    @classmethod
    def _wrap_handlers(cls, items: object) -> object:
        return _as_steps(items)

    @field_validator("when", mode="before")
    @classmethod
    def _render_condition(cls, when: object) -> object:
        if isinstance(when, Operand):
            raise ValueError("an operand isn't a condition: compare it, as in param('x') == 'y'")
        return when.render() if isinstance(when, Condition) else when

    @model_validator(mode="after")
    def _check_handlers(self) -> Self:
        _check_handlers(self._label(), self, HANDLER_FIELDS)
        return self


class Step(_Node):
    """An action placed in a stage (or a handler list) under a name, unique where it's placed.

    `inputs` binds each of the action's input artifacts to an earlier step's output artifact, as
    "<stage>.<step>.<artifact>".
    """

    name: Name
    action: Action
    inputs: dict[str, str] = {}

    def __init__(self, name: str, action: Action, /, **data) -> None:
        super().__init__(name=name, action=action, **data)

    def _label(self) -> str:
        return f"step {self.name!r}"

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, inputs: dict[str, str]) -> dict[str, str]:
        for name, reference in inputs.items():
            if not _ARTIFACT_REF.fullmatch(reference):
                raise ValueError(
                    f"input {name!r}: {reference!r} must be '<stage>.<step>.<artifact>'"
                )
        return inputs


class Stage(_Node):
    """An ordered phase of a pipeline. Its steps run in parallel; a bare action is a step named
    after its class.

    `sources` are extra repositories checked out at the start of the stage, resolved to their
    commits then, for this stage's steps only (see Source).
    """

    name: Name
    steps: Annotated[list[Step], Field(min_length=1)]
    sources: list["Source"] = []

    def __init__(self, name: str, steps: list[Step | Action], /, **data) -> None:
        super().__init__(name=name, steps=steps, **data)

    def _label(self) -> str:
        return f"stage {self.name!r}"

    @field_validator("steps", mode="before")
    @classmethod
    def _wrap_steps(cls, items: object) -> object:
        return _as_steps(items)

    @model_validator(mode="after")
    def _check_step_names(self) -> Self:
        _check_step_names(f"stage {self.name!r}", self.steps)
        return self

    @model_validator(mode="after")
    def _check_sources(self) -> Self:
        where = f"stage {self.name!r}"
        for src in self.sources:
            if src.name is None:
                raise ValueError(f"{where}: stage sources need a name (Source(name=..., ...))")
            if src.name == "main":
                raise ValueError(f"{where}: source name 'main' is reserved for the pipeline's")
        names = [src.name for src in self.sources]
        if dupes := _duplicates(names):
            raise ValueError(f"{where}: duplicate source name {dupes[0]!r}")
        tasks = {f"source-{name}": name for name in names}
        for step in self.steps:
            if step.name in tasks:
                raise ValueError(
                    f"{where}: step name {step.name!r} is taken by the stage's source "
                    f"{tasks[step.name]!r}"
                )
        return self


def _check_step_names(where: str, steps: list[Step]) -> None:
    if dupes := _duplicates([s.name for s in steps]):
        raise ValueError(
            f'{where}: duplicate step name {dupes[0]!r}; wrap one in Step("<name>", ...) to name it'
        )


def _check_handlers(where: str, owner: BaseModel, fields: tuple[str, ...]) -> None:
    for field in fields:
        handlers: list[Step] = getattr(owner, field)
        _check_step_names(f"{where} {field}", handlers)
        for handler in handlers:
            if handler.inputs:
                raise ValueError(
                    f"{where} {field}: step {handler.name!r}: handler steps cannot take inputs"
                )
            has_handlers = any(getattr(handler, f) for f in HANDLER_FIELDS)
            if handler.when is not None or handler.status is not None or has_handlers:
                raise ValueError(
                    f"{where} {field}: step {handler.name!r}: "
                    "handler steps cannot set when, status, or handlers"
                )


class TemplateRef(_Model):
    """A template of an existing WorkflowTemplate, called as is: `name` is the WorkflowTemplate,
    `template` the template in it. `arguments` are its input parameters, and may use
    `{{ param.x }}` and `{{ values.a.b }}`. `outputs` lists the output parameters it publishes,
    which Stagecraft can't inspect; only those can be referenced."""

    name: Name
    template: Name
    arguments: dict[str, str] = {}
    outputs: list[str] = []

    def __init__(self, name: str, /, **data) -> None:
        super().__init__(name=name, **data)

    @model_validator(mode="after")
    def _check_outputs(self) -> Self:
        for output in self.outputs:
            if not _OUTPUT_NAME.fullmatch(output):
                raise ValueError(f"output name {output!r} must match {_OUTPUT_NAME.pattern}")
        if dupes := _duplicates(self.outputs):
            raise ValueError(f"duplicate output name {dupes[0]!r}")
        return self


class Source(_Model):
    """A repository at a revision: a commit SHA, or a branch or tag, which is resolved to its
    commit once. `url` is what `git fetch` takes. Both may use `{{ param.x }}` and
    `{{ values.a.b }}`.

    The pipeline's source is "main" and needs no name. A stage's sources need a name, which
    places them at /sources/<name> (STAGECRAFT_SOURCE_<NAME>) and names their placeholders
    (`{{ source.<name>.sha }}`).
    """

    url: str
    revision: str
    name: Name | None = None


class Trigger(_Model):
    """Runs the pipeline on a schedule. Compiles to a CronWorkflow named "<pipeline>-<name>" that
    submits the pipeline's WorkflowTemplate.

    `schedule` is a cron expression ("0 2 * * *") or a macro ("@daily"), read in `timezone` (an
    IANA name; the workflow controller's time zone if unset). `params` sets workflow params for
    scheduled runs; values may use `{{ values.a.b }}`. Required params must be set.
    """

    schedule: str
    name: Name = "cron"
    params: dict[str, str] = {}
    timezone: str | None = None

    @classmethod
    def cron(cls, schedule: str, /, **data) -> "Trigger":
        return cls(schedule=schedule, **data)

    @model_validator(mode="after")
    def _check_schedule(self) -> Self:
        if not _CRON.fullmatch(self.schedule):
            raise ValueError(
                f"trigger {self.name!r}: {self.schedule!r} is not a cron schedule "
                "(five fields, or a macro such as @daily)"
            )
        return self


class Pipeline(_Model):
    """A pipeline: ordered stages of parallel steps, compiled to one WorkflowTemplate.

    `on_success` and `on_failure` steps run in parallel after the pipeline finishes, depending
    on its result. They can use `{{ step.name }}` (the workflow's name) and `{{ step.outcome }}`.
    A failing handler fails the pipeline.

    `setup` runs first, before the source: a step (an action) or a TemplateRef to an existing
    WorkflowTemplate. Its output parameters work everywhere as `{{ setup.<output> }}`, the
    source's fields included. It can't set when, status, or handlers.

    `source` is checked out once, before the first stage, and every step gets the checkout as its
    working directory (STAGECRAFT_SOURCE_MAIN). `{{ source.url }}`, `{{ source.revision }}`, and
    `{{ source.sha }}` (the resolved commit) work in every action, handlers included.

    `status` contexts (on the pipeline, stages, and steps) are posted by `status_reporter`, an
    ordinary action that makes the API call. It reads `{{ status.context }}`, `{{ status.state }}`
    ("pending", "success", "failure", "error"), and `{{ status.description }}`, and the commit
    from `{{ source.sha }}`: statuses need a source.

    `triggers` run the pipeline on schedules (see Trigger).
    """

    name: Name
    params: list[Param] = []
    setup: Step | TemplateRef | None = None
    source: Source | None = None
    stages: Annotated[list[Stage], Field(min_length=1)]
    on_success: list[Step] = []
    on_failure: list[Step] = []
    status: str | None = None
    status_reporter: Action | None = None
    triggers: list[Trigger] = []

    @property
    def status_contexts(self) -> list[str]:
        """Every commit-status context, in pipeline order."""
        nodes = [node for stage in self.stages for node in (stage, *stage.steps)]
        return [n.status for n in (self, *nodes) if n.status is not None]

    @field_validator("on_success", "on_failure", mode="before")
    @classmethod
    def _wrap_handlers(cls, items: object) -> object:
        return _as_steps(items)

    @field_validator("source")
    @classmethod
    def _check_source_name(cls, source: "Source | None") -> "Source | None":
        if source is not None and source.name not in (None, "main"):
            raise ValueError("the pipeline's source is named 'main'; leave its name unset")
        return source

    @field_validator("setup", mode="before")
    @classmethod
    def _wrap_setup(cls, setup: object) -> object:
        return _as_steps([setup])[0] if isinstance(setup, Action) else setup

    @model_validator(mode="after")
    def _check_setup(self) -> Self:
        setup = self.setup
        has_handlers = isinstance(setup, Step) and any(getattr(setup, f) for f in HANDLER_FIELDS)
        if isinstance(setup, Step) and (setup.when or setup.status or setup.inputs or has_handlers):
            raise ValueError(
                f"pipeline {self.name!r}: the setup step cannot set when, status, inputs, or "
                "handlers"
            )
        return self

    @model_validator(mode="after")
    def _check_unique_names(self) -> Self:
        if dupes := _duplicates([s.name for s in self.stages]):
            raise ValueError(f"pipeline {self.name!r}: duplicate stage name {dupes[0]!r}")
        for reserved in (_SETUP_STEP, _SOURCE_STEP):
            if any(s.name == reserved for s in self.stages):
                raise ValueError(
                    f"pipeline {self.name!r}: stage name {reserved!r} is reserved for the "
                    f"pipeline's {reserved} step"
                )
        if dupes := _duplicates([p.name for p in self.params]):
            raise ValueError(f"pipeline {self.name!r}: duplicate param name {dupes[0]!r}")
        _check_handlers(f"pipeline {self.name!r}", self, ("on_success", "on_failure"))
        return self

    @model_validator(mode="after")
    def _check_triggers(self) -> Self:
        if dupes := _duplicates([t.name for t in self.triggers]):
            raise ValueError(f"pipeline {self.name!r}: duplicate trigger name {dupes[0]!r}")
        params = {p.name: p for p in self.params}
        for trigger in self.triggers:
            where = f"pipeline {self.name!r}, trigger {trigger.name!r}"
            cron_name = f"{self.name}-{trigger.name}"
            if len(cron_name) > _CRON_NAME_MAX:
                raise ValueError(
                    f"{where}: CronWorkflow name {cron_name!r} is longer than "
                    f"{_CRON_NAME_MAX} characters; shorten the pipeline or trigger name"
                )
            for name, value in trigger.params.items():
                if name not in params:
                    raise ValueError(f"{where}: undeclared param {name!r}")
                enum = params[name].enum
                # A value reference is only known when Argo submits the run, and checked then.
                if enum is not None and "{{" not in value and value not in enum:
                    raise ValueError(f"{where}: param {name!r} value {value!r} is not in {enum}")
            for param in self.params:
                if param.required and param.name not in trigger.params:
                    raise ValueError(f"{where}: required param {param.name!r} has no value")
        return self

    @model_validator(mode="after")
    def _check_statuses(self) -> Self:
        contexts = self.status_contexts
        if dupes := _duplicates(contexts):
            raise ValueError(f"pipeline {self.name!r}: duplicate status context {dupes[0]!r}")
        if not contexts:
            return self
        if self.status_reporter is None:
            raise ValueError(f"pipeline {self.name!r}: status contexts need a status_reporter")
        if self.source is None:
            raise ValueError(
                f"pipeline {self.name!r}: status contexts need a pipeline source "
                "(Pipeline(source=...), whose commit they report on)"
            )
        return self

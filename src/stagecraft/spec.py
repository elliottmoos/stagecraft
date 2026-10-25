"""Spec layer: the models pipeline authors write. See DESIGN.md sections 5 and 6."""

import re
from abc import abstractmethod
from typing import Annotated, Self

from hera.workflows import Container, Script
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

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


class _Step(_Model):
    """What blocks and stages share: a condition, and handlers that run after the step finishes.

    `when` is an Argo condition. Inside it `{{ param.x }}` is a quoted string, so compare it with
    quoted values: `when="{{ param.deploy }} == 'true'"`.

    Handlers are ordinary blocks run from the step's Argo `exit` hook. `on_success` runs when the
    step succeeds, `on_failure` when it fails or errors, `finally_` in both cases; none run when
    `when` skips the step.

    `status` is a commit-status context (for example "ci/unit-tests"). The pipeline's
    status_reporter posts "pending" just before the step starts, then "success", "failure", or
    "error" when it finishes, or "success" with the description "Skipped" when `when` skips it.
    Reporting is best-effort and never fails the pipeline.

    In handlers, `{{ step.name }}` is the step's path ("stage" or
    "stage/block") and `{{ step.status }}` is "Succeeded" or "Failed". A failing handler is shown
    in the Argo UI but, unlike a pipeline-level handler, does not fail the pipeline (an Argo
    limitation).
    """

    when: str | None = None
    status: str | None = None
    on_success: list["Block"] = []
    on_failure: list["Block"] = []
    finally_: list["Block"] = []

    def _label(self) -> str:
        raise NotImplementedError

    @model_validator(mode="after")
    def _check_handlers(self) -> Self:
        _check_handlers(self._label(), self, HANDLER_FIELDS)
        return self


class Block(_Step):
    """Base type for blocks. A subclass declares its fields and compiles to one Hera template.

    String fields may contain placeholders such as `{{ param.repo }}` or `{{ values.a.b }}`; the
    compiler lowers them before calling `compile`. Inside `compile`, Helm values come only from
    `ctx.value(path, type)`. The compiler also names the returned template.
    """

    name: Name | None = None

    @property
    def block_name(self) -> str:
        return self.name or _kebab(type(self).__name__)

    def _label(self) -> str:
        return f"block {self.block_name!r}"

    @abstractmethod
    def compile(self, ctx: CompileContext) -> Container | Script: ...


class Stage(_Step):
    """An ordered step of a pipeline. Its blocks run in parallel."""

    name: Name
    blocks: Annotated[list[Block], Field(min_length=1)]

    def __init__(self, name: str, blocks: list[Block], /, **data) -> None:
        super().__init__(name=name, blocks=blocks, **data)

    def _label(self) -> str:
        return f"stage {self.name!r}"

    @model_validator(mode="after")
    def _check_block_names(self) -> Self:
        _check_block_names(f"stage {self.name!r}", self.blocks)
        return self


def _check_block_names(where: str, blocks: list[Block]) -> None:
    names = [b.block_name for b in blocks]
    for name in names:
        _check_name(name)
    if dupes := _duplicates(names):
        raise ValueError(
            f"{where}: duplicate block name {dupes[0]!r}; "
            "give blocks of the same type a distinct name="
        )


def _check_handlers(where: str, owner: BaseModel, fields: tuple[str, ...]) -> None:
    for field in fields:
        handlers: list[Block] = getattr(owner, field)
        _check_block_names(f"{where} {field}", handlers)
        for handler in handlers:
            if not _is_plain(handler):
                raise ValueError(
                    f"{where} {field}: block {handler.block_name!r}: "
                    "handler blocks cannot set when, status, or handlers"
                )


def _is_plain(block: Block) -> bool:
    """True if the block sets no step features: it only runs (handlers, the status reporter)."""
    has_handlers = any(getattr(block, f) for f in HANDLER_FIELDS)
    return block.when is None and block.status is None and not has_handlers


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
    """A pipeline: ordered stages of parallel blocks, compiled to one WorkflowTemplate.

    `on_success` and `on_failure` blocks run in parallel after the pipeline finishes, depending
    on its result. They can use `{{ step.name }}` and `{{ step.status }}`, which refer to the
    workflow. A failing handler fails the pipeline.

    `status` contexts (on the pipeline, stages, and blocks) are posted by `status_reporter`, an
    ordinary block that makes the API call. It reads `{{ status.context }}`, `{{ status.state }}`
    ("pending", "success", "failure", "error"), and `{{ status.description }}`, and the commit
    from the required `repo` and `sha` params.

    `triggers` run the pipeline on schedules (see Trigger).
    """

    name: Name
    params: list[Param] = []
    stages: Annotated[list[Stage], Field(min_length=1)]
    on_success: list[Block] = []
    on_failure: list[Block] = []
    status: str | None = None
    status_reporter: Block | None = None
    triggers: list[Trigger] = []

    @property
    def status_contexts(self) -> list[str]:
        """Every commit-status context, in pipeline order."""
        steps = [step for stage in self.stages for step in (stage, *stage.blocks)]
        return [s.status for s in (self, *steps) if s.status is not None]

    @model_validator(mode="after")
    def _check_unique_names(self) -> Self:
        if dupes := _duplicates([s.name for s in self.stages]):
            raise ValueError(f"pipeline {self.name!r}: duplicate stage name {dupes[0]!r}")
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
        if not _is_plain(self.status_reporter):
            raise ValueError(
                f"pipeline {self.name!r}: the status_reporter cannot set when, status, or handlers"
            )
        required = {p.name for p in self.params if p.required}
        if not {"repo", "sha"} <= required:
            raise ValueError(
                f"pipeline {self.name!r}: status contexts need required params 'repo' and 'sha' "
                "(the commit to report on)"
            )
        return self

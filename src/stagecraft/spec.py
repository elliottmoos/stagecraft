"""Spec layer: the models pipeline authors write. See DESIGN.md sections 5 and 6."""

import re
from abc import abstractmethod
from typing import Annotated, Self

from hera.workflows import Container, Script
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

_DNS_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


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


class Block(_Model):
    """Base type for blocks. A subclass declares its fields and compiles to one Hera template.

    String fields may contain placeholders such as `{{ param.repo }}`; the compiler lowers them
    before calling `compile`. The compiler also names the returned template.
    """

    name: Name | None = None

    @property
    def block_name(self) -> str:
        return self.name or _kebab(type(self).__name__)

    @abstractmethod
    def compile(self) -> Container | Script: ...


class Stage(_Model):
    """An ordered step of a pipeline. Its blocks run in parallel."""

    name: Name
    blocks: Annotated[list[Block], Field(min_length=1)]

    def __init__(self, name: str, blocks: list[Block], /, **data) -> None:
        super().__init__(name=name, blocks=blocks, **data)

    @model_validator(mode="after")
    def _check_block_names(self) -> Self:
        names = [b.block_name for b in self.blocks]
        for name in names:
            _check_name(name)
        if dupes := _duplicates(names):
            raise ValueError(
                f"stage {self.name!r}: duplicate block name {dupes[0]!r}; "
                "give blocks of the same type a distinct name="
            )
        return self


class Pipeline(_Model):
    """A pipeline: ordered stages of parallel blocks, compiled to one WorkflowTemplate."""

    name: Name
    params: list[Param] = []
    stages: Annotated[list[Stage], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_unique_names(self) -> Self:
        if dupes := _duplicates([s.name for s in self.stages]):
            raise ValueError(f"pipeline {self.name!r}: duplicate stage name {dupes[0]!r}")
        if dupes := _duplicates([p.name for p in self.params]):
            raise ValueError(f"pipeline {self.name!r}: duplicate param name {dupes[0]!r}")
        return self

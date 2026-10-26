"""Results passed between steps. See DESIGN.md section 16.

A step's output parameters and artifacts, declared the Hera way on the template its action
compiles to, are published as global outputs named `stagecraft-<stage>-<step>-<output>`. Later
steps read parameters through `{{ steps.<stage>.<step>.<output> }}` and receive artifacts bound
with `Step(inputs={"<input>": "<stage>.<step>.<artifact>"})`. Global outputs are readable from
every stage, hook, and `onExit` (verified on Argo 4.1.6), so nothing is routed through stage DAGs.
Artifact paths that are relative are inside the source checkout.
"""

import copy
import dataclasses
import posixpath
from collections.abc import Mapping

from hera.workflows import Artifact, Container, Parameter, Script

from stagecraft.compile import source
from stagecraft.compile.placeholders import Unavailable
from stagecraft.errors import CompileError

Key = tuple[str, str]  # (stage, step)


@dataclasses.dataclass(frozen=True)
class Produced:
    """What a step publishes, and whether it may be skipped (its step or stage has `when`)."""

    parameters: tuple[str, ...]
    artifacts: tuple[str, ...]
    conditional: bool


def publish(
    template: Container | Script, key: Key, has_source: bool, conditional: bool, where: str
) -> tuple[Container | Script, Produced]:
    """`template` with its outputs published globally and relative artifact and output paths
    placed in the checkout, and what it produces."""
    outputs = [
        _with(_in_checkout(o, has_source, where), global_name=_global(key, o.name))
        for o in _listed(template.outputs, "outputs", where)
    ]
    inputs = [
        _in_checkout(i, has_source, where) if isinstance(i, Artifact) else i
        for i in _listed(template.inputs, "inputs", where)
    ]
    produced = Produced(
        parameters=tuple(o.name for o in outputs if isinstance(o, Parameter)),
        artifacts=tuple(o.name for o in outputs if isinstance(o, Artifact)),
        conditional=conditional,
    )
    template = dataclasses.replace(template, outputs=outputs or None, inputs=inputs or None)
    return template, produced


def bind(
    template: Container | Script,
    bindings: Mapping[str, str],
    produced: Mapping[Key, Produced],
    where: str,
) -> list[Artifact]:
    """The task arguments feeding `template`'s input artifacts from earlier steps' outputs.

    Every input artifact without a source of its own must be bound, and only those can be."""
    unbound = [
        i.name
        for i in _listed(template.inputs, "inputs", where)
        if type(i) is Artifact and not (i.from_ or i.from_expression or i.optional)
    ]
    for name in bindings:
        if name not in unbound:
            raise CompileError(f"{where}: the action has no input artifact {name!r} to bind")
    for name in unbound:
        if name not in bindings:
            raise CompileError(
                f"{where}: input artifact {name!r} is not bound; "
                f"set inputs={{{name!r}: '<stage>.<step>.<artifact>'}}"
            )
    arguments = []
    for name, reference in bindings.items():
        stage, step, artifact = reference.split(".")
        producer = produced.get((stage, step))
        if producer is None or artifact not in producer.artifacts:
            raise CompileError(
                f"{where}: input {name!r}: {reference!r} is not an artifact of a step in an "
                f"earlier stage; available: {sorted(_artifact_refs(produced))}"
            )
        if producer.conditional:
            raise CompileError(f"{where}: input {name!r}: {reference!r} {_SKIPPABLE}")
        arguments.append(
            Artifact(
                name=name,
                from_=f"{{{{workflow.outputs.artifacts.{_global((stage, step), artifact)}}}}}",
            )
        )
    return arguments


def scope(produced: Mapping[Key, Produced]) -> dict[str, str]:
    """The `{{ steps.<stage>.<step>.<output> }}` fields for parameters `produced` so far."""
    fields: dict[str, str] = {}
    for key, outputs in produced.items():
        for name in outputs.parameters:
            reference = f"{{{{workflow.outputs.parameters.{_global(key, name)}}}}}"
            fields[".".join((*key, name))] = (
                Unavailable(_SKIPPABLE) if outputs.conditional else reference
            )
    return fields


_SKIPPABLE = "comes from a step that may be skipped (it or its stage has when=), leaving no value"


def _global(key: Key, name: str) -> str:
    return f"stagecraft-{key[0]}-{key[1]}-{name}"


def _artifact_refs(produced: Mapping[Key, Produced]) -> list[str]:
    return [".".join((*key, a)) for key, outputs in produced.items() for a in outputs.artifacts]


def _in_checkout[T](io: T, has_source: bool, where: str) -> T:
    """An artifact, or an output parameter read from a file, with a relative path placed in the
    checkout."""
    if isinstance(io, Artifact):
        path, kind = io.path, "artifact"
    elif isinstance(io, Parameter) and io.value_from is not None:
        path, kind = io.value_from.path, "output"
    else:
        return io
    if path is None or path.startswith("/"):
        return io
    if not has_source:
        raise CompileError(
            f"{where}: relative {kind} path {path!r} needs a pipeline source (relative paths "
            "are inside its checkout)"
        )
    full = posixpath.join(source.PATH, path)
    if isinstance(io, Artifact):
        return _with(io, path=full)
    return _with(io, value_from=io.value_from.model_copy(update={"path": full}))


def _with[T](io: T, **fields) -> T:
    """A copy of a Hera input or output with `fields` set. (dataclasses.replace re-runs Hera's
    validation, which rejects a Parameter whose value it filled in itself.)"""
    io = copy.copy(io)
    for name, value in fields.items():
        setattr(io, name, value)
    return io


def _listed(value, field: str, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CompileError(f"{where}: an action's {field} must be a list")
    return value

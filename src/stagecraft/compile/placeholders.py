"""Lowering of author placeholders (`{{ param.x }}`) to Argo expressions. See DESIGN.md §5."""

import re
from collections.abc import Callable, Collection, Mapping

from pydantic import BaseModel

from stagecraft.errors import CompileError

# Only stagecraft namespaces are matched; native Argo expressions pass through untouched.
_PLACEHOLDER = re.compile(
    r"\{\{\s*(param|setup|source|step|steps|status|values)\.([A-Za-z0-9_.-]+)\s*\}\}"
)

# Namespaces that only exist in some places, and why a placeholder outside them fails.
_SCOPED = {
    "setup": "needs a setup step (Pipeline(setup=...))",
    "source": "needs a pipeline source (Pipeline(source=...))",
    "step": "is only available in handlers (on_success, on_failure, finally_)",
    "steps": "is only available in steps, conditions, and handlers, from earlier stages",
    "status": "is only available in the status_reporter",
}

# What an unknown field means, where the generic "unknown field" would mislead.
_UNKNOWN = {"steps": "is not an output of a step in an earlier stage"}


class Unavailable(str):
    """A scoped field that exists but can't be used; the string says why."""


Scopes = Mapping[str, Mapping[str, str]]
# Resolves `{{ values.a.b }}` to a recorded value reference (CompileContext.value).
Values = Callable[[str], str]


def lower(
    text: str,
    params: Collection[str],
    scopes: Scopes | None = None,
    quote_params: bool = False,
    values: Values | None = None,
) -> str:
    """Lower placeholders in `text`.

    `values` resolves `{{ values.a.b }}` to a recorded value reference (CompileContext.value).

    `scopes` maps each scoped namespace available here ("setup" and "source" when the pipeline
    has them, "step" in handlers, "status" in the status reporter) to its fields and the Argo
    expressions that provide them.

    `quote_params` is for `when` expressions: params (and scoped fields and values) are strings,
    but Argo parses a bare `true` or `42` as a boolean or number (verified on 4.1.6:
    `true == 'true'` is false). Quoting makes `{{ param.deploy }} == 'true'` compare strings.
    """
    scopes = scopes or {}

    def replace(match: re.Match[str]) -> str:
        namespace, path = match.groups()
        if namespace == "param":
            if path not in params:
                raise CompileError(f"undeclared param {path!r}")
            expression = f"{{{{workflow.parameters.{path}}}}}"
            return f"'{expression}'" if quote_params else expression
        if namespace in _SCOPED:
            fields = scopes.get(namespace)
            if fields is None:
                raise CompileError(f"'{{{{ {namespace}.{path} }}}}' {_SCOPED[namespace]}")
            placeholder = f"'{{{{ {namespace}.{path} }}}}'"
            if path not in fields:
                if namespace in _UNKNOWN:
                    raise CompileError(
                        f"{placeholder} {_UNKNOWN[namespace]}; available: {sorted(fields)}"
                    )
                raise CompileError(
                    f"unknown {namespace} field {path!r}; available: {sorted(fields)}"
                )
            value = fields[path]
            if isinstance(value, Unavailable):
                raise CompileError(f"{placeholder} {value}")
            return f"'{value}'" if quote_params else value
        if values is None:
            raise CompileError(f"'{{{{ values.{path} }}}}' needs a compile context")
        return f"'{values(path)}'" if quote_params else values(path)

    return _PLACEHOLDER.sub(replace, text)


def lower_model[M: BaseModel](
    model: M,
    params: Collection[str],
    scopes: Scopes | None = None,
    values: Values | None = None,
) -> M:
    """Return a copy of `model` with placeholders lowered in every string it contains."""
    return model.model_copy(update={k: _lower_value(v, params, scopes, values) for k, v in model})


def _lower_value(value, params: Collection[str], scopes: Scopes | None, values: Values | None):
    match value:
        case str():
            return lower(value, params, scopes, values=values)
        case BaseModel():
            return lower_model(value, params, scopes, values)
        case list() | tuple():
            return type(value)(_lower_value(v, params, scopes, values) for v in value)
        case dict():
            return {k: _lower_value(v, params, scopes, values) for k, v in value.items()}
        case _:
            return value

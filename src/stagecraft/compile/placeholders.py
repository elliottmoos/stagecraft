"""Lowering of author placeholders (`{{ param.x }}`) to Argo expressions. See DESIGN.md §5."""

import re
from collections.abc import Collection, Mapping

from pydantic import BaseModel

from stagecraft.errors import CompileError

# Only stagecraft namespaces are matched; native Argo expressions pass through untouched.
_PLACEHOLDER = re.compile(r"\{\{\s*(param|step|status|values)\.([A-Za-z0-9_.-]+)\s*\}\}")

# Namespaces that only exist in some blocks, and where they exist.
_SCOPED = {
    "step": "handler blocks (on_success, on_failure, finally_)",
    "status": "the status_reporter block",
}

Scopes = Mapping[str, Mapping[str, str]]


def lower(
    text: str,
    params: Collection[str],
    scopes: Scopes | None = None,
    quote_params: bool = False,
) -> str:
    """Lower placeholders in `text`.

    `scopes` maps each scoped namespace available here ("step" in handlers, "status" in the
    status reporter) to its fields and the Argo expressions that provide them.

    `quote_params` is for `when` expressions: params are strings, but Argo parses a bare `true`
    or `42` as a boolean or number (verified on 4.1.6: `true == 'true'` is false). Quoting makes
    `{{ param.deploy }} == 'true'` compare strings.
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
                raise CompileError(
                    f"'{{{{ {namespace}.{path} }}}}' is only available in {_SCOPED[namespace]}"
                )
            if path not in fields:
                raise CompileError(
                    f"unknown {namespace} field {path!r}; available: {sorted(fields)}"
                )
            return fields[path]
        raise CompileError(f"'{{{{ {namespace}.{path} }}}}' placeholders are not supported yet")

    return _PLACEHOLDER.sub(replace, text)


def lower_model[M: BaseModel](
    model: M,
    params: Collection[str],
    scopes: Scopes | None = None,
    exclude: Collection[str] = (),
) -> M:
    """Return a copy of `model` with placeholders lowered in every string it contains.

    Fields in `exclude` are left as they are (handler blocks, which are lowered in their own
    context).
    """
    return model.model_copy(
        update={k: _lower_value(v, params, scopes) for k, v in model if k not in exclude}
    )


def _lower_value(value, params: Collection[str], scopes: Scopes | None):
    match value:
        case str():
            return lower(value, params, scopes)
        case BaseModel():
            return lower_model(value, params, scopes)
        case list() | tuple():
            return type(value)(_lower_value(v, params, scopes) for v in value)
        case dict():
            return {k: _lower_value(v, params, scopes) for k, v in value.items()}
        case _:
            return value

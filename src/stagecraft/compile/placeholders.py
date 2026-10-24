"""Lowering of author placeholders (`{{ param.x }}`) to Argo expressions. See DESIGN.md §5."""

import re
from collections.abc import Collection

from pydantic import BaseModel

from stagecraft.errors import CompileError

# Only stagecraft namespaces are matched; native Argo expressions pass through untouched.
_PLACEHOLDER = re.compile(r"\{\{\s*(param|step|values)\.([A-Za-z0-9_.-]+)\s*\}\}")


def lower(text: str, params: Collection[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        namespace, path = match.groups()
        if namespace != "param":
            raise CompileError(f"'{{{{ {namespace}.{path} }}}}' placeholders are not supported yet")
        if path not in params:
            raise CompileError(f"undeclared param {path!r}")
        return f"{{{{workflow.parameters.{path}}}}}"

    return _PLACEHOLDER.sub(replace, text)


def lower_model[M: BaseModel](model: M, params: Collection[str]) -> M:
    """Return a copy of `model` with placeholders lowered in every string it contains."""
    return model.model_copy(update={k: _lower_value(v, params) for k, v in model})


def _lower_value(value, params: Collection[str]):
    match value:
        case str():
            return lower(value, params)
        case BaseModel():
            return lower_model(value, params)
        case list() | tuple():
            return type(value)(_lower_value(v, params) for v in value)
        case dict():
            return {k: _lower_value(v, params) for k, v in value.items()}
        case _:
            return value

"""The chart's values contract: its values.schema.json. See DESIGN.md section 8.

Stagecraft only consumes the contract. It resolves dotted value paths through `properties`,
`required`, `type`, and local `$ref`s, and refuses anything it cannot check exactly.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from stagecraft.errors import CompileError, LoadError

# Constructs that make a path's type or presence conditional. Rather than guess, reject them on
# any schema node a referenced path passes through.
_UNSUPPORTED = ("oneOf", "anyOf", "allOf", "not", "if", "patternProperties", "dependentSchemas")
# Keys must be usable as `.Values.a.b` in a Helm template.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ValuesContract:
    """A parsed values schema. `prefix` is the dotted key the chart nests stagecraft values under,
    if any; it is prepended to every referenced path."""

    schema: Mapping
    prefix: str = ""

    @classmethod
    def load(cls, path: Path, prefix: str = "") -> "ValuesContract":
        try:
            schema = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise LoadError(f"{path}: cannot read values schema: {e}") from e
        return cls(schema, prefix)

    def full_path(self, path: str) -> str:
        return f"{self.prefix}.{path}" if self.prefix else path

    def types(self, path: str) -> set[str]:
        """The schema types of the value at `path`.

        Raises CompileError if the path is not declared, is not required at every level, or
        passes through a construct this resolver does not support.
        """
        full = self.full_path(path)
        node = self._deref(self.schema)
        walked: list[str] = []
        for key in full.split("."):
            if not _IDENTIFIER.fullmatch(key):
                raise CompileError(f"value {full!r}: key {key!r} must be an identifier")
            self._check_supported(node, walked)
            if key not in node.get("properties", {}):
                where = ".".join(walked) or "the root"
                raise CompileError(
                    f"value {full!r} is not declared in the values schema "
                    f"(no property {key!r} under {where})"
                )
            if key not in node.get("required", []):
                raise CompileError(
                    f"value {full!r} is optional in the values schema ({key!r} is not required); "
                    "only required values can be referenced"
                )
            walked.append(key)
            node = self._deref(node["properties"][key])
        self._check_supported(node, walked)
        declared = node.get("type")
        if declared is None:
            raise CompileError(f"value {full!r} has no type in the values schema")
        return {declared} if isinstance(declared, str) else set(declared)

    def _deref(self, node: Mapping) -> Mapping:
        seen = set()
        while "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise CompileError(f"unsupported $ref {ref!r}: only local references are supported")
            if ref in seen:
                raise CompileError(f"circular $ref {ref!r} in the values schema")
            seen.add(ref)
            node = self.schema
            for part in ref[2:].split("/"):
                node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    @staticmethod
    def _check_supported(node: Mapping, walked: list[str]) -> None:
        for keyword in _UNSUPPORTED:
            if keyword in node:
                where = ".".join(walked) or "the root"
                raise CompileError(f"unsupported schema construct {keyword!r} at {where!r}")

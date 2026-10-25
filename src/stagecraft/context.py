"""CompileContext: the one object the compiler passes to every action. See DESIGN.md section 16.

Actions get Helm values only through `CompileContext.value`, never by writing Helm syntax. A value
reference is returned as a marker string, so it fits any string field an action builds; the
serializer turns markers into Helm expressions after escaping Argo expressions. A marker records
whether the value is a string or another scalar, which Helm must print with `toJson`.

The context records every reference and its type. The generated chart's values schema is built
from those records, so each referenced value is required, with the type it is used as.
"""

import re
from dataclasses import dataclass, field

from stagecraft.errors import CompileError

# <<values:a.b>> for a string, <<values:a.b|json>> for a number or boolean.
VALUE_MARKER = re.compile(r"<<values:([A-Za-z0-9_.]+)(\|json)?>>")

SCALARS = (str, int, float, bool)
# Keys must be usable as `.Values.a.b` in a Helm template.
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
# Framework values (DESIGN.md section 16) live under this key; actions and pipelines can't use it.
RESERVED = "stagecraft"


@dataclass
class CompileContext:
    references: dict[str, type] = field(default_factory=dict)

    def value(self, path: str, expected: type = str) -> str:
        """Reference the Helm value at `path` (dotted), used as `expected`: str, int, float, or
        bool. Raises CompileError if `path` is not a dotted identifier, is reserved, or is
        already used with another type or as an object."""
        if path.split(".")[0] == RESERVED:
            raise CompileError(f"value {path!r}: {RESERVED!r} is reserved for framework values")
        return self._reference(path, expected)

    def framework_value(self, path: str, expected: type = str) -> str:
        """Reference one of Stagecraft's own values, under the reserved key. Compiler only."""
        assert path.split(".")[0] == RESERVED, path
        return self._reference(path, expected)

    def _reference(self, path: str, expected: type) -> str:
        if expected not in SCALARS:
            raise CompileError(
                f"value {path!r}: only scalar values (str, int, float, bool) are supported, "
                f"not {expected.__name__}"
            )
        if not _PATH.fullmatch(path):
            raise CompileError(
                f"value {path!r}: paths must be dotted identifiers ([A-Za-z_][A-Za-z0-9_]*), "
                "so they work as .Values.a.b"
            )
        known = self.references.get(path)
        if known is not None and known is not expected:
            raise CompileError(
                f"value {path!r} is used as {expected.__name__} here but as {known.__name__} "
                "elsewhere; give it one type"
            )
        for other in self.references:
            if other.startswith(f"{path}.") or path.startswith(f"{other}."):
                raise CompileError(
                    f"value {path!r}: {min(path, other, key=len)!r} would be both a value and an "
                    f"object (also used: {other!r})"
                )
        self.references[path] = expected
        return f"<<values:{path}>>" if expected is str else f"<<values:{path}|json>>"

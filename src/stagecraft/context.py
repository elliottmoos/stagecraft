"""CompileContext: the one object the compiler passes to every block. See DESIGN.md section 8.

Blocks get Helm values only through `CompileContext.value`, never by writing Helm syntax. A value
reference is returned as a marker string, so it fits any field a block builds; the serializer
turns markers into Helm expressions after escaping Argo expressions.
"""

import re
from dataclasses import dataclass

from stagecraft.contract import ValuesContract
from stagecraft.errors import CompileError

VALUE_MARKER = re.compile(r"<<values:([A-Za-z0-9_.]+)>>")

_SCALARS = {"string", "integer", "number", "boolean"}
_EXPECTED = {
    str: ("string", {"string"}),
    int: ("integer", {"integer"}),
    float: ("number", {"number", "integer"}),
    bool: ("boolean", {"boolean"}),
}


@dataclass(frozen=True)
class CompileContext:
    contract: ValuesContract

    def value(self, path: str, expected: type | None = None) -> str:
        """Reference the required Helm value at `path` (dotted, relative to the values prefix).

        `expected` is the Python type the block uses the value as: str, int, float, or bool.
        None accepts any scalar, which is how `{{ values.a.b }}` placeholders are checked.
        Raises CompileError if the contract does not guarantee a value of that type.
        """
        if expected is not None and expected not in _EXPECTED:
            raise CompileError(
                f"value {path!r}: only scalar values (str, int, float, bool) are supported, "
                f"not {expected.__name__}"
            )
        types = self.contract.types(path)
        full = self.contract.full_path(path)
        if "null" in types:
            raise CompileError(f"value {full!r} is nullable in the values schema")
        usage, allowed = _EXPECTED[expected] if expected else ("a scalar", _SCALARS)
        if not types <= allowed:
            declared = "/".join(sorted(types))
            raise CompileError(
                f"value {full!r} is {declared} in the values schema, but is used as {usage}"
            )
        return f"<<values:{full}>>"

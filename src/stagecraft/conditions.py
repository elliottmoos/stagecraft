"""Typed conditions for `when`. See DESIGN.md section 16.

    when=param("deploy") == "true"
    when=(setup("event") == "push") & ~(param("env") == "prod")

A condition renders to the placeholder string a hand-written `when` would hold
(`{{ param.deploy }} == 'true'`), so it is lowered and checked exactly like one: references are
validated when the pipeline compiles, and operands are quoted so Argo compares strings. Everything
compared is a string, so literals are strings too.
"""

import re

_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_PARENTHESES = (
    "combine comparisons in parentheses: (param('a') == 'x') & (param('b') == 'y'), because "
    "& and | bind tighter than == and !="
)


class Condition:
    """Something `when=` takes. Combine conditions with & (and), | (or), and ~ (not)."""

    def render(self) -> str:
        raise NotImplementedError

    def __and__(self, other: "Condition") -> "Condition":
        return _Binary("&&", self, _condition(other))

    def __or__(self, other: "Condition") -> "Condition":
        return _Binary("||", self, _condition(other))

    def __invert__(self) -> "Condition":
        return _Not(self)

    def __rand__(self, other: object) -> "Condition":
        raise TypeError(_PARENTHESES)

    __ror__ = __rand__

    def __bool__(self) -> bool:
        raise TypeError("use & and | to combine conditions, not `and` and `or`")


class Operand:
    """A value a condition compares: a param, a setup or step output, a source field, or a Helm
    value. Compare it with == or != to a string or another operand."""

    def __init__(self, namespace: str, path: str) -> None:
        self._placeholder = f"{{{{ {namespace}.{path} }}}}"

    def render(self) -> str:
        return self._placeholder

    def __eq__(self, other: object) -> Condition:  # type: ignore[override]
        return _Compare(self, "==", other)

    def __ne__(self, other: object) -> Condition:  # type: ignore[override]
        return _Compare(self, "!=", other)

    __hash__ = None  # type: ignore[assignment]

    def __and__(self, other: object) -> Condition:
        raise TypeError(_PARENTHESES)

    __rand__ = __or__ = __ror__ = __and__

    def __bool__(self) -> bool:
        raise TypeError("an operand isn't a condition: compare it, as in param('x') == 'y'")


def param(name: str) -> Operand:
    """A declared param."""
    if not _PARAM_NAME.fullmatch(name):
        raise ValueError(f"param name {name!r} must match {_PARAM_NAME.pattern}")
    return Operand("param", name)


def setup(output: str) -> Operand:
    """An output of the pipeline's setup step."""
    return Operand("setup", output)


def output(path: str) -> Operand:
    """An output parameter of a step in an earlier stage, as "<stage>.<step>.<output>"."""
    return Operand("steps", path)


def source(field: str) -> Operand:
    """A field of the pipeline's source ("sha", "revision", "url") or of a stage source
    ("<name>.sha")."""
    return Operand("source", field)


def value(path: str) -> Operand:
    """A Helm value, by dotted path."""
    return Operand("values", path)


class _Compare(Condition):
    def __init__(self, left: Operand, op: str, right: object) -> None:
        if isinstance(right, Condition):
            raise TypeError(_PARENTHESES)
        if not isinstance(right, Operand | str):
            raise TypeError(
                f"compare with a string, not {right!r}: params, outputs, and values are strings "
                "(write 'true', not True)"
            )
        if isinstance(right, str) and "'" in right:
            raise ValueError(f"a literal can't contain a single quote: {right!r}")
        self._text = f"{left.render()} {op} {_render(right)}"

    def render(self) -> str:
        return self._text


class _Binary(Condition):
    def __init__(self, op: str, left: Condition, right: Condition) -> None:
        self._text = f"({left.render()}) {op} ({right.render()})"

    def render(self) -> str:
        return self._text


class _Not(Condition):
    def __init__(self, inner: Condition) -> None:
        self._text = f"!({inner.render()})"

    def render(self) -> str:
        return self._text


def _condition(other: object) -> Condition:
    if not isinstance(other, Condition):
        raise TypeError(_PARENTHESES)
    return other


def _render(operand: Operand | str) -> str:
    return operand.render() if isinstance(operand, Operand) else f"'{operand}'"

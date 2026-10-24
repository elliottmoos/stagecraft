"""Escaping of Argo expressions so Helm passes them through. See DESIGN.md section 8.

Argo and Helm both use `{{ }}`. Every Argo expression in the YAML text is wrapped in a Helm
action that prints it literally, so `helm template` reproduces the original text exactly.
"""

import json
import re

from stagecraft.errors import SerializeError

# Single-line on purpose: an expression split across lines is rejected below rather than guessed.
_EXPRESSION = re.compile(r"\{\{.*?\}\}")


def escape_argo(text: str) -> str:
    parts = []
    position = 0
    for match in _EXPRESSION.finditer(text):
        parts.append(_check_plain(text[position : match.start()]))
        parts.append(_helm_literal(match.group()))
        position = match.end()
    parts.append(_check_plain(text[position:]))
    return "".join(parts)


def _helm_literal(expression: str) -> str:
    if "`" not in expression:
        return "{{`" + expression + "`}}"
    # Go template string literals accept JSON string escapes; keep non-ASCII as-is because Go
    # rejects the surrogate-pair escapes json.dumps would emit for astral characters.
    return "{{" + json.dumps(expression, ensure_ascii=False) + "}}"


def _check_plain(segment: str) -> str:
    for delimiter in ("{{", "}}"):
        if delimiter in segment:
            line = segment[segment.index(delimiter) :].splitlines()[0]
            raise SerializeError(f"unbalanced {delimiter!r} in rendered output near {line!r}")
    return segment

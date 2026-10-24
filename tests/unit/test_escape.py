import pytest

from stagecraft.errors import SerializeError
from stagecraft.serialize.escape import escape_argo


def test_text_without_expressions_is_unchanged():
    assert escape_argo("image: alpine:3.22\n") == "image: alpine:3.22\n"


def test_argo_expression_becomes_helm_raw_string():
    assert escape_argo("echo {{workflow.name}}") == "echo {{`{{workflow.name}}`}}"


def test_every_expression_is_escaped():
    assert escape_argo("{{a}} and {{b}}") == "{{`{{a}}`}} and {{`{{b}}`}}"


def test_expression_containing_backtick_uses_quoted_helm_string():
    assert escape_argo("{{=`x`}}") == '{{"{{=`x`}}"}}'


def test_quoted_helm_string_escapes_quotes_and_backslashes():
    assert escape_argo('{{=`a` + "\\n"}}') == '{{"{{=`a` + \\"\\\\n\\"}}"}}'


@pytest.mark.parametrize("text", ["echo {{ oops", "echo oops }}", "{{ multi\nline }}"])
def test_unbalanced_braces_are_rejected(text):
    with pytest.raises(SerializeError, match="unbalanced"):
        escape_argo(text)

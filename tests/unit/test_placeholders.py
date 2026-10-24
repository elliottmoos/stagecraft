import pytest

from stagecraft.compile.placeholders import lower
from stagecraft.errors import CompileError


def test_param_placeholder_lowers_to_workflow_parameter():
    assert lower("git clone {{ param.repo }}", params={"repo"}) == (
        "git clone {{workflow.parameters.repo}}"
    )


def test_placeholder_whitespace_is_optional():
    assert lower("{{param.repo}}/{{  param.sha  }}", params={"repo", "sha"}) == (
        "{{workflow.parameters.repo}}/{{workflow.parameters.sha}}"
    )


def test_undeclared_param_fails():
    with pytest.raises(CompileError, match="undeclared param 'nope'"):
        lower("{{ param.nope }}", params={"repo"})


def test_values_placeholders_resolve_through_the_given_resolver():
    assert lower("#{{ values.notify.channel }}", params=set(), values=lambda p: f"<{p}>") == (
        "#<notify.channel>"
    )


def test_values_placeholders_need_a_resolver():
    with pytest.raises(CompileError, match="needs a values contract"):
        lower("{{ values.notify.channel }}", params=set())


def test_step_placeholders_lower_to_the_given_step_context():
    step = {"name": "{{workflow.name}}", "status": "{{workflow.status}}"}

    assert lower("{{ step.name }} is {{ step.status }}", params=set(), scopes={"step": step}) == (
        "{{workflow.name}} is {{workflow.status}}"
    )


def test_step_placeholders_need_a_step_context():
    with pytest.raises(CompileError, match="only available in handler blocks"):
        lower("{{ step.status }}", params=set())


def test_native_argo_expressions_pass_through():
    text = "{{inputs.parameters.x}} {{=sprig.trim(workflow.name)}} {{ workflow.status }}"

    assert lower(text, params=set()) == text

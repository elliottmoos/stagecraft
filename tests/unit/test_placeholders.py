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


@pytest.mark.parametrize("text", ["{{ values.notify.channel }}", "{{ step.status }}"])
def test_placeholders_from_later_slices_fail_instead_of_passing_through(text):
    with pytest.raises(CompileError, match="not supported yet"):
        lower(text, params=set())


def test_native_argo_expressions_pass_through():
    text = "{{inputs.parameters.x}} {{=sprig.trim(workflow.name)}} {{ workflow.status }}"

    assert lower(text, params=set()) == text

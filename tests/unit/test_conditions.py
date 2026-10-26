"""Typed conditions: `when=param("deploy") == "true"` instead of hand-quoted Argo expressions
(DESIGN.md section 16)."""

import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.conditions import output, param, setup, source, value
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Source, Stage, Step

from fixtures.actions import Build, Shell

DEPLOY = "'{{workflow.parameters.deploy}}'"


def when_of(condition, **pipeline_fields):
    pipeline = Pipeline(
        name="p",
        params=[Param("deploy", default="false"), Param("env", default="staging")],
        stages=[Stage("s", [Shell(script="true")], when=condition)],
        **pipeline_fields,
    )
    m = compile_pipeline(pipeline, CompileContext()).to_dict()
    main = next(t for t in m["spec"]["templates"] if t["name"] == "main")
    return next(t for t in main["dag"]["tasks"] if t["name"] == "s")["when"]


def test_a_comparison_renders_to_the_placeholder_form():
    assert (param("deploy") == "true").render() == "{{ param.deploy }} == 'true'"
    assert (param("deploy") != "true").render() == "{{ param.deploy }} != 'true'"


def test_conditions_lower_like_hand_written_when_strings():
    assert when_of(param("deploy") == "true") == f"{DEPLOY} == 'true'"


def test_conditions_combine_with_and_or_and_not():
    condition = (param("deploy") == "true") & ~((param("env") == "prod") | (param("env") == "qa"))

    assert when_of(condition) == (
        f"({DEPLOY} == 'true') && (!(('{{{{workflow.parameters.env}}}}' == 'prod')"
        " || ('{{workflow.parameters.env}}' == 'qa')))"
    )


def test_two_operands_can_be_compared():
    assert (param("env") == setup("env")).render() == "{{ param.env }} == {{ setup.env }}"


def test_every_kind_of_operand_renders_to_its_placeholder():
    assert [
        o.render()
        for o in (
            setup("event"),
            output("build.binary.digest"),
            source("sha"),
            source("fixtures.sha"),
            value("deploy.env"),
        )
    ] == [
        "{{ setup.event }}",
        "{{ steps.build.binary.digest }}",
        "{{ source.sha }}",
        "{{ source.fixtures.sha }}",
        "{{ values.deploy.env }}",
    ]


def test_operands_are_checked_when_the_pipeline_compiles():
    with pytest.raises(CompileError, match="undeclared param 'nope'"):
        when_of(param("nope") == "x")


def test_conditions_can_use_earlier_step_outputs():
    pipeline = Pipeline(
        name="p",
        source=Source(url="https://x/y.git", revision="main"),
        stages=[
            Stage("build", [Step("binary", Build())]),
            Stage("ship", [Shell(script="true")], when=output("build.binary.digest") != ""),
        ],
    )

    m = compile_pipeline(pipeline, CompileContext()).to_dict()

    main = next(t for t in m["spec"]["templates"] if t["name"] == "main")
    ship = next(t for t in main["dag"]["tasks"] if t["name"] == "ship")
    assert ship["when"] == "'{{workflow.outputs.parameters.stagecraft-build-binary-digest}}' != ''"


def test_helm_values_in_when_are_quoted_like_params():
    # A bare `prod` would be a word to Argo, not a string (the same trap as params).
    assert when_of(value("deploy.env") == "prod") == "'<<values:deploy.env>>' == 'prod'"
    assert when_of("{{ values.deploy.env }} == 'prod'") == "'<<values:deploy.env>>' == 'prod'"


def test_raw_when_strings_still_work():
    assert when_of("{{ param.deploy }} == 'true'") == f"{DEPLOY} == 'true'"


@pytest.mark.parametrize("literal", [True, 1, None])
def test_literals_are_strings(literal):
    with pytest.raises(TypeError, match="compare with a string"):
        param("deploy") == literal  # noqa: B015


def test_literals_cannot_contain_single_quotes():
    with pytest.raises(ValueError, match="can't contain a single quote"):
        param("env") == "it's"  # noqa: B015


def test_python_and_or_are_rejected():
    with pytest.raises(TypeError, match=r"use & and \|"):
        (param("a") == "x") and (param("b") == "y")


def test_precedence_mistakes_are_rejected():
    # `==` binds looser than `&`, so this is param == ("x" & param): an easy mistake.
    with pytest.raises(TypeError, match="parentheses"):
        param("a") == "x" & (param("b") == "y")  # noqa: B015


def test_a_bare_operand_is_not_a_condition():
    with pytest.raises(ValidationError, match="compare it"):
        Stage("s", [Shell(script="true")], when=param("deploy"))


@pytest.mark.parametrize("bad", ["", "a b", "1x"])
def test_param_names_are_checked(bad):
    with pytest.raises(ValueError, match="param name"):
        param(bad)

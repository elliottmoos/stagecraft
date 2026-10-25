import pytest
from pydantic import ValidationError

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell


def test_param_takes_name_positionally():
    param = Param("repo", required=True, description="Repository")

    assert param.name == "repo"
    assert param.required
    assert param.default is None


def test_required_param_cannot_have_default():
    with pytest.raises(ValidationError, match="required param cannot have a default"):
        Param("repo", required=True, default="x")


def test_optional_param_needs_default():
    with pytest.raises(ValidationError, match="optional param needs a default"):
        Param("push")


def test_param_default_must_be_in_enum():
    with pytest.raises(ValidationError, match="default 'maybe' is not in enum"):
        Param("push", default="maybe", enum=["true", "false"])


def test_a_bare_action_is_a_step_named_after_its_class():
    class UnitTests(Shell):
        pass

    stage = Stage("verify", [UnitTests(script="true"), Step("lint", Shell(script="true"))])

    assert [(s.name, type(s.action)) for s in stage.steps] == [
        ("unit-tests", UnitTests),
        ("lint", Shell),
    ]


def test_step_names_must_be_unique_within_stage():
    with pytest.raises(ValidationError, match=r"duplicate step name 'shell'; wrap one in Step"):
        Stage("verify", [Shell(script="a"), Shell(script="b")])


def test_actions_have_no_step_fields():
    # Conditions, statuses, and handlers belong to the step that places an action.
    for field in ("name", "when", "status", "on_failure"):
        with pytest.raises(ValidationError, match=field):
            Shell(script="true", **{field: "x"})


def test_handlers_are_steps_too():
    stage = Stage(
        "s",
        [Shell(script="true")],
        on_failure=[Shell(script="a"), Step("page", Shell(script="b"))],
    )

    assert [h.name for h in stage.on_failure] == ["shell", "page"]


def test_handler_steps_cannot_set_step_features():
    with pytest.raises(ValidationError, match="handler steps cannot set when, status, or handlers"):
        Stage("s", [Shell(script="true")], on_failure=[Step("page", Shell(script="b"), when="x")])


def test_status_reporter_is_an_action():
    with pytest.raises(ValidationError, match="status_reporter"):
        Pipeline(
            name="p",
            params=[Param("repo", required=True), Param("sha", required=True)],
            status="ci/p",
            status_reporter=Step("report", Shell(script="")),
            stages=[Stage("s", [Shell(script="true")])],
        )


def test_stage_needs_a_step():
    with pytest.raises(ValidationError, match="at least 1 item"):
        Stage("verify", [])


def test_stage_names_must_be_unique():
    with pytest.raises(ValidationError, match="duplicate stage name 'verify'"):
        Pipeline(
            name="p",
            stages=[Stage("verify", [Shell(script="a")]), Stage("verify", [Shell(script="b")])],
        )


def test_param_names_must_be_unique():
    with pytest.raises(ValidationError, match="duplicate param name 'repo'"):
        Pipeline(
            name="p",
            params=[Param("repo", required=True), Param("repo", required=True)],
            stages=[Stage("s", [Shell(script="a")])],
        )


def test_pipeline_needs_a_stage():
    with pytest.raises(ValidationError, match="at least 1 item"):
        Pipeline(name="p", stages=[])


@pytest.mark.parametrize("name", ["Verify", "verify_1", "-verify", "verify-", "a" * 64])
def test_names_must_be_dns_labels(name):
    with pytest.raises(ValidationError, match="lowercase"):
        Stage(name, [Shell(script="a")])


def test_there_is_no_shared_workspace():
    # Files cross job boundaries only as artifacts (DESIGN.md section 16).
    with pytest.raises(ValidationError, match="workspace"):
        Shell(script="true", workspace="read")
    with pytest.raises(ValidationError, match="workspace"):
        Step("s", Shell(script="true"), workspace="read")
    with pytest.raises(ValidationError, match="workspace"):
        Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])], workspace={})

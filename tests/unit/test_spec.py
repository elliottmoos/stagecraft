import pytest
from pydantic import ValidationError

from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell


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


def test_block_name_defaults_to_kebab_case_class_name():
    class UnitTests(Shell):
        pass

    assert UnitTests(script="true").block_name == "unit-tests"
    assert Shell(name="lint", script="true").block_name == "lint"


def test_block_names_must_be_unique_within_stage():
    with pytest.raises(ValidationError, match="duplicate block name 'shell'"):
        Stage("verify", [Shell(script="a"), Shell(script="b")])


def test_stage_needs_a_block():
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
        Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])], workspace={})

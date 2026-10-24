import pytest

from fixtures.blocks import Shell
from fixtures.pipelines import smoke
from support import nodes_by_name

pytestmark = pytest.mark.integration


def test_stages_run_in_order_and_blocks_in_a_stage_run_in_parallel(install_and_submit):
    workflow = install_and_submit([smoke.pipeline], "smoke-ci", {"repo": "example/repo"})

    assert workflow["status"]["phase"] == "Succeeded"
    nodes = nodes_by_name(workflow)
    prepare, verify, package = nodes["prepare"], nodes["verify"], nodes["package"]
    assert prepare["finishedAt"] <= verify["startedAt"]
    assert verify["finishedAt"] <= package["startedAt"]
    # Both verify blocks sleep 2s; they overlap only if they ran in parallel.
    lint, unit_tests = nodes["lint"], nodes["unit-tests"]
    assert lint["startedAt"] < unit_tests["finishedAt"]
    assert unit_tests["startedAt"] < lint["finishedAt"]


def test_param_placeholder_reaches_the_block(run_block):
    workflow = run_block(
        Shell(name="check", script='test "{{ param.repo }}" = example/repo'),
        params={"repo": "example/repo"},
    )

    assert workflow["status"]["phase"] == "Succeeded"


def test_failing_block_fails_the_workflow(run_block):
    workflow = run_block(Shell(name="fail", script="exit 3"))

    assert workflow["status"]["phase"] == "Failed"
    assert nodes_by_name(workflow)["fail"]["phase"] == "Failed"

"""The containerset workspace strategy on kind (DESIGN.md sections 7 and 13)."""

import time

import pytest

from fixtures.pipelines import workspace
from support import nodes_by_name

pytestmark = pytest.mark.integration


def pods(workflow: dict) -> list[str]:
    nodes = workflow["status"]["nodes"].values()
    return sorted(n["displayName"] for n in nodes if n["type"] == "Pod")


def test_stages_share_the_workspace_and_parallel_readers_share_one_pod(argo):
    # The readers fail unless they see the clone's file and cannot write; the build reads it a
    # stage later; notify, which declares no access, fails if the workspace is mounted.
    workflow = argo.run(workspace.pipeline)

    assert workflow["status"]["phase"] == "Succeeded"
    assert pods(workflow) == ["build", "clone", "notify", "workspace"]
    nodes = nodes_by_name(workflow)
    assert nodes["lint"]["phase"] == nodes["unit-tests"]["phase"] == "Succeeded"


def test_a_failing_container_fails_the_stage_after_its_siblings_finish(argo):
    workflow = argo.run(workspace.pipeline, {"fail": "true"})

    assert workflow["status"]["phase"] == "Failed"
    nodes = nodes_by_name(workflow)
    assert nodes["unit-tests"]["phase"] == "Failed"
    # lint is still sleeping when unit-tests fails, and completes anyway.
    assert nodes["lint"]["phase"] == "Succeeded"
    assert nodes["verify"]["phase"] == "Failed"
    assert nodes["on-failure-alert"]["phase"] == "Succeeded"
    assert nodes["package"]["phase"] == "Omitted"


def test_the_claim_is_deleted_when_a_failed_run_ends(argo):
    argo.install([workspace.pipeline])
    name = argo.submit(workspace.pipeline.name, {"fail": "true"})
    argo.wait(name)

    claim = f"{name}-workspace"
    deadline = time.monotonic() + 60
    while argo.kubectl("get", "pvc", claim, "--ignore-not-found", "-o", "name").strip():
        assert time.monotonic() < deadline, f"claim {claim} still exists 60s after the run"
        time.sleep(2)

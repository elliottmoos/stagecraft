"""The pipeline source on a real controller (DESIGN.md section 16). The fixture fetches
octocat/Hello-World from GitHub, so these tests need network access from the kind cluster."""

import pytest

from fixtures.pipelines import source
from support import nodes_by_name

pytestmark = pytest.mark.integration

MASTER = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"  # Hello-World's master, unchanged since 2012
OLDER = "762941318ee16e59dabbacb1b4049eec22f0d303"  # an earlier commit, not a branch tip


def resolved_sha(workflow: dict) -> str | None:
    outputs = workflow["status"].get("outputs", {}).get("parameters", [])
    return next((p.get("value") for p in outputs if p["name"] == "stagecraft-source-sha"), None)


def reporter_args(workflow: dict) -> list[str]:
    """The status reporter's rendered commands, one per report that ran."""
    return [
        n["inputs"]["parameters"]
        for n in workflow["status"]["nodes"].values()
        if n.get("templateName") == "status-reporter" and n["phase"] == "Succeeded"
    ]


@pytest.mark.parametrize(("revision", "sha"), [("master", MASTER), (OLDER, OLDER)])
def test_a_revision_resolves_to_one_commit_that_every_step_builds(argo, revision, sha):
    # The readme step fails unless it runs in the checkout and HEAD is the resolved commit.
    workflow = argo.run(source.pipeline, {"revision": revision})

    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes_by_name(workflow)["readme"]["phase"] == "Succeeded"
    assert resolved_sha(workflow) == sha
    assert len(reporter_args(workflow)) == 4  # pending and success, for two contexts


def test_a_missing_revision_fails_before_any_stage_and_reports_nothing(argo):
    workflow = argo.run(source.pipeline, {"revision": "no-such-branch"})

    nodes = nodes_by_name(workflow)
    assert workflow["status"]["phase"] == "Failed"
    assert nodes["source"]["phase"] == "Failed"
    assert nodes["verify"]["phase"] == "Omitted"
    assert resolved_sha(workflow) is None
    # No commit to report on: the pipeline's final report is skipped, not attempted.
    assert reporter_args(workflow) == []
    assert nodes["status-failure"]["phase"] == "Skipped"

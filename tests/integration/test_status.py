"""Commit statuses on a real controller, read back from the fake reporter's workflow nodes."""

import itertools

import pytest

from fixtures.actions import Shell
from fixtures.pipelines import status
from support import unique_name

pytestmark = pytest.mark.integration

COMMIT = {"repo": "example/repo", "sha": "abc123"}


def reports(workflow: dict) -> dict[str, list[tuple[str, str]]]:
    """(state, description) posted per context, in order. Asserts each report finished before
    the next one for the same context started, so the last one is what GitHub shows."""
    calls = [
        n
        for n in workflow["status"]["nodes"].values()
        if n.get("templateName") == "status-reporter" and n["phase"] in ("Succeeded", "Failed")
    ]
    by_context: dict[str, list[dict]] = {}
    for call in sorted(calls, key=lambda n: n["startedAt"]):
        args = {p["name"]: p["value"] for p in call["inputs"]["parameters"]}
        by_context.setdefault(args["context"], []).append({**args, "node": call})
    for context, posts in by_context.items():
        for earlier, later in itertools.pairwise(posts):
            assert earlier["node"]["finishedAt"] <= later["node"]["startedAt"], context
    return {c: [(p["state"], p["description"]) for p in posts] for c, posts in by_context.items()}


PENDING = ("pending", "Running")
SUCCESS = ("success", "Succeeded")
FAILURE = ("failure", "Failed")


def test_failure_reports_failure_and_never_reports_stages_that_did_not_run(argo):
    workflow = argo.run(status.pipeline, {**COMMIT, "fail": "true"})

    assert workflow["status"]["phase"] == "Failed"
    assert reports(workflow) == {
        "ci/pipeline": [PENDING, FAILURE],
        "ci/verify": [PENDING, FAILURE],
        "ci/unit-tests": [PENDING, FAILURE],
    }


def test_success_with_skipped_stage_reports_it_as_skipped(argo):
    workflow = argo.run(status.pipeline, COMMIT)

    assert workflow["status"]["phase"] == "Succeeded"
    assert reports(workflow) == {
        "ci/pipeline": [PENDING, SUCCESS],
        "ci/verify": [PENDING, SUCCESS],
        "ci/unit-tests": [PENDING, SUCCESS],
        "ci/deploy": [("success", "Skipped")],
    }


def test_success_with_stage_run_by_when(argo):
    workflow = argo.run(status.pipeline, {**COMMIT, "deploy": "true"})

    assert workflow["status"]["phase"] == "Succeeded"
    assert reports(workflow)["ci/deploy"] == [PENDING, SUCCESS]


def test_failing_reporter_never_fails_the_pipeline(argo):
    broken = status.pipeline.model_copy(
        update={
            "name": unique_name("broken-reporter"),
            "status_reporter": Shell(script="exit 1"),
        }
    )

    workflow = argo.run(broken, COMMIT)

    assert workflow["status"]["phase"] == "Succeeded"
    nodes = workflow["status"]["nodes"].values()
    calls = [n for n in nodes if n.get("templateName") == "status-reporter"]
    assert calls and all(n["phase"] in ("Failed", "Skipped") for n in calls)


def test_stopped_pipeline_reports_failure_except_for_the_running_step(argo):
    argo.install([status.pipeline])
    workflow = argo.submit(status.pipeline.name, {**COMMIT, "sleep": "120"})
    argo.wait_for_running_pod(workflow, "unit-tests")

    argo.stop(workflow)
    finished = argo.wait(workflow)

    assert finished["status"]["phase"] == "Failed"
    assert reports(finished)["ci/pipeline"][-1] == FAILURE
    assert reports(finished)["ci/verify"][-1] == FAILURE
    # Known gap, pinned so an Argo upgrade that changes it is noticed: `argo stop` does not run
    # the exit hook of a step that was running, so its context stays pending until a re-run.
    assert reports(finished)["ci/unit-tests"] == [PENDING]

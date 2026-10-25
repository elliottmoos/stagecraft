"""Pipeline-level on_success / on_failure on a real controller. The handlers in the fixture
pipeline exit non-zero unless they see the expected workflow status, so a Succeeded handler node
proves the status reached it."""

import pytest

from stagecraft.spec import Pipeline, Stage, Step

from fixtures.actions import Shell
from fixtures.pipelines import handlers
from support import nodes_by_name, unique_name

pytestmark = pytest.mark.integration


def handler_phases(workflow: dict) -> dict[str, str]:
    nodes = nodes_by_name(workflow)
    return {name: nodes[name]["phase"] for name in ("on-success-notify", "on-failure-notify")}


def test_success_runs_on_success_only(argo):
    workflow = argo.run(handlers.pipeline)

    assert workflow["status"]["phase"] == "Succeeded"
    assert handler_phases(workflow) == {
        "on-success-notify": "Succeeded",
        "on-failure-notify": "Skipped",
    }


def test_failure_runs_on_failure_only(argo):
    workflow = argo.run(handlers.pipeline, {"fail": "true"})

    assert workflow["status"]["phase"] == "Failed"
    assert handler_phases(workflow) == {
        "on-success-notify": "Skipped",
        "on-failure-notify": "Succeeded",
    }


def test_stopped_workflow_runs_on_failure(argo):
    argo.install([handlers.pipeline])
    workflow = argo.submit(handlers.pipeline.name, {"sleep": "120"})
    argo.wait_for_running_pod(workflow)

    argo.stop(workflow)
    finished = argo.wait(workflow)

    assert finished["status"]["phase"] == "Failed"
    assert handler_phases(finished)["on-failure-notify"] == "Succeeded"


def test_terminated_workflow_runs_no_handlers(argo):
    # Argo behavior, pinned here because slice 5 (commit statuses) must account for it:
    # `argo terminate` skips the exit handler entirely.
    argo.install([handlers.pipeline])
    workflow = argo.submit(handlers.pipeline.name, {"sleep": "120"})
    argo.wait_for_running_pod(workflow)

    argo.terminate(workflow)
    finished = argo.wait(workflow)

    assert finished["status"]["phase"] == "Failed"
    assert not {"on-success-notify", "on-failure-notify"} & nodes_by_name(finished).keys()


def test_failing_handler_fails_the_pipeline(argo):
    pipeline = Pipeline(
        name=unique_name("failing-handler"),
        stages=[Stage("run", [Step("work", Shell(script="true"))])],
        on_success=[Step("notify", Shell(script="exit 1"))],
    )

    workflow = argo.run(pipeline)

    assert workflow["status"]["phase"] == "Failed"
    assert nodes_by_name(workflow)["work"]["phase"] == "Succeeded"
    assert nodes_by_name(workflow)["on-success-notify"]["phase"] == "Failed"

"""Step and stage handlers on a real controller. Handlers in the fixture pipeline fail unless they
see the expected step name and status, so every hook below must have Succeeded."""

import pytest

from stagecraft.spec import Pipeline, Stage, Step

from fixtures.actions import Shell
from fixtures.pipelines import hooks
from support import nodes_by_name, unique_name

pytestmark = pytest.mark.integration


def fired_hooks(workflow: dict) -> dict[str, str]:
    """Phase of every handler that ran, keyed '<step>/<handler task>'.

    Handlers are the non-skipped children of a step's '<step>.onExit' hook node.
    """
    nodes = workflow["status"]["nodes"]
    fired = {}
    for node in nodes.values():
        if node["displayName"].endswith(".onExit"):
            step = node["displayName"].removesuffix(".onExit")
            for child in (nodes[i] for i in node.get("children", [])):
                if child["phase"] != "Skipped":
                    fired[f"{step}/{child['displayName']}"] = child["phase"]
    return fired


def test_failure_runs_failure_and_finally_handlers_for_step_and_stage(argo):
    workflow = argo.run(hooks.pipeline, {"fail": "true"})

    assert workflow["status"]["phase"] == "Failed"
    assert fired_hooks(workflow) == {
        "lint/on-success-notify": "Succeeded",
        "unit-tests/on-failure-notify": "Succeeded",
        "unit-tests/finally-upload-reports-failed": "Succeeded",
        "verify/on-failure-notify": "Succeeded",
        "verify/finally-cleanup-failed": "Succeeded",
    }


def test_success_runs_success_and_finally_handlers_and_when_true_runs_the_stage(argo):
    workflow = argo.run(hooks.pipeline, {"deploy": "true"})

    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes_by_name(workflow)["staging"]["phase"] == "Succeeded"
    assert fired_hooks(workflow) == {
        "lint/on-success-notify": "Succeeded",
        "unit-tests/finally-upload-reports-succeeded": "Succeeded",
        "verify/finally-cleanup-succeeded": "Succeeded",
        "deploy/finally-report-succeeded": "Succeeded",
    }


def test_stage_skipped_by_when_runs_none_of_its_handlers(argo):
    workflow = argo.run(hooks.pipeline)

    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes_by_name(workflow)["deploy"]["phase"] == "Skipped"
    assert not [name for name in fired_hooks(workflow) if name.startswith("deploy/")]


def test_failing_step_handler_does_not_fail_the_pipeline(argo):
    # Argo limitation, accepted by design (unlike pipeline-level handlers, which do fail it).
    pipeline = Pipeline(
        name=unique_name("failing-hook"),
        stages=[
            Stage(
                "run",
                [
                    Step(
                        "work", Shell(script="true"), on_success=[Step("n", Shell(script="exit 1"))]
                    )
                ],
            )
        ],
    )

    workflow = argo.run(pipeline)

    assert workflow["status"]["phase"] == "Succeeded"
    assert fired_hooks(workflow) == {"work/on-success-n": "Failed"}

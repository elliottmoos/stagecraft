"""Cron triggers on kind: the schedule fires and runs the pipeline with the trigger's params."""

import time

import pytest

from stagecraft.spec import Param, Pipeline, Stage, Step, Trigger

from fixtures.actions import Shell
from support import unique_name

pytestmark = pytest.mark.integration


def test_a_cron_trigger_runs_the_pipeline_with_its_params(argo):
    # The step fails unless the scheduled run got the trigger's param, not the default.
    name = unique_name("cron")
    pipeline = Pipeline(
        name=name,
        params=[Param("mode", default="manual")],
        stages=[
            Stage("check", [Step("mode", Shell(script='test "{{ param.mode }}" = scheduled'))])
        ],
        triggers=[Trigger.cron("* * * * *", params={"mode": "scheduled"})],
    )
    # Install the template before the trigger. Installed together, Helm applies the CronWorkflow
    # first; Argo marks it invalid until it re-checks it, minutes later (see CLAUDE.md).
    argo.install([pipeline.model_copy(update={"triggers": []})])
    argo.install([pipeline])
    cron = f"{name}-cron"
    try:
        workflow = argo.wait(first_run(argo, cron, timeout=90))
    finally:
        argo.kubectl("delete", "cronworkflow", cron, "--ignore-not-found")

    assert workflow["status"]["phase"] == "Succeeded"
    assert workflow["spec"]["workflowTemplateRef"] == {"name": name}


def first_run(argo, cron: str, timeout: float) -> str:
    """The name of the first workflow the CronWorkflow `cron` starts."""
    selector = f"workflows.argoproj.io/cron-workflow={cron}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jsonpath = "jsonpath={.items[*].metadata.name}"
        if names := argo.kubectl("get", "workflows", "-l", selector, "-o", jsonpath).split():
            return names[0]
        time.sleep(3)
    raise AssertionError(f"{cron} started no workflow within {timeout}s")

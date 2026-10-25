"""The setup step on a real controller (DESIGN.md section 16). setup-ci calls an existing
WorkflowTemplate and fetches octocat/Hello-World from GitHub, so it needs network access."""

import pytest

from fixtures.pipelines import setup
from support import FIXTURE_TEMPLATES, nodes_by_name

pytestmark = pytest.mark.integration


def test_a_template_ref_setup_feeds_the_source(argo):
    # The existing WorkflowTemplate is installed separately, as it would be in a real cluster.
    argo.kubectl("apply", "-f", str(FIXTURE_TEMPLATES / "parse-webhook.yaml"))

    # head fails unless the checkout is at the commit the payload names.
    workflow = argo.run(setup.pipeline)

    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes_by_name(workflow)["head"]["phase"] == "Succeeded"


def test_action_setup_outputs_reach_steps_conditions_and_handlers(argo):
    workflow = argo.run(setup.action_pipeline)

    nodes = nodes_by_name(workflow)
    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes["deploy"]["phase"] == "Succeeded"
    assert nodes["release"]["phase"] == "Skipped"
    assert nodes["finally-shell-succeeded"]["phase"] == "Succeeded"

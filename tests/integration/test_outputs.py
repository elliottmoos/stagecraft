"""Results between steps on a real controller (DESIGN.md section 16). The fixture fetches
octocat/Hello-World from GitHub, so it needs network access."""

import pytest

from fixtures.pipelines import outputs
from support import nodes_by_name

pytestmark = pytest.mark.integration


def test_artifacts_and_parameters_reach_later_stages_conditions_and_handlers(argo):
    # package fails unless the binary landed in its checkout and the digest arrived; deploy
    # runs only if its condition on the digest holds; the pipeline handler checks it too.
    workflow = argo.run(outputs.pipeline)

    nodes = nodes_by_name(workflow)
    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes["image"]["phase"] == "Succeeded"
    assert nodes["deploy"]["phase"] == "Succeeded"
    assert nodes["on-success-shell"]["phase"] == "Succeeded"

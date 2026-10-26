"""Stage sources on a real controller (DESIGN.md section 16). The fixture fetches two public
octocat repositories from GitHub, so it needs network access."""

import pytest

from fixtures.pipelines import stage_sources
from support import nodes_by_name

pytestmark = pytest.mark.integration


def test_a_stage_source_reaches_only_its_stages_steps(argo):
    workflow = argo.run(stage_sources.pipeline)

    nodes = nodes_by_name(workflow)
    assert workflow["status"]["phase"] == "Succeeded"
    assert nodes["source-spoon"]["phase"] == "Succeeded"
    assert nodes["after"]["phase"] == "Succeeded"

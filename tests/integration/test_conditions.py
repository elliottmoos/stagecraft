"""Typed conditions on a real controller (DESIGN.md section 16)."""

import pytest

from fixtures.pipelines import conditions
from support import nodes_by_name

pytestmark = pytest.mark.integration


def test_combined_conditions_and_values_evaluate_as_written(argo):
    workflow = argo.run(conditions.pipeline)

    nodes = nodes_by_name(workflow)
    assert workflow["status"]["phase"] == "Succeeded"
    assert {name: nodes[name]["phase"] for name in ("deploy", "either", "by-value")} == {
        "deploy": "Succeeded",
        "either": "Succeeded",
        "by-value": "Succeeded",
    }
    assert {name: nodes[name]["phase"] for name in ("prod-only", "not-value")} == {
        "prod-only": "Skipped",
        "not-value": "Skipped",
    }

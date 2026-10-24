"""Helpers for tests that shell out to helm, argo, kubeconform, and kubectl."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

from stagecraft.compile import compile_pipeline, compile_triggers
from stagecraft.context import VALUE_MARKER
from stagecraft.serialize import serialize
from stagecraft.spec import Pipeline

from fixtures.contract import CONTEXT

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_CHART = FIXTURES / "chart"
CRD_SCHEMAS = FIXTURES / "crd-schemas"


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} is not installed")
    return path


def run(*args: str, timeout: float = 120, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(
            f"{' '.join(args)} exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def build_chart(tmp_path: Path, pipelines: list[Pipeline]) -> Path:
    """Copy the fixture chart and write the rendered pipelines into its templates/."""
    chart = tmp_path / "chart"
    shutil.rmtree(chart, ignore_errors=True)  # a test may install several times
    shutil.copytree(FIXTURE_CHART, chart)
    for pipeline in pipelines:
        (chart / "templates" / f"{pipeline.name}.yaml").write_text(serialize(*manifests(pipeline)))
    return chart


def manifests(pipeline: Pipeline) -> list:
    """The pipeline's compiled WorkflowTemplate, then its CronWorkflows."""
    return [compile_pipeline(pipeline, CONTEXT), *compile_triggers(pipeline, CONTEXT)]


def helm_template(chart: Path, out: Path) -> list[dict]:
    """Run `helm template`, write the result to `out`, and return the parsed documents."""
    rendered = run(require_tool("helm"), "template", "fixture", str(chart))
    out.write_text(rendered)
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def with_fixture_values(node):
    """`node` with value markers replaced by the fixture chart's values, as Helm renders them
    (`quote` stringifies: booleans as true/false)."""
    values = yaml.safe_load((FIXTURE_CHART / "values.yaml").read_text())

    def lookup(match) -> str:
        value = values
        for key in match[1].split("."):
            value = value[key]
        return str(value).lower() if isinstance(value, bool) else str(value)

    def walk(n):
        if isinstance(n, dict):
            return {k: walk(v) for k, v in n.items()}
        if isinstance(n, list):
            return [walk(v) for v in n]
        return VALUE_MARKER.sub(lookup, n) if isinstance(n, str) else n

    return walk(node)


def nodes_by_name(workflow: dict) -> dict[str, dict]:
    """Workflow nodes keyed by display name (stage or block name)."""
    return {n["displayName"]: n for n in workflow["status"]["nodes"].values()}


def unique_name(prefix: str) -> str:
    """A resource name that won't clash with earlier runs on the same cluster."""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"

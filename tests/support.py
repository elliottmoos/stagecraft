"""Helpers for tests that shell out to helm, argo, kubeconform, and kubectl."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

from stagecraft.compile import compile_pipeline
from stagecraft.serialize import serialize
from stagecraft.spec import Pipeline

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
    shutil.copytree(FIXTURE_CHART, chart)
    for pipeline in pipelines:
        (chart / "templates" / f"{pipeline.name}.yaml").write_text(
            serialize(compile_pipeline(pipeline))
        )
    return chart


def helm_template(chart: Path, out: Path) -> list[dict]:
    """Run `helm template`, write the result to `out`, and return the parsed documents."""
    rendered = run(require_tool("helm"), "template", "fixture", str(chart))
    out.write_text(rendered)
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def nodes_by_name(workflow: dict) -> dict[str, dict]:
    """Workflow nodes keyed by display name (stage or block name)."""
    return {n["displayName"]: n for n in workflow["status"]["nodes"].values()}


def unique_name(prefix: str) -> str:
    """A resource name that won't clash with earlier runs on the same cluster."""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"

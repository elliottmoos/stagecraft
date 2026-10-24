"""Behavior tests on kind. Every command names the kind context explicitly, so these tests never
touch whatever cluster the current kubeconfig context points at."""

import json
import subprocess
import time
from pathlib import Path

import pytest

from stagecraft.spec import Block, Param, Pipeline, Stage

from support import build_chart, require_tool, run, unique_name

CONTEXT = "kind-stagecraft"
NAMESPACE = "argo"
RELEASE = "stagecraft-fixture"


class Argo:
    """Install pipelines through the fixture chart and drive workflows with the argo CLI."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.helm = require_tool("helm")
        self.argo = [require_tool("argo"), "--context", CONTEXT, "-n", NAMESPACE]

    def install(self, pipelines: list[Pipeline]) -> None:
        chart = build_chart(self.tmp_path, pipelines)
        run(self.helm, "upgrade", "--install", RELEASE, str(chart),
            "--kube-context", CONTEXT, "-n", NAMESPACE)  # fmt: skip

    def submit(self, template: str, params: dict[str, str] | None = None) -> str:
        """Submit a workflow from an installed template and return its name."""
        param_args = [arg for k, v in (params or {}).items() for arg in ("-p", f"{k}={v}")]
        return run(*self.argo, "submit", "--from", f"workflowtemplate/{template}",
                   *param_args, "-o", "name").strip()  # fmt: skip

    def get(self, workflow: str) -> dict:
        return json.loads(run(*self.argo, "get", workflow, "-o", "json"))

    def wait(self, workflow: str) -> dict:
        """Wait for the workflow to finish and return it."""
        # `argo wait` exits non-zero when the workflow fails; callers assert on the phase.
        run(*self.argo, "wait", workflow, timeout=300, check=False)
        return self.get(workflow)

    def wait_for_running_pod(self, workflow: str, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            nodes = self.get(workflow).get("status", {}).get("nodes", {}).values()
            if any(n["type"] == "Pod" and n["phase"] == "Running" for n in nodes):
                return
            time.sleep(1)
        raise AssertionError(f"{workflow} has no running pod after {timeout}s")

    def stop(self, workflow: str) -> None:
        run(*self.argo, "stop", workflow)

    def terminate(self, workflow: str) -> None:
        run(*self.argo, "terminate", workflow)

    def run(self, pipeline: Pipeline, params: dict[str, str] | None = None) -> dict:
        """Install one pipeline, run it to completion, and return the finished Workflow."""
        self.install([pipeline])
        return self.wait(self.submit(pipeline.name, params))


@pytest.fixture(scope="session")
def kind_cluster():
    kubectl = require_tool("kubectl")
    probe = subprocess.run(
        [kubectl, "--context", CONTEXT, "get", "deployment/workflow-controller", "-n", NAMESPACE],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip(f"no Argo in kube context {CONTEXT}; run scripts/kind-up.sh")


@pytest.fixture
def argo(kind_cluster, tmp_path) -> Argo:
    return Argo(tmp_path)


@pytest.fixture
def run_block(argo):
    """Run one block as a one-step pipeline and return the finished Workflow."""

    def run_one(block: Block, params: dict[str, str] | None = None):
        pipeline = Pipeline(
            name=unique_name(f"run-{block.block_name}"),
            params=[Param(k, required=True) for k in params or {}],
            stages=[Stage("run", [block])],
        )
        return argo.run(pipeline, params)

    return run_one

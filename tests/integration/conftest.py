"""Behavior tests on kind. Every command names the kind context explicitly, so these tests never
touch whatever cluster the current kubeconfig context points at."""

import json
import subprocess
import uuid

import pytest

from stagecraft.spec import Block, Param, Pipeline, Stage

from support import build_chart, require_tool, run

CONTEXT = "kind-stagecraft"
NAMESPACE = "argo"
RELEASE = "stagecraft-fixture"


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
    return CONTEXT


@pytest.fixture
def install_and_submit(kind_cluster, tmp_path):
    """Install pipelines through the fixture chart, then submit one and wait for it to finish.

    Returns the finished Workflow as a dict.
    """
    helm, argo = require_tool("helm"), require_tool("argo")

    def submit(pipelines: list[Pipeline], name: str, params: dict[str, str] | None = None):
        chart = build_chart(tmp_path, pipelines)
        run(helm, "upgrade", "--install", RELEASE, str(chart),
            "--kube-context", kind_cluster, "-n", NAMESPACE)  # fmt: skip
        argo_args = ["--context", kind_cluster, "-n", NAMESPACE]
        param_args = [arg for k, v in (params or {}).items() for arg in ("-p", f"{k}={v}")]
        workflow = run(argo, "submit", *argo_args, "--from", f"workflowtemplate/{name}",
                       *param_args, "-o", "name").strip()  # fmt: skip
        # `argo wait` exits non-zero when the workflow fails; the caller asserts on the phase.
        run(argo, "wait", *argo_args, workflow, timeout=300, check=False)
        return json.loads(run(argo, "get", *argo_args, workflow, "-o", "json"))

    return submit


@pytest.fixture
def run_block(install_and_submit):
    """Run one block as a one-step pipeline and return the finished Workflow."""

    def run_one(block: Block, params: dict[str, str] | None = None):
        name = f"run-{block.block_name}-{uuid.uuid4().hex[:6]}"
        pipeline = Pipeline(
            name=name,
            params=[Param(k, required=True) for k in params or {}],
            stages=[Stage("run", [block])],
        )
        return install_and_submit([pipeline], name, params)

    return run_one

"""Workspace declarations and the containerset strategy (DESIGN.md section 7)."""

import pytest
from hera.workflows import Script
from hera.workflows.models import RetryStrategy
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Stage, Workspace

from fixtures.blocks import Shell
from fixtures.contract import CONTEXT
from fixtures.pipelines import smoke, workspace

WS = Workspace(strategy="containerset", size="1Gi")


def manifest(pipeline):
    return compile_pipeline(pipeline, CONTEXT).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def reader(name, **fields):
    return Shell(name=name, script="true", workspace="read", **fields)


def pipeline_with(*stages, **fields):
    return Pipeline(name="p", workspace=WS, stages=list(stages), **fields)


# Declarations


def test_size_must_be_a_kubernetes_quantity():
    with pytest.raises(ValidationError, match="size '5 GB' must be a Kubernetes quantity"):
        Workspace(strategy="containerset", size="5 GB")


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValidationError, match="containerset"):
        Workspace(strategy="artifact", size="1Gi")


def test_blocks_using_the_workspace_need_a_pipeline_workspace():
    with pytest.raises(ValidationError, match=r"stage 's', block 'lint' uses the workspace"):
        Pipeline(name="p", stages=[Stage("s", [reader("lint")])])


def test_workspace_without_blocks_using_it_is_rejected():
    with pytest.raises(ValidationError, match="workspace is set, but no block uses it"):
        pipeline_with(Stage("s", [Shell(script="true")]))


@pytest.mark.parametrize("other", ["read", "write"])
def test_a_writer_is_its_stages_only_workspace_block(other):
    blocks = [
        Shell(name="a", script="true", workspace="write"),
        Shell(name="b", script="true", workspace=other),
    ]

    with pytest.raises(ValidationError, match=r"stage 's': block 'a' writes the workspace, so"):
        Stage("s", blocks)


def test_a_writer_can_run_beside_blocks_without_workspace_access():
    Stage("s", [Shell(name="a", script="true", workspace="write"), Shell(name="b", script="")])


def test_handlers_cannot_use_the_workspace():
    with pytest.raises(ValidationError, match="handler blocks cannot use the workspace"):
        Stage("s", [Shell(script="true")], finally_=[reader("upload")])


def test_status_reporter_cannot_use_the_workspace():
    with pytest.raises(ValidationError, match="the status_reporter cannot use the workspace"):
        pipeline_with(
            Stage("s", [reader("lint")]),
            params=[Param("repo", required=True), Param("sha", required=True)],
            status="ci/p",
            status_reporter=reader("report"),
        )


# Compilation


def test_no_workspace_means_no_volume():
    spec = manifest(smoke.pipeline)["spec"]

    assert "volumeClaimTemplates" not in spec
    assert "volumeMounts" not in template(manifest(smoke.pipeline), "verify-lint")["container"]


def test_workspace_is_one_rwo_claim_per_run_deleted_when_the_run_ends():
    spec = manifest(workspace.pipeline)["spec"]

    assert spec["volumeClaimTemplates"] == [
        {
            "metadata": {"name": "workspace"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
        }
    ]
    assert spec["volumeClaimGC"] == {"strategy": "OnWorkflowCompletion"}


def test_a_lone_workspace_block_runs_in_its_own_pod_with_the_volume_mounted():
    container = template(manifest(workspace.pipeline), "prepare-clone")["container"]

    assert container["volumeMounts"] == [{"name": "workspace", "mountPath": "/workspace"}]
    assert container["workingDir"] == "/workspace"


def test_readers_mount_read_only():
    container = template(manifest(workspace.pipeline), "package-build")["container"]

    assert container["volumeMounts"] == [
        {"name": "workspace", "mountPath": "/workspace", "readOnly": True}
    ]


def test_parallel_workspace_blocks_share_one_container_set_pod():
    m = manifest(workspace.pipeline)

    assert [(t["name"], t["template"]) for t in template(m, "verify")["dag"]["tasks"]] == [
        ("workspace", "verify-workspace"),
        ("notify", "verify-notify"),
    ]
    container_set = template(m, "verify-workspace")["containerSet"]
    assert [c["name"] for c in container_set["containers"]] == ["lint", "unit-tests"]
    assert all(c["workingDir"] == "/workspace" for c in container_set["containers"])
    # Argo only adds the claim's volume to the pod for containerSet-level mounts.
    assert all("volumeMounts" not in c for c in container_set["containers"])
    assert container_set["volumeMounts"] == [
        {"name": "workspace", "mountPath": "/workspace", "readOnly": True}
    ]
    assert "volumeMounts" not in template(m, "verify-notify")["container"]


def test_grouped_blocks_keep_no_template_of_their_own():
    names = {t["name"] for t in manifest(workspace.pipeline)["spec"]["templates"]}

    assert "verify-lint" not in names
    assert "verify-unit-tests" not in names


def test_block_working_dir_is_kept():
    class InSubdir(Shell):
        def compile(self, ctx: CompileContext):
            container = super().compile(ctx)
            container.working_dir = "/workspace/sub"
            return container

    m = manifest(pipeline_with(Stage("s", [InSubdir(script="true", workspace="read")])))

    assert template(m, "s-in-subdir")["container"]["workingDir"] == "/workspace/sub"


@pytest.mark.parametrize(
    "fields",
    [
        {"when": "true"},
        {"status": "ci/lint"},
        {"on_failure": [Shell(name="alert", script="true")]},
    ],
    ids=["when", "status", "handlers"],
)
def test_grouped_blocks_cannot_set_step_features(fields):
    pipeline = pipeline_with(
        Stage("s", [reader("lint", **fields), reader("tests")]),
        params=[Param("repo", required=True), Param("sha", required=True)],
        status_reporter=Shell(name="report", script="true"),
    )

    with pytest.raises(
        CompileError,
        match=r"stage 's', block 'lint': blocks sharing a containerset pod cannot set when, "
        r"status, or handlers",
    ):
        compile_pipeline(pipeline, CONTEXT)


def test_grouped_blocks_must_compile_to_a_container():
    class Py(Shell):
        def compile(self, ctx: CompileContext):
            return Script(image="python", source="print(1)")

    pipeline = pipeline_with(Stage("s", [Py(script="", workspace="read"), reader("tests")]))

    with pytest.raises(CompileError, match=r"block 'py': .* must compile to a Container"):
        compile_pipeline(pipeline, CONTEXT)


def test_grouped_blocks_cannot_set_template_level_fields():
    class Retried(Shell):
        def compile(self, ctx: CompileContext):
            container = super().compile(ctx)
            container.retry_strategy = RetryStrategy(limit="2")
            return container

    pipeline = pipeline_with(Stage("s", [Retried(script="true", workspace="read"), reader("b")]))

    with pytest.raises(CompileError, match=r"block 'retried': .* cannot set retry_strategy"):
        compile_pipeline(pipeline, CONTEXT)


def test_group_task_name_cannot_collide_with_a_block():
    pipeline = pipeline_with(
        Stage("s", [reader("a"), reader("b"), Shell(name="workspace", script="true")])
    )

    with pytest.raises(CompileError, match="block name 'workspace' is reserved"):
        compile_pipeline(pipeline, CONTEXT)

"""Workspace strategies: how blocks share the pipeline's workspace. See DESIGN.md section 7.

The workspace is one ReadWriteOnce PVC per run, so pods on different nodes cannot mount it at the
same time. With the "containerset" strategy, a stage's workspace blocks run as containers of one
pod; stages run one after another, so the volume is only ever mounted by one pod at a time. A
strategy is a pipeline choice and reads no Helm values: platform details (the StorageClass) come
from cluster defaults.
"""

import dataclasses

from hera.workflows import Container, ContainerNode, ContainerSet
from hera.workflows.models import (
    ObjectMeta,
    PersistentVolumeClaim,
    PersistentVolumeClaimSpec,
    Quantity,
    VolumeClaimGC,
    VolumeMount,
    VolumeResourceRequirements,
)

from stagecraft.errors import CompileError
from stagecraft.spec import WORKSPACE_PATH, Block, Pipeline, Stage, Workspace

VOLUME = "workspace"
# The task name of a stage's containerSet; its template is "<stage>-workspace".
GROUP = "workspace"

# Delete the claim even when the run fails, so failed builds don't leak volumes.
CLAIM_GC = VolumeClaimGC(strategy="OnWorkflowCompletion")

# Container fields a containerSet container can take. Anything else a block's Container sets
# (retries, timeouts, inputs, pod-level settings) belongs to a whole template.
_NODE_FIELDS = {f.name for f in dataclasses.fields(ContainerNode)} - {"volumes", "dependencies"}


def claim(workspace: Workspace) -> PersistentVolumeClaim:
    # No storageClassName: the cluster's default StorageClass applies.
    return PersistentVolumeClaim(
        metadata=ObjectMeta(name=VOLUME),
        spec=PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=VolumeResourceRequirements(requests={"storage": Quantity(workspace.size)}),
        ),
    )


def grouped(pipeline: Pipeline, stage: Stage) -> list[Block]:
    """The stage's blocks that share one containerSet pod: its workspace blocks, if there are
    two or more. They are all readers: a writer must be its stage's only workspace block."""
    if pipeline.workspace is None:
        return []
    users = [b for b in stage.blocks if b.workspace != "none"]
    return users if len(users) > 1 else []


def mount[T](template: T, access: str) -> T:
    """`template` (a Container or Script) with the workspace mounted at WORKSPACE_PATH,
    read-only for "read", and as the working directory unless it sets one."""
    return dataclasses.replace(
        template,
        volume_mounts=[*(template.volume_mounts or []), _volume_mount(access)],
        working_dir=template.working_dir or WORKSPACE_PATH,
    )


def container_set(name: str, nodes: list[ContainerNode]) -> ContainerSet:
    """A containerSet running `nodes` in one pod with the workspace mounted read-only.

    The mount is on the containerSet, which applies it to every container: Argo 4.1.6 adds a
    claim's volume to the pod only for containerSet-level mounts, so a per-container mount fails
    with "volumeMounts[0].name: Not found".
    """
    return ContainerSet(name=name, containers=nodes, volume_mounts=[_volume_mount("read")])


def _volume_mount(access: str) -> VolumeMount:
    read_only = True if access == "read" else None
    return VolumeMount(name=VOLUME, mount_path=WORKSPACE_PATH, read_only=read_only)


def container_node(template, name: str) -> ContainerNode:
    """A block's compiled template as a containerSet container named `name`, working in the
    workspace unless it sets its own working directory."""
    if not isinstance(template, Container):
        raise CompileError(
            f"blocks sharing a containerset pod must compile to a Container, "
            f"not a {type(template).__name__}"
        )
    fields = {
        f.name: value
        for f in dataclasses.fields(template)
        if f.name != "name" and (value := getattr(template, f.name)) is not None
    }
    if unsupported := sorted(fields.keys() - _NODE_FIELDS):
        raise CompileError(
            f"blocks sharing a containerset pod cannot set {', '.join(unsupported)} "
            "(template-level fields); move the block to its own stage"
        )
    return ContainerNode(name=name, **{"working_dir": WORKSPACE_PATH, **fields})

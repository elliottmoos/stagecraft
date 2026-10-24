"""A shared workspace with the containerset strategy: one writer, then a stage of readers that
share one pod. Readers fail unless they see the clone's file and cannot write."""

from stagecraft.spec import Param, Pipeline, Stage, Workspace

from fixtures.blocks import Shell

READ_ONLY = "! touch probe 2>/dev/null"
FAIL = 'test "{{ param.fail }}" != true'

pipeline = Pipeline(
    name="workspace-ci",
    workspace=Workspace(strategy="containerset", size="1Gi"),
    params=[Param("fail", default="false", enum=["true", "false"])],
    stages=[
        Stage("prepare", [Shell(name="clone", script="echo source > src.txt", workspace="write")]),
        Stage(
            "verify",
            [
                Shell(
                    name="lint",
                    script=f"grep -q source src.txt && {READ_ONLY} && sleep 3",
                    workspace="read",
                ),
                Shell(
                    name="unit-tests",
                    script=f"grep -q source src.txt && {READ_ONLY} && {FAIL}",
                    workspace="read",
                ),
                Shell(name="notify", script="test ! -e /workspace"),
            ],
            on_failure=[Shell(name="alert", script="echo verify failed")],
        ),
        Stage("package", [Shell(name="build", script="cat src.txt", workspace="read")]),
    ],
)

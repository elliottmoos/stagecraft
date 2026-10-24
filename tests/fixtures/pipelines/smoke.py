from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell

pipeline = Pipeline(
    name="smoke-ci",
    params=[
        Param("repo", required=True, description="Repository to build, as owner/name"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
    ],
    stages=[
        Stage("prepare", [Shell(name="clone", script="echo cloning {{ param.repo }}")]),
        Stage(
            "verify",
            [
                Shell(name="lint", script="echo lint; sleep 2"),
                Shell(name="unit-tests", script="echo unit tests; sleep 2"),
            ],
        ),
        Stage("package", [Shell(name="build", script='echo "build, push={{ param.push }}"')]),
    ],
)

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell

pipeline = Pipeline(
    name="smoke-ci",
    params=[
        Param("repo", required=True, description="Repository to build, as owner/name"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
    ],
    stages=[
        Stage("prepare", [Step("clone", Shell(script="echo cloning {{ param.repo }}"))]),
        Stage(
            "verify",
            [
                Step("lint", Shell(script="echo lint; sleep 2")),
                Step("unit-tests", Shell(script="echo unit tests; sleep 2")),
            ],
        ),
        Stage("package", [Step("build", Shell(script='echo "build, push={{ param.push }}"'))]),
    ],
)

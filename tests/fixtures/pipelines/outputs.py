"""Results between steps: build publishes an artifact (in the checkout) and a parameter; package
receives both; deploy runs only if its condition on the parameter holds. Every step fails unless
it sees what build published, so a Succeeded run proves the wiring."""

from stagecraft.spec import Pipeline, Source, Stage, Step

from fixtures.actions import Build, Package, Shell

CHECK_DIGEST = Shell(script='test "{{ steps.build.binary.digest }}" = sha256:abc')

pipeline = Pipeline(
    name="outputs-ci",
    source=Source(url="https://github.com/octocat/Hello-World.git", revision="master"),
    stages=[
        Stage("build", [Step("binary", Build())]),
        Stage(
            "package",
            [
                Step(
                    "image",
                    Package(digest="{{ steps.build.binary.digest }}"),
                    inputs={"bin": "build.binary.bin"},
                )
            ],
        ),
        Stage("deploy", [CHECK_DIGEST], when="{{ steps.build.binary.digest }} == 'sha256:abc'"),
    ],
    on_success=[CHECK_DIGEST],
)

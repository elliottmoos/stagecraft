"""Commit statuses on pipeline, stage, and step, with a fake reporter that only echoes. The
commit comes from the pipeline source, the same tiny public repository as the source fixture."""

from stagecraft.spec import Param, Pipeline, Source, Stage, Step

from fixtures.actions import Shell

REPORTER = Shell(
    script='echo "{{ status.context }}={{ status.state }} ({{ status.description }})'
    ' for {{ source.url }}@{{ source.sha }}"'
)

pipeline = Pipeline(
    name="status-ci",
    status="ci/pipeline",
    status_reporter=REPORTER,
    source=Source(url="https://github.com/octocat/Hello-World.git", revision="master"),
    params=[
        Param("fail", default="false", enum=["true", "false"]),
        Param("deploy", default="false", enum=["true", "false"]),
        Param("sleep", default="0"),
    ],
    stages=[
        Stage(
            "verify",
            [
                Step("lint", Shell(script="true")),
                Step(
                    "unit-tests",
                    Shell(script='sleep {{ param.sleep }}; test "{{ param.fail }}" != true'),
                    status="ci/unit-tests",
                ),
            ],
            status="ci/verify",
        ),
        Stage(
            "deploy",
            [Step("staging", Shell(script="true"))],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
)

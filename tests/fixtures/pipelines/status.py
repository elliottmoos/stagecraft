"""Commit statuses on pipeline, stage, and step, with a fake reporter that only echoes."""

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell

REPORTER = Shell(
    script='echo "{{ status.context }}={{ status.state }} ({{ status.description }})'
    ' for {{ param.repo }}@{{ param.sha }}"'
)

pipeline = Pipeline(
    name="status-ci",
    status="ci/pipeline",
    status_reporter=REPORTER,
    params=[
        Param("repo", required=True),
        Param("sha", required=True),
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

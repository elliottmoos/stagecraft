"""Commit statuses on pipeline, stage, and block, with a fake reporter that only echoes."""

from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell

REPORTER = Shell(
    name="report",
    script=(
        'echo "{{ status.context }}={{ status.state }} ({{ status.description }})'
        ' for {{ param.repo }}@{{ param.sha }}"'
    ),
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
                Shell(name="lint", script="true"),
                Shell(
                    name="unit-tests",
                    script='sleep {{ param.sleep }}; test "{{ param.fail }}" != true',
                    status="ci/unit-tests",
                ),
            ],
            status="ci/verify",
        ),
        Stage(
            "deploy",
            [Shell(name="staging", script="true")],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
)

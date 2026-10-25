"""Pipeline-level on_success / on_failure. Each handler fails unless it sees the outcome it expects,
so a passing handler proves it received the right context."""

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell


def notify(outcome: str) -> Step:
    """A handler that echoes its context and fails unless it sees `outcome`."""
    script = 'echo "{{ step.name }}: {{ step.outcome }}"; test "{{ step.outcome }}" = ' + outcome
    return Step("notify", Shell(script=script))


pipeline = Pipeline(
    name="handlers-ci",
    params=[
        Param("fail", default="false", enum=["true", "false"]),
        Param("sleep", default="0", description="Seconds the build step sleeps"),
    ],
    stages=[
        Stage(
            "build",
            [
                Step(
                    "work", Shell(script='sleep {{ param.sleep }}; test "{{ param.fail }}" != true')
                )
            ],
        )
    ],
    on_success=[notify("succeeded")],
    on_failure=[notify("failed")],
)

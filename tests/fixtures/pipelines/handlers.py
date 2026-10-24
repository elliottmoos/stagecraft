"""Pipeline-level on_success / on_failure. Each handler fails unless it sees the status it expects,
so a passing handler proves it received the right context."""

from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell


def notify(expect: str) -> Shell:
    """A handler that echoes its context and fails unless `test "<status>" <expect>` holds."""
    script = 'echo "{{ step.name }}: {{ step.status }}"; test "{{ step.status }}" ' + expect
    return Shell(name="notify", script=script)


pipeline = Pipeline(
    name="handlers-ci",
    params=[
        Param("fail", default="false", enum=["true", "false"]),
        Param("sleep", default="0", description="Seconds the build block sleeps"),
    ],
    stages=[
        Stage(
            "build",
            [Shell(name="work", script='sleep {{ param.sleep }}; test "{{ param.fail }}" != true')],
        )
    ],
    on_success=[notify("= Succeeded")],
    on_failure=[notify("!= Succeeded")],
)

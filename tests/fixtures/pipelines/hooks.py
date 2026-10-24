"""Block and stage handlers plus `when`. Handlers fail unless they see the expected step name and
status, so a Succeeded handler node proves its context arrived."""

from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell


def expect(handler: str, step_name: str, status: str) -> Shell:
    script = f'test "{{{{ step.name }}}}" = {step_name} && test "{{{{ step.status }}}}" = {status}'
    return Shell(name=handler, script=script)


pipeline = Pipeline(
    name="hooks-ci",
    params=[
        Param("fail", default="false", enum=["true", "false"]),
        Param("deploy", default="false", enum=["true", "false"]),
    ],
    stages=[
        Stage(
            "verify",
            [
                Shell(
                    name="lint",
                    script="true",
                    on_success=[expect("notify", "verify/lint", "Succeeded")],
                ),
                Shell(
                    name="unit-tests",
                    script='test "{{ param.fail }}" != true',
                    on_failure=[expect("notify", "verify/unit-tests", "Failed")],
                    finally_=[Shell(name="upload-reports", script='echo "{{ step.status }}"')],
                ),
            ],
            on_failure=[expect("notify", "verify", "Failed")],
            finally_=[Shell(name="cleanup", script='echo "{{ step.name }} {{ step.status }}"')],
        ),
        Stage(
            "deploy",
            [Shell(name="staging", script="echo deploying")],
            when="{{ param.deploy }} == 'true'",
            finally_=[Shell(name="report", script='echo "{{ step.status }}"')],
        ),
    ],
)

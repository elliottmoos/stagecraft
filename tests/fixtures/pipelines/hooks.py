"""Step and stage handlers plus `when`. Handlers fail unless they see the expected step name and
outcome, so a Succeeded handler node proves its context arrived."""

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell


def expect(handler: str, step_name: str, outcome: str) -> Step:
    script = (
        f'test "{{{{ step.name }}}}" = {step_name} && test "{{{{ step.outcome }}}}" = {outcome}'
    )
    return Step(handler, Shell(script=script))


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
                Step(
                    "lint",
                    Shell(script="true"),
                    on_success=[expect("notify", "verify/lint", "succeeded")],
                ),
                Step(
                    "unit-tests",
                    Shell(script='test "{{ param.fail }}" != true'),
                    on_failure=[expect("notify", "verify/unit-tests", "failed")],
                    finally_=[Step("upload-reports", Shell(script='echo "{{ step.outcome }}"'))],
                ),
            ],
            on_failure=[expect("notify", "verify", "failed")],
            finally_=[Step("cleanup", Shell(script='echo "{{ step.name }} {{ step.outcome }}"'))],
        ),
        Stage(
            "deploy",
            [Step("staging", Shell(script="echo deploying"))],
            when="{{ param.deploy }} == 'true'",
            finally_=[Step("report", Shell(script='echo "{{ step.outcome }}"'))],
        ),
    ],
)

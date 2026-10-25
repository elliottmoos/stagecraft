import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.errors import CompileError
from stagecraft.spec import Pipeline, Stage, Step

from fixtures.actions import Shell
from fixtures.contract import CONTEXT
from fixtures.pipelines import hooks as hooks_fixture

SUCCEEDED = "{{inputs.parameters.status}} == Succeeded"
FAILED = "{{inputs.parameters.status}} == Failed || {{inputs.parameters.status}} == Error"


def manifest(pipeline):
    return compile_pipeline(pipeline, CONTEXT).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def task(m, dag, name):
    return next(t for t in template(m, dag)["dag"]["tasks"] if t["name"] == name)


def handler_tasks(m, dag):
    return [(t["name"], t["template"], t["when"]) for t in template(m, dag)["dag"]["tasks"]]


def test_step_handlers_run_from_an_exit_hook_passing_the_step_status():
    hooks = task(manifest(hooks_fixture.pipeline), "verify", "unit-tests")["hooks"]

    assert hooks == {
        "exit": {
            "template": "verify-unit-tests-on-exit",
            "arguments": {
                "parameters": [{"name": "status", "value": "{{tasks.unit-tests.status}}"}]
            },
        }
    }


def test_handler_dag_guards_each_handler_by_status():
    m = manifest(hooks_fixture.pipeline)

    assert template(m, "verify-unit-tests-on-exit")["inputs"] == {
        "parameters": [{"name": "status"}]
    }
    assert handler_tasks(m, "verify-unit-tests-on-exit") == [
        ("on-failure-notify", "verify-unit-tests-on-failure-notify", FAILED),
        (
            "finally-upload-reports-succeeded",
            "verify-unit-tests-finally-upload-reports-succeeded",
            SUCCEEDED,
        ),
        (
            "finally-upload-reports-failed",
            "verify-unit-tests-finally-upload-reports-failed",
            FAILED,
        ),
    ]


def test_stage_handlers_run_from_an_exit_hook_on_the_stage_task():
    m = manifest(hooks_fixture.pipeline)

    assert task(m, "main", "verify")["hooks"]["exit"]["template"] == "verify-on-exit"
    assert handler_tasks(m, "verify-on-exit") == [
        ("on-failure-notify", "verify-on-failure-notify", FAILED),
        ("finally-cleanup-succeeded", "verify-finally-cleanup-succeeded", SUCCEEDED),
        ("finally-cleanup-failed", "verify-finally-cleanup-failed", FAILED),
    ]


def test_step_placeholders_in_handlers_are_fixed_per_hook():
    m = manifest(hooks_fixture.pipeline)

    assert template(m, "verify-unit-tests-on-failure-notify")["container"]["args"] == [
        'test "verify/unit-tests" = verify/unit-tests && test "failed" = failed'
    ]
    assert template(m, "verify-finally-cleanup-succeeded")["container"]["args"] == [
        'echo "verify succeeded"'
    ]
    assert template(m, "verify-finally-cleanup-failed")["container"]["args"] == [
        'echo "verify failed"'
    ]


def test_steps_without_handlers_get_no_hooks():
    assert "hooks" not in task(manifest(hooks_fixture.pipeline), "deploy", "staging")


def test_stage_when_is_lowered_onto_the_stage_task():
    deploy = task(manifest(hooks_fixture.pipeline), "main", "deploy")

    # Quoted so Argo compares strings: a bare `true` would parse as a boolean.
    assert deploy["when"] == "'{{workflow.parameters.deploy}}' == 'true'"


def test_step_when_is_lowered_onto_the_step_task():
    pipeline = Pipeline(
        name="p",
        params=[hooks_fixture.pipeline.params[1]],
        stages=[
            Stage("s", [Step("shell", Shell(script="true"), when="{{ param.deploy }} == 'true'")])
        ],
    )

    assert task(manifest(pipeline), "s", "shell")["when"] == (
        "'{{workflow.parameters.deploy}}' == 'true'"
    )


def test_handler_errors_name_the_full_location():
    pipeline = Pipeline(
        name="p",
        stages=[
            Stage(
                "s",
                [
                    Step(
                        "shell", Shell(script="true"), on_failure=[Shell(script="{{ param.nope }}")]
                    )
                ],
            )
        ],
    )

    with pytest.raises(
        CompileError,
        match="pipeline 'p', stage 's', step 'shell', on_failure step 'shell': undeclared",
    ):
        compile_pipeline(pipeline, CONTEXT)


@pytest.mark.parametrize(
    "handler",
    [
        Step("shell", Shell(script="true"), when="true == true"),
        Step("shell", Shell(script="true"), on_failure=[Shell(script="true")]),
    ],
)
def test_handler_steps_cannot_have_when_or_handlers(handler):
    with pytest.raises(ValidationError, match="cannot set when, status, or handlers"):
        Stage("s", [Shell(script="true")], finally_=[handler])


def test_handler_names_must_be_unique_within_a_list():
    with pytest.raises(ValidationError, match="finally_: duplicate step name 'shell'"):
        Step("shell", Shell(script="true"), finally_=[Shell(script="a"), Shell(script="b")])

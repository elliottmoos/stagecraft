import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.errors import CompileError
from stagecraft.spec import Pipeline, Stage

from fixtures.actions import Shell
from fixtures.contract import CONTEXT
from fixtures.pipelines import handlers, smoke


def manifest(pipeline):
    return compile_pipeline(pipeline, CONTEXT).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def pipeline_with(**handlers):
    return Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])], **handlers)


def test_no_exit_handler_without_pipeline_handlers():
    m = manifest(smoke.pipeline)

    assert "onExit" not in m["spec"]
    assert "on-exit" not in [t["name"] for t in m["spec"]["templates"]]


def test_handlers_run_from_an_exit_handler_dag_branching_on_workflow_status():
    m = manifest(handlers.pipeline)

    assert m["spec"]["onExit"] == "on-exit"
    assert [
        (t["name"], t["template"], t["when"]) for t in template(m, "on-exit")["dag"]["tasks"]
    ] == [
        ("on-success-notify", "on-success-notify", "{{workflow.status}} == Succeeded"),
        (
            "on-failure-notify",
            "on-failure-notify",
            "{{workflow.status}} == Failed || {{workflow.status}} == Error",
        ),
    ]


def test_handler_failures_are_not_suppressed():
    # A failing handler is unexpected and must fail the pipeline (Argo's default for onExit).
    tasks = template(manifest(handlers.pipeline), "on-exit")["dag"]["tasks"]

    assert not any("continueOn" in t for t in tasks)


def test_step_placeholders_in_pipeline_handlers_are_the_workflow_name_and_fixed_outcome():
    m = manifest(handlers.pipeline)

    assert template(m, "on-success-notify")["container"]["args"] == [
        'echo "{{workflow.name}}: succeeded"; test "succeeded" = succeeded'
    ]
    assert template(m, "on-failure-notify")["container"]["args"] == [
        'echo "{{workflow.name}}: failed"; test "failed" = failed'
    ]


def test_step_placeholders_are_rejected_outside_handlers():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="echo {{ step.outcome }}")])])

    with pytest.raises(
        CompileError, match=r"stage 's', step 'shell': '\{\{ step.outcome }}' is only available"
    ):
        compile_pipeline(pipeline, CONTEXT)


def test_unknown_step_field_is_rejected():
    pipeline = pipeline_with(on_failure=[Shell(script="echo {{ step.sha }}")])

    with pytest.raises(CompileError, match="unknown step field 'sha'"):
        compile_pipeline(pipeline, CONTEXT)


def test_handler_errors_name_the_handler_list():
    pipeline = pipeline_with(on_failure=[Shell(script="echo {{ param.nope }}")])

    with pytest.raises(CompileError, match="pipeline 'p', on_failure step 'shell': undeclared"):
        compile_pipeline(pipeline, CONTEXT)


def test_handler_step_names_must_be_unique_within_a_list():
    with pytest.raises(ValidationError, match="on_success: duplicate step name 'shell'"):
        pipeline_with(on_success=[Shell(script="a"), Shell(script="b")])

import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Pipeline, Source, Stage, Step

from fixtures.actions import Shell
from fixtures.pipelines import status

SOURCE = Source(url="https://example.com/repo.git", revision="main")


def manifest(pipeline):
    return compile_pipeline(pipeline, CompileContext()).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, dag):
    return {t["name"]: t for t in template(m, dag)["dag"]["tasks"]}


def report_args(task):
    return {p["name"]: p["value"] for p in task["arguments"]["parameters"]}


def test_reports_go_through_a_best_effort_dag():
    m = manifest(status.pipeline)

    report = template(m, "status-report")
    assert report["inputs"] == {
        "parameters": [{"name": "context"}, {"name": "state"}, {"name": "description"}]
    }
    [call] = report["dag"]["tasks"]
    assert call["template"] == "status-reporter"
    assert call["continueOn"] == {"failed": True, "error": True}
    assert report_args(call) == {
        "context": "{{inputs.parameters.context}}",
        "state": "{{inputs.parameters.state}}",
        "description": "{{inputs.parameters.description}}",
    }


def test_reporter_compiles_once_with_status_inputs():
    reporter = template(manifest(status.pipeline), "status-reporter")

    assert reporter["inputs"] == {
        "parameters": [{"name": "context"}, {"name": "state"}, {"name": "description"}]
    }
    assert reporter["container"]["args"] == [
        'echo "{{inputs.parameters.context}}={{inputs.parameters.state}}'
        " ({{inputs.parameters.description}})"
        " for https://github.com/octocat/Hello-World.git"
        '@{{workflow.outputs.parameters.stagecraft-source-sha}}"'
    ]


def test_stage_reports_pending_before_it_starts():
    main = tasks(manifest(status.pipeline), "main")
    pending = main["verify-status-pending"]

    assert pending["template"] == "status-report"
    assert report_args(pending) == {
        "context": "ci/verify",
        "state": "pending",
        "description": "Running",
    }
    # The first stage and its pending report both wait for the source step.
    assert pending["depends"] == "source"
    assert main["verify"]["depends"] == "source && verify-status-pending"


def test_pending_report_shares_the_stage_depends_and_when():
    main = tasks(manifest(status.pipeline), "main")
    pending, deploy = main["deploy-status-pending"], main["deploy"]

    assert pending["depends"] == "verify"
    assert pending["when"] == deploy["when"] == "'{{workflow.parameters.deploy}}' == 'true'"
    assert deploy["depends"] == "verify && deploy-status-pending"


def test_stage_skipped_by_when_reports_success_skipped():
    skipped = tasks(manifest(status.pipeline), "main")["deploy-status-skipped"]

    assert report_args(skipped) == {
        "context": "ci/deploy",
        "state": "success",
        "description": "Skipped",
    }
    assert skipped["depends"] == "verify"
    assert skipped["when"] == "!('{{workflow.parameters.deploy}}' == 'true')"


def test_final_status_is_reported_from_the_exit_hook_by_outcome():
    m = manifest(status.pipeline)

    assert tasks(m, "main")["verify"]["hooks"]["exit"]["template"] == "verify-on-exit"
    finals = tasks(m, "verify-on-exit")
    assert {
        name: (t["template"], report_args(t)["state"], report_args(t)["description"], t["when"])
        for name, t in finals.items()
    } == {
        "status-success": (
            "status-report",
            "success",
            "Succeeded",
            "{{inputs.parameters.status}} == Succeeded",
        ),
        "status-failure": (
            "status-report",
            "failure",
            "Failed",
            "{{inputs.parameters.status}} == Failed",
        ),
        "status-error": (
            "status-report",
            "error",
            "Error",
            "{{inputs.parameters.status}} == Error",
        ),
    }


def test_step_status_reports_pending_inside_the_stage_dag():
    verify = tasks(manifest(status.pipeline), "verify")

    assert report_args(verify["unit-tests-status-pending"])["context"] == "ci/unit-tests"
    assert verify["unit-tests"]["depends"] == "unit-tests-status-pending"
    assert "depends" not in verify["lint"]


def test_pipeline_status_reports_pending_at_start_and_final_from_on_exit():
    m = manifest(status.pipeline)

    pending = tasks(m, "main")["pipeline-status-pending"]
    assert report_args(pending)["context"] == "ci/pipeline"
    assert pending["depends"] == "source"  # the commit to report on is known from then on
    assert m["spec"]["onExit"] == "on-exit"
    failure = tasks(m, "on-exit")["status-failure"]
    assert report_args(failure)["context"] == "ci/pipeline"
    assert failure["when"] == (
        "{{workflow.status}} == Failed"
        " && '{{workflow.outputs.parameters.stagecraft-source-sha}}' =~ '^[0-9a-f]{40}$'"
    )


def test_pipelines_without_statuses_are_unchanged():
    m = manifest(Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])]))

    names = [t["name"] for t in m["spec"]["templates"]]
    assert "status-reporter" not in names
    assert "status-report" not in names


def test_status_placeholders_are_rejected_outside_the_reporter():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="{{ status.state }}")])])

    with pytest.raises(CompileError, match="only available in the status_reporter"):
        compile_pipeline(pipeline, CompileContext())


def test_status_contexts_must_be_unique():
    with pytest.raises(ValidationError, match="duplicate status context 'ci/x'"):
        Pipeline(
            name="p",
            source=SOURCE,
            status="ci/x",
            status_reporter=status.REPORTER,
            stages=[Stage("s", [Shell(script="true")], status="ci/x")],
        )


def test_statuses_need_a_reporter():
    with pytest.raises(ValidationError, match="status contexts need a status_reporter"):
        Pipeline(name="p", source=SOURCE, status="ci/x", stages=[Stage("s", [Shell(script="")])])


def test_handler_steps_cannot_report_status():
    with pytest.raises(ValidationError, match="handler steps cannot set when, status, or"):
        Stage(
            "s",
            [Shell(script="true")],
            finally_=[Step("shell", Shell(script="true"), status="ci/x")],
        )

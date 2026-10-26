"""Stage sources: extra repositories a stage checks out at its start, for its own steps
(DESIGN.md section 16)."""

import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Pipeline, Source, Stage, Step

from fixtures.actions import Shell

MAIN = Source(url="https://example.com/service.git", revision="main")
FIXTURES = Source(name="fixtures", url="https://example.com/fixtures.git", revision="v2")
SHA = "{{workflow.outputs.parameters.stagecraft-source-e2e-fixtures-sha}}"


def manifest(pipeline):
    return compile_pipeline(pipeline, CompileContext()).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, dag):
    return {t["name"]: t for t in template(m, dag)["dag"]["tasks"]}


def pipeline(*stages, source=MAIN):
    return Pipeline(name="p", source=source, stages=list(stages))


def e2e(*steps, **fields):
    return Stage("e2e", list(steps) or [Shell(script="true")], sources=[FIXTURES], **fields)


def test_a_stage_source_is_fetched_at_the_start_of_its_stage():
    m = manifest(pipeline(e2e(Shell(script="true"), Step("other", Shell(script="true")))))

    stage = tasks(m, "e2e")
    assert stage["source-fixtures"] == {
        "name": "source-fixtures",
        "template": "e2e-source-fixtures",
    }
    assert stage["shell"]["depends"] == "source-fixtures"
    assert stage["other"]["depends"] == "source-fixtures"
    fetch = template(m, "e2e-source-fixtures")
    assert "git init -q /sources/fixtures" in fetch["container"]["args"][0]
    env = {e["name"]: e.get("value") for e in fetch["container"]["env"]}
    assert (env["URL"], env["REVISION"]) == ("https://example.com/fixtures.git", "v2")
    assert fetch["outputs"]["artifacts"] == [
        {
            "name": "checkout",
            "globalName": "stagecraft-source-e2e-fixtures",
            "path": "/sources/fixtures",
        }
    ]


def test_the_stages_steps_get_its_sources_beside_the_main_one():
    m = manifest(pipeline(e2e()))

    arguments = tasks(m, "e2e")["shell"]["arguments"]["artifacts"]
    assert {
        "name": "stagecraft-source-e2e-fixtures",
        "from": "{{workflow.outputs.artifacts.stagecraft-source-e2e-fixtures}}",
    } in arguments
    step = template(m, "e2e-shell")
    assert {"name": "stagecraft-source-e2e-fixtures", "path": "/sources/fixtures"} in (
        step["inputs"]["artifacts"]
    )
    env = {e["name"]: e["value"] for e in step["container"]["env"]}
    assert env["STAGECRAFT_SOURCE_FIXTURES"] == "/sources/fixtures"
    assert step["container"]["workingDir"] == "/sources/main"


def test_without_a_main_source_the_working_directory_is_left_alone():
    step = template(manifest(pipeline(e2e(), source=None)), "e2e-shell")

    assert "workingDir" not in step["container"]


def test_other_stages_dont_get_a_stages_sources():
    m = manifest(pipeline(Stage("unit", [Shell(script="true")]), e2e()))

    assert "depends" not in tasks(m, "unit")["shell"]
    assert "STAGECRAFT_SOURCE_FIXTURES" not in str(template(m, "unit-shell"))


def test_stage_source_placeholders_work_in_its_steps_and_handlers():
    m = manifest(
        pipeline(
            e2e(
                Step("run", Shell(script="echo {{ source.fixtures.sha }}"),
                     when="{{ source.fixtures.revision }} == 'v2'"),
                finally_=[Shell(script="echo {{ source.fixtures.sha }}")],
            )
        )
    )  # fmt: skip

    assert template(m, "e2e-run")["container"]["args"] == [f"echo {SHA}"]
    assert tasks(m, "e2e")["run"]["when"] == "'v2' == 'v2'"
    assert template(m, "e2e-finally-shell-failed")["container"]["args"] == [f"echo {SHA}"]


def test_stage_source_placeholders_are_only_available_in_that_stage():
    with pytest.raises(CompileError, match=r"unknown source field 'fixtures.sha'"):
        manifest(pipeline(e2e(), Stage("later", [Shell(script="{{ source.fixtures.sha }}")])))


def test_a_step_with_a_status_reports_pending_after_the_stage_sources():
    m = manifest(
        Pipeline(
            name="p",
            source=MAIN,
            status_reporter=Shell(script=""),
            stages=[e2e(Step("run", Shell(script="true"), status="ci/e2e-run"))],
        )
    )

    stage = tasks(m, "e2e")
    assert stage["run-status-pending"]["depends"] == "source-fixtures"
    assert stage["run"]["depends"] == "source-fixtures && run-status-pending"


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        ([Source(url="https://x/y.git", revision="main")], "stage sources need a name"),
        ([Source(name="main", url="https://x/y.git", revision="main")], "'main' is reserved"),
        ([FIXTURES, FIXTURES], "duplicate source name 'fixtures'"),
    ],
)
def test_stage_source_names_are_checked(sources, message):
    with pytest.raises(ValidationError, match=message):
        Stage("e2e", [Shell(script="true")], sources=sources)


def test_a_step_cannot_take_a_stage_sources_task_name():
    with pytest.raises(ValidationError, match="step name 'source-fixtures' is taken"):
        Stage("e2e", [Step("source-fixtures", Shell(script="true"))], sources=[FIXTURES])


def test_the_pipeline_source_is_named_main():
    with pytest.raises(ValidationError, match="the pipeline's source is named 'main'"):
        Pipeline(name="p", source=FIXTURES, stages=[Stage("s", [Shell(script="true")])])

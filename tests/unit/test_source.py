"""The pipeline source: a generated step resolving the revision to a commit and sharing the
checkout with every step (DESIGN.md section 16)."""

import pytest
from hera.workflows import Container
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Source, Stage

from fixtures.actions import Shell
from fixtures.pipelines import smoke, source

SHA = "{{workflow.outputs.parameters.stagecraft-source-sha}}"
CHECKOUT = "{{workflow.outputs.artifacts.stagecraft-source}}"


def manifest(pipeline, ctx=None):
    return compile_pipeline(pipeline, ctx or CompileContext()).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, dag):
    return {t["name"]: t for t in template(m, dag)["dag"]["tasks"]}


def with_source(*stages, **fields):
    return Pipeline(
        name="p",
        source=Source(url="https://example.com/repo.git", revision="main"),
        stages=list(stages) or [Stage("s", [Shell(script="true")])],
        **fields,
    )


def test_the_source_step_runs_first_and_everything_else_waits_for_it():
    main = tasks(manifest(source.pipeline), "main")

    assert main["source"] == {"name": "source", "template": "source"}
    assert main["pipeline-status-pending"]["depends"] == "source"
    # As without a source, the first stage doesn't wait for the pipeline's pending report.
    assert main["verify"]["depends"] == "source"


def test_the_source_step_fetches_shallowly_and_publishes_the_commit_and_checkout():
    m = manifest(source.pipeline)
    step = template(m, "source")

    container = step["container"]
    assert container["image"] == "<<values:stagecraft.sources.image>>"
    assert "fetch -q --depth 1" in container["args"][0]
    env = {e["name"]: e for e in container["env"]}
    assert env["URL"]["value"] == "https://github.com/octocat/Hello-World.git"
    assert env["REVISION"]["value"] == "{{workflow.parameters.revision}}"
    assert env["GIT_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "<<values:stagecraft.sources.secret>>",
        "key": "password",
        "optional": True,
    }
    assert step["outputs"] == {
        "parameters": [
            {
                "name": "sha",
                "globalName": "stagecraft-source-sha",
                "valueFrom": {"path": "/tmp/stagecraft-sha"},
            }
        ],
        "artifacts": [
            {"name": "checkout", "globalName": "stagecraft-source", "path": "/sources/main"}
        ],
    }


def test_the_source_settings_are_framework_values():
    ctx = CompileContext()

    compile_pipeline(source.pipeline, ctx)

    assert ctx.references["stagecraft.sources.image"] is str
    assert ctx.references["stagecraft.sources.secret"] is str


def test_every_step_gets_the_checkout_as_its_working_directory():
    m = manifest(source.pipeline)

    assert tasks(m, "verify")["readme"]["arguments"]["artifacts"] == [
        {"name": "stagecraft-source", "from": CHECKOUT}
    ]
    step = template(m, "verify-readme")
    assert step["inputs"]["artifacts"] == [{"name": "stagecraft-source", "path": "/sources/main"}]
    assert {"name": "STAGECRAFT_SOURCE_MAIN", "value": "/sources/main"} in step["container"]["env"]
    assert step["container"]["workingDir"] == "/sources/main"


def test_an_actions_own_working_directory_is_kept():
    class InSubdir(Shell):
        def compile(self, ctx: CompileContext) -> Container:
            container = super().compile(ctx)
            container.working_dir = "/sources/main/app"
            return container

    m = manifest(with_source(Stage("s", [InSubdir(script="true")])))

    assert template(m, "s-in-subdir")["container"]["workingDir"] == "/sources/main/app"


def test_handlers_and_the_reporter_get_no_checkout():
    m = manifest(source.pipeline)

    for name in ("verify-finally-shell-succeeded", "status-reporter"):
        assert "artifacts" not in template(m, name).get("inputs", {})
    for task in tasks(m, "verify-on-exit").values():
        assert "artifacts" not in task.get("arguments", {})


def test_source_placeholders_work_in_every_action():
    m = manifest(source.pipeline)

    assert SHA in template(m, "verify-readme")["container"]["args"][0]
    assert template(m, "verify-finally-shell-failed")["container"]["args"] == [
        f'echo "verify failed at {SHA}"'
    ]
    reporter = template(m, "status-reporter")["container"]["args"][0]
    assert f"for https://github.com/octocat/Hello-World.git@{SHA}" in reporter


def test_the_revision_placeholder_is_the_revision_as_given():
    pipeline = with_source(Stage("s", [Shell(script="echo {{ source.revision }}")]))

    assert template(manifest(pipeline), "s-shell")["container"]["args"] == ["echo main"]


def test_source_placeholders_need_a_source():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="{{ source.sha }}")])])

    with pytest.raises(CompileError, match=r"'\{\{ source.sha }}' needs a pipeline source"):
        compile_pipeline(pipeline, CompileContext())


def test_source_url_and_revision_may_use_params_and_values():
    pipeline = Pipeline(
        name="p",
        params=[Param("rev", default="main")],
        source=Source(url="{{ values.git.url }}", revision="{{ param.rev }}"),
        stages=[Stage("s", [Shell(script="true")])],
    )
    ctx = CompileContext()

    env = {e["name"]: e for e in template(manifest(pipeline, ctx), "source")["container"]["env"]}

    assert env["URL"]["value"] == "<<values:git.url>>"
    assert env["REVISION"]["value"] == "{{workflow.parameters.rev}}"
    assert ctx.references["git.url"] is str


def test_the_pipelines_final_status_is_only_reported_with_a_commit():
    on_exit = tasks(manifest(source.pipeline), "on-exit")

    assert on_exit["status-success"]["when"] == (
        f"{{{{workflow.status}}}} == Succeeded && '{SHA}' =~ '^[0-9a-f]{{40}}$'"
    )


def test_statuses_need_a_source():
    with pytest.raises(ValidationError, match="status contexts need a pipeline source"):
        Pipeline(
            name="p",
            status="ci/p",
            status_reporter=Shell(script=""),
            stages=[Stage("s", [Shell(script="true")])],
        )


def test_source_is_a_reserved_stage_name_with_a_source():
    with pytest.raises(ValidationError, match="stage name 'source' is reserved"):
        with_source(Stage("source", [Shell(script="true")]))


def test_pipelines_without_a_source_are_unchanged():
    assert "source" not in tasks(manifest(smoke.pipeline), "main")


def test_a_source_needs_a_url_and_a_revision():
    with pytest.raises(ValidationError, match="revision"):
        Source(url="https://example.com/repo.git")

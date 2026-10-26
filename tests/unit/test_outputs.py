"""Results passed between steps: output parameters as {{ steps.<stage>.<step>.<output> }} and
artifacts bound with Step(inputs=...), both through global outputs (DESIGN.md section 16)."""

import pytest
from hera.workflows import Container, GitArtifact, Parameter
from hera.workflows.models import ValueFrom
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Pipeline, Source, Stage, Step

from fixtures.actions import Build, Package, Shell

SOURCE = Source(url="https://example.com/repo.git", revision="main")
DIGEST = "{{workflow.outputs.parameters.stagecraft-build-binary-digest}}"
BIN = "{{workflow.outputs.artifacts.stagecraft-build-binary-bin}}"


def manifest(pipeline):
    return compile_pipeline(pipeline, CompileContext()).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, dag):
    return {t["name"]: t for t in template(m, dag)["dag"]["tasks"]}


def build_then(*later, build=None, **fields):
    return Pipeline(
        name="p",
        source=fields.pop("source", SOURCE),
        stages=[Stage("build", [build or Step("binary", Build())]), *later],
        **fields,
    )


def package(**step_fields):
    step_fields.setdefault("inputs", {"bin": "build.binary.bin"})
    return Step("image", Package(digest="{{ steps.build.binary.digest }}"), **step_fields)


def test_step_outputs_are_published_globally_with_paths_in_the_checkout():
    outputs = template(manifest(build_then()), "build-binary")["outputs"]

    assert outputs == {
        "artifacts": [
            {
                "name": "bin",
                "globalName": "stagecraft-build-binary-bin",
                "path": "/sources/main/dist/app",
            }
        ],
        "parameters": [
            {
                "name": "digest",
                "globalName": "stagecraft-build-binary-digest",
                "valueFrom": {"path": "/tmp/digest"},
            }
        ],
    }


def test_a_bound_input_artifact_comes_from_the_global_output():
    m = manifest(build_then(Stage("package", [package()])))

    arguments = tasks(m, "package")["image"]["arguments"]["artifacts"]
    assert {"name": "bin", "from": BIN} in arguments
    inputs = template(m, "package-image")["inputs"]["artifacts"]
    assert {"name": "bin", "path": "/sources/main/dist/app"} in inputs


def test_output_parameters_are_placeholders_in_later_steps():
    m = manifest(build_then(Stage("package", [package()])))

    env = {e["name"]: e.get("value") for e in template(m, "package-image")["container"]["env"]}
    assert env["DIGEST"] == DIGEST


def test_output_parameters_work_in_conditions_and_handlers():
    m = manifest(
        build_then(
            Stage(
                "deploy",
                [Shell(script="echo {{ steps.build.binary.digest }}")],
                when="{{ steps.build.binary.digest }} != ''",
            ),
            on_failure=[Shell(script="echo {{ steps.build.binary.digest }}")],
        )
    )

    assert tasks(m, "main")["deploy"]["when"] == f"'{DIGEST}' != ''"
    assert template(m, "on-failure-shell")["container"]["args"] == [f"echo {DIGEST}"]


@pytest.mark.parametrize(
    ("stages", "message"),
    [
        (
            [
                Stage(
                    "build",
                    [Step("binary", Build()), Shell(script="{{ steps.build.binary.digest }}")],
                )
            ],
            r"'\{\{ steps.build.binary.digest }}' is not an output of a step in an earlier stage",
        ),
        (
            [Stage("build", [Shell(script="{{ steps.build.binary.sha }}")])],
            "is not an output of a step in an earlier stage",
        ),
    ],
)
def test_output_references_must_come_from_earlier_stages(stages, message):
    with pytest.raises(CompileError, match=message):
        manifest(Pipeline(name="p", source=SOURCE, stages=stages))


def test_outputs_of_conditional_steps_cannot_be_used():
    pipeline = build_then(
        Stage("package", [package()]),
        build=Step("binary", Build(), when="'a' == 'b'"),
    )

    with pytest.raises(CompileError, match="may be skipped"):
        manifest(pipeline)


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        (
            {},
            r"input artifact 'bin' is not bound; set inputs=\{'bin': '<stage>.<step>.<artifact>'\}",
        ),
        ({"bin": "build.binary.bin", "lib": "build.binary.bin"}, "has no input artifact 'lib'"),
        ({"bin": "build.nope.bin"}, "'build.nope.bin' is not an artifact of a step in an earlier"),
        ({"bin": "package.image.bin"}, "'package.image.bin' is not an artifact of a step in an"),
    ],
)
def test_input_bindings_are_checked(inputs, message):
    with pytest.raises(CompileError, match=message):
        manifest(build_then(Stage("package", [package(inputs=inputs)])))


def test_inputs_with_their_own_source_need_no_binding():
    class FromGit(Shell):
        def compile(self, ctx: CompileContext) -> Container:
            container = super().compile(ctx)
            container.inputs = [GitArtifact(name="docs", path="/docs", repo="https://x/y.git")]
            return container

    manifest(Pipeline(name="p", stages=[Stage("s", [FromGit(script="true")])]))


def test_relative_artifact_paths_need_a_source():
    with pytest.raises(CompileError, match="relative artifact path 'dist/app' needs a pipeline"):
        manifest(Pipeline(name="p", stages=[Stage("build", [Step("binary", Build())])]))


def test_step_outputs_are_not_available_to_the_status_reporter():
    pipeline = build_then(
        status="ci/p",
        status_reporter=Shell(script="echo {{ steps.build.binary.digest }}"),
    )

    with pytest.raises(CompileError, match=r"status_reporter: '\{\{ steps.build.binary.digest"):
        manifest(pipeline)


@pytest.mark.parametrize("reference", ["build.bin", "build.binary.bin.x", "Build.binary.bin"])
def test_input_references_are_stage_step_artifact(reference):
    with pytest.raises(ValidationError, match=r"must be '<stage>\.<step>\.<artifact>'"):
        Step("image", Package(), inputs={"bin": reference})


def test_handlers_take_no_inputs():
    with pytest.raises(ValidationError, match="handler steps cannot take inputs"):
        Stage("s", [Shell(script="")], on_failure=[package()])


def test_relative_output_parameter_paths_are_in_the_checkout_too():
    class Digest(Shell):
        def compile(self, ctx: CompileContext) -> Container:
            container = super().compile(ctx)
            container.outputs = [Parameter(name="digest", value_from=ValueFrom(path="out/digest"))]
            return container

    pipeline = Pipeline(name="p", source=SOURCE, stages=[Stage("s", [Digest(script="true")])])

    [output] = template(manifest(pipeline), "s-digest")["outputs"]["parameters"]
    assert output["valueFrom"] == {"path": "/sources/main/out/digest"}

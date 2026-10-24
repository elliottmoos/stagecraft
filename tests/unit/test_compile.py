import pytest

from stagecraft.compile import compile_pipeline
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell
from fixtures.pipelines import smoke


def manifest(pipeline):
    return compile_pipeline(pipeline).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, name):
    return [(t["name"], t["template"], t.get("depends")) for t in template(m, name)["dag"]["tasks"]]


def test_compiles_to_workflow_template_named_after_pipeline():
    m = manifest(smoke.pipeline)

    assert m["kind"] == "WorkflowTemplate"
    assert m["metadata"]["name"] == "smoke-ci"
    assert m["spec"]["entrypoint"] == "main"


def test_stages_run_in_order_with_one_depends_edge_each():
    assert tasks(manifest(smoke.pipeline), "main") == [
        ("prepare", "prepare", None),
        ("verify", "verify", "prepare"),
        ("package", "package", "verify"),
    ]


def test_each_stage_is_a_sub_dag_of_parallel_blocks():
    assert tasks(manifest(smoke.pipeline), "verify") == [
        ("lint", "verify-lint", None),
        ("unit-tests", "verify-unit-tests", None),
    ]


def test_block_templates_are_named_stage_dash_block():
    lint = template(manifest(smoke.pipeline), "verify-lint")

    assert lint["container"]["args"] == ["echo lint; sleep 2"]


def test_params_become_workflow_arguments():
    assert manifest(smoke.pipeline)["spec"]["arguments"]["parameters"] == [
        {"name": "repo", "description": "Repository to build, as owner/name"},
        {
            "name": "push",
            "value": "false",
            "enum": ["true", "false"],
            "description": "Push the image",
        },
    ]


def test_param_placeholders_in_blocks_are_lowered():
    clone = template(manifest(smoke.pipeline), "prepare-clone")

    assert clone["container"]["args"] == ["echo cloning {{workflow.parameters.repo}}"]


def test_compile_does_not_mutate_the_spec():
    compile_pipeline(smoke.pipeline)

    assert smoke.pipeline.stages[0].blocks[0].script == "echo cloning {{ param.repo }}"


def test_undeclared_param_error_names_the_location():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="{{ param.nope }}")])])

    with pytest.raises(CompileError, match="pipeline 'p', stage 's', block 'shell': undeclared"):
        compile_pipeline(pipeline)


@pytest.mark.parametrize(
    "stages",
    [
        [Stage("a-b", [Shell(script="x")]), Stage("a", [Shell(name="b", script="x")])],
        [Stage("main", [Shell(script="x")])],
    ],
)
def test_template_name_collisions_are_rejected(stages):
    pipeline = Pipeline(name="p", params=[Param("x", default="1")], stages=stages)

    with pytest.raises(CompileError, match=r"pipeline 'p': template name '(a-b|main)'"):
        compile_pipeline(pipeline)

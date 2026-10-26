"""The setup step: runs first, as an action or an existing WorkflowTemplate, and its declared
outputs are readable everywhere as {{ setup.<output> }} (DESIGN.md section 16)."""

import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Source, Stage, Step, TemplateRef

from fixtures.actions import Emit, Shell

REF = "{{workflow.outputs.parameters.stagecraft-setup-ref}}"
PARSE = TemplateRef(
    "parse-webhook",
    template="parse",
    arguments={"payload": "{{ param.payload }}"},
    outputs=["repo_url", "ref"],
)


def manifest(pipeline):
    return compile_pipeline(pipeline, CompileContext()).to_dict()


def template(m, name):
    return next(t for t in m["spec"]["templates"] if t["name"] == name)


def tasks(m, dag):
    return {t["name"]: t for t in template(m, dag)["dag"]["tasks"]}


def pipeline(setup=PARSE, stages=None, **fields):
    return Pipeline(
        name="p",
        params=[Param("payload", default="{}")],
        setup=setup,
        stages=stages or [Stage("s", [Shell(script="true")])],
        **fields,
    )


def test_a_template_ref_setup_calls_the_template_and_publishes_its_outputs():
    m = manifest(pipeline())

    assert tasks(m, "main")["setup"] == {"name": "setup", "template": "setup"}
    assert template(m, "setup") == {
        "name": "setup",
        "steps": [
            [
                {
                    "name": "parse",
                    "templateRef": {"name": "parse-webhook", "template": "parse"},
                    "arguments": {
                        "parameters": [
                            {"name": "payload", "value": "{{workflow.parameters.payload}}"}
                        ]
                    },
                }
            ]
        ],
        "outputs": {
            "parameters": [
                {
                    "name": name,
                    "globalName": f"stagecraft-setup-{name}",
                    "valueFrom": {"parameter": f"{{{{steps.parse.outputs.parameters.{name}}}}}"},
                }
                for name in ("repo_url", "ref")
            ]
        },
    }


def test_an_action_setup_runs_as_a_step_and_publishes_the_actions_outputs():
    m = manifest(pipeline(setup=Step("parse", Emit(values={"event": "push"}))))

    [[step]] = template(m, "setup")["steps"]
    assert step == {"name": "parse", "template": "setup-parse"}
    assert template(m, "setup-parse")["outputs"]["parameters"] == [
        {"name": "event", "valueFrom": {"path": "/tmp/event"}}
    ]
    assert template(m, "setup")["outputs"]["parameters"][0]["globalName"] == (
        "stagecraft-setup-event"
    )


def test_a_bare_action_setup_is_named_after_its_class():
    m = manifest(pipeline(setup=Emit(values={"event": "push"})))

    assert template(m, "setup")["steps"] == [[{"name": "emit", "template": "setup-emit"}]]


def test_everything_runs_after_the_setup_and_the_source_after_both():
    m = manifest(pipeline(source=Source(url="{{ setup.repo_url }}", revision="{{ setup.ref }}")))

    main = tasks(m, "main")
    assert main["source"]["depends"] == "setup"
    assert main["s"]["depends"] == "source"


def test_without_a_source_the_first_stage_waits_for_the_setup():
    assert tasks(manifest(pipeline()), "main")["s"]["depends"] == "setup"


def test_setup_outputs_work_in_the_source_steps_conditions_and_handlers():
    m = manifest(
        pipeline(
            source=Source(url="{{ setup.repo_url }}", revision="{{ setup.ref }}"),
            stages=[
                Stage(
                    "s",
                    [Shell(script="echo {{ setup.ref }}")],
                    when="{{ setup.ref }} == 'main'",
                    finally_=[Shell(script="echo {{ setup.ref }}")],
                )
            ],
        )
    )

    env = {e["name"]: e.get("value") for e in template(m, "source")["container"]["env"]}
    assert env["REVISION"] == REF
    assert template(m, "s-shell")["container"]["args"] == [f"echo {REF}"]
    assert tasks(m, "main")["s"]["when"] == f"'{REF}' == 'main'"
    assert template(m, "s-finally-shell-failed")["container"]["args"] == [f"echo {REF}"]


def test_undeclared_setup_outputs_fail_the_render():
    with pytest.raises(CompileError, match=r"unknown setup field 'sha'; available: \['ref'"):
        manifest(pipeline(stages=[Stage("s", [Shell(script="{{ setup.sha }}")])]))


def test_setup_placeholders_need_a_setup():
    plain = Pipeline(name="p", stages=[Stage("s", [Shell(script="{{ setup.ref }}")])])

    with pytest.raises(CompileError, match=r"'\{\{ setup.ref }}' needs a setup step"):
        manifest(plain)


def test_template_ref_arguments_take_params_and_values():
    ctx = CompileContext()
    ref = TemplateRef("parse-webhook", template="parse", arguments={"token": "{{ values.t }}"})

    compile_pipeline(pipeline(setup=ref), ctx)

    assert ctx.references["t"] is str


def test_the_setup_step_is_plain():
    with pytest.raises(
        ValidationError, match="the setup step cannot set when, status, inputs, or handlers"
    ):
        pipeline(setup=Step("parse", Emit(values={}), status="ci/parse"))


def test_setup_is_a_reserved_stage_name():
    with pytest.raises(ValidationError, match="stage name 'setup' is reserved"):
        Pipeline(name="p", stages=[Stage("setup", [Shell(script="true")])])


@pytest.mark.parametrize("name", ["bad name", "1x", "a.b"])
def test_template_ref_output_names_must_be_identifiers(name):
    with pytest.raises(ValidationError, match="output name"):
        TemplateRef("parse-webhook", template="parse", outputs=[name])

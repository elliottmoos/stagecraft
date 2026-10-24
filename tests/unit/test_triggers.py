"""Cron triggers compile to CronWorkflows that run the pipeline's WorkflowTemplate."""

import pytest
from pydantic import ValidationError

from stagecraft.compile import compile_pipeline, compile_triggers
from stagecraft.errors import CompileError
from stagecraft.serialize import serialize
from stagecraft.spec import Param, Pipeline, Stage, Trigger

from fixtures.blocks import Shell
from fixtures.contract import CONTEXT
from fixtures.pipelines import smoke, triggers

MODE = Param("mode", default="a", enum=["a", "b"])


def pipeline_with(*trigger_list, params=(MODE,)):
    return Pipeline(
        name="p",
        params=list(params),
        stages=[Stage("s", [Shell(script="true")])],
        triggers=list(trigger_list),
    )


def crons(pipeline):
    return [c.to_dict() for c in compile_triggers(pipeline, CONTEXT)]


def test_no_triggers_means_no_cron_workflows():
    assert crons(smoke.pipeline) == []


def test_a_cron_trigger_runs_the_pipeline_template_with_its_params():
    nightly = crons(triggers.pipeline)[0]

    assert nightly == {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "CronWorkflow",
        "metadata": {"name": "triggers-ci-nightly"},
        "spec": {
            "schedules": ["0 2 * * *"],
            "workflowSpec": {
                "arguments": {
                    "parameters": [
                        {"name": "repo", "value": "example/repo"},
                        {"name": "mode", "value": "nightly"},
                    ]
                },
                "workflowTemplateRef": {"name": "triggers-ci"},
            },
        },
    }


def test_timezone_and_value_params_are_passed_through():
    weekly = crons(triggers.pipeline)[1]["spec"]

    assert weekly["timezone"] == "Europe/Amsterdam"
    assert weekly["workflowSpec"]["arguments"]["parameters"][2] == {
        "name": "channel",
        "value": "<<values:notify.channel>>",
    }


def test_cron_workflows_follow_the_template_in_one_file():
    template = compile_pipeline(triggers.pipeline, CONTEXT)

    rendered = serialize(template, *compile_triggers(triggers.pipeline, CONTEXT))

    assert rendered.count("\n---\n") == 2
    assert rendered.index("kind: WorkflowTemplate") < rendered.index("kind: CronWorkflow")


def test_trigger_name_defaults_to_cron():
    assert crons(pipeline_with(Trigger.cron("@daily")))[0]["metadata"]["name"] == "p-cron"


def test_trigger_names_must_be_unique():
    with pytest.raises(ValidationError, match="duplicate trigger name 'cron'"):
        pipeline_with(Trigger.cron("@daily"), Trigger.cron("@hourly"))


@pytest.mark.parametrize("schedule", ["0 2 * *", "every day", "@sometimes", "0 2 * * * *"])
def test_schedules_must_be_valid_cron(schedule):
    with pytest.raises(ValidationError, match="is not a cron schedule"):
        Trigger.cron(schedule)


def test_cron_workflow_names_fit_argos_limit():
    with pytest.raises(ValidationError, match=r"'p-n+' is longer than 52 characters"):
        pipeline_with(Trigger.cron("@daily", name="n" * 51))


def test_trigger_params_must_be_declared():
    with pytest.raises(ValidationError, match="trigger 'cron': undeclared param 'nope'"):
        pipeline_with(Trigger.cron("@daily", params={"nope": "x"}))


def test_trigger_params_must_be_in_the_enum():
    with pytest.raises(ValidationError, match="trigger 'cron': param 'mode' value 'c' is not in"):
        pipeline_with(Trigger.cron("@daily", params={"mode": "c"}))


def test_triggers_must_set_required_params():
    with pytest.raises(ValidationError, match="trigger 'cron': required param 'repo' has no value"):
        pipeline_with(Trigger.cron("@daily"), params=[Param("repo", required=True)])


def test_trigger_param_values_cannot_use_other_placeholders():
    pipeline = pipeline_with(Trigger.cron("@daily", params={"mode": "{{ param.mode }}"}))

    with pytest.raises(CompileError, match="pipeline 'p', trigger 'cron', param 'mode'"):
        compile_triggers(pipeline, CONTEXT)


def test_trigger_value_params_are_checked_against_the_contract():
    pipeline = pipeline_with(Trigger.cron("@daily", params={"mode": "{{ values.nope }}"}))

    with pytest.raises(CompileError, match="value 'nope' is not declared"):
        compile_triggers(pipeline, CONTEXT)

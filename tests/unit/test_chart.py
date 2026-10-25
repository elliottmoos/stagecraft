"""The generated chart: Chart.yaml, a values schema built from value references, and templates
(DESIGN.md section 16)."""

import json

import yaml

from stagecraft.chart import MARKER, render_chart, values_schema
from stagecraft.spec import Pipeline, Stage

from fixtures.actions import Shell
from fixtures.pipelines import smoke, triggers


def test_the_chart_holds_chart_metadata_a_schema_and_one_template_per_pipeline():
    files = render_chart([smoke.pipeline, triggers.pipeline])

    assert sorted(files) == [
        "Chart.yaml",
        "templates/smoke-ci.yaml",
        "templates/triggers-ci.yaml",
        "values.schema.json",
    ]


def test_chart_metadata_names_the_chart_and_carries_the_marker():
    chart = yaml.safe_load(render_chart([smoke.pipeline], name="ci-pipelines")["Chart.yaml"])

    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "ci-pipelines"
    assert chart["version"] == "0.0.0"
    assert chart["annotations"] == {MARKER: "true"}


def test_the_chart_is_named_pipelines_by_default():
    assert yaml.safe_load(render_chart([smoke.pipeline])["Chart.yaml"])["name"] == "pipelines"


def test_every_reference_is_a_required_typed_property():
    schema = values_schema({"images.shell": str, "build.retries": int, "build.debug": bool})

    assert schema == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["build", "images"],
        "properties": {
            "global": {"type": "object"},
            "build": {
                "type": "object",
                "required": ["debug", "retries"],
                "properties": {"debug": {"type": "boolean"}, "retries": {"type": "integer"}},
            },
            "images": {
                "type": "object",
                "required": ["shell"],
                "properties": {"shell": {"type": "string"}},
            },
        },
    }


def test_floats_are_numbers():
    assert values_schema({"x": float})["properties"]["x"] == {"type": "number"}


def test_the_schema_collects_references_from_every_pipeline():
    notify = Pipeline(
        name="notify", stages=[Stage("s", [Shell(script="{{ values.notify.channel }}")])]
    )

    schema = json.loads(render_chart([smoke.pipeline, notify])["values.schema.json"])

    assert schema["required"] == ["images", "notify"]

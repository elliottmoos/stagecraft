"""Value references validated against a values contract (DESIGN.md sections 8 and 9.3)."""

import pytest

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.errors import CompileError
from stagecraft.serialize import serialize
from stagecraft.spec import Pipeline, Stage

from fixtures.blocks import Shell
from fixtures.contract import CONTEXT

SCHEMA = {
    "type": "object",
    "required": ["a", "obj", "nullable", "choice", "loose", "bad-key", "mixed"],
    "properties": {
        "a": {
            "type": "object",
            "required": ["s", "n", "ref"],
            "properties": {
                "s": {"type": "string"},
                "n": {"type": "integer"},
                "ref": {"$ref": "#/$defs/flag"},
                "optional": {"type": "string"},
            },
        },
        "obj": {"type": "object", "required": [], "properties": {}},
        "nullable": {"type": ["string", "null"]},
        "choice": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        "loose": {"type": "object", "additionalProperties": True, "properties": {}},
        "bad-key": {"type": "string"},
        "mixed": {"type": ["string", "integer"]},
    },
    "$defs": {"flag": {"type": "boolean"}},
}


def context(prefix: str = "") -> CompileContext:
    return CompileContext(ValuesContract(SCHEMA, prefix=prefix))


def test_valid_reference_returns_a_marker_for_the_full_path():
    assert context().value("a.s", str) == "<<values:a.s>>"


def test_non_string_scalars_are_marked_for_json_rendering():
    # Helm's `quote` prints large numbers in exponent notation ("1e+06"); `toJson` doesn't.
    assert context().value("a.n", int) == "<<values:a.n|json>>"


def test_local_refs_are_followed():
    assert context().value("a.ref", bool) == "<<values:a.ref|json>>"


def test_prefix_is_prepended_to_lookups_and_expressions():
    nested = {"type": "object", "required": ["ci"], "properties": {"ci": SCHEMA}}
    ctx = CompileContext(ValuesContract(nested, prefix="ci"))

    assert ctx.value("a.s", str) == "<<values:ci.a.s>>"


@pytest.mark.parametrize("path", ["a.missing", "missing", "loose.anything"])
def test_undeclared_paths_fail(path):
    # Undeclared keys are nonexistent even where additionalProperties allows them.
    with pytest.raises(CompileError, match="is not declared in the values schema"):
        context().value(path, str)


def test_optional_paths_fail():
    with pytest.raises(CompileError, match=r"'a\.optional' is optional .* only required values"):
        context().value("a.optional", str)


def test_wrong_type_fails():
    with pytest.raises(
        CompileError, match=r"'a\.n' is integer in the values schema, but is used as string"
    ):
        context().value("a.n", str)


def test_objects_cannot_be_used_as_scalars():
    with pytest.raises(CompileError, match=r"'obj' is object .* used as a scalar"):
        context().value("obj")


def test_scalars_mixing_string_with_other_types_fail():
    with pytest.raises(CompileError, match="'mixed' mixes string with other types"):
        context().value("mixed")


def test_nullable_values_fail():
    with pytest.raises(CompileError, match="'nullable' is nullable"):
        context().value("nullable", str)


def test_unsupported_schema_constructs_fail():
    with pytest.raises(CompileError, match="unsupported schema construct 'oneOf' at 'choice'"):
        context().value("choice")


def test_non_identifier_keys_fail():
    with pytest.raises(CompileError, match="'bad-key' must be an identifier"):
        context().value("bad-key", str)


def test_structured_expected_types_are_not_supported_yet():
    with pytest.raises(CompileError, match="only scalar values"):
        context().value("obj", dict)


def test_pipeline_placeholder_failure_names_pipeline_stage_block_and_path():
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="echo {{ values.notify.nope }}")])]
    )

    with pytest.raises(
        CompileError,
        match=r"pipeline 'p', stage 's', block 'shell': value 'notify\.nope' is not declared",
    ):
        compile_pipeline(pipeline, CONTEXT)


def test_block_reference_failure_names_the_block():
    empty = CompileContext(ValuesContract({"type": "object", "properties": {}}))
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])])

    with pytest.raises(CompileError, match=r"block 'shell': value 'images\.shell' is not declared"):
        compile_pipeline(pipeline, empty)


def test_placeholder_in_a_string_renders_a_helm_splice():
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="notify {{ values.notify.channel }}")])]
    )

    rendered = serialize(compile_pipeline(pipeline, CONTEXT))

    assert (
        '- "notify {{ .Values.notify.channel | quote | trimPrefix "\\"" | trimSuffix "\\"" }}"'
        in rendered
    )


def test_number_inside_a_string_renders_as_json():
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="retry {{ values.strings.retries }}")])]
    )

    rendered = serialize(compile_pipeline(pipeline, CONTEXT))

    assert '- "retry {{ .Values.strings.retries | toJson }}"' in rendered


def test_whole_number_field_renders_as_a_quoted_json_number():
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="{{ values.strings.retries }}")])]
    )

    rendered = serialize(compile_pipeline(pipeline, CONTEXT))

    assert "- {{ .Values.strings.retries | toJson | quote }}" in rendered


def test_whole_field_reference_renders_quoted_helm_value():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])])

    rendered = serialize(compile_pipeline(pipeline, CONTEXT))

    assert "image: {{ .Values.images.shell | quote }}" in rendered

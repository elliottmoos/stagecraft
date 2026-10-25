"""Value references: recorded with their types, checked for consistency, and rendered as Helm
expressions (DESIGN.md sections 8 and 16)."""

import pytest
from hera.workflows import Container

from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.errors import CompileError
from stagecraft.serialize import serialize
from stagecraft.spec import Action, Pipeline, Stage

from fixtures.actions import Shell


class Retries(Action):
    """Uses strings.retries as an integer."""

    script: str = "retry {}"

    def compile(self, ctx: CompileContext) -> Container:
        retries = ctx.value("strings.retries", int)
        return Container(image="busybox", args=[self.script.format(retries)])


def test_a_reference_returns_a_marker_and_is_recorded_with_its_type():
    ctx = CompileContext()

    assert ctx.value("a.s", str) == "<<values:a.s>>"
    assert ctx.references == {"a.s": str}


def test_non_string_scalars_are_marked_for_json_rendering():
    # Helm's `quote` prints large numbers in exponent notation ("1e+06"); `toJson` doesn't.
    ctx = CompileContext()

    assert ctx.value("a.n", int) == "<<values:a.n|json>>"
    assert ctx.value("a.f", bool) == "<<values:a.f|json>>"


def test_references_default_to_strings():
    ctx = CompileContext()
    ctx.value("a.s")

    assert ctx.references == {"a.s": str}


def test_the_same_path_can_be_referenced_again_with_the_same_type():
    ctx = CompileContext()
    ctx.value("a.n", int)
    ctx.value("a.n", int)

    assert ctx.references == {"a.n": int}


def test_one_path_cannot_have_two_types():
    ctx = CompileContext()
    ctx.value("a.n", int)

    with pytest.raises(
        CompileError, match=r"value 'a\.n' is used as str here but as int elsewhere"
    ):
        ctx.value("a.n", str)


@pytest.mark.parametrize(("first", "second"), [("a", "a.b"), ("a.b", "a")])
def test_a_path_cannot_be_both_a_value_and_an_object(first, second):
    ctx = CompileContext()
    ctx.value(first, str)

    with pytest.raises(CompileError, match="both a value and an object"):
        ctx.value(second, str)


@pytest.mark.parametrize("path", ["bad-key", "a..b", "a.1x", ""])
def test_keys_must_be_identifiers(path):
    with pytest.raises(CompileError, match="must be dotted identifiers"):
        CompileContext().value(path, str)


def test_the_stagecraft_key_is_reserved_for_framework_values():
    with pytest.raises(CompileError, match="'stagecraft' is reserved"):
        CompileContext().value("stagecraft.sources.image", str)


def test_structured_expected_types_are_not_supported_yet():
    with pytest.raises(CompileError, match="only scalar values"):
        CompileContext().value("obj", dict)


def test_placeholders_are_recorded_as_strings():
    ctx = CompileContext()
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="echo {{ values.notify.channel }}")])]
    )

    compile_pipeline(pipeline, ctx)

    assert ctx.references == {"images.shell": str, "notify.channel": str}


def test_conflicts_name_the_pipeline_stage_and_step():
    pipeline = Pipeline(
        name="p",
        stages=[Stage("s", [Retries(), Shell(script="echo {{ values.strings.retries }}")])],
    )

    with pytest.raises(
        CompileError,
        match=r"pipeline 'p', stage 's', step 'shell': value 'strings\.retries' is used as str",
    ):
        compile_pipeline(pipeline, CompileContext())


def test_placeholder_in_a_string_renders_a_helm_splice():
    pipeline = Pipeline(
        name="p", stages=[Stage("s", [Shell(script="notify {{ values.notify.channel }}")])]
    )

    rendered = serialize(compile_pipeline(pipeline, CompileContext()))

    assert (
        '- "notify {{ .Values.notify.channel | quote | trimPrefix "\\"" | trimSuffix "\\"" }}"'
        in rendered
    )


def test_number_inside_a_string_renders_as_json():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Retries()])])

    rendered = serialize(compile_pipeline(pipeline, CompileContext()))

    assert '- "retry {{ .Values.strings.retries | toJson }}"' in rendered


def test_whole_number_field_renders_as_a_quoted_json_number():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Retries(script="{}")])])

    rendered = serialize(compile_pipeline(pipeline, CompileContext()))

    assert "- {{ .Values.strings.retries | toJson | quote }}" in rendered


def test_whole_field_reference_renders_quoted_helm_value():
    pipeline = Pipeline(name="p", stages=[Stage("s", [Shell(script="true")])])

    rendered = serialize(compile_pipeline(pipeline, CompileContext()))

    assert "image: {{ .Values.images.shell | quote }}" in rendered

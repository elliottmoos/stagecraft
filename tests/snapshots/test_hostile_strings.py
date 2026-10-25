"""Strings that are hard to carry through YAML and Helm unchanged. Each one is put in an action's
script, a param default, and a param description; `helm template` must reproduce the compiled
manifest exactly (DESIGN.md section 8)."""

from stagecraft.compile import compile_pipeline
from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell
from fixtures.contract import CONTEXT
from support import build_chart, helm_template, with_fixture_values

HOSTILE = [
    # Argo expressions
    "{{workflow.name}}{{workflow.uid}}",
    "{{=sprig.trim(`  x  `)}}",
    '{{=workflow.name + "`" + "\\\\"}}',
    "{{{workflow.name}}}",
    '{{"{{"}}',
    # Text that Helm or Go templates would otherwise evaluate
    "{{ .Values.secret }}",
    "{{- .Values.x -}}",
    "docker inspect --format '{{.Id}}'",
    '{{ include "x" . }}{{ fail "boom" }}',
    # YAML syntax and implicit types
    'a: "b" # c \\ d',
    "- item",
    "? key",
    "&anchor *alias !tag %directive @at `tick`",
    "yes",
    "null",
    "~",
    "0x1F",
    "012",
    "1e3",
    "  leading and trailing spaces  ",
    "",
    # Multi-line, whitespace, and non-ASCII
    "line one\n  {{workflow.name}} indented\nline three\n",
    "trailing space \nnext",
    "tab\there",
    "crlf\r\nline",
    "héllo ✓ 你好 🚀",
    # Values next to Argo expressions
    "{{ values.notify.channel }}{{workflow.name}}",
    "multi\n{{ values.strings.tricky }}\n{{ values.strings.big }}",
]


def pipeline() -> Pipeline:
    def plain(text: str) -> str:
        # Params can't hold value references.
        return text.replace("{{ values.", "{{ workflow.")

    return Pipeline(
        name="hostile",
        params=[
            Param(f"p{i}", default=plain(text), description=plain(text))
            for i, text in enumerate(HOSTILE)
        ],
        stages=[
            Stage("only", [Step(f"s{i}", Shell(script=text)) for i, text in enumerate(HOSTILE)])
        ],
    )


def test_helm_reproduces_hostile_strings_exactly(tmp_path):
    hostile = pipeline()

    docs = helm_template(build_chart(tmp_path, [hostile]), tmp_path / "rendered.yaml")

    assert docs == [with_fixture_values(compile_pipeline(hostile, CONTEXT).to_dict())]

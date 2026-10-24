"""Rendered templates: snapshot, then prove they survive Helm, `argo lint`, and kubeconform."""

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from stagecraft.compile import compile_pipeline
from stagecraft.serialize import serialize

from fixtures.pipelines import escaping, handlers, hooks, smoke
from support import CRD_SCHEMAS, build_chart, helm_template, require_tool, run

PIPELINES = [smoke.pipeline, escaping.pipeline, handlers.pipeline, hooks.pipeline]


class YAMLSnapshot(SingleFileSnapshotExtension):
    file_extension = "yaml"
    _write_mode = WriteMode.TEXT


@pytest.fixture(params=PIPELINES, ids=lambda p: p.name)
def pipeline(request):
    return request.param


@pytest.fixture
def helm_output(pipeline, tmp_path):
    """The pipeline rendered through `helm template`: (path to output file, parsed docs)."""
    out = tmp_path / "rendered.yaml"
    docs = helm_template(build_chart(tmp_path, [pipeline]), out)
    return out, docs


def test_rendered_template_matches_snapshot(pipeline, snapshot):
    assert serialize(compile_pipeline(pipeline)) == snapshot(extension_class=YAMLSnapshot)


def test_helm_template_reproduces_the_compiled_manifest(pipeline, helm_output):
    _, docs = helm_output

    assert docs == [compile_pipeline(pipeline).to_dict()]


def test_argo_lint_accepts_helm_output(helm_output):
    out, _ = helm_output

    run(require_tool("argo"), "lint", "--offline", "--output", "simple", str(out))


def test_kubeconform_accepts_helm_output(helm_output):
    out, _ = helm_output

    run(
        require_tool("kubeconform"),
        "-strict",
        "-summary",
        "-schema-location",
        "default",
        "-schema-location",
        f"{CRD_SCHEMAS}/{{{{.ResourceKind}}}}_{{{{.ResourceAPIVersion}}}}.json",
        str(out),
    )

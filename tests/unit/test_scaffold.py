from importlib.metadata import version

from hera.workflows import DAG, Container, WorkflowTemplate
from typer.testing import CliRunner

from stagecraft.cli import app


def test_hera_is_pinned_version():
    # Bumping Hera is a deliberate change: update CLAUDE.md and re-check DESIGN.md section 13.
    assert version("hera") == "7.1.0"


def test_hera_builds_workflow_template_without_cluster():
    with WorkflowTemplate(name="smoke", entrypoint="main") as wt:
        hello = Container(name="hello", image="alpine:3.22", command=["true"])
        with DAG(name="main"):
            hello()

    manifest = wt.to_dict()

    assert manifest["kind"] == "WorkflowTemplate"
    assert manifest["apiVersion"] == "argoproj.io/v1alpha1"
    assert [t["name"] for t in manifest["spec"]["templates"]] == ["hello", "main"]


def test_cli_version():
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert "hera 7.1.0" in result.output

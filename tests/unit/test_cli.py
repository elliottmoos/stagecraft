from pathlib import Path

import pytest
from typer.testing import CliRunner

from stagecraft.cli import app
from stagecraft.compile import compile_pipeline
from stagecraft.graph import mermaid
from stagecraft.serialize import serialize

from fixtures.contract import CONTEXT
from fixtures.pipelines import smoke

TESTS = Path(__file__).parents[1]
# Relative to TESTS, the working directory of every test here.
VALUES = "--values-schema=fixtures/chart/values.schema.json"

BROKEN = """
from fixtures.actions import Shell
from stagecraft.spec import Pipeline, Stage

pipeline = Pipeline(name="broken", stages=[Stage("s", [Shell(script="{{ param.nope }}")])])
"""


@pytest.fixture(autouse=True)
def in_tests_dir(monkeypatch):
    # Pipeline files import their actions relative to the working directory, as in a pipelines repo.
    monkeypatch.chdir(TESTS)


def test_render_writes_one_template_per_pipeline(tmp_path):
    result = CliRunner().invoke(
        app, ["render", "fixtures/pipelines/smoke.py", VALUES, "--out", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["smoke-ci.yaml"]
    assert (tmp_path / "smoke-ci.yaml").read_text() == serialize(
        compile_pipeline(smoke.pipeline, CONTEXT)
    )


def test_render_accepts_a_directory(tmp_path):
    result = CliRunner().invoke(
        app, ["render", "fixtures/pipelines", VALUES, "--out", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "escaping.yaml",
        "handlers-ci.yaml",
        "hooks-ci.yaml",
        "smoke-ci.yaml",
        "status-ci.yaml",
        "triggers-ci.yaml",
    ]


def test_render_writes_nothing_when_any_pipeline_fails(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text(BROKEN)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        app, ["render", "fixtures/pipelines/smoke.py", str(broken), VALUES, "--out", str(out)]
    )

    assert result.exit_code == 1
    assert "pipeline 'broken', stage 's', step 'shell': undeclared param 'nope'" in result.output
    assert not out.exists()


def test_render_rejects_files_without_pipelines(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("x = 1\n")

    result = CliRunner().invoke(app, ["render", str(empty), VALUES, "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "no Pipeline found" in result.output


def test_render_reports_an_unreadable_values_schema(tmp_path):
    result = CliRunner().invoke(
        app,
        ["render", "fixtures/pipelines/smoke.py", "--values-schema=missing.json", "--out", "x"],
    )

    assert result.exit_code == 1
    assert "missing.json: cannot read values schema" in result.output


def test_check_reports_ok_without_writing(tmp_path):
    result = CliRunner().invoke(app, ["check", "fixtures/pipelines", VALUES])

    assert result.exit_code == 0, result.output
    assert result.output == (
        "ok: escaping, handlers-ci, hooks-ci, smoke-ci, status-ci, triggers-ci\n"
    )


def test_check_fails_like_render(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text(BROKEN)

    result = CliRunner().invoke(app, ["check", str(broken), VALUES])

    assert result.exit_code == 1
    assert "pipeline 'broken', stage 's', step 'shell': undeclared param 'nope'" in result.output


def test_graph_prints_a_mermaid_diagram():
    result = CliRunner().invoke(app, ["graph", "fixtures/pipelines/smoke.py"])

    assert result.exit_code == 0, result.output
    assert result.output == mermaid(smoke.pipeline)


def test_graph_needs_a_pipeline_name_when_paths_hold_several():
    result = CliRunner().invoke(app, ["graph", "fixtures/pipelines"])

    assert result.exit_code == 1
    assert "6 pipelines found (escaping, handlers-ci, " in result.output
    assert "choose one with --pipeline" in result.output


def test_graph_picks_a_pipeline_by_name():
    result = CliRunner().invoke(app, ["graph", "fixtures/pipelines", "--pipeline", "smoke-ci"])

    assert result.exit_code == 0, result.output
    assert result.output == mermaid(smoke.pipeline)


def test_graph_rejects_an_unknown_pipeline_name():
    result = CliRunner().invoke(app, ["graph", "fixtures/pipelines", "--pipeline", "nope"])

    assert result.exit_code == 1
    assert "no pipeline named 'nope'" in result.output


def test_contexts_lists_each_pipelines_status_contexts_in_order():
    result = CliRunner().invoke(app, ["contexts", "fixtures/pipelines"])

    assert result.exit_code == 0, result.output
    assert result.output == (
        "status-ci\tci/pipeline\n"
        "status-ci\tci/verify\n"
        "status-ci\tci/unit-tests\n"
        "status-ci\tci/deploy\n"
    )

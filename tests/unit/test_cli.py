from pathlib import Path

import pytest
from typer.testing import CliRunner

from stagecraft.cli import app
from stagecraft.compile import compile_pipeline
from stagecraft.serialize import serialize

from fixtures.pipelines import smoke

TESTS = Path(__file__).parents[1]

BROKEN = """
from fixtures.blocks import Shell
from stagecraft.spec import Pipeline, Stage

pipeline = Pipeline(name="broken", stages=[Stage("s", [Shell(script="{{ param.nope }}")])])
"""


@pytest.fixture(autouse=True)
def in_tests_dir(monkeypatch):
    # Pipeline files import their blocks relative to the working directory, as in a pipelines repo.
    monkeypatch.chdir(TESTS)


def test_render_writes_one_template_per_pipeline(tmp_path):
    result = CliRunner().invoke(
        app, ["render", "fixtures/pipelines/smoke.py", "--out", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["smoke-ci.yaml"]
    assert (tmp_path / "smoke-ci.yaml").read_text() == serialize(compile_pipeline(smoke.pipeline))


def test_render_accepts_a_directory(tmp_path):
    result = CliRunner().invoke(app, ["render", "fixtures/pipelines", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "escaping.yaml",
        "handlers-ci.yaml",
        "hooks-ci.yaml",
        "smoke-ci.yaml",
    ]


def test_render_writes_nothing_when_any_pipeline_fails(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text(BROKEN)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        app, ["render", "fixtures/pipelines/smoke.py", str(broken), "--out", str(out)]
    )

    assert result.exit_code == 1
    assert "pipeline 'broken', stage 's', block 'shell': undeclared param 'nope'" in result.output
    assert not out.exists()


def test_render_rejects_files_without_pipelines(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("x = 1\n")

    result = CliRunner().invoke(app, ["render", str(empty), "--out", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "no Pipeline found" in result.output

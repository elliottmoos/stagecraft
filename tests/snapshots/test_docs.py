"""The docs' examples must keep working as the API changes.

The guide in docs/ builds one project chapter by chapter. A code fence tagged `file=<path>` is a
complete file of that project; later chapters replace earlier versions. After each chapter the
project is rebuilt from the files so far: test files are run, and every pipeline is compiled
against the docs' values schema. The finished project is rendered through `helm template`,
`argo lint`, and kubeconform, and the guide's diagram must match `stagecraft graph`.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from stagecraft.compile import compile_pipeline, compile_triggers
from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.graph import mermaid
from stagecraft.loader import load_pipelines
from stagecraft.serialize import serialize

from support import CRD_SCHEMAS, require_tool, run

DOCS = Path(__file__).parents[2] / "docs"
CHAPTERS = sorted(DOCS.glob("[0-9][0-9]-*.md"))
FENCE = re.compile(r"^```(\w+) file=(\S+)\n(.*?)^```", re.S | re.M)


@pytest.fixture(autouse=True)
def forget_project_modules(tmp_path):
    """Each chapter's project has its own `actions` module: forget the modules a test loaded
    from its project."""
    yield
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if file is not None and Path(file).is_relative_to(tmp_path):
            del sys.modules[name]


def files_through(chapter: Path) -> dict[str, str]:
    """The project's files as of the end of `chapter`."""
    files: dict[str, str] = {}
    for path in CHAPTERS[: CHAPTERS.index(chapter) + 1]:
        for _, name, content in FENCE.findall(path.read_text()):
            files[name] = content
    return files


def build(files: dict[str, str], root: Path, monkeypatch) -> list:
    """Write the project, run its tests, and return its compiled pipelines."""
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root))
    for test_file in sorted(root.glob("tests/test_*.py")):
        _run_tests(test_file)
    if not (root / "pipelines").is_dir():
        return []
    ctx = CompileContext(ValuesContract.load(root / "chart" / "values.schema.json"))
    return [
        (p, compile_pipeline(p, ctx), compile_triggers(p, ctx))
        for p in load_pipelines([root / "pipelines"])
    ]


def _run_tests(test_file: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"_docs_{test_file.stem}", test_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, test in vars(module).items():
        if name.startswith("test_") and callable(test):
            test()


@pytest.mark.parametrize("chapter", CHAPTERS, ids=lambda p: p.stem)
def test_chapter_examples_compile(chapter, tmp_path, monkeypatch):
    files = files_through(chapter)

    compiled = build(files, tmp_path / "project", monkeypatch)

    for _, template, crons in compiled:
        assert serialize(template, *crons)


def test_finished_project_renders_lints_and_validates(tmp_path, monkeypatch):
    root = tmp_path / "project"
    compiled = build(files_through(CHAPTERS[-1]), root, monkeypatch)
    chart = root / "chart"
    (chart / "templates").mkdir(exist_ok=True)
    for pipeline, template, crons in compiled:
        (chart / "templates" / f"{pipeline.name}.yaml").write_text(serialize(template, *crons))

    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(run(require_tool("helm"), "template", "ci", str(chart)))
    run(require_tool("argo"), "lint", "--offline", "--output", "simple", str(rendered))
    run(
        require_tool("kubeconform"),
        "-strict",
        "-schema-location",
        "default",
        "-schema-location",
        f"{CRD_SCHEMAS}/{{{{.ResourceKind}}}}_{{{{.ResourceAPIVersion}}}}.json",
        str(rendered),
    )


def test_guides_diagram_matches_stagecraft_graph(tmp_path, monkeypatch):
    compiled = build(files_through(CHAPTERS[-1]), tmp_path / "project", monkeypatch)
    service_ci = next(p for p, _, _ in compiled if p.name == "service-ci")
    [diagram] = re.findall(r"^```mermaid\n(.*?)^```", CHAPTERS[-1].read_text(), re.S | re.M)

    assert diagram == mermaid(service_ci)


def test_every_chapter_is_linked_from_the_index():
    index = (DOCS / "README.md").read_text()

    assert [c.name for c in CHAPTERS if f"({c.name})" not in index] == []


def test_chart_files_exist_for_rendering():
    # Guard against a chapter dropping the chart the rest of the guide renders into.
    assert {"chart/Chart.yaml", "chart/values.yaml", "chart/values.schema.json"} <= set(
        files_through(CHAPTERS[-1])
    )

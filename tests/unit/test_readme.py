"""The README's examples must keep compiling as the API changes."""

import re
from pathlib import Path

from stagecraft.chart import render_chart
from stagecraft.loader import load_pipelines

README = Path(__file__).parents[2] / "README.md"


def test_readme_examples_compile(tmp_path, monkeypatch):
    # The first two examples are the actions module and a pipeline file.
    actions, pipeline, *_ = re.findall(r"```python\n(.*?)```", README.read_text(), re.S)
    (tmp_path / "actions.py").write_text(actions)
    (tmp_path / "service_ci.py").write_text(pipeline)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    [loaded] = load_pipelines([tmp_path / "service_ci.py"])

    assert loaded.name == "service-ci"
    assert "templates/service-ci.yaml" in render_chart([loaded])

"""The README's examples must keep compiling as the API changes."""

import re
from pathlib import Path

from stagecraft.compile import compile_pipeline, compile_triggers
from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.loader import load_pipelines
from stagecraft.serialize import serialize

README = Path(__file__).parents[2] / "README.md"


def string(*path: str) -> dict:
    """A schema requiring a string at each dotted path."""
    schema: dict = {"type": "object", "required": [], "properties": {}}
    for dotted in path:
        node = schema
        *parents, leaf = dotted.split(".")
        for key in parents:
            if key not in node["properties"]:
                node["required"].append(key)
                node["properties"][key] = {"type": "object", "required": [], "properties": {}}
            node = node["properties"][key]
        node["required"].append(leaf)
        node["properties"][leaf] = {"type": "string"}
    return schema


def test_readme_examples_compile(tmp_path, monkeypatch):
    # The first two examples are the blocks module and a pipeline file.
    blocks, pipeline, *_ = re.findall(r"```python\n(.*?)```", README.read_text(), re.S)
    (tmp_path / "blocks.py").write_text(blocks)
    (tmp_path / "service_ci.py").write_text(pipeline)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    schema = string("images.shell", "images.curl", "github.tokenSecret", "notify.channel")

    [loaded] = load_pipelines([tmp_path / "service_ci.py"])

    ctx = CompileContext(ValuesContract(schema))
    assert loaded.name == "service-ci"
    assert serialize(compile_pipeline(loaded, ctx), *compile_triggers(loaded, ctx))

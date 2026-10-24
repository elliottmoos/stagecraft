"""Loading pipeline definition files: the CLI's I/O boundary. The compiler never imports it."""

import importlib.util
import sys
from pathlib import Path

from pydantic import ValidationError

from stagecraft.errors import LoadError
from stagecraft.spec import Pipeline


def load_pipelines(paths: list[Path]) -> list[Pipeline]:
    """Import each pipeline file and collect its module-level Pipeline objects.

    A directory contributes its *.py files (not recursively, skipping names starting with '_').
    The working directory is put on sys.path so pipeline files can import the project's own
    block modules, as they would in a pipelines repo.
    """
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    pipelines: list[Pipeline] = []
    for index, file in enumerate(_pipeline_files(paths)):
        found = _load_file(file, module_name=f"_stagecraft_pipelines_{index}_{file.stem}")
        if not found:
            raise LoadError(f"{file}: no Pipeline found at module level")
        pipelines += found

    names = [p.name for p in pipelines]
    if dupes := {n for n in names if names.count(n) > 1}:
        raise LoadError(f"pipeline names must be unique, found duplicates: {sorted(dupes)}")
    return pipelines


def _pipeline_files(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.glob("*.py") if not p.name.startswith("_"))
        elif path.is_file():
            files.append(path)
        else:
            raise LoadError(f"{path}: no such file or directory")
    return files


def _load_file(file: Path, module_name: str) -> list[Pipeline]:
    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        raise LoadError(f"{file}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # Pydantic resolves annotations through sys.modules.
    try:
        spec.loader.exec_module(module)
    except ValidationError as e:
        raise LoadError(f"{file}: {e}") from e
    return [value for value in vars(module).values() if isinstance(value, Pipeline)]

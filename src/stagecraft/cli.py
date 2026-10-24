"""Command-line entry point. Commands are added slice by slice (see DESIGN.md section 11)."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from stagecraft import __version__
from stagecraft.compile import compile_pipeline
from stagecraft.errors import StagecraftError
from stagecraft.loader import load_pipelines
from stagecraft.serialize import serialize

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Compile stagecraft pipelines to Helm-compatible Argo Workflows templates."""


@app.command("version")
def show_version() -> None:
    """Print the stagecraft version and the pinned Hera version."""
    typer.echo(f"stagecraft {__version__} (hera {version('hera')})")


@app.command()
def render(
    paths: Annotated[list[Path], typer.Argument(help="Pipeline files or directories of them.")],
    out: Annotated[Path, typer.Option(help="Directory to write templates into.")],
) -> None:
    """Compile pipelines and write one template file per pipeline.

    Nothing is written unless every pipeline compiles.
    """
    try:
        rendered = {f"{p.name}.yaml": serialize(compile_pipeline(p)) for p in load_pipelines(paths)}
    except StagecraftError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    out.mkdir(parents=True, exist_ok=True)
    for filename, text in rendered.items():
        (out / filename).write_text(text)
        typer.echo(f"wrote {out / filename}")

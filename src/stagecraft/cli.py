"""Command-line entry point. Commands are added slice by slice (see DESIGN.md section 11)."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from stagecraft import __version__
from stagecraft.compile import compile_pipeline
from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
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
    values_schema: Annotated[
        Path, typer.Option(help="The chart's values.schema.json (the values contract).")
    ],
    values_prefix: Annotated[
        str, typer.Option(help="Dotted key the chart nests stagecraft values under, if any.")
    ] = "",
) -> None:
    """Compile pipelines and write one template file per pipeline.

    Every Helm value reference is checked against the values schema. Nothing is written unless
    every pipeline compiles.
    """
    try:
        ctx = CompileContext(ValuesContract.load(values_schema, values_prefix))
        rendered = {
            f"{p.name}.yaml": serialize(compile_pipeline(p, ctx)) for p in load_pipelines(paths)
        }
    except StagecraftError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    out.mkdir(parents=True, exist_ok=True)
    for filename, text in rendered.items():
        (out / filename).write_text(text)
        typer.echo(f"wrote {out / filename}")

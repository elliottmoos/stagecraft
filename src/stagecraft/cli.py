"""Command-line entry point. Commands are added slice by slice (see DESIGN.md section 11)."""

from importlib.metadata import version

import typer

from stagecraft import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Compile stagecraft pipelines to Helm-compatible Argo Workflows templates."""


@app.command("version")
def show_version() -> None:
    """Print the stagecraft version and the pinned Hera version."""
    typer.echo(f"stagecraft {__version__} (hera {version('hera')})")

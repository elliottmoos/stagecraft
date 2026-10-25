"""Command-line entry point. The CLI is the only layer that reads files and prints."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from stagecraft import __version__
from stagecraft.compile import compile_pipeline, compile_triggers
from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.errors import StagecraftError
from stagecraft.graph import mermaid
from stagecraft.loader import load_pipelines
from stagecraft.serialize import serialize
from stagecraft.spec import Pipeline

app = typer.Typer(no_args_is_help=True, add_completion=False)

Paths = Annotated[list[Path], typer.Argument(help="Pipeline files or directories of them.")]
ValuesSchema = Annotated[
    Path, typer.Option(help="The chart's values.schema.json (the values contract).")
]
ValuesPrefix = Annotated[
    str, typer.Option(help="Dotted key the chart nests stagecraft values under, if any.")
]


@app.callback()
def main() -> None:
    """Compile stagecraft pipelines to Helm-compatible Argo Workflows templates."""


@app.command("version")
def show_version() -> None:
    """Print the stagecraft version and the pinned Hera version."""
    typer.echo(f"stagecraft {__version__} (hera {version('hera')})")


@app.command()
def render(
    paths: Paths,
    out: Annotated[Path, typer.Option(help="Directory to write templates into.")],
    values_schema: ValuesSchema,
    values_prefix: ValuesPrefix = "",
) -> None:
    """Compile pipelines and write one template file per pipeline.

    A pipeline's file holds its WorkflowTemplate and a CronWorkflow per trigger. Every Helm value
    reference is checked against the values schema. Nothing is written unless
    every pipeline compiles.
    """
    with _errors():
        rendered = _render(paths, values_schema, values_prefix)

    out.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (out / f"{name}.yaml").write_text(text)
        typer.echo(f"wrote {out / f'{name}.yaml'}")


@app.command()
def check(paths: Paths, values_schema: ValuesSchema, values_prefix: ValuesPrefix = "") -> None:
    """Compile and validate pipelines as render does, without writing anything."""
    with _errors():
        rendered = _render(paths, values_schema, values_prefix)
    typer.echo(f"ok: {', '.join(rendered)}")


@app.command()
def graph(
    paths: Paths,
    pipeline: Annotated[
        str | None, typer.Option(help="Pipeline to draw, when the paths hold several.")
    ] = None,
) -> None:
    """Print a Mermaid diagram of a pipeline: its stages, steps, conditions, handlers, and
    triggers."""
    with _errors():
        typer.echo(mermaid(_pick(load_pipelines(paths), pipeline)), nl=False)


@app.command()
def contexts(paths: Paths) -> None:
    """List commit-status contexts: the checks a branch protection rule can require.

    Prints one "<pipeline><TAB><context>" line per context, in pipeline order.
    """
    with _errors():
        pipelines = load_pipelines(paths)
    for p in pipelines:
        for context in p.status_contexts:
            typer.echo(f"{p.name}\t{context}")


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except StagecraftError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e


def _render(paths: list[Path], values_schema: Path, values_prefix: str) -> dict[str, str]:
    """Each pipeline's rendered file (its WorkflowTemplate, then a CronWorkflow per trigger), by
    pipeline name."""
    ctx = CompileContext(ValuesContract.load(values_schema, values_prefix))
    return {
        p.name: serialize(compile_pipeline(p, ctx), *compile_triggers(p, ctx))
        for p in load_pipelines(paths)
    }


def _pick(pipelines: list[Pipeline], name: str | None) -> Pipeline:
    names = [p.name for p in pipelines]
    if name is not None:
        if name not in names:
            raise StagecraftError(f"no pipeline named {name!r}; found {', '.join(names)}")
        return pipelines[names.index(name)]
    if len(pipelines) > 1:
        raise StagecraftError(
            f"{len(pipelines)} pipelines found ({', '.join(names)}); choose one with --pipeline"
        )
    return pipelines[0]

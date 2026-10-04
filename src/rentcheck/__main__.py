"""Typer application entry point for the ``rentcheck`` CLI."""

from __future__ import annotations

import typer

from rentcheck import __version__
from rentcheck.cli import ablate, mine, report, scan

app = typer.Typer(
    name="rentcheck",
    help="A/B test agent config sections, skills, and MCP servers.",
    no_args_is_help=True,
    add_completion=False,
)

app.command()(scan.scan)
app.command()(mine.mine)
app.command()(ablate.ablate)
app.command()(report.report)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"rentcheck {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """rentcheck command-line interface."""


if __name__ == "__main__":
    app()

"""``rentcheck mine`` — turn real past commits into reproducible tasks.

Presentation only: parses options, drives a progress bar, and renders the
:class:`MineResult` the mining service returns as a Rich table or JSON.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from rentcheck.core.config import load_settings
from rentcheck.core.errors import RentcheckError
from rentcheck.schemas.mine_result import MineResult
from rentcheck.services.mining_service import mine_repo

_console = Console()
_err_console = Console(stderr=True)


def mine(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the git repository to mine.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    max_tasks: int = typer.Option(20, "--max-tasks", min=1, help="Stop after this many tasks."),
    max_diff_lines: int = typer.Option(
        200, "--max-diff-lines", min=1, help="Skip commits whose source diff is larger."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only mine commits since this git date (e.g. 2024-01-01)."
    ),
    force: bool = typer.Option(False, "--force", help="Re-create tasks that already exist."),
    json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable JSON result."),
) -> None:
    """Mine commits that change both tests and source into replayable tasks."""
    try:
        settings = load_settings(path)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=_err_console,
            transient=True,
        ) as progress:
            bar = progress.add_task("Scanning commits…", total=None)

            def on_scan(_commit: object) -> None:
                progress.advance(bar, 0)
                progress.update(bar, description="Scanning commits…")

            result = mine_repo(
                path,
                max_tasks=max_tasks,
                max_diff_lines=max_diff_lines,
                since=since,
                force=force,
                settings=settings,
                on_scan=on_scan,
            )
    except RentcheckError as exc:
        _err_console.print(f"[red]error:[/] {exc.message}")
        if exc.hint:
            _err_console.print(f"[dim]hint: {exc.hint}[/]")
        raise typer.Exit(code=1) from exc

    if json_output:
        _console.print_json(result.model_dump_json())
        return
    _render(result)


def _render(result: MineResult) -> None:
    """Render the created-tasks table and a one-line summary."""
    if result.created:
        _console.print(_table(result))
    _console.print(f"\n[dim]Tasks written under {result.tasks_dir}[/]")
    _console.print(_summary(result))


def _table(result: MineResult) -> Table:
    """Build the per-task breakdown table."""
    table = Table(title="Mined tasks", title_style="bold")
    table.add_column("Task id", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Diff lines", justify="right")
    table.add_column("Test command")
    for task in result.created:
        table.add_row(
            task.id,
            str(task.files_changed),
            str(task.diff_lines),
            task.test_command,
        )
    return table


def _summary(result: MineResult) -> str:
    """Build the 'Scanned N, created M, skipped K (reasons: …)' line."""
    skipped_total = sum(result.skipped.values())
    reasons = ", ".join(f"{reason}: {n}" for reason, n in sorted(result.skipped.items()))
    tail = f" (reasons: {reasons})" if reasons else ""
    return (
        f"Scanned {result.scanned_commits} commits, "
        f"created {len(result.created)} tasks, "
        f"skipped {skipped_total}{tail}."
    )

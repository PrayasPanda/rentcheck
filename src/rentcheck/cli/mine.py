"""``rentcheck mine`` — turn real past commits into reproducible tasks.

Presentation only: parses options, drives a progress bar, and renders the
:class:`MineResult` the mining service returns as a Rich table or JSON.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from rentcheck.core.config import Settings, load_settings
from rentcheck.core.errors import RentcheckError
from rentcheck.schemas.mine_result import MineResult
from rentcheck.schemas.validation import TaskStatus, ValidationResult
from rentcheck.services.mining_service import mine_repo
from rentcheck.services.validation_service import validate_tasks

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
    validate: bool = typer.Option(
        True, "--validate/--no-validate", help="Validate mined tasks (red/green) after mining."
    ),
    validate_only: bool = typer.Option(
        False, "--validate-only", help="Skip mining; only validate already-mined tasks."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable JSON result."),
) -> None:
    """Mine commits that change both tests and source into replayable tasks."""
    try:
        settings = load_settings(path)
        validations: list[ValidationResult] | None = None
        if validate_only:
            validations = _run_validation(path, settings, force=force, task_ids=None)
            if json_output:
                _print_validation_json(validations)
            else:
                _render_validation(validations)
            return

        result = _run_mining(path, settings, max_tasks, max_diff_lines, since, force)
        if validate:
            ids = [t.id for t in result.created]
            validations = _run_validation(path, settings, force=force, task_ids=ids)
    except RentcheckError as exc:
        _err_console.print(f"[red]error:[/] {exc.message}")
        if exc.hint:
            _err_console.print(f"[dim]hint: {exc.hint}[/]")
        raise typer.Exit(code=1) from exc

    if json_output:
        _console.print_json(result.model_dump_json())
        return
    _render(result)
    if validations is not None:
        _render_validation(validations)


def _run_mining(
    path: Path,
    settings: Settings,
    max_tasks: int,
    max_diff_lines: int,
    since: str | None,
    force: bool,
) -> MineResult:
    """Drive mining with a scanning spinner and return the result."""
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

        return mine_repo(
            path,
            max_tasks=max_tasks,
            max_diff_lines=max_diff_lines,
            since=since,
            force=force,
            settings=settings,
            on_scan=on_scan,
        )


def _run_validation(
    path: Path,
    settings: Settings,
    *,
    force: bool,
    task_ids: list[str] | None,
) -> list[ValidationResult]:
    """Drive validation with a live spinner and return per-task results."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_err_console,
        transient=True,
    ) as progress:
        bar = progress.add_task("Validating tasks…", total=None)

        def on_status(task_id: str, status: str) -> None:
            progress.update(bar, description=f"Validating {task_id} → {status}")

        return asyncio.run(
            validate_tasks(
                path,
                task_ids=task_ids,
                force=force,
                settings=settings,
                on_status=on_status,
            )
        )


_STATUS_STYLE = {
    TaskStatus.VALID: "green",
    TaskStatus.FLAKY: "yellow",
    TaskStatus.DROPPED: "red",
    TaskStatus.PENDING: "dim",
}


def _render_validation(results: list[ValidationResult]) -> None:
    """Render the validation table and summary, warning on too few valid tasks."""
    if results:
        _console.print(_validation_table(results))
    _console.print(_validation_summary(results))
    valid = sum(1 for r in results if r.status is TaskStatus.VALID)
    if valid < 5:
        _console.print(
            f"[yellow]warning:[/] only {valid} valid task(s); "
            "fewer than 5 makes later A/B results inconclusive."
        )


def _validation_table(results: list[ValidationResult]) -> Table:
    """Build the per-task validation breakdown table."""
    table = Table(title="Validation", title_style="bold")
    table.add_column("Task id", overflow="fold")
    table.add_column("Status")
    table.add_column("Target tests", justify="right")
    table.add_column("Drop reason")
    for r in results:
        style = _STATUS_STYLE.get(r.status, "")
        table.add_row(
            r.task_id,
            f"[{style}]{r.status.value}[/]" if style else r.status.value,
            str(len(r.target_tests)),
            r.drop_reason.value if r.drop_reason else "",
        )
    return table


def _validation_summary(results: list[ValidationResult]) -> str:
    """Build the 'Mined N, valid M, flaky F, dropped D (top reasons: …)' line."""
    total = len(results)
    valid = sum(1 for r in results if r.status is TaskStatus.VALID)
    flaky = sum(1 for r in results if r.status is TaskStatus.FLAKY)
    dropped = sum(1 for r in results if r.status is TaskStatus.DROPPED)
    reasons: dict[str, int] = {}
    for r in results:
        if r.drop_reason is not None:
            reasons[r.drop_reason.value] = reasons.get(r.drop_reason.value, 0) + 1
    top = ", ".join(
        f"{name}: {n}" for name, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
    )
    tail = f" (top reasons: {top})" if top else ""
    return f"Mined {total}, valid {valid}, flaky {flaky}, dropped {dropped}{tail}."


def _print_validation_json(results: list[ValidationResult]) -> None:
    """Emit the validation results as a JSON array."""
    import json

    payload = [r.model_dump(mode="json") for r in results]
    _console.print_json(json.dumps(payload))


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

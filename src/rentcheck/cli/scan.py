"""``rentcheck scan`` — inventory agent context and estimate its monthly cost.

This module is presentation only: it parses CLI options, delegates to the scan
service, and renders the result as a Rich table or JSON. No scan logic lives
here.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rentcheck.core.config import load_settings
from rentcheck.core.errors import RentcheckError
from rentcheck.schemas.scan_result import ScanResult
from rentcheck.services.scan_service import scan_repo

_console = Console()
_err_console = Console(stderr=True)


def scan(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the repository to scan.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable JSON result."),
    requests_per_day: int | None = typer.Option(
        None,
        "--requests-per-day",
        min=0,
        help="Assumed agent requests per day for the cost estimate.",
    ),
    price_per_mtok: float | None = typer.Option(
        None,
        "--price-per-mtok",
        min=0.0,
        help="USD price per million input tokens for the cost estimate.",
    ),
) -> None:
    """Inventory AGENTS.md/CLAUDE.md sections, skills, and MCP servers."""
    try:
        settings = load_settings(path)
        if requests_per_day is not None:
            settings = settings.model_copy(update={"requests_per_day": requests_per_day})
        if price_per_mtok is not None:
            settings = settings.model_copy(update={"price_per_mtok": price_per_mtok})
        result = scan_repo(path, settings=settings)
    except RentcheckError as exc:
        _err_console.print(f"[red]error:[/] {exc.message}")
        if exc.hint:
            _err_console.print(f"[dim]hint: {exc.hint}[/]")
        raise typer.Exit(code=1) from exc

    if json_output:
        _console.print_json(result.model_dump_json())
        return
    _render(result)


def _render(result: ScanResult) -> None:
    """Render a scan result as a headline, table, and highlights."""
    _console.print(_table(result))
    _console.print()
    _render_headline(result)
    _render_largest(result)
    _render_stale(result)
    _render_warnings(result)
    _console.print(
        f"\n[dim]Token counts are approximate ({result.tokenizer}); cost figures are estimates.[/]"
    )


def _table(result: ScanResult) -> Table:
    """Build the per-item breakdown table."""
    table = Table(title="Agent context inventory", title_style="bold")
    table.add_column("Item", overflow="fold")
    table.add_column("Kind")
    table.add_column("Tokens", justify="right")
    table.add_column("% of total", justify="right")
    table.add_column("Stale refs", justify="right")

    total = result.total_tokens or 1
    for scanned in sorted(result.items, key=lambda s: s.item.token_count, reverse=True):
        item = scanned.item
        pct = item.token_count / total * 100
        stale = len(scanned.stale_refs)
        table.add_row(
            item.id,
            item.kind,
            f"{item.token_count:,}",
            f"{pct:.1f}%",
            f"[yellow]{stale}[/]" if stale else "0",
        )
    return table


def _render_headline(result: ScanResult) -> None:
    """Print the one-line cost headline."""
    cost = result.cost
    _console.print(
        f"[bold]Your agent reads {result.total_tokens:,} tokens of context on "
        f"every request[/] "
        f"([green]~${cost.monthly_usd:,.2f}/month[/] at "
        f"{cost.requests_per_day:,} requests/day)."
    )


def _render_largest(result: ScanResult) -> None:
    """Print the three largest items by token count."""
    largest = sorted(result.items, key=lambda s: s.item.token_count, reverse=True)[:3]
    if not largest:
        return
    _console.print("\n[bold]Largest items[/]")
    for scanned in largest:
        item = scanned.item
        _console.print(f"  {item.token_count:>7,}  {item.id}")


def _render_stale(result: ScanResult) -> None:
    """List stale references grouped by the item they appear in."""
    if not result.stale_refs:
        return
    _console.print(f"\n[bold yellow]Stale references ({len(result.stale_refs)})[/]")
    by_item: dict[str, list[str]] = {}
    for ref in result.stale_refs:
        by_item.setdefault(ref.item_id, []).append(f"{ref.reference} ({ref.kind})")
    for item_id, refs in by_item.items():
        _console.print(f"  [yellow]{item_id}[/]: {', '.join(refs)}")


def _render_warnings(result: ScanResult) -> None:
    """Print any non-fatal warnings collected during the scan."""
    if not result.warnings:
        return
    _console.print("\n[bold]Warnings[/]")
    for warning in result.warnings:
        _console.print(f"  [dim]! {warning}[/]")

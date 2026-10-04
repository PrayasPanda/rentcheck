"""CLI command modules, one per subcommand."""

from __future__ import annotations

from rich.console import Console

_console = Console()


def not_implemented(name: str) -> None:
    """Print a uniform 'not implemented yet' notice for a stub command."""
    _console.print(f"[yellow]rentcheck {name}[/]: [dim]not implemented yet[/]")

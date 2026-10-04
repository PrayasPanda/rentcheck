"""Logging setup using rich for readable console output."""

from __future__ import annotations

import logging

from rich.logging import RichHandler


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging with a rich handler (idempotent)."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)

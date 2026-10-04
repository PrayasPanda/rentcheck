"""Exception hierarchy for rentcheck.

Every error carries a user-friendly ``message`` and an optional ``hint`` that
suggests how to fix the problem. The CLI renders both.
"""

from __future__ import annotations


class RentcheckError(Exception):
    """Base class for all rentcheck errors."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\nhint: {self.hint}"
        return self.message


class ConfigError(RentcheckError):
    """Raised when configuration is missing or invalid."""


class MiningError(RentcheckError):
    """Raised when mining historical tasks from a repository fails."""


class AgentRunError(RentcheckError):
    """Raised when a coding agent fails to run or produces no usable result."""


class BudgetExceeded(RentcheckError):
    """Raised when a run would exceed the configured USD budget."""

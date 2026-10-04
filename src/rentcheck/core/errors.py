"""Exception hierarchy for rentcheck."""

from __future__ import annotations


class RentcheckError(Exception):
    """Base class for all rentcheck errors."""


class ConfigError(RentcheckError):
    """Raised when configuration is missing or invalid."""


class RepositoryError(RentcheckError):
    """Raised when a repository/data-store operation fails."""

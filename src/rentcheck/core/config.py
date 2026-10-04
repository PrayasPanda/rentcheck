"""Application settings, loaded from the environment with a ``RENTCHECK_`` prefix."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for rentcheck.

    Values are read from environment variables (``RENTCHECK_*``) or a ``.env``
    file. Extend this as commands gain real behavior.
    """

    model_config = SettingsConfigDict(env_prefix="RENTCHECK_", env_file=".env")

    repo_path: str = "."
    results_db: str = "rentcheck.db"


def get_settings() -> Settings:
    """Return the application settings."""
    return Settings()

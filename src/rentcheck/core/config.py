"""Runtime configuration for rentcheck.

Settings come from three layers, highest priority first:

1. Environment variables prefixed ``RENTCHECK_`` (e.g. ``RENTCHECK_BUDGET_USD``).
2. A ``rentcheck.toml`` file in the target repository.
3. The field defaults defined below.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from rentcheck.core.errors import ConfigError


class Settings(BaseSettings):
    """Validated runtime configuration for rentcheck."""

    model_config = SettingsConfigDict(
        env_prefix="RENTCHECK_",
        extra="ignore",
    )

    default_agent: str = "aider"
    default_model: str = "ollama/qwen3-coder"
    budget_usd: float = Field(default=0.0, ge=0.0, description="0.0 means local models only.")
    trials_per_variant: int = Field(default=3, ge=1)
    timeout_seconds: int = Field(default=600, ge=1)
    max_concurrency: int = Field(default=2, ge=1)
    workdir: Path = Path(".rentcheck")
    ollama_base_url: str = "http://localhost:11434"

    # Cost-estimate inputs for ``rentcheck scan``.
    requests_per_day: int = Field(
        default=200,
        ge=0,
        description="Assumed agent requests per day for the cost estimate.",
    )
    price_per_mtok: float = Field(
        default=3.0,
        ge=0.0,
        description="USD price per million input tokens for the cost estimate.",
    )
    days_per_month: int = Field(
        default=30,
        ge=1,
        description="Days per month used to project the monthly cost estimate.",
    )

    # Mining inputs for ``rentcheck mine``.
    test_command: str | None = Field(
        default=None,
        description="Override the auto-detected test command for mined tasks.",
    )
    test_patterns: tuple[str, ...] = Field(
        default=(
            "tests/",
            "test/",
            "test_*.py",
            "*_test.py",
            "*.test.ts",
            "*.test.js",
            "*.spec.ts",
            "*.spec.js",
            "*_test.go",
            "tests.rs",
        ),
        description="Glob/prefix patterns that mark a changed path as a test file.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: init args > env > rentcheck.toml > defaults.
        toml_path = cls.model_config.get("toml_file")
        toml_source = TomlConfigSettingsSource(settings_cls, toml_file=toml_path)
        return (init_settings, env_settings, toml_source)


def load_settings(repo_path: Path) -> Settings:
    """Load settings for ``repo_path``, applying env > toml > defaults.

    Raises:
        ConfigError: if any value fails validation.
    """
    toml_file = repo_path / "rentcheck.toml"

    class _Settings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="RENTCHECK_",
            extra="ignore",
            toml_file=toml_file if toml_file.is_file() else None,
        )

    try:
        return _Settings()
    except ValidationError as exc:
        raise ConfigError(
            _format_validation_error(exc),
            hint="Check environment variables (RENTCHECK_*) and rentcheck.toml.",
        ) from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid rentcheck configuration:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


__all__: list[str] = ["Settings", "load_settings"]


def _demo() -> None:
    """Self-check: precedence and validation. Run with ``python -m rentcheck.core.config``."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "rentcheck.toml").write_text('budget_usd = 5.0\ndefault_agent = "claude"\n')

        s = load_settings(repo)
        assert s.budget_usd == 5.0, s.budget_usd  # from toml
        assert s.default_agent == "claude", s.default_agent

        os.environ["RENTCHECK_BUDGET_USD"] = "10.0"
        try:
            s = load_settings(repo)
            assert s.budget_usd == 10.0, s.budget_usd  # env overrides toml

            os.environ["RENTCHECK_TRIALS_PER_VARIANT"] = "0"
            try:
                load_settings(repo)
            except ConfigError:
                pass
            else:
                raise AssertionError("expected ConfigError for trials_per_variant=0")
        finally:
            os.environ.pop("RENTCHECK_BUDGET_USD", None)
            os.environ.pop("RENTCHECK_TRIALS_PER_VARIANT", None)

    print("config self-check passed")


if __name__ == "__main__":
    _demo()

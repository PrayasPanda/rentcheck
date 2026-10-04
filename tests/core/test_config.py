"""Tests for settings loading: defaults, toml, env precedence, and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rentcheck.core.config import load_settings
from rentcheck.core.errors import ConfigError


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("RENTCHECK_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    s = load_settings(tmp_path)
    assert s.default_agent == "aider"
    assert s.default_model == "ollama/qwen3-coder"
    assert s.budget_usd == 0.0
    assert s.trials_per_variant == 3
    assert s.timeout_seconds == 600
    assert s.max_concurrency == 2
    assert s.workdir == Path(".rentcheck")
    assert s.ollama_base_url == "http://localhost:11434"


def test_toml_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "rentcheck.toml").write_text(
        'default_agent = "claude"\nbudget_usd = 2.5\ntrials_per_variant = 5\n'
    )
    s = load_settings(tmp_path)
    assert s.default_agent == "claude"
    assert s.budget_usd == 2.5
    assert s.trials_per_variant == 5
    # Untouched fields keep defaults.
    assert s.timeout_seconds == 600


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "rentcheck.toml").write_text("budget_usd = 2.5\n")
    monkeypatch.setenv("RENTCHECK_BUDGET_USD", "9.0")
    s = load_settings(tmp_path)
    assert s.budget_usd == 9.0


@pytest.mark.parametrize(
    ("env_key", "env_val"),
    [
        ("RENTCHECK_TRIALS_PER_VARIANT", "0"),
        ("RENTCHECK_BUDGET_USD", "-1"),
        ("RENTCHECK_MAX_CONCURRENCY", "0"),
        ("RENTCHECK_TIMEOUT_SECONDS", "0"),
    ],
)
def test_invalid_values_raise_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_val: str,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(env_key, env_val)
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path)
    assert exc.value.hint is not None


def test_invalid_toml_value_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "rentcheck.toml").write_text("trials_per_variant = 0\n")
    with pytest.raises(ConfigError):
        load_settings(tmp_path)

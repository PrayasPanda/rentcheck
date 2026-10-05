"""CLI-level tests for ``rentcheck scan``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rentcheck.__main__ import app

runner = CliRunner()


def test_scan_table_output(sample_repo: Path) -> None:
    result = runner.invoke(app, ["scan", str(sample_repo)])
    assert result.exit_code == 0
    assert "Your agent reads" in result.stdout
    assert "tokens of context" in result.stdout
    assert "scripts/legacy_migrate.sh" in result.stdout
    assert "approximate" in result.stdout


def test_scan_json_output(sample_repo: Path) -> None:
    result = runner.invoke(
        app,
        ["scan", str(sample_repo), "--json", "--requests-per-day", "500"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tokenizer"] == "cl100k_base"
    assert payload["total_tokens"] > 0
    assert payload["cost"]["requests_per_day"] == 500
    assert any(r["reference"] == "scripts/legacy_migrate.sh" for r in payload["stale_refs"])

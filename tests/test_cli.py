"""Smoke tests for the rentcheck CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from rentcheck import __version__
from rentcheck.__main__ import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


@pytest.mark.parametrize("command", ["mine", "ablate", "report"])
def test_command_stub(command: str) -> None:
    result = runner.invoke(app, [command])
    assert result.exit_code == 0
    assert "not implemented yet" in result.stdout

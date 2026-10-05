"""CLI-level test for ``rentcheck mine``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from rentcheck.__main__ import app

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_mine_json_output(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "m.py").write_text("def f():\n    return 1\n")
    (repo / "test_m.py").write_text("from m import f\n\n\ndef test_f():\n    assert f() == 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    (repo / "m.py").write_text("def f():\n    return 2\n")
    (repo / "test_m.py").write_text("from m import f\n\n\ndef test_f():\n    assert f() == 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: bump return value")

    result = runner.invoke(app, ["mine", str(repo), "--no-validate", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert len(payload["created"]) == 1
    assert payload["created"][0]["test_command"] == "pytest"
    assert payload["scanned_commits"] == 2


def test_mine_validate_only_json(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    # mul() is wrong at baseline; the fix commit corrects it and adds a test.
    (repo / "calc.py").write_text("def mul(a, b):\n    return 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    (repo / "calc.py").write_text("def mul(a, b):\n    return a * b\n")
    (repo / "test_calc.py").write_text(
        "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: correct mul")

    # Pin the test command to this interpreter's pytest so it resolves on PATH
    # regardless of whether a `pytest` console script is installed.
    env = {"RENTCHECK_TEST_COMMAND": f"{sys.executable} -m pytest -q"}

    # Mine first (no validation), then validate-only.
    mined = runner.invoke(app, ["mine", str(repo), "--no-validate", "--json"], env=env)
    assert mined.exit_code == 0, mined.stdout

    result = runner.invoke(app, ["mine", str(repo), "--validate-only", "--json"], env=env)
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["status"] == "valid"
    assert payload[0]["fail_tests"] == ["test_calc.py::test_mul"]

"""Tests for the validation service against a real temporary git repository.

Each task directory is authored directly (task.json + tests.patch + gold.patch)
so the BASE/GOLD behaviour of every scenario is controlled precisely, rather
than mined from commits that happen to produce it. All tasks share one parent
commit; validation checks it out into throwaway worktrees.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rentcheck.core.config import Settings
from rentcheck.schemas.task import Task
from rentcheck.schemas.validation import DropReason, TaskStatus
from rentcheck.services.validation_service import validate_tasks


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _file_patch(path: str, body: str) -> str:
    """A minimal git-apply-able patch that creates ``path`` with ``body``."""
    lines = body.splitlines()
    hunk = f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    return header + hunk


def _write_task(
    tasks_dir: Path,
    task_id: str,
    parent_sha: str,
    tests_patch: str,
    gold_patch: str,
    *,
    test_command: str = "python -m pytest -q",
) -> None:
    """Author a pending task directory with the given patches."""
    d = tasks_dir / task_id
    (d / "logs").mkdir(parents=True, exist_ok=True)
    task = Task(
        id=task_id,
        commit_sha="0" * 40,
        parent_sha=parent_sha,
        description="desc",
        files_changed=(),
        test_command=test_command,
        fail_tests=(),
        pass_tests=(),
    )
    (d / "task.json").write_text(task.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (d / "tests.patch").write_text(tests_patch, encoding="utf-8")
    (d / "gold.patch").write_text(gold_patch, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo with one baseline commit; returns (repo path, parent sha)."""
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Tester")
    # mul() exists but is wrong at baseline, so the good task's target test is
    # collectable and fails with an assertion (clean node id), not an import error.
    (r / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return 0\n"
    )
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "baseline")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True
    ).stdout.strip()
    return r, sha


def _settings(timeout: int = 60) -> Settings:
    return Settings(timeout_seconds=timeout, max_concurrency=2)


def _load(repo_path: Path, task_id: str) -> Task:
    data = json.loads((repo_path / ".rentcheck" / "tasks" / task_id / "task.json").read_text())
    return Task(**data)


# --- scenario patches -------------------------------------------------------

# A good red/green task: mul() exists at baseline but returns 0, so the target
# test fails on BASE (red); the gold fixes mul() so it passes on GOLD (green).
GOOD_TESTS = _file_patch(
    "test_mul.py",
    "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
)
GOOD_GOLD = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -3,4 +3,4 @@ def add(a, b):\n"
    " \n"
    " \n"
    " def mul(a, b):\n"
    "-    return 0\n"
    "+    return a * b\n"
)


async def test_good_task_is_valid(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    _write_task(tasks, "good", sha, GOOD_TESTS, GOOD_GOLD)

    results = await validate_tasks(repo_path, settings=_settings())
    assert len(results) == 1
    r = results[0]
    assert r.status is TaskStatus.VALID
    assert r.fail_tests == ("test_mul.py::test_mul",)
    stored = _load(repo_path, "good")
    assert stored.status is TaskStatus.VALID
    assert stored.fail_tests == ("test_mul.py::test_mul",)


async def test_base_already_passes_is_dropped(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    # Target test passes with only tests.patch (uses existing add()).
    tests = _file_patch(
        "test_add.py",
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    gold = _file_patch("noop.py", "x = 1\n")
    _write_task(tasks, "already", sha, tests, gold)

    results = await validate_tasks(repo_path, settings=_settings())
    assert results[0].status is TaskStatus.DROPPED
    assert results[0].drop_reason is DropReason.BASE_ALREADY_PASSES


async def test_gold_fails_is_dropped(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    # Target fails on BASE (mul returns 0) and still fails on GOLD because the
    # gold "fix" is wrong (a + b instead of a * b).
    gold = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -3,4 +3,4 @@ def add(a, b):\n"
        " \n"
        " \n"
        " def mul(a, b):\n"
        "-    return 0\n"
        "+    return a + b\n"
    )
    _write_task(tasks, "goldfail", sha, GOOD_TESTS, gold)

    results = await validate_tasks(repo_path, settings=_settings())
    assert results[0].status is TaskStatus.DROPPED
    assert results[0].drop_reason is DropReason.GOLD_FAILS


async def test_flaky_task_is_dropped(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    tests = _file_patch(
        "test_flak.py",
        "from flak import val\n\n\ndef test_flak():\n    assert val() == 1\n",
    )
    # A file toggle: first GOLD run creates the toggle and returns 0 (fail),
    # second run sees it and returns 1 (pass) — results differ → flaky.
    # The toggle lives in the shared .rentcheck dir (walk up from the worktree),
    # so it persists across the two separate GOLD worktrees → differing results.
    gold_body = (
        "import pathlib\n"
        "def _toggle():\n"
        "    for p in pathlib.Path(__file__).resolve().parents:\n"
        "        if p.name == '.rentcheck':\n"
        "            return p / '.flak_toggle'\n"
        "    return pathlib.Path(__file__).with_name('.flak_toggle')\n"
        "def val():\n"
        "    t = _toggle()\n"
        "    if t.exists():\n"
        "        return 1\n"
        "    t.write_text('x')\n"
        "    return 0\n"
    )
    gold = _file_patch("flak.py", gold_body)
    _write_task(tasks, "flaky", sha, tests, gold)

    results = await validate_tasks(repo_path, settings=_settings())
    assert results[0].status is TaskStatus.FLAKY


async def test_timeout_is_dropped(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    tests = _file_patch(
        "test_slow.py",
        "from slow import go\n\n\ndef test_slow():\n    assert go() == 1\n",
    )
    gold = _file_patch("slow.py", "import time\n\n\ndef go():\n    time.sleep(30)\n    return 1\n")
    _write_task(tasks, "slow", sha, tests, gold)

    results = await validate_tasks(repo_path, settings=_settings(timeout=1))
    assert results[0].status is TaskStatus.DROPPED
    assert results[0].drop_reason is DropReason.TIMED_OUT


async def test_patch_fails_to_apply_is_dropped(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    # tests.patch is valid (target test present) but gold.patch cannot apply
    # (it edits a file that does not exist at the parent) → PATCH_FAILED on GOLD.
    bad_gold = (
        "diff --git a/missing.py b/missing.py\n"
        "--- a/missing.py\n"
        "+++ b/missing.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    _write_task(tasks, "badpatch", sha, GOOD_TESTS, bad_gold)

    results = await validate_tasks(repo_path, settings=_settings())
    assert results[0].status is TaskStatus.DROPPED
    assert results[0].drop_reason is DropReason.PATCH_FAILED


async def test_worktrees_always_removed(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    _write_task(tasks, "good", sha, GOOD_TESTS, GOOD_GOLD)
    await validate_tasks(repo_path, settings=_settings())
    wt = repo_path / ".rentcheck" / "worktrees"
    assert not wt.exists() or not any(wt.iterdir())


async def test_idempotent_unless_force(repo: tuple[Path, str]) -> None:
    repo_path, sha = repo
    tasks = repo_path / ".rentcheck" / "tasks"
    _write_task(tasks, "good", sha, GOOD_TESTS, GOOD_GOLD)

    first = await validate_tasks(repo_path, settings=_settings())
    assert len(first) == 1
    # Re-running without force skips the already-validated task.
    second = await validate_tasks(repo_path, settings=_settings())
    assert second == []
    # With force it runs again.
    forced = await validate_tasks(repo_path, settings=_settings(), force=True)
    assert len(forced) == 1
    assert forced[0].status is TaskStatus.VALID

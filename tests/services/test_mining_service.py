"""Tests for the mining service against a real temporary git repository."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rentcheck.core.errors import MiningError
from rentcheck.schemas.task import Task
from rentcheck.services.mining_service import mine_repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


@pytest.fixture
def mined_repo(tmp_path: Path) -> Path:
    """A tiny repo with one minable commit and several that must be skipped."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")

    # Root commit (no parent) — baseline source + tests.
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    _commit(repo, "initial")

    # Docs-only commit — must be skipped (no test change).
    (repo / "README.md").write_text("# proj\n")
    _commit(repo, "docs: add readme (#1)")

    # Good code+test commit — the one task we expect.
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
    )
    (repo / "test_calc.py").write_text(
        "from calc import add, mul\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_mul():\n    assert mul(2, 3) == 6\n"
    )
    _commit(repo, "feat: add mul (#2)\n\nAdds multiply.\n\nCo-authored-by: Bot <bot@example.com>")

    # Test-only commit — must be skipped (no source change).
    (repo / "test_calc.py").write_text(
        (repo / "test_calc.py").read_text()
        + "\n\ndef test_add_zero():\n    assert add(1, 0) == 1\n"
    )
    _commit(repo, "test: extra add case")

    # Huge diff commit (with a test change) — skipped by --max-diff-lines.
    (repo / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(500)) + "\n")
    (repo / "test_big.py").write_text("import big\n\n\ndef test_big():\n    assert big.x0 == 0\n")
    _commit(repo, "feat: add big module")

    # Merge commit — skipped. Branch carries only a docs change (not minable
    # itself) so the single expected task stays unambiguous.
    _git(repo, "checkout", "-b", "side")
    (repo / "NOTES.md").write_text("notes\n")
    _commit(repo, "docs: notes on side branch")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "merge side", "side")

    return repo


def test_only_good_commit_becomes_task(mined_repo: Path) -> None:
    result = mine_repo(mined_repo, max_diff_lines=200)
    assert len(result.created) == 1
    task = result.created[0]
    assert task.id.endswith("-feat-add-mul")
    assert task.test_command == "pytest"
    # Every other commit type is represented in skip reasons.
    assert result.skipped["merge commit"] == 1
    assert result.skipped["no test change"] >= 1  # docs commit
    assert result.skipped["no source change"] >= 1  # test-only commit
    assert result.skipped["diff too large"] >= 1  # big commit


def test_patches_split_tests_and_gold(mined_repo: Path) -> None:
    result = mine_repo(mined_repo)
    task_dir = mined_repo / ".rentcheck" / "tasks" / result.created[0].id
    tests_patch = (task_dir / "tests.patch").read_text()
    gold_patch = (task_dir / "gold.patch").read_text()

    assert "test_calc.py" in tests_patch and "mul" in tests_patch
    assert "calc.py" in gold_patch and "def mul" in gold_patch
    # Gold must not leak into the agent-visible patch, and vice versa.
    assert "b/calc.py" not in tests_patch
    assert "b/test_calc.py" not in gold_patch

    stored = Task(**json.loads((task_dir / "task.json").read_text()))
    assert stored.fail_tests == () and stored.pass_tests == ()
    assert "modifying the test files" in stored.description
    assert "#2" not in stored.description  # issue number stripped
    assert "Co-authored-by" not in stored.description


def test_ids_are_stable(mined_repo: Path) -> None:
    first = mine_repo(mined_repo).created[0].id
    second = mine_repo(mined_repo, force=True).created[0].id
    assert first == second


def test_rerun_skips_existing_unless_force(mined_repo: Path) -> None:
    mine_repo(mined_repo)
    rerun = mine_repo(mined_repo)
    assert rerun.created == ()
    assert rerun.skipped["already exists"] == 1

    forced = mine_repo(mined_repo, force=True)
    assert len(forced.created) == 1


def test_working_tree_untouched(mined_repo: Path) -> None:
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=mined_repo, capture_output=True, text=True
    ).stdout
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=mined_repo, capture_output=True, text=True
    ).stdout
    mine_repo(mined_repo)
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=mined_repo, capture_output=True, text=True
    ).stdout
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=mined_repo, capture_output=True, text=True
    ).stdout
    # .rentcheck is the only new path; HEAD and tracked files are unchanged.
    assert head_before == head_after
    assert all(".rentcheck" in line for line in after.splitlines() if line not in before)


def test_worktree_always_cleaned_up(mined_repo: Path) -> None:
    from rentcheck.repositories.git_repo import GitRepo

    repo = GitRepo(mined_repo)
    head = next(repo.iter_commits()).sha

    with pytest.raises(RuntimeError):
        with repo.worktree(head, "boom") as wt:
            assert wt.exists()
            raise RuntimeError("boom")
    assert not (mined_repo / ".rentcheck" / "worktrees" / "boom").exists()


def test_not_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(MiningError, match="not a git repository"):
        mine_repo(plain)

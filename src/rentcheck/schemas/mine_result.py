"""Schemas describing the result of ``rentcheck mine``."""

from __future__ import annotations

from pydantic import BaseModel


class MinedTask(BaseModel, frozen=True):
    """A task created during mining, with the figures shown in the CLI table."""

    id: str
    commit_sha: str
    files_changed: int
    diff_lines: int
    test_command: str


class MineResult(BaseModel, frozen=True):
    """The complete result of mining a repository for tasks.

    ``skipped`` maps a human-readable reason to the number of commits dropped
    for it (e.g. ``{"merge commit": 3, "no test change": 12}``).
    """

    repo_path: str
    scanned_commits: int
    created: tuple[MinedTask, ...]
    skipped: dict[str, int]
    tasks_dir: str


__all__ = ["MineResult", "MinedTask"]

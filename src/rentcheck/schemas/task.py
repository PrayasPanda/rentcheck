"""The :class:`Task` schema: one historical change to reproduce."""

from __future__ import annotations

from pydantic import BaseModel


class Task(BaseModel, frozen=True):
    """A single coding task mined from repository history.

    A task reproduces one commit: starting from ``parent_sha``, the agent must
    make ``fail_tests`` pass without breaking ``pass_tests``.
    """

    id: str
    commit_sha: str
    parent_sha: str
    description: str
    files_changed: tuple[str, ...]
    test_command: str
    fail_tests: tuple[str, ...]
    pass_tests: tuple[str, ...]

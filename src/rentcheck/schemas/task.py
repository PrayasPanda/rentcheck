"""The :class:`Task` schema: one historical change to reproduce."""

from __future__ import annotations

from pydantic import BaseModel

from rentcheck.schemas.validation import DropReason, TaskStatus


class Task(BaseModel, frozen=True):
    """A single coding task mined from repository history.

    A task reproduces one commit: starting from ``parent_sha``, the agent must
    make ``fail_tests`` pass without breaking ``pass_tests``. ``status`` and
    ``drop_reason`` are filled by the validator; freshly mined tasks are
    ``PENDING`` with no reason.
    """

    id: str
    commit_sha: str
    parent_sha: str
    description: str
    files_changed: tuple[str, ...]
    test_command: str
    fail_tests: tuple[str, ...]
    pass_tests: tuple[str, ...]
    status: TaskStatus = TaskStatus.PENDING
    drop_reason: DropReason | None = None

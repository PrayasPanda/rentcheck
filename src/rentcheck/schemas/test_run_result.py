"""The :class:`TestRunResult` schema: the outcome of one test-command run."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel


class TestRunResult(BaseModel, frozen=True):
    """The outcome of running a task's test command once in a worktree.

    ``passed_tests`` and ``failed_tests`` hold per-test node ids when the
    framework produced structured output (pytest via ``--junitxml``); for other
    frameworks they are empty and only ``exit_code`` is meaningful.
    """

    # Tell pytest this is not a test class despite the ``Test`` prefix.
    __test__: ClassVar[bool] = False

    exit_code: int
    passed_tests: tuple[str, ...]
    failed_tests: tuple[str, ...]
    duration_s: float
    timed_out: bool
    log_path: str

    @property
    def ok(self) -> bool:
        """Whether the command as a whole succeeded (exit code 0, no timeout)."""
        return self.exit_code == 0 and not self.timed_out


__all__ = ["TestRunResult"]

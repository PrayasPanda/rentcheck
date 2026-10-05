"""Schemas for task validation: status, drop reasons, and the per-task result."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class TaskStatus(str, Enum):
    """The validation verdict stored on a task.

    ``PENDING`` is the pre-validation state of a freshly mined task.
    """

    PENDING = "pending"
    VALID = "valid"
    FLAKY = "flaky"
    DROPPED = "dropped"


class DropReason(str, Enum):
    """Why a task was dropped during validation."""

    PATCH_FAILED = "patch_failed"
    SETUP_FAILED = "setup_failed"
    TIMED_OUT = "timed_out"
    BASE_ALREADY_PASSES = "base_already_passes"
    GOLD_FAILS = "gold_fails"
    NO_TARGET_TESTS = "no_target_tests"


class ValidationResult(BaseModel, frozen=True):
    """The outcome of validating one task, carried to storage and the CLI."""

    task_id: str
    status: TaskStatus
    drop_reason: DropReason | None = None
    fail_tests: tuple[str, ...] = ()
    pass_tests: tuple[str, ...] = ()
    target_tests: tuple[str, ...] = ()


__all__ = ["DropReason", "TaskStatus", "ValidationResult"]

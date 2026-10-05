"""Validate mined tasks so only reliable red/green ones are kept.

A task is useful only if its target tests FAIL on the parent commit with
``tests.patch`` applied (BASE) and PASS once ``gold.patch`` is also applied
(GOLD), consistently. This service reproduces both states in throwaway
worktrees, runs the tests, and classifies each task as valid, flaky, or dropped
with a clear reason.

Target tests are the test functions added or changed by ``tests.patch``; only
those drive the BASE-fails / GOLD-passes decision. ``fail_tests`` records the
targets that failed on BASE and pass on GOLD; ``pass_tests`` records tests that
pass in both states (used later to detect regressions).

All subprocess work happens inside worktrees; the user's working tree is never
touched, and every worktree is cleaned up even on error, timeout, or Ctrl+C.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from rentcheck.core.config import Settings, load_settings
from rentcheck.repositories.git_repo import GitRepo
from rentcheck.schemas.task import Task
from rentcheck.schemas.test_run_result import TestRunResult
from rentcheck.schemas.validation import DropReason, TaskStatus, ValidationResult
from rentcheck.services.test_runner import run_tests

# Lines like ``+def test_foo(`` / ``+    def test_bar(`` added by tests.patch.
_ADDED_TEST_RE = re.compile(r"^\+\s*def\s+(test_\w+)\s*\(")
# ``+++ b/path/to/test_x.py`` marks the current file in a unified diff.
_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")

ProgressFn = Callable[[str, str], None]


async def validate_tasks(
    repo_path: Path,
    *,
    task_ids: Iterable[str] | None = None,
    force: bool = False,
    settings: Settings | None = None,
    on_status: ProgressFn | None = None,
) -> list[ValidationResult]:
    """Validate stored tasks concurrently and persist each verdict.

    Args:
        task_ids: restrict to these task ids; ``None`` validates all stored tasks.
        force: re-validate tasks already marked valid/flaky/dropped.
        settings: pre-loaded settings; loaded from the repo when omitted.
        on_status: optional callback ``(task_id, status)`` for live CLI updates.
    """
    repo = GitRepo(repo_path)
    settings = settings or load_settings(repo.root)
    tasks_dir = repo.root / ".rentcheck" / "tasks"

    wanted = set(task_ids) if task_ids is not None else None
    pending = [
        d
        for d in sorted(tasks_dir.glob("*/task.json"))
        if (wanted is None or d.parent.name in wanted)
    ]

    sem = asyncio.Semaphore(settings.max_concurrency)
    results = await asyncio.gather(
        *(_validate_one(repo, p, settings, force, sem, on_status) for p in pending)
    )
    return [r for r in results if r is not None]


async def _validate_one(
    repo: GitRepo,
    task_json: Path,
    settings: Settings,
    force: bool,
    sem: asyncio.Semaphore,
    on_status: ProgressFn | None,
) -> ValidationResult | None:
    """Validate a single task, skipping already-validated ones unless forced."""
    task = Task.model_validate_json(task_json.read_text(encoding="utf-8"))
    if task.status is not TaskStatus.PENDING and not force:
        return None

    async with sem:
        if on_status is not None:
            on_status(task.id, "validating")
        result = await _run_validation(repo, task, task_json.parent, settings)

    _persist(task_json, task, result)
    if on_status is not None:
        on_status(task.id, result.status.value)
    return result


async def _run_validation(
    repo: GitRepo,
    task: Task,
    task_dir: Path,
    settings: Settings,
) -> ValidationResult:
    """Reproduce BASE and GOLD states and classify the task."""
    tests_patch = task_dir / "tests.patch"
    gold_patch = task_dir / "gold.patch"
    logs = task_dir / "logs"

    targets = _target_tests(tests_patch)
    if not targets:
        return _drop(task, DropReason.NO_TARGET_TESTS)

    # BASE: parent + tests.patch — target tests must fail.
    base = await _reproduce(repo, task, [tests_patch], settings, logs, "base")
    if isinstance(base, DropReason):
        return _drop(task, base)
    if not _any_target_failed(base, targets):
        return _drop(task, DropReason.BASE_ALREADY_PASSES)

    # GOLD (twice): parent + tests.patch + gold.patch — target tests must pass.
    gold1 = await _reproduce(repo, task, [tests_patch, gold_patch], settings, logs, "gold1")
    if isinstance(gold1, DropReason):
        return _drop(task, gold1)
    gold2 = await _reproduce(repo, task, [tests_patch, gold_patch], settings, logs, "gold2")
    if isinstance(gold2, DropReason):
        return _drop(task, gold2)

    if set(gold1.passed_tests) != set(gold2.passed_tests) or set(gold1.failed_tests) != set(
        gold2.failed_tests
    ):
        return ValidationResult(
            task_id=task.id, status=TaskStatus.FLAKY, target_tests=tuple(sorted(targets))
        )

    gold_passed = set(gold1.passed_tests)
    if not targets <= gold_passed:
        return _drop(task, DropReason.GOLD_FAILS)

    fail_tests = tuple(sorted(t for t in targets if t in set(base.failed_tests)))
    pass_tests = tuple(sorted(set(base.passed_tests) & gold_passed))
    return ValidationResult(
        task_id=task.id,
        status=TaskStatus.VALID,
        fail_tests=fail_tests,
        pass_tests=pass_tests,
        target_tests=tuple(sorted(targets)),
    )


async def _reproduce(
    repo: GitRepo,
    task: Task,
    patches: list[Path],
    settings: Settings,
    logs: Path,
    label: str,
) -> TestRunResult | DropReason:
    """Build a worktree at the parent, apply patches, run setup + tests.

    Returns a :class:`TestRunResult`, or a :class:`DropReason` when a patch
    fails to apply, setup fails, or the run times out. The worktree is always
    removed on exit.
    """
    slug = f"{task.id}-{label}"
    with repo.worktree(task.parent_sha, slug) as wt:
        for patch in patches:
            if not repo.apply_patch(wt, patch):
                return DropReason.PATCH_FAILED
        if settings.setup_command and not await _setup(settings, wt, logs, label):
            return DropReason.SETUP_FAILED
        run = await run_tests(
            task.test_command,
            wt,
            timeout_s=settings.timeout_seconds,
            log_path=logs / f"{label}.log",
            label=label,
        )
        if run.timed_out:
            return DropReason.TIMED_OUT
        return run


async def _setup(settings: Settings, wt: Path, logs: Path, label: str) -> bool:
    """Run the optional setup command once in the worktree; True on success."""
    assert settings.setup_command is not None
    run = await run_tests(
        settings.setup_command,
        wt,
        timeout_s=settings.timeout_seconds,
        log_path=logs / f"{label}-setup.log",
        label=f"{label}-setup",
    )
    return run.ok


def _target_tests(tests_patch: Path) -> set[str]:
    """Extract ``file::test_name`` ids for tests added/changed by the patch."""
    if not tests_patch.exists():
        return set()
    targets: set[str] = set()
    current = ""
    for line in tests_patch.read_text(encoding="utf-8").splitlines():
        file_match = _FILE_RE.match(line)
        if file_match:
            current = file_match.group(1)
            continue
        test_match = _ADDED_TEST_RE.match(line)
        if test_match and current:
            targets.add(f"{current}::{test_match.group(1)}")
    return targets


def _any_target_failed(run: TestRunResult, targets: set[str]) -> bool:
    """Whether at least one target test failed (or is absent, i.e. errored)."""
    failed = set(run.failed_tests)
    passed = set(run.passed_tests)
    return any(t in failed or t not in passed for t in targets)


def _drop(task: Task, reason: DropReason) -> ValidationResult:
    """Build a dropped :class:`ValidationResult` with ``reason``."""
    return ValidationResult(task_id=task.id, status=TaskStatus.DROPPED, drop_reason=reason)


def _persist(task_json: Path, task: Task, result: ValidationResult) -> None:
    """Write the validated task back to its JSON with status and test sets."""
    updated = task.model_copy(
        update={
            "status": result.status,
            "drop_reason": result.drop_reason,
            "fail_tests": result.fail_tests,
            "pass_tests": result.pass_tests,
        }
    )
    task_json.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")


__all__ = ["validate_tasks"]

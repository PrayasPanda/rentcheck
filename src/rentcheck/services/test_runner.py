"""Run a task's test command in a worktree and capture structured results.

Tests are run with :mod:`asyncio` subprocesses so many tasks can be validated
concurrently without blocking. For pytest we inject ``--junitxml`` and parse the
XML, which is reliable; for every other framework we fall back to the whole
command's exit code. Full stdout/stderr is saved under
``.rentcheck/tasks/<task_id>/logs/`` for debugging.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path
from xml.etree import ElementTree

from rentcheck.schemas.test_run_result import TestRunResult


async def run_tests(
    command: str,
    cwd: Path,
    *,
    timeout_s: float,
    log_path: Path,
    label: str = "run",
) -> TestRunResult:
    """Run ``command`` in ``cwd``, capturing output and per-test results.

    Args:
        command: the shell test command (e.g. ``"pytest"``).
        cwd: the worktree to run inside; never the user's working tree.
        timeout_s: per-run wall-clock timeout; a breach marks ``timed_out``.
        log_path: file to write combined stdout/stderr to.
        label: short tag recorded in the log header (e.g. ``"base"``/``"gold"``).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path: Path | None = None
    full_command = command
    if _is_pytest(command):
        junit_path = log_path.with_suffix(".junit.xml")
        # Double-quote the path: valid for both cmd.exe and POSIX shells when the
        # path has no embedded quotes (log paths never do). shlex.quote uses
        # single quotes, which cmd.exe passes through literally.
        full_command = f'{command} --junitxml "{junit_path}"'

    started = time.monotonic()
    stdout, exit_code, timed_out = await _spawn(full_command, cwd, timeout_s)
    duration_s = time.monotonic() - started

    header = f"$ {command}  [{label}, exit={exit_code}, timed_out={timed_out}]\n\n"
    log_path.write_text(header + stdout, encoding="utf-8")

    passed, failed = _parse_junit(junit_path) if junit_path else ((), ())

    return TestRunResult(
        exit_code=exit_code,
        passed_tests=passed,
        failed_tests=failed,
        duration_s=round(duration_s, 3),
        timed_out=timed_out,
        log_path=log_path.as_posix(),
    )


async def _spawn(command: str, cwd: Path, timeout_s: float) -> tuple[str, int, bool]:
    """Run ``command`` via the shell in ``cwd``, returning (output, code, timed_out).

    A shell is used so a test command string (``pytest``, ``npm test``, …)
    resolves its executable on PATH the way a user would run it. On timeout the
    process is killed and ``exit_code`` is reported as -1.
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError):
        _kill(proc)
        out, _ = await proc.communicate()
        return out.decode("utf-8", errors="replace"), -1, True
    return out.decode("utf-8", errors="replace"), proc.returncode or 0, False


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Best-effort kill of a still-running subprocess."""
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover - already exited
        pass


def _is_pytest(command: str) -> bool:
    """Whether ``command`` invokes pytest (so junit parsing applies)."""
    head = shlex.split(command)[:3]
    return any(part == "pytest" or part.endswith("pytest") for part in head)


def _parse_junit(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse a pytest junit XML into (passed node ids, failed node ids).

    A testcase counts as failed if it has a ``failure`` or ``error`` child;
    skipped cases are ignored. Node ids are built as ``file::name`` to match the
    ids extracted from ``tests.patch``.
    """
    if not path.exists():
        return (), ()
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError:  # pragma: no cover - defensive
        return (), ()
    passed: list[str] = []
    failed: list[str] = []
    for case in root.iter("testcase"):
        node_id = _node_id(case)
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(node_id)
        elif case.find("skipped") is None:
            passed.append(node_id)
    return tuple(passed), tuple(failed)


def _node_id(case: ElementTree.Element) -> str:
    """Build a ``file::name`` node id from a junit ``testcase`` element."""
    name = case.get("name", "")
    file = case.get("file") or _file_from_classname(case.get("classname", ""))
    return f"{file}::{name}" if file else name


def _file_from_classname(classname: str) -> str:
    """Derive a file path from a pytest junit ``classname`` (dotted module)."""
    if not classname:
        return ""
    module = classname.split(".")[0] if "." in classname else classname
    return f"{module.replace('.', '/')}.py"


__all__ = ["run_tests"]

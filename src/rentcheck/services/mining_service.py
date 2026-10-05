"""The mining service: turn real past commits into reproducible tasks.

Walks history newest first, keeps commits that change both test and source
files, and writes each one out as a :class:`Task` plus two patches: ``tests.patch``
(shown to the agent, applied on top of the parent) and ``gold.patch`` (the hidden
source solution). Validation — actually running the tests to confirm they fail
then pass — is intentionally left to a later PR; this service only constructs the
material it needs, leaving ``fail_tests``/``pass_tests`` empty.

The CLI layer renders what :func:`mine_repo` returns; all mining logic is here.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

from rentcheck.core.config import Settings, load_settings
from rentcheck.core.errors import MiningError
from rentcheck.repositories.git_repo import CommitInfo, FileDiff, GitRepo
from rentcheck.schemas.mine_result import MinedTask, MineResult
from rentcheck.schemas.task import Task

# Paths that, on their own, do not constitute a real source change worth mining.
_NON_SOURCE_SUFFIXES = (
    ".md",
    ".rst",
    ".txt",
    ".lock",
    ".cfg",
    ".ini",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
)
_NON_SOURCE_NAMES = frozenset(
    {"package-lock.json", "poetry.lock", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock"}
)

# Noise stripped from commit messages when building a task description.
_ISSUE_RE = re.compile(r"\(?#\d+\)?|\bGH-\d+\b", re.IGNORECASE)
_TRAILER_RE = re.compile(
    r"^(co-authored-by|signed-off-by|reviewed-by|acked-by|fixes|closes|refs):",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_INSTRUCTION = "Make the failing tests pass without modifying the test files."

ProgressFn = Callable[[CommitInfo], None]


def mine_repo(
    repo_path: Path,
    *,
    max_tasks: int = 20,
    max_diff_lines: int = 200,
    since: str | None = None,
    force: bool = False,
    settings: Settings | None = None,
    on_scan: ProgressFn | None = None,
) -> MineResult:
    """Mine ``repo_path`` for up to ``max_tasks`` reproducible tasks.

    Args:
        max_tasks: stop after this many candidate commits become tasks.
        max_diff_lines: skip commits whose non-test source diff is larger.
        since: optional git date bound passed to history walking.
        force: re-create tasks whose directory already exists.
        settings: pre-loaded settings; loaded from the repo when omitted.
        on_scan: optional callback invoked once per commit as it is examined.

    Raises:
        MiningError: if the path is not a git repo, or no candidates are found.
    """
    repo = GitRepo(repo_path)
    settings = settings or load_settings(repo.root)
    tasks_dir = repo.root / ".rentcheck" / "tasks"

    created: list[MinedTask] = []
    skipped: Counter[str] = Counter()
    scanned = 0

    for commit in repo.iter_commits(since=since):
        scanned += 1
        if on_scan is not None:
            on_scan(commit)

        reason = _reject_reason(commit)
        if reason is not None:
            skipped[reason] += 1
            continue

        diffs = repo.file_diffs(commit.sha, commit.parent_sha)
        tests, source = _split_diffs(diffs, settings.test_patterns)

        reason = _reject_diffs(tests, source, max_diff_lines)
        if reason is not None:
            skipped[reason] += 1
            continue

        task_id = _task_id(commit)
        task_dir = tasks_dir / task_id
        if task_dir.exists() and not force:
            skipped["already exists"] += 1
            continue

        task = _build_task(commit, tests, source, settings)
        _write_task(task_dir, task, tests, source)
        created.append(
            MinedTask(
                id=task_id,
                commit_sha=commit.sha,
                files_changed=len(tests) + len(source),
                diff_lines=_diff_lines(source),
                test_command=task.test_command,
            )
        )
        if len(created) >= max_tasks:
            break

    if not created and not skipped.get("already exists"):
        raise MiningError(
            "no commits with both code and test changes found",
            hint="Try --since to widen history, or raise --max-diff-lines.",
        )

    return MineResult(
        repo_path=repo.root.as_posix(),
        scanned_commits=scanned,
        created=tuple(created),
        skipped=dict(skipped),
        tasks_dir=tasks_dir.as_posix(),
    )


# --- candidate selection ---------------------------------------------------


def _reject_reason(commit: CommitInfo) -> str | None:
    """Reasons to reject a commit on metadata alone, else ``None``."""
    if commit.is_merge:
        return "merge commit"
    if not commit.parent_sha:
        return "no parent"
    return None


def _split_diffs(
    diffs: list[FileDiff], patterns: tuple[str, ...]
) -> tuple[list[FileDiff], list[FileDiff]]:
    """Partition diffs into (test files, non-test source files)."""
    tests = [d for d in diffs if _is_test_path(d.path, patterns)]
    source = [d for d in diffs if not _is_test_path(d.path, patterns)]
    return tests, source


def _reject_diffs(tests: list[FileDiff], source: list[FileDiff], max_diff_lines: int) -> str | None:
    """Reasons to reject a commit based on its diffs, else ``None``."""
    if not tests:
        return "no test change"
    if any(d.is_binary for d in tests + source):
        return "binary change"
    real_source = [d for d in source if not _is_non_source(d.path)]
    if not real_source:
        return "no source change"
    if _diff_lines(real_source) > max_diff_lines:
        return "diff too large"
    return None


def _is_test_path(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``path`` matches any configured test pattern."""
    name = path.rsplit("/", 1)[-1]
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or f"/{pat}" in f"/{path}":
                return True
        elif fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


def _is_non_source(path: str) -> bool:
    """Whether ``path`` is docs/config/lockfile noise, not real source."""
    name = path.rsplit("/", 1)[-1]
    return name in _NON_SOURCE_NAMES or path.lower().endswith(_NON_SOURCE_SUFFIXES)


def _diff_lines(diffs: list[FileDiff]) -> int:
    """Total added + deleted lines across ``diffs``."""
    return sum(d.added + d.deleted for d in diffs)


# --- task construction ------------------------------------------------------


def _build_task(
    commit: CommitInfo,
    tests: list[FileDiff],
    source: list[FileDiff],
    settings: Settings,
) -> Task:
    """Assemble a :class:`Task` from a commit and its split diffs."""
    files = tuple(sorted(d.path for d in tests + source))
    return Task(
        id=_task_id(commit),
        commit_sha=commit.sha,
        parent_sha=commit.parent_sha,
        description=_build_description(commit),
        files_changed=files,
        test_command=settings.test_command or _detect_test_command(files),
        fail_tests=(),  # filled by the validator in a later PR
        pass_tests=(),  # filled by the validator in a later PR
    )


def _build_description(commit: CommitInfo) -> str:
    """Build a deterministic, cleaned task description from the commit message."""
    subject = _ISSUE_RE.sub("", commit.subject).strip()
    body_lines = [
        line
        for line in commit.body.splitlines()
        if line.strip() and not _TRAILER_RE.match(line.strip())
    ]
    body = _ISSUE_RE.sub("", "\n".join(body_lines)).strip()
    parts = [subject] if subject else []
    if body:
        parts.append(body)
    parts.append(_INSTRUCTION)
    return "\n\n".join(parts)


def _detect_test_command(files: tuple[str, ...]) -> str:
    """Pick a test command from the languages present in the changed files."""
    suffixes = {Path(f).suffix for f in files}
    if ".py" in suffixes:
        return "pytest"
    if ".go" in suffixes:
        return "go test ./..."
    if ".rs" in suffixes:
        return "cargo test"
    if suffixes & {".ts", ".tsx", ".js", ".jsx"}:
        return "npm test"
    return "pytest"


def _task_id(commit: CommitInfo) -> str:
    """Stable, readable id: short sha plus a slug of the subject."""
    clean = _ISSUE_RE.sub("", commit.subject)
    slug = _SLUG_RE.sub("-", clean.lower()).strip("-")[:40].strip("-")
    short = commit.sha[:8]
    return f"{short}-{slug}" if slug else short


# --- storage ----------------------------------------------------------------


def _write_task(task_dir: Path, task: Task, tests: list[FileDiff], source: list[FileDiff]) -> None:
    """Write ``task.json``, ``tests.patch`` and ``gold.patch`` for one task."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.json").write_text(task.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (task_dir / "tests.patch").write_text(_combine_patches(tests), encoding="utf-8")
    (task_dir / "gold.patch").write_text(_combine_patches(source), encoding="utf-8")


def _combine_patches(diffs: list[FileDiff]) -> str:
    """Concatenate per-file patches into one applyable patch file."""
    return "".join(d.patch if d.patch.endswith("\n") else d.patch + "\n" for d in diffs)


def _iter_tasks(tasks_dir: Path) -> Iterator[Task]:  # pragma: no cover - used by later PR
    """Load every stored task (for the validator PR to consume)."""
    for task_json in sorted(tasks_dir.glob("*/task.json")):
        yield Task(**json.loads(task_json.read_text(encoding="utf-8")))


__all__ = ["mine_repo"]

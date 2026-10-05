"""Thin, read-only-ish wrapper over git for history mining.

Built on GitPython because it is already a project dependency (used by
``file_store`` for ``.gitignore`` handling), so there is no new dependency and
no hand-rolled subprocess parsing. This module only *reads* history and manages
throwaway worktrees under ``.rentcheck/worktrees/``; it never touches the user's
working tree, index, or current branch.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import git

from rentcheck.core.errors import MiningError


@dataclass(frozen=True)
class CommitInfo:
    """Metadata for a single commit, parent-relative.

    ``parent_sha`` is empty for a root commit (no parent) and such commits are
    not minable. ``is_merge`` is true when the commit has more than one parent.
    """

    sha: str
    parent_sha: str
    subject: str
    body: str
    is_merge: bool


@dataclass(frozen=True)
class FileDiff:
    """A single file's change between a commit and its parent.

    ``patch`` is a unified diff hunk for just this file, suitable for writing to
    a ``.patch`` file and applying with ``git apply``. ``is_binary`` marks
    changes git could not express as text.
    """

    path: str
    added: int
    deleted: int
    is_binary: bool
    patch: str


class GitRepo:
    """Read access to one repository's history, plus worktree management."""

    def __init__(self, path: Path) -> None:
        """Open the repository containing ``path``.

        Raises:
            MiningError: if ``path`` is not inside a git repository.
        """
        try:
            self._repo = git.Repo(path, search_parent_directories=True)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError) as exc:
            raise MiningError(
                f"not a git repository: {path}",
                hint="Run rentcheck mine from inside a git repository, or pass a repo path.",
            ) from exc
        self.root = Path(self._repo.working_dir)

    def iter_commits(self, since: str | None = None) -> Iterator[CommitInfo]:
        """Yield commits newest first on the current branch.

        Args:
            since: optional git date (e.g. ``"2024-01-01"``) to bound history.
        """
        kwargs = {"since": since} if since else {}
        for commit in self._repo.iter_commits(**kwargs):  # type: ignore[arg-type]
            parents = commit.parents
            message = str(commit.message)
            subject, _, body = message.partition("\n")
            yield CommitInfo(
                sha=commit.hexsha,
                parent_sha=parents[0].hexsha if parents else "",
                subject=subject.strip(),
                body=body.strip(),
                is_merge=len(parents) > 1,
            )

    def file_diffs(self, sha: str, parent_sha: str) -> list[FileDiff]:
        """Return per-file diffs for ``sha`` against ``parent_sha``.

        Patches are generated per file so test changes and source changes can be
        split into separate ``.patch`` files downstream.
        """
        parent = self._repo.commit(parent_sha)
        commit = self._repo.commit(sha)
        diffs: list[FileDiff] = []
        for diff in parent.diff(commit, create_patch=True):
            path = diff.b_path or diff.a_path
            if path is None:
                continue
            raw = diff.diff
            patch = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            added, deleted = _count_changes(patch)
            diffs.append(
                FileDiff(
                    path=path,
                    added=added,
                    deleted=deleted,
                    is_binary="Binary files" in patch or bool(diff.b_blob and _is_binary(diff)),
                    patch=_full_file_patch(diff, patch),
                )
            )
        return diffs

    @contextmanager
    def worktree(self, sha: str, slug: str) -> Iterator[Path]:
        """Check out ``sha`` in a throwaway worktree, always cleaned up.

        The worktree lives under ``.rentcheck/worktrees/`` and is removed on exit
        even if the body raises. The user's working tree is never touched.
        """
        wt_dir = self.root / ".rentcheck" / "worktrees" / slug
        self._remove_worktree(wt_dir)  # clear any stale leftover
        try:
            self._repo.git.worktree("add", "--detach", str(wt_dir), sha)
        except git.GitCommandError as exc:
            raise MiningError(
                f"could not create worktree for {sha[:8]}",
                hint="Ensure the working tree is clean and .rentcheck/worktrees is writable.",
            ) from exc
        try:
            yield wt_dir
        finally:
            self._remove_worktree(wt_dir)

    def _remove_worktree(self, wt_dir: Path) -> None:
        """Remove a worktree and prune git's bookkeeping, ignoring absence."""
        if wt_dir.exists():
            try:
                self._repo.git.worktree("remove", "--force", str(wt_dir))
            except git.GitCommandError:
                pass
        try:
            self._repo.git.worktree("prune")
        except git.GitCommandError:
            pass


def _is_binary(diff: git.Diff) -> bool:
    """Best-effort binary detection from a diff's blobs."""
    for blob in (diff.a_blob, diff.b_blob):
        if blob is not None and b"\x00" in blob.data_stream.read(8192):
            return True
    return False


def _count_changes(patch: str) -> tuple[int, int]:
    """Count added/deleted lines in a unified diff body."""
    added = deleted = 0
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
    return added, deleted


def _full_file_patch(diff: git.Diff, body: str) -> str:
    """Wrap a diff body in a minimal ``git apply``-able file header."""
    a = diff.a_path or diff.b_path
    b = diff.b_path or diff.a_path
    return f"diff --git a/{a} b/{b}\n--- a/{a}\n+++ b/{b}\n{body}"


__all__ = ["CommitInfo", "FileDiff", "GitRepo"]

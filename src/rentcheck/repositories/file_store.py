"""Discovery of agent context sources and MCP configs on disk.

This module knows *where* agent context lives in a repository and how to read
it safely. It performs no parsing or token counting; those belong to the scan
service. Discovery respects ``.gitignore`` (when the target is a git repo) and
always skips a small set of vendor and tooling directories.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import git

#: Directories never descended into, regardless of ``.gitignore``.
ALWAYS_SKIP = frozenset({".git", "node_modules", ".venv", "venv", ".rentcheck"})

#: Glob-free filenames that, anywhere in the tree, are instruction context.
_INSTRUCTION_NAMES = frozenset({"AGENTS.md", "CLAUDE.md"})


@dataclass(frozen=True)
class DiscoveredFile:
    """A context source found on disk, with its path relative to the repo."""

    #: Path relative to the repository root, using forward slashes.
    rel_path: str
    #: Absolute path on disk.
    abs_path: Path


@dataclass(frozen=True)
class McpServer:
    """One MCP server declared in a config file."""

    name: str
    #: Tool names declared for the server, when the config lists them.
    tools: tuple[str, ...]
    #: Path (relative to repo) of the config file the server was declared in.
    source_rel_path: str


@dataclass
class Discovery:
    """Everything :func:`discover` found in a repository."""

    instruction_files: list[DiscoveredFile] = field(default_factory=list)
    skill_files: list[DiscoveredFile] = field(default_factory=list)
    rule_files: list[DiscoveredFile] = field(default_factory=list)
    mcp_servers: list[McpServer] = field(default_factory=list)
    #: Non-fatal problems (unreadable files, malformed JSON).
    warnings: list[str] = field(default_factory=list)


# Static MCP config locations, relative to the repo root.
_MCP_CONFIG_PATHS = (".mcp.json", ".claude/settings.json", ".cursor/mcp.json")


def discover(repo_path: Path) -> Discovery:
    """Find all agent context sources and MCP configs under ``repo_path``."""
    repo_path = repo_path.resolve()
    result = Discovery()
    ignored = _IgnoreChecker(repo_path)

    for abs_path in _walk_files(repo_path, ignored):
        rel = _rel(repo_path, abs_path)
        name = abs_path.name
        if name in _INSTRUCTION_NAMES:
            result.instruction_files.append(DiscoveredFile(rel, abs_path))
        elif _is_skill_file(rel):
            result.skill_files.append(DiscoveredFile(rel, abs_path))
        elif _is_rule_file(rel, name):
            result.rule_files.append(DiscoveredFile(rel, abs_path))

    _sort_discovered(result)
    _collect_mcp_servers(repo_path, ignored, result)
    return result


def read_text(file: DiscoveredFile) -> str | None:
    """Read a discovered file as UTF-8 text, or ``None`` if it cannot be read."""
    try:
        return file.abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def iter_repo_paths(repo_path: Path) -> set[str]:
    """Return every tracked-or-present repo path (files and dirs), relative.

    Used by staleness checks to decide whether a referenced path exists. Both
    files and directories are included; vendor and ignored directories are
    pruned, but their *names* are still recorded so references to e.g.
    ``node_modules`` are not flagged.
    """
    repo_path = repo_path.resolve()
    ignored = _IgnoreChecker(repo_path)
    paths: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(repo_path):
        here = Path(dirpath)
        kept: list[str] = []
        for d in dirnames:
            rel = _rel(repo_path, here / d)
            paths.add(rel)  # record the name even if we prune it
            if d not in ALWAYS_SKIP and not ignored.is_ignored(here / d, is_dir=True):
                kept.append(d)
        dirnames[:] = kept
        for f in filenames:
            paths.add(_rel(repo_path, here / f))
    return paths


# --- internals -------------------------------------------------------------


def _walk_files(repo_path: Path, ignored: _IgnoreChecker) -> Iterator[Path]:
    """Yield candidate files, pruning skipped and ignored directories."""
    for dirpath, dirnames, filenames in os.walk(repo_path):
        here = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in ALWAYS_SKIP and not ignored.is_ignored(here / d, is_dir=True)
        ]
        for name in filenames:
            abs_path = here / name
            if not ignored.is_ignored(abs_path, is_dir=False):
                yield abs_path


def _is_skill_file(rel: str) -> bool:
    """True for ``.claude/skills/*/SKILL.md`` and ``.agents/skills/*/SKILL.md``."""
    parts = rel.split("/")
    if parts[-1] != "SKILL.md":
        return False
    return parts[:2] in (
        [".claude", "skills"],
        [".agents", "skills"],
    )


def _is_rule_file(rel: str, name: str) -> bool:
    """True for cursor rules, ``.cursorrules``, and copilot instructions."""
    if name == ".cursorrules":
        return True
    if rel == ".github/copilot-instructions.md":
        return True
    return rel.startswith(".cursor/rules/")


def _sort_discovered(result: Discovery) -> None:
    """Order discovered files deterministically by relative path."""
    result.instruction_files.sort(key=lambda f: f.rel_path)
    result.skill_files.sort(key=lambda f: f.rel_path)
    result.rule_files.sort(key=lambda f: f.rel_path)


def _collect_mcp_servers(repo_path: Path, ignored: _IgnoreChecker, result: Discovery) -> None:
    """Parse MCP server configs, recording warnings for malformed files."""
    for rel in _MCP_CONFIG_PATHS:
        abs_path = repo_path / rel
        if not abs_path.is_file() or ignored.is_ignored(abs_path, is_dir=False):
            continue
        servers, warning = _parse_mcp_config(abs_path, rel)
        result.mcp_servers.extend(servers)
        if warning is not None:
            result.warnings.append(warning)
    result.mcp_servers.sort(key=lambda s: (s.source_rel_path, s.name))


def _parse_mcp_config(abs_path: Path, rel: str) -> tuple[list[McpServer], str | None]:
    """Parse one MCP config file into servers plus an optional warning."""
    try:
        raw = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], f"Could not read MCP config {rel}; skipping."
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"Malformed JSON in {rel} ({exc.msg}); skipping."
    if not isinstance(data, dict):
        return [], None

    servers_obj = data.get("mcpServers")
    if not isinstance(servers_obj, dict):
        return [], None

    servers: list[McpServer] = []
    for name, spec in servers_obj.items():
        tools = _extract_tool_names(spec)
        servers.append(McpServer(name=str(name), tools=tools, source_rel_path=rel))
    return servers, None


def _extract_tool_names(spec: object) -> tuple[str, ...]:
    """Pull a tool-name list out of a server spec, tolerating shapes."""
    if not isinstance(spec, dict):
        return ()
    for key in ("tools", "allowedTools", "autoApprove"):
        value = spec.get(key)
        if isinstance(value, list):
            return tuple(str(v) for v in value if isinstance(v, str))
    return ()


def _rel(repo_path: Path, abs_path: Path) -> str:
    """Return ``abs_path`` relative to ``repo_path`` with forward slashes."""
    return abs_path.relative_to(repo_path).as_posix()


class _IgnoreChecker:
    """Checks whether paths are git-ignored, with a no-git fallback.

    When ``repo_path`` is inside a git work tree, ignore decisions come from
    git (honoring nested ``.gitignore`` files). Otherwise nothing is treated
    as ignored beyond :data:`ALWAYS_SKIP`, which the caller handles.
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo: git.Repo | None = None
        try:
            self._repo = git.Repo(repo_path, search_parent_directories=True)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            self._repo = None
        self._cache: dict[str, bool] = {}

    def is_ignored(self, abs_path: Path, *, is_dir: bool) -> bool:
        """Return whether ``abs_path`` is git-ignored."""
        if self._repo is None:
            return False
        key = abs_path.as_posix()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        probe = key + "/" if is_dir else key
        try:
            result = bool(self._repo.ignored(probe))
        except git.GitCommandError:
            result = False
        self._cache[key] = result
        return result


__all__ = [
    "ALWAYS_SKIP",
    "Discovery",
    "DiscoveredFile",
    "McpServer",
    "discover",
    "iter_repo_paths",
    "read_text",
]

"""The scan service: turn a repository into a :class:`ScanResult`.

This orchestrates discovery, parsing into :class:`ContextItem` objects, token
counting, offline staleness checks, and the monthly cost estimate. It contains
all scan business logic; the CLI layer only renders what this returns.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found]

from rentcheck.core.config import Settings, load_settings
from rentcheck.core.tokenizer import TiktokenCounter, TokenCounter
from rentcheck.repositories import file_store
from rentcheck.repositories.file_store import DiscoveredFile, Discovery, McpServer
from rentcheck.schemas.context_item import ContextItem
from rentcheck.schemas.scan_result import (
    CostEstimate,
    ScannedItem,
    ScanResult,
    StaleRef,
)


def scan_repo(
    repo_path: Path,
    *,
    settings: Settings | None = None,
    counter: TokenCounter | None = None,
) -> ScanResult:
    """Scan ``repo_path`` and return a fully populated :class:`ScanResult`."""
    repo_path = repo_path.resolve()
    settings = settings or load_settings(repo_path)
    counter = counter or TiktokenCounter()

    discovery = file_store.discover(repo_path)
    warnings = list(discovery.warnings)

    items = _build_items(discovery, counter, warnings)
    repo_paths = file_store.iter_repo_paths(repo_path)
    commands = _collect_commands(repo_path)

    scanned: list[ScannedItem] = []
    all_refs: list[StaleRef] = []
    for item in items:
        refs = _find_stale_refs(item, repo_paths, commands)
        all_refs.extend(refs)
        scanned.append(ScannedItem(item=item, stale_refs=tuple(refs)))

    total_tokens = sum(item.token_count for item in items)
    cost = _estimate_cost(total_tokens, settings)

    return ScanResult(
        repo_path=repo_path.as_posix(),
        tokenizer=counter.name,
        total_tokens=total_tokens,
        items=tuple(scanned),
        stale_refs=tuple(all_refs),
        cost=cost,
        warnings=tuple(warnings),
    )


# --- parsing ---------------------------------------------------------------


def _build_items(
    discovery: Discovery, counter: TokenCounter, warnings: list[str]
) -> list[ContextItem]:
    """Parse every discovered source into token-counted context items."""
    items: list[ContextItem] = []
    for file in discovery.instruction_files:
        text = file_store.read_text(file)
        if text is None:
            warnings.append(f"Could not read {file.rel_path}; skipping.")
            continue
        items.extend(_split_markdown(file, text, counter))
    for file in discovery.skill_files + discovery.rule_files:
        item = _build_file_item(file, counter, warnings)
        if item is not None:
            items.append(item)
    for server in discovery.mcp_servers:
        items.append(_build_mcp_item(server, counter))
    return items


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")


def _split_markdown(file: DiscoveredFile, text: str, counter: TokenCounter) -> list[ContextItem]:
    """Split an instruction file into section items by ``##``/``###`` headings.

    Content before the first heading becomes a ``preamble`` section. Each
    section's content includes its heading line.
    """
    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    current: tuple[str, list[str]] | None = None

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match is None:
            (current[1] if current is not None else preamble).append(line)
            continue
        current = (match.group(2), [line])
        sections.append(current)

    items: list[ContextItem] = []
    if preamble and "".join(preamble).strip():
        items.append(_make_section_item(file, "preamble", "(preamble)", preamble, counter))
    for title, lines in sections:
        slug = _slugify(title)
        items.append(_make_section_item(file, slug, title, lines, counter))
    return items


def _make_section_item(
    file: DiscoveredFile,
    slug: str,
    title: str,
    lines: list[str],
    counter: TokenCounter,
) -> ContextItem:
    """Build one ``section`` context item from a block of lines."""
    content = "\n".join(lines).strip("\n")
    return ContextItem(
        id=f"{file.rel_path}#{slug}",
        kind="section",
        source_path=file.rel_path,
        title=title,
        content=content,
        token_count=counter.count(content),
    )


def _build_file_item(
    file: DiscoveredFile, counter: TokenCounter, warnings: list[str]
) -> ContextItem | None:
    """Build a ``skill`` context item for a whole skill/rule file."""
    text = file_store.read_text(file)
    if text is None:
        warnings.append(f"Could not read {file.rel_path}; skipping.")
        return None
    return ContextItem(
        id=file.rel_path,
        kind="skill",
        source_path=file.rel_path,
        title=_skill_title(file.rel_path, text),
        content=text,
        token_count=counter.count(text),
    )


def _build_mcp_item(server: McpServer, counter: TokenCounter) -> ContextItem:
    """Build an ``mcp_server`` context item from a discovered server."""
    tool_line = ", ".join(server.tools) if server.tools else "(tools not declared)"
    content = f"MCP server: {server.name}\nTools: {tool_line}"
    return ContextItem(
        id=f"mcp:{server.name}",
        kind="mcp_server",
        source_path=server.source_rel_path,
        title=server.name,
        content=content,
        token_count=counter.count(content),
    )


def _skill_title(rel_path: str, text: str) -> str:
    """Pick a human title for a skill file: its skills-dir name or first heading."""
    parts = rel_path.split("/")
    if parts[-1] == "SKILL.md" and len(parts) >= 2:
        return parts[-2]
    for line in text.splitlines():
        match = re.match(r"^#{1,3}\s+(.*\S)", line)
        if match:
            return match.group(1)
    return rel_path


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Turn a heading into a stable, readable id fragment."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug or "section"


# --- staleness -------------------------------------------------------------

# A path-like token: contains a slash or a dotted filename, no spaces/quotes.
_PATH_RE = re.compile(r"(?<![\w./-])([A-Za-z0-9_.][\w./-]*/[\w./-]+|[\w-]+\.[A-Za-z0-9]+)")
# A command invocation like ``npm run build`` / ``pnpm test`` / ``make lint``.
_COMMAND_RE = re.compile(
    r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?([a-zA-Z0-9:_-]+)\b|\bmake\s+([a-zA-Z0-9:_-]+)\b"
)
# Common words that look path-like but are prose, not repo paths.
_PATH_IGNORE_SUFFIXES = (".md",)
_RUNNER_SUBCOMMANDS = frozenset(
    {"run", "install", "ci", "test", "start", "build", "exec", "add", "remove"}
)


def _find_stale_refs(item: ContextItem, repo_paths: set[str], commands: set[str]) -> list[StaleRef]:
    """Return references in ``item`` that do not resolve in the repository.

    Conservative by design: only flags clearly path-like or command-like
    tokens, and never flags a reference that resolves to any existing path or
    defined script. Fewer false positives beats more findings.
    """
    refs: list[StaleRef] = []
    seen: set[tuple[str, str]] = set()
    text = _strip_code_spans(item.content)

    for match in _PATH_RE.finditer(text):
        raw = match.group(1).rstrip(".,);:")
        if not _is_checkable_path(raw) or raw in repo_paths:
            continue
        if _path_exists_loosely(raw, repo_paths):
            continue
        key = ("path", raw)
        if key not in seen:
            seen.add(key)
            refs.append(StaleRef(kind="path", reference=raw, item_id=item.id))

    for match in _COMMAND_RE.finditer(text):
        script = match.group(1) or match.group(2)
        if script is None or script in _RUNNER_SUBCOMMANDS or script in commands:
            continue
        key = ("command", script)
        if key not in seen:
            seen.add(key)
            refs.append(StaleRef(kind="command", reference=script, item_id=item.id))

    return refs


def _strip_code_spans(text: str) -> str:
    """Drop fenced code blocks but keep inline-code content for scanning."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line.replace("`", " "))
    return "\n".join(out)


def _is_checkable_path(raw: str) -> bool:
    """Whether ``raw`` is specific enough to check for existence."""
    if raw.startswith(("http://", "https://", "//")):
        return False
    if raw.lower().endswith(_PATH_IGNORE_SUFFIXES) and "/" not in raw:
        return False
    # Require either a directory separator or a filename with a short extension.
    if "/" in raw:
        return True
    name, _, ext = raw.rpartition(".")
    return bool(name) and 1 <= len(ext) <= 4 and ext.isalnum()


def _path_exists_loosely(raw: str, repo_paths: set[str]) -> bool:
    """Match a reference against repo paths, tolerating ``./`` and trailing ``/``."""
    candidate = raw.lstrip("./").rstrip("/")
    if candidate in repo_paths:
        return True
    # A bare filename that exists anywhere in the tree is not stale.
    if "/" not in candidate:
        return any(p.rsplit("/", 1)[-1] == candidate for p in repo_paths)
    return False


# --- commands defined in the repo -----------------------------------------


def _collect_commands(repo_path: Path) -> set[str]:
    """Collect script/target names defined by package.json, Makefile, pyproject."""
    commands: set[str] = set()
    commands |= _npm_scripts(repo_path / "package.json")
    commands |= _makefile_targets(repo_path / "Makefile")
    commands |= _pyproject_scripts(repo_path / "pyproject.toml")
    return commands


def _npm_scripts(path: Path) -> set[str]:
    """Return script names from a package.json ``scripts`` object."""
    data = _safe_load_json(path)
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if isinstance(scripts, dict):
        return {str(k) for k in scripts}
    return set()


_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")


def _makefile_targets(path: Path) -> set[str]:
    """Return target names from a Makefile."""
    text = _safe_read(path)
    if text is None:
        return set()
    targets: set[str] = set()
    for line in text.splitlines():
        match = _MAKE_TARGET_RE.match(line)
        if match and match.group(1) != ".PHONY":
            targets.add(match.group(1))
    return targets


def _pyproject_scripts(path: Path) -> set[str]:
    """Return console-script names from ``[project.scripts]`` in pyproject.toml."""
    text = _safe_read(path)
    if text is None:
        return set()
    try:
        data = tomllib.loads(text)
    except ValueError:
        return set()
    scripts = data.get("project", {}).get("scripts", {})
    return {str(k) for k in scripts} if isinstance(scripts, dict) else set()


def _safe_read(path: Path) -> str | None:
    """Read a file as text, returning ``None`` on any error."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _safe_load_json(path: Path) -> object:
    """Load JSON from a file, returning ``{}`` on any error."""
    text = _safe_read(path)
    if text is None:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# --- cost ------------------------------------------------------------------


def _estimate_cost(total_tokens: int, settings: Settings) -> CostEstimate:
    """Estimate monthly input-token cost of loading all context per request."""
    per_request_musd = total_tokens / 1_000_000 * settings.price_per_mtok
    monthly = per_request_musd * settings.requests_per_day * settings.days_per_month
    return CostEstimate(
        total_tokens=total_tokens,
        price_per_mtok=settings.price_per_mtok,
        requests_per_day=settings.requests_per_day,
        days_per_month=settings.days_per_month,
        monthly_usd=round(monthly, 2),
    )


__all__ = ["scan_repo"]

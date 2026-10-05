"""Tests for the scan service: parsing, ids, staleness, and cost math."""

from __future__ import annotations

from pathlib import Path

import pytest

from rentcheck.core.config import Settings
from rentcheck.schemas.scan_result import ScanResult
from rentcheck.services.scan_service import scan_repo


class WordCounter:
    """A deterministic :class:`TokenCounter`: one token per whitespace word."""

    name = "words"

    def count(self, text: str) -> int:
        return len(text.split())


def _run(repo: Path, **overrides: object) -> ScanResult:
    settings = Settings(**overrides)  # type: ignore[arg-type]
    return scan_repo(repo, settings=settings, counter=WordCounter())


# --- parsing & ids ---------------------------------------------------------


def test_splits_sections_by_heading(sample_repo: Path) -> None:
    result = _run(sample_repo)
    ids = {s.item.id for s in result.items}
    assert "AGENTS.md#build-and-test" in ids
    assert "AGENTS.md#testing-conventions" in ids
    assert "AGENTS.md#deployment" in ids


def test_content_before_first_heading_is_preamble(sample_repo: Path) -> None:
    result = _run(sample_repo)
    preambles = [s for s in result.items if s.item.id == "AGENTS.md#preamble"]
    assert len(preambles) == 1
    assert preambles[0].item.kind == "section"
    assert "exercise rentcheck" in preambles[0].item.content


def test_ids_are_stable_across_runs(sample_repo: Path) -> None:
    first = {s.item.id for s in _run(sample_repo).items}
    second = {s.item.id for s in _run(sample_repo).items}
    assert first == second


def test_skill_file_is_one_item(sample_repo: Path) -> None:
    result = _run(sample_repo)
    skills = [s for s in result.items if s.item.kind == "skill"]
    ids = {s.item.id for s in skills}
    assert ".claude/skills/testing/SKILL.md" in ids
    testing = next(s for s in skills if s.item.id == ".claude/skills/testing/SKILL.md")
    assert testing.item.title == "testing"


def test_mcp_server_is_one_item(sample_repo: Path) -> None:
    result = _run(sample_repo)
    mcp = {s.item.id for s in result.items if s.item.kind == "mcp_server"}
    assert mcp == {"mcp:filesystem", "mcp:github"}


# --- staleness -------------------------------------------------------------


def test_flags_the_deliberately_stale_path(sample_repo: Path) -> None:
    result = _run(sample_repo)
    stale = {(r.kind, r.reference) for r in result.stale_refs}
    assert ("path", "scripts/legacy_migrate.sh") in stale


def test_no_false_positive_on_existing_paths(sample_repo: Path) -> None:
    result = _run(sample_repo)
    refs = {r.reference for r in result.stale_refs}
    # These are mentioned in AGENTS.md/CLAUDE.md and do exist.
    for existing in ("src/utils.js", "src/index.js", "deploy.config.json", "build.js"):
        assert existing not in refs


def test_no_false_positive_on_defined_commands(sample_repo: Path) -> None:
    result = _run(sample_repo)
    commands = {r.reference for r in result.stale_refs if r.kind == "command"}
    # build/test are npm scripts; deploy is a Make target — all defined.
    assert not ({"build", "test", "deploy"} & commands)


def test_flags_undefined_command(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Tasks\nRun `npm run nonexistent-script` to do the thing.\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"scripts": {"build": "x"}}', encoding="utf-8")
    result = _run(tmp_path)
    stale = {(r.kind, r.reference) for r in result.stale_refs}
    assert ("command", "nonexistent-script") in stale


def test_ignores_urls_and_prose(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Links\nSee https://example.com/docs for details. Visit the site.\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.stale_refs == ()


# --- cost ------------------------------------------------------------------


def test_cost_math(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("## S\n" + "word " * 1000, encoding="utf-8")
    result = _run(
        tmp_path,
        price_per_mtok=2.0,
        requests_per_day=100,
        days_per_month=30,
    )
    # 1001 tokens (heading word "S" + 1000) * 2/1e6 * 100 * 30
    expected = round(result.total_tokens / 1_000_000 * 2.0 * 100 * 30, 2)
    assert result.cost.monthly_usd == expected
    assert result.cost.price_per_mtok == 2.0
    assert result.cost.requests_per_day == 100


def test_total_tokens_is_sum_of_items(sample_repo: Path) -> None:
    result = _run(sample_repo)
    assert result.total_tokens == sum(s.item.token_count for s in result.items)


# --- integration with the real tokenizer -----------------------------------


def test_real_tokenizer_produces_positive_counts(sample_repo: Path) -> None:
    result = scan_repo(sample_repo, settings=Settings())
    assert result.tokenizer == "cl100k_base"
    assert result.total_tokens > 0
    assert all(s.item.token_count > 0 for s in result.items)


def test_empty_repo_does_not_crash(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.total_tokens == 0
    assert result.items == ()
    assert result.cost.monthly_usd == 0.0


@pytest.mark.parametrize("filename", ["AGENTS.md", "CLAUDE.md"])
def test_unreadable_file_warns(tmp_path: Path, filename: str) -> None:
    # Write invalid UTF-8 so read_text fails cleanly.
    (tmp_path / filename).write_bytes(b"## S\n\xff\xfe not utf8")
    result = _run(tmp_path)
    assert any(filename in w for w in result.warnings)

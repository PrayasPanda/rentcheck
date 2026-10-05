"""Tests for discovery of context sources and MCP configs."""

from __future__ import annotations

from pathlib import Path

from rentcheck.repositories import file_store


def test_discovers_instruction_files(sample_repo: Path) -> None:
    discovery = file_store.discover(sample_repo)
    rels = {f.rel_path for f in discovery.instruction_files}
    assert rels == {"AGENTS.md", "CLAUDE.md"}


def test_discovers_skill_files(sample_repo: Path) -> None:
    discovery = file_store.discover(sample_repo)
    rels = {f.rel_path for f in discovery.skill_files}
    assert rels == {
        ".claude/skills/deploy/SKILL.md",
        ".claude/skills/testing/SKILL.md",
    }


def test_parses_mcp_servers_with_and_without_tools(sample_repo: Path) -> None:
    discovery = file_store.discover(sample_repo)
    by_name = {s.name: s for s in discovery.mcp_servers}
    assert set(by_name) == {"filesystem", "github"}
    assert by_name["filesystem"].tools == ("read_file", "write_file", "list_directory")
    assert by_name["github"].tools == ()
    assert by_name["filesystem"].source_rel_path == ".mcp.json"


def test_malformed_mcp_json_warns_not_crashes(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{ not valid json", encoding="utf-8")
    discovery = file_store.discover(tmp_path)
    assert discovery.mcp_servers == []
    assert any("Malformed JSON" in w for w in discovery.warnings)


def test_skips_vendor_and_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "AGENTS.md").write_text("vendor", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    discovery = file_store.discover(tmp_path)
    assert {f.rel_path for f in discovery.instruction_files} == {"AGENTS.md"}


def test_iter_repo_paths_records_files_and_dirs(sample_repo: Path) -> None:
    paths = file_store.iter_repo_paths(sample_repo)
    assert "src/index.js" in paths
    assert "src" in paths
    assert "deploy.config.json" in paths

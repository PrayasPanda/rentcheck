"""The :class:`ContextItem` schema: one removable unit of agent context."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ContextKind = Literal["section", "skill", "mcp_server"]


class ContextItem(BaseModel, frozen=True):
    """A single testable unit of agent context.

    A context item is one thing that could be removed from an agent's
    configuration: a section of AGENTS.md, a skill, or an MCP server.
    """

    id: str
    kind: ContextKind
    source_path: str
    title: str
    content: str
    token_count: int

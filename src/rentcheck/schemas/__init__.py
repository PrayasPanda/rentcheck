"""Pydantic models shared across rentcheck."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ElementKind(str, Enum):
    """The kind of agent-config element being tested."""

    SECTION = "section"
    SKILL = "skill"
    MCP_SERVER = "mcp_server"


class Element(BaseModel):
    """A single testable unit of agent configuration."""

    id: str
    kind: ElementKind
    name: str
    tokens: int = 0

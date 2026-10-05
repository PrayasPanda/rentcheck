"""Schemas describing the result of ``rentcheck scan``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rentcheck.schemas.context_item import ContextItem

StaleKind = Literal["path", "command"]


class StaleRef(BaseModel, frozen=True):
    """A reference in context text that could not be resolved in the repo.

    A stale reference is a path or command name mentioned in an agent
    instruction that does not exist, suggesting the context is out of date.
    """

    kind: StaleKind
    #: The exact text referenced, e.g. ``"scripts/build.sh"`` or ``"npm test"``.
    reference: str
    #: ``id`` of the :class:`ContextItem` the reference appears in.
    item_id: str


class ScannedItem(BaseModel, frozen=True):
    """A :class:`ContextItem` enriched with scan findings."""

    item: ContextItem
    stale_refs: tuple[StaleRef, ...] = ()


class CostEstimate(BaseModel, frozen=True):
    """An estimated monthly cost of loading the scanned context every request.

    All figures are estimates; actual cost depends on the model, caching, and
    real request volume.
    """

    total_tokens: int
    price_per_mtok: float
    requests_per_day: int
    days_per_month: int
    monthly_usd: float


class ScanResult(BaseModel, frozen=True):
    """The complete result of scanning a repository for agent context."""

    repo_path: str
    tokenizer: str
    total_tokens: int
    items: tuple[ScannedItem, ...]
    stale_refs: tuple[StaleRef, ...]
    cost: CostEstimate
    #: Non-fatal problems encountered while scanning (unreadable files, etc.).
    warnings: tuple[str, ...] = ()


__all__ = [
    "CostEstimate",
    "ScanResult",
    "ScannedItem",
    "StaleKind",
    "StaleRef",
]

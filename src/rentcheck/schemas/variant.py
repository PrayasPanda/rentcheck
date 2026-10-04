"""The :class:`Variant` schema: one configuration arm of an ablation test."""

from __future__ import annotations

from pydantic import BaseModel


class Variant(BaseModel, frozen=True):
    """One configuration arm tested against a task.

    The baseline keeps all context. Other variants remove a single item
    (``removed_item_id``). A sham variant removes nothing but is labelled as a
    removal, to measure placebo effects.
    """

    id: str
    name: str
    removed_item_id: str | None = None
    is_baseline: bool = False
    is_sham: bool = False

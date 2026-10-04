"""The :class:`RunResult` schema: the outcome of one agent trial."""

from __future__ import annotations

from pydantic import BaseModel


class RunResult(BaseModel, frozen=True):
    """The outcome of running one variant against one task, once.

    Multiple trials per (task, variant) pair are averaged downstream to
    estimate pass rate and cost with confidence intervals.
    """

    task_id: str
    variant_id: str
    trial: int
    passed: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_s: float
    error: str | None = None

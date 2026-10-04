"""The :class:`ItemVerdict` schema: the keep/cut decision for one context item."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Verdict = Literal["KEEP", "CUT", "INCONCLUSIVE"]


class ItemVerdict(BaseModel, frozen=True):
    """The measured effect of removing one context item.

    ``delta_pass_rate`` is the change in pass rate (removed minus baseline),
    with a confidence interval ``[ci_low, ci_high]``. ``delta_tokens_pct`` is
    the percent change in token usage. The ``verdict`` summarises the call.
    """

    item_id: str
    delta_pass_rate: float
    ci_low: float
    ci_high: float
    delta_tokens_pct: float
    verdict: Verdict

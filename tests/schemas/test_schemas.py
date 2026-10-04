"""Tests for schema validation and JSON round-trips."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from rentcheck.schemas import (
    ContextItem,
    ItemVerdict,
    RunResult,
    Task,
    Variant,
)

SAMPLES: list[BaseModel] = [
    ContextItem(
        id="s1",
        kind="section",
        source_path="AGENTS.md",
        title="Testing",
        content="Run pytest.",
        token_count=12,
    ),
    Task(
        id="t1",
        commit_sha="abc",
        parent_sha="def",
        description="Fix the bug",
        files_changed=("a.py", "b.py"),
        test_command="pytest",
        fail_tests=("test_a",),
        pass_tests=("test_b",),
    ),
    Variant(id="v1", name="baseline", is_baseline=True),
    RunResult(
        task_id="t1",
        variant_id="v1",
        trial=0,
        passed=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.0,
        duration_s=1.5,
    ),
    ItemVerdict(
        item_id="s1",
        delta_pass_rate=-0.1,
        ci_low=-0.2,
        ci_high=0.0,
        delta_tokens_pct=-5.0,
        verdict="CUT",
    ),
]


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model


def test_context_item_rejects_bad_kind() -> None:
    with pytest.raises(ValidationError):
        ContextItem(
            id="x",
            kind="bogus",  # type: ignore[arg-type]
            source_path="p",
            title="t",
            content="c",
            token_count=1,
        )


def test_item_verdict_rejects_bad_verdict() -> None:
    with pytest.raises(ValidationError):
        ItemVerdict(
            item_id="x",
            delta_pass_rate=0.0,
            ci_low=0.0,
            ci_high=0.0,
            delta_tokens_pct=0.0,
            verdict="MAYBE",  # type: ignore[arg-type]
        )


def test_frozen_models_are_immutable() -> None:
    v = Variant(id="v1", name="baseline")
    with pytest.raises(ValidationError):
        v.name = "changed"  # type: ignore[misc]

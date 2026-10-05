"""Pydantic models shared across rentcheck."""

from __future__ import annotations

from rentcheck.schemas.context_item import ContextItem, ContextKind
from rentcheck.schemas.item_verdict import ItemVerdict, Verdict
from rentcheck.schemas.mine_result import MinedTask, MineResult
from rentcheck.schemas.run_result import RunResult
from rentcheck.schemas.scan_result import (
    CostEstimate,
    ScannedItem,
    ScanResult,
    StaleKind,
    StaleRef,
)
from rentcheck.schemas.task import Task
from rentcheck.schemas.test_run_result import TestRunResult
from rentcheck.schemas.validation import DropReason, TaskStatus, ValidationResult
from rentcheck.schemas.variant import Variant

__all__ = [
    "ContextItem",
    "ContextKind",
    "CostEstimate",
    "DropReason",
    "ItemVerdict",
    "MineResult",
    "MinedTask",
    "RunResult",
    "ScanResult",
    "ScannedItem",
    "StaleKind",
    "StaleRef",
    "Task",
    "TaskStatus",
    "TestRunResult",
    "ValidationResult",
    "Variant",
    "Verdict",
]

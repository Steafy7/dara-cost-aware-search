from __future__ import annotations

from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from audit_live_reachability import build_audit


def _record(
    *,
    pattern_id: str = "p1",
    arm: str = "h1_dara",
    budget: int = 5,
    phases: list[str] | None = None,
    gt: list[str] | None = None,
    calls: int = 2,
    stop: str = "branch_budget_exhausted",
) -> dict[str, object]:
    return {
        "pattern_id": pattern_id,
        "kind": "mixed",
        "arm": arm,
        "budget": budget,
        "top1_phase_ids": phases or [],
        "gt_phase_ids": gt or ["a"],
        "branch_calls": calls,
        "actual_costs": [1, 1],
        "stop_reason": stop,
    }


def test_build_audit_distinguishes_recoverable_and_endpoint_misses() -> None:
    finite = {
        "benchmark_id": "test",
        "method": "finite",
        "records": [
            _record(budget=5, phases=[], calls=1),
            _record(budget=10, phases=["a"], calls=3),
        ],
    }
    endpoint = {
        "benchmark_id": "test",
        "method": "endpoint",
        "records": [
            _record(budget=1000, phases=["a"], calls=4, stop="frontier_exhausted"),
        ],
    }

    audit = build_audit(finite, endpoint)

    assert audit["pattern_count"] == 1
    assert audit["summary_by_budget"] == {
        "5": {
            "runs": 1,
            "finite_exact": 0,
            "endpoint_exact": 1,
            "finite_matches_endpoint": 0,
            "recoverable_misses": 1,
            "endpoint_not_exact": 0,
            "branch_calls": {"min": 1, "max": 1, "total": 1},
            "endpoint_branch_calls": {"min": 4, "max": 4, "total": 4},
        },
        "10": {
            "runs": 1,
            "finite_exact": 1,
            "endpoint_exact": 1,
            "finite_matches_endpoint": 1,
            "recoverable_misses": 0,
            "endpoint_not_exact": 0,
            "branch_calls": {"min": 3, "max": 3, "total": 3},
            "endpoint_branch_calls": {"min": 4, "max": 4, "total": 4},
        },
    }


def test_build_audit_rejects_non_exhaustive_endpoint() -> None:
    finite = {"benchmark_id": "test", "records": [_record()]}
    endpoint = {
        "benchmark_id": "test",
        "records": [_record(budget=20, stop="branch_budget_exhausted")],
    }

    with pytest.raises(ValueError, match="frontier_exhausted"):
        build_audit(finite, endpoint)


def test_build_audit_rejects_mismatched_benchmark() -> None:
    with pytest.raises(ValueError, match="benchmark ids differ"):
        build_audit(
            {"benchmark_id": "finite", "records": [_record()]},
            {"benchmark_id": "endpoint", "records": [_record(stop="frontier_exhausted")]},
        )

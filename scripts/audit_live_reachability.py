#!/usr/bin/env python3
"""Join finite-budget live runs with a high-budget endpoint audit.

The endpoint is deliberately supplied as a separate artifact.  Running the
live DARA tree is expensive, and separating execution from analysis makes the
comparison reproducible without silently rerunning or changing an arm's
semantics.  The endpoint must have stopped at ``frontier_exhausted`` (rather
than at a branch budget) to be treated as an exhaustive trajectory.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"{path} is not a live comparison artifact")
    return payload


def _key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record["pattern_id"]), str(record["arm"])


def _phase_ids(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(value) for value in record.get("top1_phase_ids", [])))


def build_audit(
    finite: dict[str, Any],
    endpoint: dict[str, Any],
) -> dict[str, Any]:
    """Build a per-pattern reachability report from two live artifacts.

    ``endpoint`` is an exhaustive trajectory for each pattern/arm, while
    ``finite`` contains one or more bounded budgets.  A GT miss at the
    endpoint is reported as ``endpoint_not_exact``; it is evidence about this
    DARA trajectory, not a proof that a different proposer could not reach GT.
    """

    if finite.get("benchmark_id") != endpoint.get("benchmark_id"):
        raise ValueError("finite and endpoint benchmark ids differ")
    finite_records = [_as_record(item) for item in finite["records"]]
    endpoint_records = [_as_record(item) for item in endpoint["records"]]
    endpoint_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in endpoint_records:
        key = _key(record)
        if key in endpoint_by_key:
            raise ValueError(f"duplicate endpoint record for {key}")
        if str(record.get("stop_reason")) != "frontier_exhausted":
            raise ValueError(
                f"endpoint {key} stopped at {record.get('stop_reason')!r}; "
                "use a frontier_exhausted high-budget artifact"
            )
        endpoint_by_key[key] = record

    rows: list[dict[str, Any]] = []
    finite_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in finite_records:
        key = _key(record)
        if key not in endpoint_by_key:
            raise ValueError(f"finite record {key} has no endpoint record")
        finite_by_key[key].append(record)

    for key, records in finite_by_key.items():
        endpoint_record = endpoint_by_key[key]
        endpoint_phases = _phase_ids(endpoint_record)
        endpoint_gt = tuple(sorted(str(value) for value in endpoint_record.get("gt_phase_ids", [])))
        endpoint_exact = endpoint_phases == endpoint_gt
        for record in sorted(records, key=lambda item: int(item["budget"])):
            phases = _phase_ids(record)
            gt = tuple(sorted(str(value) for value in record.get("gt_phase_ids", [])))
            oracle_calls = int(endpoint_record["branch_calls"])
            finite_calls = int(record["branch_calls"])
            rows.append(
                {
                    "pattern_id": key[0],
                    "kind": record.get("kind"),
                    "arm": key[1],
                    "budget": int(record["budget"]),
                    "finite_top1_phase_ids": list(phases),
                    "finite_top1_exact": phases == gt,
                    "finite_matches_endpoint": phases == endpoint_phases,
                    "endpoint_top1_phase_ids": list(endpoint_phases),
                    "endpoint_exact": endpoint_exact,
                    "endpoint_reachability": (
                        "gt_reached" if endpoint_exact else "endpoint_not_exact"
                    ),
                    "branch_calls": finite_calls,
                    "endpoint_branch_calls": oracle_calls,
                    "branch_call_gap_to_endpoint": oracle_calls - finite_calls,
                    "actual_costs": list(record.get("actual_costs", [])),
                    "endpoint_actual_costs": list(endpoint_record.get("actual_costs", [])),
                    "stop_reason": record.get("stop_reason"),
                    "endpoint_stop_reason": endpoint_record.get("stop_reason"),
                }
            )

    # Keep ordering deterministic even if input artifacts were generated with
    # a different pattern/arm loop order.
    rows.sort(key=lambda item: (item["pattern_id"], item["arm"], item["budget"]))
    by_budget: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_budget[int(row["budget"])].append(row)

    summary_by_budget: dict[str, dict[str, Any]] = {}
    for budget, budget_rows in sorted(by_budget.items()):
        summary_by_budget[str(budget)] = {
            "runs": len(budget_rows),
            "finite_exact": sum(bool(row["finite_top1_exact"]) for row in budget_rows),
            "endpoint_exact": sum(bool(row["endpoint_exact"]) for row in budget_rows),
            "finite_matches_endpoint": sum(
                bool(row["finite_matches_endpoint"]) for row in budget_rows
            ),
            "recoverable_misses": sum(
                not row["finite_top1_exact"] and row["endpoint_exact"]
                for row in budget_rows
            ),
            "endpoint_not_exact": sum(not row["endpoint_exact"] for row in budget_rows),
            "branch_calls": {
                "min": min(int(row["branch_calls"]) for row in budget_rows),
                "max": max(int(row["branch_calls"]) for row in budget_rows),
                "total": sum(int(row["branch_calls"]) for row in budget_rows),
            },
            "endpoint_branch_calls": {
                "min": min(int(row["endpoint_branch_calls"]) for row in budget_rows),
                "max": max(int(row["endpoint_branch_calls"]) for row in budget_rows),
                "total": sum(int(row["endpoint_branch_calls"]) for row in budget_rows),
            },
        }

    return {
        "benchmark_id": finite.get("benchmark_id"),
        "method": "live-DARA-reachability-audit",
        "finite_artifact": finite.get("method"),
        "endpoint_artifact": endpoint.get("method"),
        "endpoint_budget": max(
            (int(record["budget"]) for record in endpoint_records),
            default=None,
        ),
        "endpoint_stop_requirement": "frontier_exhausted",
        "pattern_count": len({row["pattern_id"] for row in rows}),
        "arm_count": len({row["arm"] for row in rows}),
        "budgets": sorted(by_budget),
        "summary_by_budget": summary_by_budget,
        "records": rows,
    }


def _as_record(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("live artifact contains a non-object record")
    required = ("pattern_id", "arm", "budget", "branch_calls", "stop_reason")
    missing = [field for field in required if field not in item]
    if missing:
        raise ValueError(f"live record missing fields: {', '.join(missing)}")
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finite", type=Path, required=True)
    parser.add_argument("--endpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    audit = build_audit(_load(args.finite), _load(args.endpoint))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "patterns": audit["pattern_count"],
                "arms": audit["arm_count"],
                "budgets": audit["budgets"],
                "output": str(args.output.resolve()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

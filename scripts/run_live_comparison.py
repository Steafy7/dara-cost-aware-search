#!/usr/bin/env python3
"""Run the live Original-DARA versus H1-DARA comparison on a manifest."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time

from dara_cost_aware.live_dara import LiveDaraPilot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budgets", type=int, nargs="+", default=[5, 10, 15, 20])
    parser.add_argument("--max-phases", type=int, default=3)
    parser.add_argument("--no-express", action="store_true")
    parser.add_argument("--no-angular-cut", action="store_true")
    parser.add_argument("--root-zero-match-recall", action="store_true")
    args = parser.parse_args()

    logging.disable(logging.INFO)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records: list[dict[str, object]] = []
    started = time.perf_counter()
    for pattern in manifest["patterns"]:
        pilot = LiveDaraPilot(
            pattern_path=Path(pattern["xy_path"]),
            phase_paths=tuple(Path(path) for path in manifest["phase_bank"]),
            max_phases=args.max_phases,
            express_mode=not args.no_express,
            enable_angular_cut=not args.no_angular_cut,
            root_zero_match_recall=args.root_zero_match_recall,
        )
        ground_truth = tuple(sorted(str(phase) for phase in pattern["gt_phase_ids"]))
        for result in pilot.run_budgets(budgets=tuple(args.budgets)):
            records.append(
                {
                    "pattern_id": pattern["pattern_id"],
                    "kind": pattern.get("kind"),
                    "arm": result.arm.value,
                    "budget": result.budget,
                    "branch_calls": result.branch_calls,
                    "top1_phase_ids": list(result.top1_phase_ids),
                    "gt_phase_ids": list(ground_truth),
                    "top1_exact": result.top1_phase_ids == ground_truth,
                    "selected_action_ids": list(result.selected_action_ids),
                    "stop_reason": result.stop_reason,
                    "actual_costs": list(result.actual_costs),
                    "cost_mismatches": [list(item) for item in result.cost_mismatches],
                }
            )

    payload = {
        "benchmark_id": manifest.get("benchmark_id", "unknown"),
        "method": "live-DARA-search-tree-original-vs-H1-DARA",
        "source": manifest.get("source", "external manifest"),
        "phase_bank_size": len(manifest["phase_bank"]),
        "max_phases": args.max_phases,
        "express_mode": not args.no_express,
        "enable_angular_cut": not args.no_angular_cut,
        "root_zero_match_recall": args.root_zero_match_recall,
        "budgets": list(args.budgets),
        "pattern_count": len(manifest["patterns"]),
        "branch_cost_mismatch_count": sum(
            bool(row["cost_mismatches"]) for row in records
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "patterns": payload["pattern_count"],
        "records": len(records),
        "cost_mismatches": payload["branch_cost_mismatch_count"],
        "elapsed_seconds": payload["elapsed_seconds"],
        "output": str(args.output.resolve()),
    }))
    return 0 if payload["branch_cost_mismatch_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

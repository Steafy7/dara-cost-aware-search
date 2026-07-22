#!/usr/bin/env python3
"""Run or resume the ground-truth-isolated Original-DARA XRED trace."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dara_cost_aware.xred_dispatch import run_xred_trace_isolated
from dara_cost_aware.xred_trace import run_xred_trace_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--branch-budget", type=int, default=1_000_000)
    parser.add_argument("--worker-pattern-id", help=argparse.SUPPRESS)
    parser.add_argument("--worker-config", help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.disable(logging.INFO)
    worker_mode = args.worker_pattern_id is not None or args.worker_config is not None
    if worker_mode:
        if args.worker_pattern_id is None or args.worker_config is None:
            parser.error("worker pattern and configuration must be supplied together")
        progress = run_xred_trace_job(
            args.manifest,
            args.output_root,
            pattern_id=args.worker_pattern_id,
            config_name=args.worker_config,
            branch_budget=args.branch_budget,
        )
        return 0 if progress["status"] == "complete" else 1

    summary = run_xred_trace_isolated(
        args.manifest,
        args.output_root,
        branch_budget=args.branch_budget,
        worker_script=Path(__file__),
    )
    headline = {
        key: summary[key] for key in ("completed_jobs", "error_jobs", "pending_jobs", "total_jobs")
    }
    print(json.dumps(headline, sort_keys=True))
    return 0 if summary["error_jobs"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

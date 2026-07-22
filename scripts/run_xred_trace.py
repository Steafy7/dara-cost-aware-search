#!/usr/bin/env python3
"""Run or resume the ground-truth-isolated Original-DARA XRED trace."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dara_cost_aware.xred_trace import run_xred_trace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--branch-budget", type=int, default=1_000_000)
    args = parser.parse_args()
    logging.disable(logging.INFO)
    summary = run_xred_trace(
        args.manifest,
        args.output_root,
        branch_budget=args.branch_budget,
    )
    print(json.dumps({key: summary[key] for key in ("completed_jobs", "error_jobs", "total_jobs")}, sort_keys=True))
    return 0 if summary["error_jobs"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

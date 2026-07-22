#!/usr/bin/env python3
"""Prepare the complete primary XRED cohort with labels kept offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dara_cost_aware.xred_trace import build_xred_cohort


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_manifest, labels = build_xred_cohort(args.source, args.output)
    payload = {"run_manifest": str(run_manifest), "labels": str(labels)}
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

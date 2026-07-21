"""Audit a portable ten-pattern frozen-state manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .frozen_state import audit_frozen_pilot_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the external frozen post-initialization state and "
            "singleton-cache manifest without running branch search."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_frozen_pilot_manifest(args.manifest, args.artifact_root)
    print(
        json.dumps(
            {
                "branch_search_call_count": audit.branch_search_call_count,
                "checked_artifact_count": audit.checked_artifact_count,
                "pattern_ids": list(audit.pattern_ids),
                "status": "valid",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

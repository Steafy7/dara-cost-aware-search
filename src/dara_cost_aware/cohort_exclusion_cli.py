from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from dara_cost_aware.artifacts import canonical_json_bytes
from dara_cost_aware.cohort_exclusion import (
    CohortCollisionError,
    StructureRef,
    enforce_structure_exclusion,
)


_CANDIDATES = (
    (
        "COD:1000017@303120",
        "cod_1000017_303120.cif",
        "4013310ed8c8d50d1c86076e8d99ef0ed2595ee66c47158bbf4e86fd3afbce00",
    ),
    (
        "COD:1528915@176429",
        "cod_1528915_176429.cif",
        "708f9de65ab399d4e8e750f9fa72ee06753a8fc69fcec6b4a64e5d7134ad90bd",
    ),
)
_EXCLUSIONS = (
    (
        "excluded:xred:sha256:ab933752",
        "xred_1000017_3a601025.cif",
        "ab9337523a55bd4df615f2c89ccfddc82dfb991b4eb3216120691ff4e893d914",
    ),
    (
        "excluded:xred:sha256:dcce8b6d",
        "xred_1528915_3a601025.cif",
        "dcce8b6ded2f51ad7bd7aaf65bb59efadc5308d8e54cca1e85fa336acb337d29",
    ),
)


def _references(
    root: Path,
    values: tuple[tuple[str, str, str], ...],
) -> tuple[StructureRef, ...]:
    return tuple(
        StructureRef(identity=identity, path=root / filename, expected_sha256=digest)
        for identity, filename, digest in values
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce the fail-closed COD-SR-40-v1/XRED collision audit."
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)

    try:
        audit = enforce_structure_exclusion(
            _references(arguments.input_root, _CANDIDATES),
            _references(arguments.input_root, _EXCLUSIONS),
        )
    except CohortCollisionError as error:
        audit = error.audit
        exit_code = 2
    else:
        exit_code = 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(canonical_json_bytes(audit.to_json()))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

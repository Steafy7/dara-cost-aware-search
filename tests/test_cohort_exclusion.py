from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure

from dara_cost_aware.cohort_exclusion import (
    CohortCollisionError,
    StructureExclusionValidationError,
    StructureRef,
    audit_structure_exclusion,
)


def _write_ref(
    path: Path,
    *,
    identity: str,
    structure: Structure,
    comment: str = "",
) -> StructureRef:
    payload = (comment + structure.to(fmt="cif")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return StructureRef(
        identity=identity,
        path=path,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _collision_pairs(tmp_path: Path) -> tuple[tuple[StructureRef, ...], tuple[StructureRef, ...]]:
    alumina = Structure(
        Lattice.hexagonal(4.76, 12.99),
        ["Al", "Al", "O", "O", "O"],
        [
            (0.0, 0.0, 0.352),
            (0.0, 0.0, 0.648),
            (0.306, 0.0, 0.25),
            (0.0, 0.306, 0.25),
            (0.694, 0.694, 0.25),
        ],
    )
    tungsten_oxide = Structure(
        Lattice.monoclinic(7.30, 7.54, 7.69, 90.9),
        ["W", "O", "O", "O"],
        [
            (0.25, 0.25, 0.25),
            (0.50, 0.25, 0.25),
            (0.25, 0.50, 0.25),
            (0.25, 0.25, 0.50),
        ],
    )
    candidates = (
        _write_ref(
            tmp_path / "candidate-1000017.cif",
            identity="COD:1000017@303120",
            structure=alumina,
        ),
        _write_ref(
            tmp_path / "candidate-1528915.cif",
            identity="COD:1528915@176429",
            structure=tungsten_oxide,
        ),
    )
    exclusions = (
        _write_ref(
            tmp_path / "excluded-alumina.cif",
            identity="xred/private/alumina",
            structure=alumina.copy(),
            comment="# Different public serialization\n",
        ),
        _write_ref(
            tmp_path / "excluded-tungsten-oxide.cif",
            identity="xred/private/tungsten-oxide",
            structure=tungsten_oxide.copy(),
            comment="# Different public serialization\n",
        ),
    )
    return candidates, exclusions


def test_exclusion_guard_finds_both_structural_collisions_and_fails_closed(
    tmp_path: Path,
) -> None:
    candidates, exclusions = _collision_pairs(tmp_path)

    audit = audit_structure_exclusion(candidates, exclusions)

    assert audit.status == "INVALIDATED_XRED_STRUCTURE_COLLISION"
    assert {collision.candidate_identity for collision in audit.collisions} == {
        "COD:1000017@303120",
        "COD:1528915@176429",
    }
    assert all(
        collision.candidate_sha256 != collision.excluded_sha256
        for collision in audit.collisions
    )
    serialized = json.dumps(audit.to_json(), sort_keys=True)
    assert "xred/private" not in serialized
    assert str(tmp_path) not in serialized
    with pytest.raises(CohortCollisionError, match="2 forbidden structure collisions"):
        audit.assert_clear()


def test_exclusion_guard_rejects_mutation_before_structure_matching(tmp_path: Path) -> None:
    candidates, exclusions = _collision_pairs(tmp_path)
    candidates[0].path.write_bytes(candidates[0].path.read_bytes() + b"\n# mutation\n")

    with pytest.raises(StructureExclusionValidationError, match="digest mismatch"):
        audit_structure_exclusion(candidates, exclusions)


def test_exclusion_guard_passes_without_a_matching_structure(tmp_path: Path) -> None:
    candidate = Structure(Lattice.cubic(4.2), ["Mg", "O"], [(0, 0, 0), (0.5,) * 3])
    excluded = Structure(Lattice.cubic(5.4), ["Ca", "O"], [(0, 0, 0), (0.5,) * 3])

    audit = audit_structure_exclusion(
        (
            _write_ref(
                tmp_path / "candidate.cif",
                identity="COD:candidate@1",
                structure=candidate,
            ),
        ),
        (
            _write_ref(
                tmp_path / "excluded.cif",
                identity="excluded/private/1",
                structure=excluded,
            ),
        ),
    )

    assert audit.status == "PASSED_STRUCTURE_EXCLUSION"
    assert audit.collisions == ()
    audit.assert_clear()

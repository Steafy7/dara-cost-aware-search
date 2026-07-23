from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure

from dara_cost_aware.cohort_exclusion import (
    CohortCollisionError,
    StructureExclusionValidationError,
    StructureRef,
    enforce_structure_exclusion,
)
from dara_cost_aware.cohort_exclusion_cli import main as exclusion_main


_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cohort_exclusion"


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


def _write_payload_ref(path: Path, *, identity: str, payload: str) -> StructureRef:
    encoded = payload.encode("utf-8")
    path.write_bytes(encoded)
    return StructureRef(
        identity=identity,
        path=path,
        expected_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _collision_pairs(tmp_path: Path) -> tuple[tuple[StructureRef, ...], tuple[StructureRef, ...]]:
    source_names = ("cod_1000017_303120.cif", "cod_1528915_176429.cif")
    for source_name in source_names:
        encoded = (_FIXTURE_ROOT / f"{source_name}.b64").read_bytes()
        (tmp_path / source_name).write_bytes(base64.b64decode(encoded))
    xred_names = ("xred_1000017_3a601025.cif", "xred_1528915_3a601025.cif")
    for source_name in xred_names:
        shutil.copyfile(_FIXTURE_ROOT / source_name, tmp_path / source_name)
    candidates = (
        StructureRef(
            identity="COD:1000017@303120",
            path=tmp_path / source_names[0],
            expected_sha256="4013310ed8c8d50d1c86076e8d99ef0ed2595ee66c47158bbf4e86fd3afbce00",
        ),
        StructureRef(
            identity="COD:1528915@176429",
            path=tmp_path / source_names[1],
            expected_sha256="708f9de65ab399d4e8e750f9fa72ee06753a8fc69fcec6b4a64e5d7134ad90bd",
        ),
    )
    exclusions = (
        StructureRef(
            identity="xred/private/alumina",
            path=tmp_path / xred_names[0],
            expected_sha256="ab9337523a55bd4df615f2c89ccfddc82dfb991b4eb3216120691ff4e893d914",
        ),
        StructureRef(
            identity="xred/private/tungsten-oxide",
            path=tmp_path / xred_names[1],
            expected_sha256="dcce8b6ded2f51ad7bd7aaf65bb59efadc5308d8e54cca1e85fa336acb337d29",
        ),
    )
    return candidates, exclusions


def test_exclusion_guard_finds_both_structural_collisions_and_fails_closed(
    tmp_path: Path,
) -> None:
    candidates, exclusions = _collision_pairs(tmp_path)

    with pytest.raises(CohortCollisionError, match="2 forbidden structure collisions") as caught:
        enforce_structure_exclusion(candidates, exclusions)
    audit = caught.value.audit

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


def test_exclusion_guard_rejects_mutation_before_structure_matching(tmp_path: Path) -> None:
    candidates, exclusions = _collision_pairs(tmp_path)
    candidates[0].path.write_bytes(candidates[0].path.read_bytes() + b"\n# mutation\n")

    with pytest.raises(StructureExclusionValidationError, match="digest mismatch"):
        enforce_structure_exclusion(candidates, exclusions)


def test_exclusion_guard_passes_without_a_matching_structure(tmp_path: Path) -> None:
    candidate = Structure(Lattice.cubic(4.2), ["Mg", "O"], [(0, 0, 0), (0.5,) * 3])
    excluded = Structure(Lattice.cubic(5.4), ["Ca", "O"], [(0, 0, 0), (0.5,) * 3])

    audit = enforce_structure_exclusion(
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


def test_exclusion_guard_forensically_parses_small_occupancy_overshoot(
    tmp_path: Path,
) -> None:
    candidate = Structure(Lattice.cubic(4.2), ["Mg", "O"], [(0, 0, 0), (0.5,) * 3])
    excluded_cif = """data_excluded
_symmetry_space_group_name_H-M 'P 1'
_cell_length_a 5
_cell_length_b 5
_cell_length_c 5
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Al1 Al 0 0 0 1.00066
O1 O 0.5 0.5 0.5 1
"""

    audit = enforce_structure_exclusion(
        (
            _write_ref(
                tmp_path / "candidate.cif",
                identity="COD:candidate@1",
                structure=candidate,
            ),
        ),
        (
            _write_payload_ref(
                tmp_path / "excluded.cif",
                identity="excluded/private/overshoot",
                payload=excluded_cif,
            ),
        ),
    )

    assert audit.status == "PASSED_STRUCTURE_EXCLUSION"
    assert audit.to_json()["exclusion_parser"] == {
        "allow_disorder": True,
        "occupancy_tolerance": "1.01",
        "type": "pymatgen.io.cif.CifParser",
    }


def test_exclusion_guard_allows_disordered_forbidden_structure(tmp_path: Path) -> None:
    candidate = Structure(Lattice.cubic(4.2), ["Mg", "O"], [(0, 0, 0), (0.5,) * 3])
    excluded = Structure(
        Lattice.cubic(5.0),
        [{"Al": 0.5}, "O"],
        [(0, 0, 0), (0.5,) * 3],
    )

    audit = enforce_structure_exclusion(
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
                identity="excluded/private/disordered",
                structure=excluded,
            ),
        ),
    )

    assert audit.status == "PASSED_STRUCTURE_EXCLUSION"


def test_exclusion_guard_keeps_candidate_structure_strict(tmp_path: Path) -> None:
    candidate = Structure(
        Lattice.cubic(4.2),
        [{"Mg": 0.5}, "O"],
        [(0, 0, 0), (0.5,) * 3],
    )
    excluded = Structure(Lattice.cubic(5.4), ["Ca", "O"], [(0, 0, 0), (0.5,) * 3])

    with pytest.raises(
        StructureExclusionValidationError,
        match="disordered structure is unsupported for COD:candidate@1",
    ):
        enforce_structure_exclusion(
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
                    identity="excluded/private/control",
                    structure=excluded,
                ),
            ),
        )


def test_v1_executable_writes_audit_and_terminates_nonzero(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"
    _collision_pairs(tmp_path)

    exit_code = exclusion_main(
        ["--input-root", str(tmp_path), "--output", str(output)]
    )

    assert exit_code == 2
    payload = json.loads(output.read_text())
    assert payload["status"] == "INVALIDATED_XRED_STRUCTURE_COLLISION"
    assert payload["collision_count"] == 2
    assert "xred/private" not in output.read_text()
    assert str(tmp_path) not in output.read_text()

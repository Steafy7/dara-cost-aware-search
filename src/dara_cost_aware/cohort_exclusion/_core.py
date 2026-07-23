"""Fail-closed structure exclusion for prospective public cohorts.

This module is deliberately independent of DARA outcomes and rescue state. It
compares a prospective structure bank with a forbidden benchmark structure
set, records only content hashes for the forbidden side, and makes a detected
collision impossible to treat as a successful freeze.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re

from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core.structure import Structure

from dara_cost_aware.artifacts import JsonValue


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class StructureExclusionValidationError(ValueError):
    """An exclusion input is malformed, unsupported, or content-mismatched."""


class CohortCollisionError(StructureExclusionValidationError):
    """A prospective bank contains at least one forbidden structure."""

    def __init__(self, audit: StructureExclusionAudit) -> None:
        self.audit = audit
        super().__init__(
            f"{len(audit.collisions)} forbidden structure collisions invalidate the cohort"
        )


@dataclass(frozen=True)
class _StructureMatcherConfig:
    ltol: str
    stol: str
    angle_tol: str
    primitive_cell: bool
    scale: bool
    attempt_supercell: bool

    def build(self) -> StructureMatcher:
        return StructureMatcher(
            ltol=float(self.ltol),
            stol=float(self.stol),
            angle_tol=float(self.angle_tol),
            primitive_cell=self.primitive_cell,
            scale=self.scale,
            attempt_supercell=self.attempt_supercell,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "angle_tol": self.angle_tol,
            "attempt_supercell": self.attempt_supercell,
            "ltol": self.ltol,
            "primitive_cell": self.primitive_cell,
            "scale": self.scale,
            "stol": self.stol,
            "type": "pymatgen.analysis.structure_matcher.StructureMatcher",
        }


_MATCHER_CONFIG = _StructureMatcherConfig(
    ltol="0.2",
    stol="0.3",
    angle_tol="5",
    primitive_cell=True,
    scale=True,
    attempt_supercell=False,
)


@dataclass(frozen=True)
class StructureRef:
    """One immutable local CIF input to the offline exclusion audit."""

    identity: str
    path: Path
    expected_sha256: str

    def __post_init__(self) -> None:
        if not self.identity:
            raise StructureExclusionValidationError("structure identity must not be empty")
        if _SHA256_RE.fullmatch(self.expected_sha256) is None:
            raise StructureExclusionValidationError("expected_sha256 must be 64 lowercase hex")


@dataclass(frozen=True)
class StructureCollision:
    candidate_identity: str
    candidate_sha256: str
    excluded_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "candidate_sha256": self.candidate_sha256,
            "excluded_sha256": self.excluded_sha256,
            "structure_match": True,
        }


@dataclass(frozen=True)
class StructureExclusionAudit:
    candidate_count: int
    exclusion_count: int
    collisions: tuple[StructureCollision, ...]

    @property
    def status(self) -> str:
        if self.collisions:
            return "INVALIDATED_XRED_STRUCTURE_COLLISION"
        return "PASSED_STRUCTURE_EXCLUSION"

    def assert_clear(self) -> None:
        if self.collisions:
            raise CohortCollisionError(self)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "audit_scope": "offline_one_way_structure_exclusion",
            "candidate_count": self.candidate_count,
            "collision_count": len(self.collisions),
            "collisions": [collision.to_json() for collision in self.collisions],
            "exclusion_count": self.exclusion_count,
            "matcher": _MATCHER_CONFIG.to_json(),
            "status": self.status,
        }


def _load_structure(reference: StructureRef) -> Structure:
    try:
        payload = reference.path.read_bytes()
    except OSError as error:
        raise StructureExclusionValidationError(
            f"cannot read structure {reference.identity}"
        ) from error
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != reference.expected_sha256:
        raise StructureExclusionValidationError(
            f"structure digest mismatch for {reference.identity}"
        )
    try:
        structure = Structure.from_str(payload.decode("utf-8"), fmt="cif")
    except (UnicodeDecodeError, ValueError) as error:
        raise StructureExclusionValidationError(
            f"cannot parse structure {reference.identity}"
        ) from error
    if not structure.is_ordered:
        raise StructureExclusionValidationError(
            f"disordered structure is unsupported for {reference.identity}"
        )
    numeric_values = (
        *structure.lattice.matrix.ravel(),
        *structure.frac_coords.ravel(),
        *(float(occupancy) for site in structure for occupancy in site.species.values()),
    )
    if not numeric_values or not all(math.isfinite(float(value)) for value in numeric_values):
        raise StructureExclusionValidationError(
            f"structure has non-finite coordinates or occupancy for {reference.identity}"
        )
    return structure


def _validate_refs(references: tuple[StructureRef, ...], *, role: str) -> None:
    if not references:
        raise StructureExclusionValidationError(f"{role} structures must not be empty")
    identities = tuple(reference.identity for reference in references)
    if len(identities) != len(set(identities)):
        raise StructureExclusionValidationError(f"{role} structure identities must be unique")


def enforce_structure_exclusion(
    candidates: tuple[StructureRef, ...],
    exclusions: tuple[StructureRef, ...],
) -> StructureExclusionAudit:
    """Compare a prospective bank with a forbidden set and fail on collision.

    The function has no replacement hook and accepts no labels or outcomes.
    It cannot return a continuation value after detecting a collision.
    Forbidden identities and paths never enter the audit or exception.
    """

    _validate_refs(candidates, role="candidate")
    _validate_refs(exclusions, role="exclusion")
    candidate_structures = tuple(
        (reference, _load_structure(reference)) for reference in candidates
    )
    excluded_structures = tuple(
        (reference, _load_structure(reference)) for reference in exclusions
    )
    matcher = _MATCHER_CONFIG.build()
    collisions: list[StructureCollision] = []
    for candidate_ref, candidate in candidate_structures:
        for excluded_ref, excluded in excluded_structures:
            if candidate.composition.reduced_formula != excluded.composition.reduced_formula:
                continue
            if bool(matcher.fit(candidate, excluded)):
                collisions.append(
                    StructureCollision(
                        candidate_identity=candidate_ref.identity,
                        candidate_sha256=candidate_ref.expected_sha256,
                        excluded_sha256=excluded_ref.expected_sha256,
                    )
                )
    audit = StructureExclusionAudit(
        candidate_count=len(candidates),
        exclusion_count=len(exclusions),
        collisions=tuple(collisions),
    )
    audit.assert_clear()
    return audit

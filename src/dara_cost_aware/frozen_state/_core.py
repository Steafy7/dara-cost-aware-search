"""Portable manifests for the common post-initialization pilot state.

The real DARA/BGMN setup writes the tree and singleton cache outside Git.  This
module owns the small, content-addressed manifest that lets the four arms
prove they are using the same revision-zero state without loading or
re-running branch search during setup validation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import cast

from dara_cost_aware.artifacts import (
    ArtifactRef,
    ArtifactValidationError,
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    read_canonical_json,
    validate_online_payload,
)


FROZEN_STATE_SCHEMA_VERSION = 1
FROZEN_STATE_CONTRACT_VERSION = "ticket-17-v1"
PILOT_VERSION = "dara-cost-aware-rough-pilot-v1"
PILOT_PATTERN_IDS = (
    "In2O3_0p5002-MnCO3_0p5000--2min",
    "Li2CO3_0p9001-ZrO2_0p1000--2min",
    "NiO_0p6007-Bi2O3_0p4008--2min",
    "TiO2_0p4009-ZnO_0p6010--8min",
    "V2O5_0p3015-TiO2_0p6998--8min",
    "In2O3_0p3022-La(OH)3_0p5014-TiO2_0p2004--2min",
    "NiO_0p1031-La(OH)3_0p2025-TiO2_0p7014--2min",
    "NiO_0p2030-Li2CO3_0p2999-TiO2_0p5025--8min",
    "NiO_0p3035-Bi2O3_0p3016-Li2CO3_0p3997--8min",
    "NiO_0p7028-La(OH)3_0p1010-ZnO_0p2000--8min",
)
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUIRED_PROVENANCE = {
    "bgmn",
    "candidate_pool",
    "dara_source",
    "instrument",
    "python",
    "runner_contract",
    "serializer",
}


class FrozenStateValidationError(ArtifactValidationError):
    """A frozen-state manifest or external artifact failed closed validation."""


def _string(value: JsonValue, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FrozenStateValidationError(f"{field} must be a non-empty string")
    return value


def _integer(value: JsonValue, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FrozenStateValidationError(f"{field} must be an integer")
    return value


def _artifact(value: JsonValue, *, field: str) -> ArtifactRef:
    if not isinstance(value, dict) or set(value) != {
        "byte_length",
        "path",
        "role",
        "sha256",
    }:
        raise FrozenStateValidationError(f"{field} reference fields differ")
    path = _string(value["path"], field=f"{field}.path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FrozenStateValidationError(f"{field}.path must be portable and relative")
    digest = _string(value["sha256"], field=f"{field}.sha256")
    if _SHA256_RE.fullmatch(digest) is None:
        raise FrozenStateValidationError(f"{field}.sha256 is not a SHA-256 identifier")
    length = _integer(value["byte_length"], field=f"{field}.byte_length")
    if length < 0:
        raise FrozenStateValidationError(f"{field}.byte_length must be nonnegative")
    return ArtifactRef(
        role=_string(value["role"], field=f"{field}.role"),
        path=path,
        sha256=digest,
        byte_length=length,
    )


def artifact_ref_from_file(role: str, path: Path, artifact_root: Path) -> ArtifactRef:
    """Hash one external setup artifact using a portable root-relative path."""

    if not role or not re.fullmatch(r"[a-z][a-z0-9_-]*", role):
        raise ValueError("artifact role must be a stable lowercase identifier")
    root = artifact_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise FrozenStateValidationError("artifact must live below artifact_root") from error
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise FrozenStateValidationError(f"cannot read artifact {relative}") from error
    return ArtifactRef(
        role=role,
        path=relative.as_posix(),
        sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        byte_length=len(data),
    )


def _validate_provenance(provenance: tuple[tuple[str, str], ...]) -> None:
    if not all(
        isinstance(name, str) and name and isinstance(value, str) and value
        for name, value in provenance
    ):
        raise FrozenStateValidationError("provenance entries must be non-empty strings")
    names = tuple(name for name, _ in provenance)
    if tuple(sorted(provenance)) != provenance:
        raise FrozenStateValidationError("provenance must be sorted by name")
    if len(names) != len(set(names)):
        raise FrozenStateValidationError("provenance names must be unique")
    if not _REQUIRED_PROVENANCE.issubset(names):
        missing = sorted(_REQUIRED_PROVENANCE - set(names))
        raise FrozenStateValidationError(f"provenance is missing {missing}")
    try:
        validate_online_payload({name: value for name, value in provenance})
    except ArtifactValidationError as error:
        raise FrozenStateValidationError(f"GT-like provenance is forbidden: {error}") from error


@dataclass(frozen=True)
class FrozenStateInput:
    """Observed output of one serial DARA initialization pass.

    ``branch_search_call_count`` is deliberately explicit: a setup producer
    must report zero, rather than asking the audit to infer it from opaque
    pickles.  Initialization calls are reported separately and are outside
    the branch-stage budget.
    """

    pattern_id: str
    tree_artifact: ArtifactRef
    singleton_cache_artifact: ArtifactRef
    frontier_fingerprint: str
    initialization_call_count: int
    branch_search_call_count: int
    provenance: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.pattern_id:
            raise FrozenStateValidationError("pattern_id must not be empty")
        if not _SHA256_RE.fullmatch(self.frontier_fingerprint):
            raise FrozenStateValidationError("frontier_fingerprint must be a SHA-256 identifier")
        if self.initialization_call_count <= 0:
            raise FrozenStateValidationError("initialization_call_count must be positive")
        if self.branch_search_call_count != 0:
            raise FrozenStateValidationError(
                "branch search must not run during frozen-state setup"
            )
        _validate_provenance(self.provenance)

    def identity_json(self, *, pilot_version: str = PILOT_VERSION) -> dict[str, JsonValue]:
        def artifact_identity(reference: ArtifactRef) -> dict[str, JsonValue]:
            return {
                "byte_length": reference.byte_length,
                "role": reference.role,
                "sha256": reference.sha256,
            }

        return {
            "branch_search_call_count": self.branch_search_call_count,
            "contract_version": FROZEN_STATE_CONTRACT_VERSION,
            "frontier_fingerprint": self.frontier_fingerprint,
            "initialization_call_count": self.initialization_call_count,
            "pattern_id": self.pattern_id,
            "pilot_version": pilot_version,
            "provenance": {name: value for name, value in self.provenance},
            "singleton_cache_artifact": artifact_identity(self.singleton_cache_artifact),
            "state_revision": 0,
            "tree_artifact": artifact_identity(self.tree_artifact),
        }


@dataclass(frozen=True)
class FrozenStateRecord:
    pattern_id: str
    frozen_state_id: str
    state_revision: int
    tree_artifact: ArtifactRef
    singleton_cache_artifact: ArtifactRef
    singleton_cache_namespace: str
    frontier_fingerprint: str
    initialization_call_count: int
    branch_search_call_count: int
    provenance: tuple[tuple[str, str], ...]

    @classmethod
    def from_input(cls, value: FrozenStateInput, *, pilot_version: str) -> FrozenStateRecord:
        identity = value.identity_json(pilot_version=pilot_version)
        frozen_state_id = canonical_sha256(identity)
        return cls(
            pattern_id=value.pattern_id,
            frozen_state_id=frozen_state_id,
            state_revision=0,
            tree_artifact=value.tree_artifact,
            singleton_cache_artifact=value.singleton_cache_artifact,
            singleton_cache_namespace=(
                f"{pilot_version}/{value.pattern_id}/{frozen_state_id.removeprefix('sha256:')}"
                "/singleton"
            ),
            frontier_fingerprint=value.frontier_fingerprint,
            initialization_call_count=value.initialization_call_count,
            branch_search_call_count=value.branch_search_call_count,
            provenance=value.provenance,
        )

    def __post_init__(self) -> None:
        if not self.pattern_id:
            raise FrozenStateValidationError("pattern_id must not be empty")
        if _SHA256_RE.fullmatch(self.frozen_state_id) is None:
            raise FrozenStateValidationError("frozen_state_id must be a SHA-256 identifier")
        if self.state_revision != 0:
            raise FrozenStateValidationError("frozen state must describe revision zero")
        if (
            not self.singleton_cache_namespace
            or "/singleton" not in self.singleton_cache_namespace
        ):
            raise FrozenStateValidationError("singleton cache namespace is malformed")
        if not _SHA256_RE.fullmatch(self.frontier_fingerprint):
            raise FrozenStateValidationError("frontier_fingerprint must be a SHA-256 identifier")
        if self.initialization_call_count <= 0:
            raise FrozenStateValidationError("initialization_call_count must be positive")
        if self.branch_search_call_count != 0:
            raise FrozenStateValidationError(
                "branch search must not run during frozen-state setup"
            )
        _validate_provenance(self.provenance)
        _artifact(self.tree_artifact.to_json(), field="tree_artifact")
        _artifact(self.singleton_cache_artifact.to_json(), field="singleton_cache_artifact")

    def identity_json(self, *, pilot_version: str = PILOT_VERSION) -> dict[str, JsonValue]:
        return FrozenStateInput(
            pattern_id=self.pattern_id,
            tree_artifact=self.tree_artifact,
            singleton_cache_artifact=self.singleton_cache_artifact,
            frontier_fingerprint=self.frontier_fingerprint,
            initialization_call_count=self.initialization_call_count,
            branch_search_call_count=self.branch_search_call_count,
            provenance=self.provenance,
        ).identity_json(pilot_version=pilot_version)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "branch_search_call_count": self.branch_search_call_count,
            "frozen_state_id": self.frozen_state_id,
            "frontier_fingerprint": self.frontier_fingerprint,
            "initialization_call_count": self.initialization_call_count,
            "pattern_id": self.pattern_id,
            "provenance": {name: value for name, value in self.provenance},
            "singleton_cache_artifact": self.singleton_cache_artifact.to_json(),
            "singleton_cache_namespace": self.singleton_cache_namespace,
            "state_revision": self.state_revision,
            "tree_artifact": self.tree_artifact.to_json(),
        }


@dataclass(frozen=True)
class FrozenPilotManifest:
    pilot_version: str
    cohort_manifest: ArtifactRef
    states: tuple[FrozenStateRecord, ...]

    def __post_init__(self) -> None:
        if self.pilot_version != PILOT_VERSION:
            raise FrozenStateValidationError("unsupported pilot version")
        if self.cohort_manifest.role != "cohort-manifest":
            raise FrozenStateValidationError("cohort manifest must use the cohort-manifest role")
        if tuple(state.pattern_id for state in self.states) != PILOT_PATTERN_IDS:
            raise FrozenStateValidationError(
                "states must use the exact ticket-8 pattern order and cardinality"
            )
        if len({state.frozen_state_id for state in self.states}) != len(self.states):
            raise FrozenStateValidationError("frozen state IDs must be unique")
        for state in self.states:
            if state.state_revision != 0 or state.branch_search_call_count != 0:
                raise FrozenStateValidationError(
                    f"{state.pattern_id} is not a revision-zero setup state"
                )

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "cohort_manifest": self.cohort_manifest.to_json(),
            "contract_version": FROZEN_STATE_CONTRACT_VERSION,
            "pilot_version": self.pilot_version,
            "schema_name": "frozen_pilot_manifest",
            "schema_version": FROZEN_STATE_SCHEMA_VERSION,
            "states": [state.to_json() for state in self.states],
        }
        validate_online_payload(payload)
        return payload

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(self.to_json()))


def freeze_pilot(
    cohort_manifest: ArtifactRef,
    states: tuple[FrozenStateInput, ...],
    *,
    pilot_version: str = PILOT_VERSION,
) -> FrozenPilotManifest:
    """Build the immutable ten-pattern manifest from setup observations."""

    if pilot_version != PILOT_VERSION:
        raise FrozenStateValidationError("unsupported pilot version")
    return FrozenPilotManifest(
        pilot_version=pilot_version,
        cohort_manifest=cohort_manifest,
        states=tuple(
            FrozenStateRecord.from_input(state, pilot_version=pilot_version) for state in states
        ),
    )


def _parse_provenance(value: JsonValue, *, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(item, str) for name, item in value.items()
    ):
        raise FrozenStateValidationError(f"{field} must be a string object")
    return tuple(sorted((name, cast(str, item)) for name, item in value.items()))


def _parse_state(value: JsonValue) -> FrozenStateRecord:
    if not isinstance(value, dict) or set(value) != {
        "branch_search_call_count",
        "frozen_state_id",
        "frontier_fingerprint",
        "initialization_call_count",
        "pattern_id",
        "provenance",
        "singleton_cache_artifact",
        "singleton_cache_namespace",
        "state_revision",
        "tree_artifact",
    }:
        raise FrozenStateValidationError("frozen state fields differ")
    return FrozenStateRecord(
        pattern_id=_string(value["pattern_id"], field="state.pattern_id"),
        frozen_state_id=_string(value["frozen_state_id"], field="state.frozen_state_id"),
        state_revision=_integer(value["state_revision"], field="state.state_revision"),
        tree_artifact=_artifact(value["tree_artifact"], field="state.tree_artifact"),
        singleton_cache_artifact=_artifact(
            value["singleton_cache_artifact"], field="state.singleton_cache_artifact"
        ),
        singleton_cache_namespace=_string(
            value["singleton_cache_namespace"], field="state.singleton_cache_namespace"
        ),
        frontier_fingerprint=_string(
            value["frontier_fingerprint"], field="state.frontier_fingerprint"
        ),
        initialization_call_count=_integer(
            value["initialization_call_count"], field="state.initialization_call_count"
        ),
        branch_search_call_count=_integer(
            value["branch_search_call_count"], field="state.branch_search_call_count"
        ),
        provenance=_parse_provenance(value["provenance"], field="state.provenance"),
    )


def read_frozen_pilot_manifest(path: Path) -> FrozenPilotManifest:
    """Read and validate the canonical portable manifest, without artifacts."""

    try:
        value = read_canonical_json(path)
    except ArtifactValidationError as error:
        raise FrozenStateValidationError(str(error)) from error
    if set(value) != {
        "cohort_manifest",
        "contract_version",
        "pilot_version",
        "schema_name",
        "schema_version",
        "states",
    }:
        raise FrozenStateValidationError("frozen pilot manifest fields differ")
    if value["contract_version"] != FROZEN_STATE_CONTRACT_VERSION:
        raise FrozenStateValidationError("frozen-state contract differs")
    if value["schema_name"] != "frozen_pilot_manifest" or value["schema_version"] != 1:
        raise FrozenStateValidationError("frozen pilot schema differs")
    states_value = value["states"]
    if not isinstance(states_value, list):
        raise FrozenStateValidationError("frozen pilot states must be a list")
    return FrozenPilotManifest(
        pilot_version=_string(value["pilot_version"], field="pilot_version"),
        cohort_manifest=_artifact(value["cohort_manifest"], field="cohort_manifest"),
        states=tuple(_parse_state(item) for item in states_value),
    )


@dataclass(frozen=True)
class FrozenPilotAudit:
    pattern_ids: tuple[str, ...]
    checked_artifact_count: int
    branch_search_call_count: int


def _check_artifact(reference: ArtifactRef, artifact_root: Path) -> None:
    relative = Path(reference.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FrozenStateValidationError("artifact path is unsafe")
    path = artifact_root / relative
    try:
        data = path.read_bytes()
    except OSError as error:
        raise FrozenStateValidationError(f"missing artifact {reference.path}") from error
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if len(data) != reference.byte_length or digest != reference.sha256:
        raise FrozenStateValidationError(f"artifact digest or length mismatch: {reference.path}")


def audit_frozen_pilot_manifest(path: Path, artifact_root: Path) -> FrozenPilotAudit:
    """Validate the manifest, cohort selection record, and all external objects."""

    manifest = read_frozen_pilot_manifest(path)
    root = artifact_root.resolve()
    _check_artifact(manifest.cohort_manifest, root)
    try:
        cohort = read_canonical_json(root / manifest.cohort_manifest.path)
    except ArtifactValidationError as error:
        raise FrozenStateValidationError(f"cohort manifest is invalid: {error}") from error
    expected_cohort_fields = {
        "pattern_ids",
        "selection_algorithm_version",
        "source_doi",
        "source_sha256",
        "source_url",
    }
    if set(cohort) != expected_cohort_fields:
        raise FrozenStateValidationError("cohort manifest fields differ or contain GT-like data")
    if cohort.get("pattern_ids") != list(PILOT_PATTERN_IDS):
        raise FrozenStateValidationError("cohort manifest pattern IDs differ")
    if cohort.get("selection_algorithm_version") != "ticket-8-v1":
        raise FrozenStateValidationError("cohort selection algorithm differs")
    source_sha256 = cohort.get("source_sha256")
    if not isinstance(source_sha256, str) or _SHA256_RE.fullmatch(source_sha256) is None:
        raise FrozenStateValidationError("cohort source digest is invalid")

    checked = 1
    branch_calls = 0
    for state in manifest.states:
        expected_id = canonical_sha256(state.identity_json(pilot_version=manifest.pilot_version))
        if state.frozen_state_id != expected_id:
            raise FrozenStateValidationError(f"frozen state identity differs: {state.pattern_id}")
        expected_namespace = (
            f"{manifest.pilot_version}/{state.pattern_id}/"
            f"{state.frozen_state_id.removeprefix('sha256:')}/singleton"
        )
        if state.singleton_cache_namespace != expected_namespace:
            raise FrozenStateValidationError(f"singleton namespace differs: {state.pattern_id}")
        if state.tree_artifact.role != "search-tree":
            raise FrozenStateValidationError(f"tree artifact role differs: {state.pattern_id}")
        if state.singleton_cache_artifact.role != "singleton-cache":
            raise FrozenStateValidationError(
                f"singleton cache artifact role differs: {state.pattern_id}"
            )
        _check_artifact(state.tree_artifact, root)
        _check_artifact(state.singleton_cache_artifact, root)
        checked += 2
        branch_calls += state.branch_search_call_count
    if branch_calls != 0:
        raise FrozenStateValidationError("setup audit observed branch search calls")
    return FrozenPilotAudit(
        pattern_ids=PILOT_PATTERN_IDS,
        checked_artifact_count=checked,
        branch_search_call_count=branch_calls,
    )


__all__ = (
    "FROZEN_STATE_CONTRACT_VERSION",
    "FROZEN_STATE_SCHEMA_VERSION",
    "PILOT_PATTERN_IDS",
    "PILOT_VERSION",
    "FrozenPilotAudit",
    "FrozenPilotManifest",
    "FrozenStateInput",
    "FrozenStateRecord",
    "FrozenStateValidationError",
    "artifact_ref_from_file",
    "audit_frozen_pilot_manifest",
    "freeze_pilot",
    "read_frozen_pilot_manifest",
)

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
import tempfile
from typing import cast

from dara_cost_aware.artifacts import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    validate_online_payload,
)


CACHE_SCHEMA_VERSION = 1


class CacheStatus(StrEnum):
    MISSING = "missing"
    VALID_HIT = "valid_hit"
    INVALID_METADATA = "invalid_metadata"
    INVALID_RESULT = "invalid_result"


class CachePublicationError(RuntimeError):
    """An immutable cache location already contains incompatible data."""


@dataclass(frozen=True)
class StrictRefinementIdentity:
    """Complete ordered scientific execution identity for one refinement."""

    strict_key: str
    pattern_digest: str
    ordered_input_digests: tuple[str, ...]
    scientific_parameters: tuple[tuple[str, str], ...]
    provenance: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.pattern_digest or not self.ordered_input_digests:
            raise ValueError("strict refinement identity requires pattern and ordered inputs")
        for field, pairs in (
            ("scientific_parameters", self.scientific_parameters),
            ("provenance", self.provenance),
        ):
            names = tuple(name for name, _ in pairs)
            if len(names) != len(set(names)):
                raise ValueError(f"{field} names must be unique")
            if tuple(sorted(pairs)) != pairs:
                raise ValueError(f"{field} must be sorted by name")
        if self.strict_key != canonical_sha256(self.identity_json()):
            raise ValueError("strict_key does not match canonical execution identity")

    @classmethod
    def create(
        cls,
        *,
        pattern_digest: str,
        ordered_input_digests: tuple[str, ...],
        scientific_parameters: tuple[tuple[str, str], ...],
        provenance: tuple[tuple[str, str], ...],
    ) -> StrictRefinementIdentity:
        normalized_parameters = tuple(sorted(scientific_parameters))
        normalized_provenance = tuple(sorted(provenance))
        identity: dict[str, JsonValue] = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "ordered_input_digests": list(ordered_input_digests),
            "pattern_digest": pattern_digest,
            "provenance": {name: value for name, value in normalized_provenance},
            "scientific_parameters": {name: value for name, value in normalized_parameters},
        }
        return cls(
            strict_key=canonical_sha256(identity),
            pattern_digest=pattern_digest,
            ordered_input_digests=ordered_input_digests,
            scientific_parameters=normalized_parameters,
            provenance=normalized_provenance,
        )

    def identity_json(self) -> dict[str, JsonValue]:
        return {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "ordered_input_digests": list(self.ordered_input_digests),
            "pattern_digest": self.pattern_digest,
            "provenance": {name: value for name, value in self.provenance},
            "scientific_parameters": {name: value for name, value in self.scientific_parameters},
        }


@dataclass(frozen=True)
class CacheProbe:
    strict_key: str
    status: CacheStatus
    reason: str
    result: dict[str, JsonValue] | None

    @property
    def adds_debit(self) -> bool:
        return self.status is not CacheStatus.VALID_HIT


@dataclass(frozen=True)
class CostedCohort:
    probes: tuple[CacheProbe, ...]
    predicted_new_calls: int
    unique_miss_keys: tuple[str, ...]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _key_name(strict_key: str) -> str:
    if not strict_key.startswith("sha256:"):
        raise ValueError("strict refinement keys must be SHA-256 identifiers")
    return strict_key.removeprefix("sha256:")


class StrictRefinementStore:
    """Pure strict-cache probing plus immutable successful-result publication."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _metadata_path(self, strict_key: str) -> Path:
        return self.root / "metadata" / f"{_key_name(strict_key)}.json"

    def _result_path(self, result_sha256: str) -> Path:
        return self.root / "results" / f"{_key_name(result_sha256)}.json"

    def probe(self, identity: StrictRefinementIdentity) -> CacheProbe:
        metadata_path = self._metadata_path(identity.strict_key)
        if not metadata_path.exists():
            return CacheProbe(identity.strict_key, CacheStatus.MISSING, "metadata_missing", None)
        try:
            raw_metadata = metadata_path.read_bytes()
            decoded = json.loads(raw_metadata)
            if not isinstance(decoded, dict):
                raise ValueError("metadata is not an object")
            metadata = cast(dict[str, JsonValue], decoded)
            if canonical_json_bytes(metadata) != raw_metadata:
                raise ValueError("metadata is not canonical")
            expected_fields = {
                "cache_schema_version",
                "identity",
                "producing_attempt_id",
                "result_byte_length",
                "result_sha256",
                "strict_key",
                "validation_status",
            }
            if set(metadata) != expected_fields:
                raise ValueError("metadata fields differ")
            if metadata["cache_schema_version"] != CACHE_SCHEMA_VERSION:
                raise ValueError("cache schema differs")
            if metadata["strict_key"] != identity.strict_key:
                raise ValueError("strict key differs")
            if metadata["identity"] != identity.identity_json():
                raise ValueError("strict identity differs")
            if metadata["validation_status"] != "valid":
                raise ValueError("cache entry is not marked valid")
            result_sha256 = metadata["result_sha256"]
            result_byte_length = metadata["result_byte_length"]
            if not isinstance(result_sha256, str) or not isinstance(result_byte_length, int):
                raise ValueError("result reference is malformed")
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return CacheProbe(
                identity.strict_key,
                CacheStatus.INVALID_METADATA,
                "metadata_validation_failed",
                None,
            )

        result_path = self._result_path(result_sha256)
        try:
            raw_result = result_path.read_bytes()
            if len(raw_result) != result_byte_length:
                raise ValueError("result length differs")
            decoded_result = json.loads(raw_result)
            if not isinstance(decoded_result, dict):
                raise ValueError("result is not an object")
            result = cast(dict[str, JsonValue], decoded_result)
            if canonical_json_bytes(result) != raw_result:
                raise ValueError("result is not canonical")
            if canonical_sha256(result) != result_sha256:
                raise ValueError("result digest differs")
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return CacheProbe(
                identity.strict_key,
                CacheStatus.INVALID_RESULT,
                "result_validation_failed",
                None,
            )
        return CacheProbe(identity.strict_key, CacheStatus.VALID_HIT, "validated", result)

    def cost(self, identities: tuple[StrictRefinementIdentity, ...]) -> CostedCohort:
        probes = tuple(self.probe(identity) for identity in identities)
        unique_miss_keys = tuple(
            dict.fromkeys(probe.strict_key for probe in probes if probe.adds_debit)
        )
        return CostedCohort(
            probes=probes,
            predicted_new_calls=len(unique_miss_keys),
            unique_miss_keys=unique_miss_keys,
        )

    def publish_success(
        self,
        identity: StrictRefinementIdentity,
        result: dict[str, JsonValue],
        *,
        producing_attempt_id: str,
    ) -> CacheProbe:
        if not producing_attempt_id:
            raise ValueError("producing_attempt_id must not be empty")
        validate_online_payload(result)
        existing = self.probe(identity)
        if existing.status is CacheStatus.VALID_HIT:
            if existing.result != result:
                raise CachePublicationError("valid cache entry has a different result")
            return existing
        if existing.status is not CacheStatus.MISSING:
            raise CachePublicationError("invalid immutable cache entry cannot be overwritten")

        result_bytes = canonical_json_bytes(result)
        result_sha256 = canonical_sha256(result)
        metadata: dict[str, JsonValue] = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "identity": identity.identity_json(),
            "producing_attempt_id": producing_attempt_id,
            "result_byte_length": len(result_bytes),
            "result_sha256": result_sha256,
            "strict_key": identity.strict_key,
            "validation_status": "valid",
        }
        _atomic_write(self._result_path(result_sha256), result_bytes)
        _atomic_write(self._metadata_path(identity.strict_key), canonical_json_bytes(metadata))
        published = self.probe(identity)
        if published.status is not CacheStatus.VALID_HIT:
            raise CachePublicationError("published cache entry failed readback validation")
        return published


__all__ = (
    "CACHE_SCHEMA_VERSION",
    "CacheProbe",
    "CachePublicationError",
    "CacheStatus",
    "CostedCohort",
    "StrictRefinementIdentity",
    "StrictRefinementStore",
)

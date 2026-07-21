from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import cast

from dara_cost_aware.scheduling import Arm, PreparedExpansion, SchedulingDecision


SCHEMA_VERSION = 1
CONTRACT_VERSION = "ticket-14-v1"
GENESIS_EVENT_HASH = "sha256:" + "0" * 64
type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]


class ArtifactValidationError(ValueError):
    """An authoritative artifact is malformed, incompatible, or altered."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _validate_json(value: object, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactValidationError(f"{path} contains a non-finite number")
        raise ArtifactValidationError(
            f"{path} contains a binary float; scientific decimals must be strings"
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ArtifactValidationError(f"{path} contains a non-string key")
            _validate_json(item, path=f"{path}.{key}")
        return
    raise ArtifactValidationError(f"{path} contains unsupported type {type(value).__name__}")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Encode the I-JSON subset used by the pilot in RFC-8785-compatible form."""

    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: JsonValue) -> str:
    return _sha256(canonical_json_bytes(value))


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


def _read_json(path: Path) -> dict[str, JsonValue]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"cannot read JSON artifact {path.name}") from error
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{path.name} must contain a JSON object")
    typed = cast(dict[str, JsonValue], value)
    if canonical_json_bytes(typed) != raw:
        raise ArtifactValidationError(f"{path.name} is not canonically encoded")
    return typed


def _expect_keys(value: dict[str, JsonValue], expected: set[str], *, schema: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ArtifactValidationError(
            f"{schema} fields differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _contains_gt_part(name: str) -> bool:
    normalized = name.casefold()
    parts = {part for part in re.split(r"[^a-z0-9]+", normalized) if part}
    collapsed = "".join(part for part in re.split(r"[^a-z0-9]+", normalized) if part)
    forbidden_substrings = {
        "correct",
        "firstcorrect",
        "groundtruth",
        "label",
        "oracle",
        "target",
    }
    return "gt" in parts or any(token in collapsed for token in forbidden_substrings)


def validate_online_names(names: tuple[str, ...], *, field: str) -> None:
    forbidden = sorted(name for name in names if _contains_gt_part(name))
    if forbidden:
        raise ArtifactValidationError(f"{field} contains GT-like names: {forbidden}")


def validate_online_payload(value: JsonValue, *, path: str = "$") -> None:
    if isinstance(value, dict):
        validate_online_names(tuple(value), field=path)
        for key, item in value.items():
            validate_online_payload(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_online_payload(item, path=f"{path}[{index}]")


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    pilot_version: str
    pattern_id: str
    arm: Arm
    budget: int
    run_attempt_id: str
    frozen_state_id: str
    provenance: tuple[tuple[str, str], ...]
    environment_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("pilot_version", self.pilot_version),
            ("pattern_id", self.pattern_id),
            ("run_attempt_id", self.run_attempt_id),
            ("frozen_state_id", self.frozen_state_id),
        ):
            if not value:
                raise ValueError(f"{name} must not be empty")
        if self.budget < 0:
            raise ValueError("budget must be nonnegative")
        provenance_names = tuple(name for name, _ in self.provenance)
        if len(provenance_names) != len(set(provenance_names)):
            raise ValueError("provenance names must be unique")
        validate_online_names(provenance_names, field="provenance")
        validate_online_names(self.environment_keys, field="environment_keys")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "arm": self.arm.value,
            "budget": self.budget,
            "contract_version": CONTRACT_VERSION,
            "environment_keys": list(self.environment_keys),
            "frozen_state_id": self.frozen_state_id,
            "ground_truth_available": False,
            "pattern_id": self.pattern_id,
            "pilot_version": self.pilot_version,
            "provenance": {name: value for name, value in self.provenance},
            "run_attempt_id": self.run_attempt_id,
            "run_id": self.run_id,
            "schema_name": "run_manifest",
            "schema_version": SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class FrozenStateManifest:
    frozen_state_id: str
    state_revision: int
    frontier_fingerprint: str

    def __post_init__(self) -> None:
        if not self.frozen_state_id or not self.frontier_fingerprint:
            raise ValueError("frozen state identifiers must not be empty")
        if self.state_revision != 0:
            raise ValueError("a frozen state manifest must describe revision zero")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "contract_version": CONTRACT_VERSION,
            "frontier_fingerprint": self.frontier_fingerprint,
            "frozen_state_id": self.frozen_state_id,
            "schema_name": "frozen_state_manifest",
            "schema_version": SCHEMA_VERSION,
            "state_revision": self.state_revision,
        }


@dataclass(frozen=True)
class TerminalOutput:
    kind: str
    pattern_id: str
    stop_reason: str
    state_revision: int
    debit: int
    accepted_phase_ids: tuple[str, ...]
    rwp: str | None
    abstention_reason: str | None
    provisional_hypothesis_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"result", "abstention"}:
            raise ValueError("terminal output kind must be result or abstention")
        if not self.pattern_id or not self.stop_reason:
            raise ValueError("terminal pattern and stop reason must not be empty")
        if self.state_revision < 0 or self.debit < 0:
            raise ValueError("terminal revision and debit must be nonnegative")
        if self.kind == "result":
            if (
                not self.accepted_phase_ids
                or self.rwp is None
                or self.abstention_reason is not None
            ):
                raise ValueError("a terminal result requires phases and Rwp only")
        elif self.accepted_phase_ids or self.rwp is not None or self.abstention_reason is None:
            raise ValueError("an abstention must not carry a scientific result")

    @classmethod
    def result(
        cls,
        *,
        pattern_id: str,
        stop_reason: str,
        state_revision: int,
        debit: int,
        accepted_phase_ids: tuple[str, ...],
        rwp: str,
    ) -> TerminalOutput:
        return cls(
            kind="result",
            pattern_id=pattern_id,
            stop_reason=stop_reason,
            state_revision=state_revision,
            debit=debit,
            accepted_phase_ids=accepted_phase_ids,
            rwp=rwp,
            abstention_reason=None,
            provisional_hypothesis_ids=(),
        )

    @classmethod
    def abstention(
        cls,
        *,
        pattern_id: str,
        stop_reason: str,
        state_revision: int,
        debit: int,
        provisional_hypothesis_ids: tuple[str, ...] = (),
    ) -> TerminalOutput:
        return cls(
            kind="abstention",
            pattern_id=pattern_id,
            stop_reason=stop_reason,
            state_revision=state_revision,
            debit=debit,
            accepted_phase_ids=(),
            rwp=None,
            abstention_reason="no_scientifically_terminal_incumbent",
            provisional_hypothesis_ids=provisional_hypothesis_ids,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "abstention_reason": self.abstention_reason,
            "accepted_phase_ids": list(self.accepted_phase_ids),
            "contract_version": CONTRACT_VERSION,
            "debit": self.debit,
            "kind": self.kind,
            "pattern_id": self.pattern_id,
            "provisional_hypothesis_ids": list(self.provisional_hypothesis_ids),
            "rwp": self.rwp,
            "schema_name": "terminal_result",
            "schema_version": SCHEMA_VERSION,
            "state_revision": self.state_revision,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    event_type: str
    event_id: str
    previous_event_hash: str
    payload: dict[str, JsonValue]
    payload_sha256: str
    event_sha256: str


@dataclass(frozen=True)
class OnlineSeal:
    path: Path
    sha256: str
    final_event_hash: str
    terminal_reason: str


@dataclass(frozen=True)
class ArtifactRef:
    role: str
    path: str
    sha256: str
    byte_length: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "byte_length": self.byte_length,
            "path": self.path,
            "role": self.role,
            "sha256": self.sha256,
        }


def _sibling_json(action: PreparedExpansion) -> list[JsonValue]:
    return [
        {
            "cache_hit": sibling.cache_hit,
            "sibling_id": sibling.sibling_id,
            "strict_key": sibling.strict_key,
        }
        for sibling in action.siblings
    ]


def prepared_action_json(action: PreparedExpansion) -> dict[str, JsonValue]:
    return {
        "action_id": action.action_id,
        "dara_benefit": float_text(action.dara_benefit),
        "fifo_position": action.fifo_position,
        "parent_id": action.parent_id,
        "siblings": _sibling_json(action),
        "state_revision": action.state_revision,
        "v3_benefit": float_text(action.v3_benefit),
    }


def frontier_fingerprint(actions: tuple[PreparedExpansion, ...]) -> str:
    return canonical_sha256([prepared_action_json(action) for action in actions])


def cohort_fingerprint(action: PreparedExpansion) -> str:
    return canonical_sha256(
        {
            "action_id": action.action_id,
            "parent_id": action.parent_id,
            "siblings": _sibling_json(action),
            "state_revision": action.state_revision,
        }
    )


class RunJournal:
    """Atomic append-only online ledger and content-addressed artifact publisher."""

    def __init__(self, root: Path, manifest: RunManifest) -> None:
        self.root = root
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        root: Path,
        manifest: RunManifest,
        frozen_state: FrozenStateManifest,
    ) -> RunJournal:
        root.mkdir(parents=True, exist_ok=True)
        seal_path = root / "seal.json"
        if seal_path.exists():
            raise ArtifactValidationError("a sealed run is immutable")
        for path, payload in (
            (root / "run_manifest.json", manifest.to_json()),
            (root / "frozen_state_manifest.json", frozen_state.to_json()),
        ):
            encoded = canonical_json_bytes(payload)
            if path.exists() and path.read_bytes() != encoded:
                raise ArtifactValidationError(f"existing {path.name} does not match")
            if not path.exists():
                _atomic_write(path, encoded)
        events_path = root / "events.ndjson"
        if not events_path.exists():
            _atomic_write(events_path, b"")
        _read_events(events_path, expected_run_id=manifest.run_id)
        return cls(root, manifest)

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return _read_events(self.root / "events.ndjson", expected_run_id=self.manifest.run_id)

    def _append(self, event_type: str, payload: dict[str, JsonValue]) -> LedgerEvent:
        validate_online_payload(payload)
        if (self.root / "seal.json").exists():
            raise ArtifactValidationError("a sealed run is immutable")
        events = self.events
        sequence = len(events)
        previous_hash = events[-1].event_sha256 if events else GENESIS_EVENT_HASH
        payload_sha256 = canonical_sha256(payload)
        identity: dict[str, JsonValue] = {
            "event_type": event_type,
            "payload_sha256": payload_sha256,
            "previous_event_hash": previous_hash,
            "run_id": self.manifest.run_id,
            "sequence": sequence,
        }
        event_id = canonical_sha256(identity)
        envelope: dict[str, JsonValue] = {
            "contract_version": CONTRACT_VERSION,
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "payload_sha256": payload_sha256,
            "previous_event_hash": previous_hash,
            "run_id": self.manifest.run_id,
            "schema_name": "run_event",
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
        }
        event_sha256 = canonical_sha256(envelope)
        stored = dict(envelope)
        stored["event_sha256"] = event_sha256
        line = canonical_json_bytes(stored) + b"\n"
        current = (self.root / "events.ndjson").read_bytes()
        _atomic_write(self.root / "events.ndjson", current + line)
        return LedgerEvent(
            sequence=sequence,
            event_type=event_type,
            event_id=event_id,
            previous_event_hash=previous_hash,
            payload=payload,
            payload_sha256=payload_sha256,
            event_sha256=event_sha256,
        )

    @property
    def debit(self) -> int:
        return sum(event.event_type == "attempt_started" for event in self.events)

    def publish_artifact(self, role: str, payload: dict[str, JsonValue]) -> ArtifactRef:
        validate_online_payload(payload)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", role):
            raise ValueError("artifact role must be a stable lowercase identifier")
        encoded = canonical_json_bytes(payload)
        digest = _sha256(encoded)
        relative = f"artifacts/{role}/{digest.removeprefix('sha256:')}.json"
        path = self.root / relative
        if path.exists() and path.read_bytes() != encoded:
            raise ArtifactValidationError("content-addressed artifact collision")
        if not path.exists():
            _atomic_write(path, encoded)
        return ArtifactRef(role=role, path=relative, sha256=digest, byte_length=len(encoded))

    def read_artifact(self, reference: ArtifactRef) -> dict[str, JsonValue]:
        path = Path(reference.path)
        if path.is_absolute() or ".." in path.parts:
            raise ArtifactValidationError("artifact reference path is unsafe")
        raw = (self.root / path).read_bytes()
        if len(raw) != reference.byte_length or _sha256(raw) != reference.sha256:
            raise ArtifactValidationError("artifact reference digest mismatch")
        return _read_json(self.root / path)

    def record_decision(
        self,
        *,
        state_revision: int,
        checkpoint_id: str,
        debit: int,
        validated_cache_keys: frozenset[str],
        actions: tuple[PreparedExpansion, ...],
        decision: SchedulingDecision,
    ) -> LedgerEvent:
        if decision.arm is not self.manifest.arm:
            raise ArtifactValidationError("decision arm does not match run manifest")
        if decision.remaining_budget != self.manifest.budget - debit:
            raise ArtifactValidationError("decision remaining budget does not match debit")
        if tuple(item.action_id for item in decision.assessments) != tuple(
            action.action_id for action in actions
        ):
            raise ArtifactValidationError("decision assessment set does not match frontier")
        assessments: list[JsonValue] = []
        for assessment in decision.assessments:
            assessments.append(
                {
                    "action_id": assessment.action_id,
                    "benefit": float_text(assessment.benefit),
                    "eligible": assessment.eligible,
                    "new_call_cost": assessment.new_call_cost,
                    "ordering_key": [
                        float_text(float(value)) if index < 3 else str(value)
                        for index, value in enumerate(assessment.ordering_key)
                    ],
                    "rejection_reason": assessment.rejection_reason,
                }
            )
        return self._append(
            "decision_snapshot",
            {
                "actions": [prepared_action_json(action) for action in actions],
                "arm": decision.arm.value,
                "assessments": assessments,
                "checkpoint_id": checkpoint_id,
                "debit": debit,
                "frontier_fingerprint": frontier_fingerprint(actions),
                "remaining_budget": decision.remaining_budget,
                "state_revision": state_revision,
                "validated_cache_keys": cast(list[JsonValue], sorted(validated_cache_keys)),
            },
        )

    def record_selection(
        self,
        *,
        snapshot: LedgerEvent,
        selected_action_id: str | None,
        stop_reason: str | None,
    ) -> LedgerEvent:
        if snapshot.event_type != "decision_snapshot":
            raise ArtifactValidationError("selection must reference a decision snapshot")
        return self._append(
            "action_selected",
            {
                "decision_event_hash": snapshot.event_sha256,
                "selected_action_id": selected_action_id,
                "stop_reason": stop_reason,
            },
        )

    def record_attempt_started(self, *, action_id: str, strict_key: str) -> LedgerEvent:
        debit_before = self.debit
        if debit_before >= self.manifest.budget:
            raise ArtifactValidationError("attempt would exceed the branch budget")
        attempt_identity: dict[str, JsonValue] = {
            "action_id": action_id,
            "debit_before": debit_before,
            "run_id": self.manifest.run_id,
            "strict_key": strict_key,
        }
        attempt_id = canonical_sha256(attempt_identity)
        return self._append(
            "attempt_started",
            {
                "action_id": action_id,
                "attempt_id": attempt_id,
                "debit_after": debit_before + 1,
                "debit_before": debit_before,
                "strict_key": strict_key,
            },
        )

    def record_attempt_outcome(
        self,
        *,
        attempt: LedgerEvent,
        outcome: str,
        result_reference: ArtifactRef | None = None,
        failure_class: str | None = None,
    ) -> LedgerEvent:
        if attempt.event_type != "attempt_started":
            raise ArtifactValidationError("attempt outcome must reference attempt_started")
        if outcome not in {"succeeded", "failed", "indeterminate"}:
            raise ValueError("unsupported attempt outcome")
        attempt_id = attempt.payload.get("attempt_id")
        strict_key = attempt.payload.get("strict_key")
        if not isinstance(attempt_id, str) or not isinstance(strict_key, str):
            raise ArtifactValidationError("attempt identity is malformed")
        if outcome == "succeeded" and result_reference is None:
            raise ValueError("successful attempt requires a result reference")
        if outcome != "succeeded" and result_reference is not None:
            raise ValueError("unsuccessful attempt cannot reference a result")
        return self._append(
            f"attempt_{outcome}",
            {
                "attempt_id": attempt_id,
                "failure_class": failure_class,
                "result_reference": (
                    result_reference.to_json() if result_reference is not None else None
                ),
                "strict_key": strict_key,
            },
        )

    def record_cache_hit(
        self,
        *,
        action_id: str,
        strict_key: str,
        metadata_digest: str,
    ) -> LedgerEvent:
        return self._append(
            "cache_hit_validated",
            {
                "action_id": action_id,
                "debit": self.debit,
                "metadata_digest": metadata_digest,
                "strict_key": strict_key,
            },
        )

    def record_transition(
        self,
        *,
        action: PreparedExpansion,
        state_revision_before: int,
        state_revision_after: int,
        checkpoint: ArtifactRef,
        next_frontier_fingerprint: str,
        terminal_candidate: dict[str, JsonValue] | None,
    ) -> LedgerEvent:
        fingerprint = cohort_fingerprint(action)
        return self._append(
            "transition_committed",
            {
                "checkpoint": checkpoint.to_json(),
                "committed_cohort_fingerprint": fingerprint,
                "debit": self.debit,
                "executed_cohort_fingerprint": fingerprint,
                "next_frontier_fingerprint": next_frontier_fingerprint,
                "selected_action_id": action.action_id,
                "selected_cohort_fingerprint": fingerprint,
                "state_revision_after": state_revision_after,
                "state_revision_before": state_revision_before,
                "terminal_candidate": terminal_candidate,
            },
        )

    def terminate(self, terminal: TerminalOutput) -> OnlineSeal:
        if terminal.pattern_id != self.manifest.pattern_id:
            raise ArtifactValidationError("terminal pattern does not match run manifest")
        if terminal.debit > self.manifest.budget:
            raise ArtifactValidationError("terminal debit exceeds branch budget")
        if terminal.debit != self.debit:
            raise ArtifactValidationError("terminal debit does not match durable attempts")
        final_event = self._append(
            "run_terminated",
            {
                "debit": terminal.debit,
                "state_revision": terminal.state_revision,
                "stop_reason": terminal.stop_reason,
                "terminal_kind": terminal.kind,
            },
        )
        terminal_path = self.root / "terminal_result.json"
        _atomic_write(terminal_path, canonical_json_bytes(terminal.to_json()))
        authoritative = tuple(
            path
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.name != "seal.json"
        )
        files: list[JsonValue] = []
        for path in authoritative:
            relative = path.relative_to(self.root).as_posix()
            data = path.read_bytes()
            files.append(
                {
                    "byte_length": len(data),
                    "path": relative,
                    "sha256": _sha256(data),
                }
            )
        seal_payload: dict[str, JsonValue] = {
            "contract_version": CONTRACT_VERSION,
            "files": files,
            "final_event_hash": final_event.event_sha256,
            "run_id": self.manifest.run_id,
            "schema_name": "online_seal",
            "schema_version": SCHEMA_VERSION,
            "terminal_reason": terminal.stop_reason,
        }
        encoded = canonical_json_bytes(seal_payload)
        seal_path = self.root / "seal.json"
        _atomic_write(seal_path, encoded)
        return OnlineSeal(
            path=seal_path,
            sha256=_sha256(encoded),
            final_event_hash=final_event.event_sha256,
            terminal_reason=terminal.stop_reason,
        )


def _read_events(path: Path, *, expected_run_id: str) -> tuple[LedgerEvent, ...]:
    try:
        lines = path.read_bytes().splitlines()
    except OSError as error:
        raise ArtifactValidationError("cannot read event ledger") from error
    events: list[LedgerEvent] = []
    previous_hash = GENESIS_EVENT_HASH
    expected_fields = {
        "contract_version",
        "event_id",
        "event_sha256",
        "event_type",
        "payload",
        "payload_sha256",
        "previous_event_hash",
        "run_id",
        "schema_name",
        "schema_version",
        "sequence",
    }
    for sequence, line in enumerate(lines):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ArtifactValidationError("event ledger contains invalid JSON") from error
        if not isinstance(raw, dict):
            raise ArtifactValidationError("event ledger line must be an object")
        value = cast(dict[str, JsonValue], raw)
        _expect_keys(value, expected_fields, schema="run_event")
        if canonical_json_bytes(value) != line:
            raise ArtifactValidationError("event ledger line is not canonical")
        if value["sequence"] != sequence:
            raise ArtifactValidationError("event sequence gap or reorder detected")
        if value["run_id"] != expected_run_id:
            raise ArtifactValidationError("event run ID mismatch")
        if value["previous_event_hash"] != previous_hash:
            raise ArtifactValidationError("event previous hash mismatch")
        payload = value["payload"]
        if not isinstance(payload, dict):
            raise ArtifactValidationError("event payload must be an object")
        typed_payload = payload
        if value["payload_sha256"] != canonical_sha256(typed_payload):
            raise ArtifactValidationError("event payload digest mismatch")
        without_event_hash = dict(value)
        event_hash = without_event_hash.pop("event_sha256")
        if event_hash != canonical_sha256(without_event_hash):
            raise ArtifactValidationError("event hash mismatch")
        event_type = value["event_type"]
        event_id = value["event_id"]
        payload_sha256 = value["payload_sha256"]
        if not all(
            isinstance(item, str) for item in (event_type, event_id, event_hash, payload_sha256)
        ):
            raise ArtifactValidationError("event identity fields must be strings")
        events.append(
            LedgerEvent(
                sequence=sequence,
                event_type=cast(str, event_type),
                event_id=cast(str, event_id),
                previous_event_hash=previous_hash,
                payload=typed_payload,
                payload_sha256=payload_sha256,
                event_sha256=event_hash,
            )
        )
        previous_hash = event_hash
    return tuple(events)


def validate_seal(path: Path) -> OnlineSeal:
    seal = _read_json(path)
    _expect_keys(
        seal,
        {
            "contract_version",
            "files",
            "final_event_hash",
            "run_id",
            "schema_name",
            "schema_version",
            "terminal_reason",
        },
        schema="online_seal",
    )
    root = path.parent
    files = seal["files"]
    if not isinstance(files, list):
        raise ArtifactValidationError("seal files must be a list")
    sealed_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ArtifactValidationError("seal file reference must be an object")
        typed_item = item
        _expect_keys(typed_item, {"byte_length", "path", "sha256"}, schema="seal_file")
        relative = typed_item["path"]
        expected_length = typed_item["byte_length"]
        expected_digest = typed_item["sha256"]
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ArtifactValidationError("seal contains an unsafe artifact path")
        if relative in sealed_paths:
            raise ArtifactValidationError("seal contains a duplicate artifact path")
        sealed_paths.add(relative)
        data = (root / relative).read_bytes()
        if len(data) != expected_length or _sha256(data) != expected_digest:
            raise ArtifactValidationError(f"sealed artifact digest mismatch: {relative}")
    required = {
        "events.ndjson",
        "frozen_state_manifest.json",
        "run_manifest.json",
        "terminal_result.json",
    }
    if not required.issubset(sealed_paths):
        raise ArtifactValidationError("seal omits an authoritative online artifact")
    run_manifest = _read_json(root / "run_manifest.json")
    run_id = run_manifest.get("run_id")
    if not isinstance(run_id, str) or run_id != seal["run_id"]:
        raise ArtifactValidationError("seal run ID mismatch")
    events = _read_events(root / "events.ndjson", expected_run_id=run_id)
    if not events or events[-1].event_type != "run_terminated":
        raise ArtifactValidationError("sealed ledger lacks a terminal event")
    if events[-1].event_sha256 != seal["final_event_hash"]:
        raise ArtifactValidationError("seal final event hash mismatch")
    terminal = _read_json(root / "terminal_result.json")
    if terminal.get("stop_reason") != seal["terminal_reason"]:
        raise ArtifactValidationError("terminal reason does not match seal")
    encoded = canonical_json_bytes(seal)
    terminal_reason = seal["terminal_reason"]
    final_event_hash = seal["final_event_hash"]
    if not isinstance(terminal_reason, str) or not isinstance(final_event_hash, str):
        raise ArtifactValidationError("seal identity fields must be strings")
    return OnlineSeal(
        path=path,
        sha256=_sha256(encoded),
        final_event_hash=final_event_hash,
        terminal_reason=terminal_reason,
    )


def read_canonical_json(path: Path) -> dict[str, JsonValue]:
    return _read_json(path)


def read_ledger(path: Path, *, run_id: str) -> tuple[LedgerEvent, ...]:
    return _read_events(path, expected_run_id=run_id)


def float_text(value: float) -> str:
    if not math.isfinite(value):
        raise ArtifactValidationError("scientific value must be finite")
    return format(value, ".17g")


def parse_float_text(value: JsonValue, *, field: str) -> float:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field} must be a decimal string")
    try:
        parsed = float(value)
    except ValueError as error:
        raise ArtifactValidationError(f"{field} is not a decimal string") from error
    if not math.isfinite(parsed):
        raise ArtifactValidationError(f"{field} must be finite")
    return parsed


__all__ = (
    "ArtifactRef",
    "ArtifactValidationError",
    "CONTRACT_VERSION",
    "FrozenStateManifest",
    "JsonValue",
    "LedgerEvent",
    "OnlineSeal",
    "RunJournal",
    "RunManifest",
    "TerminalOutput",
    "canonical_json_bytes",
    "cohort_fingerprint",
    "frontier_fingerprint",
    "canonical_sha256",
    "float_text",
    "prepared_action_json",
    "read_canonical_json",
    "read_ledger",
    "parse_float_text",
    "validate_online_names",
    "validate_online_payload",
    "validate_seal",
)

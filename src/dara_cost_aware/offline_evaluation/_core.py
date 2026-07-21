from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dara_cost_aware.artifacts import (
    ArtifactValidationError,
    JsonValue,
    canonical_sha256,
    read_canonical_json,
    validate_seal,
)


@dataclass(frozen=True)
class EvaluationResult:
    online_seal_sha256: str
    gt_manifest_sha256: str
    equivalence_contract_version: str
    pattern_id: str
    exact_set_success: bool

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "equivalence_contract_version": self.equivalence_contract_version,
            "exact_set_success": self.exact_set_success,
            "gt_manifest_sha256": self.gt_manifest_sha256,
            "online_seal_sha256": self.online_seal_sha256,
            "pattern_id": self.pattern_id,
            "schema_name": "evaluation_result",
            "schema_version": 1,
        }


def _string(value: JsonValue, *, field: str) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field} must be a string")
    return value


def _phase_set(value: JsonValue) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArtifactValidationError("equivalent phase set must be a string list")
    phases = cast(list[str], value)
    if not phases or len(phases) != len(set(phases)):
        raise ArtifactValidationError("equivalent phase set must be nonempty and unique")
    return frozenset(phases)


def evaluate_sealed_run(seal_path: Path, gt_manifest_path: Path) -> EvaluationResult:
    """Join GT only after validating a sealed online result; never write online files."""

    seal = validate_seal(seal_path)
    root = seal_path.parent
    run_manifest = read_canonical_json(root / "run_manifest.json")
    terminal = read_canonical_json(root / "terminal_result.json")
    gt_manifest = read_canonical_json(gt_manifest_path)
    required_gt_fields = {
        "equivalence_contract_version",
        "patterns",
        "pilot_version",
        "schema_name",
        "schema_version",
    }
    if set(gt_manifest) != required_gt_fields:
        raise ArtifactValidationError("GT manifest fields differ")
    if gt_manifest.get("schema_name") != "gt_manifest" or gt_manifest.get("schema_version") != 1:
        raise ArtifactValidationError("GT manifest schema is unsupported")
    if gt_manifest.get("pilot_version") != run_manifest.get("pilot_version"):
        raise ArtifactValidationError("GT and online pilot versions differ")
    pattern_id = _string(run_manifest.get("pattern_id"), field="online pattern ID")
    if terminal.get("pattern_id") != pattern_id:
        raise ArtifactValidationError("terminal and run pattern IDs differ")
    patterns = gt_manifest.get("patterns")
    if not isinstance(patterns, dict) or pattern_id not in patterns:
        raise ArtifactValidationError("GT manifest lacks the online pattern")
    equivalent_values = patterns[pattern_id]
    if not isinstance(equivalent_values, list) or not equivalent_values:
        raise ArtifactValidationError("GT pattern requires equivalent phase sets")
    equivalent_sets = tuple(_phase_set(value) for value in equivalent_values)

    terminal_kind = _string(terminal.get("kind"), field="terminal kind")
    if terminal_kind == "result":
        accepted = _phase_set(terminal.get("accepted_phase_ids"))
        exact_set_success = accepted in equivalent_sets
    elif terminal_kind == "abstention":
        exact_set_success = False
    else:
        raise ArtifactValidationError("terminal kind is unsupported")

    return EvaluationResult(
        online_seal_sha256=seal.sha256,
        gt_manifest_sha256=canonical_sha256(gt_manifest),
        equivalence_contract_version=_string(
            gt_manifest.get("equivalence_contract_version"),
            field="equivalence contract version",
        ),
        pattern_id=pattern_id,
        exact_set_success=exact_set_success,
    )


__all__ = ("EvaluationResult", "evaluate_sealed_run")

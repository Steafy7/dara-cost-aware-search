import shutil
from pathlib import Path

import pytest

from dara_cost_aware.artifacts import (
    ArtifactValidationError,
    FrozenStateManifest,
    RunJournal,
    RunManifest,
    TerminalOutput,
    validate_seal,
)
from dara_cost_aware.scheduling import Arm


FROZEN_STATE_ID = "sha256:" + "1" * 64
FRONTIER_FINGERPRINT = "sha256:" + "2" * 64


def manifest() -> RunManifest:
    return RunManifest(
        run_id="pilot-v1/pattern-a/original-dara/B5/attempt-1",
        pilot_version="pilot-v1",
        pattern_id="pattern-a",
        arm=Arm.ORIGINAL_DARA,
        budget=5,
        run_attempt_id="attempt-1",
        frozen_state_id=FROZEN_STATE_ID,
        provenance=(("runner_contract", "ticket-14-v1"),),
        environment_keys=(),
    )


def frozen_state() -> FrozenStateManifest:
    return FrozenStateManifest(
        frozen_state_id=FROZEN_STATE_ID,
        state_revision=0,
        frontier_fingerprint=FRONTIER_FINGERPRINT,
    )


def test_online_seal_detects_tampering_and_is_portable(tmp_path: Path) -> None:
    run_root = tmp_path / "original"
    journal = RunJournal.create(run_root, manifest(), frozen_state())
    seal = journal.terminate(
        TerminalOutput.abstention(
            pattern_id="pattern-a",
            stop_reason="frontier_exhausted",
            state_revision=0,
            debit=0,
        )
    )

    validated = validate_seal(seal.path)
    relocated_root = tmp_path / "relocated"
    shutil.copytree(run_root, relocated_root)
    relocated = validate_seal(relocated_root / "seal.json")

    assert validated.sha256 == seal.sha256
    assert relocated.sha256 == seal.sha256
    assert str(run_root) not in (run_root / "seal.json").read_text()

    events_path = relocated_root / "events.ndjson"
    events_path.write_text(
        events_path.read_text().replace("frontier_exhausted", "branch_budget_exhausted")
    )

    with pytest.raises(ArtifactValidationError, match="digest|hash"):
        validate_seal(relocated_root / "seal.json")


@pytest.mark.parametrize(
    "stop_reason",
    (
        "frontier_exhausted",
        "branch_budget_exhausted",
        "no_fitting_atomic_action",
        "explicit_abstention",
        "scientific_transition_failed",
        "indeterminate_attempt",
        "provenance_mismatch",
        "invariant_violation",
    ),
)
def test_every_shared_stop_reason_round_trips(
    tmp_path: Path,
    stop_reason: str,
) -> None:
    journal = RunJournal.create(tmp_path / stop_reason, manifest(), frozen_state())
    seal = journal.terminate(
        TerminalOutput.abstention(
            pattern_id="pattern-a",
            stop_reason=stop_reason,
            state_revision=0,
            debit=0,
        )
    )

    assert validate_seal(seal.path).terminal_reason == stop_reason


@pytest.mark.parametrize("mutation", ("edit", "delete", "insert", "reorder"))
def test_ledger_edit_delete_insert_and_reorder_are_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_root = tmp_path / "source"
    journal = RunJournal.create(run_root, manifest(), frozen_state())
    attempt = journal.record_attempt_started(action_id="action-a", strict_key="sha256:key-a")
    journal.record_attempt_outcome(
        attempt=attempt,
        outcome="failed",
        failure_class="SyntheticFailure",
    )
    seal = journal.terminate(
        TerminalOutput.abstention(
            pattern_id="pattern-a",
            stop_reason="scientific_transition_failed",
            state_revision=0,
            debit=1,
        )
    )
    altered = tmp_path / mutation
    shutil.copytree(run_root, altered)
    lines = (altered / "events.ndjson").read_text().splitlines()
    if mutation == "edit":
        lines[0] = lines[0].replace("action-a", "action-b")
    elif mutation == "delete":
        del lines[0]
    elif mutation == "insert":
        lines.insert(1, lines[0])
    else:
        lines[0], lines[1] = lines[1], lines[0]
    (altered / "events.ndjson").write_text("\n".join(lines) + "\n")

    with pytest.raises(ArtifactValidationError, match="digest|hash"):
        validate_seal(altered / seal.path.name)

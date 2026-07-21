import subprocess
from pathlib import Path
import sys

import pytest

from dara_cost_aware.artifacts import (
    ArtifactValidationError,
    FrozenStateManifest,
    OnlineSeal,
    RunJournal,
    RunManifest,
    TerminalOutput,
    canonical_json_bytes,
    validate_online_payload,
)
from dara_cost_aware.offline_evaluation import evaluate_sealed_run
from dara_cost_aware.scheduling import Arm


def sealed_result(tmp_path: Path) -> OnlineSeal:
    frozen_state_id = "sha256:" + "a" * 64
    journal = RunJournal.create(
        tmp_path / "online",
        RunManifest(
            run_id="pilot-v1/pattern-a/original_dara/B5/attempt-1",
            pilot_version="pilot-v1",
            pattern_id="pattern-a",
            arm=Arm.ORIGINAL_DARA,
            budget=5,
            run_attempt_id="attempt-1",
            frozen_state_id=frozen_state_id,
            provenance=(("runner_contract", "ticket-14-v1"),),
            environment_keys=(),
        ),
        FrozenStateManifest(
            frozen_state_id=frozen_state_id,
            state_revision=0,
            frontier_fingerprint="sha256:" + "b" * 64,
        ),
    )
    return journal.terminate(
        TerminalOutput.result(
            pattern_id="pattern-a",
            stop_reason="frontier_exhausted",
            state_revision=0,
            debit=0,
            accepted_phase_ids=("phase-a", "phase-b"),
            rwp="9.5",
        )
    )


def test_online_manifest_rejects_gt_like_inputs_and_imports_no_evaluator() -> None:
    with pytest.raises(ArtifactValidationError, match="GT-like"):
        RunManifest(
            run_id="run",
            pilot_version="pilot-v1",
            pattern_id="pattern-a",
            arm=Arm.ORIGINAL_DARA,
            budget=5,
            run_attempt_id="attempt-1",
            frozen_state_id="sha256:" + "a" * 64,
            provenance=(("GT_PATH", "/forbidden"),),
            environment_keys=(),
        )

    with pytest.raises(ArtifactValidationError, match="GT-like"):
        validate_online_payload({"nested": {"groundTruthPath": "/forbidden", "oracleScore": "1"}})

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import dara_cost_aware.verification; "
                "assert 'dara_cost_aware.offline_evaluation' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_offline_evaluation_validates_seal_first_and_never_mutates_online_files(
    tmp_path: Path,
) -> None:
    seal = sealed_result(tmp_path)
    gt_path = tmp_path / "offline" / "gt_manifest.json"
    gt_path.parent.mkdir()
    gt_path.write_bytes(
        canonical_json_bytes(
            {
                "equivalence_contract_version": "equivalence-v1",
                "patterns": {"pattern-a": [["phase-a", "phase-b"]]},
                "pilot_version": "pilot-v1",
                "schema_name": "gt_manifest",
                "schema_version": 1,
            }
        )
    )
    online_before = {
        path.relative_to(seal.path.parent): path.read_bytes()
        for path in seal.path.parent.rglob("*")
        if path.is_file()
    }

    first = evaluate_sealed_run(seal.path, gt_path)
    second = evaluate_sealed_run(seal.path, gt_path)

    assert first == second
    assert first.exact_set_success
    assert first.online_seal_sha256 == seal.sha256
    assert online_before == {
        path.relative_to(seal.path.parent): path.read_bytes()
        for path in seal.path.parent.rglob("*")
        if path.is_file()
    }

    events_path = seal.path.parent / "events.ndjson"
    events_path.write_text(events_path.read_text().replace("frontier_exhausted", "tampered"))
    gt_path.unlink()

    with pytest.raises(ArtifactValidationError, match="digest|hash"):
        evaluate_sealed_run(seal.path, gt_path)

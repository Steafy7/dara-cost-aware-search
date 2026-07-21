from collections.abc import Callable
from pathlib import Path

import pytest

from dara_cost_aware.artifacts import ArtifactValidationError, JsonValue
from dara_cost_aware.replay import replay_run, verify_completion_equivalence
from dara_cost_aware.refinement_store import StrictRefinementIdentity
from dara_cost_aware.scheduling import Arm, PreparedExpansion, PreparedSibling
from dara_cost_aware.verification import (
    TerminalCandidate,
    VerificationRunSpec,
    VerificationScenario,
    VerificationState,
    run_verification_arm,
)


def refinement_identity(name: str) -> StrictRefinementIdentity:
    return StrictRefinementIdentity.create(
        pattern_digest="sha256:pattern-a",
        ordered_input_digests=("sha256:pattern-a", f"sha256:{name}"),
        scientific_parameters=(("wavelength", "1.5406"),),
        provenance=(
            ("bgmn", "sha256:bgmn-v1"),
            ("dara", "9473ee240daac0491fbe4294948aedd19555d6ec"),
            ("instrument", "sha256:instrument-v1"),
        ),
    )


def scenario(
    *,
    candidate: Callable[[VerificationState], TerminalCandidate | None] | None = None,
) -> VerificationScenario:
    expensive = (refinement_identity("phase-a"), refinement_identity("shared"))
    cheap = (refinement_identity("phase-cheap"),)
    terminal = (expensive[1],)
    identities = tuple(dict.fromkeys(expensive + cheap + terminal))

    def prepare(state: VerificationState) -> tuple[PreparedExpansion, ...]:
        selected = set(state.selected_action_ids)

        def action(
            action_id: str,
            *,
            fifo_position: int,
            dara_benefit: float,
            v3_benefit: float,
            requests: tuple[StrictRefinementIdentity, ...],
        ) -> PreparedExpansion:
            return PreparedExpansion(
                action_id=action_id,
                parent_id=f"parent-{action_id}",
                state_revision=state.revision,
                fifo_position=fifo_position,
                dara_benefit=dara_benefit,
                v3_benefit=v3_benefit,
                siblings=tuple(
                    PreparedSibling(
                        sibling_id=f"{action_id}-sibling-{index}",
                        strict_key=request.strict_key,
                        cache_hit=request.strict_key in state.validated_cache_keys,
                    )
                    for index, request in enumerate(requests)
                ),
            )

        actions = (
            action(
                "expensive",
                fifo_position=0,
                dara_benefit=8,
                v3_benefit=2,
                requests=expensive,
            ),
            action(
                "cheap",
                fifo_position=1,
                dara_benefit=3,
                v3_benefit=7,
                requests=cheap,
            ),
            action(
                "terminal",
                fifo_position=2,
                dara_benefit=2,
                v3_benefit=12,
                requests=terminal,
            ),
        )
        return tuple(item for item in actions if item.action_id not in selected)

    def default_candidate(state: VerificationState) -> TerminalCandidate | None:
        if "terminal" not in state.selected_action_ids:
            return None
        return TerminalCandidate(
            accepted_phase_ids=("phase-a", "phase-b"),
            rwp="9.5",
            scientifically_terminal=True,
        )

    return VerificationScenario(
        pattern_id="pattern-a",
        frozen_state_id="sha256:" + "a" * 64,
        identities=identities,
        prepare_frontier=prepare,
        terminal_candidate=candidate or default_candidate,
    )


def spec(arm: Arm, *, budget: int, attempt: str = "attempt-1") -> VerificationRunSpec:
    return VerificationRunSpec(
        pilot_version="pilot-v1",
        arm=arm,
        budget=budget,
        run_attempt_id=attempt,
        provenance=(("runner_contract", "ticket-14-v1"),),
    )


def test_atomic_deferral_and_exact_attempt_debit_replay_without_bgmn(tmp_path: Path) -> None:
    completed = run_verification_arm(
        spec(Arm.ORIGINAL_DARA, budget=1),
        scenario(),
        tmp_path / "run",
    )
    assert completed.seal is not None

    report = replay_run(completed.seal.path)

    assert report.valid
    assert report.selected_action_ids == ("cheap",)
    assert report.debit == 1
    assert len(report.attempted_strict_keys) == 1
    assert report.stop_reason == "branch_budget_exhausted"


def test_clean_resume_is_trace_equivalent_and_indeterminate_resume_fails_closed(
    tmp_path: Path,
) -> None:
    uninterrupted = run_verification_arm(
        spec(Arm.H1_DARA, budget=10),
        scenario(),
        tmp_path / "uninterrupted",
    )
    assert uninterrupted.seal is not None

    paused = run_verification_arm(
        spec(Arm.H1_DARA, budget=10),
        scenario(),
        tmp_path / "resumed",
        pause_after_commits=1,
    )
    assert paused.seal is None
    incompatible = VerificationRunSpec(
        pilot_version="pilot-v1",
        arm=Arm.H1_DARA,
        budget=10,
        run_attempt_id="attempt-1",
        provenance=(("runner_contract", "incompatible"),),
    )
    with pytest.raises(ArtifactValidationError, match="manifest"):
        run_verification_arm(incompatible, scenario(), tmp_path / "resumed")
    resumed = run_verification_arm(
        spec(Arm.H1_DARA, budget=10),
        scenario(),
        tmp_path / "resumed",
    )
    assert resumed.seal is not None
    assert (tmp_path / "uninterrupted" / "events.ndjson").read_bytes() == (
        tmp_path / "resumed" / "events.ndjson"
    ).read_bytes()
    assert uninterrupted.seal.sha256 == resumed.seal.sha256

    def crash_after_dispatch(_: StrictRefinementIdentity) -> dict[str, JsonValue]:
        raise SystemExit("synthetic process loss")

    with pytest.raises(SystemExit, match="synthetic process loss"):
        run_verification_arm(
            spec(Arm.ORIGINAL_DARA, budget=10),
            scenario(),
            tmp_path / "indeterminate",
            dispatch=crash_after_dispatch,
        )

    recovered = run_verification_arm(
        spec(Arm.ORIGINAL_DARA, budget=10),
        scenario(),
        tmp_path / "indeterminate",
    )
    assert recovered.seal is not None
    recovery_report = replay_run(recovered.seal.path)
    assert recovery_report.stop_reason == "indeterminate_attempt"
    assert recovery_report.debit == 1
    assert recovery_report.selected_action_ids == ()


def test_failed_dispatch_is_charged_once_and_stops_explicitly(tmp_path: Path) -> None:
    def fail(_: StrictRefinementIdentity) -> dict[str, JsonValue]:
        raise RuntimeError("synthetic refinement failure")

    completed = run_verification_arm(
        spec(Arm.ORIGINAL_DARA, budget=10),
        scenario(),
        tmp_path / "failed",
        dispatch=fail,
    )
    assert completed.seal is not None
    report = replay_run(completed.seal.path)

    assert report.debit == 1
    assert len(report.attempted_strict_keys) == 1
    assert report.stop_reason == "scientific_transition_failed"
    assert report.selected_action_ids == ()


def test_exhaustive_completion_is_reachable_set_equivalent_for_all_arms(
    tmp_path: Path,
) -> None:
    reports = []
    for arm in Arm:
        completed = run_verification_arm(
            spec(arm, budget=10),
            scenario(),
            tmp_path / arm.value,
        )
        assert completed.seal is not None
        reports.append(replay_run(completed.seal.path))

    equivalence = verify_completion_equivalence(tuple(reports))

    assert equivalence.equivalent
    assert equivalence.reachable_action_ids == frozenset({"expensive", "cheap", "terminal"})
    assert len(equivalence.attempted_strict_keys) == 3

    limited = run_verification_arm(
        spec(Arm.ORIGINAL_DARA, budget=1, attempt="limited"),
        scenario(),
        tmp_path / "limited",
    )
    assert limited.seal is not None
    with pytest.raises(ArtifactValidationError, match="exhaustive"):
        verify_completion_equivalence((reports[0], replay_run(limited.seal.path)))


def test_pending_candidate_is_diagnostic_only_and_serializes_as_abstention(
    tmp_path: Path,
) -> None:
    def pending_candidate(_: VerificationState) -> TerminalCandidate:
        return TerminalCandidate(
            accepted_phase_ids=("phase-provisional",),
            rwp="1.0",
            scientifically_terminal=False,
        )

    completed = run_verification_arm(
        spec(Arm.ORIGINAL_DARA, budget=1),
        scenario(candidate=pending_candidate),
        tmp_path / "pending",
    )
    assert completed.seal is not None
    report = replay_run(completed.seal.path)

    assert report.terminal_kind == "abstention"
    assert report.provisional_hypothesis_ids == ("phase-provisional",)

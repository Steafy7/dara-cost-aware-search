from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dara_cost_aware.artifacts import (
    ArtifactRef,
    ArtifactValidationError,
    FrozenStateManifest,
    JsonValue,
    OnlineSeal,
    LedgerEvent,
    RunJournal,
    RunManifest,
    TerminalOutput,
    canonical_sha256,
    cohort_fingerprint,
    frontier_fingerprint,
    read_canonical_json,
    validate_seal,
)
from dara_cost_aware.refinement_store import (
    CacheStatus,
    StrictRefinementIdentity,
    StrictRefinementStore,
)
from dara_cost_aware.scheduling import Arm, PreparedExpansion, schedule_next


@dataclass(frozen=True)
class VerificationState:
    revision: int
    selected_action_ids: tuple[str, ...]
    validated_cache_keys: frozenset[str]

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")


@dataclass(frozen=True)
class TerminalCandidate:
    accepted_phase_ids: tuple[str, ...]
    rwp: str
    scientifically_terminal: bool

    def __post_init__(self) -> None:
        if not self.accepted_phase_ids or not self.rwp:
            raise ValueError("terminal candidate requires phase IDs and Rwp")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "accepted_phase_ids": list(self.accepted_phase_ids),
            "rwp": self.rwp,
            "scientifically_terminal": self.scientifically_terminal,
        }


FrontierPreparer = Callable[[VerificationState], tuple[PreparedExpansion, ...]]
CandidateReader = Callable[[VerificationState], TerminalCandidate | None]
Dispatch = Callable[[StrictRefinementIdentity], dict[str, JsonValue]]


@dataclass(frozen=True)
class VerificationScenario:
    """Deterministic no-BGMN scientific adapter used by the invariant gate."""

    pattern_id: str
    frozen_state_id: str
    identities: tuple[StrictRefinementIdentity, ...]
    prepare_frontier: FrontierPreparer
    terminal_candidate: CandidateReader

    def __post_init__(self) -> None:
        if not self.pattern_id or not self.frozen_state_id:
            raise ValueError("verification scenario identifiers must not be empty")
        keys = tuple(identity.strict_key for identity in self.identities)
        if len(keys) != len(set(keys)):
            raise ValueError("verification refinement identities must be unique")
        if not callable(self.prepare_frontier) or not callable(self.terminal_candidate):
            raise ValueError("verification scenario adapters must be callable")


@dataclass(frozen=True)
class VerificationRunSpec:
    pilot_version: str
    arm: Arm
    budget: int
    run_attempt_id: str
    provenance: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.pilot_version or not self.run_attempt_id:
            raise ValueError("verification run identifiers must not be empty")
        if self.budget < 0:
            raise ValueError("budget must be nonnegative")


@dataclass(frozen=True)
class VerificationRun:
    seal: OnlineSeal | None
    selected_action_ids: tuple[str, ...]
    debit: int
    stop_reason: str | None


def _default_dispatch(identity: StrictRefinementIdentity) -> dict[str, JsonValue]:
    return {
        "result_kind": "deterministic_no_bgmn_fixture",
        "strict_key": identity.strict_key,
    }


def _artifact_ref(value: JsonValue) -> ArtifactRef:
    if not isinstance(value, dict):
        raise ArtifactValidationError("checkpoint reference is malformed")
    required = {"byte_length", "path", "role", "sha256"}
    if set(value) != required:
        raise ArtifactValidationError("checkpoint reference fields differ")
    role = value["role"]
    path = value["path"]
    sha256 = value["sha256"]
    byte_length = value["byte_length"]
    if not isinstance(role, str) or not isinstance(path, str) or not isinstance(sha256, str):
        raise ArtifactValidationError("checkpoint reference identifiers must be strings")
    if not isinstance(byte_length, int):
        raise ArtifactValidationError("checkpoint byte length must be an integer")
    return ArtifactRef(role=role, path=path, sha256=sha256, byte_length=byte_length)


def _restore_state(journal: RunJournal) -> tuple[VerificationState, str]:
    transitions = tuple(
        event for event in journal.events if event.event_type == "transition_committed"
    )
    if not transitions:
        return VerificationState(0, (), frozenset()), journal.manifest.frozen_state_id
    reference = _artifact_ref(transitions[-1].payload.get("checkpoint"))
    checkpoint = journal.read_artifact(reference)
    required = {
        "debit",
        "last_event_hash",
        "next_frontier_fingerprint",
        "schema_name",
        "schema_version",
        "selected_action_ids",
        "state_revision",
        "terminal_candidate",
        "validated_cache_keys",
    }
    if set(checkpoint) != required:
        raise ArtifactValidationError("checkpoint fields differ")
    revision = checkpoint["state_revision"]
    selected = checkpoint["selected_action_ids"]
    validated = checkpoint["validated_cache_keys"]
    if (
        not isinstance(revision, int)
        or not isinstance(selected, list)
        or not isinstance(validated, list)
    ):
        raise ArtifactValidationError("checkpoint state is malformed")
    if not all(isinstance(value, str) for value in selected + validated):
        raise ArtifactValidationError("checkpoint identifiers must be strings")
    return (
        VerificationState(
            revision=revision,
            selected_action_ids=tuple(cast(list[str], selected)),
            validated_cache_keys=frozenset(cast(list[str], validated)),
        ),
        reference.sha256,
    )


def _unfinished_attempts(journal: RunJournal) -> tuple[LedgerEvent, ...]:
    starts = {
        event.payload.get("attempt_id"): event
        for event in journal.events
        if event.event_type == "attempt_started"
    }
    finished = {
        event.payload.get("attempt_id")
        for event in journal.events
        if event.event_type in {"attempt_succeeded", "attempt_failed", "attempt_indeterminate"}
    }
    return tuple(event for attempt_id, event in starts.items() if attempt_id not in finished)


def _terminal_output(
    scenario: VerificationScenario,
    state: VerificationState,
    *,
    debit: int,
    stop_reason: str,
) -> TerminalOutput:
    candidate = scenario.terminal_candidate(state)
    if candidate is not None and candidate.scientifically_terminal:
        return TerminalOutput.result(
            pattern_id=scenario.pattern_id,
            stop_reason=stop_reason,
            state_revision=state.revision,
            debit=debit,
            accepted_phase_ids=candidate.accepted_phase_ids,
            rwp=candidate.rwp,
        )
    provisional = candidate.accepted_phase_ids if candidate is not None else ()
    return TerminalOutput.abstention(
        pattern_id=scenario.pattern_id,
        stop_reason=stop_reason,
        state_revision=state.revision,
        debit=debit,
        provisional_hypothesis_ids=provisional,
    )


def _completed_run(root: Path) -> VerificationRun:
    seal = validate_seal(root / "seal.json")
    terminal = read_canonical_json(root / "terminal_result.json")
    manifest = read_canonical_json(root / "run_manifest.json")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str):
        raise ArtifactValidationError("run manifest lacks run ID")
    journal_events = RunJournal(
        root,
        RunManifest(
            run_id=run_id,
            pilot_version=cast(str, manifest["pilot_version"]),
            pattern_id=cast(str, manifest["pattern_id"]),
            arm=Arm(cast(str, manifest["arm"])),
            budget=cast(int, manifest["budget"]),
            run_attempt_id=cast(str, manifest["run_attempt_id"]),
            frozen_state_id=cast(str, manifest["frozen_state_id"]),
            provenance=tuple(sorted(cast(dict[str, str], manifest["provenance"]).items())),
            environment_keys=tuple(cast(list[str], manifest["environment_keys"])),
        ),
    ).events
    selected = tuple(
        cast(str, event.payload["selected_action_id"])
        for event in journal_events
        if event.event_type == "transition_committed"
    )
    return VerificationRun(
        seal=seal,
        selected_action_ids=selected,
        debit=cast(int, terminal["debit"]),
        stop_reason=cast(str, terminal["stop_reason"]),
    )


def run_verification_arm(
    spec: VerificationRunSpec,
    scenario: VerificationScenario,
    root: Path,
    *,
    pause_after_commits: int | None = None,
    dispatch: Dispatch | None = None,
) -> VerificationRun:
    """Run or cleanly resume one deterministic no-BGMN invariant fixture."""

    if pause_after_commits is not None and pause_after_commits < 1:
        raise ValueError("pause_after_commits must be positive")
    run_id = (
        f"{spec.pilot_version}/{scenario.pattern_id}/{spec.arm.value}/"
        f"B{spec.budget}/{spec.run_attempt_id}"
    )
    manifest = RunManifest(
        run_id=run_id,
        pilot_version=spec.pilot_version,
        pattern_id=scenario.pattern_id,
        arm=spec.arm,
        budget=spec.budget,
        run_attempt_id=spec.run_attempt_id,
        frozen_state_id=scenario.frozen_state_id,
        provenance=spec.provenance,
        environment_keys=(),
    )
    initial_state = VerificationState(0, (), frozenset())
    initial_actions = tuple(scenario.prepare_frontier(initial_state))
    frozen = FrozenStateManifest(
        frozen_state_id=scenario.frozen_state_id,
        state_revision=0,
        frontier_fingerprint=frontier_fingerprint(initial_actions),
    )
    if (root / "seal.json").exists():
        if read_canonical_json(root / "run_manifest.json") != manifest.to_json():
            raise ArtifactValidationError("sealed run manifest does not match requested resume")
        return _completed_run(root)
    journal = RunJournal.create(root, manifest, frozen)
    state, checkpoint_id = _restore_state(journal)

    unfinished = _unfinished_attempts(journal)
    if unfinished:
        for event in unfinished:
            journal.record_attempt_outcome(
                attempt=event,
                outcome="indeterminate",
                failure_class="completion_missing_after_dispatch",
            )
        terminal = _terminal_output(
            scenario,
            state,
            debit=journal.debit,
            stop_reason="indeterminate_attempt",
        )
        seal = journal.terminate(terminal)
        return VerificationRun(seal, state.selected_action_ids, journal.debit, terminal.stop_reason)

    executor = dispatch or _default_dispatch
    store = StrictRefinementStore(root / "artifacts" / "refinement_cache")
    identity_by_key = {identity.strict_key: identity for identity in scenario.identities}
    commits_this_call = 0

    while True:
        actions = tuple(scenario.prepare_frontier(state))
        if any(action.state_revision != state.revision for action in actions):
            raise ArtifactValidationError("prepared frontier contains a stale revision")
        for action in actions:
            for sibling in action.siblings:
                identity = identity_by_key.get(sibling.strict_key)
                if identity is None:
                    raise ArtifactValidationError("prepared sibling has no strict identity")
                actual_hit = store.probe(identity).status is CacheStatus.VALID_HIT
                if sibling.cache_hit != actual_hit:
                    raise ArtifactValidationError("prepared cache evidence is stale")
        decision = schedule_next(
            arm=spec.arm,
            actions=actions,
            state_revision=state.revision,
            remaining_budget=spec.budget - journal.debit,
            validated_cache_keys=state.validated_cache_keys,
        )
        snapshot = journal.record_decision(
            state_revision=state.revision,
            checkpoint_id=checkpoint_id,
            debit=journal.debit,
            validated_cache_keys=state.validated_cache_keys,
            actions=actions,
            decision=decision,
        )
        journal.record_selection(
            snapshot=snapshot,
            selected_action_id=decision.selected_action_id,
            stop_reason=decision.stop_reason,
        )
        if decision.selected_action_id is None:
            if decision.stop_reason is None:
                raise ArtifactValidationError("terminal decision lacks a stop reason")
            terminal = _terminal_output(
                scenario,
                state,
                debit=journal.debit,
                stop_reason=decision.stop_reason,
            )
            seal = journal.terminate(terminal)
            return VerificationRun(
                seal=seal,
                selected_action_ids=state.selected_action_ids,
                debit=journal.debit,
                stop_reason=decision.stop_reason,
            )

        selected = next(
            action for action in actions if action.action_id == decision.selected_action_id
        )
        revalidated_actions = tuple(scenario.prepare_frontier(state))
        revalidated = next(
            action
            for action in revalidated_actions
            if action.action_id == decision.selected_action_id
        )
        if revalidated != selected or cohort_fingerprint(revalidated) != cohort_fingerprint(
            selected
        ):
            raise ArtifactValidationError("prepared and executed cohorts differ")
        identities = tuple(identity_by_key[sibling.strict_key] for sibling in selected.siblings)
        costed = store.cost(identities)
        assessment = next(
            item for item in decision.assessments if item.action_id == selected.action_id
        )
        if costed.predicted_new_calls != assessment.new_call_cost:
            raise ArtifactValidationError("revalidated strict cost differs from snapshot")
        if journal.debit + costed.predicted_new_calls > spec.budget:
            raise ArtifactValidationError("atomic cohort would exceed remaining budget")

        seen_keys: set[str] = set()
        for identity in identities:
            if identity.strict_key in seen_keys:
                continue
            seen_keys.add(identity.strict_key)
            probe = store.probe(identity)
            if probe.status is CacheStatus.VALID_HIT:
                journal.record_cache_hit(
                    action_id=selected.action_id,
                    strict_key=identity.strict_key,
                    metadata_digest=canonical_sha256(identity.identity_json()),
                )
                continue
            attempt = journal.record_attempt_started(
                action_id=selected.action_id,
                strict_key=identity.strict_key,
            )
            try:
                result = executor(identity)
            except Exception as error:
                journal.record_attempt_outcome(
                    attempt=attempt,
                    outcome="failed",
                    failure_class=type(error).__name__,
                )
                terminal = _terminal_output(
                    scenario,
                    state,
                    debit=journal.debit,
                    stop_reason="scientific_transition_failed",
                )
                seal = journal.terminate(terminal)
                return VerificationRun(
                    seal=seal,
                    selected_action_ids=state.selected_action_ids,
                    debit=journal.debit,
                    stop_reason=terminal.stop_reason,
                )
            attempt_id = attempt.payload.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise ArtifactValidationError("durable attempt lacks an ID")
            store.publish_success(identity, result, producing_attempt_id=attempt_id)
            result_reference = journal.publish_artifact("refinement_results", result)
            journal.record_attempt_outcome(
                attempt=attempt,
                outcome="succeeded",
                result_reference=result_reference,
            )

        state_before = state
        state = VerificationState(
            revision=state.revision + 1,
            selected_action_ids=state.selected_action_ids + (selected.action_id,),
            validated_cache_keys=state.validated_cache_keys.union(
                identity.strict_key for identity in identities
            ),
        )
        candidate = scenario.terminal_candidate(state)
        next_actions = tuple(scenario.prepare_frontier(state))
        next_fingerprint = frontier_fingerprint(next_actions)
        checkpoint = journal.publish_artifact(
            "checkpoints",
            {
                "debit": journal.debit,
                "last_event_hash": journal.events[-1].event_sha256,
                "next_frontier_fingerprint": next_fingerprint,
                "schema_name": "verification_checkpoint",
                "schema_version": 1,
                "selected_action_ids": list(state.selected_action_ids),
                "state_revision": state.revision,
                "terminal_candidate": candidate.to_json() if candidate is not None else None,
                "validated_cache_keys": cast(list[JsonValue], sorted(state.validated_cache_keys)),
            },
        )
        journal.record_transition(
            action=selected,
            state_revision_before=state_before.revision,
            state_revision_after=state.revision,
            checkpoint=checkpoint,
            next_frontier_fingerprint=next_fingerprint,
            terminal_candidate=candidate.to_json() if candidate is not None else None,
        )
        checkpoint_id = checkpoint.sha256
        commits_this_call += 1
        if pause_after_commits is not None and commits_this_call >= pause_after_commits:
            return VerificationRun(
                seal=None,
                selected_action_ids=state.selected_action_ids,
                debit=journal.debit,
                stop_reason=None,
            )


__all__ = (
    "Dispatch",
    "TerminalCandidate",
    "VerificationRun",
    "VerificationRunSpec",
    "VerificationScenario",
    "VerificationState",
    "run_verification_arm",
)

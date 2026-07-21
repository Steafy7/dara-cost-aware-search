from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dara_cost_aware.artifacts import (
    ArtifactValidationError,
    JsonValue,
    cohort_fingerprint,
    frontier_fingerprint,
    read_canonical_json,
    read_ledger,
    validate_seal,
)
from dara_cost_aware.scheduling import (
    Arm,
    PreparedExpansion,
    PreparedSibling,
    SchedulingDecision,
    schedule_next,
)


@dataclass(frozen=True)
class ReplayReport:
    valid: bool
    seal_sha256: str
    frozen_state_id: str
    arm: Arm
    budget: int
    state_revision: int
    debit: int
    selected_action_ids: tuple[str, ...]
    reachable_action_ids: frozenset[str]
    attempted_strict_keys: tuple[str, ...]
    validated_cache_hit_keys: tuple[str, ...]
    stop_reason: str
    terminal_kind: str
    provisional_hypothesis_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompletionEquivalence:
    equivalent: bool
    reachable_action_ids: frozenset[str]
    attempted_strict_keys: frozenset[str]


def _string(value: JsonValue, *, field: str) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field} must be a string")
    return value


def _integer(value: JsonValue, *, field: str) -> int:
    if not isinstance(value, int):
        raise ArtifactValidationError(f"{field} must be an integer")
    return value


def _string_list(value: JsonValue, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArtifactValidationError(f"{field} must be a string list")
    return tuple(cast(list[str], value))


def _prepared_action(value: JsonValue) -> PreparedExpansion:
    if not isinstance(value, dict):
        raise ArtifactValidationError("prepared action must be an object")
    required = {
        "action_id",
        "dara_benefit",
        "fifo_position",
        "parent_id",
        "siblings",
        "state_revision",
        "v3_benefit",
    }
    if set(value) != required:
        raise ArtifactValidationError("prepared action fields differ")
    siblings_value = value["siblings"]
    if not isinstance(siblings_value, list):
        raise ArtifactValidationError("prepared siblings must be a list")
    siblings: list[PreparedSibling] = []
    for sibling_value in siblings_value:
        if not isinstance(sibling_value, dict) or set(sibling_value) != {
            "cache_hit",
            "sibling_id",
            "strict_key",
        }:
            raise ArtifactValidationError("prepared sibling fields differ")
        cache_hit = sibling_value["cache_hit"]
        if not isinstance(cache_hit, bool):
            raise ArtifactValidationError("prepared cache status must be boolean")
        siblings.append(
            PreparedSibling(
                sibling_id=_string(sibling_value["sibling_id"], field="sibling_id"),
                strict_key=_string(sibling_value["strict_key"], field="strict_key"),
                cache_hit=cache_hit,
            )
        )
    try:
        dara_benefit = float(_string(value["dara_benefit"], field="dara_benefit"))
        v3_benefit = float(_string(value["v3_benefit"], field="v3_benefit"))
    except ValueError as error:
        raise ArtifactValidationError("prepared benefit is not numeric") from error
    return PreparedExpansion(
        action_id=_string(value["action_id"], field="action_id"),
        parent_id=_string(value["parent_id"], field="parent_id"),
        state_revision=_integer(value["state_revision"], field="state_revision"),
        fifo_position=_integer(value["fifo_position"], field="fifo_position"),
        dara_benefit=dara_benefit,
        v3_benefit=v3_benefit,
        siblings=tuple(siblings),
    )


def _actions(value: JsonValue) -> tuple[PreparedExpansion, ...]:
    if not isinstance(value, list):
        raise ArtifactValidationError("snapshot actions must be a list")
    return tuple(_prepared_action(item) for item in value)


def _expected_assessments(decision: SchedulingDecision) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        {
            "action_id": assessment.action_id,
            "benefit": format(assessment.benefit, ".17g"),
            "eligible": assessment.eligible,
            "new_call_cost": assessment.new_call_cost,
            "ordering_key": [
                format(float(value), ".17g") if index < 3 else str(value)
                for index, value in enumerate(assessment.ordering_key)
            ],
            "rejection_reason": assessment.rejection_reason,
        }
        for assessment in decision.assessments
    )


def replay_run(seal_path: Path) -> ReplayReport:
    """Validate policy, budget, cache, transition, resume, and terminal evidence."""

    seal = validate_seal(seal_path)
    root = seal_path.parent
    manifest = read_canonical_json(root / "run_manifest.json")
    frozen = read_canonical_json(root / "frozen_state_manifest.json")
    terminal = read_canonical_json(root / "terminal_result.json")
    run_id = _string(manifest.get("run_id"), field="run_id")
    arm = Arm(_string(manifest.get("arm"), field="arm"))
    budget = _integer(manifest.get("budget"), field="budget")
    frozen_state_id = _string(manifest.get("frozen_state_id"), field="frozen_state_id")
    if frozen.get("frozen_state_id") != frozen_state_id:
        raise ArtifactValidationError("frozen state ID differs from run manifest")
    expected_frontier = _string(frozen.get("frontier_fingerprint"), field="frontier_fingerprint")
    expected_checkpoint_id = frozen_state_id
    expected_validated_keys = frozenset[str]()
    events = read_ledger(root / "events.ndjson", run_id=run_id)

    debit = 0
    state_revision = 0
    selected_action_ids: list[str] = []
    reachable_action_ids: set[str] = set()
    attempted_keys: list[str] = []
    hit_keys: list[str] = []
    attempt_states: dict[str, str] = {}
    pending_decision: SchedulingDecision | None = None
    pending_actions: tuple[PreparedExpansion, ...] = ()
    pending_snapshot_hash: str | None = None
    pending_selected_action: str | None = None
    latest_candidate: JsonValue = None
    terminal_event_seen = False

    for event in events:
        payload = event.payload
        if event.event_type == "decision_snapshot":
            if pending_decision is not None:
                raise ArtifactValidationError("decision snapshot lacks a selection")
            actions = _actions(payload.get("actions"))
            snapshot_frontier = _string(
                payload.get("frontier_fingerprint"), field="snapshot frontier"
            )
            if frontier_fingerprint(actions) != snapshot_frontier:
                raise ArtifactValidationError("snapshot action set fingerprint differs")
            if snapshot_frontier != expected_frontier:
                raise ArtifactValidationError("snapshot omits, injects, or alters frontier actions")
            if payload.get("checkpoint_id") != expected_checkpoint_id:
                raise ArtifactValidationError("snapshot checkpoint does not match resume state")
            if _integer(payload.get("state_revision"), field="snapshot revision") != state_revision:
                raise ArtifactValidationError("snapshot revision differs")
            if _integer(payload.get("debit"), field="snapshot debit") != debit:
                raise ArtifactValidationError("snapshot debit differs")
            remaining_budget = _integer(payload.get("remaining_budget"), field="remaining budget")
            if remaining_budget != budget - debit:
                raise ArtifactValidationError("snapshot remaining budget differs")
            validated_keys = frozenset(
                _string_list(payload.get("validated_cache_keys"), field="validated keys")
            )
            if validated_keys != expected_validated_keys:
                raise ArtifactValidationError("snapshot validated-cache state differs")
            pending_decision = schedule_next(
                arm=arm,
                actions=actions,
                state_revision=state_revision,
                remaining_budget=remaining_budget,
                validated_cache_keys=validated_keys,
            )
            assessments = payload.get("assessments")
            if not isinstance(assessments, list) or tuple(assessments) != _expected_assessments(
                pending_decision
            ):
                raise ArtifactValidationError("recorded assessments do not replay")
            pending_actions = actions
            pending_snapshot_hash = event.event_sha256
            reachable_action_ids.update(action.action_id for action in actions)
        elif event.event_type == "action_selected":
            if pending_decision is None or pending_snapshot_hash is None:
                raise ArtifactValidationError("selection lacks a decision snapshot")
            if payload.get("decision_event_hash") != pending_snapshot_hash:
                raise ArtifactValidationError("selection references the wrong snapshot")
            selected = payload.get("selected_action_id")
            if selected is not None and not isinstance(selected, str):
                raise ArtifactValidationError("selected action ID is malformed")
            if selected != pending_decision.selected_action_id:
                raise ArtifactValidationError("selected action does not replay")
            if payload.get("stop_reason") != pending_decision.stop_reason:
                raise ArtifactValidationError("selected stop reason does not replay")
            pending_selected_action = selected
            if selected is None:
                pending_decision = None
                pending_actions = ()
                pending_snapshot_hash = None
        elif event.event_type == "attempt_started":
            debit_before = _integer(payload.get("debit_before"), field="attempt debit before")
            debit_after = _integer(payload.get("debit_after"), field="attempt debit after")
            if debit_before != debit or debit_after != debit + 1 or debit_after > budget:
                raise ArtifactValidationError("attempt debit is not monotone and exact")
            attempt_id = _string(payload.get("attempt_id"), field="attempt_id")
            if attempt_id in attempt_states:
                raise ArtifactValidationError("attempt is duplicated")
            strict_key = _string(payload.get("strict_key"), field="attempt strict key")
            if strict_key in attempted_keys:
                raise ArtifactValidationError("strict miss was dispatched more than once")
            attempt_states[attempt_id] = "started"
            attempted_keys.append(strict_key)
            debit = debit_after
        elif event.event_type in {
            "attempt_succeeded",
            "attempt_failed",
            "attempt_indeterminate",
        }:
            attempt_id = _string(payload.get("attempt_id"), field="attempt outcome ID")
            if attempt_states.get(attempt_id) != "started":
                raise ArtifactValidationError("attempt outcome is missing or duplicated")
            attempt_states[attempt_id] = event.event_type
        elif event.event_type == "cache_hit_validated":
            if _integer(payload.get("debit"), field="cache-hit debit") != debit:
                raise ArtifactValidationError("validated cache hit changed debit")
            hit_keys.append(_string(payload.get("strict_key"), field="cache-hit strict key"))
        elif event.event_type == "transition_committed":
            if pending_decision is None or pending_selected_action is None:
                raise ArtifactValidationError("transition lacks a selected action")
            selected_action = next(
                action for action in pending_actions if action.action_id == pending_selected_action
            )
            expected_cohort = cohort_fingerprint(selected_action)
            for name in (
                "selected_cohort_fingerprint",
                "executed_cohort_fingerprint",
                "committed_cohort_fingerprint",
            ):
                if payload.get(name) != expected_cohort:
                    raise ArtifactValidationError("prepared/executed/committed cohort differs")
            before = _integer(payload.get("state_revision_before"), field="revision before")
            after = _integer(payload.get("state_revision_after"), field="revision after")
            if before != state_revision or after != state_revision + 1:
                raise ArtifactValidationError("transition revision is not monotone")
            if _integer(payload.get("debit"), field="transition debit") != debit:
                raise ArtifactValidationError("transition debit differs from attempts")
            expected_frontier = _string(
                payload.get("next_frontier_fingerprint"), field="next frontier"
            )

            checkpoint_reference = payload.get("checkpoint")
            if not isinstance(checkpoint_reference, dict) or set(checkpoint_reference) != {
                "byte_length",
                "path",
                "role",
                "sha256",
            }:
                raise ArtifactValidationError("transition checkpoint reference is malformed")
            checkpoint_relative = _string(checkpoint_reference.get("path"), field="checkpoint path")
            checkpoint_path = Path(checkpoint_relative)
            if checkpoint_path.is_absolute() or ".." in checkpoint_path.parts:
                raise ArtifactValidationError("transition checkpoint path is unsafe")
            checkpoint = read_canonical_json(root / checkpoint_path)
            if set(checkpoint) != {
                "debit",
                "last_event_hash",
                "next_frontier_fingerprint",
                "schema_name",
                "schema_version",
                "selected_action_ids",
                "state_revision",
                "terminal_candidate",
                "validated_cache_keys",
            }:
                raise ArtifactValidationError("checkpoint fields differ")
            if checkpoint.get("last_event_hash") != event.previous_event_hash:
                raise ArtifactValidationError("checkpoint does not bind its preceding ledger event")
            if _integer(checkpoint.get("debit"), field="checkpoint debit") != debit:
                raise ArtifactValidationError("checkpoint debit differs")
            if _integer(checkpoint.get("state_revision"), field="checkpoint revision") != after:
                raise ArtifactValidationError("checkpoint revision differs")
            if checkpoint.get("next_frontier_fingerprint") != expected_frontier:
                raise ArtifactValidationError("checkpoint frontier differs")
            checkpoint_selected = _string_list(
                checkpoint.get("selected_action_ids"), field="checkpoint selected actions"
            )
            if checkpoint_selected != tuple(selected_action_ids) + (pending_selected_action,):
                raise ArtifactValidationError("checkpoint selected actions differ")
            checkpoint_validated = frozenset(
                _string_list(
                    checkpoint.get("validated_cache_keys"),
                    field="checkpoint validated cache keys",
                )
            )
            expected_validated_keys = expected_validated_keys.union(
                sibling.strict_key for sibling in selected_action.siblings
            )
            if checkpoint_validated != expected_validated_keys:
                raise ArtifactValidationError("checkpoint validated-cache state differs")
            latest_candidate = payload.get("terminal_candidate")
            if checkpoint.get("terminal_candidate") != latest_candidate:
                raise ArtifactValidationError("checkpoint incumbent differs from transition")
            expected_checkpoint_id = _string(
                checkpoint_reference.get("sha256"), field="checkpoint digest"
            )

            selected_action_ids.append(pending_selected_action)
            state_revision = after
            pending_decision = None
            pending_actions = ()
            pending_snapshot_hash = None
            pending_selected_action = None
        elif event.event_type == "run_terminated":
            if _integer(payload.get("debit"), field="terminal debit") != debit:
                raise ArtifactValidationError("terminal event debit differs")
            if _integer(payload.get("state_revision"), field="terminal revision") != state_revision:
                raise ArtifactValidationError("terminal event revision differs")
            terminal_event_seen = True
        else:
            raise ArtifactValidationError(f"unknown ledger event type {event.event_type}")

    if not terminal_event_seen:
        raise ArtifactValidationError("sealed replay lacks a terminal event")
    if any(state == "started" for state in attempt_states.values()):
        raise ArtifactValidationError("sealed run contains an unfinished attempt")
    terminal_debit = _integer(terminal.get("debit"), field="terminal debit")
    terminal_revision = _integer(terminal.get("state_revision"), field="terminal revision")
    if terminal_debit != debit or terminal_revision != state_revision:
        raise ArtifactValidationError("terminal output differs from replayed state")
    stop_reason = _string(terminal.get("stop_reason"), field="terminal stop reason")
    terminal_kind = _string(terminal.get("kind"), field="terminal kind")
    provisional = _string_list(
        terminal.get("provisional_hypothesis_ids"), field="provisional hypotheses"
    )
    if terminal_kind == "result":
        if not isinstance(latest_candidate, dict):
            raise ArtifactValidationError("terminal result lacks a committed incumbent")
        if latest_candidate.get("scientifically_terminal") is not True:
            raise ArtifactValidationError("pending hypothesis became a terminal result")
        accepted = _string_list(terminal.get("accepted_phase_ids"), field="accepted phases")
        candidate_phases = _string_list(
            latest_candidate.get("accepted_phase_ids"), field="incumbent phases"
        )
        if accepted != candidate_phases or terminal.get("rwp") != latest_candidate.get("rwp"):
            raise ArtifactValidationError("terminal result differs from committed incumbent")
        if provisional:
            raise ArtifactValidationError("terminal result contains provisional hypotheses")
    elif terminal_kind == "abstention":
        if isinstance(latest_candidate, dict):
            is_terminal = latest_candidate.get("scientifically_terminal")
            candidate_phases = _string_list(
                latest_candidate.get("accepted_phase_ids"), field="candidate phases"
            )
            if is_terminal is True:
                raise ArtifactValidationError("scientifically terminal incumbent was discarded")
            if provisional != candidate_phases:
                raise ArtifactValidationError(
                    "abstention diagnostics differ from pending candidate"
                )
    else:
        raise ArtifactValidationError("terminal output kind is unsupported")
    return ReplayReport(
        valid=True,
        seal_sha256=seal.sha256,
        frozen_state_id=frozen_state_id,
        arm=arm,
        budget=budget,
        state_revision=state_revision,
        debit=debit,
        selected_action_ids=tuple(selected_action_ids),
        reachable_action_ids=frozenset(reachable_action_ids),
        attempted_strict_keys=tuple(attempted_keys),
        validated_cache_hit_keys=tuple(hit_keys),
        stop_reason=stop_reason,
        terminal_kind=terminal_kind,
        provisional_hypothesis_ids=provisional,
    )


def verify_completion_equivalence(
    reports: tuple[ReplayReport, ...],
) -> CompletionEquivalence:
    if not reports:
        raise ValueError("completion equivalence requires at least one replay")
    frozen_states = {report.frozen_state_id for report in reports}
    reachable_sets = {report.reachable_action_ids for report in reports}
    attempted_sets = {frozenset(report.attempted_strict_keys) for report in reports}
    if len(frozen_states) != 1:
        raise ArtifactValidationError("completion reports use different frozen states")
    if any(report.stop_reason != "frontier_exhausted" for report in reports):
        raise ArtifactValidationError("completion equivalence requires exhaustive runs")
    if len(reachable_sets) != 1 or len(attempted_sets) != 1:
        raise ArtifactValidationError("exhaustive reachable action/cache-miss sets differ")
    return CompletionEquivalence(
        equivalent=True,
        reachable_action_ids=reports[0].reachable_action_ids,
        attempted_strict_keys=frozenset(reports[0].attempted_strict_keys),
    )


__all__ = (
    "CompletionEquivalence",
    "ReplayReport",
    "replay_run",
    "verify_completion_equivalence",
)

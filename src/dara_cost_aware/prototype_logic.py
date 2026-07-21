"""PROTOTYPE: in-memory logic model for the shared four-arm harness.

This module is intentionally throwaway. It exercises the scheduling state
machine without DARA, BGMN, persistence, ground truth, or scientific results.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .scheduling import Arm, PreparedExpansion, SchedulingDecision, schedule_next


PILOT_BUDGETS = (5, 10, 15, 20)


@dataclass(frozen=True)
class PrototypeScenario:
    """One immutable stand-in for a frozen post-initialization checkpoint."""

    frozen_state_id: str
    actions: tuple[PreparedExpansion, ...]

    def __post_init__(self) -> None:
        if not self.frozen_state_id:
            raise ValueError("frozen_state_id must not be empty")
        action_ids = tuple(action.action_id for action in self.actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("prototype action IDs must be unique")


@dataclass(frozen=True)
class PrototypeTransition:
    """One selected atomic cohort and its resulting in-memory state."""

    state_revision_before: int
    selected_action_id: str
    new_call_cost: int
    debit_before: int
    debit_after: int
    validated_cache_keys_after: frozenset[str]


@dataclass(frozen=True)
class PrototypeRun:
    """Trace-visible result of one arm at one branch-call budget."""

    frozen_state_id: str
    arm: Arm
    budget: int
    decisions: tuple[SchedulingDecision, ...]
    transitions: tuple[PrototypeTransition, ...]
    debit: int
    stop_reason: str

    @property
    def selected_action_ids(self) -> tuple[str, ...]:
        return tuple(transition.selected_action_id for transition in self.transitions)


def _run_one(
    scenario: PrototypeScenario,
    *,
    arm: Arm,
    budget: int,
) -> PrototypeRun:
    if budget < 0:
        raise ValueError("budget must be nonnegative")

    remaining = list(scenario.actions)
    validated_cache_keys: set[str] = set()
    decisions: list[SchedulingDecision] = []
    transitions: list[PrototypeTransition] = []
    state_revision = 0
    debit = 0

    while True:
        prepared = tuple(
            replace(action, state_revision=state_revision) for action in remaining
        )
        decision = schedule_next(
            arm=arm,
            actions=prepared,
            state_revision=state_revision,
            remaining_budget=budget - debit,
            validated_cache_keys=frozenset(validated_cache_keys),
        )
        decisions.append(decision)
        if decision.selected_action_id is None:
            if decision.stop_reason is None:
                raise RuntimeError("terminal scheduling decision lacks a stop reason")
            stop_reason = decision.stop_reason
            break

        selected = next(
            action for action in prepared if action.action_id == decision.selected_action_id
        )
        assessment = next(
            item
            for item in decision.assessments
            if item.action_id == decision.selected_action_id
        )
        debit_before = debit
        debit += assessment.new_call_cost
        validated_cache_keys.update(sibling.strict_key for sibling in selected.siblings)
        transitions.append(
            PrototypeTransition(
                state_revision_before=state_revision,
                selected_action_id=selected.action_id,
                new_call_cost=assessment.new_call_cost,
                debit_before=debit_before,
                debit_after=debit,
                validated_cache_keys_after=frozenset(validated_cache_keys),
            )
        )
        remaining = [action for action in remaining if action.action_id != selected.action_id]
        state_revision += 1

    return PrototypeRun(
        frozen_state_id=scenario.frozen_state_id,
        arm=arm,
        budget=budget,
        decisions=tuple(decisions),
        transitions=tuple(transitions),
        debit=debit,
        stop_reason=stop_reason,
    )


def run_four_arm_prototype(
    scenario: PrototypeScenario,
    *,
    budgets: tuple[int, ...] = PILOT_BUDGETS,
) -> tuple[PrototypeRun, ...]:
    """Clone one scenario for every controlled arm and requested budget."""

    return tuple(
        _run_one(scenario, arm=arm, budget=budget)
        for budget in budgets
        for arm in Arm
    )

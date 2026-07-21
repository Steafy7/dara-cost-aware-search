"""PROTOTYPE: in-memory logic model for the shared four-arm harness.

This module is intentionally throwaway. It exercises the scheduling state
machine without DARA, BGMN, persistence, ground truth, or scientific results.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .scheduling import Arm, PreparedExpansion, SchedulingDecision, schedule_next


PILOT_BUDGETS = (5, 10, 15, 20)


@dataclass(frozen=True)
class PrototypeState:
    """Scientific state supplied to the revision-bound preparation seam."""

    revision: int
    selected_action_ids: tuple[str, ...]
    validated_cache_keys: frozenset[str]

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")


FrontierPreparer = Callable[[PrototypeState], tuple[PreparedExpansion, ...]]


@dataclass(frozen=True)
class PrototypeScenario:
    """A frozen checkpoint plus its revision-aware toy preparation seam."""

    frozen_state_id: str
    prepare_frontier: FrontierPreparer

    def __post_init__(self) -> None:
        if not self.frozen_state_id:
            raise ValueError("frozen_state_id must not be empty")
        if not callable(self.prepare_frontier):
            raise ValueError("prepare_frontier must be callable")


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

    decisions: list[SchedulingDecision] = []
    transitions: list[PrototypeTransition] = []
    state = PrototypeState(
        revision=0,
        selected_action_ids=(),
        validated_cache_keys=frozenset(),
    )
    debit = 0

    while True:
        prepared = tuple(scenario.prepare_frontier(state))
        if any(action.state_revision != state.revision for action in prepared):
            raise ValueError("prepared frontier contains a stale state revision")
        decision = schedule_next(
            arm=arm,
            actions=prepared,
            state_revision=state.revision,
            remaining_budget=budget - debit,
            validated_cache_keys=state.validated_cache_keys,
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
            item for item in decision.assessments if item.action_id == decision.selected_action_id
        )
        debit_before = debit
        debit += assessment.new_call_cost
        validated_cache_keys_after = state.validated_cache_keys.union(
            sibling.strict_key for sibling in selected.siblings
        )
        transitions.append(
            PrototypeTransition(
                state_revision_before=state.revision,
                selected_action_id=selected.action_id,
                new_call_cost=assessment.new_call_cost,
                debit_before=debit_before,
                debit_after=debit,
                validated_cache_keys_after=validated_cache_keys_after,
            )
        )
        state = PrototypeState(
            revision=state.revision + 1,
            selected_action_ids=state.selected_action_ids + (selected.action_id,),
            validated_cache_keys=validated_cache_keys_after,
        )

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

    return tuple(_run_one(scenario, arm=arm, budget=budget) for budget in budgets for arm in Arm)

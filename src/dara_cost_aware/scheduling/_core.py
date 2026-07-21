from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class Arm(StrEnum):
    """The four controlled scheduling arms."""

    ORIGINAL_DARA = "original_dara"
    PREVIOUS_V3 = "previous_v3"
    H1_DARA = "h1_dara"
    H1_V3 = "h1_v3"


@dataclass(frozen=True)
class PreparedSibling:
    """One sibling refinement in its scientific execution order."""

    sibling_id: str
    strict_key: str
    cache_hit: bool

    def __post_init__(self) -> None:
        if not self.sibling_id:
            raise ValueError("sibling_id must not be empty")
        if not self.strict_key:
            raise ValueError("strict_key must not be empty")


@dataclass(frozen=True)
class PreparedExpansion:
    """An immutable complete sibling cohort for one pending parent."""

    action_id: str
    parent_id: str
    state_revision: int
    fifo_position: int
    dara_benefit: float
    v3_benefit: float
    siblings: tuple[PreparedSibling, ...]

    def __post_init__(self) -> None:
        if not self.action_id:
            raise ValueError("action_id must not be empty")
        if not self.parent_id:
            raise ValueError("parent_id must not be empty")
        if self.state_revision < 0:
            raise ValueError("state_revision must be nonnegative")
        if self.fifo_position < 0:
            raise ValueError("fifo_position must be nonnegative")
        if not self.siblings:
            raise ValueError("a prepared expansion must contain its complete sibling cohort")
        for name, value in (
            ("dara_benefit", self.dara_benefit),
            ("v3_benefit", self.v3_benefit),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


OrderingKey = tuple[float, float, float, int, int, str]


@dataclass(frozen=True)
class ActionAssessment:
    """Trace-ready scheduling facts for one available action."""

    action_id: str
    eligible: bool
    rejection_reason: str | None
    benefit: float
    new_call_cost: int
    ordering_key: OrderingKey


@dataclass(frozen=True)
class SchedulingDecision:
    """A complete pre-decision assessment and its selected action."""

    arm: Arm
    remaining_budget: int
    assessments: tuple[ActionAssessment, ...]
    selected_action_id: str | None
    stop_reason: str | None


def _benefit(arm: Arm, action: PreparedExpansion) -> float:
    if arm in (Arm.ORIGINAL_DARA, Arm.H1_DARA):
        return action.dara_benefit
    return action.v3_benefit


def _new_call_cost(
    action: PreparedExpansion,
    validated_cache_keys: frozenset[str],
) -> int:
    known_hits = set(validated_cache_keys)
    known_hits.update(sibling.strict_key for sibling in action.siblings if sibling.cache_hit)
    return len(
        {
            sibling.strict_key
            for sibling in action.siblings
            if sibling.strict_key not in known_hits
        }
    )


def _ordering_key(
    arm: Arm,
    action: PreparedExpansion,
    *,
    benefit: float,
    cost: int,
) -> OrderingKey:
    if cost == 0:
        return (0.0, -benefit, 0.0, 0, action.fifo_position, action.action_id)
    if arm is Arm.ORIGINAL_DARA:
        return (
            1.0,
            float(action.fifo_position),
            0.0,
            0,
            action.fifo_position,
            action.action_id,
        )
    if arm is Arm.PREVIOUS_V3:
        return (
            1.0,
            -benefit,
            -benefit,
            cost,
            action.fifo_position,
            action.action_id,
        )
    return (
        1.0,
        -(benefit / cost),
        -benefit,
        cost,
        action.fifo_position,
        action.action_id,
    )


def schedule_next(
    *,
    arm: Arm,
    actions: tuple[PreparedExpansion, ...],
    state_revision: int,
    remaining_budget: int,
    validated_cache_keys: frozenset[str],
) -> SchedulingDecision:
    """Assess the complete frontier and choose one eligible atomic expansion."""

    if remaining_budget < 0:
        raise ValueError("remaining_budget must be nonnegative")
    if state_revision < 0:
        raise ValueError("state_revision must be nonnegative")

    assessments: list[ActionAssessment] = []
    eligible: list[tuple[OrderingKey, str]] = []
    for action in actions:
        cost = _new_call_cost(action, validated_cache_keys)
        benefit = _benefit(arm, action)
        ordering_key = _ordering_key(arm, action, benefit=benefit, cost=cost)
        revision_matches = action.state_revision == state_revision
        fits = cost <= remaining_budget
        eligible_now = revision_matches and fits
        if not revision_matches:
            rejection_reason = "stale_state_revision"
        elif not fits:
            rejection_reason = "cost_exceeds_remaining_budget"
        else:
            rejection_reason = None
        assessments.append(
            ActionAssessment(
                action_id=action.action_id,
                eligible=eligible_now,
                rejection_reason=rejection_reason,
                benefit=benefit,
                new_call_cost=cost,
                ordering_key=ordering_key,
            )
        )
        if eligible_now:
            eligible.append((ordering_key, action.action_id))

    if not actions:
        selected_action_id = None
        stop_reason = "frontier_exhausted"
    elif not eligible:
        selected_action_id = None
        stop_reason = (
            "branch_budget_exhausted"
            if remaining_budget == 0
            else "no_fitting_atomic_action"
        )
    else:
        selected_action_id = min(eligible)[1]
        stop_reason = None

    return SchedulingDecision(
        arm=arm,
        remaining_budget=remaining_budget,
        assessments=tuple(assessments),
        selected_action_id=selected_action_id,
        stop_reason=stop_reason,
    )

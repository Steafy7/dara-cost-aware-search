import pytest

from dara_cost_aware.scheduling import (
    Arm,
    PreparedExpansion,
    PreparedSibling,
    schedule_next,
)


def expansion(
    action_id: str,
    *,
    fifo_position: int,
    dara_benefit: float,
    v3_benefit: float,
    new_calls: int,
    state_revision: int = 0,
) -> PreparedExpansion:
    return PreparedExpansion(
        action_id=action_id,
        parent_id=f"parent-{action_id}",
        state_revision=state_revision,
        fifo_position=fifo_position,
        dara_benefit=dara_benefit,
        v3_benefit=v3_benefit,
        siblings=tuple(
            PreparedSibling(
                sibling_id=f"{action_id}-sibling-{index}",
                strict_key=f"{action_id}-key-{index}",
                cache_hit=False,
            )
            for index in range(new_calls)
        ),
    )


@pytest.mark.parametrize(
    ("arm", "expected_action"),
    [
        (Arm.ORIGINAL_DARA, "a"),
        (Arm.PREVIOUS_V3, "d"),
        (Arm.H1_DARA, "b"),
        (Arm.H1_V3, "b"),
    ],
)
def test_each_arm_selects_from_the_same_atomic_frontier(
    arm: Arm,
    expected_action: str,
) -> None:
    actions = (
        expansion("a", fifo_position=0, dara_benefit=8, v3_benefit=4, new_calls=4),
        expansion("b", fifo_position=1, dara_benefit=3, v3_benefit=9, new_calls=1),
        expansion("d", fifo_position=2, dara_benefit=2, v3_benefit=12, new_calls=3),
    )

    decision = schedule_next(
        arm=arm,
        actions=actions,
        state_revision=0,
        remaining_budget=5,
        validated_cache_keys=frozenset(),
    )

    assert decision.selected_action_id == expected_action
    assert tuple(item.action_id for item in decision.assessments) == ("a", "b", "d")
    assert all(item.eligible for item in decision.assessments)


def test_stale_and_oversized_actions_are_rejected_before_selection() -> None:
    stale = expansion(
        "stale",
        fifo_position=0,
        dara_benefit=100,
        v3_benefit=100,
        new_calls=1,
        state_revision=0,
    )
    oversized = expansion(
        "oversized",
        fifo_position=1,
        dara_benefit=90,
        v3_benefit=90,
        new_calls=3,
        state_revision=1,
    )
    fitting = expansion(
        "fitting",
        fifo_position=2,
        dara_benefit=1,
        v3_benefit=1,
        new_calls=2,
        state_revision=1,
    )

    decision = schedule_next(
        arm=Arm.ORIGINAL_DARA,
        actions=(stale, oversized, fitting),
        state_revision=1,
        remaining_budget=2,
        validated_cache_keys=frozenset(),
    )

    assert decision.selected_action_id == "fitting"
    assert decision.assessments[0].rejection_reason == "stale_state_revision"
    assert decision.assessments[1].rejection_reason == "cost_exceeds_remaining_budget"


@pytest.mark.parametrize("arm", list(Arm))
def test_complete_cache_only_action_precedes_positive_cost_work(arm: Arm) -> None:
    cache_only = expansion(
        "cache-only",
        fifo_position=1,
        dara_benefit=1,
        v3_benefit=1,
        new_calls=2,
    )
    expensive = expansion(
        "expensive",
        fifo_position=0,
        dara_benefit=100,
        v3_benefit=100,
        new_calls=1,
    )

    decision = schedule_next(
        arm=arm,
        actions=(expensive, cache_only),
        state_revision=0,
        remaining_budget=5,
        validated_cache_keys=frozenset({"cache-only-key-0", "cache-only-key-1"}),
    )

    assert decision.selected_action_id == "cache-only"
    assert decision.assessments[1].new_call_cost == 0

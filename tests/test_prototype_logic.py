from collections.abc import Callable

from dara_cost_aware.prototype_logic import (
    PrototypeScenario,
    PrototypeState,
    run_four_arm_prototype,
)
from dara_cost_aware.scheduling import Arm, PreparedExpansion, PreparedSibling


def action(
    action_id: str,
    *,
    fifo_position: int,
    dara_benefit: float,
    v3_benefit: float,
    cost: int,
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
            for index in range(cost)
        ),
    )


def prepare_static_actions(
    actions: tuple[PreparedExpansion, ...],
) -> Callable[[PrototypeState], tuple[PreparedExpansion, ...]]:
    def prepare(state: PrototypeState) -> tuple[PreparedExpansion, ...]:
        selected = set(state.selected_action_ids)
        return tuple(
            PreparedExpansion(
                action_id=item.action_id,
                parent_id=item.parent_id,
                state_revision=state.revision,
                fifo_position=item.fifo_position,
                dara_benefit=item.dara_benefit,
                v3_benefit=item.v3_benefit,
                siblings=tuple(
                    PreparedSibling(
                        sibling_id=sibling.sibling_id,
                        strict_key=sibling.strict_key,
                        cache_hit=sibling.strict_key in state.validated_cache_keys,
                    )
                    for sibling in item.siblings
                ),
            )
            for item in actions
            if item.action_id not in selected
        )

    return prepare


def test_four_arm_prototype_clones_one_frozen_state_at_every_budget() -> None:
    actions = (
        action("a", fifo_position=0, dara_benefit=8, v3_benefit=4, cost=4),
        action("b", fifo_position=1, dara_benefit=3, v3_benefit=9, cost=1),
        action("d", fifo_position=2, dara_benefit=2, v3_benefit=12, cost=3),
    )
    scenario = PrototypeScenario(
        frozen_state_id="sha256:frozen-demo-v1",
        prepare_frontier=prepare_static_actions(actions),
    )

    runs = run_four_arm_prototype(scenario)

    assert len(runs) == 16
    assert {run.arm for run in runs} == set(Arm)
    assert {run.budget for run in runs} == {5, 10, 15, 20}
    assert {run.frozen_state_id for run in runs} == {"sha256:frozen-demo-v1"}

    budget_five = {run.arm: run for run in runs if run.budget == 5}
    assert budget_five[Arm.ORIGINAL_DARA].selected_action_ids == ("a", "b")
    assert budget_five[Arm.PREVIOUS_V3].selected_action_ids == ("d", "b")
    assert budget_five[Arm.H1_DARA].selected_action_ids == ("b", "a")
    assert budget_five[Arm.H1_V3].selected_action_ids == ("b", "d")
    assert budget_five[Arm.ORIGINAL_DARA].stop_reason == "branch_budget_exhausted"
    assert budget_five[Arm.PREVIOUS_V3].stop_reason == "no_fitting_atomic_action"
    assert all(run.debit <= run.budget for run in runs)
    assert all(run.decisions[-1].selected_action_id is None for run in runs)


def test_frontier_is_reprepared_from_committed_scientific_state() -> None:
    seen_states: list[PrototypeState] = []

    def prepare(state: PrototypeState) -> tuple[PreparedExpansion, ...]:
        seen_states.append(state)
        if state.selected_action_ids == ():
            return (
                action(
                    "a",
                    fifo_position=0,
                    dara_benefit=1,
                    v3_benefit=1,
                    cost=1,
                    state_revision=state.revision,
                ),
            )
        if state.selected_action_ids == ("a",):
            return (
                action(
                    "b",
                    fifo_position=0,
                    dara_benefit=2,
                    v3_benefit=2,
                    cost=1,
                    state_revision=state.revision,
                ),
            )
        return ()

    runs = run_four_arm_prototype(
        PrototypeScenario(
            frozen_state_id="sha256:revision-aware-demo",
            prepare_frontier=prepare,
        ),
        budgets=(2,),
    )

    assert all(run.selected_action_ids == ("a", "b") for run in runs)
    assert any(state.revision == 1 and state.selected_action_ids == ("a",) for state in seen_states)

"""PROTOTYPE: inspect the four-arm scheduling state machine in a terminal."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .prototype_logic import (
    PILOT_BUDGETS,
    PrototypeScenario,
    PrototypeState,
    run_four_arm_prototype,
)
from .scheduling import PreparedExpansion, PreparedSibling


def _missing_siblings(
    action_id: str,
    count: int,
    validated_cache_keys: frozenset[str],
) -> tuple[PreparedSibling, ...]:
    return tuple(
        PreparedSibling(
            sibling_id=f"{action_id}-sibling-{index}",
            strict_key=f"{action_id}-strict-key-{index}",
            cache_hit=f"{action_id}-strict-key-{index}" in validated_cache_keys,
        )
        for index in range(count)
    )


def _prepare_demo_frontier(
    state: PrototypeState,
) -> tuple[PreparedExpansion, ...]:
    selected = set(state.selected_action_ids)
    actions = (
        PreparedExpansion(
            action_id="fifo-high-dara",
            parent_id="node-000",
            state_revision=state.revision,
            fifo_position=0,
            dara_benefit=8,
            v3_benefit=4,
            siblings=_missing_siblings("fifo-high-dara", 4, state.validated_cache_keys),
        ),
        PreparedExpansion(
            action_id="cheap-high-v3",
            parent_id="node-001",
            state_revision=state.revision,
            fifo_position=1,
            dara_benefit=3,
            v3_benefit=9,
            siblings=_missing_siblings("cheap-high-v3", 1, state.validated_cache_keys),
        ),
        PreparedExpansion(
            action_id="v3-top",
            parent_id="node-002",
            state_revision=state.revision,
            fifo_position=2,
            dara_benefit=2,
            v3_benefit=12,
            siblings=_missing_siblings("v3-top", 3, state.validated_cache_keys),
        ),
        PreparedExpansion(
            action_id="cache-only",
            parent_id="node-003",
            state_revision=state.revision,
            fifo_position=3,
            dara_benefit=1,
            v3_benefit=1,
            siblings=(
                PreparedSibling(
                    sibling_id="cache-only-sibling",
                    strict_key="cache-only-strict-key",
                    cache_hit=True,
                ),
            ),
        ),
    )
    return tuple(action for action in actions if action.action_id not in selected)


def _demo_scenario() -> PrototypeScenario:
    return PrototypeScenario(
        frozen_state_id="sha256:prototype-frozen-post-init-v1",
        prepare_frontier=_prepare_demo_frontier,
    )


def _json_line(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PROTOTYPE — print the full four-arm scheduling state after every action."
    )
    parser.add_argument(
        "--budgets",
        type=int,
        nargs="+",
        default=list(PILOT_BUDGETS),
        help="branch-call budgets to exercise (default: 5 10 15 20)",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    budgets = tuple(int(value) for value in args.budgets)
    runs = run_four_arm_prototype(_demo_scenario(), budgets=budgets)
    print("PROTOTYPE — in-memory scheduling only; no DARA, BGMN, GT, or persistence")
    for run in runs:
        print(f"\nRUN arm={run.arm.value} budget={run.budget} frozen_state={run.frozen_state_id}")
        for index, decision in enumerate(run.decisions):
            debit_used = run.transitions[index - 1].debit_after if index else 0
            _json_line(
                {
                    "event": "decision_snapshot",
                    "state_revision": index,
                    "debit_used": debit_used,
                    "remaining_budget": run.budget - debit_used,
                    "actions": [
                        {
                            "action_id": item.action_id,
                            "eligible": item.eligible,
                            "rejection_reason": item.rejection_reason,
                            "benefit": item.benefit,
                            "new_call_cost": item.new_call_cost,
                            "ordering_key": item.ordering_key,
                        }
                        for item in decision.assessments
                    ],
                    "selected_action_id": decision.selected_action_id,
                    "stop_reason": decision.stop_reason,
                }
            )
            if index < len(run.transitions):
                transition = run.transitions[index]
                _json_line(
                    {
                        "event": "transition_committed",
                        "state_revision_before": transition.state_revision_before,
                        "selected_action_id": transition.selected_action_id,
                        "new_call_cost": transition.new_call_cost,
                        "debit_before": transition.debit_before,
                        "debit_after": transition.debit_after,
                        "validated_cache_keys_after": sorted(transition.validated_cache_keys_after),
                    }
                )
        _json_line(
            {
                "event": "run_terminated",
                "arm": run.arm.value,
                "budget": run.budget,
                "debit": run.debit,
                "selected_action_ids": run.selected_action_ids,
                "stop_reason": run.stop_reason,
            }
        )


if __name__ == "__main__":
    main()

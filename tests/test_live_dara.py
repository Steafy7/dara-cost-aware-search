from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass

from dara_cost_aware.live_dara._core import (
    LiveDaraPilot,
    _score_with_root_zero_match_recall,
)


@dataclass(frozen=True)
class FakePhase:
    path: Path


class FakeTree:
    def __init__(self) -> None:
        self.phase = FakePhase(Path("phase-a.cif"))
        self.all_phases_result = {self.phase: object()}
        self.node = SimpleNamespace(
            identifier="node-1",
            data=SimpleNamespace(status="pending", current_phases=(), current_result=None),
        )

    def all_nodes(self) -> list[object]:
        return [self.node]

    def score_phases(self, remaining: dict[object, object], current: object) -> tuple[list[object], dict[object, list[float]], float]:
        assert tuple(remaining) == (self.phase,)
        assert current is None
        return [self.phase], {self.phase: [3.5, 0.0, 0.0, 0.0]}, 0.0


def test_live_frontier_preparation_preserves_atomic_sibling_cost_and_benefit(tmp_path: Path) -> None:
    pilot = LiveDaraPilot(
        pattern_path=tmp_path / "pattern.xy",
        phase_paths=(),
    )

    prepared = pilot.prepare_frontier(
        FakeTree(),
        revision=0,
        cache_keys=frozenset(),
        fifo_positions={},
    )

    assert len(prepared) == 1
    action = prepared[0].action
    assert action.action_id == "node:node-1"
    assert action.state_revision == 0
    assert action.dara_benefit == 3.5
    assert len(action.siblings) == 1
    assert action.siblings[0].cache_hit is False
    assert action.siblings[0].strict_key.startswith("sha256:")


def test_root_zero_match_recall_widens_only_zero_matched_root() -> None:
    phases = [object(), object()]

    def score_phases(available: dict[object, object], current: object | None) -> tuple[list[object], dict[object, list[float]], float]:
        assert current is None
        return [phases[0]], {phases[0]: [0.0, 1.0], phases[1]: [0.0, 1.0]}, 0.0

    best, _, _ = _score_with_root_zero_match_recall(
        score_phases,
        {phase: object() for phase in phases},
    )

    assert best == phases


def test_root_zero_match_recall_preserves_positive_or_nonroot_selection() -> None:
    phase = object()

    def score_phases(available: dict[object, object], current: object | None) -> tuple[list[object], dict[object, list[float]], float]:
        return [phase], {phase: [2.0, 0.0]}, 0.0

    selected, _, _ = _score_with_root_zero_match_recall(
        score_phases,
        {phase: object()},
        current_result=object(),
    )

    assert selected == [phase]

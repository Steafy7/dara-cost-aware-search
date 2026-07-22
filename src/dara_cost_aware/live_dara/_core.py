"""Small, guarded seam between the pure scheduler and DARA's SearchTree.

The module deliberately keeps DARA imports inside the runtime context.  The
pure scheduling package remains importable without the scientific runtime,
while the live pilot gets one place to pin the DARA worker functions, count
branch refinements, and restore the process-global hooks after a run.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import shutil
from types import MethodType
from typing import Any, cast

import numpy as np

from dara_cost_aware.scheduling import (
    Arm,
    PreparedExpansion,
    PreparedSibling,
    schedule_next,
)


logger = logging.getLogger(__name__)


def _scalar_absolute_log_error(x: np.ndarray, y: np.ndarray) -> float:
    """Return a scalar metric for SciPy's callable ``cdist`` contract.

    DARA 1.1.12 returns an elementwise array here.  Newer SciPy releases call
    a custom metric with one-dimensional rows and require one scalar result.
    This compatibility shim preserves DARA's maximum elementwise error while
    avoiding a source edit to the pinned third-party package.
    """

    x_clipped = np.clip(x, 1e-10, None)
    y_clipped = np.clip(y, 1e-10, None)
    return float(np.max(np.abs(np.log(x_clipped) - np.log(y_clipped))))


def _strict_key(pattern: Path, current_phases: tuple[str, ...], sibling: str) -> str:
    payload = "|".join((str(pattern.resolve()), *current_phases, sibling))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _raw_benefit(raw_score: Any) -> float:
    if isinstance(raw_score, (list, tuple, np.ndarray)) and raw_score:
        try:
            return max(0.0, float(raw_score[0]))
        except (TypeError, ValueError):
            return 0.0
    try:
        return max(0.0, float(raw_score))
    except (TypeError, ValueError):
        return 0.0


def _score_with_root_zero_match_recall(
    score_phases: Any,
    all_phases_result: dict[Any, Any],
    current_result: Any = None,
) -> tuple[list[Any], dict[Any, list[float]], float]:
    """Opt-in root-only recall fallback for a zero matched-peak signal.

    The fallback uses only the observed matcher output. It never reads ground
    truth and never widens non-root expansions.
    """

    best_phases, raw_scores, threshold = score_phases(
        all_phases_result,
        current_result,
    )
    if (
        current_result is None
        and raw_scores
        and all(
            _raw_benefit(raw_score) == 0.0
            for raw_score in raw_scores.values()
        )
    ):
        return list(all_phases_result), raw_scores, threshold
    return best_phases, raw_scores, threshold


@dataclass(frozen=True)
class PreparedLiveAction:
    """Prepared scheduler action plus the DARA node it expands."""

    action: PreparedExpansion
    node_id: str


@dataclass(frozen=True)
class LiveArmResult:
    """Minimal online trace summary for one arm and one budget."""

    arm: Arm
    budget: int
    branch_calls: int
    selected_action_ids: tuple[str, ...]
    stop_reason: str
    actual_costs: tuple[int, ...]
    cost_mismatches: tuple[tuple[str, int, int], ...]
    top1_phase_ids: tuple[str, ...]


class DaraLiveRuntime:
    """Install local DARA workers and count every refinement dispatch.

    DARA's public search API delegates to Ray helpers.  The pilot uses the same
    worker functions synchronously so the exact branch-call debit is visible
    and deterministic on one machine.  All monkeypatches are process-local and
    restored on exit.
    """

    def __init__(self) -> None:
        self.refinement_calls = 0
        self._tree: Any = None
        self._eflech: Any = None
        self._original_batch_refinement: Any = None
        self._original_batch_peak_matching: Any = None
        self._original_copy_instrument_files: Any = None
        self._original_refinement_function: Any = None
        self._original_absolute_log_error: Any = None

    def __enter__(self) -> DaraLiveRuntime:
        import dara.eflech_worker as eflech_module
        import dara.search.peak_matcher as peak_matcher_module
        import dara.search.tree as tree_module

        eflech_module = cast(Any, eflech_module)
        peak_matcher_module = cast(Any, peak_matcher_module)
        tree_module = cast(Any, tree_module)

        self._tree = tree_module
        self._eflech = eflech_module
        self._original_batch_refinement = tree_module.batch_refinement
        self._original_batch_peak_matching = tree_module.batch_peak_matching
        self._original_copy_instrument_files = eflech_module.copy_instrument_files  # type: ignore[attr-defined]
        self._original_refinement_function = (
            tree_module.remote_do_refinement_no_saving._function  # type: ignore[attr-defined]
        )
        self._original_absolute_log_error = peak_matcher_module.absolute_log_error

        peak_matcher_module.absolute_log_error = cast(Any, _scalar_absolute_log_error)

        def counted_refinement(*args: Any, **kwargs: Any) -> Any:
            self.refinement_calls += 1
            return self._original_refinement_function(*args, **kwargs)

        cast(Any, tree_module.remote_do_refinement_no_saving)._function = counted_refinement

        def copy_instrument_files_with_ger(
            instrument_profile: str | Path,
            working_dir: Path,
        ) -> str:
            instrument_name = self._original_copy_instrument_files(
                instrument_profile,
                working_dir,
            )
            profile_path = Path(instrument_profile)
            if profile_path.suffix == ".geq" and profile_path.exists():
                ger_path = profile_path.with_suffix(".ger")
            else:
                ger_path = (
                    Path(eflech_module.__file__).parent
                    / "data"
                    / "BGMN-Templates"
                    / "Devices"
                    / f"{instrument_name}.ger"
                )
            if ger_path.is_file():
                shutil.copy(ger_path, working_dir)
            return str(instrument_name)

        eflech_module.copy_instrument_files = copy_instrument_files_with_ger  # type: ignore[attr-defined]

        def local_batch_refinement(
            pattern_path: Path,
            cif_paths: list[Any],
            wavelength: str | float = "Cu",
            instrument_profile: str | Path = "Aeris-fds-Pixcel1d-Medipix3",
            phase_params: dict[str, Any] | None = None,
            refinement_params: dict[str, Any] | None = None,
        ) -> list[Any]:
            return [
                cast(Any, tree_module.remote_do_refinement_no_saving)._function(
                    pattern_path,
                    references,
                    wavelength,
                    instrument_profile,
                    phase_params,
                    refinement_params,
                )
                for references in cif_paths
            ]

        def local_batch_peak_matching(
            peak_calcs: list[np.ndarray],
            peak_obs: np.ndarray | list[np.ndarray],
            return_type: str = "PeakMatcher",
            batch_size: int = 100,
            score_kwargs: Any = None,
        ) -> list[Any]:
            del score_kwargs
            if isinstance(peak_obs, np.ndarray):
                peak_obs = [peak_obs] * len(peak_calcs)
            if len(peak_calcs) != len(peak_obs):
                raise ValueError("peak calculation and observation lengths differ")
            all_data = list(zip(peak_calcs, peak_obs, strict=True))
            results: list[Any] = []
            for start in range(0, len(all_data), batch_size):
                results.extend(
                    tree_module.remote_peak_matching._function(  # type: ignore[attr-defined]
                        all_data[start : start + batch_size],
                        return_type=return_type,
                    )
                )
            return results

        tree_module.batch_refinement = local_batch_refinement
        tree_module.batch_peak_matching = local_batch_peak_matching
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._tree is None or self._eflech is None:
            return
        self._tree.batch_refinement = self._original_batch_refinement
        self._tree.batch_peak_matching = self._original_batch_peak_matching
        self._tree.remote_do_refinement_no_saving._function = (
            self._original_refinement_function
        )
        self._eflech.copy_instrument_files = self._original_copy_instrument_files
        import dara.search.peak_matcher as peak_matcher_module

        cast(Any, peak_matcher_module).absolute_log_error = self._original_absolute_log_error


class LiveDaraPilot:
    """Run Original-DARA and H1-DARA from one cloned initialized SearchTree."""

    def __init__(
        self,
        *,
        pattern_path: Path,
        phase_paths: tuple[Path, ...],
        instrument_profile: str = "Aeris-fds-Pixcel1d-Medipix3",
        max_phases: int = 3,
        express_mode: bool = True,
        enable_angular_cut: bool = True,
        root_zero_match_recall: bool = False,
    ) -> None:
        self.pattern_path = pattern_path.resolve()
        self.phase_paths = tuple(path.resolve() for path in phase_paths)
        self.instrument_profile = instrument_profile
        self.max_phases = max_phases
        self.express_mode = express_mode
        self.enable_angular_cut = enable_angular_cut
        self.root_zero_match_recall = root_zero_match_recall

    def initialize(self) -> tuple[DaraLiveRuntime, Any]:
        """Enter the live runtime and construct one revision-zero SearchTree."""

        runtime = DaraLiveRuntime().__enter__()
        try:
            from dara.search.core import DEFAULT_PHASE_PARAMS, DEFAULT_REFINEMENT_PARAMS
            from dara.search.tree import SearchTree

            tree = SearchTree(
                pattern_path=self.pattern_path,
                cif_paths=list(self.phase_paths),
                pinned_phases=None,
                refine_params=dict(DEFAULT_REFINEMENT_PARAMS),
                phase_params=dict(DEFAULT_PHASE_PARAMS),
                instrument_profile=self.instrument_profile,
                express_mode=self.express_mode,
                enable_angular_cut=self.enable_angular_cut,
                max_phases=self.max_phases,
                record_peak_matcher_scores=True,
            )
            if self.root_zero_match_recall:
                original_score_phases = tree.score_phases

                def score_phases_with_root_recall(
                    _tree: Any,
                    all_phases_result: dict[Any, Any],
                    current_result: Any = None,
                ) -> tuple[list[Any], dict[Any, list[float]], float]:
                    return _score_with_root_zero_match_recall(
                        original_score_phases,
                        all_phases_result,
                        current_result,
                    )

                setattr(tree, "score_phases", MethodType(score_phases_with_root_recall, tree))
        except Exception:
            runtime.__exit__(None, None, None)
            raise
        return runtime, tree

    def prepare_frontier(
        self,
        tree: Any,
        *,
        revision: int,
        cache_keys: frozenset[str],
        fifo_positions: dict[str, int],
    ) -> tuple[PreparedLiveAction, ...]:
        actions: list[PreparedLiveAction] = []
        for node in tree.all_nodes():
            data = node.data
            if data is None or data.status != "pending":
                continue
            node_id = str(node.identifier)
            if node_id not in fifo_positions:
                fifo_positions[node_id] = len(fifo_positions)
            current_phases = tuple(phase.path.stem for phase in data.current_phases)
            remaining = {
                phase: result
                for phase, result in tree.all_phases_result.items()
                if phase not in set(data.current_phases)
            }
            best_phases, raw_scores, _ = tree.score_phases(
                remaining,
                data.current_result,
            )
            siblings: tuple[PreparedSibling, ...]
            if not best_phases:
                terminal_id = "__dara_terminal__"
                terminal_key = _strict_key(self.pattern_path, current_phases, terminal_id)
                siblings = (
                    PreparedSibling(
                        sibling_id=terminal_id,
                        strict_key=terminal_key,
                        cache_hit=True,
                    ),
                )
                benefit = 0.0
            else:
                siblings = tuple(
                    PreparedSibling(
                        sibling_id=phase.path.stem,
                        strict_key=_strict_key(self.pattern_path, current_phases, phase.path.stem),
                        cache_hit=(
                            _strict_key(self.pattern_path, current_phases, phase.path.stem)
                            in cache_keys
                        ),
                    )
                    for phase in best_phases
                )
                benefit = sum(
                    _raw_benefit(raw_scores.get(phase, 0.0)) for phase in best_phases
                )
                if benefit == 0.0:
                    benefit = float(len(best_phases))
            actions.append(
                PreparedLiveAction(
                    action=PreparedExpansion(
                        action_id=f"node:{node_id}",
                        parent_id=node_id,
                        state_revision=revision,
                        fifo_position=fifo_positions[node_id],
                        dara_benefit=benefit,
                        v3_benefit=benefit,
                        siblings=siblings,
                    ),
                    node_id=node_id,
                )
            )
        return tuple(actions)

    def run_arm(
        self,
        tree: Any,
        runtime: DaraLiveRuntime,
        *,
        arm: Arm,
        budget: int,
    ) -> LiveArmResult:
        if budget < 0:
            raise ValueError("budget must be nonnegative")
        revision = 0
        debit = 0
        cache_keys = frozenset[str]()
        fifo_positions: dict[str, int] = {}
        selected: list[str] = []
        actual_costs: list[int] = []
        mismatches: list[tuple[str, int, int]] = []

        while True:
            prepared = self.prepare_frontier(
                tree,
                revision=revision,
                cache_keys=cache_keys,
                fifo_positions=fifo_positions,
            )
            action_by_id = {item.action.action_id: item for item in prepared}
            decision = schedule_next(
                arm=arm,
                actions=tuple(item.action for item in prepared),
                state_revision=revision,
                remaining_budget=budget - debit,
                validated_cache_keys=cache_keys,
            )
            if decision.selected_action_id is None:
                stop_reason = decision.stop_reason or "unknown_stop"
                break
            selected_action = action_by_id[decision.selected_action_id]
            assessment = next(
                item
                for item in decision.assessments
                if item.action_id == decision.selected_action_id
            )
            calls_before = runtime.refinement_calls
            tree.expand_node(selected_action.node_id)
            actual_cost = runtime.refinement_calls - calls_before
            actual_costs.append(actual_cost)
            if actual_cost != assessment.new_call_cost:
                mismatches.append(
                    (selected_action.action.action_id, assessment.new_call_cost, actual_cost)
                )
                stop_reason = "predicted_actual_cost_mismatch"
                break
            debit += actual_cost
            cache_keys = cache_keys.union(
                sibling.strict_key for sibling in selected_action.action.siblings
            )
            selected.append(selected_action.action.action_id)
            revision += 1

        top1_phase_ids: tuple[str, ...] = ()
        results = tree.get_search_results()
        if results:
            top1_phase_ids = tuple(
                sorted(
                    alternatives[0].path.stem
                    for alternatives in results[0].phases
                    if alternatives
                )
            )
        return LiveArmResult(
            arm=arm,
            budget=budget,
            branch_calls=debit,
            selected_action_ids=tuple(selected),
            stop_reason=stop_reason,
            actual_costs=tuple(actual_costs),
            cost_mismatches=tuple(mismatches),
            top1_phase_ids=top1_phase_ids,
        )

    def run_budgets(self, *, budgets: tuple[int, ...]) -> tuple[LiveArmResult, ...]:
        """Initialize once, then clone the same state for each arm and budget."""

        if any(budget < 0 for budget in budgets):
            raise ValueError("budgets must be nonnegative")
        runtime, tree = self.initialize()
        try:
            results: list[LiveArmResult] = []
            for budget in budgets:
                for arm in (Arm.ORIGINAL_DARA, Arm.H1_DARA):
                    results.append(
                        self.run_arm(copy.deepcopy(tree), runtime, arm=arm, budget=budget)
                    )
            return tuple(results)
        finally:
            runtime.__exit__(None, None, None)

    def run_comparison(self, *, budget: int) -> tuple[LiveArmResult, ...]:
        """Initialize once, clone the state, then run Original and H1 arms."""

        runtime, tree = self.initialize()
        try:
            return tuple(
                self.run_arm(copy.deepcopy(tree), runtime, arm=arm, budget=budget)
                for arm in (Arm.ORIGINAL_DARA, Arm.H1_DARA)
            )
        finally:
            runtime.__exit__(None, None, None)


__all__ = (
    "DaraLiveRuntime",
    "LiveArmResult",
    "LiveDaraPilot",
    "PreparedLiveAction",
)

"""Ground-truth-isolated preparation and tracing for public XRED runs."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, cast

from dara_cost_aware.live_dara import DaraLiveRuntime, LiveDaraPilot
from dara_cost_aware.scheduling import Arm, schedule_next


PRIMARY_PROFILE_NAMES = frozenset({"data.csv", "intensity.csv"})


@dataclass(frozen=True)
class TraceConfig:
    """One predeclared Original-DARA sensitivity configuration."""

    name: str
    express_mode: bool
    enable_angular_cut: bool
    max_phases: int = 5


def default_trace_configs() -> tuple[TraceConfig, ...]:
    """Return official defaults first, followed by a two-toggle diagnostic grid."""

    return (
        TraceConfig("official", express_mode=True, enable_angular_cut=True),
        TraceConfig("no-express", express_mode=False, enable_angular_cut=True),
        TraceConfig("no-angular-cut", express_mode=True, enable_angular_cut=False),
        TraceConfig(
            "no-express-no-angular-cut",
            express_mode=False,
            enable_angular_cut=False,
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "unnamed"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_pickle(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _sha256(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_two_column_csv(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            try:
                x_value = float(row[0].strip())
                y_value = float(row[1].strip())
            except ValueError:
                continue
            if math.isfinite(x_value) and math.isfinite(y_value):
                rows.append((x_value, y_value))
    if len(rows) < 2:
        raise ValueError(f"profile has fewer than two numeric rows: {path}")
    if any(right[0] <= left[0] for left, right in zip(rows, rows[1:], strict=False)):
        raise ValueError(f"profile x axis is not strictly increasing: {path}")
    return rows


def _write_xy(path: Path, rows: Iterable[tuple[float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for x_value, y_value in rows:
            handle.write(f"{x_value:.10g} {y_value:.10g}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_xred_cohort(source: Path, output: Path) -> tuple[Path, Path]:
    """Prepare all primary XRED profiles and split run inputs from labels."""

    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)

    profiles = sorted(
        path
        for path in source.rglob("*.csv")
        if path.name.casefold() in PRIMARY_PROFILE_NAMES
    )
    if not profiles:
        raise ValueError(f"no primary XRED profiles found below {source}")

    cif_sources: dict[str, list[Path]] = {}
    for cif_path in sorted(source.rglob("*.cif")):
        cif_sources.setdefault(_sha256(cif_path), []).append(cif_path)
    if not cif_sources:
        raise ValueError(f"no CIF files found below {source}")

    phase_id_by_hash: dict[str, str] = {}
    phase_records: list[dict[str, Any]] = []
    phase_bank: list[str] = []
    for digest, sources in sorted(cif_sources.items(), key=lambda item: str(item[1][0])):
        canonical = sources[0]
        phase_id = f"xred-{_slug(canonical.stem)}-{digest[:10]}"
        destination = output / "cifs" / f"{phase_id}.cif"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, destination)
        phase_id_by_hash[digest] = phase_id
        phase_bank.append(str(destination))
        phase_records.append(
            {
                "phase_id": phase_id,
                "sha256": digest,
                "path": str(destination),
                "source_paths": [str(path.relative_to(source)) for path in sources],
            }
        )

    run_patterns: list[dict[str, Any]] = []
    label_patterns: list[dict[str, Any]] = []
    used_pattern_ids: set[str] = set()
    expected_by_category = {"monophase": 1, "biphase": 2, "triphase": 3}
    for profile in profiles:
        relative = profile.relative_to(source)
        category = relative.parts[0] if len(relative.parts) > 1 else "unclassified"
        base_id = _slug("-".join((category, profile.parent.name, profile.stem)))
        pattern_id = f"xred-{base_id}"
        if pattern_id in used_pattern_ids:
            pattern_id = f"{pattern_id}-{_sha256(profile)[:8]}"
        used_pattern_ids.add(pattern_id)

        rows = _read_two_column_csv(profile)
        xy_path = output / "xy" / f"{pattern_id}.xy"
        _write_xy(xy_path, rows)
        local_hashes = sorted({_sha256(path) for path in profile.parent.glob("*.cif")})
        declared_ids = sorted(phase_id_by_hash[digest] for digest in local_hashes)
        expected = expected_by_category.get(category.casefold())
        if expected is None:
            label_status = "folder-declared-set"
        elif len(declared_ids) == expected:
            label_status = "complete"
        else:
            label_status = "partial"

        run_patterns.append(
            {
                "pattern_id": pattern_id,
                "xy_path": str(xy_path),
                "kind": category,
                "point_count": len(rows),
                "source_profile": str(relative),
                "source_sha256": _sha256(profile),
            }
        )
        label_patterns.append(
            {
                "pattern_id": pattern_id,
                "declared_phase_ids": declared_ids,
                "declared_phase_count": len(declared_ids),
                "expected_phase_count": expected,
                "label_status": label_status,
            }
        )

    run_manifest_path = output / "run_manifest.json"
    labels_path = output / "labels.json"
    _atomic_write_json(
        run_manifest_path,
        {
            "schema_version": 1,
            "benchmark_id": "xred-primary-26-original-dara-trace",
            "source": str(source),
            "phase_bank": phase_bank,
            "phase_bank_size": len(phase_bank),
            "patterns": run_patterns,
        },
    )
    _atomic_write_json(
        labels_path,
        {
            "schema_version": 1,
            "benchmark_id": "xred-primary-26-original-dara-trace",
            "run_manifest_sha256": _sha256(run_manifest_path),
            "phases": phase_records,
            "patterns": label_patterns,
        },
    )
    return run_manifest_path, labels_path


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _phase_id(phase: Any) -> str:
    return str(phase.path.stem)


def _result_summary(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    lst = result.lst_data
    try:
        weights = {
            str(key): _finite_float(value)
            for key, value in result.get_phase_weights().items()
        }
    except (TypeError, ValueError, ZeroDivisionError):
        weights = {}
    return {
        "rwp": _finite_float(lst.rwp),
        "rpb": _finite_float(lst.rpb),
        "rp": _finite_float(lst.rp),
        "rho": _finite_float(lst.rho),
        "phase_weights": weights,
        "calculated_peak_count": int(len(result.peak_data)),
    }


def _node_summary(tree: Any, node: Any) -> dict[str, Any]:
    data = node.data
    parent = tree.parent(node.identifier)
    raw_scores = data.peak_matcher_scores or {}
    return {
        "node_id": str(node.identifier),
        "parent_id": None if parent is None else str(parent.identifier),
        "status": str(data.status),
        "phase_ids": [_phase_id(phase) for phase in data.current_phases],
        "group_id": int(data.group_id),
        "fom": _finite_float(data.fom),
        "lattice_strain": _finite_float(data.lattice_strain),
        "peak_matcher_threshold": _finite_float(data.peak_matcher_threshold),
        "peak_matcher_scores": {
            _phase_id(phase): [_finite_float(value) for value in values]
            for phase, values in raw_scores.items()
        },
        "isolated_missing_peaks": data.isolated_missing_peaks,
        "isolated_extra_peaks": data.isolated_extra_peaks,
        "result": _result_summary(data.current_result),
    }


def _top_results(tree: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result in tree.get_search_results():
        records.append(
            {
                "phase_alternatives": [
                    sorted(_phase_id(phase) for phase in alternatives)
                    for alternatives in result.phases
                ],
                "foms": [list(map(_finite_float, values)) for values in result.foms],
                "lattice_strains": [
                    list(map(_finite_float, values)) for values in result.lattice_strains
                ],
                "missing_peaks": result.missing_peaks,
                "extra_peaks": result.extra_peaks,
                "result": _result_summary(result.refinement_result),
            }
        )
    return records


def _tree_summary(tree: Any, input_phase_ids: tuple[str, ...]) -> dict[str, Any]:
    singleton_results = {
        _phase_id(phase): _result_summary(result)
        for phase, result in tree.all_phases_result.items()
    }
    return {
        "nodes": [_node_summary(tree, node) for node in tree.all_nodes()],
        "singleton_results": singleton_results,
        "removed_singleton_phase_ids": sorted(set(input_phase_ids) - singleton_results.keys()),
        "top_results": _top_results(tree),
    }


def _prepared_payload(prepared: tuple[Any, ...], decision: Any) -> list[dict[str, Any]]:
    assessments = {item.action_id: item for item in decision.assessments}
    payload: list[dict[str, Any]] = []
    for item in prepared:
        action = item.action
        assessment = assessments[action.action_id]
        payload.append(
            {
                "action_id": action.action_id,
                "parent_id": action.parent_id,
                "fifo_position": action.fifo_position,
                "benefit": _finite_float(assessment.benefit),
                "new_call_cost": assessment.new_call_cost,
                "eligible": assessment.eligible,
                "rejection_reason": assessment.rejection_reason,
                "ordering_key": list(assessment.ordering_key),
                "siblings": [
                    {
                        "phase_id": sibling.sibling_id,
                        "strict_key": sibling.strict_key,
                        "cache_hit": sibling.cache_hit,
                    }
                    for sibling in action.siblings
                ],
            }
        )
    return payload


def _event_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = event.get("event_id")
            if isinstance(event_id, str):
                ids.add(event_id)
    return ids


def _seal_checkpoint(
    job_dir: Path,
    tree: Any,
    state: dict[str, Any],
    *,
    last_event: dict[str, Any] | None,
) -> None:
    tree_path = job_dir / "tree.pickle"
    tree_sha256 = _atomic_pickle(tree_path, tree)
    checkpoint = {
        **state,
        "tree_pickle": str(tree_path),
        "tree_pickle_sha256": tree_sha256,
        "last_event": last_event,
    }
    _atomic_write_json(job_dir / "checkpoint.json", checkpoint)


def _load_checkpoint(job_dir: Path) -> tuple[Any, dict[str, Any]] | None:
    checkpoint_path = job_dir / "checkpoint.json"
    if not checkpoint_path.is_file():
        return None
    state = cast(dict[str, Any], json.loads(checkpoint_path.read_text(encoding="utf-8")))
    tree_path = Path(state["tree_pickle"])
    if _sha256(tree_path) != state["tree_pickle_sha256"]:
        raise ValueError(f"tree checkpoint hash mismatch: {tree_path}")
    with tree_path.open("rb") as handle:
        tree = pickle.load(handle)
    return tree, state


def _run_job(
    *,
    pattern: dict[str, Any],
    phase_paths: tuple[Path, ...],
    config: TraceConfig,
    job_dir: Path,
    branch_budget: int,
) -> dict[str, Any]:
    complete_path = job_dir / "complete.json"
    if complete_path.is_file():
        return cast(dict[str, Any], json.loads(complete_path.read_text(encoding="utf-8")))

    job_dir.mkdir(parents=True, exist_ok=True)
    events_path = job_dir / "events.jsonl"
    runtime = DaraLiveRuntime().__enter__()
    try:
        checkpoint = _load_checkpoint(job_dir)
        if checkpoint is None:
            runtime.__exit__(None, None, None)
            pilot = LiveDaraPilot(
                pattern_path=Path(pattern["xy_path"]),
                phase_paths=phase_paths,
                max_phases=config.max_phases,
                express_mode=config.express_mode,
                enable_angular_cut=config.enable_angular_cut,
            )
            runtime, tree = pilot.initialize()
            state: dict[str, Any] = {
                "schema_version": 1,
                "pattern_id": pattern["pattern_id"],
                "config": asdict(config),
                "status": "running",
                "revision": 0,
                "branch_calls": 0,
                "singleton_calls": runtime.refinement_calls,
                "cache_keys": [],
                "fifo_positions": {},
                "selected_action_ids": [],
                "actual_costs": [],
                "cost_mismatches": [],
                "started_unix": time.time(),
            }
            _seal_checkpoint(job_dir, tree, state, last_event=None)
        else:
            tree, state = checkpoint
            last_event = state.get("last_event")
            if isinstance(last_event, dict):
                event_id = last_event.get("event_id")
                if isinstance(event_id, str) and event_id not in _event_ids(events_path):
                    _append_jsonl(events_path, last_event)
            pilot = LiveDaraPilot(
                pattern_path=Path(pattern["xy_path"]),
                phase_paths=phase_paths,
                max_phases=config.max_phases,
                express_mode=config.express_mode,
                enable_angular_cut=config.enable_angular_cut,
            )

        revision = int(state["revision"])
        debit = int(state["branch_calls"])
        cache_keys = frozenset(map(str, state["cache_keys"]))
        fifo_positions = {
            str(key): int(value) for key, value in state["fifo_positions"].items()
        }
        selected = list(map(str, state["selected_action_ids"]))
        actual_costs = list(map(int, state["actual_costs"]))
        mismatches = [list(item) for item in state["cost_mismatches"]]
        input_phase_ids = tuple(path.stem for path in phase_paths)

        while True:
            prepared = pilot.prepare_frontier(
                tree,
                revision=revision,
                cache_keys=cache_keys,
                fifo_positions=fifo_positions,
            )
            decision = schedule_next(
                arm=Arm.ORIGINAL_DARA,
                actions=tuple(item.action for item in prepared),
                state_revision=revision,
                remaining_budget=branch_budget - debit,
                validated_cache_keys=cache_keys,
            )
            if decision.selected_action_id is None:
                stop_reason = decision.stop_reason or "unknown_stop"
                break

            action_by_id = {item.action.action_id: item for item in prepared}
            selected_action = action_by_id[decision.selected_action_id]
            assessment = next(
                item
                for item in decision.assessments
                if item.action_id == decision.selected_action_id
            )
            nodes_before = {str(node.identifier) for node in tree.all_nodes()}
            calls_before = runtime.refinement_calls
            tree.expand_node(selected_action.node_id)
            actual_cost = runtime.refinement_calls - calls_before
            created_nodes = [
                _node_summary(tree, node)
                for node in tree.all_nodes()
                if str(node.identifier) not in nodes_before
            ]
            if actual_cost != assessment.new_call_cost:
                mismatches.append(
                    [selected_action.action.action_id, assessment.new_call_cost, actual_cost]
                )
            debit += actual_cost
            cache_keys = cache_keys.union(
                sibling.strict_key for sibling in selected_action.action.siblings
            )
            selected.append(selected_action.action.action_id)
            actual_costs.append(actual_cost)
            revision += 1
            event = {
                "event_id": f"{pattern['pattern_id']}:{config.name}:{revision}",
                "revision_before": revision - 1,
                "revision_after": revision,
                "remaining_budget_before": branch_budget - debit + actual_cost,
                "frontier": _prepared_payload(prepared, decision),
                "selected_action_id": decision.selected_action_id,
                "predicted_cost": assessment.new_call_cost,
                "actual_cost": actual_cost,
                "created_nodes": created_nodes,
            }
            state.update(
                {
                    "revision": revision,
                    "branch_calls": debit,
                    "cache_keys": sorted(cache_keys),
                    "fifo_positions": fifo_positions,
                    "selected_action_ids": selected,
                    "actual_costs": actual_costs,
                    "cost_mismatches": mismatches,
                }
            )
            _seal_checkpoint(job_dir, tree, state, last_event=event)
            if event["event_id"] not in _event_ids(events_path):
                _append_jsonl(events_path, event)
            if mismatches:
                stop_reason = "predicted_actual_cost_mismatch"
                break

        final = {
            **state,
            "status": "complete",
            "stop_reason": stop_reason,
            "completed_unix": time.time(),
            "elapsed_seconds": time.time() - float(state["started_unix"]),
            "tree": _tree_summary(tree, input_phase_ids),
            "events_path": str(events_path),
            "events_sha256": _sha256(events_path) if events_path.is_file() else None,
        }
        _atomic_write_json(complete_path, final)
        state.update({"status": "complete", "stop_reason": stop_reason})
        _seal_checkpoint(job_dir, tree, state, last_event=state.get("last_event"))
        return final
    finally:
        runtime.__exit__(None, None, None)


def run_xred_trace(
    manifest_path: Path,
    output_root: Path,
    *,
    branch_budget: int = 1_000_000,
    configs: tuple[TraceConfig, ...] | None = None,
) -> dict[str, Any]:
    """Run or resume Original DARA over every manifest pattern and configuration."""

    if branch_budget <= 0:
        raise ValueError("branch_budget must be positive")
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    forbidden = {"gt_phase_ids", "declared_phase_ids", "ground_truth", "labels"}
    manifest_keys = set().union(*(set(pattern) for pattern in manifest["patterns"]))
    leaked = forbidden.intersection(manifest_keys)
    if leaked:
        raise ValueError(f"live manifest contains offline label fields: {sorted(leaked)}")

    phase_paths = tuple(Path(path).resolve() for path in manifest["phase_bank"])
    selected_configs = configs or default_trace_configs()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    progress_path = output_root / "progress.jsonl"
    records: list[dict[str, Any]] = []
    for config in selected_configs:
        for pattern in manifest["patterns"]:
            job_dir = output_root / config.name / str(pattern["pattern_id"])
            try:
                result = _run_job(
                    pattern=pattern,
                    phase_paths=phase_paths,
                    config=config,
                    job_dir=job_dir,
                    branch_budget=branch_budget,
                )
                progress = {
                    "pattern_id": pattern["pattern_id"],
                    "config": config.name,
                    "status": "complete",
                    "branch_calls": result["branch_calls"],
                    "singleton_calls": result["singleton_calls"],
                    "stop_reason": result["stop_reason"],
                    "cost_mismatch_count": len(result["cost_mismatches"]),
                    "job_dir": str(job_dir),
                }
            except Exception as error:
                progress = {
                    "pattern_id": pattern["pattern_id"],
                    "config": config.name,
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "job_dir": str(job_dir),
                }
                _atomic_write_json(job_dir / "error.json", progress)
            records.append(progress)
            _append_jsonl(progress_path, progress)
            summary = {
                "schema_version": 1,
                "benchmark_id": manifest.get("benchmark_id", "unknown"),
                "method": "original-dara-checkpointed-trace",
                "manifest": str(manifest_path),
                "manifest_sha256": _sha256(manifest_path),
                "ground_truth_loaded": False,
                "branch_budget": branch_budget,
                "configs": [asdict(item) for item in selected_configs],
                "pattern_count": len(manifest["patterns"]),
                "phase_bank_size": len(phase_paths),
                "completed_jobs": sum(row["status"] == "complete" for row in records),
                "error_jobs": sum(row["status"] == "error" for row in records),
                "total_jobs": len(manifest["patterns"]) * len(selected_configs),
                "records": records,
            }
            _atomic_write_json(output_root / "summary.json", summary)
            print(json.dumps(progress, sort_keys=True), flush=True)
    return summary




def _config_named(name: str) -> TraceConfig:
    for config in default_trace_configs():
        if config.name == name:
            return config
    raise ValueError(f"unknown trace configuration: {name}")


def _job_progress(
    *,
    pattern_id: str,
    config_name: str,
    job_dir: Path,
) -> dict[str, Any]:
    complete_path = job_dir / "complete.json"
    if complete_path.is_file():
        result = cast(
            dict[str, Any],
            json.loads(complete_path.read_text(encoding="utf-8")),
        )
        return {
            "pattern_id": pattern_id,
            "config": config_name,
            "status": "complete",
            "branch_calls": int(result.get("branch_calls", 0)),
            "singleton_calls": int(result.get("singleton_calls", 0)),
            "stop_reason": result.get("stop_reason"),
            "cost_mismatch_count": len(result.get("cost_mismatches", [])),
            "job_dir": str(job_dir),
        }
    error_path = job_dir / "error.json"
    if error_path.is_file():
        error = cast(
            dict[str, Any],
            json.loads(error_path.read_text(encoding="utf-8")),
        )
        return {
            "pattern_id": pattern_id,
            "config": config_name,
            "status": "error",
            "error_type": error.get("error_type", "UnknownError"),
            "error": error.get("error", "worker failed without an error message"),
            "job_dir": str(job_dir),
        }
    return {
        "pattern_id": pattern_id,
        "config": config_name,
        "status": "pending",
        "job_dir": str(job_dir),
    }


def collect_xred_trace_summary(
    manifest_path: Path,
    output_root: Path,
    *,
    branch_budget: int = 1_000_000,
    configs: tuple[TraceConfig, ...] | None = None,
) -> dict[str, Any]:
    """Collect the current job artifacts, preferring sealed completions."""

    manifest_path = manifest_path.resolve()
    manifest = cast(
        dict[str, Any],
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    selected_configs = configs or default_trace_configs()
    output_root = output_root.resolve()
    records = [
        _job_progress(
            pattern_id=str(pattern["pattern_id"]),
            config_name=config.name,
            job_dir=output_root / config.name / str(pattern["pattern_id"]),
        )
        for config in selected_configs
        for pattern in manifest["patterns"]
    ]
    summary = {
        "schema_version": 1,
        "benchmark_id": manifest.get("benchmark_id", "unknown"),
        "method": "original-dara-process-isolated-trace",
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "ground_truth_loaded": False,
        "branch_budget": branch_budget,
        "configs": [asdict(item) for item in selected_configs],
        "pattern_count": len(manifest["patterns"]),
        "phase_bank_size": len(manifest["phase_bank"]),
        "completed_jobs": sum(row["status"] == "complete" for row in records),
        "error_jobs": sum(row["status"] == "error" for row in records),
        "pending_jobs": sum(row["status"] == "pending" for row in records),
        "total_jobs": len(records),
        "records": records,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_root / "summary.json", summary)
    return summary


def run_xred_trace_job(
    manifest_path: Path,
    output_root: Path,
    *,
    pattern_id: str,
    config_name: str,
    branch_budget: int = 1_000_000,
) -> dict[str, Any]:
    """Run exactly one pattern/configuration job in the current process."""

    manifest_path = manifest_path.resolve()
    manifest = cast(
        dict[str, Any],
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    forbidden = {"gt_phase_ids", "declared_phase_ids", "ground_truth", "labels"}
    manifest_keys = set().union(*(set(pattern) for pattern in manifest["patterns"]))
    leaked = forbidden.intersection(manifest_keys)
    if leaked:
        raise ValueError(f"live manifest contains offline label fields: {sorted(leaked)}")
    matching = [
        pattern for pattern in manifest["patterns"] if pattern["pattern_id"] == pattern_id
    ]
    if len(matching) != 1:
        raise ValueError(f"expected one manifest pattern named {pattern_id!r}")
    config = _config_named(config_name)
    phase_paths = tuple(Path(path).resolve() for path in manifest["phase_bank"])
    output_root = output_root.resolve()
    job_dir = output_root / config.name / pattern_id
    try:
        result = _run_job(
            pattern=matching[0],
            phase_paths=phase_paths,
            config=config,
            job_dir=job_dir,
            branch_budget=branch_budget,
        )
        progress = {
            "pattern_id": pattern_id,
            "config": config.name,
            "status": "complete",
            "branch_calls": result["branch_calls"],
            "singleton_calls": result["singleton_calls"],
            "stop_reason": result["stop_reason"],
            "cost_mismatch_count": len(result["cost_mismatches"]),
            "job_dir": str(job_dir),
        }
    except Exception as error:
        progress = {
            "pattern_id": pattern_id,
            "config": config.name,
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(error),
            "job_dir": str(job_dir),
        }
        _atomic_write_json(job_dir / "error.json", progress)
    _append_jsonl(output_root / "progress.jsonl", progress)
    print(json.dumps(progress, sort_keys=True), flush=True)
    return progress


def _deduplicated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    path_parts = environment.get("PATH", "").split(os.pathsep)
    environment["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))
    return environment


def run_xred_trace_isolated(
    manifest_path: Path,
    output_root: Path,
    *,
    branch_budget: int = 1_000_000,
    configs: tuple[TraceConfig, ...] | None = None,
    worker_script: Path,
    python_executable: Path | None = None,
) -> dict[str, Any]:
    """Retry incomplete jobs with one fresh Python process per job."""

    if branch_budget <= 0:
        raise ValueError("branch_budget must be positive")
    manifest_path = manifest_path.resolve()
    manifest = cast(
        dict[str, Any],
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    selected_configs = configs or default_trace_configs()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    executable = python_executable or Path(sys.executable)
    worker_log = output_root / "isolated_workers.log"
    for config in selected_configs:
        for pattern in manifest["patterns"]:
            pattern_id = str(pattern["pattern_id"])
            job_dir = output_root / config.name / pattern_id
            if (job_dir / "complete.json").is_file():
                continue
            completed = subprocess.run(
                [
                    str(executable),
                    str(worker_script.resolve()),
                    "--manifest",
                    str(manifest_path),
                    "--output-root",
                    str(output_root),
                    "--branch-budget",
                    str(branch_budget),
                    "--worker-pattern-id",
                    pattern_id,
                    "--worker-config",
                    config.name,
                ],
                capture_output=True,
                check=False,
                env=_deduplicated_environment(),
                text=True,
            )
            with worker_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "pattern_id": pattern_id,
                            "config": config.name,
                            "returncode": completed.returncode,
                            "stdout": completed.stdout,
                            "stderr": completed.stderr,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            if (
                completed.returncode != 0
                and not (job_dir / "complete.json").is_file()
                and not (job_dir / "error.json").is_file()
            ):
                _atomic_write_json(
                    job_dir / "error.json",
                    {
                        "pattern_id": pattern_id,
                        "config": config.name,
                        "status": "error",
                        "error_type": "WorkerProcessError",
                        "error": completed.stderr or completed.stdout,
                        "job_dir": str(job_dir),
                    },
                )
            summary = collect_xred_trace_summary(
                manifest_path,
                output_root,
                branch_budget=branch_budget,
                configs=selected_configs,
            )
            print(
                json.dumps(
                    {
                        "completed_jobs": summary["completed_jobs"],
                        "error_jobs": summary["error_jobs"],
                        "pending_jobs": summary["pending_jobs"],
                        "total_jobs": summary["total_jobs"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    return collect_xred_trace_summary(
        manifest_path,
        output_root,
        branch_budget=branch_budget,
        configs=selected_configs,
    )

__all__ = (
    "TraceConfig",
    "build_xred_cohort",
    "default_trace_configs",
    "run_xred_trace",
    "run_xred_trace_isolated",
    "run_xred_trace_job",
)

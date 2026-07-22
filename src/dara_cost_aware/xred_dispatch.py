"""Process-isolated orchestration for resumable XRED trace jobs."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, cast

from dara_cost_aware.xred_trace import (
    TraceConfig,
    _append_jsonl,
    _atomic_write_json,
    _sha256,
    default_trace_configs,
    load_sealed_xred_completion,
    load_xred_run_manifest,
)


def _load_failure_seal(
    job_dir: Path,
    *,
    pattern_id: str,
    config_name: str,
) -> dict[str, Any] | None:
    seal_path = job_dir / "failure_seal.json"
    error_path = job_dir / "error.json"
    if not seal_path.is_file() or not error_path.is_file():
        return None
    try:
        seal = cast(
            dict[str, Any],
            json.loads(seal_path.read_text(encoding="utf-8")),
        )
        if seal.get("status") != "terminal_error":
            return None
        if seal.get("pattern_id") != pattern_id or seal.get("config") != config_name:
            return None
        if _sha256(error_path) != seal.get("error_sha256"):
            return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return seal


def _isolated_attempt_count(job_dir: Path) -> int:
    attempts_path = job_dir / "isolated_attempts.jsonl"
    if not attempts_path.is_file():
        return 0
    return sum(1 for line in attempts_path.read_text(encoding="utf-8").splitlines() if line)


def _seal_failed_retry(
    job_dir: Path,
    *,
    pattern_id: str,
    config_name: str,
) -> dict[str, Any]:
    error_path = job_dir / "error.json"
    if not error_path.is_file():
        raise ValueError(f"failed worker did not persist an error artifact: {job_dir}")
    seal = {
        "schema_version": 1,
        "pattern_id": pattern_id,
        "config": config_name,
        "status": "terminal_error",
        "isolated_attempts": _isolated_attempt_count(job_dir),
        "error_path": str(error_path),
        "error_sha256": _sha256(error_path),
        "sealed_unix": time.time(),
        "retry_policy": "one isolated retry after the initial long-lived run",
    }
    _atomic_write_json(job_dir / "failure_seal.json", seal)
    return seal


def _job_progress(
    *,
    pattern_id: str,
    config_name: str,
    job_dir: Path,
) -> dict[str, Any]:
    completion = load_sealed_xred_completion(
        job_dir,
        pattern_id=pattern_id,
        config_name=config_name,
    )
    if completion is not None:
        return {
            "pattern_id": pattern_id,
            "config": config_name,
            "status": "complete",
            "branch_calls": int(completion.get("branch_calls", 0)),
            "singleton_calls": int(completion.get("singleton_calls", 0)),
            "stop_reason": completion.get("stop_reason"),
            "cost_mismatch_count": len(completion.get("cost_mismatches", [])),
            "job_dir": str(job_dir),
        }
    failure_seal = _load_failure_seal(
        job_dir,
        pattern_id=pattern_id,
        config_name=config_name,
    )
    error_path = job_dir / "error.json"
    if failure_seal is not None or error_path.is_file():
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
            "failure_sealed": failure_seal is not None,
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
    """Collect validated completion and terminal-failure artifacts."""

    manifest_path, manifest = load_xred_run_manifest(manifest_path)
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
        "terminal_error_jobs": sum(row.get("failure_sealed") is True for row in records),
        "total_jobs": len(records),
        "records": records,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_root / "summary.json", summary)
    return summary


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
    """Retry each incomplete job once in a fresh Python process."""

    if branch_budget <= 0:
        raise ValueError("branch_budget must be positive")
    manifest_path, manifest = load_xred_run_manifest(manifest_path)
    selected_configs = configs or default_trace_configs()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    executable = python_executable or Path(sys.executable)
    worker_log = output_root / "isolated_workers.jsonl"
    for config in selected_configs:
        for pattern in manifest["patterns"]:
            pattern_id = str(pattern["pattern_id"])
            job_dir = output_root / config.name / pattern_id
            if (
                load_sealed_xred_completion(
                    job_dir,
                    pattern_id=pattern_id,
                    config_name=config.name,
                )
                is not None
                or _load_failure_seal(
                    job_dir,
                    pattern_id=pattern_id,
                    config_name=config.name,
                )
                is not None
            ):
                continue
            if _isolated_attempt_count(job_dir) >= 1 and (job_dir / "error.json").is_file():
                _seal_failed_retry(
                    job_dir,
                    pattern_id=pattern_id,
                    config_name=config.name,
                )
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
            _append_jsonl(
                job_dir / "isolated_attempts.jsonl",
                {
                    "attempt": _isolated_attempt_count(job_dir) + 1,
                    "returncode": completed.returncode,
                    "completed_unix": time.time(),
                },
            )
            _append_jsonl(
                worker_log,
                {
                    "pattern_id": pattern_id,
                    "config": config.name,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
            )
            completion = load_sealed_xred_completion(
                job_dir,
                pattern_id=pattern_id,
                config_name=config.name,
            )
            if completion is None:
                error_path = job_dir / "error.json"
                if not error_path.is_file():
                    _atomic_write_json(
                        error_path,
                        {
                            "pattern_id": pattern_id,
                            "config": config.name,
                            "status": "error",
                            "error_type": "WorkerArtifactError",
                            "error": completed.stderr or completed.stdout,
                            "job_dir": str(job_dir),
                        },
                    )
                _seal_failed_retry(
                    job_dir,
                    pattern_id=pattern_id,
                    config_name=config.name,
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
                        "terminal_error_jobs": summary["terminal_error_jobs"],
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
    "collect_xred_trace_summary",
    "run_xred_trace_isolated",
)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dara_cost_aware.xred_dispatch import run_xred_trace_isolated
from dara_cost_aware.xred_trace import (
    TraceConfig,
    build_xred_cohort,
    default_trace_configs,
    run_xred_trace,
)


def _write_profile(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("angle,intensity\n10,100\n11,200\n12,150\n", encoding="utf-8")


def _write_sealed_completion(
    job_dir: Path,
    *,
    pattern_id: str,
    config_name: str,
    branch_calls: int,
    singleton_calls: int,
) -> None:
    import hashlib

    job_dir.mkdir(parents=True, exist_ok=True)
    tree_path = job_dir / "tree.pickle"
    tree_path.write_bytes(b"test tree checkpoint")
    tree_sha256 = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    (job_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "tree_pickle": str(tree_path),
                "tree_pickle_sha256": tree_sha256,
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "complete.json").write_text(
        json.dumps(
            {
                "pattern_id": pattern_id,
                "config": {"name": config_name},
                "status": "complete",
                "branch_calls": branch_calls,
                "singleton_calls": singleton_calls,
                "stop_reason": "frontier_exhausted",
                "cost_mismatches": [],
                "events_path": str(job_dir / "events.jsonl"),
                "events_sha256": None,
            }
        ),
        encoding="utf-8",
    )


def test_build_xred_cohort_deduplicates_cifs_and_isolates_labels(tmp_path: Path) -> None:
    source = tmp_path / "xred"
    mono = source / "monophase" / "sample-a"
    bi = source / "biphase" / "sample-b"
    _write_profile(mono / "data.csv")
    _write_profile(bi / "intensity.csv")
    (mono / "alpha.cif").write_text("same cif\n", encoding="utf-8")
    (bi / "alpha-copy.cif").write_text("same cif\n", encoding="utf-8")
    (bi / "beta.cif").write_text("different cif\n", encoding="utf-8")

    manifest_path, labels_path = build_xred_cohort(source, tmp_path / "prepared")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))

    assert manifest["phase_bank_size"] == 2
    assert len(manifest["patterns"]) == 2
    assert all(Path(path).is_file() for path in manifest["phase_bank"])
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert [row["pattern_id"] for row in manifest["patterns"]] == [
        "xred-p0001",
        "xred-p0002",
    ]
    assert all(set(row) == {"pattern_id", "xy_path"} for row in manifest["patterns"])
    assert "biphase" not in manifest_text
    assert "monophase" not in manifest_text
    assert all("source_profile" in row and "kind" in row for row in labels["patterns"])
    assert "declared_phase_ids" not in manifest_path.read_text(encoding="utf-8")
    assert {row["label_status"] for row in labels["patterns"]} == {"complete"}
    assert len(labels["phases"]) == 2


def test_default_trace_configs_run_official_defaults_first() -> None:
    configs = default_trace_configs()

    assert [config.name for config in configs] == [
        "official",
        "no-express",
        "no-angular-cut",
        "no-express-no-angular-cut",
    ]
    assert configs[0].express_mode is True
    assert configs[0].enable_angular_cut is True
    assert configs[0].max_phases == 5


def test_live_runner_rejects_manifest_fields_outside_online_allowlist(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "phase_bank": [],
                "patterns": [
                    {
                        "pattern_id": "leak",
                        "xy_path": "pattern.xy",
                        "target_phases": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-online fields"):
        run_xred_trace(manifest, tmp_path / "run")
    with pytest.raises(ValueError, match="non-online fields"):
        run_xred_trace_isolated(
            manifest,
            tmp_path / "isolated-run",
            worker_script=tmp_path / "unused-worker.py",
        )


def test_isolated_runner_skips_sealed_job_and_completes_pending_jobs(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "benchmark_id": "isolation-test",
                "phase_bank": [],
                "patterns": [
                    {"pattern_id": "sealed", "xy_path": "sealed.xy"},
                    {"pattern_id": "retry-a", "xy_path": "retry-a.xy"},
                    {"pattern_id": "retry-b", "xy_path": "retry-b.xy"},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "run"
    sealed_job = output / "official" / "sealed"
    _write_sealed_completion(
        sealed_job,
        pattern_id="sealed",
        config_name="official",
        branch_calls=2,
        singleton_calls=3,
    )
    fake_worker = tmp_path / "fake_worker.py"
    fake_worker.write_text(
        """\
import argparse
import hashlib
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest")
parser.add_argument("--output-root", type=Path)
parser.add_argument("--branch-budget")
parser.add_argument("--worker-pattern-id")
parser.add_argument("--worker-config")
args = parser.parse_args()
with (args.output_root / "worker_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"pattern_id": args.worker_pattern_id, "pid": os.getpid()}) + "\\n")
job = args.output_root / args.worker_config / args.worker_pattern_id
job.mkdir(parents=True, exist_ok=True)
tree = job / "tree.pickle"
tree.write_bytes(b"worker tree")
checkpoint = {
    "status": "complete",
    "tree_pickle": str(tree),
    "tree_pickle_sha256": hashlib.sha256(tree.read_bytes()).hexdigest(),
}
(job / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
(job / "complete.json").write_text(json.dumps({
    "pattern_id": args.worker_pattern_id,
    "config": {"name": args.worker_config},
    "status": "complete",
    "branch_calls": 1,
    "singleton_calls": 3,
    "stop_reason": "frontier_exhausted",
    "cost_mismatches": [],
    "events_path": str(job / "events.jsonl"),
    "events_sha256": None,
}), encoding="utf-8")
""",
        encoding="utf-8",
    )

    summary = run_xred_trace_isolated(
        manifest,
        output,
        configs=(TraceConfig("official", True, True),),
        worker_script=fake_worker,
    )
    calls = [
        json.loads(line)
        for line in (output / "worker_calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert [call["pattern_id"] for call in calls] == ["retry-a", "retry-b"]
    assert len({call["pid"] for call in calls}) == 2
    assert summary["completed_jobs"] == 3
    assert summary["error_jobs"] == 0


def test_isolated_runner_seals_a_failed_retry_instead_of_repeating_it(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "benchmark_id": "failure-seal-test",
                "phase_bank": [],
                "patterns": [{"pattern_id": "repeat-failure", "xy_path": "pattern.xy"}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "run"
    fake_worker = tmp_path / "failing_worker.py"
    fake_worker.write_text(
        """\
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest")
parser.add_argument("--output-root", type=Path)
parser.add_argument("--branch-budget")
parser.add_argument("--worker-pattern-id")
parser.add_argument("--worker-config")
args = parser.parse_args()
args.output_root.mkdir(parents=True, exist_ok=True)
with (args.output_root / "failed_worker_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"pattern_id": args.worker_pattern_id}) + "\\n")
job = args.output_root / args.worker_config / args.worker_pattern_id
job.mkdir(parents=True, exist_ok=True)
(job / "error.json").write_text(json.dumps({
    "pattern_id": args.worker_pattern_id,
    "config": args.worker_config,
    "status": "error",
    "error_type": "DeterministicFailure",
    "error": "same failure",
    "job_dir": str(job),
}), encoding="utf-8")
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    config = (TraceConfig("official", True, True),)

    first = run_xred_trace_isolated(
        manifest,
        output,
        configs=config,
        worker_script=fake_worker,
    )
    second = run_xred_trace_isolated(
        manifest,
        output,
        configs=config,
        worker_script=fake_worker,
    )
    calls = (output / "failed_worker_calls.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(calls) == 1
    assert first["error_jobs"] == 1
    assert second["error_jobs"] == 1
    assert second["pending_jobs"] == 0

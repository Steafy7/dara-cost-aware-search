from __future__ import annotations

import json
from pathlib import Path

import pytest

from dara_cost_aware.xred_trace import (
    TraceConfig,
    build_xred_cohort,
    default_trace_configs,
    run_xred_trace,
    run_xred_trace_isolated,
)


def _write_profile(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("angle,intensity\n10,100\n11,200\n12,150\n", encoding="utf-8")


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


def test_live_runner_rejects_manifest_with_ground_truth(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "phase_bank": [],
                "patterns": [
                    {"pattern_id": "leak", "xy_path": "pattern.xy", "gt_phase_ids": []}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="offline label fields"):
        run_xred_trace(manifest, tmp_path / "run")


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
    sealed_job.mkdir(parents=True)
    (sealed_job / "complete.json").write_text(
        json.dumps(
            {
                "pattern_id": "sealed",
                "branch_calls": 2,
                "singleton_calls": 3,
                "stop_reason": "frontier_exhausted",
                "cost_mismatches": [],
            }
        ),
        encoding="utf-8",
    )
    fake_worker = tmp_path / "fake_worker.py"
    fake_worker.write_text(
        """\
import argparse
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
(job / "complete.json").write_text(json.dumps({
    "pattern_id": args.worker_pattern_id,
    "branch_calls": 1,
    "singleton_calls": 3,
    "stop_reason": "frontier_exhausted",
    "cost_mismatches": [],
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

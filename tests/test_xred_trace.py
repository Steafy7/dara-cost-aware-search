from __future__ import annotations

import json
from pathlib import Path

import pytest

from dara_cost_aware.xred_trace import (
    build_xred_cohort,
    default_trace_configs,
    run_xred_trace,
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

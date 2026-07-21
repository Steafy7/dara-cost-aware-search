import json
from pathlib import Path

import pytest

from dara_cost_aware.artifacts import ArtifactValidationError, canonical_json_bytes
from dara_cost_aware.refinement_store import (
    CacheStatus,
    StrictRefinementIdentity,
    StrictRefinementStore,
)


def identity(
    *,
    ordered_inputs: tuple[str, ...] = ("sha256:pattern", "sha256:phase-a", "sha256:phase-b"),
    bgmn: str = "sha256:bgmn-v1",
) -> StrictRefinementIdentity:
    return StrictRefinementIdentity.create(
        pattern_digest="sha256:pattern",
        ordered_input_digests=ordered_inputs,
        scientific_parameters=(("wavelength", "1.5406"),),
        provenance=(
            ("bgmn", bgmn),
            ("dara", "9473ee240daac0491fbe4294948aedd19555d6ec"),
            ("instrument", "sha256:instrument-v1"),
        ),
    )


def test_strict_cache_probe_validates_order_provenance_and_result_bytes(tmp_path: Path) -> None:
    store = StrictRefinementStore(tmp_path / "cache")
    request = identity()

    initial = store.cost((request, request))
    assert initial.predicted_new_calls == 1
    assert initial.probes[0].status is CacheStatus.MISSING
    assert not (tmp_path / "cache").exists()

    store.publish_success(
        request,
        {"refinement": "synthetic-no-bgmn-result", "rwp": "12.5"},
        producing_attempt_id="attempt-0001",
    )

    assert store.cost((request, request)).predicted_new_calls == 0
    assert store.probe(request).status is CacheStatus.VALID_HIT
    assert (
        store.probe(
            identity(ordered_inputs=("sha256:pattern", "sha256:phase-b", "sha256:phase-a"))
        ).status
        is CacheStatus.MISSING
    )
    assert store.probe(identity(bgmn="sha256:bgmn-v2")).status is CacheStatus.MISSING

    result_path = next((tmp_path / "cache" / "results").glob("*.json"))
    result_path.write_text(json.dumps({"refinement": "altered", "rwp": "0"}))

    corrupted = store.probe(request)
    assert corrupted.status is CacheStatus.INVALID_RESULT
    assert corrupted.adds_debit


def test_strict_cache_mutated_metadata_is_an_explicit_miss(tmp_path: Path) -> None:
    store = StrictRefinementStore(tmp_path / "cache")
    request = identity()
    store.publish_success(
        request,
        {"refinement": "synthetic-no-bgmn-result", "rwp": "12.5"},
        producing_attempt_id="attempt-0001",
    )
    metadata_path = next((tmp_path / "cache" / "metadata").glob("*.json"))
    metadata = json.loads(metadata_path.read_bytes())
    metadata["validation_status"] = "altered"
    metadata_path.write_bytes(canonical_json_bytes(metadata))

    probe = store.probe(request)
    assert probe.status is CacheStatus.INVALID_METADATA
    assert probe.adds_debit


def test_strict_cache_rejects_gt_like_result_fields_before_publication(tmp_path: Path) -> None:
    store = StrictRefinementStore(tmp_path / "cache")

    with pytest.raises(ArtifactValidationError, match="GT-like"):
        store.publish_success(
            identity(),
            {"nested": {"oracleScore": "1"}},
            producing_attempt_id="attempt-0001",
        )

    assert not (tmp_path / "cache").exists()

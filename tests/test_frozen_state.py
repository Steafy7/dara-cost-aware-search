from __future__ import annotations

import json
from pathlib import Path

import pytest

from dara_cost_aware.artifacts import ArtifactRef, JsonValue, canonical_json_bytes, canonical_sha256
from dara_cost_aware.frozen_state import (
    FROZEN_STATE_CONTRACT_VERSION,
    PILOT_PATTERN_IDS,
    FrozenStateInput,
    FrozenPilotManifest,
    FrozenStateRecord,
    FrozenStateValidationError,
    artifact_ref_from_file,
    audit_frozen_pilot_manifest,
    freeze_pilot,
)


def _artifact(root: Path, name: str, payload: bytes) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return artifact_ref_from_file("search-tree" if "tree" in name else "singleton-cache", path, root)


def _manifest(tmp_path: Path) -> FrozenPilotManifest:
    cohort: dict[str, JsonValue] = {
        "pattern_ids": list(PILOT_PATTERN_IDS),
        "selection_algorithm_version": "ticket-8-v1",
        "source_doi": "10.1021/acs.chemmater.5c02820.s002",
        "source_sha256": "sha256:" + "a" * 64,
        "source_url": "https://acs.figshare.com/articles/dataset/31164953",
    }
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_bytes(canonical_json_bytes(cohort))
    cohort_ref = artifact_ref_from_file("cohort-manifest", cohort_path, tmp_path)
    states = []
    for index, pattern_id in enumerate(PILOT_PATTERN_IDS):
        tree_ref = _artifact(tmp_path, f"states/{index}/tree.bin", f"tree-{index}".encode())
        cache_ref = _artifact(
            tmp_path,
            f"states/{index}/singleton-cache.bin",
            f"cache-{index}".encode(),
        )
        states.append(
            FrozenStateInput(
                pattern_id=pattern_id,
                tree_artifact=tree_ref,
                singleton_cache_artifact=cache_ref,
                frontier_fingerprint=f"sha256:{index:064x}",
                initialization_call_count=index + 1,
                branch_search_call_count=0,
                provenance=(
                    ("bgmn", "sha256:" + "b" * 64),
                    ("candidate_pool", "sha256:" + "c" * 64),
                    ("dara_source", "9473ee240daac0491fbe4294948aedd19555d6ec"),
                    ("instrument", "sha256:" + "i" * 64),
                    ("python", "3.12"),
                    ("runner_contract", FROZEN_STATE_CONTRACT_VERSION),
                    ("serializer", "pickle-protocol-5"),
                ),
            )
        )
    return freeze_pilot(cohort_ref, tuple(states))


def _as_input(state: FrozenStateRecord) -> FrozenStateInput:
    return FrozenStateInput(
        pattern_id=state.pattern_id,
        tree_artifact=state.tree_artifact,
        singleton_cache_artifact=state.singleton_cache_artifact,
        frontier_fingerprint=state.frontier_fingerprint,
        initialization_call_count=state.initialization_call_count,
        branch_search_call_count=state.branch_search_call_count,
        provenance=state.provenance,
    )


def test_freeze_pilot_requires_the_frozen_ticket8_order_and_derives_namespaces(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)

    assert tuple(state.pattern_id for state in manifest.states) == PILOT_PATTERN_IDS
    assert len({state.frozen_state_id for state in manifest.states}) == 10
    assert all(
        state.singleton_cache_namespace.startswith(
            "dara-cost-aware-rough-pilot-v1/"
        )
        for state in manifest.states
    )
    assert manifest.to_json()["schema_name"] == "frozen_pilot_manifest"

    path = tmp_path / "frozen-pilot.json"
    manifest.write(path)
    assert json.loads(path.read_text()) == manifest.to_json()


def test_audit_frozen_pilot_checks_artifacts_and_no_branch_search(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    path = tmp_path / "frozen-pilot.json"
    manifest.write(path)

    audit = audit_frozen_pilot_manifest(path, tmp_path)

    assert audit.pattern_ids == PILOT_PATTERN_IDS
    assert audit.checked_artifact_count == 21
    assert audit.branch_search_call_count == 0


def test_audit_rejects_tampered_external_artifact(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    path = tmp_path / "frozen-pilot.json"
    manifest.write(path)
    first_tree = tmp_path / "states/0/tree.bin"
    first_tree.write_bytes(b"tampered")

    with pytest.raises(FrozenStateValidationError, match="digest"):
        audit_frozen_pilot_manifest(path, tmp_path)


def test_freeze_pilot_rejects_branch_search_and_gt_like_provenance(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    first = manifest.states[0]
    with pytest.raises(FrozenStateValidationError, match="branch search"):
        freeze_pilot(
            manifest.cohort_manifest,
            (
                FrozenStateInput(
                    pattern_id=first.pattern_id,
                    tree_artifact=first.tree_artifact,
                    singleton_cache_artifact=first.singleton_cache_artifact,
                    frontier_fingerprint=first.frontier_fingerprint,
                    initialization_call_count=1,
                    branch_search_call_count=1,
                    provenance=first.provenance,
                ),
                *(_as_input(item) for item in manifest.states[1:]),
            ),
        )

    with pytest.raises(FrozenStateValidationError, match="GT-like"):
        freeze_pilot(
            manifest.cohort_manifest,
            (
                FrozenStateInput(
                    pattern_id=first.pattern_id,
                    tree_artifact=first.tree_artifact,
                    singleton_cache_artifact=first.singleton_cache_artifact,
                    frontier_fingerprint=first.frontier_fingerprint,
                    initialization_call_count=1,
                    branch_search_call_count=0,
                    provenance=tuple(
                        sorted(first.provenance + (("ground_truth", "sha256:" + "d" * 64),))
                    ),
                ),
                *(_as_input(item) for item in manifest.states[1:]),
            ),
        )


def test_frozen_state_id_is_independent_of_host_path(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    state = manifest.states[0]
    identity = state.identity_json()

    assert state.frozen_state_id == canonical_sha256(identity)
    assert str(tmp_path) not in json.dumps(manifest.to_json(), sort_keys=True)

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    tree = relocated / "tree.bin"
    cache = relocated / "singleton-cache.bin"
    tree.write_bytes((tmp_path / "states/0/tree.bin").read_bytes())
    cache.write_bytes((tmp_path / "states/0/singleton-cache.bin").read_bytes())
    relocated_state = FrozenStateRecord.from_input(
        FrozenStateInput(
            pattern_id=state.pattern_id,
            tree_artifact=artifact_ref_from_file("search-tree", tree, relocated),
            singleton_cache_artifact=artifact_ref_from_file(
                "singleton-cache", cache, relocated
            ),
            frontier_fingerprint=state.frontier_fingerprint,
            initialization_call_count=state.initialization_call_count,
            branch_search_call_count=0,
            provenance=state.provenance,
        ),
        pilot_version=manifest.pilot_version,
    )
    assert relocated_state.frozen_state_id == state.frozen_state_id

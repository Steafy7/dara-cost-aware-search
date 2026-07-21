# Frozen post-initialization pilot setup

This is the approved setup contract for the ten-pattern rough pilot. It is a
serial preparation pass, not a branch-search run. The implementation records
only a portable manifest; the SearchTree, singleton results, and BGMN cache
remain external content-addressed artifacts.

## Setup sequence

For each pattern in the exact ticket-8 order:

1. Resolve the public pattern and candidate-pool files from the frozen cohort
   manifest. Resolve licensed ICSD/CIF, instrument, and BGMN inputs from the
   explicitly configured artifact root; never search arbitrary host paths.
2. Verify the pinned DARA source guard (`9473ee240daac0491fbe4294948aedd19555d6ec`),
   runner contract, Python/serializer, instrument, candidate pool, and BGMN
   digests before construction.
3. Run one DARA `SearchTree` initialization. Its singleton refinements are
   recorded and cached under the state-specific `.../<frozen_state_id>/singleton`
   namespace. Do not call `expand_node`, `expand_root`, or any branch scheduler.
4. Persist the revision-zero tree and singleton-cache inventory outside Git,
   compute their byte lengths and SHA-256 digests, and emit one
   `FrozenStateInput` observation with the initialization-call count and an
   explicit zero branch-search count.
5. Build the ten-record manifest with `freeze_pilot`, then run the audit below
   before cloning the state for any arm.

The state ID is derived from scientific inputs and provenance, not host paths,
wall-clock values, usernames, or process IDs. All four arms must reference the
same state ID and singleton namespace for a pattern; branch caches are fresh
arm-local namespaces derived from that state. Initialization cost is reported
separately and never charged to `{5, 10, 15, 20}`.

## Audit command

```bash
uv run dara-cost-aware-audit-frozen-pilot \
  --manifest /external/pilot/frozen-pilot.json \
  --artifact-root /external/pilot
```

A valid audit checks the canonical cohort record, all 20 state/cache objects,
state identity and namespace derivation, provenance, revision zero, and a
zero branch-search count. Any missing, non-canonical, tampered, stale,
GT-like, or path-dependent artifact fails closed. The command does not import
GT and does not invoke DARA or BGMN.

No live setup is committed to this repository: the current development machine
does not contain the licensed ICSD root, benchmark profile files, or BGMN
executable. Therefore no scientific state or pilot result is fabricated by the
no-BGMN test suite.

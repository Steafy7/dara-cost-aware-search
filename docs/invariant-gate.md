# Ticket-14 no-BGMN invariant gate

This branch implements the deterministic gate required before the frozen
ten-pattern rough pilot may run. It is an engineering and provenance check,
not a pilot result or a claim about real DARA/BGMN behavior.

## Verified seams

- `artifacts` atomically publishes canonical manifests, a hash-chained event
  ledger, content-addressed checkpoints/results, terminal output, and a
  portable online seal.
- `refinement_store` derives ordered strict execution keys, probes without
  mutation, validates complete provenance and readable result bytes, and
  deduplicates exact new-call cost by strict key.
- `verification` runs all four schedulers through one deterministic no-BGMN
  harness, records every decision before selection, debits attempts before
  dispatch, persists resumable checkpoints, and fails closed after an
  unfinished dispatch.
- `replay` replays policy, budget, cache, checkpoint, cohort, incumbent,
  terminal, clean-resume, and exhaustive reachable-set evidence without
  dispatching refinements.
- `offline_evaluation` is imported separately, validates the online seal before
  reading GT, and cannot mutate online artifacts.

The tests cover atomic cohort deferral, exact zero/one debit, duplicate keys,
cache corruption and provenance/order mismatches, all deterministic arm
rules, event and artifact tampering, byte-identical clean resume,
indeterminate and failed attempts, pending-incumbent abstention, every shared
stop reason, path-independent identity, GT-like online inputs, post-seal exact
set evaluation, and exhaustive all-arm action/cache-miss equivalence.

## Run the gate

```bash
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
```

## Deliberate remaining boundary

This gate uses deterministic synthetic scientific and refinement adapters. It
does not bind DARA's live tree, execute BGMN, create the ten frozen
post-initialization states, read licensed phase data, or run any pilot pattern.
Those actions remain gated by their separate Wayfinder tickets. A real pilot
adapter must preserve these interfaces and pass the same tests; these fixtures
do not prove dynamic DARA/BGMN equivalence by themselves.

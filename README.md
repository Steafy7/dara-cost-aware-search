# DARA Cost-Aware Search

Reproducible experiments for exact-immediate-cost anytime scheduling over
DARA's unchanged full-sibling parent-expansion operator.

## Status

The `codex/h1-prototype` branch contains the accepted in-memory scheduling
tracer bullet and a deterministic no-BGMN invariant gate for exact debit,
strict cache validation, atomic persistence, replay/resume, completion
equivalence, and post-seal GT evaluation. See
[the invariant-gate scope](docs/invariant-gate.md).

Real DARA binding, frozen pilot checkpoints, and live BGMN execution have not
started. The ten-pattern pilot has not been run.

The development pilot will compare four scheduling arms from identical frozen
post-initialization states:

- deterministic sequential Original DARA;
- previous SLAI-DARA V3 scheduling;
- H1-DARA exact benefit/cost scheduling; and
- H1-V3 exact benefit/cost scheduling with the same frozen V3 evidence.

The primary comparison is H1-DARA versus Original DARA at per-pattern
branch-call budgets 5, 10, 15, and 20 on a frozen ten-pattern development
cohort. This is a rough feasibility pilot, not a full DARA40 evaluation.

## Scientific boundary

- Original DARA v1.1.12 is imported at exact commit
  `9473ee240daac0491fbe4294948aedd19555d6ec`; it is not forked or vendored.
- DARA retains phase generation, refinement, grouping, acceptance, and
  scientific stopping semantics.
- The experimental code may change only parent-expansion ordering, exact
  branch-call accounting, persistence, trace/replay, and offline evaluation.
- Ground truth is forbidden from the online scheduler and is joined only by a
  separate evaluator after an online run is sealed.
- BGMN binaries, model checkpoints, large frozen states, caches, and live
  results are external content-addressed artifacts and are never committed.

The governing research and Wayfinder decisions remain in
[SLAI_DARA](https://github.com/yimeng403-del/SLAI_DARA).

## Development

The locked environment is managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --frozen
uv run pytest
uv run dara-cost-aware-prototype
```

code-review-graph is a pinned, local development aid for caller, blast-radius,
and affected-test discovery. It is not a runtime dependency, provenance
system, or correctness oracle. See
[the local workflow](docs/development/code-review-graph.md).

# Architecture boundary

This repository implements the architecture accepted in the SLAI-DARA
Wayfinder ticket
[Decide the separate implementation repository architecture](https://github.com/yimeng403-del/SLAI_DARA/issues/11).

The intended deep modules are:

- a guarded DARA scientific-kernel adapter;
- a pure scheduling module with four strategy adapters;
- frozen DARA-native and V3 benefit-evidence adapters;
- a strict refinement store that owns cache validation and attempted-call
  debit;
- one shared four-arm harness;
- content-addressed artifacts and deterministic replay; and
- a process-separated offline ground-truth evaluator.

The `codex/h1-prototype` branch now implements the pure scheduling tracer
bullet plus no-BGMN `artifacts`, `refinement_store`, `verification`,
`replay`, and `offline_evaluation` modules. Their interfaces are exercised
through deterministic contract fixtures. The guarded real-DARA kernel adapter,
frozen pilot states, and live BGMN dispatcher remain deliberately deferred.

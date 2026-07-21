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

There is no scheduler implementation in this foundation commit. Interfaces and
behavior are introduced test-first in the prototype ticket.

# code-review-graph workflow

The repository pins code-review-graph 2.3.6 as a development tool, separate
from the Python runtime environment.

## Setup

```bash
uv tool install code-review-graph==2.3.6
code-review-graph install --platform codex
code-review-graph build
code-review-graph status
```

The local `.code-review-graph/` database and generated exports are ignored.
Inspect any installer-generated rules or configuration before committing.

## Required use

- Query callers before changing a module interface.
- Inspect blast radius and affected tests for each implementation change.
- Run graph-aware delta review before merging.
- Verify graph suggestions against direct source inspection and tests.

## Prohibited use

- Do not use graph output as scheduler evidence, an ablation metric, scientific
  provenance, or a correctness oracle.
- Do not rely on it to prove dynamic DARA behavior, GT isolation, or cache
  identity.
- Do not publish unsanitized graph exports; they may contain absolute paths and
  structural metadata.

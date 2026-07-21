# Live XRED-10 adapter smoke run

The guarded adapter was run against the public XRED-10 fallback manifest at
branch budgets 5, 10, 15, and 20. Each case was initialized once, cloned for
Original DARA and H1-DARA, and evaluated from the same revision-zero tree.

| Arm | B=5 | B=10 | B=15 | B=20 | branch calls (B=20) |
|---|---:|---:|---:|---:|---:|
| Original DARA | 6/10 | 6/10 | 6/10 | 6/10 | 19 |
| H1-DARA | 6/10 | 6/10 | 6/10 | 6/10 | 19 |

At B=5, Original used 17 branch calls and H1 used 18. At B=10/15/20 both
used 19. All 80 arm/budget runs had zero predicted-versus-actual cost
mismatches. H1 therefore does not beat Original DARA on this operational cohort.

This is the scoped public benchmark for this effort. Licensed DARA40 artifacts
are out of scope, and the previous-V3/H1-V3 arms are not yet wired into the
live runner.

Example:

```bash
uv run python scripts/run_live_comparison.py \
  --manifest /external/xred-10/manifest.json \
  --output /external/xred-10/live-comparison.json
```

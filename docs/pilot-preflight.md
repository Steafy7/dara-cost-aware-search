# Ten-pattern pilot preflight

## Result

The ticket-15 pilot was **not run**. The preflight stopped before any DARA
SearchTree initialization, BGMN dispatch, branch refinement, cache mutation,
or ground-truth access. No budget-success curve or per-pattern outcome is
claimed.

## Checks

| Requirement | Observation | Status |
|---|---|---|
| Pinned DARA package | `dara-xrd` 1.1.12 imports from the locked `.venv` | pass |
| Official ten-pattern profiles | The public `CederGroupHub/dara` checkout is present at source commit `5c5a252a0c7dd18ef3704407a28895ee8d11665a`; its `dataset/precursor_mixture` directory contains all 40 official scans, including the ten frozen ticket-8 IDs | pass (external source) |
| Licensed ICSD/CIF phase bank | No licensed phase-bank root or CIF files are available | missing |
| BGMN executable/runtime | No `bgmn` executable or BGMN runtime is available | missing |
| Frozen state | No audited `frozen-pilot.json` or 20 external SearchTree/singleton-cache objects are available | missing |
| Live runner | The branch contains the no-BGMN invariant harness and frozen-state audit, but no real-DARA four-arm pilot runner | missing |

The public dataset is the measured-pattern input only. The planning
repository's existing benchmark JSON files do not substitute for licensed
structures or a runnable refinement environment. The approved contract
requires the actual complete sibling operator, exact strict-cache accounting,
serial per-pattern execution, and sealed online traces; synthetic or COD
substitutions would change the scoped experiment.

## Provisioning checklist

Before rerunning the pilot, make the ten frozen official XRDML files available
from the pinned public checkout (or an external mirror) and record their
content digests. Also provision all of the following outside Git: the approved
licensed ICSD/CIF phase bank and candidate pools, the pinned DARA source guard,
the instrument `.geq`/`.ger` files, a working BGMN executable/runtime, and the
revision-zero frozen-state manifest plus its 20 audited external artifacts.
Then run `dara-cost-aware-audit-frozen-pilot` and require a zero
`branch_search_call_count` before any arm starts.

This is an environment-readiness result only. It is not a scheduler result,
accuracy result, or continuation-gate pass.

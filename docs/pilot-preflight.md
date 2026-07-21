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
| Official ten-pattern measured inputs | The public `CederGroupHub/dara` checkout is present at source commit `5c5a252a0c7dd18ef3704407a28895ee8d11665a`; its `dataset/precursor_mixture` directory contains all 40 official scans, including the ten frozen ticket-8 IDs | pass (external source) |
| Licensed ICSD/CIF phase bank | No licensed phase-bank root or CIF files are available | missing |
| BGMN executable/runtime | Official DARA bundle provides BGMN 4.2.23; executable SHA-256 `10cc9c1de0cca0eb6e12ff20abd44e959489c7d87082463639cfd297cfe9d23f` | pass (external runtime) |
| Instrument templates | Official AERIS `.geq`/`.ger` templates are available; SHA-256 values are recorded in the handoff | pass (template) |
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
content digests. The official BGMN 4.2.23 bundle and AERIS templates are now
available locally, but the approved licensed ICSD/CIF phase bank and candidate
pools, pinned DARA source guard, access-controlled artifact root, and
revision-zero frozen-state manifest plus its 20 audited external artifacts
remain to be provisioned.
Then run `dara-cost-aware-audit-frozen-pilot` and require a zero
`branch_search_call_count` before any arm starts.

This is an environment-readiness result only. It is not a scheduler result,
accuracy result, or continuation-gate pass.

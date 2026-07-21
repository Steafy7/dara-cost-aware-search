# Frozen-pilot provisioning handoff

Issue [#18](https://github.com/yimeng403-del/SLAI_DARA/issues/18) supplies
external inputs for the ten-pattern rough pilot. It does not select patterns,
access ground truth online, run branch search, or report pilot outcomes.

## Current host audit

The implementation environment has the pinned `dara-xrd` 1.1.12 package, the
frozen-state audit code, and a detached checkout of the public
`CederGroupHub/dara` source at commit
`5c5a252a0c7dd18ef3704407a28895ee8d11665a`. Its
`dataset/precursor_mixture` directory contains the 40 official measured scans,
including the ten IDs frozen by ticket #8. It does not have an approved pilot
artifact root. The official DARA BGMN 4.2.23 executable is available locally
with SHA-256 `10cc9c1de0cca0eb6e12ff20abd44e959489c7d87082463639cfd297cfe9d23f`;
the AERIS templates are also present (`.geq` SHA-256
`71d53c448ec0bce16bb6c729409127f437f230cd4a9f18919f73c05da28a3808`, `.ger`
SHA-256 `659e3a690a1b8b270eea7d69f423506c18318ab928c43fb010dff8f9a70a3a3b`).
The approved ICSD/CIF phase bank and candidate pools, access-controlled
artifact root, and frozen states remain absent.

The pinned public measured-input digests are:

| Frozen ID | SHA-256 |
|---|---|
| `In2O3_0p5002-MnCO3_0p5000--2min` | `8977f00fe2eaf56b2aec590a0b427d99b133170961f72620c12374bc7a79e352` |
| `Li2CO3_0p9001-ZrO2_0p1000--2min` | `f45868fe5c1948772d7906d49b910989b819fc31abe56b39318b65b31b3952f7` |
| `NiO_0p6007-Bi2O3_0p4008--2min` | `e5e4255a79e6d24c5f9bd08a21528faad0f53483c48539bbfaaba291e5848ed1` |
| `TiO2_0p4009-ZnO_0p6010--8min` | `09d86c479feccffbf96850df35ffacfb29a6190f9ae97ff69f8b339cc6f905d1` |
| `V2O5_0p3015-TiO2_0p6998--8min` | `a520cd156fa3848bafe8ee19ad29d20b85f9fe0fdd0549544ef972ad8f423190` |
| `In2O3_0p3022-La(OH)3_0p5014-TiO2_0p2004--2min` | `f517dc48381d081901d9b0f46fa692ae4a9b8ecfc2724fe9d0a64a8b81a5f931` |
| `NiO_0p1031-La(OH)3_0p2025-TiO2_0p7014--2min` | `b03852b1ca71e1f4be4cff89193d29361c26a4b09e85ea115459d97c81ab3594` |
| `NiO_0p2030-Li2CO3_0p2999-TiO2_0p5025--8min` | `df03a24b50bde1bef1da1db76cfc20c94d67b4eab3a69ec9cb22fea4250339fa` |
| `NiO_0p3035-Bi2O3_0p3016-Li2CO3_0p3997--8min` | `3bab84c9a0f3b6a293340ff1178e93a680931d1a253c2ad775218c0211a05008` |
| `NiO_0p7028-La(OH)3_0p1010-ZnO_0p2000--8min` | `7b211df7703f3471933ec1ce5e5c3dab097d26cf0077883c78431e97c8617b73` |

The documented roots
`/data/202500025/HU/YVONNE/SLAI_DARA_data` and
`/data/202500025/HU/SLAI_DARA_bundle` are absent. The adjacent public DARA
vendor checkout therefore supplies the measured-pattern inputs only; it is not
a substitute for the licensed ICSD/CIF bank, instrument inputs, frozen
states, or a working BGMN executable.

No scientific files were copied into this repository and no synthetic or COD
structures were substituted.

## Required external root

The operator must provide one access-controlled directory outside Git. Its
layout may vary, but the following evidence must be addressable from that
root:

```text
<artifact-root>/
  profiles/                     # ten frozen official XRDML files or a pinned mirror
  phase-bank/                   # licensed ICSD/CIF files and candidate pools
  instrument/                   # the selected .geq/.ger files
  runtime/                      # the pinned BGMN executable/build (local bundle now present)
  frozen/frozen-pilot.json      # revision-zero ten-record manifest
  frozen/objects/                # 20 SearchTree/singleton-cache objects
```

For every file or executable, record the source URL/DOI or license
provenance, byte length, and SHA-256 in the external handoff record. Keep that
record beside the artifacts; do not commit the artifacts or machine-specific
paths here.

## Handoff procedure

1. Supply the artifact-root path and access procedure to the operator running
   the pilot. Confirm that the root contains all ten official profiles, the
   licensed phase bank and candidate pools, instrument files, and BGMN build.
2. Verify the DARA source guard
   `9473ee240daac0491fbe4294948aedd19555d6ec` and record the runtime and
   serializer versions.
3. Run one serial `SearchTree` initialization per frozen pattern only. Persist
   revision-zero SearchTree and singleton-cache objects under the root; do not
   call `expand_node`, `expand_root`, or any branch scheduler during setup.
4. Build and audit the manifest before any arm starts:

   ```bash
   uv run dara-cost-aware-audit-frozen-pilot \
     --manifest <artifact-root>/frozen/frozen-pilot.json \
     --artifact-root <artifact-root>
   ```

   The audit must pass with exactly ten records, twenty external state/cache
   objects, revision zero for every state, and a zero branch-search call
   count. A missing, stale, non-canonical, tampered, path-dependent, or
   ground-truth-like object fails closed.
5. After the audit passes, attach the external manifest and digest record to
   the provisioning ticket. Only then is the frozen pilot eligible to run.

Until those inputs and access details are supplied, the pilot must remain
unrun. This handoff is an environment-readiness artifact, not a pilot result.

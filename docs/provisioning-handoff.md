# Frozen-pilot provisioning handoff

Issue [#18](https://github.com/yimeng403-del/SLAI_DARA/issues/18) supplies
external inputs for the ten-pattern rough pilot. It does not select patterns,
access ground truth online, run branch search, or report pilot outcomes.

## Current host audit

The implementation environment has the pinned `dara-xrd` 1.1.12 package and
the frozen-state audit code, but it does not have an approved pilot artifact
root or BGMN runtime. The documented roots
`/data/202500025/HU/YVONNE/SLAI_DARA_data` and
`/data/202500025/HU/SLAI_DARA_bundle` are absent. The adjacent public DARA
vendor checkout contains example and precursor-mixture files, but it is not a
substitute for the ten approved precursor profiles, licensed ICSD/CIF bank,
instrument inputs, or a working BGMN executable.

No scientific files were copied into this repository and no synthetic or COD
structures were substituted.

## Required external root

The operator must provide one access-controlled directory outside Git. Its
layout may vary, but the following evidence must be addressable from that
root:

```text
<artifact-root>/
  profiles/                     # ten official precursor profile files
  phase-bank/                   # licensed ICSD/CIF files and candidate pools
  instrument/                   # the selected .geq/.ger files
  runtime/                      # the pinned BGMN executable/build
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

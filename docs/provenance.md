# Provenance foundation

## Original DARA

- Package: `dara-xrd`
- Version: `1.1.12`
- Repository: `https://github.com/CederGroupHub/dara`
- Commit: `9473ee240daac0491fbe4294948aedd19555d6ec`
- Integration: exact PEP 508 VCS dependency plus committed `uv.lock`

Source-signature guards for the audited internal seam are mandatory before any
scheduler execution. The declared files are recorded in
`provenance/dara-pin.json`.

## Artifact policy

The repository tracks code, schemas, configurations, compact manifests, and
tiny no-BGMN fixtures. Large scientific artifacts live in an explicit external
artifact root and are addressed by logical role, SHA-256 digest, and byte
length. Host paths are not scientific identity.

Online run artifacts contain no ground truth. Evaluation accepts GT only after
the online seal validates.

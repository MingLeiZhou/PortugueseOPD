# PT60-Candidate Clean-Room Archive Package Validation

Generated: 2026-07-15T15:06:58+00:00

Validation mode: `package_clean_room_tarball_extraction`

Archive: `data/releases/PT60-Candidate-v1.0.2.tar.gz`

Archive SHA-256: `328e64adcfc6c7210ed9558793a14fdb661e5e3bab741f1d275a89e5593d1447`

Status: `PASS`

## Results

- Manifest records: 67
- Files after extraction: 67
- Machine-readable CSV/JSON/GraphML paths: 58
- Data-dictionary documented paths: 58
- Data-dictionary field records: 1201

## Failure counts

- `missing_manifest_fields`: 0
- `missing_documentation`: 0
- `documented_missing`: 0
- `missing_required`: 0
- `manifest_hash_mismatches`: 0
- `checksum_mismatches`: 0
- `checksum_missing_paths`: 0
- `checksum_undocumented`: 0
- `checksum_extra`: 0
- `dictionary_missing_paths`: 0
- `dictionary_extra_paths`: 0
- `failed_headline_count_checks`: 0

## Scope

This is an archive-package clean-room validation. It proves that a fresh extraction of the downloadable tarball reconciles manifest records, checksums, schema coverage and frozen headline counts without relying on the development release directory.

It does not prove that future dynamic E-REDES API responses will be byte-identical to the recorded snapshot.

## Tagged rebuild result

Two detached clean worktrees of annotated tag `pt60-candidate-v1.0.2` at commit `93c861c0b3a40da7e51f2724b3deed2fcd9adae9` rebuilt the archive from the same frozen derived inputs. Both archives have SHA-256 `328e64adcfc6c7210ed9558793a14fdb661e5e3bab741f1d275a89e5593d1447`, matching the validated main-worktree archive. This establishes byte-identical tagged reconstruction for the deposited build inputs.

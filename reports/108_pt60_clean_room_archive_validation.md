# PT60-Candidate Clean-Room Archive Package Validation

Generated: 2026-07-15T15:01:38+00:00

Validation mode: `package_clean_room_tarball_extraction`

Archive: `data/releases/PT60-Candidate-v1.0.2.tar.gz`

Archive SHA-256: `8bf6f70386d732bf0a275e4a6a84ab86d7cda31d0125a2ab7860873401a7b8d0`

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

It does not prove full source-to-archive regeneration from raw E-REDES/API downloads. That stronger validation requires a clean tagged checkout, frozen or re-downloadable source snapshots, and network/source availability.

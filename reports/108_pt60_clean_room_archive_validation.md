# PT60-Candidate Clean-Room Archive Package Validation

Generated: 2026-07-14T15:17:43+00:00

Validation mode: `package_clean_room_tarball_extraction`

Archive: `data/releases/PT60-Candidate-v1.0.0.tar.gz`

Archive SHA-256: `586cfec3f76440185e0d717ac1a4302393fe2888a15b9f057c7ee07b2ccf226b`

Status: `PASS`

## Results

- Manifest records: 63
- Files after extraction: 63
- Machine-readable CSV/JSON/GraphML paths: 54
- Data-dictionary documented paths: 54
- Data-dictionary field records: 1090

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

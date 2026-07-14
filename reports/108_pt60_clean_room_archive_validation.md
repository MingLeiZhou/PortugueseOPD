# PT60-Candidate Clean-Room Archive Package Validation

Generated: 2026-07-14T14:21:25+00:00

Validation mode: `package_clean_room_tarball_extraction`

Archive: `/Users/jumiray/Projects/PortugueseOPD/data/releases/PT60-Candidate-v1.0.0.tar.gz`

Archive SHA-256: `0ca66cd5b0ec7e1a6747a8cf20e79edd0497acb0e15b611a2dfa7d0198fe31e1`

Status: `PASS`

## Results

- Manifest records: 183
- Files after extraction: 183
- Machine-readable CSV/JSON/GraphML paths: 143
- Data-dictionary documented paths: 143
- Data-dictionary field records: 7601

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

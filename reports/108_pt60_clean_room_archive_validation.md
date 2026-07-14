# PT60-Candidate Clean-Room Archive Package Validation

Generated: 2026-07-14T16:18:48+00:00

Validation mode: `package_clean_room_tarball_extraction`

Archive: `data/releases/PT60-Candidate-v1.0.0.tar.gz`

Archive SHA-256: `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`

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

## Deterministic final-tag rebuild

A detached clean worktree of annotated tag `pt60-candidate-v1.0.0`, commit `1eb690302f35f7c0090c14c994d073539cbb5335`, was populated with the frozen derived core and validation input set. The release directory, schema package and tarball were rebuilt using the tagged scripts.

- Main-worktree archive SHA-256: `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`
- Clean-tag archive SHA-256: `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`
- Byte-for-byte archive match: `PASS`
- Clean-tag package validation: `PASS`

The builder fixes release-generated timestamps and normalizes gzip/tar ownership and time metadata. This establishes deterministic packaging from the frozen derived inputs.

## Raw-source reacquisition limitation

The first clean-tag build without staged derived inputs failed closed because ignored `data/processed/` artifacts were absent. Live reacquisition was then tested for the three topology-critical source identifiers. The recorded Opendatasoft v2.1 export URLs for `rede-at-teste`, `se-at_2025` and `pc-at_2025` returned HTTP 404 during this audit. Raw source snapshots and their hashes were not frozen in Git.

Therefore this validation does not establish raw/API-to-release reproducibility. Before submission, either deposit licensed frozen topology-critical input snapshots with checksums or state this limitation explicitly while treating the DOI archive as the preserved versioned derived dataset.

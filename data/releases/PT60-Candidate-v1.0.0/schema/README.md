# PT60-Candidate v1.0.0 Schema Package

Generated: 2026-07-14T13:20:19Z

This package documents public CSV, JSON and GraphML fields in the PT60-Candidate v1.0.0 release archive.

## Contents

- `data_dictionary.csv`: field-level dictionary covering 1090 fields/JSON pointers/GraphML attributes across 54 public machine-readable files.
- `file_schema_summary.csv`: file-level counts and schema coverage.
- `join_relationships.csv`: recommended joins among core topology, validation, provenance and archive-control files.
- `crs_and_geometry.md` and `crs_and_geometry.json`: CRS, geometry encoding, units and missing-value semantics.
- `json_schemas/`: machine-readable schemas for principal core, validation and provenance files.

## Coverage by file group

- `core_topology`: 323 fields
- `provenance`: 179 fields
- `release_control`: 93 fields
- `schema`: 139 fields
- `technical_validation`: 356 fields

## Interpretation limits

The schema package documents a candidate-topology dataset. It does not assert operator validation, branch precision/recall, operational grid-model readiness, AC power-flow readiness or OPF readiness.

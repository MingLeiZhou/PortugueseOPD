# 90 Stage-2 Dataset Release Note

Generated: 2026-07-13T11:46:17+00:00

Scope: build a stage-2 Portuguese benchmark-derived feature/target/provenance dataset layer for later SSL and risk-prediction preparation, without performing machine learning.

## Release identity

- dataset id: `pt_grid_benchmark_stage2`
- release id: `pt_grid_benchmark_stage2_v1`
- upstream stage-1 release id: `pt_grid_benchmark_stage1_v1`

## Included tables

| table | rows |
| --- | --- |
| pt_stage2_bus_features.csv | 54 |
| pt_stage2_line_features.csv | 36 |
| pt_stage2_generator_features.csv | 18 |
| pt_stage2_line_risk_targets.csv | 163 |
| pt_stage2_generator_risk_targets.csv | 94 |
| pt_stage2_provenance_flags.csv | 108 |


## What stage-2 adds

- static bus, line, and generator feature tables built from the validated stage-1 package
- scenario-conditioned line and generator target tables from existing S20/S21/S22/S30 diagnostics
- consolidated provenance/governance flags for diagnostic interpretation

## Boundary

- diagnostic-only
- not operator-grade
- no ML training or data splits
- no AC OPF outputs

# 88 Stage-1 Dataset Release Note

Generated: 2026-07-13T11:46:17+00:00

Scope: package the frozen Portuguese benchmark-candidate v1 machine-readable artifacts into one stage-1 dataset bundle for downstream SSL/risk data engineering, without adding machine-learning logic.

## Release identity

- dataset id: `pt_grid_benchmark_stage1`
- release id: `pt_grid_benchmark_stage1_v1`
- schema version: `1.0`
- ACPF freeze: `S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6`
- DC OPF freeze: `S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC`

## Included packaged tables

| table | rows |
| --- | --- |
| pt_dataset_buses.csv | 54 |
| pt_dataset_lines.csv | 36 |
| pt_dataset_loads.csv | 25 |
| pt_dataset_generators.csv | 18 |
| pt_dataset_generator_assignment.csv | 1426 |
| pt_dataset_generator_dispatch_proxy.csv | 1426 |
| pt_dataset_generator_costs.csv | 18 |
| pt_dataset_benchmark_summaries.csv | 5 |
| pt_dataset_line_policy.csv | 5 |


## Why this package exists

This release turns the frozen benchmark-candidate v1 artifacts into one reproducible dataset product. It is intended to support later graph/data-engineering and SSL/risk-preparation work, while keeping the current project’s diagnostic-only benchmark boundary explicit.

## Included governance context

- S16 backbone buses/lines/loads are the packaged PF structural core.
- S30 benchmark summaries provide the packaged DC OPF outcome layer.
- mixed-corridor policy is carried as an explicit line-policy table.
- generator candidate, assignment, proxy, and cost layers are retained as both compact benchmark-usable rows and broader provenance tables.

## Major limitations

- diagnostic-only release
- not operator-grade
- no AC OPF outputs included
- no ML labels or train/validation/test split logic included
- generator and import semantics remain proxy-governed rather than source-backed operator semantics

## Intended downstream use

- stable dataset packaging for graph/data engineering
- benchmark-derived substrate for later SSL/risk enrichment
- reproducible machine-readable release companion to the benchmark package documents

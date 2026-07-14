# 92 Stage-3 Graph Export Release Note

Generated: 2026-07-13T11:46:18+00:00

Scope: package the validated stage-2 Portuguese benchmark-derived feature/target layer into a graph export and graph-linked sample-index layer for later SSL and risk work, without doing machine learning.

## Release identity

- dataset id: `pt_grid_benchmark_stage3_graph`
- release id: `pt_grid_benchmark_stage3_graph_v1`
- upstream stage-2 release id: `pt_grid_benchmark_stage2_v1`

## Included outputs

| table | rows |
| --- | --- |
| pt_stage3_graph_nodes.csv | 54 |
| pt_stage3_graph_edges.csv | 36 |
| pt_stage3_generator_nodes.csv | 18 |
| pt_stage3_generator_bus_links.csv | 18 |
| pt_stage3_line_risk_samples.csv | 163 |
| pt_stage3_generator_risk_samples.csv | 94 |


## What stage-3 adds

- canonical graph node and edge exports
- homogeneous bus-line GraphML export
- generator sidecar node/link tables for later heterogeneous graph work
- graph-linked line and generator sample-index tables

## Boundary

- diagnostic-only
- not operator-grade
- no ML training or splits
- no framework-specific tensor export

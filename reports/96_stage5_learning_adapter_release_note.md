# 96 Stage-5 Learning Adapter Release Note

Generated: 2026-07-13T14:09:38+00:00

Scope: publish framework-neutral adapter tables and contracts on top of the validated stage-3 topology layer and stage-4 learning benchmark package, without model training or tensor export.

## Release identity

- dataset id: `pt_grid_benchmark_stage5_adapter`
- release id: `pt_grid_benchmark_stage5_adapter_v2`
- upstream stage-3 release id: `pt_grid_benchmark_stage3_graph_v1`
- upstream stage-4 release id: `pt_grid_benchmark_stage4_learning_v2`

## Included outputs

| table | rows |
| --- | --- |
| pt_stage5_graph_node_adapter.csv | 54 |
| pt_stage5_graph_edge_adapter.csv | 36 |
| pt_stage5_generator_node_adapter.csv | 18 |
| pt_stage5_generator_link_adapter.csv | 18 |
| pt_stage5_line_supervision_adapter.csv | 403 |
| pt_stage5_generator_supervision_adapter.csv | 268 |
| pt_stage5_feature_contract.csv | 272 |
| pt_stage5_adapter_registry.csv | 6 |


## Adapter views

| adapter_view | table_name | adapter_family | entity_scope | recommended_split_id |
| --- | --- | --- | --- | --- |
| homogeneous_bus_line_graph | pt_stage5_graph_node_adapter.csv | graph_structure | bus_node |  |
| homogeneous_bus_line_graph | pt_stage5_graph_edge_adapter.csv | graph_structure | line_edge |  |
| heterogeneous_generator_sidecar | pt_stage5_generator_node_adapter.csv | graph_structure | generator_node |  |
| heterogeneous_generator_sidecar | pt_stage5_generator_link_adapter.csv | graph_structure | generator_bus_link |  |
| line_risk_supervision | pt_stage5_line_supervision_adapter.csv | supervision | line_sample |  |
| generator_risk_supervision | pt_stage5_generator_supervision_adapter.csv | supervision | generator_sample |  |


## Contract guarantees

- every supervision row carries explicit recommended, grouped-challenge, and scenario-holdout partition references
- every exported adapter column is covered by the feature contract
- train-only preprocessing rules are published separately from learned artifacts
- governance-sensitive subsets remain tagged rather than silently removed

## Boundary

- diagnostic-only
- not operator-grade
- no model training
- no framework-specific tensor export
- no persisted fitted preprocessing state

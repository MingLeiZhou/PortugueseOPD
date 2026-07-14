# 94 Stage-4 Learning Benchmark Release Note

Generated: 2026-07-13T12:33:30+00:00

Scope: package the validated stage-3 graph/sample release into a learning-ready benchmark layer with explicit task definitions, split registries, leakage controls, and benchmark-core versus governance-sensitive challenge subsets, without model training.

## Release identity

- dataset id: `pt_grid_benchmark_stage4_learning`
- release id: `pt_grid_benchmark_stage4_learning_v2`
- upstream stage-3 release id: `pt_grid_benchmark_stage3_graph_v1`

## Included outputs

| table | rows |
| --- | --- |
| pt_stage4_bus_node_features.csv | 54 |
| pt_stage4_line_edge_features.csv | 36 |
| pt_stage4_generator_node_features.csv | 18 |
| pt_stage4_generator_bus_link_features.csv | 18 |
| pt_stage4_line_risk_benchmark_samples.csv | 163 |
| pt_stage4_generator_risk_benchmark_samples.csv | 94 |
| pt_stage4_task_registry.csv | 6 |
| pt_stage4_split_registry.csv | 8 |
| pt_stage4_line_sample_splits.csv | 566 |
| pt_stage4_generator_sample_splits.csv | 362 |
| pt_stage4_label_support_audit.csv | 14 |
| pt_stage4_entity_registry.csv | 126 |
| pt_stage4_sample_registry.csv | 257 |


## Official tasks

- primary: `line_loading_regression`, `generator_dispatch_regression`
- auxiliary limited-support: `line_relative_high_stress_classification`, `generator_relative_high_dispatch_classification`
- insufficient-support challenge/plumbing: `line_overload_binary_classification`, `generator_top_dispatch_classification`

## Split overview

| task_family | split_id | train_rows | validation_rows | test_rows |
| --- | --- | --- | --- | --- |
| line | line_balanced_recommended_v1 | 98 | 32 | 33 |
| line | line_grouped_entity_primary_v1 | 67 | 28 | 25 |
| line | line_grouped_entity_relative_stress_v1 | 67 | 28 | 25 |
| line | line_scenario_family_holdout_v1 | 120 | 3 | 40 |
| generator | generator_balanced_recommended_v1 | 56 | 20 | 18 |
| generator | generator_grouped_entity_primary_v1 | 40 | 27 | 20 |
| generator | generator_grouped_entity_relative_dispatch_v1 | 40 | 27 | 20 |
| generator | generator_scenario_family_holdout_v1 | 54 | 0 | 40 |


## Split policy

- primary regression and auxiliary classification evaluation uses strict entity-grouped splits.
- `line_balanced_recommended_v1` and `generator_balanced_recommended_v1` are retained only as backward-compatible plumbing splits.
- governance-sensitive rows remain explicitly tagged in all split outputs rather than silently removed from the release.

## Label support

| task_name | task_type | total_rows | total_entities | positive_rows | positive_entities | support_status |
| --- | --- | --- | --- | --- | --- | --- |
| line_overload_binary_classification | binary_classification | 120 | 32 | 0 | 0 | NO_POSITIVE_ENTITIES |
| line_loading_regression | regression | 120 | 32 |  |  | CONTINUOUS_TARGET_GROUPED_EVALUATION |
| line_relative_high_stress_classification | binary_classification | 120 | 32 | 36 | 7 | LIMITED_GROUPED_SUPPORT |
| generator_top_dispatch_classification | binary_classification | 87 | 17 | 2 | 1 | INSUFFICIENT_FOR_THREE_WAY_GROUPED |
| generator_dispatch_regression | regression | 87 | 17 |  |  | CONTINUOUS_TARGET_GROUPED_EVALUATION |
| generator_relative_high_dispatch_classification | binary_classification | 87 | 17 | 26 | 4 | LIMITED_GROUPED_SUPPORT |


## What stage-4 adds

- benchmark task registry
- leakage-safe split registries
- benchmark-core versus governance-sensitive challenge subset annotations
- explicit benchmark inclusion/exclusion registries

## Boundary

- diagnostic-only
- not operator-grade
- learning-ready for benchmark packaging, not model-training output
- no framework-specific tensor export
- no hidden train/test preprocessing state

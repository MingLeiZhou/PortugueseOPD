# QA checklist

## Scope

This checklist covers post-structure-change QA for the staged benchmark release, with emphasis on:
- stage-4 learning benchmark contracts;
- stage-5 framework-neutral adapter contracts;
- repository documentation and path consistency;
- release-boundary regressions.

It complements the fail-closed validators. It does not replace them.

## Preconditions

- Use the project reference environment from `README.md`.
- Install dependencies from `requirements.txt`.
- Ensure upstream processed artifacts exist through stage 5.
- Run from repository root.

## Core rebuild commands

```bash
python src/run_portuguese_stage4_learning_benchmark_pipeline.py
python src/run_portuguese_stage5_learning_adapter_pipeline.py
```

Expected result:
- both pipelines finish with `status: PASS`.

## Automated smoke-test commands

```bash
python src/qa_smoke_test_stage4_consumers.py
python src/qa_smoke_test_stage5_consumers.py
python src/run_stage4_leakage_safe_baselines.py
python src/build_topology_validation_sample.py
python src/summarize_topology_external_validation.py
python src/build_reproducibility_source_manifest.py
```

Expected result:
- both smoke tests finish with `status: PASS`;
- primary and auxiliary grouped splits have zero entity leakage;
- original rare-event labels remain explicitly marked as insufficient-support challenge tasks.
- baseline feature audit reports zero target-derived inputs and uses grouped entity splits;
- topology precision remains blocked unless independent evidence and minimum adjudication requirements pass;
- the source manifest records the E-REDES portal-level CC BY 4.0 basis for every topology-critical source, including a visible fallback where a dataset catalog record does not repeat the license field;
- any public core-topology release retains E-REDES attribution, the CC BY 4.0 link, source identifiers and access dates, and an indication of transformations.

## Repository structure review

Confirm these top-level paths still exist and match the docs:
- `src/`
- `data/`
- `docs/`
- `reports/`
- `figures/`
- `logs/`
- `README.md`
- `DATA_LICENSE.md`
- `CITATION.cff`

Confirm these docs exist and remain linked correctly:
- `docs/REPOSITORY_STRUCTURE.md`
- `docs/PROJECT_STATUS.md`
- `docs/QA_CHECKLIST.md`

## Stage-4 benchmark review

Confirm these artifacts exist:
- `data/processed/dataset_release_stage4/pt_stage4_task_registry.csv`
- `data/processed/dataset_release_stage4/pt_stage4_split_registry.csv`
- `data/processed/dataset_release_stage4/pt_stage4_line_risk_benchmark_samples.csv`
- `data/processed/dataset_release_stage4/pt_stage4_generator_risk_benchmark_samples.csv`
- `data/processed/dataset_release_stage4/pt_stage4_line_sample_splits.csv`
- `data/processed/dataset_release_stage4/pt_stage4_generator_sample_splits.csv`
- `data/processed/dataset_release_stage4/pt_stage4_label_support_audit.csv`
- `data/processed/dataset_release_stage4/pt_stage4_manifest.json`
- `data/processed/dataset_release_stage4/pt_stage4_validation_summary.json`
- `data/processed/dataset_release_stage4/pt_stage4_qa_smoke_summary.json`

Confirm benchmark expectations:
- primary regression split ids are `line_grouped_entity_primary_v1` and `generator_grouped_entity_primary_v1`;
- auxiliary classification split ids are `line_grouped_entity_relative_stress_v1` and `generator_grouped_entity_relative_dispatch_v1`;
- all four evaluation splits preserve strict entity grouping and cover `train`, `validation`, and `test`;
- auxiliary classification partitions have positive support and zero cross-partition entity leakage;
- `line_balanced_recommended_v1` and `generator_balanced_recommended_v1` are backward-compatible plumbing-only splits;
- original overload/top-dispatch tasks are not headline eligible;
- relative high-stress/high-dispatch labels are not described as operational overload or dispatch thresholds.

## Stage-5 adapter review

Confirm these artifacts exist:
- `data/processed/dataset_release_stage5/pt_stage5_graph_node_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_graph_edge_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_generator_node_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_generator_link_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_line_supervision_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_generator_supervision_adapter.csv`
- `data/processed/dataset_release_stage5/pt_stage5_feature_contract.csv`
- `data/processed/dataset_release_stage5/pt_stage5_adapter_registry.csv`
- `data/processed/dataset_release_stage5/pt_stage5_train_only_preprocessing_contract.json`
- `data/processed/dataset_release_stage5/pt_stage5_manifest.json`
- `data/processed/dataset_release_stage5/pt_stage5_validation_summary.json`
- `data/processed/dataset_release_stage5/pt_stage5_qa_smoke_summary.json`

Confirm adapter expectations:
- adapter views include:
  - `homogeneous_bus_line_graph`
  - `heterogeneous_generator_sidecar`
  - `line_risk_supervision`
  - `generator_risk_supervision`
- graph endpoints resolve cleanly to node ids;
- supervision tables expose valid task names and recommended partitions;
- task-specific recommended splits match the stage-4 task registry;
- primary/auxiliary supervision rows preserve entity-group separation;
- feature contract covers every exported adapter column;
- preprocessing contract enforces train-only fitting and forbidden input columns.

## Release-boundary review

Confirm these statements remain true:
- the core PT60-Candidate topology release is governed by E-REDES CC BY 4.0 attribution requirements;
- `publication_allowed` stays `false` for electrical and learning artifacts whose scenario/proxy semantics do not support publication claims;
- `diagnostic_only` stays `true` in electrical and learning release manifests;
- `operator_grade_ready` stays `false` in all readiness manifests;
- no document claims operator-grade realism, external topology validation, AC power-flow readiness, or OPF readiness;
- benchmark labels are still described as scenario-derived diagnostic targets;
- the MIT repository license is never presented as the license for E-REDES-derived data.

## Manual notes

Record any accepted warnings here:
- 
- 
- 

## Sign-off

- Reviewer:
- Date:
- Stage-4 smoke: PASS / FAIL
- Stage-5 smoke: PASS / FAIL
- Docs/path review: PASS / FAIL
- Release-boundary review: PASS / FAIL
- Final decision: PASS / FAIL

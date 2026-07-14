# PortugueseOPD

PortugueseOPD is a fail-closed research pipeline for reconstructing and packaging
PT60-Candidate, a provenance-tracked candidate dataset for an E-REDES Portuguese
60 kV topology layer.

The project does not claim to reproduce the real operational Portuguese grid. Its
purpose is to make topology reconstruction, data provenance, engineering assumptions,
and model-readiness limits explicit and reproducible.

## Current candidate dataset

The versioned PT60-Candidate v1.0.0 release contains:

- 484 candidate facilities and 358 retained inter-facility candidate branches;
- a complete 1,342-row retained/downgraded/rejected circuit ledger;
- the selected GraphML multigraph and a 216-configuration sensitivity sweep;
- provenance manifests, schemas, checksums, internal-validation results, deterministic
  negative controls, and public-source triangulation records.

The release is not operator validated, does not estimate topology precision or recall, and
lacks the source-backed electrical parameters and operating data needed for AC power flow
or OPF. Optional electrical and learning outputs in the development pipeline remain
diagnostic-only and are excluded from the main public dataset archive.

## Repository layout

- `src/`: acquisition, reconstruction, validation, benchmark, and dataset builders.
- `data/raw/`: downloaded inputs; generated locally and excluded from Git.
- `data/metadata/`: selected provenance and release-control metadata.
- `data/processed/`: generated candidate and release data; excluded from ordinary Git and
  intended for a separately versioned data archive.
- `data/releases/PT60-Candidate-v1.0.0/`: minimized public dataset release and schemas.
- `paper/`: Scientific Data manuscript source, generated figures, PDF, and editable DOCX.
- `docs/`: stable project, release, licensing, and cleanup documentation.
- `reports/`: selected validation and release audit reports.

See [repository structure](docs/REPOSITORY_STRUCTURE.md), [project status](docs/PROJECT_STATUS.md),
and [data licensing](DATA_LICENSE.md).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.12 is the current reference environment.

## Rebuild inputs and topology

```bash
python src/validate_api.py
python src/inspect_metadata.py
python src/download_datasets.py
python src/reconstruct_at_topology_paper_logic.py
```

Raw downloads are intentionally not committed. E-REDES states that its portal data are
available under CC BY 4.0 with publisher attribution; public derived releases must retain
that attribution, identify source datasets and dates, and indicate transformations.

## Rebuild packaged dataset stages

The stage builders are fail-closed and require their declared upstream artifacts.

```bash
python src/run_portuguese_stage1_dataset_pipeline.py
python src/run_portuguese_stage2_dataset_pipeline.py
python src/run_portuguese_stage3_graph_pipeline.py
python src/run_portuguese_stage4_learning_benchmark_pipeline.py
python src/run_portuguese_stage5_learning_adapter_pipeline.py
```

Stage 4 defines diagnostic benchmark tasks and splits. Stage 5 exports framework-neutral
adapter tables. Labels are scenario-derived diagnostic targets, not observed grid events.

Stage 4 v2 uses strict entity-grouped splits for the primary loading/dispatch regression
tasks. Relative high-stress and high-dispatch classifications are limited-support auxiliary
tasks. The original overload and top-dispatch classifications are retained only as
insufficient-support challenge/plumbing tasks; their row-balanced compatibility splits must
not be used to claim cross-entity generalization.

## QA smoke tests

After rebuilding stage 4 and stage 5, run:

```bash
python src/qa_smoke_test_stage4_consumers.py
python src/qa_smoke_test_stage5_consumers.py
```

These smoke tests verify that the current repository layout, stage-4 benchmark contracts,
and stage-5 adapter contracts remain usable for downstream consumers after structure or
documentation changes. The human review checklist lives at `docs/QA_CHECKLIST.md`.

## Validation and diagnostic experiments

```bash
python src/build_topology_validation_sample.py
python src/summarize_topology_external_validation.py
python src/localize_portuguese_acpf_nonconvergence.py
python src/run_stage4_leakage_safe_baselines.py
python src/build_reproducibility_source_manifest.py
```

The topology summarizer returns `NOT_EVALUABLE` until at least 50 sampled branches have
two independent, evidence-complete reviews. Baseline preprocessing is fitted on train rows
only and evaluation uses strict entity-grouped splits. ACPF ablation convergence remains a
diagnostic signal, not electrical validation.

## Release boundary

- Code may be used under the MIT license.
- E-REDES-derived data use the portal's CC BY 4.0 terms and required publisher attribution;
  the MIT code license does not replace those terms.
- The responsible-release boundary for PT60-Candidate v1.0.0 is recorded in
  `data/metadata/responsible_release_boundary.json` and
  `reports/107_pt60_responsible_release_boundary.md`.
- The core public archive may include exact derived candidate geometries, facility
  names/codes, OSM evidence URLs, provenance manifests and validation outputs with
  attribution and non-operational disclaimers.
- Raw E-REDES downloads are excluded from the default public archive unless final
  repository/license review explicitly approves raw snapshot deposition.
- The frozen dataset archive is deposited in Figshare under reserved DOI
  [`10.6084/m9.figshare.32984021`](https://doi.org/10.6084/m9.figshare.32984021);
  DOI resolution depends on publication of the Figshare item.
- AC OPF and real-system operating claims are outside the benchmark-v1 scope.

## Citation

Citation metadata are provided in `CITATION.cff`. Cite the software release together with
the versioned Figshare dataset when using released PT60-Candidate records.

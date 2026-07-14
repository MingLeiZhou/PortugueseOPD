# PortugueseOPD

PortugueseOPD is a fail-closed research pipeline for reconstructing and packaging
PT60-Candidate, a provenance-tracked candidate dataset for an E-REDES Portuguese
60 kV topology layer.

The project does not claim to reproduce the real operational Portuguese grid. Its
purpose is to make topology reconstruction, data provenance, engineering assumptions,
and model-readiness limits explicit and reproducible.

## Current candidate dataset

The current local build contains:

- a reconstructed topology candidate with 358 reliable inter-facility AT branches;
- an AC power-flow diagnostic backbone with 54 buses, 36 lines, and 25 loads;
- a diagnostic DC-OPF reference with proxy-governed generation and costs;
- five staged dataset packages ending in framework-neutral graph-learning adapters;
- leakage-safe diagnostic baselines for the two primary regressions and two limited-support auxiliary classifications;
- a 100-branch stratified external-review package whose precision estimate remains fail-closed until independent evidence is recorded;
- branch-, transformer-, and slack-depth ACPF failure-localization outputs.

All electrical and learning outputs remain diagnostic-only. They are not operator-grade,
not a source-backed model of the Portuguese system, and not suitable for operational
decisions.

## Repository layout

- `src/`: acquisition, reconstruction, validation, benchmark, and dataset builders.
- `data/raw/`: downloaded inputs; generated locally and excluded from Git.
- `data/metadata/`: generated source metadata; excluded from Git.
- `data/processed/`: generated candidate and release data; excluded from ordinary Git and
  intended for a separately versioned data archive.
- `docs/`: stable project, release, licensing, and cleanup documentation.
- `reports/`, `figures/`, `logs/`: generated outputs; excluded from Git.

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
- No versioned PT60-Candidate data archive or DOI has been published yet.
- AC OPF and real-system operating claims are outside the benchmark-v1 scope.

## Citation

Citation metadata are provided in `CITATION.cff`. Update the author and repository fields
before creating a public release or DOI.

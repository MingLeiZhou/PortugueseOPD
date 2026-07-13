# PortugueseOPD

PortugueseOPD is a fail-closed research pipeline for reconstructing and packaging a
diagnostic Portuguese 60 kV grid benchmark from E-REDES open data.

The project does not claim to reproduce the real operational Portuguese grid. Its
purpose is to make topology reconstruction, data provenance, engineering assumptions,
and model-readiness limits explicit and reproducible.

## Current release

The frozen benchmark candidate contains:

- a reconstructed topology candidate with 358 reliable inter-facility AT branches;
- an AC power-flow diagnostic backbone with 54 buses, 36 lines, and 25 loads;
- a diagnostic DC-OPF reference with proxy-governed generation and costs;
- five staged dataset packages ending in framework-neutral graph-learning adapters.

All electrical and learning outputs remain diagnostic-only. They are not operator-grade,
not a source-backed model of the Portuguese system, and not suitable for operational
decisions.

## Repository layout

- `src/`: acquisition, reconstruction, validation, benchmark, and dataset builders.
- `data/raw/`: downloaded inputs; generated locally and excluded from Git.
- `data/metadata/`: generated source metadata; excluded from Git.
- `data/processed/`: generated candidate and release data; excluded from Git until
  redistribution clearance is complete.
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

Raw downloads are intentionally not committed. Review each upstream dataset license
before redistribution.

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

## Release boundary

- Code may be used under the MIT license.
- The code license does not grant rights to E-REDES data or derived datasets.
- Processed data are not cleared for public redistribution in this repository yet.
- AC OPF and real-system operating claims are outside the benchmark-v1 scope.

## Citation

Citation metadata are provided in `CITATION.cff`. Update the author and repository fields
before creating a public release or DOI.

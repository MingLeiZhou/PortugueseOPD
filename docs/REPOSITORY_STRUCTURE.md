# Repository structure

## Version-controlled products

- `portuguese_hv_network/src/`: canonical PT60 v2 >=60 kV pipeline.
- `portuguese_hv_network/config/`: source and modelling configuration.
- `portuguese_hv_network/site/`: interactive explorer source.
- `pt60/`, `pyproject.toml`: installable `pt60-tools` Python package.
- `paper/PT60_Sep16.MD`: canonical manuscript source.
- `paper/scripts/generate_pt60_scientific_figures.py`: reproducible paper figures.
- `src/build_pt60_hv_release.py`: release builder and release gate.
- `data/releases/PT60-v2.0.0/`: compact, frozen benchmark release.
- `README.md`, `DATA_LICENSE.md`, `CITATION.cff`: public entry points.

The root `src/` directory also retains the reproducible v1 60 kV reconstruction
pipeline and historical diagnostic utilities. They are not the canonical v2
execution entry point.

## Local-only content

- `portuguese_hv_network/data/raw/`: downloaded public inputs.
- `portuguese_hv_network/data/manifests/`: runtime source fingerprints.
- `portuguese_hv_network/outputs/`: full intermediate and solved outputs.
- `portuguese_hv_network/site/public/data/`: generated web-map payloads.
- `portuguese_hv_network/site/public/downloads/`: generated release/download files.
- `data/releases/PT60-v*-rc*/`: generated release-candidate directories and archives.
- `paper/`: non-canonical drafts, downloaded references and rendered documents;
  the canonical source and current scientific figures are explicit exceptions.
- `output/`: local package builds, OPF experiments and rendered documents.
- `temp/`: superseded experiments, nested Git metadata and temporary artifacts.

These paths are ignored. Important generated records enter Git only through a
versioned dataset release built by `src/build_pt60_hv_release.py`.

## Release policy

The release contains compact topology, scenario, pandapower, power-flow,
validation and provenance products. Raw source downloads, caches, development
logs, paper drafts, local paths and transient web build outputs are excluded.
Published release directories are immutable: a changed dataset requires a new
versioned directory rather than rebuilding an existing release in place.

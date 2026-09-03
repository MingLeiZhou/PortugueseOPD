# Repository structure

## Version-controlled products

- `portuguese_hv_network/src/`: canonical PT60 v2 >=60 kV pipeline.
- `portuguese_hv_network/config/`: source and modelling configuration.
- `portuguese_hv_network/site/`: interactive explorer source.
- `src/build_pt60_hv_release.py`: release builder and release gate.
- `data/releases/PT60-v2.0.0/`: compact benchmark release.
- `README.md`, `DATA_LICENSE.md`, `CITATION.cff`: public entry points.

The root `src/` directory also retains the reproducible v1 60 kV reconstruction
pipeline and historical diagnostic utilities. They are not the canonical v2
execution entry point.

## Local-only content

- `portuguese_hv_network/data/raw/`: downloaded public inputs.
- `portuguese_hv_network/data/manifests/`: runtime source fingerprints.
- `portuguese_hv_network/outputs/`: full intermediate and solved outputs.
- `portuguese_hv_network/site/public/data/`: generated web-map payloads.
- `paper/`: manuscript sources and rendered papers.
- `temp/`: superseded experiments, nested Git metadata and temporary artifacts.

These paths are ignored. Important generated records enter Git only through a
versioned dataset release built by `src/build_pt60_hv_release.py`.

## Release policy

The release contains compact topology, scenario, pandapower, power-flow,
validation and provenance products. Raw source downloads, caches, development
logs, paper drafts, local paths and transient web build outputs are excluded.

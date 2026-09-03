# PT60

**PT60: Portuguese public-record high-voltage topology and AC power-flow benchmark dataset**

中文名称：**《基于葡萄牙公共电网记录构建的高压拓扑与交流潮流基准数据集》**。

PT60 is a provenance-labelled research dataset and reproducible pipeline for the
Portuguese electricity network at nominal voltages **greater than or equal to
60 kV**. The name PT60 is retained as the dataset identity; from v2.0.0 onward,
it covers 60, 130, 150, 220 and 400 kV rather than only the original 60 kV
candidate layer.

## Current release: PT60 v2.0.0

The validated public-data-informed benchmark contains:

- 3,783 buses, 4,943 lines and 228 transformers;
- 60, 130, 150, 220 and 400 kV topology;
- 401 load rows representing 10,268.8 MW at the calibrated timestamp;
- 1,421 mapped public generation candidates;
- source, evidence and parameter-status fields for topology and electrical data;
- unsolved and solved pandapower networks;
- a full-scale AC power-flow case and a 50--120% scaling sweep;
- 22/22 automated structural, evidence and scenario checks passing.

The full-scale case converges with 0.9293--1.0117 pu bus voltage, 94.94% maximum
line loading and 83.44% maximum transformer loading under the declared research
assumptions. These values demonstrate benchmark consistency; they are not
operator-validated Portuguese operating measurements.

The ready-to-deposit archive is `data/releases/PT60-v2.0.0.tar.gz`. The former
60 kV-only PT60-Candidate v1.0.2 dataset remains citable at
[doi:10.6084/m9.figshare.32984021](https://doi.org/10.6084/m9.figshare.32984021).

## Repository layout

- `portuguese_hv_network/src/`: canonical acquisition, integration, modelling,
  validation and export pipeline.
- `portuguese_hv_network/config/`: source registry and explicit model assumptions.
- `portuguese_hv_network/site/`: interactive MapLibre explorer; generated map
  payloads are excluded from Git.
- `src/build_pt60_hv_release.py`: builds and validates the PT60 v2 release archive.
- `data/releases/PT60-v2.0.0/`: versioned, deposit-ready dataset.
- `src/`: retained v1 pipeline and historical diagnostic utilities.
- `docs/`: project status, repository structure and QA guidance.
- `temp/`: ignored local archive for superseded experiments and temporary files.

Downloaded inputs and full runtime outputs are intentionally excluded from Git.
The versioned release contains the compact benchmark products needed for reuse.
Paper drafts are local-only and are not part of the software repository.

## Reproduce the benchmark

Python 3.12 is the reference environment.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r portuguese_hv_network/requirements.txt
python portuguese_hv_network/src/run_pipeline.py
```

Reuse already downloaded inputs and the relation-preserving OSM extraction:

```bash
python portuguese_hv_network/src/run_pipeline.py \
  --skip-download --skip-osm-extract
```

Build the deposit archive after a successful pipeline run:

```bash
python src/build_pt60_hv_release.py
```

## Interactive explorer

```bash
cd portuguese_hv_network/site
npm install
npm run dev
```

The pipeline generates the browser payload automatically. The explorer exposes
voltage filters, topology provenance, parameter status, line loading, bus
voltage and equipment details.

## Evidence and claim boundary

PT60 combines E-REDES public records, a fingerprinted OpenStreetMap Portugal
extract, REN public system context, ERSE/PDIRD planning records, Eurostat GISCO
boundaries and related public sources. Raw downloads are not redistributed in
the repository; their URLs, hashes, sizes and roles are recorded in the source
manifest.

The topology is a public-data-informed candidate network. Actual switch states,
complete circuit-specific R/X/C, unit-level transformer impedances and controls,
synchronized reactive demand, generator dispatch and an operator state-estimator
reference are not fully public. Missing quantities remain explicitly labelled as
partly source-backed, inferred or scenario assumptions.

Do not use PT60 as an operator network snapshot or for operational switching,
protection, contingency, emergency, asset-condition or infrastructure-targeting
decisions.

## Licensing

The MIT license applies to source code only. Dataset records retain the terms of
their source providers, including E-REDES CC BY 4.0 and OpenStreetMap ODbL
obligations. Read [DATA_LICENSE.md](DATA_LICENSE.md) and the release-level
attribution files before redistribution.

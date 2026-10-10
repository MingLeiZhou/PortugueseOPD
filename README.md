# SimPT-Power / PortugueseOPD

**A public-data multi-voltage (0.4-400 kV) power-system simulation database of mainland Portugal, with its build, validation and release code.**

This repository builds SimPT-Power from Portuguese public network, asset,
operating and geographic records: a solved 60-400 kV transmission and
sub-transmission core, medium-voltage root slices, secondary substations and
four-wire and geographic low-voltage networks. The 60-400 kV core started as the
PT60 dataset and toolchain, which remain available (see [PT60](#pt60-the-60-400-kv-core-toolchain)).

## Data release and citation

- **Current release:** [SimPT-Power-2026.10.04-r4 on Zenodo](https://doi.org/10.5281/zenodo.23132707)
  (all versions: [10.5281/zenodo.23067064](https://doi.org/10.5281/zenodo.23067064)).
- **Cite:** the data release above and this code; see [`CITATION.cff`](CITATION.cff).
- **Data paper:** in preparation.

## What the database contains

| Layer | Content |
| --- | --- |
| HV core (60-400 kV) | CORE-3787-REN: 3,787 buses, 4,926 lines, 219 transformers, reconciled with the REN RNT characterisation of 31-12-2025; 31,492 solved 15-minute AC power-flow snapshots (May 2025 - March 2026) |
| Medium voltage | 617 MV root slices with inferred urban cable networks and an MV customer estimate |
| Low voltage | 72,434 secondary substations with four-wire LV feeders; the geographic LV network LV-GEO-A |
| Observations | Public observation tables |

**What changed in r4:** the LV/MV split of each substation load is time-varying,
calibrated to the national LV share of LV+MV consumption
(`src/build_station_lv_share.py`), and every load-dependent layer was rebuilt
(`scripts/rebuild_r4.sh`). The HV core and observation files are identical to r3.1.

## Scope and limits

SimPT-Power is a simulation database, not an operator network model or a digital
twin: convergence demonstrates numerical consistency only; root-specific
medium-voltage results are not a synchronous nationwide snapshot; public,
inferred and simulated values are labelled separately, and operator ground truth
(conductor assignment, feeder boundaries and switch states, phase assignment,
protection settings, earthing parameters, customer metering) is not available
from public sources.

## Notebooks

[`kaggle/`](kaggle/) holds four notebooks that run on the uncompressed open bundle
(`open_bundle_r4.zip` in the Zenodo record): quick start, HV power flow
(peak-snapshot re-solve, loading map, N-1 example), MV/LV power flow and HV graph
learning. [`kaggle/README.md`](kaggle/README.md) describes how to run them locally
or on Kaggle.

## Build the release

- **HV core:** `portuguese_hv_network/src/` (`run_pipeline.py`,
  `run_ren_reconciliation.py`, `run_monthly_15min.py`).
- **MV/LV layers and release tooling:** `src/`. The current release is built by
  `scripts/rebuild_r4.sh` (resumable rebuild of the load-dependent layers) followed
  by `python3 src/build_simpt_power_r4.py db|clean|validate|compress|freeze|bundle`.
  Earlier releases: `build_simpt_power_r3_1.py`, `build_simpt_power_release.py`,
  `build_public_release.py`, `build_open_bundle.py`.
- **Zenodo versions:** `scripts/zenodo_new_version_r4.py` creates a draft for review;
  it never publishes.
- **Inputs:** large inputs are expected under `data/external/` (for example a link
  to the external drive holding `PT60_public_data_2025-05-01_2026-03-24`).

## PT60: the 60-400 kV core toolchain

PT60 connects Portuguese public network, asset and operating records into
reproducible AC power-flow cases for the 60, 130, 150, 220 and 400 kV network.
The retained network contains 3,783 buses, 4,943 lines and 228 transformers.
The temporal database contains 31,492 consecutive 15-minute operating cases
from 1 May 2025 through 24 March 2026, together with archived inputs,
power-flow results, validation records and spatial-allocation comparisons.
SimPT-Power's HV core CORE-3787-REN is the PT60 network reconciled with the REN
characterisation.

[Download the dataset](https://grid.jczw.xyz/download) ·
[Read the paper and supporting material](https://grid.jczw.xyz/downloads/PT60-paper-cn.zip) ·
[Explore the network](https://grid.jczw.xyz/) ·
[Use the Python tools](https://grid.jczw.xyz/downloads/usage-cn.md)

| Deliverable | Canonical entry |
| --- | --- |
| Dataset | [PT60 v2.1.0-rc2 download](https://grid.jczw.xyz/download), including source attribution, manifest and file hashes |
| Python package | [pt60-tools on PyPI](https://pypi.org/project/pt60-tools/); import `pt60`, command `pt60` |
| Website | [Project and downloads](https://grid.jczw.xyz/project), [interactive map](https://grid.jczw.xyz/) |
| Paper | Manuscript in preparation, not a published article |

The PT60 dataset is a release candidate; its permanent repository deposit and DOI
are pending.

### Install and load

Use Python 3.13 in a virtual environment:

```bash
python -m pip install pt60-tools==0.4.3
pt60 fetch https://grid.jczw.xyz/download --output work/PT60-v2.1.0-rc2 --sha256 07727842080141300c4ddf180f82a4e7f9f66e3a444a30297d650f79a3486913
export PT60_DATA="$PWD/work/PT60-v2.1.0-rc2"
pt60 verify
pt60 info
pt60 view
```

The browser opens at `http://127.0.0.1:8050`. The local viewer works without
external map services. The hosted map uses the same dataset and stable IDs.

```python
from pt60 import Dataset

data = Dataset("work/PT60-v2.1.0-rc2")
case = data.cases("SUMMER")[0]
inputs = data.load_input(case["case_id"])
results = data.results(case["case_id"])
```

### Build and reproduce cases

Install solver dependencies only when calculating new results:

```bash
python -m pip install "pt60-tools[solve]==0.4.3"
pt60 init --output work/my-input
# Edit the generated input files.
pt60 solve --input work/my-input --output work/my-result
pt60 replay --case PT60_2025_SUMMER_WEEK_JUL07_13_H000 --output work/replayed-case
pt60 view --result-dir work/my-result
```

`pt60 batch --full --spatial --output work/reproduction` reproduces the full
seasonal experiment and its controls. Outputs must be outside the frozen
release. See [the usage guide](https://grid.jczw.xyz/downloads/usage-cn.md) for fields and API.
The core installation requires NumPy, without PyTorch or GridSFM.

## Development and provenance

`pt60/` is the installable package; `portuguese_hv_network/src/` builds the
underlying network; `src/` builds the medium- and low-voltage layers and the
SimPT-Power releases; `kaggle/` holds example notebooks;
`portuguese_hv_network/site/` serves the website. Install from this checkout
with `python -m pip install -e '.[solve,test]'` and run
`python -m pytest tests/test_pt60_tools.py`.

The acquisition pipeline compiles the static network from source records.
The distributed toolchain starts from that compiled network and archived
observations to reproduce cases. Source roles, inferred quantities and model
assumptions are retained in the dataset. Convergence demonstrates numerical
consistency; it does not establish agreement with an operator state estimate.

The compact `data/releases/PT60-v2.0.0/` release is frozen for provenance.
Release candidates, archives and large web-download payloads are generated
locally and are not committed. Exploratory OPF and neural work is retained in
`experiments/opf_neural/` and local `output/opf/`, outside the core package and paper.

## License

Source code is MIT licensed. Data retain source-specific terms, including
E-REDES CC BY 4.0 and OpenStreetMap ODbL obligations. See
`SOURCE_LICENSES.md` in the SimPT-Power release, the PT60
[dataset attribution](https://grid.jczw.xyz/downloads/ATTRIBUTION.md) and the dataset's license files.

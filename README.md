# PT60

**Public observations → reusable AC power-flow cases → a verifiable dataset and toolchain.**

PT60 connects Portuguese public network, asset and operating records into
reproducible AC power-flow cases for the 60, 130, 150, 220 and 400 kV network.
The retained network contains 3,783 buses, 4,943 lines and 228 transformers.
The temporal database contains 31,492 consecutive 15-minute operating cases
from 1 May 2025 through 24 March 2026, together with archived inputs,
power-flow results, validation records and spatial-allocation comparisons.

## Start here

[Download the dataset](https://grid.jczw.xyz/download) ·
[Read the paper and supporting material](https://grid.jczw.xyz/downloads/PT60-paper-cn.zip) ·
[Explore the network](https://grid.jczw.xyz/) ·
[Use the Python tools](https://grid.jczw.xyz/downloads/usage-cn.md)

## Four deliverables

| Deliverable | Canonical entry |
| --- | --- |
| Dataset | [PT60 v2.1.0-rc2 download](https://grid.jczw.xyz/download), including source attribution, manifest and file hashes |
| Python package | [pt60-tools on PyPI](https://pypi.org/project/pt60-tools/); import `pt60`, command `pt60` |
| Website | [Project and downloads](https://grid.jczw.xyz/project), [interactive map](https://grid.jczw.xyz/) |
| Paper | [Chinese manuscript and supporting material](https://grid.jczw.xyz/downloads/PT60-paper-cn.zip); canonical local source: `paper/PT60_Sep16.MD` |

The dataset is a release candidate; permanent repository deposit and DOI are
pending. The paper is a manuscript, not a published article. The Python package is published on [PyPI](https://pypi.org/project/pt60-tools/).

## Install and load

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

## Build and reproduce cases

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
underlying network; `portuguese_hv_network/site/` serves the website; `paper/`
contains the current manuscript and figure sources. Install from this checkout
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
[dataset attribution](https://grid.jczw.xyz/downloads/ATTRIBUTION.md) and the dataset's license files.

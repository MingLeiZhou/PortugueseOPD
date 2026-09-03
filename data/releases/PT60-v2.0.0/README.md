# PT60 v2.0.0

PT60 is a public-data-informed benchmark dataset for the Portuguese high-voltage
network at nominal voltages of 60, 130, 150, 220 and 400 kV. Version 2 expands
the original 60 kV candidate topology into a national >=60 kV topology and a
reproducible AC power-flow benchmark.

## Contents

- `topology/`: buses, facilities, lines and transformers with evidence status.
- `scenario/`: synchronized or allocated load, generation and boundary inputs.
- `model/`: unsolved and solved pandapower JSON networks.
- `power_flow/`: full-scale results and 50--120% sensitivity sweep.
- `validation/`: structural, evidence, capacity and operating-screen checks.
- `provenance/`: source URLs, hashes, sizes and roles; raw downloads are excluded.

The validated full-scale case has 3,783 buses, 4,943 lines, 228 transformers and
10,268.8 MW load. It converges with a voltage range of 0.9293--1.0117 pu,
maximum line loading of 94.94%, and maximum transformer loading of 83.44%.

## Claim boundary

PT60 is a research benchmark, not an operator state-estimator export. Line
geometry is public-source derived, while most impedances, reactive demand,
generator dispatch, transformer impedance/taps and switching states remain
partly source-backed or explicit engineering assumptions. Results must not be
presented as verified Portuguese operating conditions.

## Reproduction

From the repository root:

```bash
python portuguese_hv_network/src/run_pipeline.py
python src/build_pt60_hv_release.py
```

See `DATA_LICENSE.md`, `ATTRIBUTION.md`, `manifest.json` and
`checksums.sha256` before redistribution.

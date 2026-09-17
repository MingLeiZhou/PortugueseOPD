# PT60 >=60 kV pipeline

## Current user entry point

The reviewed manuscript uses **v2.1.0-rc2**. From the repository root, install
`python -m pip install -e '.[solve]'`, then use `pt60 info`, `pt60 verify`,
`pt60 init`, `pt60 solve`, `pt60 replay`, `pt60 batch`, and `pt60 view`.
See [the tools guide](../docs/PT60_TOOLS_GUIDE_CN.md). The lightweight viewer
reads all 336 rc2 cases and two spatial alternatives directly from the archive.
The `site/` application reads rc2 through `pt60 export-web`; project and download
entry: https://grid.jczw.xyz/project.

The pipeline commands and numerical results below describe the earlier
construction/development baseline. Use the frozen rc2 runner for manuscript
reproduction; rerunning the acquisition pipeline creates a new data realization.

This directory contains the canonical PT60 v2 pipeline for constructing a
Portuguese 60--400 kV candidate topology and AC power-flow benchmark from public
records. It supersedes the original 60 kV-only project scope while preserving
the PT60 dataset name.

## Pipeline

```text
E-REDES + DGEG + Geofabrik/OpenStreetMap + REN + ERSE/PDIRD + GISCO
                              |
             relation-aware topology integration
                              |
       buses + lines + transformers + load + generation
                              |
           evidence-ranked electrical parameters
                              |
        pandapower model + AC power flow + validation
```

Run the complete workflow:

```bash
python portuguese_hv_network/src/run_pipeline.py
```

Build a new operating case from already-normalized public records:

```bash
python portuguese_hv_network/src/pt60_public_model.py \
  --input-dir /path/to/public-input \
  --output-dir /path/to/model-result
```

The input contract and Python API are documented in
[`PUBLIC_MODEL_INTERFACE.md`](PUBLIC_MODEL_INTERFACE.md). The command validates
national accounting, maps public substation loads, enforces generator
commissioning dates and `0 <= P <= nameplate_mw`, retains unmapped generation
as an explicit residual, runs AC power flow, and exports solved bus, line,
transformer, generator, load, boundary, loss, and provenance tables.

Reuse downloaded inputs and the relation-preserving OSM extraction:

```bash
python portuguese_hv_network/src/run_pipeline.py \
  --skip-download --skip-osm-extract
```

The pipeline writes only beneath `data/` and `outputs/`; these runtime products
are ignored. The root release builder copies the compact, validated public
products into `data/releases/PT60-v2.0.0/`.

## Full-resolution monthly models

Build every 15-minute steady-state model in one Europe/Lisbon calendar month
from the complete temporal DuckDB (no daily peak/median/valley sampling):

```bash
MPLCONFIGDIR=/tmp/pt60-mpl \
PYTHONPATH=portuguese_hv_network/src \
.venv/bin/python portuguese_hv_network/src/run_monthly_15min.py \
  --database /Volumes/SD/PT60/pt60_public_timeseries.duckdb \
  --month 2025-10 \
  --output-dir /path/on/internal-ssd/monthly_models/2025-10 \
  --resume-from-normalized /Volumes/SD/PT60/monthly_models/2025-10/pt60_models_2025-10.duckdb \
  --publish-database /Volumes/SD/PT60/monthly_models/2025-10/pt60_models_2025-10.compact.duckdb \
  --workers 8
```

The source database and optional former result database are attached read-only.
During computation, the resumable database stays on the internal SSD. It copies
`grid`, `geo`, and `provenance` once, stores each case's bus and line values as
two ordered `DOUBLE[]` arrays, and bundles audit records into one JSON value.
The device order is stored once in `bus_order` and `line_order`; expanded views
remain available for row-oriented SQL. After the full month succeeds, DuckDB
bulk-converts the closed staging database into a compact `.part` database beside
the SD-card destination; it is then verified and atomically renamed. Use
`--max-intervals 1` for a smoke test;
partial smoke tests are never published. Completed intervals are skipped on
restart unless `--rerun-complete` is supplied.

Two boundary rules are explicit in each case result: a missing weather hour at
the beginning of the archive may use the nearest available sample within one
hour, recording the requested hour, actual hour, and offset; during the autumn
DST fold, E-REDES observations sharing an indistinguishable wall-clock label
are averaged per substation and marked `AMBIGUOUS_DST_FOLD_MEAN_BY_SUBSTATION`.
The latter avoids doubling demand while acknowledging that the public label
does not identify which repeated-clock observation belongs to which fold.

## Current validated result

- 3,783 buses, 4,943 lines and 228 transformers;
- one active component for all scenario load and generation;
- 32/32 automated checks pass;
- full-scale 10,268.8 MW AC power flow converges;
- voltage 0.9324--1.0207 pu;
- maximum line/transformer loading 95.59%/77.55%;
- the 2026-01-20 Portugal--Spain boundary contains 7 external buses and 9
  in-service circuits at 6 Portuguese connection locations.

The later Ponte de Lima--Fontefría 400 kV interconnector is explicitly out of
service in this snapshot. Its post-commissioning 4,200/3,500 MW directional
exchange capacities are not backfilled into the January operating case.

A separate public-evidence experiment adds a synchronized 2026-03-23 E-REDES
load hold-out, activates the new interconnector for 2026-09-02, and replaces the
summer January-profile proxy with the prior year's same-season profile:

```bash
python portuguese_hv_network/src/run_temporal_validation.py --refresh
python portuguese_hv_network/src/run_temporal_24h_panel.py --refresh
```

Its interpretation and limitations are documented in
[`../docs/PT60_REALITY_GAP_REMEDIATION_LOG.md`](../docs/PT60_REALITY_GAP_REMEDIATION_LOG.md).

Regenerate the machine-readable public-benchmark scope decision ledger with:

```bash
python portuguese_hv_network/src/assess_public_benchmark_gaps.py
```

Every topology, parameter and operating row carries a direct, partially
source-backed, inferred or scenario-assumption status. Numerical convergence is
not operator validation.

## Interactive map

```bash
cd portuguese_hv_network/site
npm install
npm run dev
```

The map supports voltage-level filtering, evidence inspection, equipment search,
line-loading and bus-voltage views. Generated GeoJSON is excluded from Git and
is refreshed by the pipeline.

See [STATUS.md](STATUS.md) for the concise result and limitation record.

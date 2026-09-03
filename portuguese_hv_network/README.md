# PT60 >=60 kV pipeline

This directory contains the canonical PT60 v2 pipeline for constructing a
Portuguese 60--400 kV candidate topology and AC power-flow benchmark from public
records. It supersedes the original 60 kV-only project scope while preserving
the PT60 dataset name.

## Pipeline

```text
E-REDES + Geofabrik/OpenStreetMap + REN + ERSE/PDIRD + GISCO
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

Reuse downloaded inputs and the relation-preserving OSM extraction:

```bash
python portuguese_hv_network/src/run_pipeline.py \
  --skip-download --skip-osm-extract
```

The pipeline writes only beneath `data/` and `outputs/`; these runtime products
are ignored. The root release builder copies the compact, validated public
products into `data/releases/PT60-v2.0.0/`.

## Current validated result

- 3,783 buses, 4,943 lines and 228 transformers;
- one active component for all scenario load and generation;
- 22/22 automated checks pass;
- full-scale 10,268.8 MW AC power flow converges;
- voltage 0.9293--1.0117 pu;
- maximum line/transformer loading 94.94%/83.44%.

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

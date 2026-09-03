# Project status

Updated: 2026-09-03

## Objective

Deliver **PT60**, a Portuguese public-record high-voltage topology and AC
power-flow benchmark covering all modelled voltage levels greater than or equal
to 60 kV. PT60 remains the dataset name; v2.0.0 supersedes the former 60 kV-only
scope without claiming to reproduce an operator network.

## Current result

- Voltage levels: 60, 130, 150, 220 and 400 kV.
- Network: 3,783 buses, 4,943 lines and 228 transformers.
- Scenario: 10,268.8 MW full-scale load with 1,421 mapped generation candidates.
- Connectivity: one component contains all scenario load and generation.
- Validation: 22/22 checks pass.
- AC power flow: full-scale case converges.
- Screen: 0.9293--1.0117 pu voltage, 94.94% maximum line loading and 83.44%
  maximum transformer loading.
- Release: `data/releases/PT60-v2.0.0/` plus deterministic tar.gz archive.

## Evidence coverage

All line geometry is derived from direct public E-REDES or OSM records. Of 4,943
line rows, 1,517 are partially backed by matched PDIRD circuits; the other 3,426
use declared voltage-class parameter proxies. Transformer topology includes 199
direct OSM transformer records and 29 evidence-labelled public-record or
co-location inferences. All non-observed operating quantities retain explicit
status fields.

## Remaining limitations

- no complete authoritative switching or busbar state;
- incomplete circuit-specific impedance and shunt data;
- incomplete unit-level transformer impedance, tap and control records;
- reactive demand and asset-level dispatch are scenario quantities;
- no operator state-estimator or EMS comparison;
- OSM geometry is public evidence, not operator validation.

The dataset is appropriate for reproducible topology integration, provenance
research, power-flow workflows, sensitivity studies and benchmark development.
It is not appropriate for operational or security-sensitive grid decisions.

## Release work remaining outside Git

The v2.0.0 archive is ready for repository deposit. Publishing it under a DOI,
confirming the downloaded archive hash and completing any associated paper's
author/governance declarations remain external actions.

# 104 PT60 Spatial-Alignment Negative Control

Generated: 2026-07-14T13:30:40+00:00

## Purpose

This negative control keeps endpoint names and branch attributes fixed but translates retained branch geometries by a deterministic longitude/latitude offset. The test checks whether corridor-based OSM/OpenInfraMap evidence declines when spatial alignment is deliberately broken.

## Design

- branches tested: 358
- seed: 20260714
- displacement: 1.75 degrees longitude and 0.85 degrees latitude
- endpoint names, facility codes, voltage, status, length and branch identifiers are retained
- interpretation: spatial matcher selectivity only; this is not an accuracy, precision or recall estimate

## Results

- real corridor-evidence branches: 290 / 358 (0.8101)
- displaced-geometry corridor-evidence branches: 1 / 358 (0.0028)
- absolute corridor-evidence rate drop: 0.8073
- relative corridor-evidence reduction: 0.9966

| external_evidence_status | real branches | displaced-geometry branches |
|---|---:|---:|
| NO_EXTERNAL_OSM_MATCH | 0 | 337 |
| OSM_GEOMETRY_MEDIUM | 48 | 1 |
| OSM_GEOMETRY_OPERATOR_STRONG | 64 | 0 |
| OSM_NAME_OPERATOR_STRONG | 183 | 0 |
| OSM_NEARBY_WEAK | 7 | 20 |
| OSM_PARTIAL_NAME_NEARBY | 56 | 0 |

## Paper Interpretation

A decline in corridor evidence after spatial displacement supports the selectivity of the geometry component of the public-source matcher. Name-based evidence can remain because endpoint labels are intentionally retained; these categories should still be described as public-source concordance, not branch truth.

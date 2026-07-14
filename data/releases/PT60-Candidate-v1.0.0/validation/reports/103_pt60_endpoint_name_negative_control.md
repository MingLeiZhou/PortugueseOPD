# 103 PT60 Endpoint-Name Negative Control

Generated: 2026-07-14T13:25:26+00:00

## Purpose

This negative control keeps retained branch geometries fixed and replaces endpoint names with endpoint pairs from other retained branches in the same length stratum. The test checks whether name-based OSM/OpenInfraMap evidence declines when endpoint identity is deliberately broken.

## Design

- branches tested: 358
- seed: 20260714
- corruption: deterministic length-stratified cyclic shift of endpoint-name pairs
- geometry, voltage, status, length and branch identifiers are retained
- interpretation: matcher selectivity only; this is not an accuracy, precision or recall estimate

## Results

- real strong-name evidence: 183 / 358 (0.5112)
- corrupted-name strong-name evidence: 9 / 358 (0.0251)
- absolute strong-name rate drop: 0.4860
- relative strong-name reduction: 0.9508

| external_evidence_status | real branches | corrupted-name branches |
|---|---:|---:|
| OSM_GEOMETRY_MEDIUM | 48 | 14 |
| OSM_GEOMETRY_OPERATOR_STRONG | 64 | 325 |
| OSM_NAME_OPERATOR_STRONG | 183 | 9 |
| OSM_NEARBY_WEAK | 7 | 7 |
| OSM_PARTIAL_NAME_NEARBY | 56 | 3 |

## Paper Interpretation

A decline in strong-name evidence after endpoint-name corruption supports using endpoint names as a non-trivial public-source concordance signal. Remaining geometry-based matches should be described as corridor concordance, not branch truth.

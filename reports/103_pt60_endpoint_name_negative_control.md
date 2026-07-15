# 103 PT60 Endpoint-Name Negative Control

Generated: 2026-07-15T14:49:40+00:00

## Purpose

This negative control keeps retained branch geometries fixed and replaces endpoint names with endpoint pairs from other retained branches in the same length stratum. The test checks whether name-based OSM/OpenInfraMap evidence declines when endpoint identity is deliberately broken.

## Design

- branches tested: 358
- seed: 20260714
- corruption: deterministic length-stratified cyclic shift of endpoint-name pairs
- geometry, voltage, status, length and branch identifiers are retained
- interpretation: matcher selectivity only; this is not an accuracy, precision or recall estimate

## Results

- real strong-name evidence: 182 / 358 (0.5084)
- corrupted-name strong-name evidence: 9 / 358 (0.0251)
- absolute strong-name rate drop: 0.4832
- relative strong-name reduction: 0.9505

| external_evidence_status | real branches | corrupted-name branches |
|---|---:|---:|
| OSM_GEOMETRY_MEDIUM | 50 | 9 |
| OSM_GEOMETRY_OPERATOR_STRONG | 63 | 327 |
| OSM_NAME_OPERATOR_STRONG | 182 | 9 |
| OSM_NEARBY_WEAK | 7 | 6 |
| OSM_PARTIAL_NAME_NEARBY | 56 | 7 |

## Paper Interpretation

A decline in strong-name evidence after endpoint-name corruption supports using endpoint names as a non-trivial public-source concordance signal. Remaining geometry-based matches should be described as corridor concordance, not branch truth.

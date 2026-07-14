# 105 PT60 OSM/OpenInfraMap Evidence Independence Audit

Generated: 2026-07-14T13:40:16+00:00

## Scope

This audit classifies the provenance risk of OSM/OpenInfraMap evidence used for PT60 public-source triangulation. It does not treat OSM as ground truth and it does not treat `operator=E-REDES` as operator validation.

## Inputs

- matched retained branches: `data/processed/topology_validation/pt_topology_cross_validation_osm_matches_independence_audit.csv`
- OSM meta cache: `data/processed/topology_validation/pt_osm_openinframap_60kv_power_ways_meta.json`
- OSM history cache: `data/processed/topology_validation/pt_osm_matched_way_histories.json`

## Results

- OSM records with metadata: 5551
- matched branches audited: 358
- branches with timestamped matched OSM metadata: 358
- unique matched OSM ways with history: 271
- branches with historical source-tag risk: 1

| independence_category | branches |
|---|---:|
| more_independent_public_evidence | 266 |
| possibly_same_source | 5 |
| unknown | 87 |

## Evidence Role Counts

| evidence_role | branches |
|---|---:|
| endpoint_name_or_ref+geometry_corridor+operator_tag | 189 |
| endpoint_name_or_ref+operator_tag | 5 |
| geometry_corridor | 1 |
| geometry_corridor+operator_tag | 22 |
| operator_tag | 4 |
| partial_endpoint_name_or_ref+geometry_corridor+operator_tag | 78 |
| partial_endpoint_name_or_ref+operator_tag | 59 |

## Interpretation

Use `public-source triangulation` or `public-source concordance` in the manuscript. Do not use `independent validation`, `operator validation`, precision, recall, or accuracy language from these categories alone. Branches labelled `more_independent_public_evidence` have stronger inspectable public evidence, but their source derivation is still not guaranteed.

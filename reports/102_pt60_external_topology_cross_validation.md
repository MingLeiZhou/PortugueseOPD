# 102 PT60 External Topology Cross-Validation

Generated: 2026-07-13T16:52:38+00:00

## Scope

This report tests whether independent public sources can provide external evidence for the PT60-Candidate branch topology. It does not replace operator validation and it does not overwrite the manual review protocol.

## Inputs

- candidate branches: `data/releases/PT60-Candidate-v1.0.0/core_topology/at_interfacility_candidate_branches.csv`
- OSM/OpenInfraMap raw cache: local generation input excluded from the public repository and main release archive
- source audit: `data/releases/PT60-Candidate-v1.0.0/validation/pt_topology_cross_validation_source_audit.csv`

## Source Findings

- E-REDES official open-data tables remain useful for facility, voltage, load, capacity, and consistency checks, but they are not an independent branch-level truth table when the candidate topology is derived from E-REDES topology geometries.
- REN and ENTSO-E sources are useful for RNT/transmission boundary context, not for complete 60 kV distribution branch validation.
- OSM/OpenInfraMap contains external public 60 kV records in Portugal, including records with `voltage=60000`, `operator=E-REDES`, names, references, and circuit/cable tags. These are suitable for triangulation, not official truth. Their independence from E-REDES source geometry must be checked before using them as a strict external validation layer.

## Automated Evidence Summary

- branches tested: 358
- OSM 60 kV evidence ways downloaded: 5551
- branches with any non-empty OSM match category: 358
- strong OSM evidence branches: 247
- medium OSM evidence branches: 104
- weak OSM nearby branches: 7
- no OSM match branches: 0

## Status Counts

| external_evidence_status | branches |
|---|---:|
| OSM_GEOMETRY_MEDIUM | 48 |
| OSM_GEOMETRY_OPERATOR_STRONG | 64 |
| OSM_NAME_OPERATOR_STRONG | 183 |
| OSM_NEARBY_WEAK | 7 |
| OSM_PARTIAL_NAME_NEARBY | 56 |

## Example Matches

| branch_id | endpoints | status | OSM evidence | distance_m | coverage_500m |
|---|---|---|---|---:|---:|
| ATPL_00001 | BUSTOS - MIRA | OSM_NAME_OPERATOR_STRONG | Bustos - Mira | 0.0 | 1.00 |
| ATPL_00002 | OLIVEIRA DO BAIRRO - BUSTOS | OSM_GEOMETRY_MEDIUM | Bustos - Mira | 25.2 | 0.09 |
| ATPL_00003 | MERCEANA - VALE TEJO | OSM_PARTIAL_NAME_NEARBY | Vale do Tejo - C.P. Vila Franca I | 8.4 | 0.07 |
| ATPL_00004 | MATACÃES - MERCEANA | OSM_GEOMETRY_MEDIUM | Matacães - Joguinho 2 / Alto da Folgorosa | 5.6 | 0.33 |
| ATPL_00005 | EPAL - AREIAS (VFX) | OSM_GEOMETRY_OPERATOR_STRONG | PS Sobralinho - Areias / EPAL | 0.1 | 0.62 |
| ATPL_00006 | PÓVOA - ANAIA | OSM_NAME_OPERATOR_STRONG | Anaia - Póvoa | 0.3 | 1.00 |
| ATPL_00007 | MONTECHORO - ALBUFEIRA | OSM_GEOMETRY_MEDIUM | CF Montechoro II - Montechoro | 13.9 | 0.04 |
| ATPL_00008 | ALBUFEIRA - TUNES | OSM_PARTIAL_NAME_NEARBY | Tunes (REN) - PS Paderne | 16.6 | 0.04 |
| ATPL_00009 | ALBUFEIRA - TUNES | OSM_PARTIAL_NAME_NEARBY | Tunes (REN) - PS Paderne | 40.6 | 0.04 |
| ATPL_00010 | ALBUFEIRA - ARMAÇÃO DE PERA | OSM_GEOMETRY_OPERATOR_STRONG | https://www.openstreetmap.org/way/749735849 | 1.6 | 0.73 |

## Interpretation

The automated OSM/OpenInfraMap layer can reduce manual review effort by prioritizing branches with independent corridor/name evidence. It cannot by itself establish precision or recall because OSM coverage is incomplete and not an official operator truth source.

Recommended paper language: `externally triangulated`, not `operator-validated`.

## Next Action

Use the match table to pre-fill evidence links in the 100-branch manual validation sample. Branches with strong OSM evidence should still be adjudicated, while branches without OSM evidence should be reviewed against planning documents, satellite imagery, or operator/regulator records.

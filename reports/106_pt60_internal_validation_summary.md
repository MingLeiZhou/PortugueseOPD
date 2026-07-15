# 106 PT60 Internal Validation Summary

Generated: 2026-07-15T15:01:03+00:00

Status: `PASS`

- total checks: 25
- status counts: {'PASS': 25}
- retained branches: 358
- circuit ledger rows: 1341
- retained + downgraded/rejected: 358 + 983 = 1341
- GraphML nodes/edges: 484 / 358
- endpoint index rows: 64008
- sensitivity rows: 216

## Checks

| check_id | layer | status | observed | expected |
|---|---|---|---|---|
| raw_geometry_validity | source | PASS | 5334 | 5334 |
| retained_branch_geometry_validity | branches | PASS | 358 | 358 |
| source_coordinate_reference | crs | PASS | Opendatasoft v2.1 GeoJSON export without an epsg override; default EPSG:4326 | EPSG:4326 portal export and release geometry |
| metric_coordinate_reference | crs | PASS | ETRS89 / Portugal TM06 (EPSG:3763), Transverse Mercator, units=m | ETRS89 / Portugal TM06 (EPSG:3763), Transverse Mercator, units=m |
| retained_required_fields | branches | PASS | {"branch_id": 0, "circuit_id": 0, "from_facility_uid": 0, "to_facility_uid": 0, "from_facility_name": 0, "to_facility_name": 0, "voltage": 0, "status": 0, "total_length_km": 0, "number_of_original_segments": 0, "geometry": 0, "source_line_ids": 0, "confidence_score": 0, "classification": 0} | 0 missing required values |
| retained_branch_id_unique | branches | PASS | 358 | 358 |
| retained_circuit_id_unique | branches | PASS | 358 | 358 |
| retained_no_self_loops | branches | PASS | 0 | 0 |
| ledger_required_fields | ledger | PASS | {"circuit_id": 0, "classification": 0, "terminal_count": 0, "segment_count": 0, "total_length_km": 0, "source_line_ids": 0, "line_ids": 0, "geometry_type": 0} | 0 missing required values |
| ledger_circuit_id_unique | ledger | PASS | 1341 | 1341 |
| ledger_retained_plus_downgraded | ledger | PASS | 358+983=1341 | 358+983=1341 |
| ledger_class_reconciliation | ledger | PASS | {"ambiguous": 61, "inter-facility": 358, "isolated": 216, "loop": 5, "self-loop": 101, "single-facility": 496, "tap / multi-terminal": 104} | {"inter-facility": 358, "single-facility": 496, "isolated": 216, "tap / multi-terminal": 104, "self-loop": 101, "ambiguous": 61, "loop": 5} |
| ledger_terminal_count_distribution | ledger | PASS | {"0": 5, "1": 3, "2": 1223, "3": 98, "4": 9, "6": 3} | reported distribution |
| selected_endpoint_membership | endpoints | PASS | 2879 inside; 127 ambiguous | selected B/100 m membership row present |
| endpoint_index_rows | endpoints | PASS | 64008 | 64008 |
| selected_endpoint_clusters | endpoints | PASS | 6627 | 0.5 m snap-threshold row present |
| selected_facility_footprints | facilities | PASS | 484 | 484 |
| graph_edge_count | graph | PASS | 358 | 358 |
| graph_node_count | graph | PASS | 484 | 484 |
| graph_branch_id_parity | graph | PASS | missing=0, extra=0 | 0 missing, 0 extra |
| graph_endpoint_reference_parity | graph | PASS | 0 | 0 |
| graph_isolates_and_parallel_edges | graph | PASS | isolates=109, parallel_groups=27, parallel_edge_records=55, parallel_extra_edges=28, self_loops=0 | reported graph structure |
| sensitivity_sweep_rows | sensitivity | PASS | 216 | 216 |
| selected_sweep_row | sensitivity | PASS | 1 | 1 |
| release_hash_baseline | determinism | PASS | 9 | 9 |

## Interpretation

These checks validate internal release consistency, schema-critical completeness, ledger accounting, and GraphML/table parity. They do not validate the inferred physical topology against an operator truth source.

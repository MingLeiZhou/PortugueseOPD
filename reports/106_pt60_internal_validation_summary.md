# 106 PT60 Internal Validation Summary

Generated: 2026-07-15T13:33:59+00:00

Status: `PASS_WITH_WARNINGS`

- total checks: 25
- status counts: {'PASS': 24, 'WARN': 1}
- retained branches: 358
- circuit ledger rows: 1342
- retained + downgraded/rejected: 358 + 984 = 1342
- GraphML nodes/edges: 484 / 358
- endpoint index rows: 64008
- sensitivity rows: 216

## Checks

| check_id | layer | status | observed | expected |
|---|---|---|---|---|
| raw_geometry_validity | source | PASS | 5334 | 5334 |
| retained_branch_geometry_validity | branches | PASS | 358 | 358 |
| source_coordinate_reference | crs | PASS | Opendatasoft v2.1 GeoJSON export without an epsg override; default EPSG:4326 | EPSG:4326 portal export and release geometry |
| metric_coordinate_reference | crs | PASS | LOCAL_EQUIRECTANGULAR_PORTUGAL lon0=-8.532604, lat0=39.567953, units=m | LOCAL_EQUIRECTANGULAR_PORTUGAL lon0=-8.532604, lat0=39.567953, units=m |
| retained_required_fields | branches | PASS | {"branch_id": 0, "circuit_id": 0, "from_facility_uid": 0, "to_facility_uid": 0, "from_facility_name": 0, "to_facility_name": 0, "voltage": 0, "status": 0, "total_length_km": 0, "number_of_original_segments": 0, "geometry": 0, "source_line_ids": 0, "confidence_score": 0, "classification": 0} | 0 missing required values |
| retained_branch_id_unique | branches | PASS | 358 | 358 |
| retained_circuit_id_unique | branches | PASS | 358 | 358 |
| retained_no_self_loops | branches | PASS | 0 | 0 |
| ledger_required_fields | ledger | PASS | {"circuit_id": 0, "classification": 0, "terminal_count": 0, "segment_count": 0, "total_length_km": 0, "source_line_ids": 0, "line_ids": 0, "geometry_type": 0} | 0 missing required values |
| ledger_circuit_id_unique | ledger | PASS | 1342 | 1342 |
| ledger_retained_plus_downgraded | ledger | PASS | 358+984=1342 | 358+984=1342 |
| ledger_class_reconciliation | ledger | PASS | {"ambiguous": 61, "inter-facility": 358, "isolated": 215, "loop": 5, "self-loop": 101, "single-facility": 496, "tap / multi-terminal": 106} | {"inter-facility": 358, "single-facility": 496, "isolated": 215, "tap / multi-terminal": 106, "self-loop": 101, "ambiguous": 61, "loop": 5} |
| ledger_terminal_count_distribution | ledger | PASS | {"0": 5, "1": 2, "2": 1223, "3": 100, "4": 10, "6": 2} | reported distribution |
| selected_endpoint_membership | endpoints | PASS | 2882 inside; 127 ambiguous | selected B/100 m membership row present |
| endpoint_index_rows | endpoints | PASS | 64008 | 64008 |
| selected_endpoint_clusters | endpoints | PASS | 6631 | 0.5 m snap-threshold row present |
| selected_facility_footprints | facilities | PASS | 484 | 484 |
| graph_edge_count | graph | PASS | 358 | 358 |
| graph_node_count | graph | PASS | 484 | 484 |
| graph_branch_id_parity | graph | PASS | missing=0, extra=0 | 0 missing, 0 extra |
| graph_endpoint_reference_parity | graph | PASS | 0 | 0 |
| graph_isolates_and_parallel_edges | graph | PASS | isolates=109, parallel_groups=27, parallel_edge_records=55, parallel_extra_edges=28, self_loops=0 | reported graph structure |
| sensitivity_sweep_rows | sensitivity | PASS | 216 | 216 |
| selected_sweep_row | sensitivity | PASS | 1 | 1 |
| release_hash_baseline | determinism | WARN | 9 | clean-room rerun equality still pending |

## Interpretation

These checks validate internal release consistency, schema-critical completeness, ledger accounting, and GraphML/table parity. They do not validate the inferred physical topology against an operator truth source.

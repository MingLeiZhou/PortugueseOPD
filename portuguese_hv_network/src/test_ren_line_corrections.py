from build_topology import match_ren_correction, per_voltage_circuits


def test_single_voltage_keeps_way_count():
    assert per_voltage_circuits("2", "400000", 400) == ("2", "SINGLE_VOLTAGE_SLOT")


def test_mixed_voltage_tower_splits_circuits():
    assert per_voltage_circuits("2", "150000;400000", 150)[0] == "1"
    assert per_voltage_circuits("2", "150000;400000", 400)[0] == "1"
    assert per_voltage_circuits("4", "220000;400000", 220)[0] == "2"


def test_zero_voltage_slot_is_unused_circuit():
    circuits, status = per_voltage_circuits("2", "0;400000", 400)
    assert circuits == "1" and status.endswith("ZERO_SLOT_UNUSED")


def test_missing_tag_passthrough():
    assert per_voltage_circuits("", "400000", 400) == ("", "NO_CIRCUIT_TAG")


def test_correction_matching_by_way_and_voltage():
    corrections = [
        {"correction_id": "X1", "match_type": "osm_way_id", "match_value": "123", "voltage_kv": "150", "action": "exclude"},
        {"correction_id": "X2", "match_type": "name", "match_value": "A - B", "voltage_kv": "", "action": "exclude"},
    ]
    assert match_ren_correction({"osm_way_id": 123.0, "voltage_kv": 150, "name": ""}, corrections)["correction_id"] == "X1"
    assert match_ren_correction({"osm_way_id": 123.0, "voltage_kv": 400, "name": ""}, corrections) is None
    assert match_ren_correction({"osm_way_id": 9, "voltage_kv": 400, "name": "A - B"}, corrections)["correction_id"] == "X2"


from build_transformers import station_key, unit_mva


def test_ren_unit_mva_parses_single_phase_banks():
    assert unit_mva("3x57") == 171 and unit_mva("170") == 170.0


def test_station_key_matches_osm_and_ren_names():
    assert station_key("Subestação de Riba d'Ave") == station_key("RIBA D´AVE") == "ribadave"
    assert station_key("SIDERURGIA DA MAIA (e)") == station_key("Subestação da Siderurgia da Maia")

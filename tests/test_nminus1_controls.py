from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandapower as pp


SCRIPT = Path(__file__).resolve().parents[1] / "paper/scripts/run_nminus1_application.py"
SPEC = importlib.util.spec_from_file_location("run_nminus1_application", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _geo(lon: float, lat: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [lon, lat]})


def test_redispatch_is_power_balanced_and_capped() -> None:
    net = pp.create_empty_network()
    b0 = pp.create_bus(net, vn_kv=60, geodata=(-8.5, 41.8))
    b1 = pp.create_bus(net, vn_kv=60, geodata=(-8.4, 41.8))
    b2 = pp.create_bus(net, vn_kv=60, geodata=(-9.5, 39.0))
    for bus, coordinates in ((b0, (-8.5, 41.8)), (b1, (-8.4, 41.8)), (b2, (-9.5, 39.0))):
        net.bus.at[bus, "geo"] = _geo(*coordinates)
    line = pp.create_line_from_parameters(
        net, b0, b1, 10.0, 0.1, 0.4, 0.0, 1.0
    )
    local = pp.create_gen(net, b1, p_mw=10.0, vm_pu=1.0)
    remote = pp.create_gen(net, b2, p_mw=500.0, vm_pu=1.0)
    net.gen["nameplate_mw"] = [500.0, 500.0]
    before = float(net.gen.p_mw.sum())
    result = MODULE._redispatch_toward_contingency(
        net,
        {"element_type": "line", "element_indices": [line]},
        fraction=1.0,
        max_redispatch_mw=200.0,
    )
    assert result["redispatch_mw"] == 200.0
    assert net.gen.at[local, "p_mw"] == 210.0
    assert net.gen.at[remote, "p_mw"] == 300.0
    assert float(net.gen.p_mw.sum()) == before


def test_physical_line_outage_opens_all_series_fragments() -> None:
    net = pp.create_empty_network()
    buses = [pp.create_bus(net, vn_kv=60) for _ in range(3)]
    lines = [
        pp.create_line_from_parameters(net, buses[0], buses[1], 1, 0.1, 0.4, 0, 1),
        pp.create_line_from_parameters(net, buses[1], buses[2], 1, 0.1, 0.4, 0, 1),
    ]
    contingency = {
        "element_type": "line",
        "element_indices": lines,
        "element_id": "EREDES:circuit:TEST:60",
        "contingency_action": "OPEN_LINE",
        "outage_plan_json": json.dumps([
            {"element_index": int(index), "action": "OPEN_LINE"} for index in lines
        ]),
    }
    MODULE.apply_contingency_outage(net, contingency)
    assert not net.line.loc[lines, "in_service"].any()

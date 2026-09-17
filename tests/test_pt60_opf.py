import json
from pathlib import Path
import numpy as np
import pytest

pp = pytest.importorskip("pandapower")
from experiments.opf_neural.opf import DEFAULT_POLICY, prepare, solve, physics_audit
from experiments.opf_neural.neural import export_graph, graph_to_ppc, read_ppc


def small_net():
    from pandapower.networks import case9
    n = case9()
    n.bus["bus_id"] = [f"BUS:{i}" for i in n.bus.index]
    n.gen["nameplate_mw"] = 300.
    n.gen["name"] = [f"GEN:{i}" for i in n.gen.index]
    n.poly_cost.drop(n.poly_cost.index, inplace=True)
    pp.runpp(n, numba=False)
    return n


def test_policy_rejects_unknown_generator_and_invalid_scales():
    with pytest.raises(ValueError, match="Unknown"):
        prepare(small_net(), {**DEFAULT_POLICY, "generator_availability": {"typo": 0}})
    with pytest.raises(ValueError, match="positive"):
        prepare(small_net(), {**DEFAULT_POLICY, "load_scale": 0})


def test_opf_graph_roundtrip_has_no_label_leakage_and_detects_bad_predictions(tmp_path):
    source = tmp_path / "pf.json"
    pp.to_json(small_net(), source)
    report = solve(source, tmp_path / "opf")
    assert report["feasible"]
    graph = tmp_path / "case.pyg.json"
    metadata = export_graph(tmp_path / "opf", graph)
    assert metadata["roundtrip_admittance_max_error"] < 1e-10
    obj = json.loads(graph.read_text())
    rebuilt = graph_to_ppc(obj)
    original = read_ppc(tmp_path / "opf/ppc.json")
    assert np.allclose(rebuilt["bus"][:, [2, 3, 4, 5, 11, 12]], original["bus"][:, [2, 3, 4, 5, 11, 12]])
    assert np.allclose(rebuilt["gencost"], original["gencost"])
    assert all(row[1] == 0 and row[4] == 0 for row in obj["grid"]["nodes"]["generator"])
    assert np.any(np.array(obj["solution"]["nodes"]["generator"]) != 0)
    obj["solution"] = {"untrusted_labels": True}
    again = graph_to_ppc(obj)
    assert np.array_equal(again["gen"], rebuilt["gen"])
    bad_voltage = np.full(len(rebuilt["bus"]), 1.3 + 0j)
    bad = physics_audit(rebuilt, bad_voltage)
    assert not bad["feasible"]
    assert bad["max_voltage_violation_pu"] > .19


def test_training_rejects_variants_of_same_source_in_test(tmp_path):
    from experiments.opf_neural.training import validate_splits
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for path in [a, b]:
        path.write_text(json.dumps({"metadata": {"termination_status": "LOCALLY_SOLVED", "source_pf_sha256": "same-input"}}))
    with pytest.raises(ValueError, match="leakage"):
        validate_splits({"train": [a], "test": [b]})

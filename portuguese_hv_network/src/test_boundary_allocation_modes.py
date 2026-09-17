import pandapower as pp
import pytest

from run_temporal_validation import regularize_cross_border_boundary


def _boundary_net():
    net = pp.create_empty_network()
    for voltage, bus_id in [(400.0, "TEST:400"), (220.0, "TEST:220"), (150.0, "TEST:150")]:
        bus = pp.create_bus(net, vn_kv=voltage)
        net.bus.loc[bus, "bus_id"] = bus_id
        pp.create_ext_grid(net, bus, name=f"EXT:{bus_id}")
    return net


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        ("VOLTAGE_X_CIRCUITS", "BUS_VOLTAGE_KV_TIMES_PUBLIC_CIRCUIT_COUNT"),
        ("VOLTAGE", "BUS_VOLTAGE_KV"),
        ("UNIFORM", "UNIFORM_ACROSS_PUBLIC_BOUNDARY_BUSES"),
    ],
)
def test_boundary_allocation_modes_preserve_one_reference(mode, label):
    net = _boundary_net()
    summary = regularize_cross_border_boundary(net, 300.0, mode)
    assert len(net.ext_grid) == 1
    assert len(net.sgen) == 2
    assert summary["boundary_weight_basis"] == label
    assert summary["boundary_observation_used_as_input"] is False


def test_uniform_boundary_allocation_uses_equal_shares():
    net = _boundary_net()
    regularize_cross_border_boundary(net, 300.0, "UNIFORM")
    assert sorted(net.sgen.p_mw.tolist()) == pytest.approx([100.0, 100.0])


def test_unknown_boundary_allocation_mode_fails_closed():
    with pytest.raises(ValueError, match="Unsupported boundary allocation mode"):
        regularize_cross_border_boundary(_boundary_net(), 300.0, "UNKNOWN")

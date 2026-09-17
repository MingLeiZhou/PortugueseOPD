"""Explicit, configurable AC-OPF benchmarks derived from solved PT60 cases."""
from __future__ import annotations

import json
from pathlib import Path
import time
import numpy as np
from contextlib import contextmanager

from pt60.dataset import digest

DEFAULT_POLICY = {
    "schema": "PT60_ACOPF_BENCHMARK_V1",
    "cost_provenance": "SYNTHETIC_BENCHMARK_NOT_OBSERVED_MARKET_BIDS",
    "voltage_min_pu": 0.9, "voltage_max_pu": 1.1,
    "generator_cost_linear": 50.0, "generator_cost_quadratic": 0.01,
    "boundary_cost_linear": 80.0, "boundary_cost_quadratic": 0.01,
    "reference_boundary_p_min_mw": -3000.0, "reference_boundary_p_max_mw": 3000.0,
    "reference_boundary_q_min_mvar": -3000.0, "reference_boundary_q_max_mvar": 3000.0,
    "load_scale": 1.0, "line_rating_scale": 1.0,
    "generator_availability": {}, "generator_cost_overrides": {},
    "boundary_rule": "Optimize the single reference boundary; retain other equivalent boundary injections",
    "generator_rule": "Optimize aggregated PV generator P/Q within available nameplate and existing Q bounds; retain fixed PQ injections",
    "thermal_rule": "Apparent-power limit at both branch ends on nominal-voltage-derived ratings",
}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


@contextmanager
def scipy_sparse_compat():
    """PYPOWER's S-flow Hessian uses .H, removed from SciPy sparse matrices."""
    from scipy.sparse import spmatrix
    missing = not hasattr(spmatrix, "H")
    if missing: spmatrix.H = property(lambda self: self.getH())
    try: yield
    finally:
        if missing: del spmatrix.H


def prepare(net, policy):
    import pandapower as pp
    if set(policy) != set(DEFAULT_POLICY) or policy["schema"] != DEFAULT_POLICY["schema"]:
        raise ValueError("Policy keys/schema must match the generated benchmark policy")
    if not (0 < policy["voltage_min_pu"] < policy["voltage_max_pu"]):
        raise ValueError("Invalid voltage limits")
    if policy["load_scale"] <= 0 or policy["line_rating_scale"] <= 0:
        raise ValueError("Scenario scales must be positive")
    for quantity, unit in [("p", "mw"), ("q", "mvar")]:
        low = policy[f"reference_boundary_{quantity}_min_{unit}"]
        high = policy[f"reference_boundary_{quantity}_max_{unit}"]
        if not np.isfinite([low, high]).all() or low >= high:
            raise ValueError("Boundary bounds must be finite and ordered")
    if not np.isfinite([policy["boundary_cost_linear"], policy["boundary_cost_quadratic"]]).all() or policy["boundary_cost_quadratic"] < 0:
        raise ValueError("Invalid boundary cost")
    if net.poly_cost.shape[0] or net.pwl_cost.shape[0]:
        raise ValueError("Input already has costs; supply a solved AC-PF case")
    names = set(net.gen.name)
    if set(policy["generator_availability"]) - names or set(policy["generator_cost_overrides"]) - names:
        raise ValueError("Unknown aggregated generator name in policy")
    net.bus["min_vm_pu"] = policy["voltage_min_pu"]
    net.bus["max_vm_pu"] = policy["voltage_max_pu"]
    net.line["max_loading_percent"] = 100.0 * policy["line_rating_scale"]
    net.trafo["max_loading_percent"] = 100.0
    net.load.loc[:, ["p_mw", "q_mvar"]] *= policy["load_scale"]
    net.gen["min_p_mw"] = 0.0
    net.gen["max_p_mw"] = net.gen.nameplate_mw
    if not np.isfinite(net.gen.max_p_mw).all() or (net.gen.max_p_mw < 0).any():
        raise ValueError("All controllable generators need finite nonnegative capacity")
    if not np.isfinite(net.gen[["min_q_mvar", "max_q_mvar"]]).all().all() or (net.gen.min_q_mvar > net.gen.max_q_mvar).any():
        raise ValueError("Controllable generator Q bounds must be finite and ordered")
    net.gen["controllable"] = True
    net.sgen["controllable"] = False
    audit = []
    for index, row in net.gen.iterrows():
        available = float(policy["generator_availability"].get(row["name"], 1.0))
        if not 0 <= available <= 1:
            raise ValueError("Generator availability must be between zero and one")
        net.gen.at[index, "max_p_mw"] *= available
        if available == 0:
            net.gen.at[index, "in_service"] = False
        cost = policy["generator_cost_overrides"].get(row["name"], {})
        c1 = float(cost.get("linear", policy["generator_cost_linear"]))
        c2 = float(cost.get("quadratic", policy["generator_cost_quadratic"]))
        if not np.isfinite([c1, c2]).all() or c2 < 0:
            raise ValueError("Costs must be finite and quadratic coefficient nonnegative")
        pp.create_poly_cost(net, index, "gen", c1, cp2_eur_per_mw2=c2)
        audit.append({"name": row["name"], "bus_id": net.bus.loc[row.bus, "bus_id"],
            "p_min_mw": 0., "p_max_mw": float(net.gen.at[index, "max_p_mw"]),
            "q_min_mvar": float(row.min_q_mvar), "q_max_mvar": float(row.max_q_mvar),
            "availability": available, "c1": c1, "c2": c2})
    net.ext_grid["controllable"] = True
    for quantity in ("p", "q"):
        unit = "mw" if quantity == "p" else "mvar"
        for bound in ("min", "max"):
            net.ext_grid[f"{bound}_{quantity}_{unit}"] = policy[f"reference_boundary_{quantity}_{bound}_{unit}"]
    for index in net.ext_grid.index:
        pp.create_poly_cost(net, index, "ext_grid", policy["boundary_cost_linear"], cp2_eur_per_mw2=policy["boundary_cost_quadratic"])
    return audit


def compact_ppc(ppc):
    """Retain the solved connected network with consecutive bus indices."""
    from pandapower.pypower.idx_bus import BUS_I, BUS_TYPE
    from pandapower.pypower.idx_brch import F_BUS, T_BUS, BR_STATUS
    from pandapower.pypower.idx_gen import GEN_BUS, GEN_STATUS
    bus = ppc["bus"]
    indices = np.where(bus[:, BUS_TYPE] != 4)[0]
    mapping = {int(bus[i, BUS_I]): j for j, i in enumerate(indices)}
    branch_indices = [i for i, r in enumerate(ppc["branch"]) if r[BR_STATUS].real == 1 and int(r[F_BUS].real) in mapping and int(r[T_BUS].real) in mapping]
    gen_indices = [i for i, r in enumerate(ppc["gen"]) if r[GEN_STATUS] == 1 and int(r[GEN_BUS]) in mapping]
    out = {"baseMVA": float(ppc["baseMVA"]), "bus": bus[indices].copy(),
        "branch": ppc["branch"][branch_indices].real.copy(), "gen": ppc["gen"][gen_indices].copy(),
        "gencost": ppc["gencost"][gen_indices].copy(), "version": "2"}
    out["bus"][:, BUS_I] = np.arange(len(indices))
    # MBASE is unused by the network equations; unspecified machine bases use the system base.
    out["gen"][~np.isfinite(out["gen"][:, 6]), 6] = out["baseMVA"]
    if out["branch"].shape[1] < 26:
        out["branch"] = np.pad(out["branch"], ((0, 0), (0, 26 - out["branch"].shape[1])))
    for key, columns in [("branch", [F_BUS, T_BUS]), ("gen", [GEN_BUS])]:
        for column in columns: out[key][:, column] = [mapping[int(v)] for v in out[key][:, column]]
    return out, indices.tolist(), branch_indices, gen_indices


def physics_audit(ppc, voltage=None, generation=None):
    from pandapower.pypower.makeYbus import makeYbus
    from pandapower.pypower.makeSbus import makeSbus
    from pandapower.pypower.idx_bus import VM, VA, VMIN, VMAX
    from pandapower.pypower.idx_gen import PG, QG, PMIN, PMAX, QMIN, QMAX
    from pandapower.pypower.idx_brch import F_BUS, T_BUS, RATE_A, ANGMIN, ANGMAX
    base = ppc["baseMVA"]
    bus, branch, gen = ppc["bus"], ppc["branch"], ppc["gen"].copy()
    v = bus[:, VM] * np.exp(1j * np.deg2rad(bus[:, VA])) if voltage is None else voltage
    if generation is not None: gen[:, PG], gen[:, QG] = generation[:, 0], generation[:, 1]
    y, yf, yt = makeYbus(base, bus, branch)
    mismatch = v * np.conj(y @ v) - makeSbus(base, bus, gen)
    sf = v[branch[:, F_BUS].astype(int)] * np.conj(yf @ v) * base
    st = v[branch[:, T_BUS].astype(int)] * np.conj(yt @ v) * base
    positive = lambda x: float(max(0., np.max(x, initial=0)))
    limits = branch[:, RATE_A] > 0
    angle = np.rad2deg(np.angle(v))
    difference = angle[branch[:, F_BUS].astype(int)] - angle[branch[:, T_BUS].astype(int)]
    bounded_angle = (branch[:, ANGMIN] > -360) | (branch[:, ANGMAX] < 360)
    cost = ppc["gencost"]
    objective = float(np.sum(cost[:, 4] * gen[:, PG] ** 2 + cost[:, 5] * gen[:, PG] + cost[:, 6]))
    report = {"max_p_balance_residual_mw": float(np.max(np.abs(mismatch.real)) * base),
        "max_q_balance_residual_mvar": float(np.max(np.abs(mismatch.imag)) * base),
        "max_voltage_violation_pu": max(positive(bus[:, VMIN] - abs(v)), positive(abs(v) - bus[:, VMAX])),
        "max_generator_p_violation_mw": max(positive(gen[:, PMIN] - gen[:, PG]), positive(gen[:, PG] - gen[:, PMAX])),
        "max_generator_q_violation_mvar": max(positive(gen[:, QMIN] - gen[:, QG]), positive(gen[:, QG] - gen[:, QMAX])),
        "max_branch_s_violation_mva": positive(np.maximum(abs(sf), abs(st))[limits] - branch[limits, RATE_A]),
        "max_angle_violation_degree": max(positive(branch[bounded_angle, ANGMIN] - difference[bounded_angle]), positive(difference[bounded_angle] - branch[bounded_angle, ANGMAX])),
        "objective": objective}
    report["feasible"] = all(np.isfinite(v) for v in report.values()) and all(report[key] <= tolerance for key, tolerance in [
        ("max_p_balance_residual_mw", 1e-3), ("max_q_balance_residual_mvar", 1e-3),
        ("max_voltage_violation_pu", 1e-5), ("max_generator_p_violation_mw", 1e-3),
        ("max_generator_q_violation_mvar", 1e-3), ("max_branch_s_violation_mva", 1e-3), ("max_angle_violation_degree", 1e-5)])
    return report


def solve(source, output, policy_path=None):
    import pandapower as pp
    output = Path(output)
    if output.exists(): raise ValueError("OPF output must be a new directory")
    policy = json.loads(Path(policy_path).read_text()) if policy_path else dict(DEFAULT_POLICY)
    net = pp.from_json(source)
    audit = prepare(net, policy)
    output.mkdir(parents=True)
    write_json(output / "policy.json", policy)
    write_json(output / "generator_policy.json", audit)
    pp.to_json(net, output / "input.json")
    start = time.perf_counter()
    try:
        with scipy_sparse_compat():
            pp.runopp(net, init="pf", numba=False, OPF_FLOW_LIM=0, PDIPM_MAX_IT=150)
    except Exception as error:
        write_json(output / "report.json", {"status": "NOT_CONVERGED" if isinstance(error, pp.OPFNotConverged) else "SOLVER_ERROR",
            "error": f"{type(error).__name__}: {error}", "seconds": time.perf_counter() - start,
            "input_sha256": digest(source), "policy": policy})
        raise RuntimeError(f"AC-OPF failed; inspect {output / 'report.json'}") from error
    ppc, bus_indices, branch_indices, gen_indices = compact_ppc(net._ppc)
    report = physics_audit(ppc)
    report.update(status="LOCALLY_SOLVED" if report["feasible"] else "AUDIT_FAILED", seconds=time.perf_counter() - start,
        input_sha256=digest(source), solver="pandapower 3.5.2 / PYPOWER interior point", formulation="AC-OPF",
        cost_provenance=policy["cost_provenance"], solver_objective=float(net.res_cost))
    sidecar = Path(source).parent / "summary.json"
    if sidecar.exists():
        context = json.loads(sidecar.read_text())
        report["source_case_id"] = context.get("case_id")
        report["source_timestamp_utc"] = context.get("timestamp_utc")
    net.res_cost = float(net.res_cost)
    pp.to_json(net, output / "solved.json")
    for kind in ("bus", "line", "trafo", "gen", "ext_grid"):
        net[f"res_{kind}"].to_csv(output / f"{kind}_results.csv", index_label="model_index")
    ppc_json = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in ppc.items()}
    write_json(output / "ppc.json", ppc_json)
    write_json(output / "index_maps.json", {"bus": bus_indices, "branch": branch_indices, "gen": gen_indices})
    write_json(output / "report.json", report)
    if not report["feasible"]: raise RuntimeError(f"Solver returned a result that failed physical checks: {output / 'report.json'}")
    return report

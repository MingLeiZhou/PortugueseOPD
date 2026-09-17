"""GridSFM graph conversion, round-trip checks and independently audited inference."""
from __future__ import annotations

import json
from pathlib import Path
import time
import numpy as np

from pt60.dataset import digest
from .opf import write_json, physics_audit


def read_ppc(path):
    obj = json.loads(Path(path).read_text())
    for key in ("bus", "branch", "gen", "gencost"):
        obj[key] = np.array(obj[key], dtype=float)
    return obj


def export_graph(opf_dir, output):
    root = Path(opf_dir)
    report = json.loads((root / "report.json").read_text())
    if not report.get("feasible") or report["status"] != "LOCALLY_SOLVED":
        raise ValueError("Graph labels require an independently verified AC-OPF solution")
    p = read_ppc(root / "ppc.json")
    base = p["baseMVA"]
    buses, gen, branch = p["bus"], p["gen"], p["branch"]
    if np.any(abs(branch[:, 21:26]) > 1e-12):
        raise ValueError("GridSFM schema cannot represent asymmetric branches or branch shunt conductance")
    nodes = {"bus": buses[:, [9, 1, 12, 11]].tolist(), "generator": [], "load": [], "shunt": []}
    edges = {key: {"senders": [], "receivers": [], "features": []} for key in ("ac_line", "transformer")}
    solution_edges = {key: {"senders": [], "receivers": [], "features": []} for key in edges}
    order = {key: [] for key in edges}
    for i, g in enumerate(gen):
        c = p["gencost"][i]
        if int(c[0]) != 2 or int(c[3]) != 3:
            raise ValueError("Only quadratic polynomial generator costs are supported")
        # Initial Pg/Qg/Vg are constants, never the target OPF solution.
        nodes["generator"].append([g[6], 0., g[9] / base, g[8] / base, 0., g[4] / base,
            g[3] / base, 1., c[4] * base ** 2, c[5] * base, c[6]])
    edges["generator_link"] = {"senders": list(range(len(gen))), "receivers": gen[:, 0].astype(int).tolist()}
    for kind, columns in [("load", [2, 3]), ("shunt", [5, 4])]:
        indices = np.where(np.any(buses[:, columns] != 0, axis=1))[0]
        nodes[kind] = (buses[indices][:, columns] / base).tolist()
        edges[f"{kind}_link"] = {"senders": list(range(len(indices))), "receivers": indices.tolist()}
    for i, b in enumerate(branch):
        transformer = b[8] not in (0, 1) or b[9] != 0 or buses[int(b[0]), 9] != buses[int(b[1]), 9]
        kind = "transformer" if transformer else "ac_line"
        rate = b[5] / base
        angles = np.deg2rad(b[[11, 12]]).tolist()
        features = angles + ([b[2], b[3], rate, rate, rate, b[8] or 1., np.deg2rad(b[9]), b[4] / 2, b[4] / 2]
            if transformer else [b[4] / 2, b[4] / 2, b[2], b[3], rate, rate, rate])
        for table in (edges[kind], solution_edges[kind]):
            table["senders"].append(int(b[0])); table["receivers"].append(int(b[1]))
        edges[kind]["features"].append(features)
        # Upstream label order is to-P/Q followed by from-P/Q.
        solution_edges[kind]["features"].append((b[[15, 16, 13, 14]] / base).tolist())
        order[kind].append(i)
    obj = {"grid": {"nodes": nodes, "edges": edges, "context": [[[base]]]},
        "solution": {"nodes": {"bus": np.column_stack([np.deg2rad(buses[:, 8]), buses[:, 7]]).tolist(),
            "generator": (gen[:, [1, 2]] / base).tolist()}, "edges": solution_edges},
        "metadata": {"schema": "PT60_GRIDSFM_V1", "termination_status": "LOCALLY_SOLVED",
            "objective": report["objective"], "source_ppc_sha256": digest(root / "ppc.json"),
            "source_pf_sha256": report["input_sha256"], "branch_order": order,
            "source_case_id": report.get("source_case_id"), "source_timestamp_utc": report.get("source_timestamp_utc"),
            "load_semantics": "Net fixed PQ demand, including negative fixed injections",
            "initial_generator_features": "Pg=0,Qg=0,Vg=1; no OPF target leakage",
            "cost_provenance": report["cost_provenance"]}}
    rebuilt = graph_to_ppc(obj)
    # Audit exact admittance and objective equivalence before writing a graph.
    from pandapower.pypower.makeYbus import makeYbus
    y1 = makeYbus(base, p["bus"], p["branch"])[0]
    y2 = makeYbus(base, rebuilt["bus"], rebuilt["branch"])[0]
    delta = y1 - y2
    maximum = float(np.max(np.abs(delta.data), initial=0))
    if maximum > 1e-10: raise ValueError(f"Graph round-trip changes admittance: {maximum}")
    obj["metadata"]["roundtrip_admittance_max_error"] = maximum
    output = Path(output)
    if output.exists(): raise ValueError("Graph output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, obj)
    return obj["metadata"]


def graph_to_ppc(obj):
    """Reconstruct physical inputs solely from the graph, never from its labels."""
    grid = obj["grid"]; nodes = grid["nodes"]; edges = grid["edges"]
    base = float(grid["context"][0][0][0])
    bus = np.zeros((len(nodes["bus"]), 13)); bus[:, 0] = np.arange(len(bus)); bus[:, 7] = 1.
    bus[:, [9, 1, 12, 11]] = np.asarray(nodes["bus"])
    bus[:, 6] = 1.; bus[:, 10] = 1.
    for kind, cols in [("load", [2, 3]), ("shunt", [5, 4])]:
        for row, receiver in zip(nodes[kind], edges[kind + "_link"]["receivers"], strict=True):
            bus[receiver, cols] += np.asarray(row) * base
    gens = np.zeros((len(nodes["generator"]), 21)); costs = np.zeros((len(gens), 7))
    for i, (r, receiver) in enumerate(zip(nodes["generator"], edges["generator_link"]["receivers"], strict=True)):
        gens[i, :10] = [receiver, r[1] * base, r[4] * base, r[6] * base, r[5] * base, r[7], r[0], 1, r[3] * base, r[2] * base]
        costs[i] = [2, 0, 0, 3, r[8] / base ** 2, r[9] / base, r[10]]
    branches = []
    for kind in ("ac_line", "transformer"):
        e = edges[kind]
        for f, t, r in zip(e["senders"], e["receivers"], e["features"], strict=True):
            b = np.zeros(26); b[0:2] = [f, t]; b[10] = 1; b[11:13] = np.rad2deg(r[:2])
            if kind == "ac_line": b[2:10] = [r[4], r[5], r[2] + r[3], r[6] * base, r[7] * base, r[8] * base, 1, 0]
            else: b[2:10] = [r[2], r[3], r[9] + r[10], r[4] * base, r[5] * base, r[6] * base, r[7], np.rad2deg(r[8])]
            branches.append(b)
    return {"baseMVA": base, "version": "2", "bus": bus, "gen": gens, "gencost": costs, "branch": np.asarray(branches)}


def infer(graph_path, checkpoint, output, device="cpu"):
    import torch
    from gridsfm import load_model, predict
    torch.set_num_threads(2)
    obj = json.loads(Path(graph_path).read_text())
    output = Path(output)
    if output.exists(): raise ValueError("Inference output must be a new directory")
    model = load_model(str(checkpoint), device=device)
    tuned = "pt60_finetune" in torch.load(checkpoint, weights_only=True, map_location="cpu")["metadata"]
    start = time.perf_counter()
    result = predict(model, graph_path, fmt="pyg")
    elapsed = time.perf_counter() - start
    result = {key: value.tolist() if hasattr(value, "tolist") else value for key, value in result.items()}
    ppc = graph_to_ppc(obj)
    base = ppc["baseMVA"]
    voltage = np.asarray(result["V"]) * np.exp(1j * np.asarray(result["theta"]))
    generation = np.column_stack([result["Pg"], result["Qg"]]) * base
    report = physics_audit(ppc, voltage, generation)
    target_v = np.asarray(obj["solution"]["nodes"]["bus"])
    target_g = np.asarray(obj["solution"]["nodes"]["generator"])
    report.update(voltage_mae_pu=float(np.mean(abs(np.asarray(result["V"]) - target_v[:, 1]))),
        generator_p_mae_mw=float(np.mean(abs(generation[:, 0] - target_g[:, 0] * base))),
        generator_q_mae_mvar=float(np.mean(abs(generation[:, 1] - target_g[:, 1] * base))),
        reference_objective=obj["metadata"]["objective"], seconds=elapsed,
        predicted_feasibility_probability=result["feas"], checkpoint_sha256=digest(checkpoint),
        graph_sha256=digest(graph_path), mode="PT60_FINETUNED_GRIDSFM" if tuned else "ZERO_SHOT_PRETRAINED_GRIDSFM", device=device)
    report["objective_relative_error"] = abs(report["objective"] - report["reference_objective"]) / abs(report["reference_objective"])
    output.mkdir(parents=True)
    write_json(output / "prediction.json", result)
    write_json(output / "report.json", report)
    return report


def check_roundtrip(graph_path, output, prediction_path=None):
    from pandapower.pypower.opf import opf
    from pandapower.pypower.ppoption import ppoption
    from .opf import scipy_sparse_compat
    output = Path(output)
    if output.exists(): raise ValueError("Round-trip report already exists")
    obj = json.loads(Path(graph_path).read_text())
    ppc = graph_to_ppc(obj)
    if prediction_path:
        predicted = json.loads(Path(prediction_path).read_text())
        vm = np.asarray(predicted["V"])
        theta = np.asarray(predicted["theta"])
        pg = np.asarray(predicted["Pg"]) * ppc["baseMVA"]
        qg = np.asarray(predicted["Qg"]) * ppc["baseMVA"]
        if len(vm) != len(ppc["bus"]) or len(pg) != len(ppc["gen"]) or not all(np.isfinite(a).all() for a in [vm, theta, pg, qg]):
            raise ValueError("Prediction dimensions or values are invalid")
        ppc["bus"][:, 7] = np.clip(vm, ppc["bus"][:, 12], ppc["bus"][:, 11])
        ppc["bus"][:, 8] = np.rad2deg(theta - theta[np.where(ppc["bus"][:, 1] == 3)[0][0]])
        ppc["gen"][:, 1] = np.clip(pg, ppc["gen"][:, 9], ppc["gen"][:, 8])
        ppc["gen"][:, 2] = np.clip(qg, ppc["gen"][:, 4], ppc["gen"][:, 3])
    start = time.perf_counter()
    with scipy_sparse_compat():
        solved = opf(ppc, ppoption(VERBOSE=0, OUT_ALL=0, OPF_FLOW_LIM=0, PDIPM_MAX_IT=150,
            INIT="results" if prediction_path else "flat"))
    fallback = False
    if not solved["success"] and prediction_path:
        fallback = True
        with scipy_sparse_compat():
            solved = opf(graph_to_ppc(obj), ppoption(VERBOSE=0, OUT_ALL=0, OPF_FLOW_LIM=0, PDIPM_MAX_IT=150, INIT="flat"))
    if not solved["success"]: raise RuntimeError("Cold OPF on the reconstructed graph failed")
    report = physics_audit(solved)
    reference = obj["metadata"]["objective"]
    report.update(seconds=time.perf_counter() - start, reference_objective=reference,
        objective_relative_difference=abs(report["objective"] - reference) / abs(reference),
        initialization="NEURAL_PREDICTION_BOUNDED_INITIAL_GUESS" if prediction_path else "FLAT_ANGLES_MIDPOINT_BOUNDS_NO_SOLUTION_LABELS")
    report["fallback_to_cold_start"] = fallback
    report["roundtrip_pass"] = report["feasible"] and report["objective_relative_difference"] < 1e-4
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    if not report["roundtrip_pass"]: raise RuntimeError(f"Round-trip validation failed: {output}")
    return report

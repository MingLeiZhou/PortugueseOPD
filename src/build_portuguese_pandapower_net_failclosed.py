"""Build a Portuguese pandapower net only after ACPF readiness gates pass."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp


READY = ROOT / "data" / "processed" / "acpf_ready"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
LOG_PATH = READY / "pt_acpf_net_build_log.json"


ALLOWED_READINESS_FOR_BUILD = {"AC_PF_BENCHMARK_PLUMBING_READY", "AC_PF_SCENARIO_READY", "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY", "SOURCE_BACKED_READY"}


def read(name: str) -> pd.DataFrame:
    return pd.read_csv(READY / name)


def require_gate_pass() -> tuple[str, pd.DataFrame]:
    gate = read("pt_acpf_readiness_gate.csv")
    overall = gate[gate["check_name"] == "AC_PF_RUN_ALLOWED"]
    if overall.empty:
        raise RuntimeError("Fail-closed: readiness gate has no AC_PF_RUN_ALLOWED row.")
    status = str(overall.iloc[0]["status"])
    readiness = str(overall.iloc[0]["readiness_status"])
    if status != "PASS" or readiness not in ALLOWED_READINESS_FOR_BUILD:
        blockers = gate[(gate["critical"]) & (gate["status"] != "PASS")]
        raise RuntimeError(f"Fail-closed: ACPF readiness gate failed for readiness={readiness}. Blockers: {blockers.to_dict('records')}")
    return readiness, gate


def f(value: Any, default: float | None = None) -> float | None:
    if pd.isna(value):
        return default
    try:
        return float(value)
    except Exception:
        return default


def main() -> None:
    readiness, gate = require_gate_pass()
    bus = read("pt_bus_table_acpf.csv")
    line = read("pt_line_table_acpf.csv")
    trafo = read("pt_trafo_table_acpf.csv")
    load = read("pt_load_table_acpf.csv")
    sgen = read("pt_sgen_table_acpf.csv")
    ext_grid = read("pt_ext_grid_table_acpf.csv")
    scenarios = read("pt_acpf_scenario_definitions.csv")

    net = pp.create_empty_network(name=f"Portuguese E-REDES ACPF dry-run {readiness}", sn_mva=100.0)
    bus_lookup: dict[str, int] = {}

    for _, row in bus.iterrows():
        idx = pp.create_bus(
            net,
            vn_kv=f(row["voltage_kv"]),
            name=str(row["bus_id"]),
            type="b",
            zone=str(row.get("zone", "")) if not pd.isna(row.get("zone", "")) else None,
            in_service=bool(row["in_service"]),
            min_vm_pu=f(row.get("min_vm_pu"), 0.95),
            max_vm_pu=f(row.get("max_vm_pu"), 1.05),
        )
        bus_lookup[str(row["bus_id"])] = idx

    for _, row in line[line["in_service"]].iterrows():
        pp.create_line_from_parameters(
            net,
            from_bus=bus_lookup[str(row["from_bus"])],
            to_bus=bus_lookup[str(row["to_bus"])],
            length_km=f(row["length_km"]),
            r_ohm_per_km=f(row["r_ohm_per_km"]),
            x_ohm_per_km=f(row["x_ohm_per_km"]),
            c_nf_per_km=f(row["c_nf_per_km"]),
            max_i_ka=f(row["max_i_ka"]),
            name=str(row["line_id"]),
            in_service=True,
            type=str(row.get("asset_type", "")),
        )

    for _, row in trafo[trafo["in_service"]].iterrows():
        pp.create_transformer_from_parameters(
            net,
            hv_bus=bus_lookup[str(row["hv_bus"])],
            lv_bus=bus_lookup[str(row["lv_bus"])],
            sn_mva=f(row["sn_mva"]),
            vn_hv_kv=f(row["hv_kv"]),
            vn_lv_kv=f(row["lv_kv"]),
            vk_percent=f(row["vk_percent"]),
            vkr_percent=f(row["vkr_percent"]),
            pfe_kw=f(row.get("pfe_kw"), 0.0),
            i0_percent=f(row.get("i0_percent"), 0.0),
            shift_degree=0.0,
            tap_side=str(row.get("tap_side", "hv")),
            tap_neutral=int(f(row.get("tap_neutral"), 0)),
            tap_min=int(f(row.get("tap_min"), -11)),
            tap_max=int(f(row.get("tap_max"), 11)),
            tap_step_percent=f(row.get("tap_step_percent"), 1.5),
            tap_pos=int(f(row.get("tap_pos"), 0)),
            name=str(row["trafo_id"]),
            in_service=True,
        )

    for _, row in load[load["in_service"]].iterrows():
        pp.create_load(
            net,
            bus=bus_lookup[str(row["bus_id"])],
            p_mw=f(row["p_mw"]),
            q_mvar=f(row["q_mvar"]),
            name=str(row["load_id"]),
            in_service=True,
        )

    if len(sgen):
        for _, row in sgen[sgen.get("in_service", False)].iterrows():
            pp.create_sgen(
                net,
                bus=bus_lookup[str(row["bus_id"])],
                p_mw=f(row["p_mw"]),
                q_mvar=f(row["q_mvar"], 0.0),
                name=str(row["sgen_id"]),
                in_service=True,
            )

    for _, row in ext_grid[ext_grid["in_service"]].iterrows():
        pp.create_ext_grid(
            net,
            bus=bus_lookup[str(row["bus_id"])],
            vm_pu=f(row["vm_pu"], 1.0),
            va_degree=f(row["va_degree"], 0.0),
            name=str(row["ext_grid_id"]),
            in_service=True,
            s_sc_max_mva=f(row.get("s_sc_max_mva")),
            s_sc_min_mva=f(row.get("s_sc_min_mva")),
        )

    net.user_pf_options = {
        "readiness_status": readiness,
        "scenario_id": str(scenarios.iloc[0]["scenario_id"]) if len(scenarios) else "",
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "provenance_policy": "best_available_multilingual_diagnostic" if readiness == "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY" else "benchmark_plumbing",
    }

    pp.to_json(net, NET_PATH)
    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": readiness,
        "net_path": str(NET_PATH),
        "pandapower_version": pp.__version__,
        "net_counts": {
            "bus": int(len(net.bus)),
            "line": int(len(net.line)),
            "trafo": int(len(net.trafo)),
            "load": int(len(net.load)),
            "sgen": int(len(net.sgen)),
            "ext_grid": int(len(net.ext_grid)),
        },
        "active_counts": {
            "bus": int(net.bus["in_service"].sum()),
            "line": int(net.line["in_service"].sum()),
            "trafo": int(net.trafo["in_service"].sum()),
            "load": int(net.load["in_service"].sum()),
            "sgen": int(net.sgen["in_service"].sum()) if len(net.sgen) else 0,
            "ext_grid": int(net.ext_grid["in_service"].sum()),
        },
        "gate_status": gate.to_dict("records"),
    }
    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(log, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

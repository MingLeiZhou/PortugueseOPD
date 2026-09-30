#!/usr/bin/env python3
"""Transmission-distribution (T&D) co-simulation at one timestamp.

Fixed-point coupling between the CORE-3783 high-voltage model and the 617
all-voltage root slices:

1. every root slice is solved at the same timestamp with its 60-kV source
   voltage taken from the current HV solution;
2. the E-REDES station loads at the root buses of the HV model are replaced by
   the P and Q drawn by the root slices (no double counting);
3. the HV model is re-solved and the new 60-kV voltages are fed back.

Iterations stop when the HV voltages at the root buses and the root-slice
source powers change by less than the tolerances. The staging database is
opened read-only; results go to a new output directory.
"""
from __future__ import annotations

import argparse, concurrent.futures as cf, json, os, platform, subprocess, sys, time
from pathlib import Path

import duckdb, numpy as np, pandas as pd, pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
SOLVER = ROOT / "src/validate_equivalent_island_powerflow.py"
HV = (ROOT / "portuguese_hv_network/outputs/annual_nminus1_panel_v2/models/"
      "PT60_ANNUAL_PEAK_LOAD_20260115_1215_+0000_solved.json")
TS = "2026-01-15T12:15:00+00:00"
OLTC = False


def solve_root(task):
    root, vm, ts, out, db, deadline = task
    case = out / root.replace(":", "_")
    case.mkdir(parents=True, exist_ok=True)
    cached = case / "validation.json"
    if cached.exists():  # resume: reuse a finished case with the same source voltage
        try:
            rep = json.loads(cached.read_text()); c = rep.get("checks", {})
            if rep.get("timestamp_utc") == ts and abs(c.get("source_vm_pu", -1) - round(vm, 8)) < 1e-9:
                return _row(root, vm, 0, rep, "")
        except Exception:
            pass
    if time.time() > deadline:
        return dict(mv_root_bus=root, source_vm_pu=vm, result="NOT_RUN_TIME_BUDGET")
    cmd = [sys.executable, str(SOLVER), "--database", str(db), "--mv-root-bus", root,
           "--timestamp-utc", ts, "--output", str(case), "--no-persist-results",
           "--skip-faults", "--source-vm-pu", f"{vm:.8f}"] + (["--oltc"] if OLTC else [])
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    rep = {}
    try:
        rep = json.loads((case / "validation.json").read_text())
    except Exception:
        pass
    return _row(root, vm, proc.returncode, rep, proc.stderr[-300:] if proc.returncode else "")


def _row(root, vm, returncode, rep, stderr_tail):
    c = rep.get("checks", {})
    return dict(mv_root_bus=root, source_vm_pu=vm, returncode=returncode,
                result=rep.get("result", "NO_REPORT"), errors="; ".join(rep.get("errors", [])),
                converged=bool(c.get("power_flow_converged", False)),
                p_mw=c.get("source_p_mw", np.nan), q_mvar=c.get("source_q_mvar", np.nan),
                lv_vmin=c.get("lv_voltage_min_pu", np.nan), mv_vmin=c.get("mv_voltage_min_pu", np.nan),
                max_line=c.get("max_line_loading_percent", np.nan),
                max_trafo=c.get("max_transformer_loading_percent", np.nan),
                violations=(c.get("voltage_violation_bus_count", 0) + c.get("line_overload_count", 0)
                            + c.get("transformer_overload_count", 0)),
                stderr_tail=stderr_tail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, default=DB)
    ap.add_argument("--hv-model", type=Path, default=HV)
    ap.add_argument("--timestamp-utc", default=TS)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-iter", type=int, default=6)
    ap.add_argument("--tol-v", type=float, default=1e-4, help="max |dV| at root buses (p.u.)")
    ap.add_argument("--tol-p", type=float, default=0.1, help="max |dP| per root slice (MW)")
    ap.add_argument("--limit", type=int, default=0, help="debug: only the first N roots")
    ap.add_argument("--time-budget", type=float, default=0,
                    help="seconds; stop after this and exit 3 so the run can be resumed (0 = no limit)")
    ap.add_argument("--oltc", action="store_true", help="enable 60-kV/MV OLTC control in root slices")
    a = ap.parse_args()
    global OLTC
    OLTC = a.oltc
    out = a.output or ROOT / f"output/all_voltage/td_cosim/{a.timestamp_utc[:16].replace(':','').replace('-','')}"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    deadline = t0 + a.time_budget if a.time_budget else float("inf")
    log = lambda m: print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)

    with duckdb.connect(str(a.database), read_only=True) as con:
        roots = con.execute("SELECT mv_root_bus, hv_bus FROM parameter.mv_root_transformer_parameters "
                            "ORDER BY mv_root_bus").fetchdf()
    if a.limit:
        roots = roots.head(a.limit)

    net = pp.from_json(str(a.hv_model))
    pp.runpp(net, calculate_voltage_angles=True, numba=False)
    if not net.converged:
        raise SystemExit("standalone HV case did not converge")
    name2idx = pd.Series(net.bus.index, index=net.bus.name)
    roots["bus_idx"] = roots.hv_bus.map(name2idx)
    if roots.bus_idx.isna().any():
        raise SystemExit("unmapped HV buses: " + ", ".join(roots.hv_bus[roots.bus_idx.isna()]))
    root_buses = sorted(roots.bus_idx.astype(int).unique())
    base_vm = net.res_bus.vm_pu.copy(); base_line = net.res_line.loading_percent.copy()
    base_trafo = net.res_trafo.loading_percent.copy()
    base_ext = float(net.res_ext_grid.p_mw.sum())
    # E-REDES station loads at root buses are replaced; other loads (e.g. storage charging) stay.
    repl = net.load.bus.isin(root_buses) & net.load.name.astype(str).str.startswith("LOAD:EREDES:")
    repl_idx = list(net.load.index[repl])
    orig = net.load.loc[repl_idx].groupby("bus")[["p_mw", "q_mvar"]].sum()
    log(f"HV standalone solved; {len(roots)} roots on {len(root_buses)} HV buses; "
        f"replacing {int(repl.sum())} station loads = {orig.p_mw.sum():.1f} MW")

    td_idx = {int(b): pp.create_load(net, bus=int(b), p_mw=0.0, q_mvar=0.0, name="TD_ROOT_SUM:" + str(net.bus.at[int(b), "name"]))
              for b in root_buses}
    net.load.loc[repl_idx, "in_service"] = False

    vm = roots.bus_idx.map(base_vm).to_numpy()
    dead = np.isnan(vm)
    if dead.any():
        log(f"{int(dead.sum())} root(s) on de-energized HV buses in the CORE case; source set to 1.0 p.u.: "
            + ", ".join(roots.mv_root_bus[dead]))
    prev_p = None; prev_hv_vm = base_vm.loc[root_buses].to_numpy(); hist = []
    for k in range(1, a.max_iter + 1):
        vm = np.round(np.where(np.isnan(vm), 1.0, vm), 8)
        tasks = [(r, float(v), a.timestamp_utc, out / f"iter{k}" / "roots", a.database, deadline)
                 for r, v in zip(roots.mv_root_bus, vm)]
        with cf.ThreadPoolExecutor(a.workers) as ex:
            res = pd.DataFrame(list(ex.map(solve_root, tasks)))
        pending = int((res.result == "NOT_RUN_TIME_BUDGET").sum())
        if pending:
            log(f"iter {k}: {len(res)-pending}/{len(res)} roots done; time budget reached, rerun to resume")
            return 3
        res = res.merge(roots[["mv_root_bus", "hv_bus", "bus_idx"]], on="mv_root_bus")
        res.to_csv(out / f"roots_iter{k}.csv", index=False)
        failed = int((~res.converged).sum())
        agg = res[res.converged].groupby("bus_idx")[["p_mw", "q_mvar"]].sum()
        for b, li in td_idx.items():
            if b in agg.index:
                net.load.at[li, "p_mw"] = agg.at[b, "p_mw"]; net.load.at[li, "q_mvar"] = agg.at[b, "q_mvar"]
            else:  # all roots at this bus failed: keep the original station load
                net.load.at[li, "p_mw"] = orig.p_mw.get(b, 0.0); net.load.at[li, "q_mvar"] = orig.q_mvar.get(b, 0.0)
        pp.runpp(net, calculate_voltage_angles=True, numba=False)
        hv_vm = net.res_bus.vm_pu.loc[root_buses].to_numpy()
        dv = float(np.nanmax(np.abs(hv_vm - prev_hv_vm)))
        dp = float(np.nanmax(np.abs(res.p_mw.to_numpy() - prev_p))) if prev_p is not None else np.nan
        hist.append(dict(iteration=k, roots_failed=failed, hv_converged=bool(net.converged),
                         td_load_p_mw=float(agg.p_mw.sum()), td_load_q_mvar=float(agg.q_mvar.sum()),
                         ext_grid_p_mw=float(net.res_ext_grid.p_mw.sum()), max_dv_root_bus_pu=dv,
                         max_dp_root_mw=dp, root_lv_vmin=float(res.lv_vmin.min()),
                         root_mv_vmin=float(res.mv_vmin.min()), root_violations=int(res.violations.sum())))
        pd.DataFrame(hist).to_csv(out / "iterations.csv", index=False)
        log(f"iter {k}: failed={failed} TD P={agg.p_mw.sum():.1f} MW dV={dv:.2e} dP={dp:.3g} MW "
            f"HV conv={net.converged}")
        if not net.converged:
            break
        if prev_p is not None and dv < a.tol_v and dp < a.tol_p:
            break
        prev_p = res.p_mw.to_numpy(); prev_hv_vm = hv_vm
        vm = roots.bus_idx.map(net.res_bus.vm_pu).to_numpy()

    busc = pd.DataFrame({"bus_id": net.bus.name, "vn_kv": net.bus.vn_kv,
                         "vm_standalone": base_vm, "vm_coupled": net.res_bus.vm_pu})
    busc["is_root_bus"] = busc.index.isin(root_buses)
    busc["station_load_p_mw"] = pd.Series(orig.p_mw).reindex(busc.index)
    busc["td_load_p_mw"] = pd.Series({b: net.load.at[li, "p_mw"] for b, li in td_idx.items()}).reindex(busc.index)
    busc.to_csv(out / "hv_bus_compare.csv", index=False)
    pd.DataFrame({"line_id": net.line.name, "vn_kv": net.bus.vn_kv.loc[net.line.from_bus].to_numpy(),
                  "loading_standalone": base_line, "loading_coupled": net.res_line.loading_percent}
                 ).to_csv(out / "hv_line_compare.csv", index=False)
    summary = dict(
        timestamp_utc=a.timestamp_utc, hv_model=str(a.hv_model.relative_to(ROOT)),
        roots=len(roots), root_hv_buses=len(root_buses), iterations=len(hist),
        converged=bool(hist and hist[-1]["hv_converged"] and hist[-1]["max_dv_root_bus_pu"] < a.tol_v
                       and (hist[-1]["max_dp_root_mw"] or 1e9) < a.tol_p),
        tolerances=dict(v_pu=a.tol_v, p_mw=a.tol_p),
        replaced_station_load_p_mw=float(orig.p_mw.sum()), replaced_station_load_q_mvar=float(orig.q_mvar.sum()),
        final=hist[-1] if hist else None, ext_grid_p_standalone_mw=base_ext,
        hv_vm_change_root_buses=dict(max_abs=float((busc.vm_coupled - busc.vm_standalone)[busc.is_root_bus].abs().max()),
                                     mean=float((busc.vm_coupled - busc.vm_standalone)[busc.is_root_bus].mean())),
        hv_line_loading_max=dict(standalone=float(base_line.max()), coupled=float(net.res_line.loading_percent.max())),
        hv_lines_over_100=dict(standalone=int((base_line > 100).sum()), coupled=int((net.res_line.loading_percent > 100).sum())),
        hv_trafo_loading_max=dict(standalone=float(base_trafo.max()), coupled=float(net.res_trafo.loading_percent.max())),
        runtime_s=round(time.time() - t0, 1),
        software=dict(python=platform.python_version(), pandapower=pp.__version__),
        limitations=["Root slices are balanced positive-sequence equivalents; four-wire LV is not coupled",
                     "HV generator dispatch is kept from the CORE case; the slack absorbs the load difference",
                     "Only E-REDES station loads at root buses are replaced; other HV loads are unchanged"])
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    log("done: " + json.dumps(summary["final"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

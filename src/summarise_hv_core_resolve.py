#!/usr/bin/env python3
"""Summarise the corrected HV core re-solve (all months) and compare with the frozen CORE-3783 release.
Usage: python src/summarise_hv_core_resolve.py OUT_DIR   -> output/simpt_power_release/hv_core_resolve_summary.json"""
import hashlib, json, sys
from pathlib import Path
import duckdb
ROOT = Path(__file__).resolve().parents[1]; OUT = Path(sys.argv[1])
dest = ROOT / "output/simpt_power_release"; dest.mkdir(parents=True, exist_ok=True)
Q = """select count(*) cases, count(*) filter (where status='COMPLETE') complete, count(*) filter (where converged) converged,
       count(*) filter (where status<>'COMPLETE') failed, min(vm_pu_min) vmin, max(vm_pu_max) vmax,
       max(maximum_line_loading_percent) max_line, count(*) filter (where lines_over_100_percent>0) cases_with_overload,
       max(lines_over_100_percent) max_lines_over_100, avg(model_losses_percent_of_load) mean_loss_pct,
       avg(abs(model_net_import_mw-observed_net_import_mw)) mae_net_import_mw, max(maximum_transformer_loading_percent) max_trafo
       from monthly_model.cases"""
months, tot = {}, None
for d in sorted(p for p in OUT.iterdir() if p.is_dir() and p.name[:2] == "20"):
    f = d / f"pt60_models_{d.name}.compact.duckdb"
    if not f.exists(): continue
    c = duckdb.connect(str(f), read_only=True); r = c.sql(Q).df().iloc[0].to_dict(); c.close()
    months[d.name] = {k: (float(v) if v is not None else None) for k, v in r.items()}
keys_sum = ("cases", "complete", "converged", "failed", "cases_with_overload")
total = {k: int(sum(m[k] for m in months.values())) for k in keys_sum}
total.update({"vmin": min(m["vmin"] for m in months.values()), "vmax": max(m["vmax"] for m in months.values()),
              "max_line_loading_pct": max(m["max_line"] for m in months.values()), "max_trafo_loading_pct": max(m["max_trafo"] for m in months.values()),
              "max_lines_over_100": max(m["max_lines_over_100"] for m in months.values()),
              "mean_loss_pct_of_load": sum(m["mean_loss_pct"] * m["cases"] for m in months.values()) / total["cases"]})
# national peak-load case (paper Section 3.1 / Fig. 6b refer to the national peak)
peak = None
for d in sorted(p for p in OUT.iterdir() if p.is_dir() and p.name[:2] == "20"):
    f = d / f"pt60_models_{d.name}.compact.duckdb"
    if not f.exists(): continue
    c = duckdb.connect(str(f), read_only=True)
    r = c.sql("""select case_id, timestamp_utc::varchar ts, observed_load_mw, vm_pu_min, vm_pu_max, maximum_line_loading_percent,
                 lines_over_100_percent, maximum_transformer_loading_percent, model_losses_percent_of_load
                 from monthly_model.cases where status='COMPLETE' order by observed_load_mw desc limit 1""").df().iloc[0].to_dict(); c.close()
    if peak is None or r["observed_load_mw"] > peak["observed_load_mw"]: peak = r
total["national_peak_case"] = {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in peak.items()}
model = ROOT / "work/hv_ren_fix_2026-09-29/outputs/model/portuguese_hv_candidate.json"
s = {"model_id": "CORE-3787-REN", "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
     "source": "REN Caracterização da RNT 31-12-2025 Annex B/D reconciliation", "output_dir": str(OUT), "total": total, "months": months}
(dest / "hv_core_resolve_summary.json").write_text(json.dumps(s, indent=2)); print(json.dumps(s["total"], indent=2))

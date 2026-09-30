#!/usr/bin/env python3
"""Aggregate per-line overloads (>100 %) over all monthly CORE-3787-REN results of the frozen release.
Usage: python src/audit_hv_hotspots.py MONTH_DB [MONTH_DB ...]  -> appends to output/simpt_power_release/hotspot_audit/line_overloads_by_month.csv"""
import sys, duckdb, pandas as pd
from pathlib import Path
out = Path(__file__).resolve().parents[1] / "output/simpt_power_release/hotspot_audit/line_overloads_by_month.csv"
rows = []
for f in sys.argv[1:]:
    c = duckdb.connect(f, read_only=True)
    m = c.sql("select month from monthly_model.run_manifest").fetchone()[0]
    x = c.sql("""with u as (select unnest(line_loading_percent) ld, generate_subscripts(line_loading_percent,1) pos from monthly_model.state_arrays)
                 select pos, count(*) n_cases, count(*) filter (where ld>100) n_over, count(*) filter (where ld>80) n_over80, max(ld) max_loading, quantile_cont(ld,0.99) p99 from u group by pos
                 having max(ld) > 80""").df()
    lo = c.sql("select position pos, line_id from monthly_model.line_order").df()
    rows.append(x.merge(lo, on="pos").assign(month=m)); c.close()
df = pd.concat(rows); df.to_csv(out, mode="a", header=not out.exists(), index=False); print(len(df))

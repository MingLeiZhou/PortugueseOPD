#!/usr/bin/env python3
"""Add factorized 15-minute PTD loads to the all-voltage staging database.

Measured E-REDES substation energy is split into a capacity-bounded LV PTD
share and an inferred MV residual. PTDs without a measured station use a
nearby public station only for *shape*, scaled to their assumed peak proxy.
The view is computed on demand rather than materializing billions of rows.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def xy(lat: float, lon: float) -> tuple[float, float]:
    return lon * 111.32 * math.cos(math.radians(39.5)), lat * 111.32


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--database", type=Path, default=STAGING)
    args = parser.parse_args()
    quoted_base = str(args.base).replace("'", "''")
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute(f"ATTACH '{quoted_base}' AS frozen (READ_ONLY)")
        con.execute("""CREATE OR REPLACE TABLE operating.station_15min AS
            WITH source_energy AS (
                SELECT codigo_subestacao AS station_code,
                       CAST(data AS VARCHAR) AS source_date, hora AS source_hour,
                       sum(energia) AS energy_kwh, count(*) AS raw_record_count
                FROM frozen.main.eredes_load GROUP BY 1, 2, 3
            )
            SELECT e.station_code, c.timestamp_utc, e.source_date, e.source_hour,
                   e.energy_kwh, e.energy_kwh / 250.0 AS p_mw,
                   e.raw_record_count, 'PUBLIC_EREDES_STATION_AGGREGATE' AS evidence_status
            FROM source_energy e
            JOIN frozen.main.interval_calendar c
              ON e.source_date=c.eredes_source_date AND e.source_hour=c.eredes_source_hour
            WHERE c.eredes_alignment_status='UNIQUE'""")
        source_codes = {row[0] for row in con.execute("SELECT DISTINCT station_code FROM operating.station_15min").fetchall()}
        stations = con.execute("""SELECT facility_code, avg(lat), avg(lon) FROM equivalent.base_buses
            WHERE voltage_kv=60 AND facility_code IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL
            GROUP BY 1""").fetchall()
        station_locations = {str(code): (float(lat), float(lon)) for code, lat, lon in stations}
        fallback_codes = sorted(source_codes & station_locations.keys())
        if not fallback_codes:
            raise ValueError("No E-REDES station profiles can be located on 60 kV buses")
        tree = cKDTree(np.asarray([xy(*station_locations[code]) for code in fallback_codes]))
        ptd_rows = con.execute("""SELECT p.ptd_code, p.hv_bus, p.peak_load_proxy_mva,
                                      b.facility_code, b.lat, b.lon
            FROM equivalent.ptd_connections p
            JOIN equivalent.base_buses b ON p.hv_bus=b.bus_id""").fetchall()
        if len(ptd_rows) != con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]:
            raise ValueError("PTD-to-HV bus join lost rows")
        direct_weight = defaultdict(float)
        for _, _, proxy, code, _, _ in ptd_rows:
            if code in source_codes:
                direct_weight[code] += max(float(proxy or 0), 0.0)
        peak_by_source = dict(con.execute("""SELECT station_code, max(p_mw)
            FROM operating.station_15min GROUP BY 1""").fetchall())
        all_utc = [r[0] for r in con.execute("""SELECT DISTINCT timestamp_utc
            FROM frozen.main.interval_calendar ORDER BY 1""").fetchall()]
        observed_shape = {t:(count,total,peak_sum) for t,count,total,peak_sum in con.execute("""SELECT s.timestamp_utc,count(*),sum(s.p_mw),
                   sum(p.peak_mw)
            FROM operating.station_15min s
            JOIN (SELECT station_code,max(p_mw) AS peak_mw
                  FROM operating.station_15min GROUP BY 1) p USING(station_code)
            GROUP BY 1""").fetchall()}
        observed_times = np.asarray([datetime.fromisoformat(t).timestamp()
                                     for t in all_utc if t in observed_shape])
        observed_factors = np.asarray([
            observed_shape[t][1]/observed_shape[t][2]
            if observed_shape[t][2]>0 else 0.0
            for t in all_utc if t in observed_shape])
        all_times = np.asarray([datetime.fromisoformat(t).timestamp() for t in all_utc])
        filled_factors = np.clip(np.interp(all_times,observed_times,observed_factors),0,1)
        con.execute("""CREATE OR REPLACE TABLE operating.national_load_shape_15min (
            timestamp_utc VARCHAR, observed_station_count INTEGER,
            observed_total_mw DOUBLE, shape_factor DOUBLE, evidence_status VARCHAR)""")
        con.executemany("INSERT INTO operating.national_load_shape_15min VALUES (?,?,?,?,?)",[
            (t,observed_shape[t][0],observed_shape[t][1],float(filled_factors[i]),
             'PUBLIC_STATIONS_NORMALIZED_SHAPE') if t in observed_shape else
            (t,0,None,float(filled_factors[i]),'SYNTHETIC_DST_GAP_INTERPOLATION')
            for i,t in enumerate(all_utc)])
        con.execute("""CREATE OR REPLACE VIEW operating.station_15min_complete AS
            WITH station_peaks AS (
                SELECT station_code,max(p_mw) AS station_peak_mw
                FROM operating.station_15min GROUP BY 1
            )
            SELECT p.station_code,n.timestamp_utc,
                   coalesce(s.p_mw,p.station_peak_mw*n.shape_factor) AS p_mw,
                   CASE WHEN s.station_code IS NOT NULL
                        THEN 'PUBLIC_EREDES_STATION_AGGREGATE'
                        WHEN n.evidence_status='SYNTHETIC_DST_GAP_INTERPOLATION'
                        THEN 'IMPUTED_DST_GAP_NATIONAL_SHAPE'
                        ELSE 'IMPUTED_MISSING_STATION_NATIONAL_SHAPE' END AS data_status
            FROM station_peaks p CROSS JOIN operating.national_load_shape_15min n
            LEFT JOIN operating.station_15min s
              ON s.station_code=p.station_code AND s.timestamp_utc=n.timestamp_utc""")
        station_lv_fraction = {
            code: min(1.0, 0.97*total_mva/peak_by_source[code])
            if peak_by_source.get(code,0)>0 else 0.0
            for code,total_mva in direct_weight.items()
        }
        con.execute("""CREATE OR REPLACE TABLE operating.station_lv_split (
            station_code VARCHAR, observed_station_peak_mw DOUBLE,
            ptd_peak_proxy_sum_mw DOUBLE, lv_fraction DOUBLE,
            evidence_status VARCHAR)""")
        con.executemany("INSERT INTO operating.station_lv_split VALUES (?,?,?,?,?)", [
            (code,peak_by_source[code],0.97*total_mva,station_lv_fraction[code],
             'CAPACITY_BOUNDED_LV_SHARE_MV_RESIDUAL_SCENARIO')
            for code,total_mva in direct_weight.items()])
        allocations = []
        counts: Counter = Counter()
        for code, hv_bus, proxy, station_code, lat, lon in ptd_rows:
            peak_proxy_mw = max(float(proxy or 0) * 0.97, 0.0)
            if station_code in source_codes:
                profile = station_code
                if direct_weight[station_code] <= 0:
                    raise ValueError(f"No positive-capacity PTD can receive measured load for {station_code}")
                factor = max(float(proxy or 0), 0.0) / direct_weight[station_code]
                lv_fraction = station_lv_fraction[station_code]
                status = "DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL"
            else:
                _, index = tree.query(xy(float(lat), float(lon)))
                profile = fallback_codes[int(index)]
                peak = peak_by_source.get(profile) or 0.0
                factor = peak_proxy_mw / peak if peak > 0 else 0.0
                lv_fraction = 1.0
                status = "FALLBACK_PROFILE_SHAPE_SCALED_TO_PTD_PEAK_PROXY"
            allocations.append((code, hv_bus, station_code, profile, peak_proxy_mw,
                                factor, lv_fraction, status))
            counts[status] += 1
        con.execute("""CREATE OR REPLACE TABLE operating.ptd_allocations (
            ptd_code VARCHAR, hv_bus VARCHAR, assigned_station_code VARCHAR,
            profile_station_code VARCHAR, peak_proxy_mw DOUBLE, factor DOUBLE,
            station_lv_fraction DOUBLE,
            allocation_status VARCHAR)""")
        con.executemany("INSERT INTO operating.ptd_allocations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", allocations)
        con.execute("""CREATE OR REPLACE VIEW operating.ptd_load_15min AS
            SELECT a.ptd_code, s.timestamp_utc,
                   s.p_mw * a.factor * a.station_lv_fraction AS p_mw,
                   s.p_mw * a.factor * a.station_lv_fraction * tan(acos(0.97)) AS q_mvar,
                   a.assigned_station_code, a.profile_station_code, a.allocation_status,
                   s.data_status AS profile_data_status
            FROM operating.ptd_allocations a
            JOIN operating.station_15min_complete s ON a.profile_station_code=s.station_code""")
        con.execute("""CREATE OR REPLACE VIEW operating.station_mv_residual_15min AS
            SELECT s.station_code,s.timestamp_utc,
                   s.p_mw*(1-coalesce(f.lv_fraction,0)) AS p_mw,
                   s.p_mw*(1-coalesce(f.lv_fraction,0))*tan(acos(0.97)) AS q_mvar,
                   'INFERRED_MV_RESIDUAL_NOT_OBSERVED_SEPARATELY' AS evidence_status,
                   s.data_status AS profile_data_status
            FROM operating.station_15min_complete s
            LEFT JOIN operating.station_lv_split f USING(station_code)""")
        weight_errors = con.execute("""SELECT count(*) FROM (
            SELECT assigned_station_code, abs(sum(factor)-1) AS residual
            FROM operating.ptd_allocations
            WHERE allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
            GROUP BY 1 HAVING abs(sum(factor)-1)>1e-9)""").fetchone()[0]
        sample = con.execute("""WITH selected AS (
            SELECT station_code, timestamp_utc, p_mw FROM operating.station_15min
            WHERE station_code IN (SELECT DISTINCT assigned_station_code FROM operating.ptd_allocations
                                   WHERE allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL')
            ORDER BY timestamp_utc, station_code LIMIT 5
        ) SELECT max(abs(s.p_mw-coalesce(x.allocated_p_mw,0)-
                         s.p_mw*(1-f.lv_fraction)))
          FROM selected s LEFT JOIN LATERAL (
            SELECT sum(a.factor*a.station_lv_fraction*s.p_mw) AS allocated_p_mw
            FROM operating.ptd_allocations a
            WHERE a.assigned_station_code=s.station_code
              AND a.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
          ) x ON true
          JOIN operating.station_lv_split f USING(station_code)""").fetchone()[0]
        max_ptd_peak_violation = con.execute("""SELECT max(greatest(0,
                p.peak_mw*a.factor*a.station_lv_fraction-a.peak_proxy_mw))
            FROM operating.ptd_allocations a
            JOIN (SELECT station_code,max(p_mw) peak_mw FROM operating.station_15min
                  GROUP BY 1) p ON a.assigned_station_code=p.station_code
            WHERE a.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'""").fetchone()[0]
        checks = {
            "station_15min_rows": con.execute("SELECT count(*) FROM operating.station_15min").fetchone()[0],
            "distinct_utc_intervals": con.execute("SELECT count(DISTINCT timestamp_utc) FROM operating.station_15min").fetchone()[0],
            "complete_utc_intervals":len(all_utc),
            "imputed_dst_intervals":sum(t not in observed_shape for t in all_utc),
            "station_code_count":len(peak_by_source),
            "imputed_station_intervals":len(peak_by_source)*len(all_utc)-
                                        con.execute("SELECT count(*) FROM operating.station_15min").fetchone()[0],
            "ptd_allocations": len(allocations),
            "direct_ptds": counts["DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL"],
            "fallback_ptds": counts["FALLBACK_PROFILE_SHAPE_SCALED_TO_PTD_PEAK_PROXY"],
            "direct_stations_with_mv_residual":con.execute("""SELECT count(*)
                FROM operating.station_lv_split WHERE lv_fraction<1""").fetchone()[0],
            "max_direct_ptd_peak_proxy_violation_mw":float(max_ptd_peak_violation or 0),
            "zero_capacity_ptds_with_positive_allocation": con.execute("""SELECT count(*)
                FROM operating.ptd_allocations WHERE peak_proxy_mw=0 AND factor>0""").fetchone()[0],
            "direct_station_weight_errors": weight_errors,
            "sample_station_lv_plus_mv_balance_max_mw": float(sample or 0),
            "ambiguous_dst_calendar_rows": con.execute("SELECT count(*) FROM frozen.main.interval_calendar WHERE eredes_alignment_status='AMBIGUOUS_DST_FOLD'").fetchone()[0],
        }
        errors = []
        if checks["ptd_allocations"] != con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]:
            errors.append("Some PTDs lack allocation")
        if (weight_errors or checks["sample_station_lv_plus_mv_balance_max_mw"] > 1e-8
                or checks["max_direct_ptd_peak_proxy_violation_mw"]>1e-8):
            errors.append("Station LV/MV split or PTD peak cap does not conserve P")
        if checks["zero_capacity_ptds_with_positive_allocation"]:
            errors.append("Zero-capacity PTD received positive load allocation")
        if (checks["complete_utc_intervals"] != len(all_utc)
                or checks["imputed_dst_intervals"] != checks["ambiguous_dst_calendar_rows"]):
            errors.append("Complete UTC calendar or DST gap accounting failed")
        con.execute("CREATE OR REPLACE TABLE audit.ptd_timeseries_validation (check_name VARCHAR, value DOUBLE)")
        con.executemany("INSERT INTO audit.ptd_timeseries_validation VALUES (?, ?)",
                        [(k, float(v)) for k, v in checks.items()])
        con.execute("DETACH frozen")
    report = {"result": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors,
              "scope": "Factorized 15-minute P/Q proxy on complete UTC calendar; observed station rows retained, missing station intervals use normalized national shape, DST gaps use interpolated shape; direct station load split into capacity-bounded LV and inferred MV residual"}
    report_path = args.database.parent / "ptd_timeseries.validation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

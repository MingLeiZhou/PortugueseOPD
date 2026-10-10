#!/usr/bin/env python3
"""r4: time-varying station LV/MV split (replaces the fixed PTD-peak-proxy share).

Run after build_ptd_timeseries.py and import_national_voltage_consumption.py.

Model (see output/lvshare_study/REPORT_lv_share_calibration_2026-10-03_CN.md, variant A4L):
  G_s(t)   = P_s(t) + gamma * w_s * Solar_REN(t)                gross station load (embedded PV added back)
  f'_s     = sigmoid(logit f0_s + delta)                        level; f0_s = r3 PTD-peak-proxy share
  LV_s(t)  = clip(a_s * p_LV(t) * m(t), 0, cap_s * G_s(t))      a_s: mean(LV_s) = f'_s * mean(G_s)
  m(t)     = 1 + beta_H * dHDD_d + beta_C * dCDD_d              population-weighted daily temperature
  sigma_s(t) = LV_s(t) / G_s(t), applied to the published net station load P_s(t)
p_LV: E-REDES BTN A/B/C and IP profiles weighted by ERSE 2025 energy. gamma from a regression of the
station sum on national BT+MT consumption (loss-adjusted, E-REDES loss profiles) and REN solar.
delta, beta_H, beta_C are calibrated on the national BT/(BT+MT) share (all months); leave-one-month-out
errors are stored as the validation of the calibrated split.

Writes observation.eredes_consumption_profile_15min, observation.eredes_loss_profile_15min,
observation.temperature_daily, operating.station_lv_share_15min, operating.station_lv_share_model,
audit.station_lv_share_validation; updates operating.station_lv_split, operating.ptd_allocations and
study.station_national_consumption_15min; redefines operating.station_15min_complete (adds lv_share),
operating.ptd_load_15min and operating.station_mv_residual_15min.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BASE = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
PROFILES = ROOT / "data/raw/eredes_profiles"
TEMPERATURE = ROOT / "data/raw/openmeteo/temperature_daily_2025-04-25_2026-03-31.json"
DIRECT = "DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL"
# ERSE Caracterizacao da Procura 2025, Quadro 3-2 (GWh); BTN B:C split from the E-REDES 2026 profile document
E_A_LIKE = 3567 + 2120          # BTE + BTN > 20.7 kVA -> BTN A profile
E_BTN_SMALL = 18203             # BTN <= 20.7 kVA incl. IP
B_C_RATIO = (15.49, 9.60)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_profiles(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    prof, loss, files = [], [], {}
    for year in (2025, 2026):
        f = directory / f"Perfil_Consumo_Injecao_E-REDES_{year}.xlsx"
        d = pd.read_excel(f, header=None, skiprows=4, usecols=range(9))
        d.columns = ["date", "date2", "hhmm", "resp_mw", "btn_a", "btn_b", "btn_c", "ip", "mp"]
        d = d.dropna(subset=["hhmm"]).reset_index(drop=True)
        if len(d) != 35040:
            raise ValueError(f"{f.name}: {len(d)} rows")
        # rows are consecutive physical quarter hours in legal time, labelled by interval end
        d["timestamp_utc"] = pd.Timestamp(f"{year}-01-01", tz="UTC") + pd.to_timedelta(15 * np.arange(len(d)), unit="min")
        prof.append(d[["timestamp_utc", "resp_mw", "btn_a", "btn_b", "btn_c", "ip", "mp"]]); files[f.name] = sha(f)
        f = directory / f"Perfis_Perdas_E-REDES_{year}.xlsx"
        x = pd.read_excel(f, header=None)
        r = x.index[x.apply(lambda row: (row == "BT").any(), axis=1)][0]
        cols = x.iloc[r].tolist()
        v = x.iloc[r + 1:, [cols.index("BT"), cols.index("MT")]].dropna().astype(float).reset_index(drop=True)
        v.columns = ["loss_bt", "loss_mt"]
        if len(v) != 35040:
            raise ValueError(f"{f.name}: {len(v)} rows")
        v["timestamp_utc"] = pd.Timestamp(f"{year}-01-01", tz="UTC") + pd.to_timedelta(15 * np.arange(len(v)), unit="min")
        loss.append(v); files[f.name] = sha(f)
    return pd.concat(prof), pd.concat(loss), files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", type=Path, default=STAGING)
    ap.add_argument("--base", type=Path, default=BASE)
    ap.add_argument("--profiles", type=Path, default=PROFILES)
    ap.add_argument("--temperature", type=Path, default=TEMPERATURE)
    ap.add_argument("--ip-twh", type=float, default=0.9)
    ap.add_argument("--smax", type=float, default=0.95)
    ap.add_argument("--hdd-base", type=float, default=18.0)
    ap.add_argument("--cdd-base", type=float, default=22.0)
    ap.add_argument("--skip-lomo", action="store_true")
    ap.add_argument("--duckdb-memory", default=None, help="e.g. 1GB (for small machines)")
    args = ap.parse_args()

    prof, loss, files = read_profiles(args.profiles)
    tj = json.loads(args.temperature.read_text()); files[args.temperature.name] = sha(args.temperature)
    weights = tj["weights"]
    temp = pd.DataFrame({k: pd.Series(v["temperature_2m_mean"], index=pd.to_datetime(v["time"])) for k, v in tj["data"].items()})
    temp["t_pw"] = (temp[list(weights)] * pd.Series(weights)).sum(1) / sum(weights.values())

    with duckdb.connect(str(args.database)) as con:
        if args.duckdb_memory:
            con.execute(f"SET memory_limit='{args.duckdb_memory}'"); con.execute("SET threads=2")
        print("loading station series", flush=True)
        base_sql = str(args.base).replace("'", "''")
        con.execute(f"ATTACH '{base_sql}' AS frozen (READ_ONLY)")
        codes = [r[0] for r in con.execute("SELECT DISTINCT station_code FROM operating.station_15min ORDER BY 1").fetchall()]
        tss = [r[0] for r in con.execute("SELECT timestamp_utc FROM operating.national_load_shape_15min ORDER BY 1").fetchall()]
        con.execute("CREATE OR REPLACE TEMP TABLE _st AS SELECT p_mw FROM operating.station_15min_complete ORDER BY station_code, timestamp_utc")
        pv = con.execute("SELECT p_mw FROM _st").fetchnumpy()["p_mw"]
        if len(pv) != len(codes) * len(tss):
            raise ValueError("station_15min_complete is not a full station x calendar grid")
        print("loaded", len(pv), flush=True)
        P = np.ascontiguousarray(pv.reshape(len(codes), len(tss)).T); del pv
        con.execute("DROP TABLE _st")
        split = con.execute("SELECT station_code, lv_fraction, ptd_peak_proxy_sum_mw FROM operating.station_lv_split").df()
        if "lv_fraction_proxy" in con.execute("DESCRIBE operating.station_lv_split").df().column_name.tolist():
            raise SystemExit("station_lv_split already converted; rerun build_ptd_timeseries.py first")
        nat = con.execute("SELECT timestamp_utc, bt_mw, mt_mw FROM observation.national_consumption_unique_15min").df()
        solar = con.execute("""SELECT c.timestamp_utc, sum(r.value_mw) AS solar_mw FROM frozen.main.ren_dispatch r
            JOIN frozen.main.interval_calendar c ON r.source_date=c.local_date AND r.source_index=c.source_index
            WHERE r.series_name='Solar' GROUP BY 1""").df()
        con.execute("DETACH frozen")

        idx = pd.to_datetime(pd.Index(tss), utc=True)
        split = split.set_index("station_code").reindex(codes)
        has_split = split.lv_fraction.notna().values
        f0 = split.lv_fraction.fillna(0.0).values
        proxy_w = split.ptd_peak_proxy_sum_mw.fillna(0.0).values
        D = pd.DataFrame(index=idx)
        D = D.join(prof.set_index("timestamp_utc")).join(loss.set_index("timestamp_utc"))
        nat.index = pd.to_datetime(nat.timestamp_utc, utc=True); solar.index = pd.to_datetime(solar.timestamp_utc, utc=True)
        D = D.join(nat[["bt_mw", "mt_mw"]]).join(solar[["solar_mw"]])
        if D[["btn_a", "loss_bt", "solar_mw"]].isna().any().any():
            raise ValueError("profile, loss or solar series does not cover the calendar")
        ok = D.bt_mw.notna().values
        pub = (D.bt_mw / (D.bt_mw + D.mt_mw)).values
        local_date = pd.to_datetime(idx.tz_convert("Europe/Lisbon").date)
        month = idx.tz_convert("Europe/Lisbon").strftime("%Y-%m").values
        Td = temp.t_pw.reindex(local_date).values
        if np.isnan(Td).any():
            raise ValueError("temperature series does not cover the calendar")

        # 1. embedded PV: sum P = theta * Nsub - gamma * solar
        nsub = (D.bt_mw * (1 + D.loss_bt) * (1 + D.loss_mt) + D.mt_mw * (1 + D.loss_mt)).values
        X = np.c_[nsub[ok], -D.solar_mw.values[ok]]
        (theta, gamma), *_ = np.linalg.lstsq(X, P.sum(1)[ok], rcond=None)
        w = np.where(has_split, proxy_w, 0.0); w = w / w.sum()
        G = P + np.outer(gamma * D.solar_mw.values, w)
        Gpos = np.clip(G, 0, None); Gmean = G.mean(0)

        # 2. LV shape from E-REDES profiles
        e_ip = args.ip_twh * 1000
        e_small = E_BTN_SMALL - e_ip
        wt = {"btn_a": E_A_LIKE, "btn_b": e_small * B_C_RATIO[0] / sum(B_C_RATIO),
              "btn_c": e_small * B_C_RATIO[1] / sum(B_C_RATIO), "ip": e_ip}
        p_lv = (sum(wt[k] * D[k] / D[k].mean() for k in wt if wt[k] > 0) / sum(wt.values())).values
        hdd = np.maximum(0, args.hdd_base - Td); cdd = np.maximum(0, Td - args.cdd_base)
        dH, dC = hdd - hdd.mean(), cdd - cdd.mean()
        full = has_split & (f0 >= 0.999)
        cap = np.maximum(args.smax, f0)
        logit = np.log(np.clip(f0, 1e-6, 1 - 1e-6) / (1 - np.clip(f0, 1e-6, 1 - 1e-6)))

        def sigma(theta_):
            delta, bh, bc = theta_
            m = np.clip(1 + bh * dH + bc * dC, 0.2, 5.0)
            shape = p_lv * m
            fp = 1 / (1 + np.exp(-(logit + delta)))
            a = fp * Gmean / shape.mean()
            LV = np.clip(np.outer(shape, a), 0, cap * Gpos)
            s = np.divide(LV, G, out=np.tile(f0, (len(G), 1)), where=G > 1e-9)
            s = np.clip(s, 0, cap)
            s[:, full] = 1.0
            s[:, ~has_split] = 0.0
            return s

        def share(s):
            return (P * s).sum(1) / P.sum(1)

        def fit(mask):
            obj = lambda th: np.abs(share(sigma(th))[mask] - pub[mask]).mean()
            return minimize(obj, [0.9, 0.03, 0.02], method="Nelder-Mead",
                            options={"xatol": 1e-4, "fatol": 1e-7, "maxiter": 600}).x

        def metrics(s_, mask):
            e = s_[mask] - pub[mask]
            mm = pd.DataFrame({"m": month[mask], "s": s_[mask], "p": pub[mask]}).groupby("m").mean()
            return {"mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean()),
                    "monthly_mae": float((mm.s - mm.p).abs().mean()), "r_15min": float(np.corrcoef(s_[mask], pub[mask])[0, 1])}

        print("fitting", flush=True)
        th = fit(ok)
        S = sigma(th)
        validation = {"in_sample": metrics(share(S), ok),
                      "r3_fixed_share": metrics((P * np.where(has_split, f0, 0)).sum(1) / P.sum(1), ok)}
        lomo_rows = []
        if not args.skip_lomo:
            pred = np.full(len(pub), np.nan)
            for m in sorted(set(month[ok])):
                test, train = ok & (month == m), ok & (month != m)
                tm = fit(train); pred[test] = share(sigma(tm))[test]
                lomo_rows.append((m, *map(float, tm), float(np.abs(pred[test] - pub[test]).mean())))
                print("LOMO", m, np.round(tm, 4), round(lomo_rows[-1][-1], 4), flush=True)
            validation["leave_one_month_out"] = metrics(np.nan_to_num(pred), ok)

        print("writing", th, validation["in_sample"], flush=True)
        # write observation inputs
        for name, df in (("eredes_consumption_profile_15min", prof.assign(timestamp_utc=prof.timestamp_utc.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"))),
                         ("eredes_loss_profile_15min", loss.assign(timestamp_utc=loss.timestamp_utc.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")))):
            con.register("tmp_df", df)
            con.execute(f"CREATE OR REPLACE TABLE observation.{name} AS SELECT *, 'PUBLIC_EREDES_PROFILE' AS evidence_status FROM tmp_df")
            con.unregister("tmp_df")
        tdf = temp.reset_index().rename(columns={"index": "local_date"}); tdf["local_date"] = tdf.local_date.dt.strftime("%Y-%m-%d")
        con.register("tmp_df", tdf)
        con.execute("CREATE OR REPLACE TABLE observation.temperature_daily AS SELECT *, 'OPEN_METEO_ERA5_BASED_POPULATION_WEIGHTED' AS evidence_status FROM tmp_df")
        con.unregister("tmp_df")

        con.register("tmp_codes", pd.DataFrame({"si": np.arange(len(codes), dtype=np.int32), "station_code": codes}))
        con.register("tmp_ts", pd.DataFrame({"ti": np.arange(len(tss), dtype=np.int32), "timestamp_utc": tss}))
        flat = pd.DataFrame({"si": np.repeat(np.arange(len(codes), dtype=np.int32), len(tss)),
                             "ti": np.tile(np.arange(len(tss), dtype=np.int32), len(codes)),
                             "lv_share": S.T.reshape(-1)})
        con.register("tmp_df", flat)
        con.execute("""CREATE OR REPLACE TABLE operating.station_lv_share_15min AS
            SELECT c.station_code, t.timestamp_utc, d.lv_share,
                   'CALIBRATED_PROFILE_TEMPERATURE_LV_SHARE_SCENARIO' AS evidence_status
            FROM tmp_df d JOIN tmp_codes c USING(si) JOIN tmp_ts t USING(ti)""")
        for n in ("tmp_df", "tmp_codes", "tmp_ts"): con.unregister(n)
        del flat
        mean_share = pd.Series((P * S).sum(0) / np.where(P.sum(0) != 0, P.sum(0), 1), index=codes)
        params = {"delta_logit": th[0], "beta_hdd_per_degC_day": th[1], "beta_cdd_per_degC_day": th[2],
                  "gamma_embedded_pv_fraction_of_ren_solar": gamma, "theta_station_coverage": theta,
                  "ip_twh_assumed": args.ip_twh, "smax": args.smax, "hdd_base_c": args.hdd_base, "cdd_base_c": args.cdd_base}
        con.execute("CREATE OR REPLACE TABLE operating.station_lv_share_model (parameter VARCHAR, value DOUBLE, note VARCHAR)")
        con.executemany("INSERT INTO operating.station_lv_share_model VALUES (?,?,?)",
                        [(k, float(v), "calibrated on national BT/(BT+MT), all months" if k in ("delta_logit", "beta_hdd_per_degC_day", "beta_cdd_per_degC_day")
                          else "regression on national BT+MT and REN solar" if k.startswith(("gamma", "theta")) else "assumption")
                         for k, v in params.items()])
        con.execute("CREATE OR REPLACE TABLE audit.station_lv_share_validation (scope VARCHAR, metric VARCHAR, value DOUBLE)")
        con.executemany("INSERT INTO audit.station_lv_share_validation VALUES (?,?,?)",
                        [(sc, k, v) for sc, d in validation.items() for k, v in d.items()])
        if lomo_rows:
            con.execute("""CREATE OR REPLACE TABLE audit.station_lv_share_lomo (month VARCHAR, delta_logit DOUBLE,
                beta_hdd DOUBLE, beta_cdd DOUBLE, heldout_mae DOUBLE)""")
            con.executemany("INSERT INTO audit.station_lv_share_lomo VALUES (?,?,?,?,?)", lomo_rows)
        con.execute("CREATE OR REPLACE TABLE audit.station_lv_share_inputs (file VARCHAR, sha256 VARCHAR)")
        con.executemany("INSERT INTO audit.station_lv_share_inputs VALUES (?,?)", list(files.items()))

        # station split table: keep the r3 proxy share, lv_fraction becomes the time-mean of sigma
        con.execute("ALTER TABLE operating.station_lv_split RENAME COLUMN lv_fraction TO lv_fraction_proxy")
        con.execute("ALTER TABLE operating.station_lv_split ADD COLUMN lv_fraction DOUBLE")
        con.register("tmp_df", pd.DataFrame({"station_code": codes, "m": mean_share.values}))
        con.execute("""UPDATE operating.station_lv_split s SET lv_fraction=t.m,
            evidence_status='TIME_VARYING_CALIBRATED_LV_SHARE_MEAN_SEE_station_lv_share_15min'
            FROM tmp_df t WHERE s.station_code=t.station_code""")
        con.unregister("tmp_df")
        cols = con.execute("DESCRIBE operating.ptd_allocations").df().column_name.tolist()
        if "lv_share_mode" not in cols:
            con.execute("ALTER TABLE operating.ptd_allocations ADD COLUMN lv_share_mode VARCHAR")
        con.execute(f"""UPDATE operating.ptd_allocations a SET
            station_lv_fraction=CASE WHEN a.allocation_status='{DIRECT}' THEN s.lv_fraction ELSE a.station_lv_fraction END,
            lv_share_mode=CASE WHEN a.allocation_status='{DIRECT}' THEN 'STATION_TIME_VARYING' ELSE 'FIXED' END
            FROM operating.station_lv_split s WHERE s.station_code=a.assigned_station_code""")
        con.execute("UPDATE operating.ptd_allocations SET lv_share_mode='FIXED' WHERE lv_share_mode IS NULL")

        # views: the complete station series carries the time-varying LV share
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
                        ELSE 'IMPUTED_MISSING_STATION_NATIONAL_SHAPE' END AS data_status,
                   coalesce(l.lv_share,0) AS lv_share
            FROM station_peaks p CROSS JOIN operating.national_load_shape_15min n
            LEFT JOIN operating.station_15min s
              ON s.station_code=p.station_code AND s.timestamp_utc=n.timestamp_utc
            LEFT JOIN operating.station_lv_share_15min l
              ON l.station_code=p.station_code AND l.timestamp_utc=n.timestamp_utc""")
        con.execute("""CREATE OR REPLACE VIEW operating.ptd_load_15min AS
            SELECT a.ptd_code, s.timestamp_utc,
                   s.p_mw * a.factor * CASE WHEN a.lv_share_mode='STATION_TIME_VARYING' THEN s.lv_share ELSE a.station_lv_fraction END AS p_mw,
                   s.p_mw * a.factor * CASE WHEN a.lv_share_mode='STATION_TIME_VARYING' THEN s.lv_share ELSE a.station_lv_fraction END * tan(acos(0.97)) AS q_mvar,
                   a.assigned_station_code, a.profile_station_code, a.allocation_status,
                   s.data_status AS profile_data_status
            FROM operating.ptd_allocations a
            JOIN operating.station_15min_complete s ON a.profile_station_code=s.station_code""")
        con.execute("""CREATE OR REPLACE VIEW operating.station_mv_residual_15min AS
            SELECT s.station_code,s.timestamp_utc,
                   s.p_mw*(1-s.lv_share) AS p_mw,
                   s.p_mw*(1-s.lv_share)*tan(acos(0.97)) AS q_mvar,
                   'INFERRED_MV_RESIDUAL_NOT_OBSERVED_SEPARATELY' AS evidence_status,
                   s.data_status AS profile_data_status
            FROM operating.station_15min_complete s""")
        con.execute("""CREATE OR REPLACE TABLE study.station_national_consumption_15min AS
            WITH station AS (
              SELECT s.timestamp_utc, sum(s.p_mw) AS station_total_mw,
                     sum(s.p_mw*s.lv_share) AS model_direct_lv_mw,
                     sum(s.p_mw*(1-s.lv_share)) AS model_station_mv_residual_mw,
                     count(*) AS station_count,
                     count(*) FILTER (WHERE s.data_status<>'PUBLIC_EREDES_STATION_AGGREGATE') AS estimated_station_count
              FROM operating.station_15min_complete s
              JOIN operating.station_lv_split f USING(station_code)
              GROUP BY 1)
            SELECT s.*, n.bt_mw AS national_bt_mw, n.mt_mw AS national_mt_mw,
                   n.at_mw AS national_at_mw, n.mat_mw AS national_mat_mw, n.total_mw AS national_total_mw,
                   s.model_direct_lv_mw/nullif(s.station_total_mw,0) AS model_lv_fraction,
                   n.bt_mw/nullif(n.bt_mw+n.mt_mw,0) AS national_bt_share_of_bt_mt,
                   s.station_total_mw/nullif(n.bt_mw+n.mt_mw,0) AS station_to_national_bt_mt_ratio,
                   CASE WHEN n.timestamp_utc IS NULL THEN 'NO_UNIQUE_NATIONAL_UTC_RECORD'
                        ELSE 'CALIBRATED_SPLIT_COMPARISON_SEE_audit.station_lv_share_validation' END AS comparison_status
            FROM station s LEFT JOIN observation.national_consumption_unique_15min n USING(timestamp_utc)""")

        # checks
        bal = con.execute(f"""WITH x AS (SELECT timestamp_utc FROM operating.operating_snapshot ORDER BY timestamp_utc LIMIT 3)
            SELECT max(abs(st.p_mw - coalesce(lv.p,0) - mv.p_mw)) FROM operating.station_15min_complete st
            JOIN x USING(timestamp_utc)
            JOIN operating.station_lv_split f USING(station_code)
            LEFT JOIN (SELECT assigned_station_code AS station_code, timestamp_utc, sum(p_mw) p FROM operating.ptd_load_15min
                       WHERE allocation_status='{DIRECT}' AND timestamp_utc IN (SELECT timestamp_utc FROM x) GROUP BY 1,2) lv USING(station_code,timestamp_utc)
            JOIN operating.station_mv_residual_15min mv USING(station_code,timestamp_utc)""").fetchone()[0]
        lvpk = (P * S).max(0); prox = np.where(has_split, proxy_w, np.nan)
        rr = lvpk[has_split & (proxy_w > 0)] / prox[has_split & (proxy_w > 0)]
        peak_ratio = dict(zip(["p50", "p95", "max"], np.quantile(rr, [0.5, 0.95, 1.0]).round(3).tolist()))
        report = {"parameters": params, "validation": validation, "input_sha256": files,
                  "station_lv_plus_mv_balance_max_mw_sample": float(bal or 0),
                  "station_lv_peak_over_ptd_proxy_sum_quantiles": peak_ratio,
                  "mean_lv_share_r3": float((P * np.where(has_split, f0, 0)).sum() / P.sum()),
                  "mean_lv_share_r4": float((P * S).sum() / P.sum())}
        out_dir = ROOT / "output/all_voltage/station_lv_share"; out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "validation.json").write_text(json.dumps(report, indent=2, default=float))
        print(json.dumps(report, indent=2, default=float))
        if bal is None or bal > 1e-8:
            raise SystemExit("station LV + MV balance check failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

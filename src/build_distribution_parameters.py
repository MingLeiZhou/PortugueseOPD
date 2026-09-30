#!/usr/bin/env python3
"""Build auditable MV-line and PTD-transformer parameter scenarios.

Public E-REDES specifications provide real standard catalogue values, but do
not identify the installed conductor or transformer of each public asset.
This builder therefore keeps catalogue evidence separate from deterministic
asset-level assignments and labels every inferred or simulated field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
RAW = ROOT / "data/raw/distribution_parameters"

SOURCES = [
    ("DMA-C33-251", "MV_CABLE", "https://www.e-redes.pt/sites/edd/files/normative_docs/DMA-C33-251.pdf", "DMA-C33-251.pdf", "2017-05", "edition 3 revision 1"),
    ("DMA-C34-110", "BARE_COPPER", "https://www.e-redes.pt/sites/edd/files/normative_docs/DMA-C34-110.pdf", "DMA-C34-110.pdf", "2015-07", "edition 2"),
    ("DMA-C34-120N", "ACSR", "https://www.e-redes.pt/sites/edd/files/normative_docs/DMA-C34-120N.pdf", "DMA-C34-120N.pdf", "2010-09", "official E-REDES specification"),
    ("DMA-C52-125", "OIL_TRANSFORMER", "https://www.e-redes.pt/sites/eredes/files/2020-06/DMA-C52-125.pdf", "DMA-C52-125.pdf", "2020-05", "edition 4"),
    ("DMA-C52-130", "DRY_TRANSFORMER", "https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C52-130.pdf", "DMA-C52-130.pdf", "2014-10", "official E-REDES specification"),
]

ACSR = [
    ("26-AL1/4-ST1A", 30, 26.25, 4.37, 7.08, 1.0932),
    ("42-AL1/7-ST1A", 50, 42.41, 7.07, 9.00, 0.6765),
    ("75-AL1/13-ST1A", 90, 75.40, 12.57, 12.00, 0.3805),
    ("80-AL1/47-ST1A", 130, 80.36, 46.88, 14.60, 0.3594),
    ("136-AL1/22-ST1A", 160, 135.93, 21.99, 16.32, 0.2124),
    ("203-AL1/32-ST1A", 235, 202.62, 32.46, 19.89, 0.1425),
    ("264-AL1/62-ST1A", 325, 264.42, 61.70, 23.45, 0.1093),
]

COPPER = [
    ("CU-16", 16, 5.10, 1.140, 1.163), ("CU-25", 25, 6.42, 0.719, 0.734),
    ("CU-35", 35, 7.56, 0.519, 0.529), ("CU-50", 50, 9.00, 0.366, 0.374),
    ("CU-95", 95, 12.60, 0.192, 0.196), ("CU-185", 185, 17.64, 0.099, 0.101),
]

# E-REDES DMA-C33-251 Annex A cable list. R20 is an engineering derivation
# from conductor material/section, while ampacity is the public hot-soil,
# one-circuit value in Annex B, table B.1.
CABLES = [
    ("336901", "LXHIOZ1 1x240/16 6/10", 10, 240, "AL", 0.1225, 391),
    ("336902", "LXHIOZ1 1x120/16 8.7/15", 15, 120, "AL", 0.2450, 266),
    ("336903", "LXHIOZ1 1x240/16 8.7/15", 15, 240, "AL", 0.1225, 391),
    ("336904", "LXHIOZ1 1x120/16 18/30", 30, 120, "AL", 0.2450, 266),
    ("336905", "LXHIOZ1 1x240/16 18/30", 30, 240, "AL", 0.1225, 391),
    ("336906", "LXHIOZ1 FRT 1x120/16 8.7/15", 15, 120, "AL", 0.2450, 266),
    ("336907", "LXHIOZ1 FRT 1x240/16 8.7/15", 15, 240, "AL", 0.1225, 391),
    ("336908", "LXHIOZ1 FRT 1x500/16 8.7/15", 15, 500, "AL", 0.0588, 576),
    ("336909", "XHIOZ1 FRT 1x500/16 8.7/15", 15, 500, "CU", 0.0360, 718),
    ("336910", "LXHIOZ1 FRT 1x120/16 18/30", 30, 120, "AL", 0.2450, 266),
    ("336911", "LXHIOZ1 FRT 1x240/16 18/30", 30, 240, "AL", 0.1225, 391),
]

OIL_LOSSES = {50: (750, 81), 100: (1250, 130), 160: (1750, 189), 250: (2350, 270),
              400: (3250, 387), 630: (4600, 540), 800: (6000, 585), 1000: (7600, 693)}
DRY_LOSSES = {250: (3800, 520), 400: (5500, 750), 630: (7600, 1100), 1000: (9000, 1550)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_catalogues(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE OR REPLACE TABLE parameter.public_standard_source(source_id VARCHAR, object_class VARCHAR, source_url VARCHAR, local_file VARCHAR, publication_date VARCHAR, edition VARCHAR, sha256 VARCHAR)")
    con.executemany("INSERT INTO parameter.public_standard_source VALUES (?,?,?,?,?,?,?)", [
        (sid, cls, url, str(RAW / fn), date, edition, sha256(RAW / fn))
        for sid, cls, url, fn, date, edition in SOURCES
    ])

    con.execute("""CREATE OR REPLACE TABLE parameter.mv_overhead_conductor_catalog(
        catalog_id VARCHAR, family VARCHAR, nominal_section_mm2 DOUBLE,
        aluminium_section_mm2 DOUBLE, steel_section_mm2 DOUBLE,
        diameter_mm DOUBLE, r20_ohm_per_km DOUBLE, r20_limit_kind VARCHAR,
        source_id VARCHAR, source_page INTEGER, source_table VARCHAR,
        evidence_status VARCHAR)""")
    con.executemany("INSERT INTO parameter.mv_overhead_conductor_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (code, "ALUMINIUM_STEEL_ACSR", nominal, al, steel, dia, r20, "PUBLIC_MAXIMUM", "DMA-C34-120N", 8, "QUADRO_4", "PUBLIC_STANDARD_TYPE_NOT_ASSET_ASSIGNMENT")
        for code, nominal, al, steel, dia, r20 in ACSR
    ] + [
        (code, "BARE_COPPER", nominal, nominal, 0.0, dia, rnom, "PUBLIC_NOMINAL", "DMA-C34-110", 8, "QUADRO_5", "PUBLIC_STANDARD_TYPE_NOT_ASSET_ASSIGNMENT")
        for code, nominal, dia, rnom, _ in COPPER
    ])

    con.execute("""CREATE OR REPLACE TABLE parameter.mv_cable_catalog(
        catalog_id VARCHAR, designation VARCHAR, voltage_kv DOUBLE,
        conductor_section_mm2 DOUBLE, conductor_material VARCHAR,
        r20_ohm_per_km DOUBLE, r20_basis VARCHAR, ampacity_hot_soil_a DOUBLE,
        ampacity_basis VARCHAR, source_id VARCHAR, source_pages VARCHAR,
        evidence_status VARCHAR)""")
    con.executemany("INSERT INTO parameter.mv_cable_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (sap, des, v, sec, mat, r, "ENGINEERING_DERIVATION_FROM_MATERIAL_AND_SECTION", amps,
         "PUBLIC_DMA_C33_251_ANNEX_B_TABLE_B1_ONE_CIRCUIT_HOT_SOIL", "DMA-C33-251", "28-29",
         "PUBLIC_STANDARD_TYPE_WITH_DERIVED_RESISTANCE_NOT_ASSET_ASSIGNMENT")
        for sap, des, v, sec, mat, r, amps in CABLES
    ])

    con.execute("""CREATE OR REPLACE TABLE parameter.distribution_transformer_standard(
        catalog_id VARCHAR, construction VARCHAR, rated_kva DOUBLE, hv_kv DOUBLE,
        lv_kv DOUBLE, vk_percent DOUBLE, load_loss_w DOUBLE, no_load_loss_w DOUBLE,
        vkr_percent_from_max_loss DOUBLE, vector_group VARCHAR, cooling VARCHAR,
        source_id VARCHAR, source_pages VARCHAR, evidence_status VARCHAR)""")
    rows = []
    for kva, (pk, p0) in OIL_LOSSES.items():
        for hv in (10, 15, 30):
            vk = 5.0 if hv == 30 and kva <= 630 else (4.0 if kva <= 630 else 6.0)
            factor = 1.10 if hv == 30 else 1.0
            rows.append((f"OIL:{hv}:{kva}", "OIL_IMMERSED", kva, hv, 0.42, vk, pk*factor, p0*(1.15 if hv == 30 else 1), pk*factor/kva/10, "Dyn5", "ONAN", "DMA-C52-125", "12,32", "PUBLIC_STANDARD_TYPE_NOT_ASSET_NAMEPLATE"))
    for kva, (pk, p0) in DRY_LOSSES.items():
        for hv in (10, 15, 30):
            vk = 5.0 if hv == 30 and kva <= 630 else (4.0 if kva <= 630 else 6.0)
            factor = 1.10 if hv == 30 else 1.0
            rows.append((f"DRY:{hv}:{kva}", "DRY", kva, hv, 0.42, vk, pk*factor, p0*(1.15 if hv == 30 else 1), pk*factor/kva/10, "Dyn5", "AN", "DMA-C52-130", "7,15", "PUBLIC_STANDARD_TYPE_NOT_ASSET_NAMEPLATE"))
    con.executemany("INSERT INTO parameter.distribution_transformer_standard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def create_assignments(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE OR REPLACE TABLE parameter.mv_line_parameters AS
        WITH assigned AS (
          SELECT s.*,
            CASE WHEN power_tag='cable' THEN
                   CASE WHEN voltage_kv<=10 THEN '336901' WHEN voltage_kv<=15 THEN '336903' ELSE '336905' END
                 WHEN power_tag='line' THEN '203-AL1/32-ST1A'
                 WHEN voltage_kv>=20 THEN '136-AL1/22-ST1A'
                 ELSE '75-AL1/13-ST1A' END AS standard_catalog_id
          FROM candidate.osm_segments s WHERE voltage_kv>=1
        ), base AS (
          SELECT a.*,
            CASE WHEN a.power_tag='cable' THEN c.r20_ohm_per_km ELSE o.r20_ohm_per_km END AS r20,
            CASE WHEN a.power_tag='cable' THEN c.ampacity_hot_soil_a/1000.0
                 WHEN a.standard_catalog_id='203-AL1/32-ST1A' THEN 0.510
                 WHEN a.standard_catalog_id='136-AL1/22-ST1A' THEN 0.390 ELSE 0.260 END AS imax,
            CASE WHEN a.power_tag='cable' THEN 0.080
                 WHEN a.standard_catalog_id='203-AL1/32-ST1A' THEN 0.300
                 WHEN a.standard_catalog_id='136-AL1/22-ST1A' THEN 0.320 ELSE 0.350 END AS x1,
            CASE WHEN a.power_tag='cable' THEN 240.0 ELSE 10.0 END AS c1
          FROM assigned a
          LEFT JOIN parameter.mv_cable_catalog c ON a.standard_catalog_id=c.catalog_id
          LEFT JOIN parameter.mv_overhead_conductor_catalog o ON a.standard_catalog_id=o.catalog_id
        )
        SELECT edge_id, component_id, voltage_kv, power_tag, length_km, standard_catalog_id,
          (r20 * CASE WHEN power_tag='cable' THEN 1.2821 ELSE 1.22165 END)::DOUBLE AS r_ohm_per_km,
          x1::DOUBLE AS x_ohm_per_km, c1::DOUBLE AS c_nf_per_km, imax::DOUBLE AS max_i_ka,
          (r20 * CASE WHEN power_tag='cable' THEN 1.2821 ELSE 1.22165 END * 3.0)::DOUBLE AS r0_ohm_per_km,
          (x1*3.0)::DOUBLE AS x0_ohm_per_km,
          (r20 * CASE WHEN power_tag='cable' THEN 1.2821 ELSE 1.22165 END * 0.75)::DOUBLE AS r_low_ohm_per_km,
          (r20 * CASE WHEN power_tag='cable' THEN 1.2821 ELSE 1.22165 END * 1.25)::DOUBLE AS r_high_ohm_per_km,
          (x1*0.60)::DOUBLE AS x_low_ohm_per_km, (x1*1.40)::DOUBLE AS x_high_ohm_per_km,
          CASE WHEN power_tag='cable' THEN 'PUBLIC_OSM_TAG_PLUS_EREDES_STANDARD_CABLE_ASSIGNMENT'
               ELSE 'PUBLIC_OSM_TAG_PLUS_EREDES_STANDARD_ACSR_ASSIGNMENT' END AS evidence_basis,
          'STANDARD_CATALOG_PLUS_INFERRED_ASSET_ASSIGNMENT' AS parameter_status,
          CASE WHEN power_tag='cable' THEN 'PUBLIC_STANDARD' ELSE 'ENGINEERING_THERMAL_INFERENCE' END AS ampacity_status,
          'GEOMETRY_CLASS_INFERENCE' AS reactance_status,
          'SIMULATED_SEQUENCE_RATIO' AS zero_sequence_status,
          'MV_STANDARD_SCENARIO_V2' AS parameter_set_version
        FROM base""")

    con.execute("""CREATE OR REPLACE TABLE parameter.ptd_transformer_parameters AS
        WITH effective AS (
          SELECT p.*, e.mv_voltage_kv,
            CASE WHEN p.capacity_kva_public>0 THEN p.capacity_kva_public::DOUBLE
                 WHEN p.customer_count_public='<20' THEN 50.0
                 WHEN 2.0*coalesce(try_cast(p.customer_count_public AS DOUBLE),25.0)<=50 THEN 50.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=100 THEN 100.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=160 THEN 160.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=250 THEN 250.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=400 THEN 400.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=630 THEN 630.0
                 WHEN 2.0*try_cast(p.customer_count_public AS DOUBLE)<=800 THEN 800.0
                 ELSE 1000.0 END AS effective_kva,
            CASE WHEN p.capacity_kva_public>0 THEN 'PUBLIC_EREDES_PTD_KVA'
                 WHEN p.customer_count_public='<20' THEN 'SIMULATED_50_KVA_FROM_LT20_CUSTOMERS'
                 ELSE 'INFERRED_2_KVA_PER_CUSTOMER_ROUNDED_TO_STANDARD' END AS capacity_status
          FROM candidate.ptd_node_candidates p JOIN equivalent.ptd_connections e USING(ptd_code)
        ), banked AS (
          SELECT *, CASE WHEN effective_kva<=1000 THEN 1 ELSE ceil(effective_kva/630.0)::INTEGER END AS inferred_unit_count
          FROM effective
        ), chosen0 AS (
          SELECT *, effective_kva/inferred_unit_count AS target_unit_kva
          FROM banked
        ), chosen AS (
          SELECT *,
            CASE WHEN target_unit_kva<=50 THEN 50 WHEN target_unit_kva<=100 THEN 100
                 WHEN target_unit_kva<=160 THEN 160 WHEN target_unit_kva<=250 THEN 250
                 WHEN target_unit_kva<=400 THEN 400 WHEN target_unit_kva<=630 THEN 630
                 WHEN target_unit_kva<=800 THEN 800 ELSE 1000 END AS catalog_unit_kva,
            CASE WHEN mv_voltage_kv<=10 THEN 10 WHEN mv_voltage_kv<=15 THEN 15 ELSE 30 END AS catalog_hv_kv
          FROM chosen0
        )
        SELECT c.ptd_code, c.capacity_kva_public,
          c.effective_kva/1000.0 AS sn_mva, c.mv_voltage_kv AS hv_kv, 0.4::DOUBLE AS lv_kv,
          t.vk_percent, t.vkr_percent_from_max_loss AS vkr_percent,
          greatest(3.0,t.vk_percent-1.0)::DOUBLE AS vk_low_percent,
          least(7.0,t.vk_percent+1.0)::DOUBLE AS vk_high_percent,
          (t.vkr_percent_from_max_loss*0.75)::DOUBLE AS vkr_low_percent,
          (t.vkr_percent_from_max_loss*1.25)::DOUBLE AS vkr_high_percent,
          'Dyn5' AS vector_group, t.vk_percent AS vk0_percent,
          t.vkr_percent_from_max_loss AS vkr0_percent,
          c.capacity_status, 'STANDARD_CATALOG_PLUS_INFERRED_ASSET_ASSIGNMENT' AS impedance_status,
          'SIMULATED_EQUAL_POSITIVE_SEQUENCE' AS zero_sequence_status,
          t.catalog_id AS standard_catalog_id, c.inferred_unit_count,
          t.load_loss_w*c.inferred_unit_count AS max_load_loss_w,
          t.no_load_loss_w*c.inferred_unit_count AS max_no_load_loss_w,
          'OIL_IMMERSED_DEFAULT_UNLESS_ASSET_EVIDENCE' AS construction_assignment_status,
          'PTD_STANDARD_SCENARIO_V2' AS parameter_set_version
        FROM chosen c JOIN parameter.distribution_transformer_standard t
          ON t.catalog_id='OIL:'||c.catalog_hv_kv::VARCHAR||':'||c.catalog_unit_kva::VARCHAR""")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    args = parser.parse_args()
    for _, _, _, filename, _, _ in SOURCES:
        if not (RAW / filename).exists():
            raise FileNotFoundError(RAW / filename)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        create_catalogues(con)
        create_assignments(con)
        checks = {
            "public_source_rows": con.execute("SELECT count(*) FROM parameter.public_standard_source").fetchone()[0],
            "public_standard_overhead_rows": con.execute("SELECT count(*) FROM parameter.mv_overhead_conductor_catalog").fetchone()[0],
            "public_standard_cable_rows": con.execute("SELECT count(*) FROM parameter.mv_cable_catalog").fetchone()[0],
            "public_standard_transformer_rows": con.execute("SELECT count(*) FROM parameter.distribution_transformer_standard").fetchone()[0],
            "mv_line_rows": con.execute("SELECT count(*) FROM parameter.mv_line_parameters").fetchone()[0],
            "mv_source_rows": con.execute("SELECT count(*) FROM candidate.osm_segments WHERE voltage_kv>=1").fetchone()[0],
            "mv_distinct_parameter_combinations": con.execute("SELECT count(DISTINCT (standard_catalog_id,r_ohm_per_km,x_ohm_per_km,max_i_ka)) FROM parameter.mv_line_parameters").fetchone()[0],
            "ptd_transformer_rows": con.execute("SELECT count(*) FROM parameter.ptd_transformer_parameters").fetchone()[0],
            "ptd_source_rows": con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0],
            "positive_mv_parameters": con.execute("SELECT count(*) FROM parameter.mv_line_parameters WHERE r_ohm_per_km>0 AND x_ohm_per_km>0 AND max_i_ka>0 AND r_low_ohm_per_km<=r_ohm_per_km AND r_ohm_per_km<=r_high_ohm_per_km AND x_low_ohm_per_km<=x_ohm_per_km AND x_ohm_per_km<=x_high_ohm_per_km").fetchone()[0],
            "positive_ptd_capacity": con.execute("SELECT count(*) FROM parameter.ptd_transformer_parameters WHERE sn_mva>0").fetchone()[0],
            "inferred_missing_ptd_capacity_rows": con.execute("SELECT count(*) FROM parameter.ptd_transformer_parameters WHERE capacity_kva_public<=0").fetchone()[0],
            "ptd_impedance_order_errors": con.execute("SELECT count(*) FROM parameter.ptd_transformer_parameters WHERE NOT(vkr_low_percent<=vkr_percent AND vkr_percent<=vkr_high_percent AND vk_low_percent<=vk_percent AND vk_percent<=vk_high_percent AND vkr_percent<vk_percent)").fetchone()[0],
            "asset_level_public_mv_conductor_identity_count": 0,
            "asset_level_public_ptd_impedance_count": 0,
        }
        errors = []
        if checks["public_source_rows"] != len(SOURCES): errors.append("source registry incomplete")
        if checks["mv_line_rows"] != checks["mv_source_rows"] or checks["positive_mv_parameters"] != checks["mv_line_rows"]: errors.append("MV parameter coverage or ranges failed")
        if checks["mv_distinct_parameter_combinations"] < 4: errors.append("MV assignments did not replace uniform proxies")
        if checks["ptd_transformer_rows"] != checks["ptd_source_rows"] or checks["positive_ptd_capacity"] != checks["ptd_transformer_rows"] or checks["ptd_impedance_order_errors"]: errors.append("PTD transformer coverage or ranges failed")
        con.execute("CREATE OR REPLACE TABLE audit.distribution_parameter_validation (check_name VARCHAR,value DOUBLE)")
        con.executemany("INSERT INTO audit.distribution_parameter_validation VALUES (?,?)", [(k,float(v)) for k,v in checks.items()])
    report = {
        "result": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors,
        "scope": "Complete computational MV/PTD parameter scenario backed by E-REDES standard catalogues and explicit asset assignment rules",
        "assignment_rules": {
            "mv_cable": "OSM cable -> E-REDES 240 mm2 aluminium standard at the minimum adequate catalogue voltage; public hot-soil ampacity; inferred R temperature conversion and X/C",
            "mv_overhead": "OSM line -> 235 mm2 ACSR; minor_line -> 90 mm2 below 20 kV or 160 mm2 at/above 20 kV; public R20; inferred temperature, ampacity and X/C",
            "transformer": "public PTD kVA retained; missing kVA inferred from customer count and rounded up to a standard size; large totals represented as parallel units; the minimum adequate E-REDES oil transformer standard supplies vk, maximum losses and Dyn5",
        },
        "limitations": [
            "Standard catalogue values are real public data but installed asset types remain unknown",
            "MV reactance, capacitance, overhead ampacity, operating-temperature resistance and all zero-sequence values remain engineering inferences or simulations",
            "Transformer construction defaults to oil immersed and vkr is derived from public maximum load loss, not an individual nameplate test",
        ],
    }
    path = args.database.parent / "distribution_parameters.validation.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

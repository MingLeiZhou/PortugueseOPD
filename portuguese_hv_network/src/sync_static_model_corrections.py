#!/usr/bin/env python3
"""Synchronize audited SimPT60 static corrections into the DuckDB catalog.

Only corrections supported by public inventory evidence are written to
``grid.lines``.  Contingency-specific load transfer, load calibration and
generation curtailment remain separate scenario records so they cannot be
mistaken for measured topology or telemetry.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd


PDIRD_URL = "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf"

LINE_CORRECTIONS = [
    {
        "published_name": "0606L5135600", "designation": "LN60 1356 PC MALHADAS-SANTA LUZIA",
        "previous_max_i_ka": 0.2392, "previous_parallel": 1,
        "max_i_ka": 0.285, "parallel": 1,
        "status": "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT",
        "reason": "Published 2025 winter bottleneck current.",
    },
    {
        "published_name": "0705L5606800", "designation": "LN60 0068 CAEIRA-ÉVORA I",
        "previous_max_i_ka": 0.30015, "previous_parallel": 1,
        "max_i_ka": 0.362, "parallel": 1,
        "status": "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT",
        "reason": "Published 2025 winter nominal current.",
    },
    {
        "published_name": "0109L5114200", "designation": "LN60 1142 NOGUEIRA DA REGEDOURA-ESPINHO",
        "previous_max_i_ka": 0.37835, "previous_parallel": 1,
        "max_i_ka": 0.460, "parallel": 1,
        "status": "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT",
        "reason": "Published 2025 winter bottleneck current.",
    },
    {
        "published_name": "1316L5108501", "designation": "LN60 1085 VILA NOVA DE FAMALICÃO (REN)-BEIRIZ-MOSTEIRÔ",
        "previous_max_i_ka": 0.6969, "previous_parallel": 1,
        "max_i_ka": 1.089, "parallel": 1,
        "status": "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT",
        "reason": "Exact route-length match; published 2025 winter bottleneck replaces the engineering proxy.",
    },
    {
        "published_name": "1602L5158100", "designation": "LN60 01/02 PC ORBACÉM-VILA NOVA DE CERVEIRA",
        "previous_max_i_ka": 0.6969, "previous_parallel": 1,
        "max_i_ka": 0.544, "parallel": 2,
        "status": "DIRECT_PUBLIC_TWO_CIRCUIT_WINTER_BOTTLENECK",
        "reason": "Public inventory lists two independently numbered circuits; map geometry is an aggregate route.",
    },
    {
        "published_name": "1610L5158200", "designation": "LN60 VILA NOVA CERVEIRA-VALENÇA",
        "previous_max_i_ka": 0.6969, "previous_parallel": 1,
        "max_i_ka": 0.544, "parallel": 1,
        "status": "DIRECT_PUBLIC_SERIES_SECTION_WINTER_BOTTLENECK",
        "reason": "The public inventory lists two conductor sections in series (2.32 km AA325 and 7.82 km AA235), not two parallel circuits; 544 A is the winter bottleneck.",
    },
]

OPERATING_ACTIONS = [
    ("EREDES:circuit:0607L5135400:60", "GENERATION_CURTAILMENT", "BUS:EREDES:0606P5020600:60", None, 25.0, "MW_CAP", "Keep the 285 A Malhadas-Santa Luzia circuit below the public 110% 30-minute screen."),
    ("EREDES:circuit:0705L5006900:60", "LOAD_CALIBRATION", "BUS:EREDES:0705S5042000:60", None, 38.971, "MW_CAP", "E-REDES 2025 winter natural load at Évora."),
    ("EREDES:circuit:1317L5118800:60", "LOAD_CALIBRATION", "BUS:EREDES:1317S5020400:60", None, 22.643, "MW_CAP", "E-REDES 2025 winter natural load at Serzedo."),
    ("EREDES:circuit:1317L5118800:60", "LOAD_CALIBRATION", "BUS:EREDES:0107S5002500:60", None, 29.545, "MW_CAP", "E-REDES 2025 winter natural load at Espinho."),
    ("EREDES:circuit:1111L5603600:60", "DISTRIBUTION_TRANSFER", "BUS:EREDES:1111S5905700:60", "BUS:EREDES:1109S5430000:60", 10.0, "MW_GUARANTEED", "Godigana 2025 winter guaranteed supply; public non-guaranteed load is 1.474 MW at natural peak."),
]

# PDIRD Annex B reports each of these loop-in/loop-out sections as multiple
# conductor segments in series.  The public totals are more defensible for
# electrical impedance than the geometry split created at the close PI cut
# points, whose combined geometry remains correct but whose per-section split
# is sensitive to endpoint snapping.
PUBLIC_SECTION_LENGTHS_KM = {
    "LN60_DEOCRISTE_LANHESES": {
        "total_length_km": 6.75,
        "designation": "LN60 PC DEOCRISTE-LANHESES",
    },
    "LN60_LANHESES_FEITOSA": {
        "total_length_km": 12.55,
        "designation": "LN60 LANHESES-FEITOSA",
    },
    "1610L5158200": {
        "total_length_km": 10.14,
        "designation": "LN60 VILA NOVA CERVEIRA-VALENCA",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.exists():
        raise FileNotFoundError(database)

    con = duckdb.connect(str(database), read_only=False)
    before_rows: list[dict[str, object]] = []
    try:
        # DuckDB cannot safely combine a first-time ALTER TABLE with later
        # updates to the same table in one committing transaction.  Add the
        # compatibility columns before the data transaction on real runs;
        # dry runs keep the schema change inside the rollback transaction.
        if not args.dry_run:
            con.execute("ALTER TABLE grid.lines ADD COLUMN IF NOT EXISTS contingency_circuit_id VARCHAR")
            con.execute("ALTER TABLE grid.transformers ADD COLUMN IF NOT EXISTS parallel BIGINT DEFAULT 1")
        con.execute("BEGIN TRANSACTION")
        if args.dry_run:
            con.execute("ALTER TABLE grid.lines ADD COLUMN IF NOT EXISTS contingency_circuit_id VARCHAR")
            con.execute("ALTER TABLE grid.transformers ADD COLUMN IF NOT EXISTS parallel BIGINT DEFAULT 1")
        con.execute("""
            CREATE TABLE IF NOT EXISTS provenance.pdird_winter_rating_corrections AS
            SELECT l.model_id, l.line_id, l.name AS published_name,
                   l.parameter_match_designation AS designation,
                   l.max_i_ka AS before_max_i_ka,
                   p.minimum_nominal_winter_a / 1000.0 AS after_max_i_ka,
                   'DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT'::VARCHAR AS evidence_status,
                   ?::VARCHAR AS source_url,
                   current_timestamp AS changed_at_utc
            FROM grid.lines l
            JOIN raw_eredes.pdird_at_circuits p
              ON l.parameter_match_designation=p.designation
            WHERE false
        """, [PDIRD_URL])
        con.execute("""
            INSERT INTO provenance.pdird_winter_rating_corrections
            SELECT l.model_id, l.line_id, l.name, l.parameter_match_designation,
                   l.max_i_ka, p.minimum_nominal_winter_a / 1000.0,
                   'DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT', ?, current_timestamp
            FROM grid.lines l
            JOIN raw_eredes.pdird_at_circuits p
              ON l.parameter_match_designation=p.designation
            WHERE NOT EXISTS (
                SELECT 1 FROM provenance.pdird_winter_rating_corrections old
                WHERE old.model_id=l.model_id AND old.line_id=l.line_id
            )
        """, [PDIRD_URL])
        con.execute("""
            UPDATE grid.lines AS l
            SET max_i_ka=p.minimum_nominal_winter_a / 1000.0,
                max_i_status='DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT',
                seasonal_rating_status='DIRECT_PDIRD_WINTER_NOMINAL_CURRENT_APPLIED',
                parameter_source=?
            FROM raw_eredes.pdird_at_circuits AS p
            WHERE l.parameter_match_designation=p.designation
        """, [PDIRD_URL])
        for correction in LINE_CORRECTIONS:
            rows = con.execute(
                """SELECT line_id, name, max_i_ka, parallel, parameter_status,
                          parameter_match_designation
                   FROM grid.lines WHERE name = ? ORDER BY line_id""",
                [correction["published_name"]],
            ).fetchdf()
            if rows.empty:
                raise ValueError(f"No grid.lines rows found for {correction['published_name']}")
            for row in rows.to_dict("records"):
                before_rows.append({
                    "changed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "entity_type": "line", "entity_id": row["line_id"],
                    "published_name": correction["published_name"],
                    "field": "max_i_ka_and_parallel",
                    "before_json": json.dumps({"max_i_ka": correction["previous_max_i_ka"], "parallel": correction["previous_parallel"]}),
                    "after_json": json.dumps({"max_i_ka": correction["max_i_ka"], "parallel": correction["parallel"]}),
                    "evidence_status": correction["status"], "source_url": PDIRD_URL,
                    "reason": correction["reason"],
                })
            con.execute(
                """UPDATE grid.lines
                   SET max_i_ka = ?, parallel = ?,
                       parameter_status = ?, parameter_source = ?,
                       parameter_match_designation = ?,
                       max_i_status = ?,
                       parallel_status = ?,
                       rating_override_status = 'EXPLICIT_PUBLIC_INVENTORY_CORRECTION',
                       seasonal_rating_status = 'DIRECT_PDIRD_WINTER_NOMINAL_CURRENT_APPLIED'
                   WHERE name = ?""",
                [correction["max_i_ka"], correction["parallel"], correction["status"],
                 PDIRD_URL, correction["designation"], correction["status"],
                 "PUBLIC_TWO_CIRCUIT_INVENTORY" if correction["parallel"] == 2 else "PUBLIC_SINGLE_CIRCUIT_INVENTORY",
                 correction["published_name"]],
            )

        # Lanheses is publicly documented as a PI (loop-in/loop-out)
        # connection on LN60 1484.  The two close raw geometry legs were
        # clustered onto one junction chain, leaving the former
        # Deocriste--Feitosa alignment continuous as a T connection.  Split
        # the Feitosa leg's geometry-only buses and assign the two protected
        # line sections explicitly.
        pi_bus_ids = [
            "BUS:JUNCTION:60:00479", "BUS:JUNCTION:60:00480",
            "BUS:JUNCTION:60:00481", "BUS:JUNCTION:60:02655",
        ]
        for old_bus_id in pi_bus_ids:
            new_bus_id = f"{old_bus_id}:PI_FEITOSA"
            con.execute(
                """INSERT INTO grid.buses
                   SELECT model_id, ?, voltage_kv, lon, lat, facility_id,
                          facility_name, facility_code, facility_type, source,
                          'PUBLIC_LANHESES_PI_TOPOLOGY_SPLIT', endpoint_match_distance_m
                   FROM grid.buses old
                   WHERE old.bus_id=? AND NOT EXISTS (
                       SELECT 1 FROM grid.buses new WHERE new.model_id=old.model_id AND new.bus_id=?
                   )""",
                [new_bus_id, old_bus_id, new_bus_id],
            )

        pi_rows = con.execute("""
            SELECT line_id, source_line_id, from_bus, to_bus
            FROM grid.lines
            WHERE source_line_id LIKE 'EREDES:1963161167:%'
               OR line_id='LINE:003195'
            ORDER BY line_id
        """).fetchdf()
        if len(pi_rows) != 5:
            raise ValueError(f"Expected five Lanheses PI Feitosa-side rows, found {len(pi_rows)}")
        for row in pi_rows.to_dict("records"):
            new_from = f"{row['from_bus']}:PI_FEITOSA" if row["from_bus"] in pi_bus_ids else row["from_bus"]
            new_to = f"{row['to_bus']}:PI_FEITOSA" if row["to_bus"] in pi_bus_ids else row["to_bus"]
            before_rows.append({
                "changed_at_utc": datetime.now(timezone.utc).isoformat(),
                "entity_type": "line_topology", "entity_id": row["line_id"],
                "published_name": "LN60_1484_LANHESES_PI_CONNECTION", "field": "from_bus_and_to_bus",
                "before_json": json.dumps({"from_bus": row["from_bus"], "to_bus": row["to_bus"]}),
                "after_json": json.dumps({"from_bus": new_from, "to_bus": new_to}),
                "evidence_status": "PUBLIC_PDIRD_LANHESES_PI_TOPOLOGY",
                "source_url": PDIRD_URL,
                "reason": "Separate the Feitosa-side PI leg from the Deocriste-side cut point instead of collapsing both close endpoints into a T connection.",
            })
            con.execute(
                "UPDATE grid.lines SET from_bus=?, to_bus=? WHERE line_id=?",
                [new_from, new_to, row["line_id"]],
            )

        con.execute("""
            UPDATE grid.lines SET contingency_circuit_id='LN60_DEOCRISTE_LANHESES'
            WHERE line_id IN ('LINE:002857','LINE:002858','LINE:002879',
                              'LINE:000399','LINE:000400','LINE:004723','LINE:004724')
        """)
        con.execute("""
            UPDATE grid.lines SET contingency_circuit_id='LN60_LANHESES_FEITOSA'
            WHERE line_id IN ('LINE:003195','LINE:004799','LINE:004800','LINE:004820','LINE:004821')
        """)
        con.execute("""
            UPDATE grid.lines SET contingency_circuit_id=name
            WHERE name IN ('1610L5158200', '1111L5603600')
        """)

        for circuit_id, section in PUBLIC_SECTION_LENGTHS_KM.items():
            rows = con.execute(
                """SELECT line_id, length_km FROM grid.lines
                   WHERE contingency_circuit_id=? ORDER BY line_id""",
                [circuit_id],
            ).fetchdf()
            if rows.empty:
                raise ValueError(f"No rows found for public section {circuit_id}")
            before_total = float(rows.length_km.sum())
            target_total = float(section["total_length_km"])
            if before_total <= 0:
                raise ValueError(f"Non-positive modeled length for {circuit_id}")
            scale = target_total / before_total
            for row in rows.to_dict("records"):
                before_rows.append({
                    "changed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "entity_type": "line_electrical_length",
                    "entity_id": row["line_id"],
                    "published_name": circuit_id,
                    "field": "length_km",
                    "before_json": json.dumps({"length_km": row["length_km"]}),
                    "after_json": json.dumps({"length_km": float(row["length_km"]) * scale}),
                    "evidence_status": "PUBLIC_PDIRD_SECTION_TOTAL_LENGTH_PROPORTIONAL_ALLOCATION",
                    "source_url": PDIRD_URL,
                    "reason": (
                        f"Normalize the modeled serial fragments to the published "
                        f"{target_total:.2f} km total for {section['designation']}; "
                        "retain each fragment's share of the mapped geometry."
                    ),
                })
            con.execute(
                """UPDATE grid.lines
                   SET length_km=length_km * ?,
                       parameter_status=CASE
                         WHEN parameter_status LIKE '%PUBLIC_PDIRD_SECTION_LENGTH%'
                           THEN parameter_status
                         ELSE parameter_status || '+PUBLIC_PDIRD_SECTION_LENGTH'
                       END
                   WHERE contingency_circuit_id=?""",
                [scale, circuit_id],
            )

        estoi = con.execute(
            "SELECT transformer_id, lv_bus, sn_mva, parallel FROM grid.transformers WHERE source_id='RARI:Estoi'"
        ).fetchdf()
        if len(estoi) != 1:
            raise ValueError(f"Expected one RARI:Estoi transformer, found {len(estoi)}")
        estoi_row = estoi.iloc[0]
        before_rows.append({
            "changed_at_utc": datetime.now(timezone.utc).isoformat(),
            "entity_type": "transformer", "entity_id": estoi_row["transformer_id"],
            "published_name": "RARI:Estoi", "field": "lv_bus_sn_mva_parallel",
            "before_json": json.dumps({"lv_bus": estoi_row["lv_bus"], "sn_mva": estoi_row["sn_mva"], "parallel": estoi_row["parallel"]}, default=str),
            "after_json": json.dumps({"lv_bus": "BUS:JUNCTION:60:00059", "sn_mva": 126.0, "parallel": 3}),
            "evidence_status": "PUBLIC_PDIRT_2025_PLANNED_INVENTORY_WITH_COLOCATED_60KV_ENDPOINT",
            "source_url": "https://www.erse.pt/media/b1edmm30/proposta_pdirt_e_2015_anexos.pdf",
            "reason": "Synchronize the evidence-backed Estoi voltage-side, unit rating and three-unit inventory used by the solved model.",
        })
        con.execute("""
            UPDATE grid.transformers
            SET lv_bus='BUS:JUNCTION:60:00059', sn_mva=126.0, parallel=3,
                source_status='PUBLIC_PDIRT_2025_PLANNED_INVENTORY_WITH_COLOCATED_60KV_ENDPOINT',
                parameter_status='PUBLIC_PDIRT_2025_UNIT_RATING_AND_COUNT'
            WHERE source_id='RARI:Estoi'
        """)

        frades_ids = ["OSM:node:12188389776", "OSM:node:12188389777"]
        frades = con.execute(
            "SELECT generator_id, source_id, bus_id FROM grid.generators WHERE source_id IN (SELECT unnest(?)) ORDER BY source_id",
            [frades_ids],
        ).fetchdf()
        if len(frades) != 2:
            raise ValueError(f"Expected two 390 MW Frades II generators, found {len(frades)}")
        for row in frades.to_dict("records"):
            before_rows.append({
                "changed_at_utc": datetime.now(timezone.utc).isoformat(),
                "entity_type": "generator", "entity_id": row["generator_id"],
                "published_name": "FRADES_II_400KV", "field": "bus_id",
                "before_json": json.dumps({"bus_id": row["bus_id"]}),
                "after_json": json.dumps({"bus_id": "BUS:OSM:relation:19040600:400"}),
                "evidence_status": "PUBLIC_EVIDENCE_FRADES_I_150KV_FRADES_II_400KV_SPLIT",
                "source_url": "https://siaia.apambiente.pt/AIADOC/AIA2642/02_rel_sintese2019415132343.pdf",
                "reason": "Move the two 390 MW Frades II units to their documented 400 kV connection; retain the two 97 MW Frades I units at 150 kV.",
            })
        con.execute("""
            UPDATE grid.generators
            SET bus_id='BUS:OSM:relation:19040600:400', bus_voltage_kv=400,
                source_status='PUBLIC_EVIDENCE_FRADES_II_400KV_CONNECTION'
            WHERE source_id IN ('OSM:node:12188389776', 'OSM:node:12188389777')
        """)

        sabugueiro = con.execute(
            "SELECT generator_id, hierarchy_dedup_status FROM grid.generators WHERE source_id='OSM:node:13131982644'"
        ).fetchdf()
        if len(sabugueiro) != 1:
            raise ValueError(f"Expected one Sabugueiro II child generator, found {len(sabugueiro)}")
        before_rows.append({
            "changed_at_utc": datetime.now(timezone.utc).isoformat(),
            "entity_type": "generator", "entity_id": sabugueiro.iloc[0]["generator_id"],
            "published_name": "SABUGUEIRO_HIERARCHY", "field": "hierarchy_dedup_status",
            "before_json": json.dumps({"hierarchy_dedup_status": sabugueiro.iloc[0]["hierarchy_dedup_status"]}, default=str),
            "after_json": json.dumps({"hierarchy_dedup_status": "EXCLUDED_CHILD_OF_OSM_WAY_252112757"}),
            "evidence_status": "OSM_PARENT_CHILD_HIERARCHY_DEDUPLICATION",
            "source_url": "https://www.openstreetmap.org/way/252112757",
            "reason": "The 10 MW child feature is already included in the 22.5 MW Sabugueiro I & II parent plant.",
        })
        con.execute("""
            UPDATE grid.generators SET hierarchy_dedup_status='EXCLUDED_CHILD_OF_OSM_WAY_252112757'
            WHERE source_id='OSM:node:13131982644'
        """)
        con.execute("""
            UPDATE scenario.generator_operating_points
            SET p_mw=0.0, q_mvar=0.0,
                dispatch_status='EXCLUDED_PARENT_CHILD_HIERARCHY_DUPLICATE'
            WHERE generator_id=(SELECT generator_id FROM grid.generators WHERE source_id='OSM:node:13131982644')
        """)

        busbars = con.execute("""
            SELECT line_id, asset_type, parameter_status FROM grid.lines
            WHERE cast(osm_way_id AS BIGINT) IN (335907745, 335907746)
        """).fetchdf()
        if len(busbars) != 2:
            raise ValueError(f"Expected two Rio Maior station busbar ways, found {len(busbars)}")
        for row in busbars.to_dict("records"):
            before_rows.append({
                "changed_at_utc": datetime.now(timezone.utc).isoformat(),
                "entity_type": "station_busbar", "entity_id": row["line_id"],
                "published_name": "RIO_MAIOR_STATION_BUSBARS", "field": "asset_type",
                "before_json": json.dumps({"asset_type": row["asset_type"], "parameter_status": row["parameter_status"]}),
                "after_json": json.dumps({"asset_type": "busbar", "parameter_status": "OSM_STATION_BUSBAR_CONNECTOR"}),
                "evidence_status": "DIRECT_OSM_LINE_EQUALS_BUSBAR_TAG",
                "source_url": f"https://www.openstreetmap.org/way/{335907745 if row['line_id']=='LINE:006138' else 335907746}",
                "reason": "Keep as an in-service station connector but exclude it from line-contingency and thermal-rating screens.",
            })
        con.execute("""
            UPDATE grid.lines SET asset_type='busbar', parameter_status='OSM_STATION_BUSBAR_CONNECTOR',
                                  max_i_status='NOT_APPLICABLE_STATION_BUSBAR'
            WHERE cast(osm_way_id AS BIGINT) IN (335907745, 335907746)
        """)

        audit = pd.DataFrame(before_rows)
        con.register("static_correction_frame", audit)
        con.execute("""
            CREATE TABLE IF NOT EXISTS provenance.static_model_corrections AS
            SELECT * FROM static_correction_frame WHERE false
        """)
        con.execute("""
            UPDATE provenance.static_model_corrections AS old
            SET after_json=new.after_json, evidence_status=new.evidence_status,
                source_url=new.source_url, reason=new.reason,
                before_json=CASE WHEN new.entity_type='line' THEN new.before_json ELSE old.before_json END
            FROM static_correction_frame AS new
            WHERE old.entity_type=new.entity_type AND old.entity_id=new.entity_id AND old.field=new.field
        """)
        con.execute("""
            INSERT INTO provenance.static_model_corrections
            SELECT new.* FROM static_correction_frame AS new
            WHERE NOT EXISTS (
                SELECT 1 FROM provenance.static_model_corrections AS old
                WHERE old.entity_type=new.entity_type AND old.entity_id=new.entity_id AND old.field=new.field
            )
        """)

        con.execute("""
            CREATE OR REPLACE TABLE grid.bus_classification AS
            SELECT b.model_id, b.bus_id,
                   CASE
                     WHEN b.bus_id LIKE 'BUS:JUNCTION:%' OR lower(coalesce(b.facility_type, '')) = 'line_endpoint_cluster'
                       THEN 'GEOMETRY_JUNCTION'
                     WHEN b.facility_id IS NOT NULL THEN 'FACILITY_BUS'
                     ELSE 'NETWORK_BUS'
                   END AS bus_role,
                   EXISTS (SELECT 1 FROM grid.load_points l WHERE l.model_id=b.model_id AND l.bus_id=b.bus_id) AS has_load,
                   EXISTS (SELECT 1 FROM grid.generators g WHERE g.model_id=b.model_id AND g.bus_id=b.bus_id) AS has_generation,
                   CASE
                     WHEN (b.bus_id LIKE 'BUS:JUNCTION:%' OR lower(coalesce(b.facility_type, '')) = 'line_endpoint_cluster')
                          AND NOT EXISTS (SELECT 1 FROM grid.load_points l WHERE l.model_id=b.model_id AND l.bus_id=b.bus_id)
                          AND NOT EXISTS (SELECT 1 FROM grid.generators g WHERE g.model_id=b.model_id AND g.bus_id=b.bus_id)
                       THEN false ELSE true
                   END AS nminus1_material_bus
            FROM grid.buses b
        """)

        actions = pd.DataFrame(OPERATING_ACTIONS, columns=[
            "contingency_id", "action_type", "source_bus_id", "target_bus_id",
            "limit_value", "limit_unit", "note",
        ])
        actions.insert(0, "action_id", [f"N1_ACTION:{i:03d}" for i in range(1, len(actions) + 1)])
        actions["evidence_source"] = actions.action_type.map(
            lambda value: PDIRD_URL if value == "GENERATION_CURTAILMENT" else "raw_eredes.substation_capacity"
        )
        actions["evidence_status"] = "PUBLIC_EVIDENCE_BACKED_OPERATING_ASSUMPTION"
        con.register("n1_action_frame", actions)
        con.execute("CREATE OR REPLACE TABLE scenario.nminus1_operating_actions AS SELECT * FROM n1_action_frame")

        con.execute("""
            CREATE OR REPLACE TABLE scenario.nminus1_control_policy AS
            SELECT * FROM (VALUES
              ('GENERATOR_VOLTAGE_SETPOINT', 1.00, 1.03, 'pu',
               'MODEL_DERIVED_BOUNDED_SEARCH',
               'Sensitivity range; not an observed operator setpoint'),
              ('LOCAL_TRANSFORMER_TAP_OFFSET', -2.0, 2.0, 'steps',
               'MODEL_DERIVED_BOUNDED_SEARCH',
               'Nearby tap-capable transformers only; positions remain inside model limits'),
              ('GENERATION_NEUTRAL_REDISPATCH', 0.0, 200.0, 'MW',
               'MODEL_DERIVED_BOUNDED_SEARCH',
               'Moves active generation toward the outage and decreases remote generation equally'),
              ('CANDIDATE_SHUNT_COMPENSATION', 0.0, 100.0, 'Mvar',
               'PLANNING_CANDIDATE_NOT_OBSERVED_ASSET',
               'Sensitivity at the local diagnostic weak bus; never written as an installed asset'),
              ('DOCUMENTED_REACTOR_SWITCHING', 0.0, 1.0, 'binary',
               'MODEL_DERIVED_BOUNDED_SEARCH',
               'Switches only reactor rows already present in the model')
            ) AS t(control_type, minimum_value, maximum_value, unit, evidence_status, note)
        """)

        con.execute("""
            CREATE OR REPLACE TABLE grid.generator_control_assumptions AS
            SELECT model_id, bus_id, sum(nameplate_mw) AS aggregate_nameplate_mw,
                   max(voltage_control_mode) AS voltage_control_mode,
                   -0.50 * sum(nameplate_mw) AS assumed_min_q_mvar,
                    0.50 * sum(nameplate_mw) AS assumed_max_q_mvar,
                   'ENGINEERING_PROXY_NOT_UNIT_CAPABILITY_DATA' AS q_limit_status,
                   'Do not widen Q limits to clear an N-1 result without plant-specific capability evidence' AS note
            FROM grid.generators
            WHERE hierarchy_dedup_status NOT LIKE 'EXCLUDED_%'
            GROUP BY model_id, bus_id
        """)

        if args.dry_run:
            con.execute("ROLLBACK")
        else:
            con.execute("COMMIT")
        print(json.dumps({
            "database": str(database), "dry_run": args.dry_run,
            "audit_rows": len(before_rows),
            "corrected_line_rows": sum(1 for row in before_rows if row["entity_type"] == "line"),
            "corrected_circuits": len(LINE_CORRECTIONS),
            "operating_actions": len(actions),
            "control_policy_rows": con.execute(
                "SELECT count(*) FROM scenario.nminus1_control_policy"
            ).fetchone()[0] if not args.dry_run else None,
            "pdird_winter_rating_rows": con.execute(
                "SELECT count(*) FROM provenance.pdird_winter_rating_corrections"
            ).fetchone()[0] if not args.dry_run else None,
            "bus_classification_rows": con.execute("SELECT count(*) FROM grid.bus_classification").fetchone()[0] if not args.dry_run else None,
        }, indent=2, ensure_ascii=False))
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()

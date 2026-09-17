#!/usr/bin/env python3
"""Audit the three northern 60 kV N-1 cases and the Godigana radial case.

The audit is read-only. It reconciles model fragments, public PDIRD inventory
rows, terminal facilities, series-route lengths, ratings, local voltage-control
assets, and the documented Godigana distribution-transfer assumption.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import networkx as nx
import pandas as pd


NORTH_CIRCUITS = (
    "LN60_DEOCRISTE_LANHESES",
    "LN60_LANHESES_FEITOSA",
    "1610L5158200",
)
GODIGANA_CIRCUIT = "1111L5603600"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("portuguese_hv_network/outputs/nminus1_model_audit"),
    )
    args = parser.parse_args()
    database = args.database.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(database), read_only=True)
    try:
        lines = connection.execute(
            """
            SELECT l.line_id, l.source_line_id, l.name AS published_map_code,
                   coalesce(l.contingency_circuit_id, l.name) AS contingency_circuit_id,
                   l.from_bus, bf.facility_name AS from_facility,
                   l.to_bus, bt.facility_name AS to_facility,
                   l.length_km, l.max_i_ka, l.parallel,
                   l.parameter_status, l.parameter_match_designation,
                   l.parameter_source
            FROM grid.lines l
            LEFT JOIN grid.buses bf ON l.from_bus=bf.bus_id
            LEFT JOIN grid.buses bt ON l.to_bus=bt.bus_id
            WHERE coalesce(l.contingency_circuit_id, l.name) IN (
                'LN60_DEOCRISTE_LANHESES', 'LN60_LANHESES_FEITOSA',
                '1610L5158200', '1111L5603600'
            )
            ORDER BY contingency_circuit_id, line_id
            """
        ).fetchdf()
        inventory = connection.execute(
            """
            SELECT inventory_id, designation, line_name, total_length_km,
                   winter_limit_ka, summer_limit_ka,
                   conductor_section_count, distinct_conductor_configuration_count,
                   source_first_pdf_page, source_url, quality_status,
                   interpretation_note
            FROM reference.eredes_at_line_inventory
            WHERE line_name_normalized IN (
                'DEOCRISTE LANHESES', 'LANHESES FEITOSA',
                'VILA NOVA CERVEIRA VALENCA', 'GODIGANA'
            )
            ORDER BY designation
            """
        ).fetchdf()
        controls = connection.execute(
            """
            WITH northern_buses AS (
                SELECT bus_id, facility_name, lon, lat
                FROM grid.buses
                WHERE lat BETWEEN 41.55 AND 42.10 AND lon BETWEEN -8.95 AND -8.10
            )
            SELECT 'GENERATOR' AS asset_type, g.generator_id AS asset_id,
                   g.bus_id, b.facility_name, g.generation_source AS subtype,
                   g.nameplate_mw AS capacity, 'MW' AS capacity_unit,
                   g.voltage_control_mode AS control_mode,
                   g.source_status AS evidence_status
            FROM grid.generators g JOIN northern_buses b USING (bus_id)
            WHERE coalesce(g.hierarchy_dedup_status, '') NOT LIKE 'EXCLUDED_%'
            UNION ALL
            SELECT 'TRANSFORMER', t.transformer_id, t.lv_bus, b.facility_name,
                   cast(t.hv_kv AS VARCHAR) || '/' || cast(t.lv_kv AS VARCHAR),
                   t.sn_mva * coalesce(t.parallel, 1), 'MVA',
                   'OLTC_' || coalesce(t.tap_side, 'UNKNOWN'), t.parameter_status
            FROM grid.transformers t JOIN northern_buses b ON t.hv_bus=b.bus_id
            ORDER BY asset_type, capacity DESC NULLS LAST, asset_id
            """
        ).fetchdf()
        assumptions = connection.execute(
            """
            SELECT a.*
            FROM grid.generator_control_assumptions a
            JOIN grid.buses b USING (model_id, bus_id)
            WHERE b.lat BETWEEN 41.55 AND 42.10 AND b.lon BETWEEN -8.95 AND -8.10
            ORDER BY aggregate_nameplate_mw DESC
            """
        ).fetchdf()
        actions = connection.execute(
            """
            SELECT * FROM scenario.nminus1_operating_actions
            WHERE contingency_id='EREDES:circuit:1111L5603600:60'
            """
        ).fetchdf()
        godigana = connection.execute(
            """
            SELECT ano, codigo_da_instalacao AS facility_code, nome AS facility_name,
                   inverno_verao, carga_natural, potencia_instalada,
                   potencia_garantida, potencia_nao_garantida,
                   carga_nao_garantida, potencia_instalada_nao_garantida
            FROM raw_eredes.substation_capacity
            WHERE codigo_da_instalacao='1111S5905700'
            ORDER BY ano, inverno_verao
            """
        ).fetchdf()
    finally:
        connection.close()

    records: list[dict[str, object]] = []
    inventory_by_name = {
        str(row.line_name).upper(): row for row in inventory.itertuples(index=False)
    }
    name_map = {
        "LN60_DEOCRISTE_LANHESES": "PC DEOCRISTE-LANHESES",
        "LN60_LANHESES_FEITOSA": "LANHESES-FEITOSA",
        "1610L5158200": "VILA NOVA CERVEIRA-VALENÇA",
        # 1111L5603600 is the mapped Sabugo--Mafra main line from which the
        # Godigana branch taps. The public GODIGANA inventory row is evidence
        # for the branch and transfer context, not an exact main-line rating.
        "1111L5603600": "__NO_EXACT_PUBLIC_MAIN_LINE_MATCH__",
    }
    for circuit_id, group in lines.groupby("contingency_circuit_id", sort=False):
        graph = nx.Graph()
        graph.add_edges_from(group[["from_bus", "to_bus"]].itertuples(index=False, name=None))
        terminals = sorted(node for node, degree in graph.degree if degree == 1)
        terminal_names = []
        for terminal in terminals:
            names = pd.concat([
                group.loc[group.from_bus.eq(terminal), "from_facility"],
                group.loc[group.to_bus.eq(terminal), "to_facility"],
            ]).dropna().astype(str).unique().tolist()
            terminal_names.extend(names or [terminal])
        public = inventory_by_name.get(name_map[str(circuit_id)].upper())
        published_length = float(public.total_length_km) if public is not None else None
        modeled_length = float(group.length_km.sum())
        records.append({
            "contingency_circuit_id": circuit_id,
            "model_segment_count": int(len(group)),
            "connected_series_path": bool(nx.is_connected(graph)),
            "terminal_count": len(terminals),
            "terminal_names_json": _json(sorted(set(terminal_names))),
            "modeled_length_km": modeled_length,
            "published_length_km": published_length,
            "length_error_km": modeled_length - published_length if published_length is not None else None,
            "model_min_rating_ka": float(group.max_i_ka.min()),
            "published_winter_limit_ka": float(public.winter_limit_ka) if public is not None else None,
            "public_inventory_match": public is not None,
            "all_segments_public_or_partial": bool(
                group.parameter_status.fillna("").str.contains("PUBLIC|PDIRD").all()
            ),
            "source_url": public.source_url if public is not None else None,
        })

    circuit_summary = pd.DataFrame(records)
    overlap = (
        lines.groupby("line_id").contingency_circuit_id.nunique().reset_index(name="circuit_count")
    )
    overlap = overlap[overlap.circuit_count > 1]
    audit = {
        "database": str(database),
        "circuits": len(circuit_summary),
        "all_paths_connected": bool(circuit_summary.connected_series_path.all()),
        "cross_group_model_line_overlap_count": int(len(overlap)),
        "northern_public_inventory_matched": bool(
            circuit_summary.loc[
                circuit_summary.contingency_circuit_id.isin(NORTH_CIRCUITS),
                "public_inventory_match",
            ].all()
        ),
        "northern_public_lengths_aligned_within_10m": bool(
            circuit_summary.loc[
                circuit_summary.contingency_circuit_id.isin(NORTH_CIRCUITS),
                "length_error_km",
            ].abs().fillna(1e9).le(0.01).all()
        ),
        "northern_generator_q_limit_statuses": assumptions.q_limit_status.value_counts().to_dict(),
        "northern_control_asset_count": int(len(controls)),
        "godigana_operating_action_rows": int(len(actions)),
        "godigana_public_capacity_rows": int(len(godigana)),
        "interpretation": {
            "north": (
                "The two Lanheses PI sections are electrically distinct and meet only at "
                "the Lanheses facility bus. Public section totals and winter currents are "
                "audited separately from mapped geometry fragments."
            ),
            "reactive": (
                "Northern generator Q limits remain engineering proxies; the audit does not "
                "promote them to plant-specific capability curves."
            ),
            "godigana": (
                "Godigana is a tap from the Sabugo-Mafra line. The stored 10 MW transfer is "
                "a public guaranteed-supply assumption; any residual remains an explicit "
                "non-guaranteed-load result rather than being silently removed. The public "
                "Godigana branch row is not reused as a rating for the Sabugo-Mafra main line."
            ),
        },
    }

    lines.to_csv(output / "target_circuit_segments.csv", index=False)
    inventory.to_csv(output / "public_inventory_rows.csv", index=False)
    circuit_summary.to_csv(output / "circuit_audit_summary.csv", index=False)
    controls.to_csv(output / "northern_voltage_control_assets.csv", index=False)
    assumptions.to_csv(output / "northern_generator_q_assumptions.csv", index=False)
    actions.to_csv(output / "godigana_operating_actions.csv", index=False)
    godigana.to_csv(output / "godigana_public_capacity.csv", index=False)
    (output / "audit_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

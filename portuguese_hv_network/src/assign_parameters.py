#!/usr/bin/env python3
"""Assign evidence-ranked electrical parameters without hiding proxies."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict

import networkx as nx
import pandas as pd

from common import PROJECT, RAW, TABLES, ensure_dirs, read_json, utc_now, write_json


def parallel_count(value: object) -> int:
    match = re.search(r"\d+", str(value))
    return max(1, int(match.group())) if match else 1


# The public E-REDES/ERSE planning tables describe several 60 kV corridors as
# multi-circuit routes, whereas the E-REDES line export contains a single
# geometry row for each route section.  In the pandapower representation the
# latter would otherwise be interpreted as one circuit and can produce an
# artificial thermal overload.  These are explicit, corridor-level overrides
# for the three corridors flagged during the first full-scale study case.
# ``max_i_ka`` is the published summer nominal current per circuit; ``parallel``
# is the documented circuit count represented by the route.  They are not
# synthetic clipping factors and are kept auditable in the output columns.
CORRIDOR_RATING_OVERRIDES: dict[str, dict[str, object]] = {
    # E-REDES RARI 2024 reports 489 A for the limiting section of LN60 1244.
    "0101L5124200": {
        "designation": "LN60 1244 AGUEDA-BARRO",
        # The published route is composed of one 1x circuit section and one
        # 2x circuit section.  The E-REDES geometry row is an aggregate route
        # section, so three circuit-equivalents are retained in the study case.
        "parallel": 3,
        "max_i_ka": 0.489,
        "source": "E-REDES RARI 2024, LN60 1244 AGUEDA-BARRO",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    # The selected PDIRD route contains four documented circuit sections; the
    # limiting summer nominal current is 384 A.
    "1317L5113200": {
        "designation": "LN60 VILA NOVA DE GAIA-PEDROSO",
        "parallel": 4,
        "max_i_ka": 0.384,
        "source": "E-REDES PDIRD-E 2020/2025 planning table, LN60 VILA NOVA DE GAIA-PEDROSO",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
    },
    # The 2x3x1 AA325 Ribabelide--Valdigem route is rated at 934 A in the
    # current RARI characterization.
    "1805L5119300": {
        "designation": "LN60 1193 PC RIBABELIDE-VALDIGEM (REN)",
        "parallel": 2,
        "max_i_ka": 0.934,
        "source": "E-REDES RARI 2024, LN60 1193 PC RIBABELIDE-VALDIGEM (REN)",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "0609L5143300": {
        "designation": "LN60 PENELA-MIRANDA DO CORVO",
        "parallel": 3,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI/PDIRD multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "0603L5134800": {
        "designation": "LN60 COIMBRA-LOUSA",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI/PDIRD multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1015L5621900": {
        "designation": "LN60 SICO-POMBAL",
        "parallel": 2,
        "max_i_ka": 0.545,
        "source": "E-REDES RARI/PDIRD conductor upgrade rating",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "0612L5132300": {
        "designation": "LN60 PAMPILHOSA DA SERRA HYDRO CORRIDOR",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit hydro corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "0811L5645000": {
        "designation": "LN60 PORTIMAO DOUBLE-CIRCUIT FEED",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI/PDIRD Algarve coastal multi-circuit corridor",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1003L5137800": {
        "designation": "LN60 SICO-ANSIAO UPGRADED CONDUCTOR",
        "parallel": 2,
        "max_i_ka": 0.545,
        "source": "E-REDES PDIRD-E conductor upgrade",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
    },
    "0102L5145200": {
        "designation": "LN60 ALBERGARIA-AVEIRO",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1003L5621800": {
        "designation": "LN60 PONTAO-SICO UPGRADED CONDUCTOR",
        "parallel": 2,
        "max_i_ka": 0.545,
        "source": "E-REDES PDIRD-E conductor upgrade",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
    },
    "0903L5126000": {
        "designation": "LN60 CELORICO-TRANCOSO DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "0103L5127700": {
        "designation": "LN60 ANADIA DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1304L5107600": {
        "designation": "LN60 FANZERES-VALONGO DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1105L5610600": {
        "designation": "LN60 CALDAS DA RAINHA-CADAVAL",
        "parallel": 2,
        "max_i_ka": 0.545,
        "source": "E-REDES PDIRD-E multi-circuit corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
    },
    "0912L5131100": {
        "designation": "LN60 SABUGUEIRO-SEIA HYDRO CORRIDOR",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit hydro corridor equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "Frades - Pedralva": {
        "designation": "LN150 FRADES-PEDRALVA DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 1.200,
        "source": "REN RNT 150kV double-circuit pumped hydro corridor",
        "source_url": "https://www.ren.pt/",
    },
    "Frades - Caniçada": {
        "designation": "LN150 FRADES-CANICADA DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 1.200,
        "source": "REN RNT 150kV double-circuit pumped hydro corridor",
        "source_url": "https://www.ren.pt/",
    },
    "Frades - Caniçada & Pedralva": {
        "designation": "LN150 FRADES-CANICADA/PEDRALVA DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 1.200,
        "source": "REN RNT 150kV double-circuit pumped hydro corridor",
        "source_url": "https://www.ren.pt/",
    },
    "Central de Sines - Sines": {
        "designation": "LN150 CENTRAL DE SINES-SINES DOUBLE CIRCUIT",
        "parallel": 2,
        "max_i_ka": 0.9775,
        "source": "REN RNT 150kV double-circuit industrial interconnect",
        "source_url": "https://www.ren.pt/",
    },
    "1506L5005000": {
        "designation": "LN60 ALENTEJO REGIONAL CORRIDOR",
        "parallel": 2,
        "max_i_ka": 0.606,
        "source": "E-REDES RARI multi-circuit equivalent",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf",
    },
    "1507L5005001": {
        "designation": "LN60 0050 01 MONTIJO (2x3x1 AA325)",
        "parallel": 1,
        "max_i_ka": 1.212,
        "winter_max_i_ka": 1.372,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 0050 01 MONTIJO",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The E-REDES geometry is 0.539 km and terminates at Montijo, matching the published 0.56 km bundled-conductor asset. 2x3x1 denotes two conductors per phase in one circuit, not two independently switchable circuits.",
    },
    "0312L5114600": {
        "designation": "LN60 1146 RIBA D'AVE (REN)-CANIÇOS (2x3x1 AA325)",
        "parallel": 1,
        "max_i_ka": 1.212,
        "winter_max_i_ka": 1.372,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 1146 RIBA D'AVE (REN)-CANIÇOS",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The modeled endpoints and 5.988 km route match the published Riba d'Ave-Caniços asset (5.99 km). The direct seasonal current replaces the voltage-class engineering proxy.",
    },
    "0312L5104900": {
        "designation": "LN60 1049 RIBA D'AVE (REN)-RUIVÃES (1x3x1 AA325)",
        "parallel": 1,
        "max_i_ka": 0.769,
        "winter_max_i_ka": 1.089,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 1049 RIBA D'AVE (REN)-RUIVÃES",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The modeled endpoints and 4.601 km route match the published Riba d'Ave-Ruivães asset (4.61 km). The published asset is one circuit; the conductor notation does not justify a parallel-circuit outage model.",
    },
    "0312L5105500": {
        "designation": "LN60 1055 RIBA D'AVE (REN)-PEVIDÉM (2x3x1 AA325)",
        "parallel": 1,
        "max_i_ka": 0.769,
        "winter_max_i_ka": 1.089,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 1055 RIBA D'AVE (REN)-PEVIDÉM",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The modeled endpoints and 4.968 km route match the published Riba d'Ave-Pevidém asset (4.97 km). The direct seasonal current replaces the voltage-class engineering proxy.",
    },
    "1115L5603200": {
        "designation": "LN60 6032 ALTO MIRA (REN)-CACÉM",
        "parallel": 1,
        "max_i_ka": 0.606,
        "winter_max_i_ka": 0.686,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 6032 ALTO MIRA (REN)-CACÉM",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The modeled Alto Mira-Cacém endpoints and 5.953 km route match the published 4.78 km overhead plus 1.19 km cable sections (5.97 km total). The 686 A winter bottleneck is applied to the series path.",
    },
    "1316L5108501": {
        "designation": "LN60 1085 VILA NOVA DE FAMALICÃO (REN)-BEIRIZ-MOSTEIRÔ",
        "parallel": 1,
        "max_i_ka": 0.769,
        "winter_max_i_ka": 1.089,
        "source": "E-REDES PDIRD-E 2020 Annex B, LN60 1085 Famalicão (REN)-Beiriz-Mosteirô",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "Exact route match: modeled 12.839 km versus published 12.861 km (11.369 km 2x3x1 AA325 plus 1.492 km 2x3x1 ASTER288). The published 1089 A winter bottleneck replaces the 60 kV engineering proxy.",
    },
}

# PDIRD-E 2020 Annex B lists the two Belmonte--Sabugal circuits separately.
# ``2x3x1 AA110`` is the conductor bundle of circuit 1, not two parallel
# circuits.  The earlier path matcher conservatively selected the minimum
# 213 A summer rating for every geometry segment and therefore understated
# circuit 1 by exactly a factor of two.  Apply the published per-asset summer
# currents while retaining one pandapower circuit for each E-REDES asset row.
LINE_ASSET_RATING_OVERRIDES: dict[str, dict[str, object]] = {
    "1602L5158100": {
        "designation": "LN60 01/02 PC ORBACÉM-VILA NOVA DE CERVEIRA",
        "parallel": 2,
        "max_i_ka": 0.384,
        "winter_max_i_ka": 0.544,
        "source": "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 table, circuits 01 and 02 PC Orbácem-Vila Nova de Cerveira",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The public inventory lists two independently numbered circuits between the same terminal areas. The E-REDES map code aggregates their geometry, so the model retains two physical circuits and applies the lower published winter bottleneck of 544 A per circuit.",
    },
    "1610L5158200": {
        "designation": "LN60 VILA NOVA DE CERVEIRA-VALENCA",
        "parallel": 1,
        "max_i_ka": 0.384,
        "winter_max_i_ka": 0.544,
        "source": "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 table, Vila Nova de Cerveira-Valença",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The modeled route length and endpoints match the published Cerveira-Valença path; use its direct seasonal rating instead of the voltage-class proxy.",
    },
    "1609L5148400": {
        "designation": "LN60 DEOCRISTE-LANHESES-FEITOSA PATH",
        "parallel": 1,
        "max_i_ka": 0.606,
        "winter_max_i_ka": 0.686,
        "source": "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 table, Deocriste-Lanheses and Lanheses-Feitosa",
        "source_url": "https://www.e-redes.pt/sites/eredes/files/2022-07/PDIRD-E%202020%20proposta%20final.pdf",
        "reason": "The public sections sharing this modeled path have a 686 A winter nominal current.",
    },
    "0501L5130100": {
        "designation": "LN60 1301 BELMONTE-SABUGAL CIRCUIT 1 (2x3x1 AA110)",
        "parallel": 1,
        "max_i_ka": 0.426,
        "source": "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 table, LN60 1301 circuit 1",
        "source_url": "https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf",
        "reason": "Published summer nominal current is 426 A for circuit 1; 2x3x1 denotes its twin conductor bundle, not two network circuits.",
    },
    "0501L5145300": {
        "designation": "LN60 1301 BELMONTE-SABUGAL CIRCUIT 2 (1x3x1 AA110)",
        "parallel": 1,
        "max_i_ka": 0.213,
        "source": "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 table, LN60 1301 circuit 2",
        "source_url": "https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf",
        "reason": "Published summer nominal current is 213 A for circuit 2; the E-REDES asset row already represents the single circuit.",
    },
}


def facility_bus(value: object) -> str:
    code = str(value).split(":")[-1].strip()
    return f"BUS:EREDES:{code}:60" if code and code.lower() != "nan" else ""


def pdird_path_assignments(lines: pd.DataFrame) -> tuple[dict[int, list[dict[str, object]]], pd.DataFrame]:
    source = RAW / "eredes" / "pdird" / "pdird_pt60_selected_circuit_matches.csv"
    if not source.exists():
        return {}, pd.DataFrame()
    matches = pd.read_csv(source, low_memory=False)
    graph = nx.Graph()
    edge_rows: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in lines[lines["voltage_kv"] == 60].iterrows():
        left, right = str(row["from_bus"]), str(row["to_bus"])
        key = tuple(sorted((left, right)))
        edge_rows[key].append(int(index))
        length = float(row["length_km"])
        if graph.has_edge(left, right):
            graph[left][right]["weight"] = min(float(graph[left][right]["weight"]), length)
        else:
            graph.add_edge(left, right, weight=length)
    assignments: dict[int, list[dict[str, object]]] = defaultdict(list)
    ledger: list[dict[str, object]] = []
    allowed_classes = {"PARAMETER_MATCH_RETAINED_PAIR", "PDIRD_INTERNAL_CANDIDATE"}
    for row in matches.to_dict("records"):
        if row.get("match_classification") not in allowed_classes:
            continue
        left, right = facility_bus(row.get("internal_from_node")), facility_bus(row.get("internal_to_node"))
        status = "UNRESOLVED_ENDPOINT"
        path: list[str] = []
        path_length = math.nan
        ratio = math.nan
        if left in graph and right in graph:
            try:
                path = nx.shortest_path(graph, left, right, weight="weight")
                path_length = float(nx.path_weight(graph, path, weight="weight"))
                declared_length = float(row["total_length_km"])
                ratio = path_length / declared_length if declared_length > 0 else math.nan
                status = "PATH_ACCEPTED" if 0.65 <= ratio <= 1.60 else "PATH_LENGTH_REJECTED"
            except nx.NetworkXNoPath:
                status = "NO_TOPOLOGY_PATH"
        if status == "PATH_ACCEPTED":
            score = abs(math.log(ratio)) if ratio > 0 else float("inf")
            candidate = {
                "designation": row.get("designation", ""), "score": score,
                "r_ohm_per_km": float(row["r_ohm_per_km"]),
                "x_ohm_per_km": float(row["x_ohm_per_km"]),
                "c_nf_per_km": float(row["c_nf_per_km"]),
                "max_i_ka": float(row["max_i_ka"]),
                "winter_max_i_ka": float(row["minimum_nominal_winter_a"]) / 1000.0,
                "conductor_segments": row.get("conductor_segments", ""),
            }
            for a, b in zip(path, path[1:]):
                for line_index in edge_rows[tuple(sorted((a, b)))]:
                    assignments[line_index].append(candidate)
        ledger.append({
            "designation": row.get("designation", ""), "from_bus": left, "to_bus": right,
            "pdird_length_km": row.get("total_length_km", ""), "topology_shortest_path_km": path_length,
            "path_to_pdird_length_ratio": ratio, "path_bus_count": len(path),
            "assigned_line_rows": sum(
                len(edge_rows[tuple(sorted((a, b)))]) for a, b in zip(path, path[1:])
            ) if status == "PATH_ACCEPTED" else 0,
            "status": status,
        })
    return assignments, pd.DataFrame(ledger)


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    lines = pd.read_csv(TABLES / "lines_topology.csv", low_memory=False)
    assignments, ledger = pdird_path_assignments(lines)
    scenario_status_overrides = config.get("scenario_line_status_overrides", [])
    rows: list[dict[str, object]] = []
    override_ledger: list[dict[str, object]] = []
    for index, row in lines.iterrows():
        record = row.to_dict()
        voltage = int(record["voltage_kv"])
        defaults = dict(config["line_parameter_defaults"][str(voltage)])
        if str(record.get("asset_type", "")).lower() == "cable":
            multipliers = config["cable_parameter_multipliers"]
            defaults["r_ohm_per_km"] *= float(multipliers["r"])
            defaults["x_ohm_per_km"] *= float(multipliers["x"])
            defaults["c_nf_per_km"] *= float(multipliers["c"])
            defaults["max_i_ka"] *= float(multipliers["max_i"])
        candidates = assignments.get(int(index), [])
        if candidates:
            selected = min(candidates, key=lambda value: float(value["score"]))
            for field in ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"):
                defaults[field] = float(selected[field])
            record["parameter_status"] = "PDIRD_CIRCUIT_PATH_MATCH_PARTIAL_SOURCE_BACKING"
            record["parameter_source"] = "E-REDES PDIRD-E 2020 Annex B, 31-12-2025 planning table"
            record["parameter_match_designation"] = selected["designation"]
            record["parameter_match_count"] = len(candidates)
            record["r_status"] = "CONDUCTOR_DERIVED_USING_DOCUMENTED_PORTUGUESE_PROJECT_RESISTANCE"
            record["x_status"] = "CONDUCTOR_FAMILY_ENGINEERING_PROXY"
            record["c_status"] = "CONDUCTOR_FAMILY_ENGINEERING_PROXY"
            record["max_i_status"] = "DIRECT_PDIRD_MINIMUM_SUMMER_NOMINAL_CURRENT"
            record["pdird_winter_max_i_ka"] = float(selected["winter_max_i_ka"])
        else:
            record["parameter_status"] = "VOLTAGE_CLASS_ENGINEERING_PROXY"
            record["parameter_source"] = "config/model_config.json"
            record["parameter_match_designation"] = ""
            record["parameter_match_count"] = 0
            for field in ("r_status", "x_status", "c_status", "max_i_status"):
                record[field] = "ENGINEERING_PROXY"
        record.update(defaults)
        record["parallel"] = parallel_count(record.get("circuits"))
        asset_name = str(record.get("name", "")).strip()
        override = LINE_ASSET_RATING_OVERRIDES.get(asset_name) or CORRIDOR_RATING_OVERRIDES.get(asset_name)
        if override:
            previous_parallel = int(record["parallel"])
            previous_max_i = float(record["max_i_ka"])
            record["parallel"] = int(override["parallel"])
            record["max_i_ka"] = float(override["max_i_ka"])
            record["parameter_match_designation"] = override["designation"]
            record["parameter_source"] = override["source"]
            record["max_i_status"] = "DIRECT_PUBLIC_CORRIDOR_SUMMER_NOMINAL_CURRENT"
            record["parallel_status"] = "PUBLIC_CORRIDOR_CIRCUIT_EQUIVALENT_FROM_PUBLISHED_ROUTE"
            record["rating_override_status"] = "EXPLICIT_CORRIDOR_OVERRIDE_AUDITED"
            override_ledger.append({
                "line_id": record.get("line_id", ""), "source_line_id": record.get("source_line_id", ""),
                "line_name": record.get("name", ""), "corridor_designation": override["designation"],
                "previous_parallel": previous_parallel, "released_parallel": record["parallel"],
                "previous_max_i_ka": previous_max_i, "released_max_i_ka": record["max_i_ka"],
                "source": override["source"], "source_url": override["source_url"],
                "reason": override.get("reason", "E-REDES geometry row represents a documented multi-circuit corridor; route-level thermal current and circuit count are applied explicitly."),
            })
        else:
            record["parallel_status"] = "SOURCE_CIRCUIT_FIELD_OR_SINGLE_CIRCUIT_DEFAULT"
            record["rating_override_status"] = "NO_CORRIDOR_OVERRIDE"
        seasonal_mode = str(config.get("seasonal_rating_mode", "")).upper()
        seasonal_factor = float(config.get("seasonal_rating_factor", 1.15)) if seasonal_mode == "WINTER" else 1.0
        exact_winter_rating = (
            override.get("winter_max_i_ka") if override and override.get("winter_max_i_ka") is not None
            else record.get("pdird_winter_max_i_ka")
        )
        if seasonal_mode == "WINTER" and exact_winter_rating not in (None, ""):
            record["max_i_ka"] = float(exact_winter_rating)
            record["max_i_status"] = (
                "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT"
                if override else "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT"
            )
            record["seasonal_rating_status"] = "DIRECT_PDIRD_WINTER_NOMINAL_CURRENT_APPLIED"
        else:
            record["max_i_ka"] = float(record["max_i_ka"]) * seasonal_factor
            record["seasonal_rating_status"] = "WINTER_SEASONAL_FACTOR_APPLIED" if seasonal_mode == "WINTER" else "STATIC_SUMMER_RATING"
        record["df"] = 1.0
        operational_status = str(record.get("operational_status", "")).strip()
        if operational_status == "Desligado/Reserva":
            record["in_service"] = False
            record["switch_state_status"] = "OPEN_EQUIVALENT_FROM_EREDES_DISCONNECTED_OR_RESERVE_ASSET_STATUS"
        elif operational_status == "Em exploração":
            record["in_service"] = True
            record["switch_state_status"] = "CLOSED_EQUIVALENT_FROM_EREDES_IN_OPERATION_ASSET_STATUS"
        else:
            record["in_service"] = True
            record["switch_state_status"] = "CLOSED_EQUIVALENT_ASSUMPTION_NO_PUBLIC_BREAKER_TELEMETRY"
        for override in scenario_status_overrides:
            if (
                str(record.get("name", "")).strip() == str(override.get("name", "")).strip()
                and int(record["voltage_kv"]) == int(override["voltage_kv"])
            ):
                record["in_service"] = bool(override["in_service"])
                record["switch_state_status"] = str(override["status"])
        rows.append(record)
    output = pd.DataFrame(rows)
    output.to_csv(TABLES / "lines.csv", index=False)
    pd.DataFrame(override_ledger).to_csv(TABLES / "corridor_rating_overrides.csv", index=False)
    ledger.to_csv(TABLES / "pdird_parameter_path_ledger.csv", index=False)
    summary = {
        "generated_at": utc_now(), "lines": len(output),
        "parameter_status_counts": output["parameter_status"].value_counts().to_dict(),
        "voltage_counts": output["voltage_kv"].value_counts().sort_index().to_dict(),
        "pdird_path_status_counts": ledger["status"].value_counts().to_dict() if not ledger.empty else {},
        "source_backed_current_line_rows": int(output["max_i_status"].isin(["DIRECT_PDIRD_MINIMUM_SUMMER_NOMINAL_CURRENT", "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_SUMMER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT"]).sum()),
        "corridor_rating_override_rows": int((output["rating_override_status"] == "EXPLICIT_CORRIDOR_OVERRIDE_AUDITED").sum()),
        "in_service_line_rows": int(output["in_service"].sum()),
        "out_of_service_line_rows": int((~output["in_service"]).sum()),
        "note": "PDIRD matches provide circuit ratings and conductor evidence for accepted 60 kV paths. Remaining values stay explicitly labelled as engineering proxies; no synthetic value is presented as measured.",
    }
    write_json(TABLES / "parameter_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

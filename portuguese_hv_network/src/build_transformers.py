#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pandas as pd

from common import PROJECT, RAW, TABLES, ensure_dirs, haversine_m, normalize_name, parse_mva, parse_osm_voltage_values, read_json, utc_now, write_json


def best_bus(
    buses: pd.DataFrame, lon: float, lat: float, voltage: int, maximum_m: float,
    preferred_source: str | None = None,
) -> tuple[str | None, float]:
    candidates = buses[buses["voltage_kv"] == voltage]
    if candidates.empty:
        return None, float("inf")
    distances = candidates.apply(lambda row: haversine_m((lon, lat), (float(row["lon"]), float(row["lat"]))), axis=1)
    if preferred_source is not None:
        preferred = candidates[candidates["source"] == preferred_source]
        if not preferred.empty:
            preferred_distances = distances.loc[preferred.index]
            preferred_index = preferred_distances.idxmin()
            preferred_distance = float(preferred_distances.loc[preferred_index])
            if preferred_distance <= maximum_m:
                return str(candidates.loc[preferred_index, "bus_id"]), preferred_distance
    index = distances.idxmin()
    distance = float(distances.loc[index])
    return (str(candidates.loc[index, "bus_id"]), distance) if distance <= maximum_m else (None, distance)


def add_candidate(
    rows: list[dict[str, Any]], seen: set[tuple[str, str]], hv_bus: str, lv_bus: str,
    hv_kv: int, lv_kv: int, config: dict[str, Any], status: str, source_id: str,
    evidence: str, match_distance_m: float = 0.0, sn_mva: float | None = None,
    allow_parallel: bool = False,
) -> None:
    if hv_kv < lv_kv:
        hv_bus, lv_bus, hv_kv, lv_kv = lv_bus, hv_bus, lv_kv, hv_kv
    pair = tuple(sorted((hv_bus, lv_bus)))
    if (pair in seen and not allow_parallel) or hv_bus == lv_bus:
        return
    defaults = config["transformer_defaults"].get(f"{hv_kv}/{lv_kv}")
    if defaults is None:
        return
    if allow_parallel and any(row["source_id"] == source_id for row in rows):
        return
    seen.add(pair)
    parameters = dict(defaults)
    if sn_mva is not None and sn_mva > 0:
        parameters["sn_mva"] = sn_mva
    rows.append(
        {
            "transformer_id": f"TRAFO:{len(rows):05d}", "hv_bus": hv_bus, "lv_bus": lv_bus,
            "hv_kv": hv_kv, "lv_kv": lv_kv, **parameters, "pfe_kw": 0.0, "i0_percent": 0.0,
            "shift_degree": 0.0, "tap_side": "hv", "tap_neutral": 0, "tap_min": -8, "tap_max": 8,
            "tap_step_percent": 1.25, "tap_pos": 0, "source_status": status, "source_id": source_id,
            "evidence": evidence, "match_distance_m": match_distance_m,
            "parameter_status": "OSM_NAMEPLATE_MVA_WITH_PROXY_IMPEDANCE" if sn_mva is not None else "VOLTAGE_PAIR_PROXY",
        }
    )


def osm_transformers(buses: pd.DataFrame, config: dict[str, Any]) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    allowed = set(config["voltage_levels_kv"])
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    elements = read_json(RAW / "osm" / "transformer_elements.json")
    for element in elements:
        tags = element.get("tags") or {}
        primary = parse_osm_voltage_values(tags.get("voltage:primary"), allowed)
        secondary = parse_osm_voltage_values(tags.get("voltage:secondary"), allowed)
        if primary and secondary:
            voltages = sorted(set(primary + secondary), reverse=True)
        else:
            voltages = sorted(parse_osm_voltage_values(tags.get("voltage"), allowed), reverse=True)
        if len(voltages) < 2:
            continue
        rating_mva = parse_mva(tags.get("rating"))
        for hv_kv, lv_kv in zip(voltages, voltages[1:]):
            hv_bus, hv_d = best_bus(
                buses, element["lon"], element["lat"], hv_kv,
                float(config["substation_bus_match_m"]), preferred_source="OpenStreetMap",
            )
            lv_bus, lv_d = best_bus(
                buses, element["lon"], element["lat"], lv_kv,
                float(config["substation_bus_match_m"]),
                preferred_source="E-REDES" if lv_kv in {60, 130} else "OpenStreetMap",
            )
            if hv_bus and lv_bus:
                add_candidate(
                    rows, seen, hv_bus, lv_bus, hv_kv, lv_kv, config,
                    "DIRECT_OSM_TRANSFORMER_TAG", element["source_id"],
                    json.dumps(tags, ensure_ascii=False), max(hv_d, lv_d),
                    sn_mva=rating_mva, allow_parallel=True,
                )
    return rows, seen


def add_colocated_substations(
    rows: list[dict[str, Any]], seen: set[tuple[str, str]], buses: pd.DataFrame, config: dict[str, Any]
) -> None:
    osm = buses[(buses["source"] == "OpenStreetMap") & buses["facility_id"].astype(str).str.startswith("OSM:")]
    for facility_id, group in osm.groupby("facility_id"):
        levels = sorted(group["voltage_kv"].astype(int).unique(), reverse=True)
        for hv_kv, lv_kv in zip(levels, levels[1:]):
            hv_bus = str(group[group["voltage_kv"] == hv_kv].iloc[0]["bus_id"])
            lv_bus = str(group[group["voltage_kv"] == lv_kv].iloc[0]["bus_id"])
            add_candidate(
                rows, seen, hv_bus, lv_bus, hv_kv, lv_kv, config,
                "OSM_SUBSTATION_COLOCATION_INFERRED", str(facility_id),
                "Multiple configured voltage levels are co-located in the same OSM substation feature.",
            )


def rari_boundary_ledger(buses: pd.DataFrame, rows: list[dict[str, Any]], seen: set[tuple[str, str]], config: dict[str, Any]) -> pd.DataFrame:
    rari = pd.read_csv(RAW / "eredes" / "capacidade-rececao-rnd.csv", sep=";", low_memory=False)
    boundary_field = "ligacao_rnt_barramento_60kv_ultimo_trimestre"
    if boundary_field not in rari.columns:
        boundary_field = "ligacao_rnt_barramento_60kv_rari"
    osm_hv = buses[(buses["source"] == "OpenStreetMap") & (buses["voltage_kv"] > 60)].copy()
    osm_hv["normalized_name"] = osm_hv["facility_name"].map(normalize_name)
    eredes_60 = buses[(buses["source"] == "E-REDES") & (buses["voltage_kv"] == 60)].copy()
    all_60 = buses[buses["voltage_kv"] == 60].copy()
    ledger: list[dict[str, Any]] = []
    for boundary_name, group in rari.dropna(subset=[boundary_field]).groupby(boundary_field):
        normalized = normalize_name(boundary_name)
        name_hits = osm_hv[osm_hv["normalized_name"].map(lambda value: bool(normalized) and (normalized in value or value in normalized))]
        facility_codes = set(group["codigo"].dropna().astype(str)) if "codigo" in group else set()
        low = eredes_60[eredes_60["facility_code"].astype(str).isin(facility_codes)]
        status = "UNRESOLVED"
        created = 0
        minimum_distance = float("inf")
        if not name_hits.empty and not low.empty:
            pairs: list[tuple[float, pd.Series, pd.Series]] = []
            for _, high_row in name_hits.iterrows():
                for _, low_row in low.iterrows():
                    distance = haversine_m((float(high_row["lon"]), float(high_row["lat"])), (float(low_row["lon"]), float(low_row["lat"])))
                    pairs.append((distance, high_row, low_row))
            distance, high_row, low_row = min(pairs, key=lambda item: item[0])
            minimum_distance = distance
            if distance <= 5000:
                before = len(rows)
                add_candidate(
                    rows, seen, str(high_row["bus_id"]), str(low_row["bus_id"]), int(high_row["voltage_kv"]), 60,
                    config, "RARI_BOUNDARY_NAME_AND_SPATIAL_INFERRED", f"RARI:{boundary_name}",
                    "E-REDES RARI boundary name matches an OSM high-voltage substation; the closest associated E-REDES 60 kV facility is used.", distance,
                )
                created = len(rows) - before
                status = "CANDIDATE_CREATED" if created else "DUPLICATE_OR_UNSUPPORTED_VOLTAGE_PAIR"
            else:
                status = "NAME_MATCH_TOO_DISTANT"
        # RARI identifies the RNT boundary by name, while the listed facility
        # codes can be downstream 60 kV substations several kilometres away.
        # If that association is too distant, connect the named OSM RNT site to
        # a co-located 60 kV line endpoint instead. This is deliberately capped
        # at 1 km and remains recorded as an inferred boundary transformer.
        if name_hits.shape[0] and status in {"NAME_MATCH_TOO_DISTANT", "UNRESOLVED"}:
            fallback_pairs: list[tuple[float, pd.Series, pd.Series]] = []
            for _, high_row in name_hits.iterrows():
                for _, low_row in all_60.iterrows():
                    distance = haversine_m(
                        (float(high_row["lon"]), float(high_row["lat"])),
                        (float(low_row["lon"]), float(low_row["lat"])),
                    )
                    if distance <= 1000.0:
                        fallback_pairs.append((distance, high_row, low_row))
            if fallback_pairs:
                distance, high_row, low_row = min(fallback_pairs, key=lambda item: item[0])
                before = len(rows)
                add_candidate(
                    rows, seen, str(high_row["bus_id"]), str(low_row["bus_id"]), int(high_row["voltage_kv"]), 60,
                    config, "RARI_BOUNDARY_NAME_AND_COLOCATED_60KV_ENDPOINT_INFERRED", f"RARI:{boundary_name}",
                    "E-REDES RARI names the RNT boundary; the closest 60 kV topology endpoint co-located with the matched OSM high-voltage substation is used.",
                    distance,
                )
                created = len(rows) - before
                minimum_distance = distance
                status = "CANDIDATE_CREATED_AT_COLOCATED_60KV_ENDPOINT" if created else "DUPLICATE_OR_UNSUPPORTED_VOLTAGE_PAIR"
        ledger.append(
            {
                "boundary_name": boundary_name, "normalized_name": normalized, "rari_facility_count": len(facility_codes),
                "osm_name_match_count": len(name_hits), "candidate_created": created, "minimum_distance_m": minimum_distance if minimum_distance < float("inf") else "",
                "status": status,
            }
        )
    return pd.DataFrame(ledger)


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    buses = pd.read_csv(TABLES / "buses.csv")
    rows, seen = osm_transformers(buses, config)
    add_colocated_substations(rows, seen, buses, config)
    ledger = rari_boundary_ledger(buses, rows, seen, config)
    capacity_ledger: list[dict[str, Any]] = []
    for pair, target_mva in config.get("ren_2024_transformer_capacity_mva", {}).items():
        hv_kv, lv_kv = map(int, pair.split("/"))
        indices = [index for index, row in enumerate(rows) if int(row["hv_kv"]) == hv_kv and int(row["lv_kv"]) == lv_kv]
        before = sum(float(rows[index]["sn_mva"]) for index in indices)
        factor = float(target_mva) / before if before > 0 else 1.0
        for index in indices:
            rows[index]["pre_calibration_sn_mva"] = rows[index]["sn_mva"]
            effective_factor = max(1.0, factor)
            rows[index]["sn_mva"] = float(rows[index]["sn_mva"]) * effective_factor
            rows[index]["capacity_calibration_factor"] = effective_factor
            rows[index]["parameter_status"] = f'{rows[index]["parameter_status"]}+REN_VOLTAGE_PAIR_AGGREGATE_CALIBRATION'
        capacity_ledger.append({
            "voltage_pair": pair, "transformer_rows": len(indices), "pre_calibration_mva": before,
            "ren_target_mva": float(target_mva), "calibration_factor": factor,
            "post_calibration_mva": sum(float(rows[index]["sn_mva"]) for index in indices),
            "source": "REN RNT Quality of Service Report 2024, Table I",
        })
    pd.DataFrame(rows).to_csv(TABLES / "transformers_topology.csv", index=False)
    ledger.to_csv(TABLES / "rari_boundary_matching.csv", index=False)
    pd.DataFrame(capacity_ledger).to_csv(TABLES / "transformer_capacity_calibration.csv", index=False)
    summary = {
        "generated_at": utc_now(), "transformers": len(rows),
        "status_counts": pd.Series([row["source_status"] for row in rows]).value_counts().to_dict(),
        "rari_boundary_status_counts": ledger["status"].value_counts().to_dict(),
    }
    write_json(TABLES / "transformer_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

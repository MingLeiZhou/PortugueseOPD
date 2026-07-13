import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, get_logger, utc_now, write_json, write_text

try:
    import networkx as nx
except ImportError:  # pragma: no cover - fallback is used when networkx is unavailable.
    nx = None


BASE_MVA = 100.0
MISSING_LOOKUP = "MISSING_NEEDS_LOOKUP_SOURCE"
ESTIMATION_NOT_RUN = "NOT_ESTIMATED_NO_TRUSTWORTHY_LOOKUP"


def normalize_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", ".")
    if text in {"", "-", "nan", "NaN", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_voltage_kv(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = normalize_text(value).replace("kv", "")
    nums = re.findall(r"\d+(?:[.,]\d+)?", text)
    if not nums:
        return None
    return safe_float(nums[0])


def parse_voltage_pair(value: Any) -> tuple[float | None, float | None, list[float]]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None, None, []
    nums = [safe_float(item) for item in re.findall(r"\d+(?:[.,]\d+)?", str(value))]
    vals = [v for v in nums if v is not None]
    if not vals:
        return None, None, []
    return max(vals), min(vals), vals


def split_ids(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    ids = []
    for item in str(value).split(","):
        item = item.strip()
        if item:
            ids.append(item)
    return ids


def read_semicolon_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", dtype=str, engine="python")


def load_required_inputs(logger) -> dict[str, pd.DataFrame | dict[str, Any]]:
    paths = {
        "branches": config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv",
        "circuits": config.PROCESSED_DIR / "at_circuit_classification.csv",
        "summary": config.PROCESSED_DIR / "at_paper_logic_summary.json",
        "raw_lines": config.RAW_DIR / "rede-at-teste.csv",
        "characteristics": config.RAW_DIR / "caracteristicas-da-rede.csv",
        "load": config.RAW_DIR / "carga-na-subestacao.csv",
        "capacity": config.RAW_DIR / "capacidade-rececao-rnd.csv",
        "se_at": config.RAW_DIR / "se-at_2025.csv",
        "se_mt": config.RAW_DIR / "se-mt_2025.csv",
    }
    for key, path in paths.items():
        logger.info("Input %s: %s exists=%s", key, path, path.exists())

    branches = pd.read_csv(paths["branches"], dtype=str)
    for col in ["total_length_km", "number_of_original_segments", "confidence_score"]:
        if col in branches.columns:
            branches[col] = pd.to_numeric(branches[col], errors="coerce")

    circuits = pd.read_csv(paths["circuits"], dtype=str) if paths["circuits"].exists() else pd.DataFrame()
    raw_lines = read_semicolon_csv(paths["raw_lines"])
    characteristics = read_semicolon_csv(paths["characteristics"])
    load = read_semicolon_csv(paths["load"])
    capacity = read_semicolon_csv(paths["capacity"])
    se_at = read_semicolon_csv(paths["se_at"])
    se_mt = read_semicolon_csv(paths["se_mt"])
    summary = {}
    if paths["summary"].exists():
        with paths["summary"].open("r", encoding="utf-8") as handle:
            summary = json.load(handle)

    return {
        "branches": branches,
        "circuits": circuits,
        "raw_lines": raw_lines,
        "characteristics": characteristics,
        "load": load,
        "capacity": capacity,
        "se_at": se_at,
        "se_mt": se_mt,
        "summary": summary,
    }


def classify_asset_type(raw_tipo_values: list[str]) -> tuple[str, str, str]:
    normalized = [normalize_text(v) for v in raw_tipo_values if normalize_text(v)]
    if not normalized:
        return "unknown", "missing", "no tipo values found in source line ids"
    has_overhead = any("aereo" in v or "aerea" in v for v in normalized)
    has_cable = any("subterraneo" in v or "subterranea" in v or "cabo" in v for v in normalized)
    distinct = sorted(set(raw_tipo_values))
    if has_overhead and not has_cable:
        return "overhead", "direct_from_rnd_tipo", "; ".join(distinct)
    if has_cable and not has_overhead:
        return "cable", "direct_from_rnd_tipo", "; ".join(distinct)
    if has_overhead and has_cable:
        return "mixed", "direct_but_ambiguous_from_rnd_tipo", "; ".join(distinct)
    return "unknown", "unrecognized_rnd_tipo", "; ".join(distinct)


def raw_line_lookup(raw_lines: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if raw_lines.empty or "id" not in raw_lines.columns:
        return lookup
    for raw_id, group in raw_lines.groupby("id", dropna=False):
        if pd.isna(raw_id):
            continue
        lookup[str(raw_id)] = {
            "tipo_values": sorted(set(v for v in group.get("tipo", pd.Series(dtype=str)).dropna().astype(str))),
            "voltage_values": sorted(set(v for v in group.get("tensao_de", pd.Series(dtype=str)).dropna().astype(str))),
            "status_values": sorted(set(v for v in group.get("situacao", pd.Series(dtype=str)).dropna().astype(str))),
            "codigo_da_values": sorted(set(v for v in group.get("codigo_da", pd.Series(dtype=str)).dropna().astype(str))),
        }
    return lookup


def haversine_km(a: list[float], b: list[float]) -> float:
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))


def geometry_length_km(geometry_text: Any) -> float | None:
    if geometry_text is None or (isinstance(geometry_text, float) and math.isnan(geometry_text)):
        return None
    try:
        geom = json.loads(str(geometry_text))
    except json.JSONDecodeError:
        return None
    coords = geom.get("coordinates")
    if not coords:
        return None
    line_parts = []
    if geom.get("type") == "LineString":
        line_parts = [coords]
    elif geom.get("type") == "MultiLineString":
        line_parts = coords
    else:
        return None
    total = 0.0
    for part in line_parts:
        for idx in range(len(part) - 1):
            total += haversine_km(part[idx], part[idx + 1])
    return total


def build_line_parameter_inputs(branches: pd.DataFrame, raw_lines: pd.DataFrame) -> pd.DataFrame:
    lookup = raw_line_lookup(raw_lines)
    pair_counts = Counter()
    for _, row in branches.iterrows():
        pair = tuple(sorted([str(row.get("from_facility_code", "")), str(row.get("to_facility_code", ""))]))
        pair_counts[(pair, str(row.get("voltage", "")))] += 1

    records = []
    for _, row in branches.iterrows():
        source_ids = split_ids(row.get("source_line_ids"))
        unique_source_ids = sorted(set(source_ids))
        tipo_values = []
        source_voltage_values = []
        source_status_values = []
        for sid in unique_source_ids:
            item = lookup.get(sid, {})
            tipo_values.extend(item.get("tipo_values", []))
            source_voltage_values.extend(item.get("voltage_values", []))
            source_status_values.extend(item.get("status_values", []))
        asset_type, asset_source, asset_notes = classify_asset_type(tipo_values)
        geom_len = geometry_length_km(row.get("geometry"))
        stored_len = safe_float(row.get("total_length_km"))
        delta_pct = None
        if geom_len is not None and stored_len and stored_len > 0:
            delta_pct = abs(geom_len - stored_len) / stored_len * 100.0
        from_code = str(row.get("from_facility_code", ""))
        to_code = str(row.get("to_facility_code", ""))
        pair = tuple(sorted([from_code, to_code]))
        voltage = str(row.get("voltage", ""))
        records.append(
            {
                "branch_id": row.get("branch_id"),
                "from_bus": from_code,
                "to_bus": to_code,
                "from_facility_name": row.get("from_facility_name"),
                "to_facility_name": row.get("to_facility_name"),
                "from_facility_type": row.get("from_facility_type"),
                "to_facility_type": row.get("to_facility_type"),
                "voltage": voltage,
                "voltage_kv": parse_voltage_kv(voltage),
                "status": row.get("status"),
                "total_length_km": stored_len,
                "geometry_length_check_km": geom_len,
                "length_check_delta_pct": round(delta_pct, 6) if delta_pct is not None else None,
                "number_of_original_segments": row.get("number_of_original_segments"),
                "source_line_ids": row.get("source_line_ids"),
                "unique_source_line_id_count": len(unique_source_ids),
                "line_type_tipo_values": "; ".join(sorted(set(tipo_values))),
                "asset_type": asset_type,
                "asset_type_source": asset_source,
                "asset_type_notes": asset_notes,
                "source_voltage_values": "; ".join(sorted(set(source_voltage_values))),
                "source_status_values": "; ".join(sorted(set(source_status_values))),
                "topology_confidence_score": row.get("confidence_score"),
                "missing_voltage": pd.isna(row.get("voltage")) or str(row.get("voltage", "")).strip() == "",
                "missing_length": stored_len is None or pd.isna(stored_len),
                "abnormal_length": stored_len is None or stored_len <= 0 or stored_len > 100,
                "zero_length_geometry": geom_len is not None and geom_len <= 0,
                "geometry_length_mismatch_gt_5pct": delta_pct is not None and delta_pct > 5.0,
                "mixed_voltage": len(set(source_voltage_values)) > 1,
                "mixed_status": len(set(source_status_values)) > 1,
                "ambiguous_line_type": asset_type in {"mixed", "unknown"},
                "parallel_branch_count_between_facilities_voltage": pair_counts[(pair, voltage)],
                "is_parallel_branch": pair_counts[(pair, voltage)] > 1,
                "duplicate_branch_key": f"{pair[0]}__{pair[1]}__{voltage}__{row.get('status')}",
            }
        )
    df = pd.DataFrame(records)
    dup_counts = df["duplicate_branch_key"].value_counts()
    df["duplicate_branch_count_same_pair_voltage_status"] = df["duplicate_branch_key"].map(dup_counts).fillna(0).astype(int)
    df["is_duplicate_same_pair_voltage_status"] = df["duplicate_branch_count_same_pair_voltage_status"] > 1
    return df


def build_voltage_inventory(branch_inputs: pd.DataFrame) -> pd.DataFrame:
    records = []
    for voltage_kv, group in branch_inputs.groupby("voltage_kv", dropna=False):
        lengths = pd.to_numeric(group["total_length_km"], errors="coerce")
        type_dist = group["asset_type"].value_counts(dropna=False).to_dict()
        status_dist = group["status"].value_counts(dropna=False).to_dict()
        parallel_count = int(group["is_parallel_branch"].sum())
        records.append(
            {
                "voltage_kv": voltage_kv,
                "voltage_label": group["voltage"].dropna().astype(str).iloc[0] if len(group) else "",
                "branch_count": int(len(group)),
                "total_length_km": round(float(lengths.sum()), 6),
                "average_length_km": round(float(lengths.mean()), 6),
                "median_length_km": round(float(lengths.median()), 6),
                "max_length_km": round(float(lengths.max()), 6),
                "line_type_distribution": json.dumps(type_dist, ensure_ascii=False),
                "status_distribution": json.dumps(status_dist, ensure_ascii=False),
                "parallel_branch_records": parallel_count,
                "parallel_facility_voltage_groups": int(group.loc[group["is_parallel_branch"], "duplicate_branch_key"].nunique()),
                "missing_type_records": int(group["asset_type"].isin(["unknown", "mixed"]).sum()),
                "branches_requiring_estimated_parameters": int(len(group)),
            }
        )
    return pd.DataFrame(records).sort_values(["voltage_kv"], na_position="last")


def build_lut_template(branch_inputs: pd.DataFrame, circuits: pd.DataFrame) -> pd.DataFrame:
    voltage_classes = sorted(v for v in branch_inputs["voltage_kv"].dropna().unique())
    non_clean_voltage_classes = []
    if not circuits.empty and "voltage" in circuits.columns:
        for val in sorted(circuits["voltage"].dropna().astype(str).unique()):
            kv = parse_voltage_kv(val)
            if kv is not None and kv not in voltage_classes:
                non_clean_voltage_classes.append(kv)
    rows = []
    for voltage_kv in voltage_classes:
        for asset_type in ["overhead", "cable", "unknown"]:
            rows.append(
                {
                    "voltage_kv": voltage_kv,
                    "asset_type": asset_type,
                    "r_ohm_per_km": "",
                    "x_ohm_per_km": "",
                    "b_siemens_per_km": "",
                    "rated_current_a": "",
                    "thermal_limit_mva": "",
                    "source_type": "NEEDS_LOOKUP_SOURCE",
                    "source_reference": "",
                    "confidence": "low",
                    "observed_in_reliable_candidate_branches": True,
                    "observed_only_in_non_clean_circuits": False,
                    "notes": "No trustworthy Portugal/E-REDES-specific LUT value was found in the available local datasets; leave blank until sourced.",
                }
            )
    for voltage_kv in non_clean_voltage_classes:
        for asset_type in ["overhead", "cable", "unknown"]:
            rows.append(
                {
                    "voltage_kv": voltage_kv,
                    "asset_type": asset_type,
                    "r_ohm_per_km": "",
                    "x_ohm_per_km": "",
                    "b_siemens_per_km": "",
                    "rated_current_a": "",
                    "thermal_limit_mva": "",
                    "source_type": "NEEDS_LOOKUP_SOURCE",
                    "source_reference": "",
                    "confidence": "low",
                    "observed_in_reliable_candidate_branches": False,
                    "observed_only_in_non_clean_circuits": True,
                    "notes": "Voltage class observed outside the reliable inter-facility branch set; include only if later topology validation retains these circuits.",
                }
            )
    return pd.DataFrame(rows)


def build_parameter_estimates(branch_inputs: pd.DataFrame, lut_template: pd.DataFrame) -> pd.DataFrame:
    lut = {}
    for _, row in lut_template.iterrows():
        key = (safe_float(row["voltage_kv"]), row["asset_type"])
        values = {
            "r_ohm_per_km": safe_float(row.get("r_ohm_per_km")),
            "x_ohm_per_km": safe_float(row.get("x_ohm_per_km")),
            "b_siemens_per_km": safe_float(row.get("b_siemens_per_km")),
            "rated_current_a": safe_float(row.get("rated_current_a")),
            "thermal_limit_mva": safe_float(row.get("thermal_limit_mva")),
            "source_type": row.get("source_type"),
            "source_reference": row.get("source_reference"),
            "confidence": row.get("confidence"),
        }
        lut[key] = values

    records = []
    for _, row in branch_inputs.iterrows():
        voltage_kv = safe_float(row.get("voltage_kv"))
        asset_type = row.get("asset_type")
        lookup_asset = asset_type if asset_type in {"overhead", "cable"} else "unknown"
        lookup = lut.get((voltage_kv, lookup_asset), {})
        length = safe_float(row.get("total_length_km"))
        z_base = (voltage_kv**2 / BASE_MVA) if voltage_kv else None

        rpkm = lookup.get("r_ohm_per_km")
        xpkm = lookup.get("x_ohm_per_km")
        bpkm = lookup.get("b_siemens_per_km")
        rated_current_a = lookup.get("rated_current_a")

        can_estimate = all(v is not None for v in [voltage_kv, length, rpkm, xpkm, bpkm, rated_current_a])
        if can_estimate:
            rated_current_ka = rated_current_a / 1000.0
            r_total = rpkm * length
            x_total = xpkm * length
            b_total = bpkm * length
            thermal_mva = math.sqrt(3) * voltage_kv * rated_current_ka
            r_pu = r_total / z_base if z_base else None
            x_pu = x_total / z_base if z_base else None
            estimation_status = f"ESTIMATED_{str(lookup.get('confidence', 'low')).upper()}_CONFIDENCE"
        else:
            r_total = x_total = b_total = thermal_mva = r_pu = x_pu = None
            estimation_status = ESTIMATION_NOT_RUN

        records.append(
            {
                "branch_id": row.get("branch_id"),
                "from_bus": row.get("from_bus"),
                "to_bus": row.get("to_bus"),
                "voltage_kv": voltage_kv,
                "voltage_source": "direct_from_candidate_topology",
                "length_km": length,
                "length_source": "derived_from_merged_geometry",
                "asset_type": asset_type,
                "asset_type_source": row.get("asset_type_source"),
                "lookup_asset_type": lookup_asset,
                "base_mva": BASE_MVA,
                "z_base_ohm": z_base,
                "r_ohm_per_km": rpkm if rpkm is not None else MISSING_LOOKUP,
                "x_ohm_per_km": xpkm if xpkm is not None else MISSING_LOOKUP,
                "b_siemens_per_km": bpkm if bpkm is not None else MISSING_LOOKUP,
                "rated_current_a": rated_current_a if rated_current_a is not None else MISSING_LOOKUP,
                "thermal_limit_mva": thermal_mva if thermal_mva is not None else MISSING_LOOKUP,
                "r_total_ohm": r_total if r_total is not None else MISSING_LOOKUP,
                "x_total_ohm": x_total if x_total is not None else MISSING_LOOKUP,
                "b_total": b_total if b_total is not None else MISSING_LOOKUP,
                "r_pu": r_pu if r_pu is not None else MISSING_LOOKUP,
                "x_pu": x_pu if x_pu is not None else MISSING_LOOKUP,
                "b_pu": MISSING_LOOKUP,
                "lookup_source_type": lookup.get("source_type", "NEEDS_LOOKUP_SOURCE"),
                "lookup_source_reference": lookup.get("source_reference", ""),
                "parameter_confidence": "red_missing_lookup_source" if not can_estimate else lookup.get("confidence"),
                "estimation_status": estimation_status,
                "topology_confidence_score": row.get("topology_confidence_score"),
            }
        )
    return pd.DataFrame(records)


def aggregate_characteristics(characteristics: pd.DataFrame) -> pd.DataFrame:
    if characteristics.empty:
        return pd.DataFrame()
    df = characteristics.copy()
    code_col = "codigo_da_instalacao"
    if code_col not in df.columns:
        return pd.DataFrame()
    numeric_cols = [
        "potencia_instalada",
        "ponta",
        "potencia_de_curto_circuito_maxima_at",
        "potencia_de_curto_circuito_maxima_mt",
        "potencia_de_curto_circuito_minima_at",
        "potencia_de_curto_circuito_minima_mt",
        "corrente_de_curto_circuito_para_efeitos_de_dimensionamento_do_equipamento_at",
        "corrente_de_curto_circuito_para_efeitos_de_dimensionamento_do_equipamento_mt",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].map(safe_float)
    records = []
    for code, group in df.groupby(code_col, dropna=False):
        if pd.isna(code):
            continue
        ratio = first_non_null(group, "relacao_de_transformacao_at_mt")
        hv, lv, voltage_values = parse_voltage_pair(ratio)
        rec = {
            "substation_code": str(code),
            "source_name": first_non_null(group, "nome"),
            "source_district": first_non_null(group, "distrito"),
            "transformation_ratio": ratio,
            "high_voltage_kv": hv,
            "low_voltage_kv": lv,
            "voltage_values_kv": ";".join(str(v) for v in voltage_values),
            "neutral_regime_mt": first_non_null(group, "regime_de_neutro_mt"),
            "classification_zones": first_non_null(group, "classificacao_de_zonas"),
        }
        for col in numeric_cols:
            if col in group.columns:
                vals = pd.to_numeric(group[col], errors="coerce")
                rec[col] = vals.dropna().mean() if vals.notna().any() else None
        records.append(rec)
    return pd.DataFrame(records)


def first_non_null(df: pd.DataFrame, col: str) -> Any:
    if col not in df.columns:
        return None
    series = df[col].dropna()
    if series.empty:
        return None
    return series.astype(str).iloc[0]


def aggregate_load(load: pd.DataFrame) -> pd.DataFrame:
    if load.empty or "codigo_da_instalacao" not in load.columns:
        return pd.DataFrame()
    df = load.copy()
    for col in [
        "carga_natural",
        "potencia_instalada",
        "potencia_garantida",
        "potencia_nao_garantida",
        "disponibilidade",
        "carga_nao_garantida",
        "potencia_instalada_nao_garantida",
    ]:
        if col in df.columns:
            df[col] = df[col].map(safe_float)
    records = []
    for code, group in df.groupby("codigo_da_instalacao", dropna=False):
        if pd.isna(code):
            continue
        rec = {
            "substation_code": str(code),
            "load_name": first_non_null(group, "nome"),
            "load_voltage_ratio": first_non_null(group, "tensao"),
        }
        for col in [
            "carga_natural",
            "potencia_instalada",
            "potencia_garantida",
            "potencia_nao_garantida",
            "disponibilidade",
            "carga_nao_garantida",
            "potencia_instalada_nao_garantida",
        ]:
            if col in group.columns:
                vals = pd.to_numeric(group[col], errors="coerce")
                rec[f"{col}_min"] = vals.min()
                rec[f"{col}_max"] = vals.max()
                rec[f"{col}_mean"] = vals.mean()
        records.append(rec)
    return pd.DataFrame(records)


def aggregate_capacity(capacity: pd.DataFrame) -> pd.DataFrame:
    if capacity.empty or "codigo" not in capacity.columns:
        return pd.DataFrame()
    df = capacity.copy()
    candidate_cols = [c for c in df.columns if "mva" in c]
    for col in candidate_cols:
        df[col] = df[col].map(safe_float)
    records = []
    for code, group in df.groupby("codigo", dropna=False):
        if pd.isna(code):
            continue
        rec = {
            "substation_code": str(code),
            "capacity_name": first_non_null(group, "instalacao"),
            "capacity_installation_type": first_non_null(group, "tipo_de_instalacao"),
            "capacity_district": first_non_null(group, "distrito"),
            "capacity_municipality": first_non_null(group, "concelho"),
        }
        for col in candidate_cols:
            vals = pd.to_numeric(group[col], errors="coerce")
            rec[col] = vals.dropna().mean() if vals.notna().any() else None
        records.append(rec)
    return pd.DataFrame(records)


def graph_node_inventory(branch_inputs: pd.DataFrame) -> pd.DataFrame:
    graph_path = config.PROCESSED_DIR / "at_paper_logic_graph.graphml"
    if graph_path.exists() and nx is not None:
        try:
            graph = nx.read_graphml(graph_path)
            records = []
            for node_id, attrs in graph.nodes(data=True):
                records.append(
                    {
                        "substation_code": attrs.get("facility_code", str(node_id).split(":", 1)[-1]),
                        "facility_name": attrs.get("facility_name", ""),
                        "facility_type": attrs.get("facility_type", ""),
                    }
                )
            if records:
                return pd.DataFrame(records).drop_duplicates("substation_code")
        except Exception:
            pass

    records = []
    for side in ["from", "to"]:
        for _, row in branch_inputs.iterrows():
            records.append(
                {
                    "substation_code": row[f"{side}_bus"],
                    "facility_name": row[f"{side}_facility_name"],
                    "facility_type": row[f"{side}_facility_type"],
                }
            )
    df = pd.DataFrame(records).drop_duplicates("substation_code")
    return df


def build_transformer_availability(
    branch_inputs: pd.DataFrame,
    characteristics: pd.DataFrame,
    load: pd.DataFrame,
    capacity: pd.DataFrame,
) -> pd.DataFrame:
    nodes = graph_node_inventory(branch_inputs)
    char_agg = aggregate_characteristics(characteristics)
    load_agg = aggregate_load(load)
    cap_agg = aggregate_capacity(capacity)
    out = nodes.merge(char_agg, on="substation_code", how="left")
    out = out.merge(load_agg, on="substation_code", how="left")
    out = out.merge(cap_agg, on="substation_code", how="left")
    out["transformation_ratio_availability"] = out["transformation_ratio"].notna().map(
        {True: "directly_available", False: "missing_for_candidate_node"}
    )
    out["transformer_capacity_availability"] = out["potencia_instalada"].notna().map(
        {True: "directly_available_from_caracteristicas", False: "missing_or_pc_node"}
    )
    out["transformer_count_availability"] = "missing"
    out["transformer_impedance_availability"] = "lookup_estimable_only_after_Portuguese_source"
    out["tap_settings_availability"] = "missing_requires_E_REDES_REN_confirmation"
    out["control_mode_availability"] = "missing"
    out["neutral_regime_availability"] = out["neutral_regime_mt"].notna().map(
        {True: "directly_available_for_MT_side", False: "missing"}
    )
    return out


def build_short_circuit_inputs(branch_inputs: pd.DataFrame, characteristics: pd.DataFrame) -> pd.DataFrame:
    nodes = graph_node_inventory(branch_inputs)
    node_codes = set(nodes["substation_code"].astype(str))
    branch_counts = Counter()
    for _, row in branch_inputs.iterrows():
        branch_counts[str(row["from_bus"])] += 1
        branch_counts[str(row["to_bus"])] += 1

    char_agg = aggregate_characteristics(characteristics)
    records = []
    for _, row in char_agg.iterrows():
        hv = safe_float(row.get("high_voltage_kv"))
        smax = safe_float(row.get("potencia_de_curto_circuito_maxima_at"))
        smin = safe_float(row.get("potencia_de_curto_circuito_minima_at"))
        zmax = (hv**2 / smax) if hv and smax else None
        zmin = (hv**2 / smin) if hv and smin else None
        code = str(row.get("substation_code"))
        records.append(
            {
                "substation_code": code,
                "substation_name": row.get("source_name"),
                "matched_to_candidate_graph_node": code in node_codes,
                "candidate_graph_branch_count": branch_counts.get(code, 0),
                "transformation_ratio": row.get("transformation_ratio"),
                "high_voltage_kv": hv,
                "short_circuit_power_max_at_mva": smax,
                "short_circuit_power_min_at_mva": smin,
                "short_circuit_current_at_ka": row.get(
                    "corrente_de_curto_circuito_para_efeitos_de_dimensionamento_do_equipamento_at"
                ),
                "z_eq_from_max_sc_ohm": zmax,
                "z_eq_from_min_sc_ohm": zmin,
                "branch_parameter_estimate_available": False,
                "validation_use": "sanity_check_only_not_line_impedance_recovery",
            }
        )
    return pd.DataFrame(records)


def pct_missing(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 100.0
    missing = df[col].isna() | (df[col].astype(str).str.strip() == "")
    return round(float(missing.mean() * 100), 4)


def availability_row(
    object_type: str,
    parameter: str,
    required_pf: bool,
    required_opf: bool,
    available_directly: bool,
    derivable: bool,
    estimated_by_lookup: bool,
    source_dataset: str,
    source_field: str,
    missing_rate: float,
    confidence: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "parameter": parameter,
        "required_for_power_flow": required_pf,
        "required_for_opf": required_opf,
        "available_directly": available_directly,
        "derivable": derivable,
        "estimated_by_lookup_table": estimated_by_lookup,
        "source_dataset": source_dataset,
        "source_field": source_field,
        "missing_rate": missing_rate,
        "confidence": confidence,
        "notes": notes,
    }


def build_availability_matrix(
    branch_inputs: pd.DataFrame,
    transformer_availability: pd.DataFrame,
    short_circuit: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    branch_count = max(1, len(branch_inputs))
    asset_missing = round(float(branch_inputs["asset_type"].isin(["unknown", "mixed"]).mean() * 100), 4)
    rows.extend(
        [
            availability_row("branch", "from_bus", True, True, True, False, False, "at_interfacility_candidate_branches", "from_facility_code", pct_missing(branch_inputs, "from_bus"), "high", "Candidate topology branch terminal."),
            availability_row("branch", "to_bus", True, True, True, False, False, "at_interfacility_candidate_branches", "to_facility_code", pct_missing(branch_inputs, "to_bus"), "high", "Candidate topology branch terminal."),
            availability_row("branch", "voltage level", True, True, True, False, False, "at_interfacility_candidate_branches", "voltage", pct_missing(branch_inputs, "voltage"), "high", "All reliable candidate branches currently use 60 kV."),
            availability_row("branch", "line length", True, True, False, True, False, "at_interfacility_candidate_branches", "total_length_km; geometry", pct_missing(branch_inputs, "total_length_km"), "high", "Derived from merged circuit geometry; geometry length check is included."),
            availability_row("branch", "line type overhead/underground", False, True, True, False, False, "rede-at-teste", "tipo", asset_missing, "medium", "Direct source type exists but branches with mixed source segments remain ambiguous."),
            availability_row("branch", "number of circuits", True, True, False, False, False, "none", "", 100.0, "red", "No circuit-count or conductor-count field found in E-REDES AT line data."),
            availability_row("branch", "status / situacao", False, True, True, False, False, "at_interfacility_candidate_branches; rede-at-teste", "status; situacao", pct_missing(branch_inputs, "status"), "high", "Operational status is available; two candidate branches are disconnected/reserve."),
            availability_row("branch", "r_ohm_per_km", True, True, False, False, True, "none", "", 100.0, "red", "Requires Portugal-specific voltage and asset-type lookup source."),
            availability_row("branch", "x_ohm_per_km", True, True, False, False, True, "none", "", 100.0, "red", "Requires Portugal-specific voltage and asset-type lookup source."),
            availability_row("branch", "b_siemens_per_km", True, True, False, False, True, "none", "", 100.0, "red", "Requires Portugal-specific line/cable lookup source."),
            availability_row("branch", "rated_current", False, True, False, False, True, "none", "", 100.0, "red", "Requires conductor/cable/rating lookup source or owner rating data."),
            availability_row("branch", "thermal_limit_mva", False, True, False, True, True, "none", "", 100.0, "red", "Derivable from rated current and voltage, but rated current is missing."),
            availability_row("branch", "r_total_ohm", True, True, False, True, True, "derived", "r_ohm_per_km * length_km", 100.0, "red", "Derivable only after R lookup is sourced."),
            availability_row("branch", "x_total_ohm", True, True, False, True, True, "derived", "x_ohm_per_km * length_km", 100.0, "red", "Derivable only after X lookup is sourced."),
            availability_row("branch", "b_total", True, True, False, True, True, "derived", "b_siemens_per_km * length_km", 100.0, "red", "Derivable only after B lookup is sourced."),
            availability_row("branch", "r_pu", True, True, False, True, True, "derived", "r_total_ohm / z_base_ohm", 100.0, "red", "Per-unit conversion is reproducible on 100 MVA base after impedance lookup exists."),
            availability_row("branch", "x_pu", True, True, False, True, True, "derived", "x_total_ohm / z_base_ohm", 100.0, "red", "Per-unit conversion is reproducible on 100 MVA base after impedance lookup exists."),
            availability_row("branch", "b_pu", True, True, False, True, True, "derived", "admittance base conversion", 100.0, "red", "Requires confirmed B and conversion convention before use."),
            availability_row("branch", "confidence score", False, False, True, False, False, "at_interfacility_candidate_branches", "confidence_score", pct_missing(branch_inputs, "topology_confidence_score"), "medium", "Topology confidence only; not an electrical parameter confidence."),
        ]
    )

    node_count = max(1, len(transformer_availability))
    ratio_missing = round(float(transformer_availability["transformation_ratio"].isna().mean() * 100), 4) if not transformer_availability.empty else 100.0
    capacity_missing = round(float(transformer_availability["potencia_instalada"].isna().mean() * 100), 4) if not transformer_availability.empty and "potencia_instalada" in transformer_availability.columns else 100.0
    rows.extend(
        [
            availability_row("transformer", "substation code", True, True, True, False, False, "candidate graph; caracteristicas-da-rede", "codigo_da_instalacao", 0.0 if node_count else 100.0, "high", "Candidate graph nodes can be matched by installation code when present in RARI tables."),
            availability_row("transformer", "transformation ratio", True, True, True, False, False, "caracteristicas-da-rede; carga-na-subestacao", "relacao_de_transformacao_at_mt; tensao", ratio_missing, "medium", "Available for many SE nodes, but not for postos de corte and unmatched nodes."),
            availability_row("transformer", "high-voltage side", True, True, False, True, False, "caracteristicas-da-rede", "relacao_de_transformacao_at_mt", ratio_missing, "medium", "Parsed from transformation ratio."),
            availability_row("transformer", "low-voltage side", True, True, False, True, False, "caracteristicas-da-rede", "relacao_de_transformacao_at_mt", ratio_missing, "medium", "Parsed from transformation ratio."),
            availability_row("transformer", "installed power / capacity", True, True, True, False, False, "caracteristicas-da-rede; carga-na-subestacao", "potencia_instalada", capacity_missing, "medium", "Available for many substations; transformer count is still missing."),
            availability_row("transformer", "transformer count", True, True, False, False, False, "none", "", 100.0, "red", "No transformer-unit count found."),
            availability_row("transformer", "r_pu", True, True, False, False, True, "none", "", 100.0, "red", "Requires Portuguese transformer impedance lookup or owner data."),
            availability_row("transformer", "x_pu", True, True, False, False, True, "none", "", 100.0, "red", "Requires Portuguese transformer impedance lookup or owner data."),
            availability_row("transformer", "tap ratio / tap range", True, True, False, False, False, "none", "", 100.0, "red", "Not defensibly estimable from current public data."),
            availability_row("transformer", "control mode", False, True, False, False, False, "none", "", 100.0, "red", "Missing; needed for OPF-grade voltage control."),
            availability_row("transformer", "neutral regime", False, False, True, False, False, "caracteristicas-da-rede", "regime_de_neutro_mt", pct_missing(transformer_availability, "neutral_regime_mt"), "medium", "Available for MT side in characteristics dataset, not an AT line parameter."),
        ]
    )
    rows.extend(
        [
            availability_row("validation", "short-circuit power", False, False, True, False, False, "caracteristicas-da-rede", "potencia_de_curto_circuito_maxima_at; potencia_de_curto_circuito_minima_at", pct_missing(short_circuit, "short_circuit_power_max_at_mva"), "high", "Useful for node-level sanity checks, not direct line impedance recovery."),
            availability_row("validation", "short-circuit current", False, False, True, False, False, "caracteristicas-da-rede", "corrente_de_curto_circuito_para_efeitos_de_dimensionamento_do_equipamento_at", pct_missing(short_circuit, "short_circuit_current_at_ka"), "high", "Equipment-sizing current available."),
            availability_row("validation", "installed capacity", False, True, True, False, False, "caracteristicas-da-rede; carga-na-subestacao", "potencia_instalada", capacity_missing, "medium", "Can support transformer capacity and plausibility checks."),
            availability_row("validation", "natural load", False, True, True, False, False, "carga-na-subestacao", "carga_natural", 0.0, "high", "Substation-level seasonal load, not hourly in this table."),
            availability_row("validation", "guaranteed power", False, True, True, False, False, "carga-na-subestacao", "potencia_garantida", 0.0, "high", "Useful capacity-risk feature."),
            availability_row("validation", "reception capacity", False, True, True, False, False, "capacidade-rececao-rnd", "capacidade_de_recepcao_*", 0.0, "medium", "Fields are available but E-REDES describes them as approximate hosting-capacity values."),
            availability_row("validation", "substation location", False, False, True, False, False, "se-at_2025", "coordenadas", 0.0, "high", "Point coordinates available for AT substations."),
        ]
    )
    return pd.DataFrame(rows)


def build_source_audit() -> pd.DataFrame:
    local_pdf_candidates = [
        config.ROOT_DIR / "docs" / "2605.04289v1.pdf",
        Path("/mnt/data/2605.04289v1(1).pdf"),
    ]
    pdf_note = "; ".join(f"{p}: exists={p.exists()}" for p in local_pdf_candidates)
    records = [
        {
            "source_name": "Reference paper: Building Power Grid Models from Open Data",
            "url_or_local_path": "https://arxiv.org/abs/2605.04289",
            "parameter_type": "method; US LUT examples; topology/capacity factors; per-unit conversion",
            "voltage_level": "US transmission values, not Portugal-specific",
            "applicability_to_portugal": "method transferable; numeric tables not directly transferable",
            "directly_usable": False,
            "confidence": "high for method; low for Portuguese numeric reuse",
            "notes": pdf_note,
        },
        {
            "source_name": "E-REDES RND AT line dataset",
            "url_or_local_path": "data/raw/rede-at-teste.csv; https://e-redes.opendatasoft.com/pages/rnd/",
            "parameter_type": "geometry, voltage, status, overhead/subterranean type",
            "voltage_level": "60 kV and 130 kV observed in raw AT features",
            "applicability_to_portugal": "direct",
            "directly_usable": True,
            "confidence": "high",
            "notes": "No conductor type, circuit count, impedance, current rating, or thermal rating fields found.",
        },
        {
            "source_name": "E-REDES Caracteristicas da Rede",
            "url_or_local_path": "data/raw/caracteristicas-da-rede.csv; https://e-redes.opendatasoft.com/explore/dataset/caracteristicas-da-rede/information/",
            "parameter_type": "transformer ratio, installed power, short-circuit power/current, neutral regime",
            "voltage_level": "AT/MT substations",
            "applicability_to_portugal": "direct",
            "directly_usable": True,
            "confidence": "high",
            "notes": "Supports transformer and short-circuit feasibility, but not branch R/X/B or transformer impedance.",
        },
        {
            "source_name": "E-REDES Carga na Subestacao",
            "url_or_local_path": "data/raw/carga-na-subestacao.csv; https://e-redes.opendatasoft.com/explore/dataset/carga-na-subestacao/information/",
            "parameter_type": "natural load, installed power, guaranteed power, availability",
            "voltage_level": "AT/MT substations",
            "applicability_to_portugal": "direct",
            "directly_usable": True,
            "confidence": "high",
            "notes": "Useful validation and risk features; not line electrical parameters.",
        },
        {
            "source_name": "E-REDES Capacidade de Rececao RND",
            "url_or_local_path": "data/raw/capacidade-rececao-rnd.csv; https://e-redes.opendatasoft.com/explore/dataset/capacidade-rececao-rnd/information/",
            "parameter_type": "hosting/reception capacity, connection power, group capacity",
            "voltage_level": "AT and MT bus capacity fields",
            "applicability_to_portugal": "direct but approximate",
            "directly_usable": True,
            "confidence": "medium",
            "notes": "E-REDES page says values are approximations and do not replace formal E-REDES consultation.",
        },
        {
            "source_name": "Portuguese conductor/cable/transformer standard sources",
            "url_or_local_path": "not found in downloaded datasets",
            "parameter_type": "R/X/B/rated current/transformer impedance",
            "voltage_level": "60 kV; 130 kV if retained later",
            "applicability_to_portugal": "required",
            "directly_usable": False,
            "confidence": "red",
            "notes": "Need E-REDES/REN standards, public engineering standards, manufacturer catalogs, or literature before assigning numeric LUT rows.",
        },
    ]
    return pd.DataFrame(records)


def build_parameter_group_readiness() -> pd.DataFrame:
    rows = [
        ("line length", "GREEN", "Derived from merged circuit geometry and present for all candidate branches."),
        ("voltage level", "GREEN", "Directly available in candidate branches; all reliable branches are 60 kV."),
        ("asset type overhead/cable", "YELLOW", "Direct RND tipo exists, but mixed source segments create ambiguity for some merged branches."),
        ("line R/X/B", "RED", "No Portugal-specific lookup values or direct impedance fields found."),
        ("line thermal limit", "RED", "No rated current or thermal rating fields found; would require sourced LUT or owner data."),
        ("parallel circuit count", "RED", "No circuits/conductor-count field found."),
        ("transformer ratio", "YELLOW", "Available from caracteristicas-da-rede for many substations, missing for PC nodes and unmatched facilities."),
        ("transformer capacity", "YELLOW", "Installed power is available for many substations, but individual transformer unit counts are missing."),
        ("transformer impedance", "RED", "Requires Portuguese lookup table or owner data."),
        ("tap settings", "RED", "No tap position/range/control data found."),
        ("short-circuit validation", "YELLOW", "Short-circuit power/current is available and useful for sanity checks, not line-parameter recovery."),
        ("voltage setpoints", "RED", "No bus voltage setpoints found."),
        ("reactive control data", "RED", "No generator or transformer reactive controls in current AT topology data."),
        ("OPF cost data", "RED", "Not part of E-REDES branch/facility data; requires generation and market data."),
        ("load data", "YELLOW", "Substation seasonal load is available; hourly load requires the split diagram datasets and join validation."),
    ]
    return pd.DataFrame(rows, columns=["parameter_group", "readiness", "evidence"])


def build_reference_comparison() -> pd.DataFrame:
    rows = [
        ("voltage-class LUT for line R/X/B", "Build Portugal-specific LUT keyed by 60 kV and any retained 130 kV class", "Voltage and length available; numeric R/X/B source missing", "YELLOW/RED", "False precision if US LUT copied", "Obtain E-REDES/REN standard parameters or defensible Portuguese catalog/literature values."),
        ("cable vs overhead separate LUT", "Use RND tipo to split overhead vs subterranean", "tipo available from RND source segments", "YELLOW", "Merged circuits can mix overhead and cable segments", "Split mixed circuits or assign segment-weighted asset types before estimating."),
        ("line length from geometry", "Use merged circuit GeoJSON length", "Available for 358 branches", "GREEN", "Geometry is route length, not necessarily circuit length", "Keep geometry length checks and compare with aggregate official statistics."),
        ("thermal rating from LUT", "Derive MVA from voltage and rated current", "Rated current missing", "RED", "Thermal limits are operationally sensitive", "Request ratings or source Portuguese conductor/cable rating tables."),
        ("topology factor for parallel circuits", "Potential correction for missing circuits", "Parallel branches observable, circuit count missing", "YELLOW/RED", "Could mask topology errors", "Calibrate only against official aggregate circuit-km/capacity statistics."),
        ("capacity factor for line ratings", "Possible rating scalar after LUT exists", "No branch rating baseline", "RED", "Pure assumption without official calibration", "Wait for rating/circuit statistics or E-REDES confirmation."),
        ("transformer impedance LUT", "Need Portuguese HV/MV voltage-pair LUT", "Ratios and capacities partially available", "YELLOW/RED", "Tap and unit counts missing", "Request transformer impedance/tap/unit count or source standards."),
        ("demand allocation", "Use substation loads and hourly diagram datasets later", "Seasonal load table exists; hourly join not validated in Step 3A", "YELLOW", "Not equivalent to full dispatch/load allocation", "Perform Step 3B/4 join to hourly substation diagrams."),
        ("generator cost curves", "Not covered by E-REDES AT branch data", "No generator inventory/cost data in current inputs", "RED", "Cannot support OPF objective", "Use REN/DGEG/OMIE/ENTSO-E data in a later stage."),
        ("progressive relaxation", "Solver-side diagnostic method only", "No power-flow model built in Step 3A", "NOT APPLIED", "Would be misleading before parameters exist", "Do not run until topology, parameters, and loads are explicit."),
        ("validation against system statistics", "Use RARI aggregate lengths, capacities, SC data where available", "SC and capacity fields available; circuit-km benchmark missing", "YELLOW", "Short-circuit values are node equivalents, not line impedances", "Add official aggregate AT circuit length/rating/capacity references."),
    ]
    return pd.DataFrame(rows, columns=["reference_paper_method", "portuguese_e_redes_adaptation", "available_data", "feasibility", "risk", "required_next_action"])


def build_parallel_capacity_summary(branch_inputs: pd.DataFrame, summary: dict[str, Any]) -> dict[str, Any]:
    pair_counts = branch_inputs.groupby(["duplicate_branch_key"]).size()
    parallel_groups = pair_counts[pair_counts > 1]
    raw_lengths = summary.get("validation", {}).get("length_by_voltage", [])
    return {
        "topology_factor_needed": True,
        "capacity_factor_needed": True,
        "circuit_count_available": False,
        "parallel_branch_groups_same_pair_voltage_status": int(len(parallel_groups)),
        "parallel_branch_records_same_pair_voltage_status": int(parallel_groups.sum()) if len(parallel_groups) else 0,
        "max_parallel_records_same_pair_voltage_status": int(parallel_groups.max()) if len(parallel_groups) else 0,
        "available_calibration_proxies": [
            "total line length by voltage class from RND geometry",
            "parallel candidate branches between same facilities",
            "installed transformer capacity",
            "short-circuit strength",
            "reception capacity and load levels",
        ],
        "raw_rnd_length_by_voltage_from_step2a2": raw_lengths,
        "calibration_assessment": "Current proxies are useful for diagnostics but do not replace official circuit-km, conductor, or rating statistics. Factors would be assumptions until calibrated.",
        "false_precision_risk": "high",
    }


def make_parameter_coverage_map(branch_inputs: pd.DataFrame, estimates: pd.DataFrame) -> str | None:
    map_path = config.REPORTS_DIR / "maps" / "at_parameter_coverage_map.html"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv", dtype=str)
    merged = source.merge(estimates[["branch_id", "asset_type", "estimation_status"]], on="branch_id", how="left")
    features = []
    for _, row in merged.iterrows():
        try:
            geom = json.loads(row["geometry"])
        except Exception:
            continue
        asset = row.get("asset_type", "unknown")
        color = {
            "overhead": "#2b6cb0",
            "cable": "#dd6b20",
            "mixed": "#805ad5",
            "unknown": "#c53030",
        }.get(asset, "#718096")
        props = {
            "branch_id": row.get("branch_id"),
            "from": row.get("from_facility_code"),
            "to": row.get("to_facility_code"),
            "voltage": row.get("voltage"),
            "asset_type": asset,
            "estimation_status": row.get("estimation_status"),
            "length_km": row.get("total_length_km"),
            "color": color,
        }
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    feature_collection = {"type": "FeatureCollection", "features": features}
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AT parameter coverage map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .legend {{ background: white; padding: 8px; line-height: 1.4; font: 12px sans-serif; }}
  </style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const data = {json.dumps(feature_collection, ensure_ascii=False)};
const map = L.map('map').setView([39.6, -8.2], 7);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'}}).addTo(map);
function style(feature) {{
  return {{color: feature.properties.color, weight: 3, opacity: 0.85}};
}}
function popup(feature, layer) {{
  const p = feature.properties;
  layer.bindPopup(`<b>${{p.branch_id}}</b><br>${{p.from}} -> ${{p.to}}<br>${{p.voltage}}<br>asset=${{p.asset_type}}<br>${{p.estimation_status}}`);
}}
const layer = L.geoJSON(data, {{style, onEachFeature: popup}}).addTo(map);
if (layer.getBounds().isValid()) map.fitBounds(layer.getBounds(), {{padding: [20,20]}});
L.control({{position: 'bottomright'}}).onAdd = function() {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b>Asset type coverage</b><br><span style="color:#2b6cb0">overhead</span><br><span style="color:#dd6b20">cable</span><br><span style="color:#805ad5">mixed</span><br><span style="color:#c53030">unknown</span><br>All parameter estimates need LUT source.';
  return div;
}}.addTo(map);
</script>
</body>
</html>
"""
    write_text(map_path, html)
    return str(map_path)


def markdown_table(df: pd.DataFrame, columns: list[str] | None = None, max_rows: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.copy()
    if columns:
        view = view[columns]
    if max_rows is not None:
        view = view.head(max_rows)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = []
    for _, row in view.iterrows():
        rows.append("| " + " | ".join(str(row[col]) for col in view.columns) + " |")
    return "\n".join([header, sep, *rows])


def source_field_summary(df: pd.DataFrame, name: str) -> dict[str, Any]:
    return {
        "dataset": name,
        "rows": int(len(df)),
        "columns": list(df.columns),
    }


def write_report(
    availability: pd.DataFrame,
    branch_inputs: pd.DataFrame,
    voltage_inventory: pd.DataFrame,
    lut_template: pd.DataFrame,
    estimates: pd.DataFrame,
    transformer_availability: pd.DataFrame,
    short_circuit: pd.DataFrame,
    source_audit: pd.DataFrame,
    group_readiness: pd.DataFrame,
    reference_comparison: pd.DataFrame,
    parallel_capacity: dict[str, Any],
    summary: dict[str, Any],
    coverage_map: str | None,
) -> None:
    selected = summary.get("selected_strategy", {})
    graph_stats = summary.get("graph_statistics", {})
    clean_branches = len(branch_inputs)
    estimated_count = int((estimates["estimation_status"] != ESTIMATION_NOT_RUN).sum()) if not estimates.empty else 0
    sc_matched = int(short_circuit["matched_to_candidate_graph_node"].sum()) if not short_circuit.empty else 0
    char_sc_count = int(short_circuit["short_circuit_power_max_at_mva"].notna().sum()) if not short_circuit.empty else 0
    transformer_ratio_count = int(transformer_availability["transformation_ratio"].notna().sum()) if not transformer_availability.empty else 0
    transformer_capacity_count = int(transformer_availability["potencia_instalada"].notna().sum()) if not transformer_availability.empty and "potencia_instalada" in transformer_availability.columns else 0

    text = [
        "# 09 Electrical Parameter Feasibility",
        "",
        f"Generated: {utc_now()}",
        "",
        "Scope: Step 3A only. This validates whether the reference paper's electrical parameter estimation method can be adapted to the E-REDES Portuguese AT candidate topology. No power flow, OPF, ML/GNN training, final electrical parameter assignment, or topology overwrite was performed.",
        "",
        "Reference method: [Building Power Grid Models from Open Data: A Complete Pipeline from OpenStreetMap to Optimal Power Flow](https://arxiv.org/abs/2605.04289). The paper's Step 3 uses voltage-class lookup tables, line geometry length, separate overhead/cable assumptions, transformer voltage-pair lookup tables, topology/capacity factors, and later solver-side relaxation. This report tests transferability to Portugal; it does not copy the US numeric LUT values.",
        "",
        "## Input Topology",
        markdown_table(
            pd.DataFrame(
                [
                    {"metric": "reliable inter-facility candidate branches", "value": clean_branches},
                    {"metric": "Step 2A.2 merged circuits", "value": selected.get("merged_circuits")},
                    {"metric": "candidate graph nodes", "value": graph_stats.get("number_of_graph_nodes")},
                    {"metric": "candidate graph edges", "value": graph_stats.get("number_of_graph_edges")},
                    {"metric": "largest connected component", "value": graph_stats.get("largest_connected_component_size")},
                    {"metric": "all branches with prototype estimates", "value": estimated_count},
                ]
            )
        ),
        "",
        "## Branch Parameter Inputs",
        markdown_table(
            pd.DataFrame(
                [
                    {"metric": "missing voltage", "value": int(branch_inputs["missing_voltage"].sum())},
                    {"metric": "missing length", "value": int(branch_inputs["missing_length"].sum())},
                    {"metric": "abnormal length", "value": int(branch_inputs["abnormal_length"].sum())},
                    {"metric": "geometry length mismatch >5%", "value": int(branch_inputs["geometry_length_mismatch_gt_5pct"].sum())},
                    {"metric": "mixed voltage source segments", "value": int(branch_inputs["mixed_voltage"].sum())},
                    {"metric": "mixed status source segments", "value": int(branch_inputs["mixed_status"].sum())},
                    {"metric": "ambiguous asset type", "value": int(branch_inputs["ambiguous_line_type"].sum())},
                    {"metric": "parallel branch records", "value": int(branch_inputs["is_parallel_branch"].sum())},
                ]
            )
        ),
        "",
        "## Voltage Class Inventory",
        markdown_table(voltage_inventory),
        "",
        "## Availability Matrix",
        markdown_table(availability, max_rows=80),
        "",
        "## Portuguese LUT Feasibility",
        "No trustworthy Portugal/E-REDES-specific R/X/B/current-rating values were found in the downloaded datasets. The LUT file is therefore a template with blank numeric values and `NEEDS_LOOKUP_SOURCE` markers.",
        "",
        markdown_table(lut_template),
        "",
        "## Source Audit",
        markdown_table(source_audit),
        "",
        "## Prototype Parameter Estimation",
        f"Prototype estimation was attempted for {clean_branches} branches. Numeric R/X/B/current inputs were unavailable for every applicable LUT row, so {clean_branches - estimated_count} branches remain `MISSING_NEEDS_LOOKUP_SOURCE` and `NOT_ESTIMATED_NO_TRUSTWORTHY_LOOKUP`.",
        "",
        markdown_table(
            estimates[
                [
                    "branch_id",
                    "voltage_kv",
                    "length_km",
                    "asset_type",
                    "z_base_ohm",
                    "r_ohm_per_km",
                    "x_ohm_per_km",
                    "thermal_limit_mva",
                    "estimation_status",
                ]
            ],
            max_rows=12,
        ),
        "",
        "## Short-Circuit Validation Feasibility",
        markdown_table(
            pd.DataFrame(
                [
                    {"metric": "substations with short-circuit power AT", "value": char_sc_count},
                    {"metric": "short-circuit rows matched to candidate graph nodes", "value": sc_matched},
                    {"metric": "candidate graph nodes", "value": len(transformer_availability)},
                    {"metric": "branch estimates available for comparison", "value": estimated_count},
                ]
            )
        ),
        "",
        "Short-circuit data can support node-level plausibility checks and calibration once a branch LUT exists. It cannot identify individual branch impedance by itself.",
        "",
        "## Transformer Parameter Feasibility",
        markdown_table(
            pd.DataFrame(
                [
                    {"metric": "candidate graph facilities", "value": len(transformer_availability)},
                    {"metric": "facilities with transformation ratio", "value": transformer_ratio_count},
                    {"metric": "facilities with installed power in characteristics", "value": transformer_capacity_count},
                    {"metric": "transformer count available", "value": 0},
                    {"metric": "transformer impedance available", "value": 0},
                    {"metric": "tap settings available", "value": 0},
                ]
            )
        ),
        "",
        "Transformer objects are feasible as an inventory task for substations with AT/MT ratio and installed power. OPF-grade transformer parameters are not defensibly available yet because unit count, impedance, tap range, tap position, and control mode are missing.",
        "",
        "## Parallel Circuit And Capacity Factors",
        markdown_table(pd.DataFrame([parallel_capacity])),
        "",
        "Topology and capacity factors are likely needed because circuit counts and conductor counts are missing. Current data can provide weak proxies, but factors would be assumptions unless calibrated with official circuit-km/rating statistics or E-REDES/REN confirmation.",
        "",
        "## Readiness By Parameter Group",
        markdown_table(group_readiness),
        "",
        "## Comparison With Reference Paper Method",
        markdown_table(reference_comparison),
        "",
        "## Final Answers",
        "",
        "1. Can the reference paper method be applied? Partially. The workflow transfers, but Portugal-specific LUT values and calibration data are missing.",
        "2. Direct branch parameters: from/to bus, voltage, status, topology confidence, and source line type are available.",
        "3. Derived branch parameters: line length, voltage base, z_base on 100 MVA, and later total/per-unit values after LUTs are sourced.",
        "4. Parameters requiring LUTs: line R/X/B, rated current, thermal limit, transformer R/X, and possibly capacity/topology scaling.",
        "5. Needed Portuguese LUTs: 60 kV overhead, 60 kV underground cable, unknown/mixed 60 kV handling, and 130 kV rows if non-clean circuits are later retained; transformer LUTs for observed AT/MT voltage pairs.",
        "6. Enough data to estimate R/X/B? No. Length and voltage are enough to apply a LUT, but the LUT values are not sourced.",
        "7. Enough data to estimate thermal limits? No. Rated current/conductor/cable/rating data are missing.",
        "8. Can short-circuit data validate/calibrate? Yes for sanity checks and calibration feasibility, not for direct line impedance recovery.",
        "9. Can transformer parameters be created? Transformer inventory can be created for many substations; impedance/taps/control cannot yet.",
        "10. Are parallel/capacity factors needed? Yes, because circuit counts and ratings are missing.",
        "11. Can factors be calibrated? Not robustly from current data; only weak proxies exist.",
        "12. Suitability: topological risk analysis = yes/yellow; approximate power-flow feasibility testing = yellow only after sourced LUTs; AC power flow = red; OPF = red; cascading failure simulation = red for electrical cascading, yellow only for topology stress experiments.",
        "13. Request from E-REDES/REN: conductor/cable type, circuit count, rated current/thermal rating, line impedance or standard parameters, transformer unit count/capacity/impedance/taps, voltage setpoints, reactive control data, official circuit-km/rating aggregates, and validated topology terminal IDs.",
        "14. Recommended Step 3B: source and document Portuguese AT line/transformer lookup tables, then run a non-solver parameterization dry run with sensitivity bands and map/manual validation.",
        "",
        "## Final Conclusion",
        "",
        "### GREEN",
        "- Branch voltage, length, status, endpoint bus IDs, topology confidence, and source line type are technically reliable enough for a parameter input table.",
        "- Short-circuit and installed-power fields exist for many AT/MT substations and can support validation once estimates exist.",
        "- Per-unit conversion on a 100 MVA base is straightforward after sourced impedance values exist.",
        "",
        "### YELLOW",
        "- The reference paper method is adaptable as a transparent estimation framework.",
        "- Asset-type handling is possible but needs mixed overhead/cable branch treatment.",
        "- Transformer inventory is possible, but electrical transformer modeling still needs lookup or owner data.",
        "- Topological risk analysis can proceed using non-electrical graph features; electrical risk remains provisional.",
        "",
        "### RED",
        "- No defensible branch R/X/B or thermal limits can be assigned from current data alone.",
        "- Parallel circuit count, transformer impedance, tap settings, voltage setpoints, reactive controls, and OPF cost data are missing.",
        "- The candidate model is not AC power-flow-ready, OPF-ready, or suitable for electrical cascading simulation.",
    ]
    if coverage_map:
        text.extend(["", "## Optional Map", "", f"- `{Path(coverage_map).relative_to(config.ROOT_DIR)}`"])
    write_text(config.REPORTS_DIR / "09_electrical_parameter_feasibility.md", "\n".join(text))


def main() -> None:
    ensure_directories()
    logger = get_logger("validate_electrical_parameter_feasibility")
    logger.info("Starting Step 3A electrical parameter feasibility validation at %s", utc_now())

    inputs = load_required_inputs(logger)
    branches = inputs["branches"]
    circuits = inputs["circuits"]
    raw_lines = inputs["raw_lines"]
    characteristics = inputs["characteristics"]
    load = inputs["load"]
    capacity = inputs["capacity"]
    summary = inputs["summary"]

    branch_inputs = build_line_parameter_inputs(branches, raw_lines)
    voltage_inventory = build_voltage_inventory(branch_inputs)
    lut_template = build_lut_template(branch_inputs, circuits)
    estimates = build_parameter_estimates(branch_inputs, lut_template)
    transformer_availability = build_transformer_availability(branch_inputs, characteristics, load, capacity)
    short_circuit = build_short_circuit_inputs(branch_inputs, characteristics)
    availability = build_availability_matrix(branch_inputs, transformer_availability, short_circuit)
    source_audit = build_source_audit()
    group_readiness = build_parameter_group_readiness()
    reference_comparison = build_reference_comparison()
    parallel_capacity = build_parallel_capacity_summary(branch_inputs, summary)
    coverage_map = make_parameter_coverage_map(branch_inputs, estimates)

    availability.to_csv(config.PROCESSED_DIR / "at_parameter_availability_matrix.csv", index=False)
    branch_inputs.to_csv(config.PROCESSED_DIR / "at_line_parameter_inputs.csv", index=False)
    voltage_inventory.to_csv(config.PROCESSED_DIR / "at_voltage_class_inventory.csv", index=False)
    lut_template.to_csv(config.PROCESSED_DIR / "at_candidate_lut_template.csv", index=False)
    estimates.to_csv(config.PROCESSED_DIR / "at_candidate_branch_parameter_estimates.csv", index=False)
    transformer_availability.to_csv(config.PROCESSED_DIR / "at_transformer_parameter_availability.csv", index=False)
    short_circuit.to_csv(config.PROCESSED_DIR / "at_short_circuit_validation_inputs.csv", index=False)

    parameter_estimated_count = int((estimates["estimation_status"] != ESTIMATION_NOT_RUN).sum())
    summary_json = {
        "generated_at": utc_now(),
        "scope": "Step 3A electrical parameter feasibility validation only",
        "reference_paper": {
            "title": "Building Power Grid Models from Open Data: A Complete Pipeline from OpenStreetMap to Optimal Power Flow",
            "url": "https://arxiv.org/abs/2605.04289",
            "method_elements_checked": [
                "voltage-class line LUT",
                "separate overhead/cable LUT",
                "geometry length",
                "transformer voltage-pair LUT",
                "topology and capacity factors",
                "short-circuit and aggregate-statistics validation feasibility",
            ],
        },
        "input_topology": {
            "candidate_branch_count": int(len(branch_inputs)),
            "merged_circuits_from_step2a2": summary.get("selected_strategy", {}).get("merged_circuits"),
            "candidate_graph_nodes_from_step2a2": summary.get("graph_statistics", {}).get("number_of_graph_nodes"),
            "candidate_graph_edges_from_step2a2": summary.get("graph_statistics", {}).get("number_of_graph_edges"),
        },
        "branch_parameter_input_quality": {
            "missing_voltage": int(branch_inputs["missing_voltage"].sum()),
            "missing_length": int(branch_inputs["missing_length"].sum()),
            "abnormal_length": int(branch_inputs["abnormal_length"].sum()),
            "mixed_voltage": int(branch_inputs["mixed_voltage"].sum()),
            "mixed_status": int(branch_inputs["mixed_status"].sum()),
            "ambiguous_asset_type": int(branch_inputs["ambiguous_line_type"].sum()),
            "parallel_branch_records": int(branch_inputs["is_parallel_branch"].sum()),
        },
        "parameter_estimation": {
            "branches_attempted": int(len(estimates)),
            "branches_with_numeric_estimates": parameter_estimated_count,
            "branches_missing_lookup_source": int(len(estimates) - parameter_estimated_count),
            "status": "no numeric R/X/B/current/thermal estimates assigned because trustworthy Portuguese LUT values were not found",
        },
        "short_circuit_validation": {
            "substations_with_short_circuit_power_at": int(short_circuit["short_circuit_power_max_at_mva"].notna().sum()) if not short_circuit.empty else 0,
            "substations_matched_to_candidate_graph_nodes": int(short_circuit["matched_to_candidate_graph_node"].sum()) if not short_circuit.empty else 0,
            "can_validate_branch_estimates_now": False,
            "reason": "short-circuit data is node-level equivalent strength and no branch estimates exist yet",
        },
        "transformer_feasibility": {
            "candidate_graph_facilities": int(len(transformer_availability)),
            "facilities_with_transformation_ratio": int(transformer_availability["transformation_ratio"].notna().sum()) if not transformer_availability.empty else 0,
            "facilities_with_installed_power": int(transformer_availability["potencia_instalada"].notna().sum()) if not transformer_availability.empty and "potencia_instalada" in transformer_availability.columns else 0,
            "transformer_impedance_available": False,
            "tap_settings_available": False,
        },
        "parallel_capacity_factor_feasibility": parallel_capacity,
        "source_audit": source_audit.to_dict("records"),
        "final_feasibility": {
            "topological_risk_analysis": "YELLOW/GREEN for non-electrical graph features",
            "approximate_power_flow_feasibility_testing": "YELLOW only after sourced LUTs",
            "ac_power_flow": "RED",
            "opf": "RED",
            "cascading_failure_simulation": "RED for electrical cascading; YELLOW only for topology stress experiments",
        },
        "outputs": {
            "availability_matrix": str(config.PROCESSED_DIR / "at_parameter_availability_matrix.csv"),
            "line_parameter_inputs": str(config.PROCESSED_DIR / "at_line_parameter_inputs.csv"),
            "voltage_class_inventory": str(config.PROCESSED_DIR / "at_voltage_class_inventory.csv"),
            "candidate_lut_template": str(config.PROCESSED_DIR / "at_candidate_lut_template.csv"),
            "candidate_branch_parameter_estimates": str(config.PROCESSED_DIR / "at_candidate_branch_parameter_estimates.csv"),
            "transformer_parameter_availability": str(config.PROCESSED_DIR / "at_transformer_parameter_availability.csv"),
            "short_circuit_validation_inputs": str(config.PROCESSED_DIR / "at_short_circuit_validation_inputs.csv"),
            "report": str(config.REPORTS_DIR / "09_electrical_parameter_feasibility.md"),
            "coverage_map": coverage_map,
        },
    }
    write_json(config.PROCESSED_DIR / "at_parameter_feasibility_summary.json", summary_json)
    write_report(
        availability,
        branch_inputs,
        voltage_inventory,
        lut_template,
        estimates,
        transformer_availability,
        short_circuit,
        source_audit,
        group_readiness,
        reference_comparison,
        parallel_capacity,
        summary,
        coverage_map,
    )
    logger.info("Completed Step 3A. Wrote report and %s branch estimate rows.", len(estimates))


if __name__ == "__main__":
    main()

from itertools import combinations

import pandas as pd

import config
from utils import (
    confidence,
    ensure_directories,
    extract_points,
    get_logger,
    haversine_meters,
    load_catalog,
    load_dataset_frame,
    markdown_table,
    name_matches,
    normalize_name,
    save_dataframe,
    utc_now,
    value_overlap,
    write_text,
)


SEMANTIC_GROUPS = {
    "substation_code": ("codigo_subestacao", "codigo_da_instalacao", "codigo", "code"),
    "substation_name": ("subestacao", "instalacao", "nome", "name"),
    "district": ("distrito", "district", "dis_name", "dis_code", "codigo_distrito"),
    "municipality": ("concelho", "municip", "con_name", "con_code", "codigo_concelho"),
    "parish": ("freguesia", "parish", "codigo_freguesia"),
    "voltage": ("tensao", "voltage", "kv"),
    "time": ("datahora", "data", "hora", "date", "time", "ano"),
}


def columns_for_group(df, group):
    return [column for column in df.columns if name_matches(column, SEMANTIC_GROUPS[group])]


def add_overlap(rows, dataset_a, dataset_b, col_a, col_b, df_a, df_b, method):
    overlap = value_overlap(df_a[col_a], df_b[col_b], normalize=method == "normalized_name")
    if overlap["overlap_count"] == 0:
        return
    rows.append(
        {
            "dataset_a": dataset_a,
            "dataset_b": dataset_b,
            "candidate_key": f"{col_a} <-> {col_b}",
            "join_method": method,
            "overlap_count": overlap["overlap_count"],
            "overlap_percentage": overlap["overlap_percentage"],
            "confidence_level": confidence(
                overlap["overlap_count"], overlap["overlap_percentage"], method
            ),
            "recommended_join_method": method,
            "left_unique": overlap["left_unique"],
            "right_unique": overlap["right_unique"],
        }
    )


def spatial_overlap(dataset_a, dataset_b, df_a, df_b):
    points_a = extract_points(df_a, limit=500)
    points_b = extract_points(df_b, limit=500)
    if not points_a or not points_b:
        return []
    thresholds = [100, 500, 1000]
    matches = {threshold: 0 for threshold in thresholds}
    for point in points_a:
        nearest = min(haversine_meters(point, other) for other in points_b)
        for threshold in thresholds:
            if nearest <= threshold:
                matches[threshold] += 1
    rows = []
    denom = len(points_a) or 1
    for threshold, count in matches.items():
        pct = round(100 * count / denom, 4)
        if count:
            rows.append(
                {
                    "dataset_a": dataset_a,
                    "dataset_b": dataset_b,
                    "candidate_key": f"coordinates <= {threshold}m",
                    "join_method": "spatial",
                    "overlap_count": count,
                    "overlap_percentage": pct,
                    "confidence_level": confidence(count, pct, "spatial"),
                    "recommended_join_method": f"nearest feature within {threshold}m",
                    "left_unique": len(points_a),
                    "right_unique": len(points_b),
                }
            )
    return rows


def analyze_pairs(catalog):
    frames = {}
    for dataset_id in catalog:
        df, source_path = load_dataset_frame(dataset_id)
        if len(df) > 5000:
            df = df.head(5000)
        frames[dataset_id] = (df, source_path)
    rows = []
    for dataset_a, dataset_b in combinations(sorted(catalog), 2):
        df_a, _ = frames[dataset_a]
        df_b, _ = frames[dataset_b]
        if df_a.empty or df_b.empty:
            continue
        common = set(df_a.columns) & set(df_b.columns)
        for column in sorted(common):
            if name_matches(
                column,
                (
                    "codigo",
                    "code",
                    "id",
                    "nome",
                    "name",
                    "distrito",
                    "concelho",
                    "freguesia",
                    "tensao",
                    "data",
                    "hora",
                ),
            ):
                add_overlap(rows, dataset_a, dataset_b, column, column, df_a, df_b, "exact_key")
        for group in SEMANTIC_GROUPS:
            for col_a in columns_for_group(df_a, group):
                for col_b in columns_for_group(df_b, group):
                    if col_a == col_b and col_a in common:
                        continue
                    add_overlap(rows, dataset_a, dataset_b, col_a, col_b, df_a, df_b, "normalized_name")
        rows.extend(spatial_overlap(dataset_a, dataset_b, df_a, df_b))
    return pd.DataFrame(rows)


def object_readiness(catalog, join_df):
    def has_dataset(dataset_id):
        return dataset_id in catalog

    def fields(dataset_id):
        return set(catalog.get(dataset_id, {}).get("field_names", []))

    readiness = []
    line_fields = fields("rede-at-teste") | fields("rede-mt-teste")
    sub_fields = fields("se-at_2025") | fields("se-mt_2025") | fields("carga-na-subestacao")
    load_fields = set().union(
        *[
            fields(dataset_id)
            for dataset_id in catalog
            if dataset_id.startswith("diagrama_carga_subestacao_")
        ]
    )
    readiness.append(
        {
            "Object": "substations",
            "Required for future model": "identifier, name, voltage, coordinates, admin geography",
            "Available?": "GREEN" if {"se-at_2025", "se-mt_2025"} & set(catalog) else "YELLOW",
            "Source dataset": "se-at_2025, se-mt_2025, carga-na-subestacao",
            "Key fields": ", ".join(sorted(sub_fields & {"codigo", "instalacao", "codigo_da_instalacao", "nome", "tensao", "coordenadas", "distrito", "concelho"})),
            "Missing fields": "short-circuit power/current not present in substation layers",
            "Confidence": "medium",
        }
    )
    readiness.append(
        {
            "Object": "AT lines",
            "Required for future model": "from node, to node, voltage, geometry, length, rating",
            "Available?": "YELLOW" if has_dataset("rede-at-teste") else "RED",
            "Source dataset": "rede-at-teste",
            "Key fields": ", ".join(sorted(line_fields & {"id", "codigo_da", "tensao_de", "tipo", "situacao", "geo_shape", "geo_point_2d", "dis_code", "con_code"})),
            "Missing fields": "explicit from/to nodes, electrical impedance, thermal/current rating",
            "Confidence": "medium",
        }
    )
    readiness.append(
        {
            "Object": "MT lines",
            "Required for future model": "from node, to node, voltage, geometry, length, rating",
            "Available?": "YELLOW" if has_dataset("rede-mt-teste") else "RED",
            "Source dataset": "rede-mt-teste",
            "Key fields": ", ".join(sorted(line_fields & {"id", "codigo_da", "tensao_de", "tipo", "situacao", "geo_shape", "geo_point_2d", "dis_code", "con_code"})),
            "Missing fields": "explicit from/to nodes, impedance, thermal/current rating",
            "Confidence": "medium",
        }
    )
    readiness.append(
        {
            "Object": "transformers",
            "Required for future model": "HV/LV, ratio, capacity, substation id, count",
            "Available?": "YELLOW" if has_dataset("caracteristicas-da-rede") else "RED",
            "Source dataset": "caracteristicas-da-rede, postos-transformacao-distribuicao",
            "Key fields": ", ".join(sorted(fields("caracteristicas-da-rede") | fields("postos-transformacao-distribuicao"))[:12]),
            "Missing fields": "per-transformer impedance and explicit winding mapping",
            "Confidence": "low",
        }
    )
    readiness.append(
        {
            "Object": "hourly substation load",
            "Required for future model": "substation id, timestamp, active energy/power, geography",
            "Available?": "GREEN" if load_fields else "RED",
            "Source dataset": "diagrama_carga_subestacao_*",
            "Key fields": ", ".join(sorted(load_fields & {"codigo_subestacao", "subestacao", "datahora", "energia", "distrito", "concelho", "freguesia"})),
            "Missing fields": "instantaneous power; hourly kWh can be converted to average kW/MW",
            "Confidence": "high",
        }
    )
    readiness.extend(
        [
            {
                "Object": "installed capacity",
                "Required for future model": "installed/guaranteed power",
                "Available?": "GREEN" if "potencia_instalada" in fields("carga-na-subestacao") else "RED",
                "Source dataset": "carga-na-subestacao, caracteristicas-da-rede",
                "Key fields": "potencia_instalada, potencia_garantida, codigo_da_instalacao",
                "Missing fields": "none for substation-level capacity; not per transformer winding",
                "Confidence": "high",
            },
            {
                "Object": "reception capacity",
                "Required for future model": "available reception power",
                "Available?": "GREEN" if has_dataset("capacidade-rececao-rnd") else "RED",
                "Source dataset": "capacidade-rececao-rnd",
                "Key fields": "capacidade_de_recepcao_*_mva_*, potencia_de_ligacao_*",
                "Missing fields": "requires scenario/period selection: RARI, last quarter, forecast",
                "Confidence": "high",
            },
            {
                "Object": "short-circuit power",
                "Required for future model": "short-circuit power/current",
                "Available?": "GREEN"
                if any("curto_circuito" in field for field in fields("caracteristicas-da-rede"))
                else "RED",
                "Source dataset": "caracteristicas-da-rede",
                "Key fields": "potencia_de_curto_circuito_maxima_at/mt, corrente_de_curto_circuito_*",
                "Missing fields": "not line-specific; must be joined to substations",
                "Confidence": "high",
            },
            {
                "Object": "voltage quality events",
                "Required for future model": "event counts and quality measurements",
                "Available?": "GREEN",
                "Source dataset": "qualidade_energia_sobretensoes-final, qualidade_energia_cavas-final, qualidade_energia_fenomenoscontinuos-final",
                "Key fields": "code/name/district/tension/startdate/enddate and event metric columns",
                "Missing fields": "needs temporal and station-name/code harmonization",
                "Confidence": "medium",
            },
            {
                "Object": "service continuity indicators",
                "Required for future model": "continuity metrics",
                "Available?": "GREEN" if has_dataset("12-continuidade-de-servico-indicadores-gerais-de-continuidade-de-servico") else "RED",
                "Source dataset": "12-continuidade-de-servico-indicadores-gerais-de-continuidade-de-servico",
                "Key fields": "saifi_*, saidi_*, maifi_*, tiepi_mt_min, end_mt_mwh, codigo_concelho",
                "Missing fields": "municipality/year granularity, not individual asset outage labels",
                "Confidence": "high",
            },
            {
                "Object": "geographic coordinates",
                "Required for future model": "coordinates/geometries",
                "Available?": "GREEN",
                "Source dataset": "RND restricted layers, substations, PTs, geography references",
                "Key fields": "geo_point_2d, geo_shape, coordenadas, coordenadas_geo",
                "Missing fields": "line endpoint-node identity is not explicit",
                "Confidence": "high",
            },
            {
                "Object": "temporal coverage",
                "Required for future model": "date/time fields",
                "Available?": "GREEN",
                "Source dataset": "load/quality/continuity datasets",
                "Key fields": "datahora, data, hora, year/ano, startdate/enddate",
                "Missing fields": "different datasets use different temporal granularity",
                "Confidence": "high",
            },
            {
                "Object": "join keys",
                "Required for future model": "substation/admin/voltage/time keys",
                "Available?": "YELLOW",
                "Source dataset": "join_key_matrix.csv",
                "Key fields": "codigo/codigo_da_instalacao/codigo_subestacao, names, district/municipality/parish, coordinates",
                "Missing fields": "line terminal-to-bus keys are not explicit",
                "Confidence": "medium",
            },
        ]
    )
    return readiness


def recommendations(readiness):
    red = [row["Object"] for row in readiness if row["Available?"] == "RED"]
    yellow = [row["Object"] for row in readiness if row["Available?"] == "YELLOW"]
    text = [
        "# 05 Next Step Recommendations",
        "",
        f"Generated: {utc_now()}",
        "",
        "## Step 1 conclusion",
        "",
        "A Portuguese E-REDES-based grid model is technically feasible at substation and geospatial-network-feature level, but not yet OPF-ready. The datasets expose substations, AT/MT geometries, load time series, capacity indicators, voltage-quality events, and continuity indicators. The main gap is electrical branch modelling detail: explicit from/to bus topology, impedances, transformer parameters, and thermal ratings are not directly provided.",
        "",
        "## Answers",
        "",
        "- Ready for use: metadata/API-valid small and medium datasets, RND restricted AT/MT/geographic layers with the public page key, capacity/load/quality/continuity datasets.",
        "- Require cleaning: substation names/codes across load, capacity, RARI, and RND layers; Portuguese administrative names; voltage strings; restricted RND geometry fields.",
        "- Weak join keys: line-to-substation connectivity and transformer-to-substation mapping where only spatial proximity or installation codes are available.",
        "- Substation-level graph: feasible after normalizing substation codes/names and using RND AT/MT geometry proximity.",
        "- Line-level bus-branch model: partially feasible for topology reconstruction, but not sufficient for full AC power flow without impedance, rating, and explicit terminal-node data.",
        "- Hourly load dataset: feasible from `diagrama_carga_subestacao_*`; `energia` is kWh per interval and can be converted to average kW/MW for hourly records.",
        "- Operational risk labels: feasible as proxy labels from voltage-quality events, continuity indicators, congestion/capacity fields, and load/capacity ratios, but labels need careful temporal/geographic alignment.",
        "- Missing for full AC power flow or OPF: bus voltage setpoints, line impedances/susceptance, thermal limits, transformer impedance/tap/ratio details, generator/control data, protection constraints, and explicit from/to bus connectivity.",
        "- Step 2 topology reconstruction should begin with RND restricted line/substation layers, snap line endpoints to substations/PTs, derive candidate edges spatially, then validate against substation/load/capacity joins before any power-flow assumptions.",
        "- Large full downloads skipped by default in this run: MT line geometry, LV supports, and the five hourly-load split datasets. Use `python src/download_datasets.py --force-large` only when disk/time budget is available.",
        "",
        "## Readiness blockers",
        "",
        f"RED objects: {', '.join(red) if red else 'none from API availability alone'}.",
        f"YELLOW objects needing cleaning or assumptions: {', '.join(yellow)}.",
    ]
    write_text(config.REPORTS_DIR / "05_next_step_recommendations.md", "\n".join(text))


def main():
    ensure_directories()
    logger = get_logger("analyze_join_keys")
    catalog = load_catalog()
    join_df = analyze_pairs(catalog)
    if not join_df.empty:
        join_df = join_df.drop_duplicates(
            subset=[
                "dataset_a",
                "dataset_b",
                "candidate_key",
                "join_method",
                "recommended_join_method",
            ]
        )
    save_dataframe(join_df, config.METADATA_DIR / "join_key_matrix.csv")
    top_rows = (
        join_df.sort_values(["confidence_level", "overlap_percentage"], ascending=[True, False])
        .head(80)
        .to_dict("records")
        if not join_df.empty
        else []
    )
    readiness = object_readiness(catalog, join_df)
    text = [
        "# 04 Join Key Analysis",
        "",
        f"Generated: {utc_now()}",
        "",
        "The full pairwise matrix is saved as `data/metadata/join_key_matrix.csv`. Exact column overlap, normalized Portuguese name/code overlap, spatial proximity, and temporal field compatibility are considered where local samples/raw files are available.",
        "",
        "## Highest-signal join candidates",
        markdown_table(
            top_rows[:40],
            [
                "dataset_a",
                "dataset_b",
                "candidate_key",
                "join_method",
                "overlap_count",
                "overlap_percentage",
                "confidence_level",
                "recommended_join_method",
            ],
        ),
        "",
        "## Readiness assessment",
        markdown_table(
            readiness,
            [
                "Object",
                "Required for future model",
                "Available?",
                "Source dataset",
                "Key fields",
                "Missing fields",
                "Confidence",
            ],
        ),
    ]
    write_text(config.REPORTS_DIR / "04_join_key_analysis.md", "\n".join(text))
    recommendations(readiness)
    logger.info("Join-key analysis complete with %s candidate joins", len(join_df))


if __name__ == "__main__":
    main()

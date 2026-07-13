import csv
import html
import json
import logging
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tqdm import tqdm

import config


def ensure_directories() -> None:
    for path in [
        config.RAW_DIR,
        config.PROCESSED_DIR,
        config.METADATA_DIR,
        config.SAMPLES_DIR,
        config.REPORTS_DIR,
        config.NOTEBOOKS_DIR,
        config.LOG_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_logger(name: str) -> logging.Logger:
    ensure_directories()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(config.LOG_DIR / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, default=str)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def strip_html(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    replacements = {
        "subestacao": "se",
        "subestacoes": "se",
        "posto transformacao": "pt",
        "postos transformacao": "pt",
        "s. ": "sao ",
        "sto ": "santo ",
        "sta ": "santa ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_matches(name: str, keywords: tuple[str, ...]) -> bool:
    normalized = normalize_name(name)
    return any(normalize_name(keyword) in normalized for keyword in keywords)


def request_url(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    api_key: str | None = None,
    method: str = "GET",
    stream: bool = False,
    timeout: int = config.REQUEST_TIMEOUT,
) -> requests.Response:
    merged = dict(params or {})
    if api_key:
        merged["apikey"] = api_key
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            response = requests.request(
                method,
                url,
                params=merged,
                timeout=timeout,
                stream=stream,
                headers={"User-Agent": "PortugueseOPD-data-prep/0.1"},
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(config.BACKOFF_SECONDS * (attempt + 1))
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(config.BACKOFF_SECONDS * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Request failed after retries: {url}")


def metadata_url(dataset_id: str) -> str:
    return f"{config.BASE_URL}/api/explore/v2.1/catalog/datasets/{dataset_id}"


def records_v2_url(dataset_id: str) -> str:
    return f"{config.BASE_URL}/api/explore/v2.1/catalog/datasets/{dataset_id}/records"


def records_v1_url() -> str:
    return f"{config.BASE_URL}/api/records/1.0/search/"


def export_url(dataset_id: str, fmt: str) -> str:
    return f"{config.BASE_URL}/api/explore/v2.1/catalog/datasets/{dataset_id}/exports/{fmt}"


def old_download_url(dataset_id: str, fmt: str) -> str:
    return f"{config.BASE_URL}/explore/dataset/{dataset_id}/download/"


def discover_page_datasets(logger: logging.Logger | None = None) -> dict[str, dict[str, Any]]:
    discovered: dict[str, dict[str, Any]] = {}
    for page_name, page_url in config.PAGE_URLS.items():
        if logger:
            logger.info("Discovering datasets from page %s: %s", page_name, page_url)
        response = request_url(page_url)
        page_text = html.unescape(response.text)
        context_to_key = dict(
            re.findall(r'([A-Za-z0-9_-]+)-apikey="([^"]+)"', page_text)
        )
        for context, dataset_id in re.findall(
            r'([A-Za-z0-9_-]+)-dataset="([^"]+)"', page_text
        ):
            entry = discovered.setdefault(
                dataset_id,
                {"dataset_id": dataset_id, "sources": [], "api_key": None, "contexts": []},
            )
            entry["sources"].append(f"page:{page_name}")
            entry["contexts"].append(context)
            if context in context_to_key:
                entry["api_key"] = context_to_key[context]
        for dataset_id in re.findall(r"/explore/dataset/([A-Za-z0-9_-]+)/", page_text):
            entry = discovered.setdefault(
                dataset_id,
                {"dataset_id": dataset_id, "sources": [], "api_key": None, "contexts": []},
            )
            entry["sources"].append(f"page-link:{page_name}")
    return discovered


def seed_sources(logger: logging.Logger | None = None) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for dataset_id, label in config.DIRECT_DATASETS.items():
        sources[dataset_id] = {
            "dataset_id": dataset_id,
            "sources": [f"direct:{label}"],
            "api_key": None,
            "contexts": [],
        }
    for dataset_id in config.KNOWN_LOAD_SPLITS | config.SUPPORTING_GEO_DATASETS:
        sources.setdefault(
            dataset_id,
            {
                "dataset_id": dataset_id,
                "sources": ["configured:derived_or_supporting"],
                "api_key": None,
                "contexts": [],
            },
        )
    for dataset_id, entry in discover_page_datasets(logger).items():
        if dataset_id in sources:
            sources[dataset_id]["sources"].extend(entry.get("sources", []))
            sources[dataset_id]["contexts"].extend(entry.get("contexts", []))
            sources[dataset_id]["api_key"] = sources[dataset_id].get("api_key") or entry.get(
                "api_key"
            )
        else:
            sources[dataset_id] = entry
    return sources


def fetch_metadata(dataset_id: str, api_key: str | None = None) -> tuple[int, dict[str, Any] | None]:
    response = request_url(metadata_url(dataset_id), api_key=api_key)
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()


def fetch_records_v2(
    dataset_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    api_key: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    response = request_url(
        records_v2_url(dataset_id),
        params={"limit": limit, "offset": offset, "timezone": config.TIMEZONE},
        api_key=api_key,
    )
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()


def fetch_records_v1(
    dataset_id: str,
    *,
    rows: int = 1,
    start: int = 0,
    api_key: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    response = request_url(
        records_v1_url(),
        params={
            "dataset": dataset_id,
            "rows": rows,
            "start": start,
            "timezone": config.TIMEZONE,
        },
        api_key=api_key,
    )
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    if "fields" in record:
        flat = dict(record.get("fields") or {})
        if "recordid" in record:
            flat["_recordid"] = record["recordid"]
        if "record_timestamp" in record:
            flat["_record_timestamp"] = record["record_timestamp"]
        return flat
    return dict(record)


def serializable_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    flattened = [{k: serializable_cell(v) for k, v in flatten_record(r).items()} for r in records]
    return pd.DataFrame(flattened)


def fetch_paginated_records(
    dataset_id: str,
    *,
    max_records: int | None,
    api_key: str | None = None,
    page_size: int = config.PAGE_SIZE,
    logger: logging.Logger | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    records: list[dict[str, Any]] = []
    total_count: int | None = None
    offset = 0
    while True:
        limit = page_size
        if max_records is not None:
            remaining = max_records - len(records)
            if remaining <= 0:
                break
            limit = min(limit, remaining)
        status, payload = fetch_records_v2(dataset_id, limit=limit, offset=offset, api_key=api_key)
        if status != 200 or payload is None:
            raise RuntimeError(f"Failed v2 record page for {dataset_id}: status={status}")
        total_count = payload.get("total_count", total_count)
        batch = payload.get("results", [])
        if not batch:
            break
        records.extend(batch)
        offset += len(batch)
        if logger:
            logger.info("%s records fetched for %s", len(records), dataset_id)
        if total_count is not None and offset >= total_count:
            break
        if max_records is not None and len(records) >= max_records:
            break
    return records, total_count


def test_export_formats(dataset_id: str, api_key: str | None = None) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for fmt in config.EXPORT_FORMATS:
        url = export_url(dataset_id, fmt)
        response = request_url(url, api_key=api_key, method="HEAD")
        if response.status_code == 405:
            response = request_url(url, api_key=api_key, method="GET", stream=True)
            response.close()
        status = response.status_code
        if status == 404:
            old = old_download_url(dataset_id, fmt)
            response = request_url(
                old,
                params={
                    "format": fmt,
                    "timezone": config.TIMEZONE,
                    "use_labels_for_header": "false",
                    "csv_separator": ";",
                },
                api_key=api_key,
                method="HEAD",
            )
            status = response.status_code
            url = old
        results[fmt] = {
            "status": status,
            "available": 200 <= status < 300,
            "content_type": response.headers.get("content-type"),
            "content_length": response.headers.get("content-length"),
            "url": url,
        }
    return results


def metadata_summary(
    dataset_id: str,
    metadata: dict[str, Any],
    sources: dict[str, Any],
    export_formats: dict[str, Any],
    api_url_used: str,
) -> dict[str, Any]:
    metas = metadata.get("metas", {}).get("default", {})
    fields = metadata.get("fields", [])
    return {
        "dataset_id": dataset_id,
        "title": metas.get("title") or metas.get("title_en") or metas.get("title_pt"),
        "description": strip_html(metas.get("description")),
        "license": metas.get("license"),
        "license_url": metas.get("license_url"),
        "update_frequency": metas.get("update_frequency"),
        "last_modified": metas.get("modified"),
        "data_processed": metas.get("data_processed"),
        "metadata_processed": metas.get("metadata_processed"),
        "publisher": metas.get("publisher"),
        "visibility": metadata.get("visibility"),
        "features": metadata.get("features", []),
        "fields": fields,
        "field_names": [field.get("name") for field in fields],
        "field_types": {field.get("name"): field.get("type") for field in fields},
        "geographic_coverage": {
            "geographic_reference": metas.get("geographic_reference"),
            "territory": metas.get("territory"),
            "geometry_types": metas.get("geometry_types"),
            "bbox": metas.get("bbox"),
        },
        "temporal_coverage": None,
        "records_count": metas.get("records_count"),
        "available_export_formats": export_formats,
        "api_url_used": api_url_used,
        "requires_page_api_key": bool(sources.get("api_key")),
        "source_refs": sorted(set(sources.get("sources", []))),
        "page_contexts": sorted(set(sources.get("contexts", []))),
    }


def load_catalog() -> dict[str, Any]:
    catalog = read_json(config.METADATA_DIR / "dataset_catalog.json", default={})
    if isinstance(catalog, list):
        return {entry["dataset_id"]: entry for entry in catalog}
    return catalog


def load_sources() -> dict[str, Any]:
    return read_json(config.METADATA_DIR / "discovered_sources.json", default={}) or {}


def save_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def load_dataset_frame(dataset_id: str, prefer_raw: bool = True) -> tuple[pd.DataFrame, str]:
    raw_path = config.RAW_DIR / f"{dataset_id}.csv"
    sample_path = config.SAMPLES_DIR / f"{dataset_id}_sample.csv"
    path = raw_path if prefer_raw and raw_path.exists() else sample_path
    if not path.exists():
        return pd.DataFrame(), "missing"
    first_line = path.open("r", encoding="utf-8-sig", errors="replace").readline()
    sep = ";" if first_line.count(";") > first_line.count(",") else ","
    try:
        return pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False), str(path)
    except Exception:
        return pd.read_csv(path, sep=sep, encoding="utf-8-sig", engine="python"), str(path)


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def candidate_key_columns(df: pd.DataFrame) -> list[str]:
    candidates = []
    for column in df.columns:
        normalized = normalize_name(column)
        if any(
            token in normalized
            for token in [
                "codigo",
                "code",
                "id",
                "nome",
                "name",
                "subestacao",
                "instalacao",
                "distrito",
                "concelho",
                "freguesia",
            ]
        ):
            candidates.append(column)
    return candidates


def profile_column(
    df: pd.DataFrame,
    column: str,
    official_type: str | None = None,
) -> dict[str, Any]:
    series = df[column]
    non_null = int(series.notna().sum())
    total = int(len(series))
    missing_pct = float((1 - non_null / total) * 100) if total else 0.0
    unique = int(series.nunique(dropna=True))
    examples = [str(v) for v in series.dropna().astype(str).head(3).tolist()]
    numeric = pd.to_numeric(series, errors="coerce")
    numeric_non_null = numeric.notna().sum()
    parsed_date = pd.to_datetime(series, errors="coerce", utc=True)
    date_non_null = parsed_date.notna().sum()
    looks_date = (official_type in {"date", "datetime"}) or name_matches(column, config.DATE_KEYWORDS)
    result = {
        "column_name": column,
        "inferred_data_type": str(series.infer_objects().dtype),
        "official_api_data_type": official_type,
        "non_null_values": non_null,
        "missing_value_pct": round(missing_pct, 4),
        "unique_values": unique,
        "example_values": " | ".join(examples),
        "numeric_min": None,
        "numeric_max": None,
        "date_min": None,
        "date_max": None,
        "looks_like_key_field": column in candidate_key_columns(df)
        or (total > 0 and unique / max(non_null, 1) > 0.95 and unique > 1),
        "looks_like_location_field": name_matches(column, config.LOCATION_KEYWORDS),
        "looks_like_grid_electrical_field": name_matches(column, config.ELECTRICAL_KEYWORDS),
    }
    if numeric_non_null > 0 and numeric_non_null >= max(non_null * 0.8, 1):
        result["numeric_min"] = float(numeric.min())
        result["numeric_max"] = float(numeric.max())
    if looks_date and date_non_null > 0:
        result["date_min"] = str(parsed_date.min())
        result["date_max"] = str(parsed_date.max())
    return result


def field_detection(metadata: dict[str, Any], sample_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fields = metadata.get("fields", [])
    names = [field.get("name", "") for field in fields]
    labels = [field.get("label", "") for field in fields]
    types = [field.get("type", "") for field in fields]
    combined = " ".join(names + labels).lower()
    return {
        "geometry_fields": [
            field.get("name")
            for field in fields
            if field.get("type") in {"geo_point_2d", "geo_shape"}
            or name_matches(field.get("name", ""), ("geo", "coordenad", "lat", "lon"))
        ],
        "date_time_fields": [
            field.get("name")
            for field in fields
            if field.get("type") in {"date", "datetime"}
            or name_matches(field.get("name", ""), config.DATE_KEYWORDS)
        ],
        "voltage_fields": [
            field.get("name") for field in fields if name_matches(field.get("name", ""), config.VOLTAGE_KEYWORDS)
        ],
        "substation_fields": [
            field.get("name")
            for field in fields
            if name_matches(field.get("name", ""), config.SUBSTATION_KEYWORDS)
            or name_matches(field.get("label", ""), config.SUBSTATION_KEYWORDS)
        ],
        "has_geometry": any(t in {"geo_point_2d", "geo_shape"} for t in types)
        or "coordenad" in combined,
        "has_date_time": any(t in {"date", "datetime"} for t in types)
        or any(name_matches(name, config.DATE_KEYWORDS) for name in names),
        "has_voltage": any(name_matches(name, config.VOLTAGE_KEYWORDS) for name in names + labels),
        "has_substation_identifier": any(
            name_matches(name, config.SUBSTATION_KEYWORDS) for name in names + labels
        ),
    }


def parse_point(value: Any) -> tuple[float, float] | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    obj: Any = value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            obj = json.loads(value)
        except json.JSONDecodeError:
            parts = re.findall(r"-?\d+\.\d+|-?\d+", value)
            if len(parts) >= 2:
                a, b = float(parts[0]), float(parts[1])
                if abs(a) <= 90 and abs(b) <= 180:
                    return a, b
                return b, a
            return None
    if isinstance(obj, dict):
        if "lat" in obj and "lon" in obj:
            return float(obj["lat"]), float(obj["lon"])
        if obj.get("type") == "Point" and isinstance(obj.get("coordinates"), list):
            lon, lat = obj["coordinates"][:2]
            return float(lat), float(lon)
        geometry = obj.get("geometry") if isinstance(obj.get("geometry"), dict) else None
        if geometry and geometry.get("type") == "Point":
            lon, lat = geometry.get("coordinates", [])[:2]
            return float(lat), float(lon)
    if isinstance(obj, list) and len(obj) >= 2:
        a, b = float(obj[0]), float(obj[1])
        if abs(a) <= 90 and abs(b) <= 180:
            return a, b
        return b, a
    return None


def coordinate_columns(df: pd.DataFrame) -> list[str]:
    return [
        column
        for column in df.columns
        if name_matches(column, ("geo_point", "coordenad", "coordinates", "lat", "lon"))
    ]


def extract_points(df: pd.DataFrame, limit: int = 1000) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    columns = coordinate_columns(df)
    for column in columns:
        for value in df[column].dropna().head(limit).tolist():
            point = parse_point(value)
            if point:
                points.append(point)
            if len(points) >= limit:
                return points
    lat_cols = [c for c in df.columns if normalize_name(c) in {"lat", "latitude"}]
    lon_cols = [c for c in df.columns if normalize_name(c) in {"lon", "lng", "longitude"}]
    if lat_cols and lon_cols:
        for lat, lon in zip(df[lat_cols[0]].head(limit), df[lon_cols[0]].head(limit)):
            try:
                points.append((float(lat), float(lon)))
            except (TypeError, ValueError):
                continue
    return points[:limit]


def haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def pairwise_dataset_ids(catalog: dict[str, Any]) -> list[tuple[str, str]]:
    return list(combinations(sorted(catalog), 2))


def value_overlap(left: pd.Series, right: pd.Series, normalize: bool = False) -> dict[str, Any]:
    lvals = left.dropna().astype(str)
    rvals = right.dropna().astype(str)
    if normalize:
        lset = {normalize_name(v) for v in lvals if normalize_name(v)}
        rset = {normalize_name(v) for v in rvals if normalize_name(v)}
    else:
        lset = set(lvals)
        rset = set(rvals)
    overlap = lset & rset
    denom = min(len(lset), len(rset)) or 1
    return {
        "overlap_count": len(overlap),
        "overlap_percentage": round(100 * len(overlap) / denom, 4),
        "left_unique": len(lset),
        "right_unique": len(rset),
    }


def confidence(overlap_count: int, overlap_pct: float, method: str) -> str:
    if overlap_count == 0:
        return "none"
    if method == "spatial":
        if overlap_pct >= 80:
            return "high"
        if overlap_pct >= 30:
            return "medium"
        return "low"
    if overlap_pct >= 80 and overlap_count >= 5:
        return "high"
    if overlap_pct >= 30 or overlap_count >= 10:
        return "medium"
    return "low"


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        values = [str(row.get(column, "")) for column in columns]
        values = [value.replace("\n", " ").replace("|", "\\|") for value in values]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"

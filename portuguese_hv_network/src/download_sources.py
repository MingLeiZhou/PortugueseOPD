#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

from common import MANIFESTS, PROJECT, RAW, ensure_dirs, read_json, trace, utc_now, write_json


USER_AGENT = "Portuguese-HV-Candidate research data pipeline/0.1"


def request(session: requests.Session, method: str, url: str, **kwargs: object) -> requests.Response:
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = session.request(method, url, timeout=300, headers={"User-Agent": USER_AGENT}, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Request failed: {url}: {last}")


def discover_widget_keys(session: requests.Session, pages: list[str]) -> dict[str, str]:
    discovered: dict[str, str] = {}
    for page in pages:
        response = request(session, "GET", page)
        text = html.unescape(response.text)
        context_keys = dict(re.findall(r'([A-Za-z0-9_-]+)-apikey="([^"]+)"', text))
        for context, dataset_id in re.findall(r'([A-Za-z0-9_-]+)-dataset="([^"]+)"', text):
            if context in context_keys:
                discovered[dataset_id] = context_keys[context]
    return discovered


def download_file(session: requests.Session, url: str, path: Path, api_key: str | None, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    params = {"apikey": api_key} if api_key else None
    response = request(session, "GET", url, params=params, stream=True)
    temporary = path.with_suffix(path.suffix + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(1024 * 1024):
            if chunk:
                handle.write(chunk)
    temporary.replace(path)


def download_eredes(session: requests.Session, sources: dict[str, object], overwrite: bool) -> list[dict[str, object]]:
    base = str(sources["eredes_api_base"])
    pages = [str(sources["eredes_page"]), str(sources["eredes_rari_page"])]
    keys = discover_widget_keys(session, pages)
    rows: list[dict[str, object]] = []
    for dataset_id in sources["eredes_geo_datasets"]:
        for fmt in ("geojson", "csv"):
            url = f"{base}/{dataset_id}/exports/{fmt}"
            path = RAW / "eredes" / f"{dataset_id}.{fmt}"
            download_file(session, url, path, keys.get(str(dataset_id)), overwrite)
            rows.append(trace(path, str(dataset_id), url, "E-REDES topology input"))
    for dataset_id in sources["eredes_context_datasets"]:
        url = f"{base}/{dataset_id}/exports/csv"
        path = RAW / "eredes" / f"{dataset_id}.csv"
        download_file(session, url, path, keys.get(str(dataset_id)), overwrite)
        rows.append(trace(path, str(dataset_id), url, "E-REDES electrical or boundary context"))
    return rows


def select_and_download_load_snapshot(
    session: requests.Session, sources: dict[str, object], overwrite: bool, timestamp: str | None
) -> list[dict[str, object]]:
    output = RAW / "eredes" / "load_snapshot.csv"
    meta = RAW / "eredes" / "load_snapshot_selection.json"
    if output.exists() and meta.exists() and not overwrite:
        saved = read_json(meta)
        return [trace(output, "eredes-load-snapshot", str(saved["query_url"]), "synchronised substation active-load snapshot")]
    base = str(sources["eredes_api_base"])
    pages = [str(sources["eredes_page"]), str(sources["eredes_rari_page"])]
    keys = discover_widget_keys(session, pages)
    partitions = list(sources["eredes_load_partitions"])
    if timestamp is None:
        # A recent timestamp with broad coverage is found from compact grouped
        # queries rather than downloading the complete 14-million-row history.
        aggregate: list[pd.DataFrame] = []
        for dataset_id in partitions:
            url = f"{base}/{dataset_id}/exports/csv"
            params = {
                "select": "datahora,count(*) as record_count,count(distinct codigo_subestacao) as substation_count,sum(energia) as total_energia_kwh",
                "group_by": "datahora",
                "order_by": "datahora desc",
                "limit": "10000",
            }
            if keys.get(dataset_id):
                params["apikey"] = keys[dataset_id]
            response = request(session, "GET", url, params=params)
            frame = pd.read_csv(io.BytesIO(response.content), sep=";", encoding="utf-8-sig")
            frame["datahora"] = pd.to_datetime(frame["datahora"], utc=True)
            frame["partition"] = dataset_id
            aggregate.append(frame)
        joined = pd.concat(aggregate, ignore_index=True)
        pivot = joined.pivot_table(index="datahora", columns="partition", values="substation_count", aggfunc="max")
        complete = pivot.dropna()
        if complete.empty:
            raise RuntimeError("No timestamp shared by every load partition")
        timestamp_value = complete.sum(axis=1).idxmax()
    else:
        timestamp_value = pd.Timestamp(timestamp)
        if timestamp_value.tzinfo is None:
            timestamp_value = timestamp_value.tz_localize("UTC")
    literal = timestamp_value.strftime("%Y-%m-%dT%H:%M:%SZ")
    frames: list[pd.DataFrame] = []
    query_urls: list[str] = []
    for dataset_id in partitions:
        url = f"{base}/{dataset_id}/exports/csv"
        params = {"where": f"datahora = date'{literal}'"}
        if keys.get(dataset_id):
            params["apikey"] = keys[dataset_id]
        response = request(session, "GET", url, params=params)
        frame = pd.read_csv(io.BytesIO(response.content), sep=";", encoding="utf-8-sig")
        frame["source_partition"] = dataset_id
        frames.append(frame)
        safe_params = {key: value for key, value in params.items() if key != "apikey"}
        query_urls.append(f"{url}?{urlencode(safe_params)}")
    snapshot = pd.concat(frames, ignore_index=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(output, index=False)
    selection = {
        "selected_timestamp_utc": timestamp_value.isoformat(),
        "rows": len(snapshot),
        "query_url": query_urls,
        "selection_rule": "maximum summed substation coverage among timestamps returned by all five partition aggregates",
    }
    write_json(meta, selection)
    return [trace(output, "eredes-load-snapshot", " | ".join(query_urls), "synchronised substation active-load snapshot")]


def overpass_query(session: requests.Session, endpoints: list[str], query: str) -> tuple[dict[str, object], str]:
    last: Exception | None = None
    for endpoint in endpoints:
        try:
            response = request(session, "POST", endpoint, data={"data": query})
            return response.json(), endpoint
        except (requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
            last = exc
    raise RuntimeError(f"All Overpass endpoints failed: {last}")


def download_osm(session: requests.Session, sources: dict[str, object], overwrite: bool) -> list[dict[str, object]]:
    output = RAW / "osm" / "portugal_hv_osm.json"
    endpoint_file = RAW / "osm" / "overpass_endpoint.json"
    if not output.exists() or overwrite:
        query = r'''[out:json][timeout:300];
area["ISO3166-1"="PT"][admin_level=2]->.pt;
(
  way(area.pt)["power"~"^(line|cable)$"]["voltage"~"(^|;)(60000|130000|150000|220000|400000)($|;)"];
  relation(area.pt)["power"~"^(line|cable)$"]["voltage"~"(^|;)(60000|130000|150000|220000|400000)($|;)"];
  nwr(area.pt)["power"~"^(substation|transformer)$"];
  nwr(area.pt)["power"="plant"];
  nwr(area.pt)["power"="generator"]["generator:output:electricity"~"MW|GW",i];
);
out body geom;'''
        data, endpoint = overpass_query(session, list(sources["overpass_endpoints"]), query)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, data)
        write_json(endpoint_file, {"endpoint": endpoint, "query": query, "downloaded_at": utc_now()})
    endpoint = read_json(endpoint_file)["endpoint"] if endpoint_file.exists() else "Overpass API"
    return [trace(output, "openstreetmap-portugal-hv", str(endpoint), "150/220/400 kV geometry, substations, transformers, and generation candidates")]


def download_geofabrik_osm(session: requests.Session, sources: dict[str, object], overwrite: bool) -> list[dict[str, object]]:
    """Download the complete Portugal OSM snapshot, including relation members."""
    url = str(sources["geofabrik_portugal_pbf"])
    output = RAW / "osm" / "portugal-latest.osm.pbf"
    download_file(session, url, output, None, overwrite)
    return [
        trace(
            output,
            "geofabrik-portugal-osm-pbf",
            url,
            "complete Portugal OSM snapshot for power ways, facilities, circuits, and line-section relations",
        )
    ]


def download_reference_sources(session: requests.Session, sources: dict[str, object], overwrite: bool) -> list[dict[str, object]]:
    specifications = [
        ("geofabrik-portugal-poly", "geofabrik_portugal_poly", RAW / "osm" / "portugal.poly", "Geofabrik extraction boundary audit"),
        ("eurostat-gisco-portugal-2024", "gisco_portugal_boundary", RAW / "reference" / "portugal_gisco_2024.geojson", "official country-boundary scope audit"),
        ("ren-rnt-map-2025", "ren_rnt_map_2025_pdf", RAW / "ren" / "ren_rnt_map_2025.pdf", "official RNT circuit lengths, substations, transformers, and directly connected plants"),
        ("ren-rnt-quality-2024", "ren_rnt_quality_2024_pdf", RAW / "ren" / "ren_rnt_quality_2024.pdf", "official voltage-pair transformer capacity totals and RNT service context"),
        ("eredes-pdird-e-2020-annex-b", "eredes_pdird_2020_annex_b_pdf", RAW / "eredes" / "pdird" / "pdird_e_2020_annex_b.pdf", "official 60 kV conductor and nominal-current evidence for the 31-12-2025 planning table"),
    ]
    records: list[dict[str, object]] = []
    for source_id, key, path, role in specifications:
        url = str(sources[key])
        download_file(session, url, path, None, overwrite)
        records.append(trace(path, source_id, url, role))
    pdird_url = str(sources["eredes_pdird_2020_annex_b_pdf"])
    derived_pdird = [
        ("eredes-pdird-2025-at-circuits-parsed", RAW / "eredes" / "pdird" / "pdird_2025_at_circuits.csv", "parsed 31-12-2025 AT circuit/conductor/current table derived from the official PDIRD annex"),
        ("eredes-pdird-pt60-selected-matches", RAW / "eredes" / "pdird" / "pdird_pt60_selected_circuit_matches.csv", "reviewed PT60-to-PDIRD circuit-name match table derived from the official PDIRD annex"),
    ]
    for source_id, path, role in derived_pdird:
        if path.exists():
            records.append(trace(path, source_id, pdird_url, role))
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--timestamp", help="ISO timestamp for the E-REDES load snapshot")
    parser.add_argument("--skip-osm", action="store_true")
    parser.add_argument(
        "--osm-source", choices=("geofabrik", "overpass"), default="geofabrik",
        help="Geofabrik preserves the complete country snapshot and relation membership; Overpass is a lightweight fallback.",
    )
    args = parser.parse_args()
    ensure_dirs()
    sources = read_json(PROJECT / "config" / "sources.json")
    session = requests.Session()
    records = download_eredes(session, sources, args.overwrite)
    records += select_and_download_load_snapshot(session, sources, args.overwrite, args.timestamp)
    records += download_reference_sources(session, sources, args.overwrite)
    if not args.skip_osm:
        records += (
            download_geofabrik_osm(session, sources, args.overwrite)
            if args.osm_source == "geofabrik"
            else download_osm(session, sources, args.overwrite)
        )
    else:
        # Skipping acquisition must not remove an already frozen OSM snapshot
        # from the regenerated manifest.
        existing_pbf = RAW / "osm" / "portugal-latest.osm.pbf"
        existing_overpass = RAW / "osm" / "portugal_hv_osm.json"
        if existing_pbf.exists():
            records.append(
                trace(
                    existing_pbf,
                    "geofabrik-portugal-osm-pbf",
                    str(sources["geofabrik_portugal_pbf"]),
                    "complete Portugal OSM snapshot for power ways, facilities, circuits, and line-section relations",
                )
            )
        elif existing_overpass.exists():
            records.append(trace(existing_overpass, "openstreetmap-portugal-hv", "Overpass API", "fallback OSM high-voltage extract"))
    manifest = {
        "generated_at": utc_now(),
        "project": "Portuguese-HV-Candidate",
        "records": records,
        "api_keys_persisted": False,
        "note": "Public page widget keys are discovered at runtime and are never written to the manifest.",
    }
    write_json(MANIFESTS / "source_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

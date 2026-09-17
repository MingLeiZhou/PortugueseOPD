"""Export frozen PT60 fields into the existing Cloudflare map's static contract."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile

from .dataset import VARIANTS, digest, rows


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def feature(properties, coordinates, kind="Point"):
    return {"type": "Feature", "geometry": {"type": kind, "coordinates": coordinates}, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def export_web(dataset, output, result_dir=None):
    """Create a new directory; verify all inputs and IDs before exposing its index."""
    output = dataset._output(output)
    if output.exists():
        raise ValueError("Web output must be a new directory; keep the previous export until validation passes")
    verification = dataset.verify()
    if verification["status"] != "PASS":
        raise ValueError("Release integrity check failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pt60-web-", dir=output.parent) as tmp:
        target = Path(tmp) / "data"
        target.mkdir()
        topology = dataset.topology()
        buses = {r["bus_id"]: r for r in topology["buses"]}
        lines = {r["line_id"]: r for r in topology["lines"]}
        bus_features = [feature({"id": ident, "name": r["facility_name"],
            "voltage_kv": number(r["voltage_kv"]), "source": r["source"],
            "source_status": r["source_status"], "object_type": "bus"},
            [number(r["lon"]), number(r["lat"])]) for ident, r in buses.items()]
        # Case-dependent ratings and result fields are deliberately absent here.
        line_features = [feature({"id": ident, "name": r["name"], "object_type": "line",
            "voltage_kv": number(r["voltage_kv"]), "source": r["source"],
            "source_status": r["source_status"], "from_bus": r["from_bus"], "to_bus": r["to_bus"],
            "length_km": number(r["length_km"]), "parallel": number(r["parallel"])},
            r["coordinates"], "LineString") for ident, r in lines.items()]
        trafos = rows(dataset.path("topology/transformers.csv"))
        trafo_features = [feature({"id": r["transformer_id"], "hv_kv": number(r["hv_kv"]),
            "lv_kv": number(r["lv_kv"]), "sn_mva": number(r["sn_mva"]),
            "source_status": r["source_status"], "object_type": "transformer"},
            [number(buses[r["hv_bus"]]["lon"]), number(buses[r["hv_bus"]]["lat"])]) for r in trafos]
        facilities = rows(dataset.path("topology/facilities.csv"))
        facility_features = [feature({"id": r["facility_id"], "name": r["name"],
            "facility_type": r["facility_type"], "source": r["source"], "object_type": "facility"},
            [number(r["lon"]), number(r["lat"])]) for r in facilities]
        generators = rows(dataset.path("scenario/generators.csv"))
        generator_features = [feature({"id": r["generator_id"], "name": r["name"],
            "source_id": r["source_id"], "generation_source": r["generation_source"],
            "nameplate_mw": number(r["nameplate_mw"]), "bus_id": r["bus_id"],
            "bus_voltage_kv": number(r["bus_voltage_kv"]), "source_status": r["source_status"],
            "connection_evidence_status": r.get("connection_evidence_status"),
            "object_type": "generation_asset"}, [number(r["lon"]), number(r["lat"])])
            for r in generators if r["bus_id"] in buses and number(r["lon"]) is not None and number(r["lat"]) is not None]
        for name, features in {"buses": bus_features, "lines": line_features, "transformers": trafo_features,
                "facilities": facility_features, "generators": generator_features, "boundaries": [],
                "substation_areas": [], "power_equipment": [], "line_supports": []}.items():
            write(target / f"{name}.geojson", collection(features))
        alternatives = {(r["case_id"], r["variant"]): r for r in rows(dataset.path("validation/spatial_allocation_results.csv"))}
        cases = dataset.cases()
        custom = None
        if result_dir:
            from .viewer import load_custom
            info, custom = load_custom(dataset, result_dir)
            local_summary = json.loads((Path(result_dir) / "summary.json").read_text())
            sweep_path = Path(result_dir) / "scenario_sweep.csv"
            local_sweep = rows(sweep_path) if sweep_path.exists() else []
            cases.append({**local_summary, **info, "converged": "True"})
        catalog = []
        count = 0
        for case in cases:
            ident = case["case_id"]
            local = case["season"] == "CUSTOM"
            slug = "local-result" if local else ident.lower()
            variants = ("AC_REVISED",) if local else VARIANTS
            catalog.append({"case_id": ident, "slug": slug, "timestamp_utc": case["timestamp_utc"],
                "group_id": case["season"].lower(), "model_revision": "local-result" if local else dataset.manifest["version"],
                "variants": list(variants), "input_available": not local})
            if not local:
                write(target / "snapshots" / slug / "input.json", dataset.load_input(ident))
            for variant in variants:
                row = case if variant == "AC_REVISED" else alternatives[(ident, variant)]
                result = custom if local else dataset.results(ident, variant)
                if set(result["bus_voltage"]) != set(buses) or set(result["line_loading"]) != set(lines):
                    raise ValueError(f"Device IDs differ from topology: {ident}/{variant}")
                checks = [(min(v for v in result["bus_voltage"].values() if v is not None), "vm_pu_min"),
                    (max(v for v in result["bus_voltage"].values() if v is not None), "vm_pu_max"),
                    (max(v for v in result["line_loading"].values() if v is not None), "maximum_line_loading_percent")]
                if any(not math.isclose(v, float(row[key]), abs_tol=1e-10, rel_tol=0) for v, key in checks):
                    raise ValueError(f"Device fields disagree with summary: {ident}/{variant}")
                overlay = {"case_id": ident, "variant": variant, "boundary_bus_ids": [], "schema_version": 2,
                    "tables": {"buses": {"columns": ["id", "vm_pu"], "rows": list(map(list, result["bus_voltage"].items()))},
                        "lines": {"columns": ["id", "loading_percent"], "rows": list(map(list, result["line_loading"].items()))},
                        "transformers": {"columns": ["id"], "rows": []}, "generators": {"columns": ["id"], "rows": []}}}
                peak = max((key for key, value in result["line_loading"].items() if value is not None), key=lambda key: result["line_loading"][key])
                peak_line = lines[peak]
                peak_value = result["line_loading"][peak]
                summary = {"case_id": ident, "variant": variant, "version": "local-result" if local else dataset.manifest["version"],
                    "snapshot_kind": "CUSTOM_INPUT" if local else "SYNCHRONIZED_PUBLIC_RECORDS", "calibration_timestamp_utc": row["timestamp_utc"],
                    "converged": row["converged"] == "True", "buses": int(row["active_buses"]), "lines": int(row["active_lines"]),
                    "transformers": len(trafos), "facilities": len(facilities), "generation_assets": len(generator_features),
                    "vm_pu_min": number(row["vm_pu_min"]), "vm_pu_max": number(row["vm_pu_max"]),
                    "line_loading_percent_max": number(row["maximum_line_loading_percent"]),
                    "trafo_loading_percent_max": number(row["maximum_transformer_loading_percent"]),
                    "total_load_p_mw": number(row["total_modeled_load_mw"]), "total_generation_p_mw": number(row["total_modeled_generation_mw"]),
                    "losses_p_mw": number(row["model_losses_mw"]), "overloaded_line_rows": int(row["lines_over_100_percent"]),
                    "scenario_sweep": ([{
                        "scaling": number(item.get("scaling")),
                        "vm_pu_min": number(item.get("vm_pu_min")),
                        "vm_pu_max": number(item.get("vm_pu_max")),
                        "line_loading_percent_max": number(item.get("line_loading_percent_max")),
                        "transformer_loading_percent_max": number(item.get("transformer_loading_percent_max")),
                        "within_declared_screening_limits": str(item.get("within_declared_screening_limits", "")).lower() == "true",
                    } for item in local_sweep] if local else []), "risk_items": [{"id": peak, "kind": "LINE", "object_id": peak,
                        "title": peak_line["name"] or peak, "center": peak_line["coordinates"][len(peak_line["coordinates"]) // 2],
                        "radius_km": 2, "zoom": 10, "loading_percent": peak_value, "p_mw": None,
                        "voltage_label": f"{peak_line['voltage_kv']} kV", "severity": "OVER_LIMIT" if peak_value > 100 else "HOTSPOT"}]}
                root = target / "snapshots" / slug / variant
                write(root / "results.json", overlay)
                write(root / "summary.json", summary)
                if count == 0:
                    write(target / "summary.json", summary)
                count += 1
        for name in ["seasonal_week_validation.csv", "spatial_allocation_results.csv"]:
            # Export scientific values without machine-local paths from experiment logs.
            import csv
            table = rows(dataset.path(f"validation/{name}"))
            columns = [key for key in table[0] if key != "spatial_result_path"]
            download = target / "downloads" / name
            download.parent.mkdir(exist_ok=True)
            with download.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                writer.writeheader(); writer.writerows(table)
        metadata = {"version": dataset.manifest["version"], "formulation": "AC-PF", "cases": len(catalog),
            "variants": list(VARIANTS), "result_sets": count, "verification": verification,
            "release_manifest_sha256": digest(dataset.path("manifest.json")),
            "sampling": "Hourly samples of 15-minute intervals; timestamps are UTC interval starts",
            "device_fields": ["bus voltage magnitude (p.u.)", "line loading (%)"],
            "generator_layer": "Mapped asset inventory; not time-specific generator dispatch"}
        if result_dir:
            metadata["custom_result"] = {"case_id": custom["case_id"], "summary_sha256": digest(Path(result_dir) / "summary.json")}
        write(target / "metadata.json", metadata)
        write(target / "snapshots/index.json", {"snapshots": catalog, "default_case_id": catalog[0]["case_id"], "groups": [
            {"id": "summer", "label": {"en": "Summer week · 2025", "pt": "Semana de verão · 2025", "zh": "夏季周 · 2025"}},
            {"id": "winter", "label": {"en": "Winter week · 2026", "pt": "Semana de inverno · 2026", "zh": "冬季周 · 2026"}},
            *([{"id": "custom", "label": {"en": "Local solved case", "pt": "Caso local calculado", "zh": "本地求解案例"}}] if result_dir else [])]})
        shutil.move(str(target), str(output))
    return metadata

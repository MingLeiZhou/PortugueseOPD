#!/usr/bin/env python3
"""Export the revised 336 hourly cases without changing research or release inputs.

Full device results were not retained by the week runner. Re-solve with identical
inputs, assert agreement with the saved research results, then emit columnar
map overlays. Only publish the index once every case has passed verification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import pandas as pd
import pandapower as pp

from common import PROJECT, sha256, utc_now
from export_web_snapshot_data import (
    WEB_DATA, SNAPSHOT_DATA, clean, line_records, records_by_id, snapshot_risks,
)
from run_seasonal_week_panel import hourly_cases
from run_temporal_validation import OUTPUT, allocate_generation, run_case

REVISION = OUTPUT / "seasonal_revision"
EXPORT_LOG = OUTPUT / "web_seasonal_export"
CHECK_FIELDS = (
    "model_net_import_mw", "model_losses_mw", "vm_pu_min", "vm_pu_max",
    "maximum_line_loading_percent", "maximum_transformer_loading_percent",
    "total_modeled_load_mw", "total_modeled_generation_mw",
    "unmapped_generation_residual_mw", "lines_over_100_percent",
)
CONTEXT: dict = {}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    temporary.replace(path)


def columnar(rows: list[dict]) -> dict:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    def number(value):
        value = clean(value)
        if isinstance(value, float):
            return round(value, 6) if math.isfinite(value) else None
        return value
    return {"columns": columns, "rows": [[number(row.get(key)) for key in columns] for row in rows]}


def initialise(fingerprint: str) -> None:
    CONTEXT.update(
        fingerprint=fingerprint,
        generators=pd.read_csv(REVISION / "generators_revised.csv", low_memory=False),
        expected=pd.read_csv(REVISION / "seasonal_week_validation.csv").set_index("case_id"),
        base=json.loads((WEB_DATA / "summary.json").read_text()),
    )
    for kind in ("lines", "transformers"):
        CONTEXT[kind] = {str(f["properties"]["id"]): f for f in json.loads((WEB_DATA / f"{kind}.geojson").read_text())["features"]}


def export_case(case: dict) -> dict:
    case_id = case["case_id"]
    slug = case_id.lower()
    target = SNAPSHOT_DATA / slug
    completion = EXPORT_LOG / f"{slug}.json"
    if completion.exists():
        old = json.loads(completion.read_text())
        if old.get("fingerprint") == CONTEXT["fingerprint"] and all(
            (target / name).exists() and sha256(target / name) == digest
            for name, digest in old["files"].items()
        ):
            return old
    with TemporaryDirectory(prefix="pt60-web-solved-") as work:
        result, *_ = run_case(case, CONTEXT["generators"], False, save_solved=True,
            model_input=REVISION / "model_revised.json", output_dir=Path(work))
        expected = CONTEXT["expected"].loc[case_id]
        assert result["converged"] and abs(result["active_generation_injection_error_mw"]) < 1e-8
        for field in CHECK_FIELDS:
            if not math.isclose(float(result[field]), float(expected[field]), rel_tol=0, abs_tol=1e-5):
                raise AssertionError(f"{case_id}: {field} differs from research result")
        net = pp.from_json(Path(work) / f"{case_id}_solved.json")
    timestamp = pd.Timestamp(case["timestamp_utc"])
    allocated, _ = allocate_generation(CONTEXT["generators"], result["ren_observation"]["generation_by_source_mw"], timestamp)
    bus_voltage = net.bus.set_index("bus_id").vn_kv
    generators = []
    for row in allocated.to_dict("records"):
        bus_id = clean(row.get("bus_id"))
        generators.append(dict(
            id=row["generator_id"], p_mw=row["scenario_p_mw"], q_mvar=None,
            bus_id=bus_id, bus_voltage_kv=clean(bus_voltage.get(bus_id)),
            match_distance_m=clean(row.get("match_distance_m")),
            source_status=clean(row.get("source_status")),
            connection_evidence_status=clean(row.get("connection_evidence_status")),
            in_service=bool(row["snapshot_asset_available"]) and bool(bus_id),
            dispatch_status="CAPACITY_CONSTRAINED_ALLOCATION_NOT_UNIT_TELEMETRY",
        ))
    boundary_bus_ids = [str(net.bus.loc[int(b), "bus_id"]) for b in net.ext_grid.bus]
    boundary_bus_ids += [str(net.bus.loc[int(b), "bus_id"]) for b in net.sgen.loc[
        net.sgen.type.eq("cross_border_aggregate_equivalent"), "bus"]]
    line_rows = line_records(net)
    # The base map's January limits are not valid for all seasonal overlays.
    for row, (_, line) in zip(line_rows, net.line.iterrows()):
        row.update(max_i_ka=clean(line.max_i_ka), parameter_status=clean(line.get("parameter_status")))
    overlay = dict(
        schema_version=2, case_id=case_id, boundary_bus_ids=boundary_bus_ids,
        tables=dict(
            lines=columnar(line_rows),
            buses=columnar(records_by_id(net.bus, net.res_bus, "bus_id", ["vm_pu", "va_degree", "p_mw", "q_mvar"])),
            transformers=columnar(records_by_id(net.trafo, net.res_trafo, "transformer_id",
                ["loading_percent", "p_hv_mw", "p_lv_mw", "q_hv_mvar", "q_lv_mvar", "pl_mw"])),
            generators=columnar(generators),
        ),
    )
    summary = dict(CONTEXT["base"])
    # Do not inherit January's sweep or daily evidence warnings into an hour.
    for key in list(summary):
        if key.startswith("continuous_") or key in ("risk_notices",):
            summary.pop(key)
    summary.update(
        case_id=case_id, generated_at=utc_now(), model_revision="CONNECTION_REVISED",
        snapshot_kind="SYNCHRONIZED_PUBLIC_RECORDS", group_id=case["season"].lower() + "-week",
        calibration_timestamp_utc=case["timestamp_utc"], load_profile_mode=case["load_profile_mode"],
        new_interconnector_in_service=False, converged=True,
        buses=result["active_buses"], lines=result["active_lines"], transformers=int(net.trafo.in_service.sum()),
        generation_assets=int(sum(r["in_service"] for r in generators)),
        vm_pu_min=result["vm_pu_min"], vm_pu_max=result["vm_pu_max"],
        line_loading_percent_max=result["maximum_line_loading_percent"],
        trafo_loading_percent_max=result["maximum_transformer_loading_percent"],
        overloaded_line_rows=result["lines_over_100_percent"],
        overloaded_transformer_rows=int(net.res_trafo.loading_percent.gt(100).sum()),
        total_load_p_mw=result["total_modeled_load_mw"], total_generation_p_mw=result["total_modeled_generation_mw"],
        total_ext_grid_p_mw=result["model_net_import_mw"], losses_p_mw=result["model_losses_mw"],
        unmapped_generation_residual_mw=result["unmapped_generation_residual_mw"],
        net_import_absolute_error_mw=result["net_import_absolute_error_mw"],
        observed_net_import_mw=result["observed_net_import_mw"],
        eredes_profile_fraction_of_national_load=result["eredes_profile_fraction_of_national_load"],
        model_rnt_scope_losses_percent_of_load=result["model_rnt_scope_losses_percent_of_load"],
        ren_rnt_monthly_loss_percent=result["ren_rnt_monthly_loss_percent"],
        rating_factor_relative_to_static_summer=case["rating_factor_relative_to_static_summer"],
        risk_items=snapshot_risks(net, CONTEXT["lines"], CONTEXT["transformers"]),
        scenario_sweep=[], validation_status="PASS_WITH_FLAGGED_LIMITATIONS",
        export_fingerprint=CONTEXT["fingerprint"],
    )
    write_json(target / "results.json", overlay)
    write_json(target / "summary.json", summary)
    record = dict(
        case_id=case_id, slug=slug, timestamp_utc=case["timestamp_utc"],
        generator_geometry_url="/data/snapshots/generators_revised.geojson",
        group_id=summary["group_id"], model_revision="CONNECTION_REVISED",
        snapshot_kind=summary["snapshot_kind"], new_interconnector_in_service=False,
        fingerprint=CONTEXT["fingerprint"],
        files={name: sha256(target / name) for name in ("results.json", "summary.json")},
    )
    write_json(completion, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Pilot only: do not publish an incomplete index")
    args = parser.parse_args()
    manifest = json.loads((REVISION / "experiment_manifest.json").read_text())
    for source in manifest["sources"]:
        if sha256(PROJECT.parent / source["path"]) != source["sha256"]:
            raise RuntimeError(f"Research source changed: {source['path']}")
    # Sequential asset IDs in the legacy map do not all refer to the same
    # assets as the revised inventory. Preserve the old map for references;
    # supply a version-matched geometry/identity layer for both revised weeks.
    asset_features = []
    generators = pd.read_csv(REVISION / "generators_revised.csv", low_memory=False)
    for row in generators.to_dict("records"):
        if row["source_status"] == "OUTSIDE_PORTUGAL_CONTINENTAL_MODEL_SCOPE":
            continue
        if not all(math.isfinite(float(row[key])) for key in ("lon", "lat")):
            raise AssertionError(f"Invalid mapped geometry: {row['generator_id']}")
        properties = {key: clean(row.get(key)) for key in (
            "source_id", "name", "generation_source", "nameplate_mw", "bus_id",
            "bus_voltage_kv", "match_distance_m", "source_status", "available_from_utc",
            "connection_evidence_status", "connection_evidence_url",
        )}
        properties.update(id=row["generator_id"], object_type="generation_asset",
            source="DGEG" if str(row["source_id"]).startswith("DGEG:") else "Public asset record")
        asset_features.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[row["lon"], row["lat"]]), properties=properties))
    write_json(SNAPSHOT_DATA / "generators_revised.geojson", dict(type="FeatureCollection", features=asset_features))
    sources = [Path(__file__), Path(__file__).with_name("export_web_snapshot_data.py"),
        Path(__file__).with_name("run_seasonal_week_panel.py"), REVISION / "seasonal_week_validation.csv",
        *WEB_DATA.glob("*.geojson"), WEB_DATA / "summary.json"]
    fingerprint = hashlib.sha256(json.dumps([manifest["run_fingerprint"],
        [(str(p), sha256(p)) for p in sorted(sources)]], sort_keys=True).encode()).hexdigest()
    cases = hourly_cases()
    records = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=initialise, initargs=(fingerprint,)) as pool:
        futures = [pool.submit(export_case, case) for case in cases[:args.limit]]
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(f"{len(records)}/{len(futures)} {record['timestamp_utc']}", flush=True)
    if args.limit:
        return
    assert len(records) == 336 and len({r["case_id"] for r in records}) == 336
    current = json.loads((SNAPSHOT_DATA / "index.json").read_text())
    representatives = [dict(row, group_id="representative", model_revision="ORIGINAL_REFERENCE")
        for row in current["snapshots"] if row.get("group_id", "representative") == "representative"]
    assert len(representatives) == 3, "Preserve the three existing representative snapshots"
    for row in representatives:
        for name in ("summary.json", "results.json"):
            assert (SNAPSHOT_DATA / row["slug"] / name).is_file()
    snapshots = representatives + [
        {k: v for k, v in row.items() if k not in ("fingerprint", "files")}
        for row in sorted(records, key=lambda r: r["timestamp_utc"])
    ]
    index = dict(generated_at=utc_now(), default_case_id=representatives[0]["case_id"], snapshots=snapshots,
        groups=[
            dict(id="representative", label=dict(zh="代表快照 · 原版", en="Reference snapshots · original", pt="Cenários de referência · original")),
            dict(id="summer-week", label=dict(zh="夏季连续周 · 修订", en="Summer week · revised", pt="Semana de verão · revista")),
            dict(id="winter-week", label=dict(zh="冬季连续周 · 修订", en="Winter week · revised", pt="Semana de inverno · revista")),
        ])
    write_json(SNAPSHOT_DATA / "index.json", index)
    write_json(EXPORT_LOG / "manifest.json", dict(
        fingerprint=fingerprint, generated_at=utc_now(), verified_samples=336,
        metric_tolerance=1e-5, map_decimal_places=6, snapshots=records,
        research_manifest_sha256=sha256(REVISION / "experiment_manifest.json"),
        interpretation="Hourly steady-state model outputs, not measured device flows. No sensitivity-only cases included.",
    ))
    print(json.dumps(dict(snapshots=len(snapshots), revised_hourly=336, output=str(SNAPSHOT_DATA))))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Export compact, switchable temporal overlays for the PT60 web map."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pandapower as pp

from common import PROJECT, read_json, utc_now
from run_temporal_validation import CASES, GENERATOR_INPUT, OUTPUT, allocate_generation, ren_observation


WEB_DATA = PROJECT / "site" / "public" / "data"
SNAPSHOT_DATA = WEB_DATA / "snapshots"


def clean(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def records_by_id(table: pd.DataFrame, result: pd.DataFrame, id_column: str, fields: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, asset in table.iterrows():
        result_row = result.loc[index] if index in result.index else None
        row = {"id": str(asset[id_column]), "in_service": bool(asset.get("in_service", True))}
        for field in fields:
            row[field] = clean(result_row.get(field)) if result_row is not None else None
        rows.append(row)
    return rows


def line_records(net: pp.pandapowerNet) -> list[dict[str, Any]]:
    rows = records_by_id(
        net.line,
        net.res_line,
        "line_id",
        ["loading_percent", "p_from_mw", "p_to_mw", "q_from_mvar", "q_to_mvar", "pl_mw", "ql_mvar", "i_ka"],
    )
    for row in rows:
        flow = max(
            abs(float(row.get("p_from_mw") or 0.0)), abs(float(row.get("p_to_mw") or 0.0)),
            abs(float(row.get("q_from_mvar") or 0.0)), abs(float(row.get("q_to_mvar") or 0.0)),
        )
        if not row["in_service"] or row["loading_percent"] is None:
            row["scenario_flow_status"] = "NOT_ENERGIZED_IN_ACTIVE_COMPONENT"
        elif flow < 1e-9:
            row["scenario_flow_status"] = "ZERO_FLOW_UNDER_CURRENT_SCENARIO"
        elif flow < 0.01:
            row["scenario_flow_status"] = "BELOW_0_01_MW"
        else:
            row["scenario_flow_status"] = "ACTIVE_FLOW"
    return rows


def generator_records(case: dict[str, Any], generators: pd.DataFrame) -> list[dict[str, Any]]:
    timestamp = pd.Timestamp(case["timestamp_utc"])
    observation = ren_observation(timestamp, refresh=False)
    allocated, _ = allocate_generation(generators, observation["generation_by_source_mw"], timestamp)
    return [
        {
            "id": str(row["generator_id"]),
            "p_mw": clean(row["scenario_p_mw"]),
            "q_mvar": None,
            "dispatch_status": "CAPACITY_CONSTRAINED_MERIT_ORDER_AND_REN_SOURCE_TOTALS",
        }
        for row in allocated.to_dict("records")
    ]


def write_compact(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def snapshot_risks(
    net: pp.pandapowerNet,
    line_features: dict[str, dict[str, Any]],
    transformer_features: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    line_index = int(net.res_line["loading_percent"].idxmax())
    line = net.line.loc[line_index]
    line_result = net.res_line.loc[line_index]
    line_id = str(line["line_id"])
    line_feature = line_features[line_id]
    line_coordinates = line_feature["geometry"]["coordinates"]
    line_center = line_coordinates[len(line_coordinates) // 2]
    line_name = str(line_feature.get("properties", {}).get("name") or line_id)
    if line_id == "LINE:003778":
        line_name = "Belmonte–Sabugal · 0501L5130100"

    transformer_index = int(net.res_trafo["loading_percent"].idxmax())
    transformer = net.trafo.loc[transformer_index]
    transformer_result = net.res_trafo.loc[transformer_index]
    transformer_id = str(transformer["transformer_id"])
    transformer_feature = transformer_features[transformer_id]
    facility_names = []
    for bus_column in ("hv_bus", "lv_bus"):
        name = net.bus.loc[int(transformer[bus_column])].get("facility_name")
        if isinstance(name, str) and name and name not in facility_names:
            facility_names.append(name)
    transformer_name = " / ".join(facility_names) or transformer_id

    return [
        {
            "id": f"risk-line-{line_id}",
            "kind": "LINE",
            "object_id": line_id,
            "title": line_name,
            "center": [float(line_center[0]), float(line_center[1])],
            "radius_km": 10,
            "zoom": 10.2,
            "loading_percent": float(line_result["loading_percent"]),
            "p_mw": max(abs(float(line_result["p_from_mw"])), abs(float(line_result["p_to_mw"]))),
            "voltage_label": f"{float(line['voltage_kv']):g} kV",
            "severity": "OVER_LIMIT" if float(line_result["loading_percent"]) >= 100.0 else "HOTSPOT",
        },
        {
            "id": f"risk-transformer-{transformer_id}",
            "kind": "TRANSFORMER",
            "object_id": transformer_id,
            "title": transformer_name,
            "center": [float(value) for value in transformer_feature["geometry"]["coordinates"]],
            "radius_km": 8,
            "zoom": 10.5,
            "loading_percent": float(transformer_result["loading_percent"]),
            "p_mw": max(abs(float(transformer_result["p_hv_mw"])), abs(float(transformer_result["p_lv_mw"]))),
            "voltage_label": f"{float(transformer['vn_hv_kv']):g}/{float(transformer['vn_lv_kv']):g} kV",
            "severity": "OVER_LIMIT" if float(transformer_result["loading_percent"]) >= 100.0 else "HOTSPOT",
        },
    ]


def model_risk_notices(
    case: dict[str, Any], comparison: dict[str, Any], continuous: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return exactly two concise, non-operational evidence warnings per case."""
    overload_hours = int(continuous["hours_with_line_overload"])
    continuous_max = float(continuous["maximum_line_loading_percent"])
    loss_model = float(comparison["model_rnt_scope_losses_percent_of_load"])
    loss_reference = float(comparison["ren_rnt_monthly_loss_percent"])
    boundary_error = float(comparison["net_import_absolute_error_mw"])
    load_coverage = 100.0 * float(comparison["eredes_profile_fraction_of_national_load"])
    if str(case["case_id"]) == "PT60_2026W_JAN20":
        first = {
            "id": "continuous-winter-stress",
            "severity": "HIGH",
            "title": {
                "en": "Six winter hours exceed the static line screen",
                "pt": "Seis horas de inverno excedem o limite estático",
                "zh": "冬季连续面板有 6 个线路越限小时",
            },
            "detail": {
                "en": f"The daily maximum is {continuous_max:.2f}%. It is a parameter and spatial-allocation sensitivity, not evidence of a real outage or operator overload.",
                "pt": f"O máximo diário é {continuous_max:.2f}%. É uma sensibilidade aos parâmetros e à alocação espacial, não prova de sobrecarga real.",
                "zh": f"日内最高 {continuous_max:.2f}%。这是线路参数和空间分配敏感性，不能解释为现实系统确实过载。",
            },
        }
        second = {
            "id": "winter-voltage-and-validation-gap",
            "severity": "WARNING",
            "title": {"en": "Voltage and boundary results are not operator telemetry", "pt": "Tensão e fronteira não são telemetria do operador", "zh": "电压与边界结果不是调度遥测"},
            "detail": {
                "en": f"The 24 h minimum is {float(continuous['minimum_bus_voltage_pu']):.3f} pu; aggregate exchange differs from REN by {boundary_error:.1f} MW. Tap, reactive-device and independent REE/ENTSO-E evidence is unavailable.",
                "pt": f"O mínimo em 24 h é {float(continuous['minimum_bus_voltage_pu']):.3f} pu; o intercâmbio agregado difere da REN em {boundary_error:.1f} MW. Faltam dados de tomadas, reativos e validação REE/ENTSO-E.",
                "zh": f"24 小时最低电压为 {float(continuous['minimum_bus_voltage_pu']):.3f} pu，聚合交换与 REN 相差 {boundary_error:.1f} MW；缺少分接头、无功设备及 REE/ENTSO-E 独立证据。",
            },
        }
    elif str(case["case_id"]) == "PT60_2026S_MAR23":
        first = {
            "id": "march-partial-node-observation",
            "severity": "WARNING",
            "title": {"en": "Node-level validation remains partial", "pt": "A validação nodal continua parcial", "zh": "节点级验证仍然只是部分同步"},
            "detail": {
                "en": f"Synchronized E-REDES substations cover {load_coverage:.1f}% of national consumption; the remainder uses PDIRT delivery-point weights.",
                "pt": f"As subestações E-REDES sincronizadas cobrem {load_coverage:.1f}% do consumo nacional; o restante usa pesos dos pontos PDIRT.",
                "zh": f"同步 E-REDES 变电站覆盖全国负荷的 {load_coverage:.1f}%，其余部分仍按 PDIRT 交付点权重分配。",
            },
        }
        second = {
            "id": "march-loss-boundary-gap",
            "severity": "WARNING",
            "title": {"en": "Loss and border comparison is aggregate", "pt": "A comparação de perdas e fronteira é agregada", "zh": "网损与边界验证仍为聚合口径"},
            "detail": {
                "en": f"Model RNT-scope loss is {loss_model:.2f}% versus REN monthly {loss_reference:.2f}%; net exchange absolute error is {boundary_error:.1f} MW.",
                "pt": f"A perda modelada no âmbito RNT é {loss_model:.2f}% contra {loss_reference:.2f}% mensal da REN; o erro absoluto de intercâmbio é {boundary_error:.1f} MW.",
                "zh": f"模型 RNT 口径网损为 {loss_model:.2f}%，REN 月度值为 {loss_reference:.2f}%；净交换绝对误差为 {boundary_error:.1f} MW。",
            },
        }
    else:
        first = {
            "id": "september-seasonal-proxy",
            "severity": "HIGH",
            "title": {"en": "September node loads are a prior-year seasonal proxy", "pt": "As cargas nodais de setembro são um proxy sazonal do ano anterior", "zh": "9 月节点负荷采用上一年度季节代理"},
            "detail": {
                "en": f"2026 national totals rescale the 2025-09-02 E-REDES spatial profile. It must not be used as synchronized node validation; E-REDES coverage is {load_coverage:.1f}%.",
                "pt": f"Os totais nacionais de 2026 reescalam o perfil espacial E-REDES de 02-09-2025. Não é validação nodal sincronizada; a cobertura é {load_coverage:.1f}%.",
                "zh": f"2026 年全国总量缩放了 2025-09-02 的 E-REDES 空间分布，不能作为同步节点验证；E-REDES 覆盖率为 {load_coverage:.1f}%。",
            },
        }
        second = {
            "id": "september-proxy-stress",
            "severity": "HIGH",
            "title": {"en": f"{overload_hours} proxy-scenario hours exceed the line screen", "pt": f"{overload_hours} horas do cenário proxy excedem o limite", "zh": f"代理场景有 {overload_hours} 个线路越限小时"},
            "detail": {
                "en": f"The daily maximum is {continuous_max:.2f}%. The affected 60 kV corridors still use engineering ratings, so this is a data-quality flag rather than an operational conclusion.",
                "pt": f"O máximo diário é {continuous_max:.2f}%. Os corredores de 60 kV ainda usam limites de engenharia; é um alerta de qualidade de dados, não uma conclusão operacional.",
                "zh": f"日内最高 {continuous_max:.2f}%。相关 60 kV 走廊仍使用工程额定值，因此这是数据质量提示，而不是运行结论。",
            },
        }
    return [first, second]


def main() -> None:
    comparison_path = OUTPUT / "multi_snapshot_comparison.json"
    if not comparison_path.exists():
        raise FileNotFoundError("Run run_temporal_validation.py before exporting web snapshots")
    comparison = read_json(comparison_path)
    comparison_by_id = {str(row["case_id"]): row for row in comparison["primary_cases"]}
    generators = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    base_summary = read_json(WEB_DATA / "summary.json")
    line_features = {
        str(feature["properties"]["id"]): feature
        for feature in read_json(WEB_DATA / "lines.geojson")["features"]
    }
    transformer_features = {
        str(feature["properties"]["id"]): feature
        for feature in read_json(WEB_DATA / "transformers.geojson")["features"]
    }
    continuous_payload = read_json(OUTPUT / "continuous_24h_summary.json")
    continuous_by_id = {str(row["base_case_id"]): row for row in continuous_payload["summaries"]}
    index_rows: list[dict[str, Any]] = []

    for position, case in enumerate(CASES):
        case_id = str(case["case_id"])
        slug = case_id.lower()
        net = pp.from_json(OUTPUT / f"{case_id}_solved.json")
        comparison_row = comparison_by_id[case_id]
        continuous_row = continuous_by_id[case_id]
        line_overlays = line_records(net)
        bus_overlays = records_by_id(net.bus, net.res_bus, "bus_id", ["vm_pu", "va_degree", "p_mw", "q_mvar"])
        transformer_overlays = records_by_id(
            net.trafo, net.res_trafo, "transformer_id", ["loading_percent", "p_hv_mw", "p_lv_mw", "q_hv_mvar", "q_lv_mvar", "pl_mw"],
        )
        boundary_bus_ids = [str(net.bus.loc[int(bus_index), "bus_id"]) for bus_index in net.ext_grid["bus"]]
        if "type" in net.sgen:
            boundary_bus_ids.extend(
                str(net.bus.loc[int(bus_index), "bus_id"])
                for bus_index in net.sgen.loc[net.sgen["type"].eq("cross_border_aggregate_equivalent"), "bus"]
            )
        overlays = {
            "case_id": case_id,
            "lines": line_overlays,
            "buses": bus_overlays,
            "transformers": transformer_overlays,
            "generators": generator_records(case, generators),
            "boundary_bus_ids": boundary_bus_ids,
        }
        write_compact(SNAPSHOT_DATA / slug / "results.json", overlays)

        is_proxy = str(case["load_profile_mode"]).startswith("SEASON_MATCHED")
        summary = {
            **base_summary,
            "generated_at": utc_now(),
            "case_id": case_id,
            "snapshot_index": position,
            "snapshot_kind": "SEASON_MATCHED_PROXY" if is_proxy else "SYNCHRONIZED_PUBLIC_RECORDS",
            "load_profile_mode": case["load_profile_mode"],
            "new_interconnector_in_service": bool(case["new_interconnector_in_service"]),
            "calibration_timestamp_utc": case["timestamp_utc"],
            "converged": bool(comparison_row["converged"]),
            "buses": int(comparison_row["active_buses"]),
            "lines": int(comparison_row["active_lines"]),
            "vm_pu_min": float(comparison_row["vm_pu_min"]),
            "vm_pu_max": float(comparison_row["vm_pu_max"]),
            "line_loading_percent_max": float(comparison_row["maximum_line_loading_percent"]),
            "trafo_loading_percent_max": float(comparison_row["maximum_transformer_loading_percent"]),
            "overloaded_line_rows": int(comparison_row["lines_over_100_percent"]),
            "overloaded_transformer_rows": int(sum((net.res_trafo["loading_percent"] > 100.0).fillna(False))),
            "total_load_p_mw": float(comparison_row["total_modeled_load_mw"]),
            "total_generation_p_mw": float(comparison_row["total_modeled_generation_mw"]),
            "total_ext_grid_p_mw": float(comparison_row["model_net_import_mw"]),
            "losses_p_mw": float(comparison_row["model_losses_mw"]),
            "unmapped_generation_residual_mw": float(comparison_row["unmapped_generation_residual_mw"]),
            "eredes_profile_fraction_of_national_load": float(comparison_row["eredes_profile_fraction_of_national_load"]),
            "model_rnt_scope_losses_percent_of_load": float(comparison_row["model_rnt_scope_losses_percent_of_load"]),
            "ren_rnt_monthly_loss_percent": float(comparison_row["ren_rnt_monthly_loss_percent"]),
            "net_import_absolute_error_mw": float(comparison_row["net_import_absolute_error_mw"]),
            "continuous_overload_hours": int(continuous_row["hours_with_line_overload"]),
            "continuous_maximum_line_loading_percent": float(continuous_row["maximum_line_loading_percent"]),
            "continuous_minimum_bus_voltage_pu": float(continuous_row["minimum_bus_voltage_pu"]),
            "risk_notices": model_risk_notices(case, comparison_row, continuous_row),
            "risk_items": snapshot_risks(net, line_features, transformer_features),
            "scenario_sweep": base_summary.get("scenario_sweep", []) if position == 0 else [],
            "validation_status": "PASS_WITH_FLAGGED_LIMITATIONS",
        }
        write_compact(SNAPSHOT_DATA / slug / "summary.json", summary)
        index_rows.append({
            "case_id": case_id,
            "slug": slug,
            "timestamp_utc": case["timestamp_utc"],
            "snapshot_kind": summary["snapshot_kind"],
            "new_interconnector_in_service": summary["new_interconnector_in_service"],
        })

    write_compact(SNAPSHOT_DATA / "index.json", {"generated_at": utc_now(), "snapshots": index_rows})
    print(json.dumps({"snapshots": len(index_rows), "output": str(SNAPSHOT_DATA)}, indent=2))


if __name__ == "__main__":
    main()

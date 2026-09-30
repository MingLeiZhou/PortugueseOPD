#!/usr/bin/env python3
"""Apply the audited static corrections of the published CORE-3783 solved model (config/static_model_corrections.csv,
exported from provenance.static_model_corrections) to rebuilt tables, so a table rebuild keeps them.
Entities are matched by stable keys (source_line_id + original length for lines, source_id for generators and
transformers), not by sequential IDs. Runs after add_demand_generation/assign_parameters and before build_pandapower.
Writes outputs/tables/static_model_corrections_applied.csv."""
from __future__ import annotations
import json
import pandas as pd
from common import PROJECT, TABLES

CFG = PROJECT / "config" / "static_model_corrections.csv"
REN_STATION_OF = {"RARI:Estoi": "SET"}   # REN Annex D code of the station a RARI transformer correction refers to


def main() -> None:
    corr = pd.read_csv(CFG)
    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    buses = pd.read_csv(TABLES / "buses.csv", low_memory=False)
    trafos = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    gens = pd.read_csv(TABLES / "generators.csv", low_memory=False)
    key = lines["source_line_id"].astype(str) + "|" + lines["length_km"].round(4).astype(str)
    log = []
    for r in corr.itertuples():
        after = json.loads(r.after_json); status = "NOT_FOUND"; target = ""
        if r.entity_type in ("line", "line_electrical_length", "line_topology", "station_busbar"):
            idx = lines.index[key == f"{r.source_line_id}|{round(float(r.key_length_km), 4)}"]
            if len(idx) == 1:
                i = idx[0]; target = lines.at[i, "line_id"]
                if r.entity_type == "line":
                    lines.at[i, "max_i_ka"] = after["max_i_ka"]; lines.at[i, "parallel"] = after["parallel"]
                    lines.at[i, "parameter_status"] = str(lines.at[i, "parameter_status"]) + "+STATIC_CORRECTION:" + r.evidence_status
                elif r.entity_type == "line_electrical_length":
                    lines.at[i, "length_km"] = after["length_km"]
                elif r.entity_type == "line_topology":
                    for end in ("from_bus", "to_bus"):
                        new = after[end]
                        if new not in set(buses["bus_id"]):
                            base = buses[buses["bus_id"] == new.split(":PI_")[0]].iloc[0].copy()
                            base["bus_id"] = new; base["source_status"] = "STATIC_CORRECTION_SPLIT_BUS:" + r.evidence_status
                            buses = pd.concat([buses, base.to_frame().T], ignore_index=True)
                        lines.at[i, end] = new
                else:
                    lines.at[i, "asset_type"] = after["asset_type"]; lines.at[i, "parameter_status"] = after["parameter_status"]
                status = "APPLIED"
        elif r.entity_type == "generator":
            idx = gens.index[gens["source_id"] == r.generator_source_id]
            for i in idx:
                for k, v in after.items(): gens.at[i, k] = v
                target = gens.at[i, "generator_id"]; status = "APPLIED"
        elif r.entity_type == "transformer":
            code = REN_STATION_OF.get(r.transformer_source_id)
            idx = trafos.index[trafos["source_id"].astype(str).str.startswith(f"REN:{code}:")] if code else []
            if len(idx) == 0:
                idx = trafos.index[trafos["source_id"] == r.transformer_source_id]
            for i in idx:   # REN Annex D gives the unit ratings; only the evidence-backed 60 kV connection point is carried over
                trafos.at[i, "lv_bus"] = after["lv_bus"]; target += trafos.at[i, "transformer_id"] + ";"; status = "APPLIED_LV_BUS"
        log.append({"entity_type": r.entity_type, "old_entity_id": r.entity_id, "published_name": r.published_name, "field": r.field,
                    "new_entity_id": target, "status": status, "evidence_status": r.evidence_status})
    lines.to_csv(TABLES / "lines.csv", index=False); buses.to_csv(TABLES / "buses.csv", index=False)
    trafos.to_csv(TABLES / "transformers_topology.csv", index=False); gens.to_csv(TABLES / "generators.csv", index=False)
    out = pd.DataFrame(log); out.to_csv(TABLES / "static_model_corrections_applied.csv", index=False)
    print(out["status"].value_counts().to_dict())


if __name__ == "__main__":
    main()

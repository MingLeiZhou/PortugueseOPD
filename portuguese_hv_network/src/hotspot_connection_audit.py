"""Source-supported connection corrections; no calibration against model errors.

APA AIA 3403 p. 5 and E-REDES RARI 2024 p. 56 identify the
11.4 km Trancoso wind export corridor. APA PPA 421 identifies Sernancelhe's
60 kV connection to Moimenta. Moimenta is represented at 400 kV in PT60:
the latter correction is an explicit collector equivalent, not a reconstructed
60/400 kV plant network. Original coordinates and commissioned dates remain.
"""
from __future__ import annotations

import pandas as pd

APA_TRANCOSO = "https://siaia.apambiente.pt/AIADOC/AIA3403/parecerca_3403202192134944.pdf"
APA_SERNANCELHE = "https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/421"
RARI = "https://www.e-redes.pt/sites/eredes/files/2025-04/E-REDES_Artigo18_RARI2024_Caracterizacao_Redes_Distribuicao_MT_AT_a_31dez2024.pdf"


def correct_connections(generators, net):
    """Return copied asset table, copied net, and an auditable assignment ledger.

    Match source identity rather than mutable generated row numbers. Keep the
    original voltage-control groups; remove relocated power from their original
    bus aggregate and represent it as a PQ injection at the new connection.
    """
    from copy import deepcopy
    import pandapower as pp

    g, n, rows = generators.copy(), deepcopy(net), []
    candidates = n.line[n.line.source_line_id.astype(str).str.startswith("EREDES:252337344:")]
    if len(candidates) != 1:
        raise ValueError("Trancoso published export corridor must resolve uniquely")
    ends = [int(candidates.iloc[0][s]) for s in ("from_bus", "to_bus")]
    target = [i for i in ends if n.bus.loc[i, "bus_id"] != "BUS:EREDES:0913P5021500:60"]
    if len(target) != 1:
        raise ValueError("Trancoso export corridor no longer terminates at the documented PC")
    mapping = [
        (g.source_id.astype(str).str.startswith("DGEG:CE:795:TRANCOSO (LAGAR/PINGULINHA):"),
         str(n.bus.loc[target[0], "bus_id"]), APA_TRANCOSO, "APA_EXPORT_LINE_AND_EREDES_ROUTE_MATCH"),
        (g.source_id.astype(str).str.startswith("DGEG:CE:1135:SERNANCELHE:"),
         "BUS:OSM:way:542145772:400", APA_SERNANCELHE, "APA_NAMED_COLLECTOR_400KV_EQUIVALENT"),
    ]
    lookup = {str(r.bus_id): int(i) for i, r in n.bus.iterrows()}
    for mask, bid, url, status in mapping:
        if not mask.any() or bid not in lookup:
            raise ValueError(f"Missing source asset or destination: {bid}")
        for i, asset in g[mask].iterrows():
            old = str(asset.bus_id)
            g.loc[i, "bus_id"] = bid
            g.loc[i, "bus_voltage_kv"] = n.bus.loc[lookup[bid], "vn_kv"]
            g.loc[i, "bus_assignment_rule"] = status
            g.loc[i, "connection_evidence_url"] = url
            g.loc[i, "original_bus_id"] = old
            # Preserve the original spatial-transfer distance separately. It
            # measured asset-to-OSM-anchor, not asset-to-corrected-bus distance.
            g.loc[i, "original_match_distance_m"] = asset.get("match_distance_m")
            g.loc[i, "match_distance_m"] = float("nan")
            g.loc[i, "connection_evidence_status"] = status
            rows.append(dict(generator_id=asset.generator_id, source_id=asset.source_id,
                             name=asset["name"], nameplate_mw=asset.nameplate_mw,
                             original_bus_id=old, corrected_bus_id=bid,
                             evidence_status=status, source_url=url))
            matches = n.sgen.index[n.sgen.name.eq(asset.generator_id)]
            if len(matches):
                n.sgen.loc[matches, "bus"] = lookup[bid]
                n.sgen.loc[matches, "in_service"] = True
            else:
                pp.create_sgen(n, lookup[bid], p_mw=0, q_mvar=0, name=asset.generator_id,
                               type="source_supported_connection")
    # The original bus-level PV aggregate remains for assets still at that bus.
    # At a destination with an existing PV aggregate, avoid duplicate injection.
    pv_buses = set(n.gen.loc[n.gen.in_service, "bus"].astype(int))
    corrected_ids = {r["generator_id"] for r in rows}
    for i, row in n.sgen[n.sgen.name.isin(corrected_ids)].iterrows():
        if int(row.bus) in pv_buses:
            n.sgen.loc[i, "in_service"] = False
    return g, n, pd.DataFrame(rows)


def isolate_6202_intermediate_connections(net, lines):
    """Sensitivity only: preserve the named circuit's endpoints, isolate its
    interior GIS junctions from other circuits and the nearby Reboleira bus.
    No evidence of actual switch position is asserted by this experiment.
    """
    from copy import deepcopy
    import pandapower as pp
    n = deepcopy(net)
    ids = set(lines.loc[lines["name"].eq("1106L5620200"), "line_id"])
    selected = n.line.line_id.isin(ids)
    endpoints = {"BUS:EREDES:1106S5183100:60", "BUS:EREDES:1115S5191000:60"}
    indices = set(n.line.loc[selected, "from_bus"].astype(int)) | set(n.line.loc[selected, "to_bus"].astype(int))
    for old in sorted(indices):
        if str(n.bus.loc[old, "bus_id"]) in endpoints:
            continue
        new = pp.create_bus(n, vn_kv=60, name=f"6202 isolated continuation {old}")
        n.bus.loc[new, "bus_id"] = f"BUS:SENSITIVITY:6202:{old}"
        for col in ("from_bus", "to_bus"):
            n.line.loc[selected & n.line[col].eq(old), col] = new
    return n

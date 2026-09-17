"""Rebuild scenario PV/PQ elements from mapped, available assets.

These are declared research controls, not observed unit reactive capabilities.
The threshold applies to non-battery capacity aggregated at a bus.
"""
import pandas as pd
import pandapower as pp


def rebuild_controls(net, generators, timestamp):
    from run_temporal_validation import available_asset_mask
    lookup = {str(r.bus_id): int(i) for i, r in net.bus.iterrows() if r.in_service}
    available = available_asset_mask(generators, pd.Timestamp(timestamp))
    assets = generators[available & generators.bus_id.isin(lookup)].copy()
    assets["nameplate_mw"] = pd.to_numeric(assets.nameplate_mw, errors="raise").fillna(0)
    assets = assets[assets.nameplate_mw.gt(0)]
    net.gen.drop(net.gen.index, inplace=True)
    net.sgen.drop(net.sgen.index, inplace=True)
    controls = []
    for bid, group in assets.groupby("bus_id"):
        nonbattery = group[~group.generation_source.eq("battery")]
        capacity = float(nonbattery.nameplate_mw.sum())
        pv = capacity >= 20 and float(net.bus.loc[lookup[bid], "vn_kv"]) >= 60
        if pv:
            i = pp.create_gen(net, lookup[bid], p_mw=0, vm_pu=1.0,
                              min_q_mvar=-.5*capacity, max_q_mvar=.5*capacity,
                              name=f"PV_GROUP:{bid}")
            net.gen.loc[i, "nameplate_mw"] = capacity
            net.gen.loc[i, "source_status"] = "AVAILABLE_MAPPED_ASSETS_PROXY_Q_LIMITS"
        pq = group[group.generation_source.eq("battery")] if pv else group
        for row in pq.itertuples():
            pp.create_sgen(net, lookup[bid], p_mw=0, q_mvar=0, name=row.generator_id)
        controls.append(dict(bus_id=bid, available_nonbattery_capacity_mw=capacity,
                             pv=bool(pv), q_limit_mvar=.5*capacity if pv else 0,
                             pq_assets=len(pq), timestamp_utc=str(timestamp)))
    return pd.DataFrame(controls)

"""Synthetic tests isolate moved capacity, commissioning, and mixed bus assets."""
import os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
import unittest
import pandas as pd
import pandapower as pp
from rebuild_snapshot_controls import rebuild_controls
from run_temporal_validation import apply_generation_inputs


class SnapshotControlTests(unittest.TestCase):
    def test_moved_asset_removes_old_q_and_future_unit_has_no_q(self):
        n=pp.create_empty_network()
        for bid in ("old", "new", "BUS:OSM:way:131715746:400"):
            i=pp.create_bus(n,60);n.bus.loc[i,"bus_id"]=bid
        pp.create_gen(n,0,0,max_q_mvar=99,min_q_mvar=-99)
        g=pd.DataFrame([
            dict(generator_id="wind",bus_id="new",nameplate_mw=28,generation_source="wind",available_from_utc="2020-01-01"),
            dict(generator_id="future",bus_id="old",nameplate_mw=100,generation_source="wind",available_from_utc="2027-01-01"),
            dict(generator_id="small",bus_id="old",nameplate_mw=6,generation_source="wind",available_from_utc=None),
            dict(generator_id="battery",bus_id="new",nameplate_mw=10,generation_source="battery",available_from_utc=None)])
        t=pd.Timestamp("2026-01-01",tz="UTC")
        rebuild_controls(n,g,t)
        self.assertEqual(len(n.gen),1)
        self.assertEqual(int(n.gen.iloc[0].bus),1)
        self.assertEqual(float(n.gen.iloc[0].max_q_mvar),14)
        self.assertNotIn("future",set(n.sgen.name))
        obs={"generation_by_source_mw":{"Wind":17,"Battery Injection":2}}
        apply_generation_inputs(n,g,obs,t)
        self.assertAlmostEqual(float(n.gen.p_mw.sum()+n.sgen.p_mw.sum()),19)
        first=n.gen.copy(deep=True)
        rebuild_controls(n,g,t)
        apply_generation_inputs(n,g,obs,t)
        pd.testing.assert_frame_equal(first,n.gen)

if __name__=="__main__":unittest.main()

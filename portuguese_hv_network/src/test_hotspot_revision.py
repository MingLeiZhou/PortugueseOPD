"""Regression checks for source identity, capacity and actual injected power."""
import os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
import unittest
import pandas as pd
import pandapower as pp
from hotspot_connection_audit import correct_connections, isolate_6202_intermediate_connections
from run_temporal_validation import MODEL_INPUT, GENERATOR_INPUT, PROJECT, ren_observation, apply_generation_inputs


class HotspotRevisionTests(unittest.TestCase):
    def test_source_mapping_and_active_injection_conservation(self):
        g=pd.read_csv(GENERATOR_INPUT,low_memory=False);n=pp.from_json(MODEL_INPUT)
        original=g.copy(deep=True)
        revised,net,audit=correct_connections(g,n)
        pd.testing.assert_frame_equal(g,original)
        self.assertEqual(len(audit),4)
        self.assertAlmostEqual(g.nameplate_mw.sum(),revised.nameplate_mw.sum())
        self.assertEqual((revised.bus_id.fillna('').ne('')).sum(),(g.bus_id.fillna('').ne('')).sum()+1)
        timestamp=pd.Timestamp('2026-01-21T17:15:00Z')
        obs=ren_observation(timestamp,False)
        summary,allocated,_=apply_generation_inputs(net,revised,obs,timestamp)
        total=sum((t.loc[t.in_service,'p_mw']*t.loc[t.in_service,'scaling']).sum() for t in (net.gen,net.sgen))
        self.assertAlmostEqual(total,obs['generation_total_mw'],places=6)
        self.assertTrue((allocated.scenario_p_mw <= allocated.nameplate_mw.fillna(0)+1e-8).all())
        apply_generation_inputs(n,g,obs,timestamp,legacy_bus_aggregation=True)
        legacy=sum((t.loc[t.in_service,'p_mw']*t.loc[t.in_service,'scaling']).sum() for t in (n.gen,n.sgen))
        self.assertGreater(legacy-obs['generation_total_mw'],1.0)

    def test_circuit_sensitivity_keeps_length_and_circuit_count(self):
        n=pp.from_json(MODEL_INPUT);lines=pd.read_csv(PROJECT/'outputs/tables/lines.csv')
        changed=isolate_6202_intermediate_connections(n,lines)
        self.assertEqual(len(changed.line),len(n.line))
        pd.testing.assert_series_equal(changed.line.parallel,n.line.parallel)
        pd.testing.assert_series_equal(changed.line.length_km,n.line.length_km)
        self.assertGreater(len(changed.bus),len(n.bus))

if __name__=='__main__':unittest.main()

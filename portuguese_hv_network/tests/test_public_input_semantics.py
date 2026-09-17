import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from run_temporal_validation import aggregate_facility_loads, allocate_generation
from pt60_public_model import validate_public_input

class InputSemanticsTests(unittest.TestCase):
    def test_missing_zero_and_partial_remain_distinct(self):
        frame=pd.DataFrame({'codigo_subestacao':['a','b','c','c'], 'subestacao':['A','B','C','C'], 'energia':[None,0,250,None]})
        result=aggregate_facility_loads(frame).set_index('codigo_subestacao')
        self.assertTrue(pd.isna(result.loc['a','raw_p_mw']))
        self.assertEqual(result.loc['a','observation_status'],'MISSING')
        self.assertEqual(result.loc['b','observation_status'],'OBSERVED')
        self.assertEqual(result.loc['b','raw_p_mw'],0)
        self.assertEqual(result.loc['c','observation_status'],'PARTIAL_OBSERVATION')
        self.assertEqual(result.raw_p_mw.sum(),1)
        self.assertEqual(result.missing_observation_count.sum(),2)
    def test_public_missing_requires_explicit_status(self):
        spec={'schema_version':'1.0','case_id':'test','timestamp_utc':'2026-01-20T19:45:00Z','sources':{'national_balance':'test','substation_load':'test'},'national':dict(consumption_mw=10,consumption_plus_storage_mw=10,pumping_mw=0,battery_consumption_mw=0,import_mw=0,export_mw=0,rnt_monthly_loss_percent=0,generation_by_source_mw={})}
        loads=pd.DataFrame({'facility_code':['a'],'substation_name':['A'],'p_mw':[float('nan')]})
        with self.assertRaises(ValueError): validate_public_input(spec,loads)
        loads['observation_status']='MISSING'
        validate_public_input(spec,loads)
        loads['p_mw']=0
        with self.assertRaises(ValueError): validate_public_input(spec,loads)
        loads['observation_status']='OBSERVED'
        validate_public_input(spec,loads)

    def test_fixed_and_seed_dispatch_have_different_meanings(self):
        assets=pd.DataFrame(dict(generator_id=['a','b'], bus_id=['A','B'], generation_source=['solar','solar'], nameplate_mw=[100.,100.], available_from_utc=[None,None]))
        supplied=pd.DataFrame(dict(generator_id=['a'],p_mw=[40.]))
        fixed,residual=allocate_generation(assets,{'Solar':100},pd.Timestamp('2026-01-20',tz='UTC'),supplied)
        self.assertEqual(fixed.scenario_p_mw.tolist(),[40.,60.])
        supplied['dispatch_mode']='SEED'
        seeded,_=allocate_generation(assets,{'Solar':100},pd.Timestamp('2026-01-20',tz='UTC'),supplied)
        self.assertAlmostEqual(seeded.scenario_p_mw.iloc[0],62.5)
        self.assertAlmostEqual(seeded.scenario_p_mw.sum(),100.)
        supplied['p_mw']=float('nan')
        with self.assertRaises(ValueError): allocate_generation(assets,{'Solar':100},pd.Timestamp('2026-01-20',tz='UTC'),supplied)
    def test_fixed_capacity_is_not_used_to_absorb_residual(self):
        assets=pd.DataFrame(dict(generator_id=['a','b'], bus_id=['A','B'], generation_source=['solar','solar'], nameplate_mw=[100.,10.], available_from_utc=[None,None]))
        out,residual=allocate_generation(assets,{'Solar':100},pd.Timestamp('2026-01-20',tz='UTC'),pd.DataFrame(dict(generator_id=['a'],p_mw=[40.])))
        self.assertEqual(out.scenario_p_mw.tolist(),[40.,10.])
        self.assertEqual(next(r['unmapped_residual_mw'] for r in residual if r['generation_source']=='Solar'),50.)

if __name__=='__main__': unittest.main()

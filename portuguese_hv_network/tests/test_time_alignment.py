import json
from pathlib import Path
import sys
import unittest
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from time_alignment import ren_source_index,eredes_source_label,source_time_metadata
D=Path(__file__).resolve().parents[1]/'data/evidence/time_alignment'

class SourceClockTests(unittest.TestCase):
    def test_official_dst_day_sequences_and_repeated_hour(self):
        spring=json.loads((D/'ren_dispatch_2025-03-30.json').read_text())
        self.assertEqual(ren_source_index(spring,'2025-03-30T01:00:00Z')[1],4)
        autumn=json.loads((D/'ren_dispatch_2025-10-26.json').read_text())
        self.assertEqual(ren_source_index(autumn,'2025-10-26T00:15:00Z')[1],5)
        self.assertEqual(ren_source_index(autumn,'2025-10-26T01:15:00Z')[1],9)
    def test_interval_end_and_day_rollover(self):
        self.assertEqual(eredes_source_label('2025-07-07T23:45:00Z'),('2025-07-08','01:00'))
        self.assertEqual(eredes_source_label('2026-01-20T19:45:00Z'),('2026-01-20','20:00'))
        self.assertEqual(source_time_metadata('2025-07-07T00:15:00Z')['ren_source_timestamp_local'],'2025-07-07T01:15:00+01:00')
    def test_reject_ambiguous_eredes_clock(self):
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            eredes_source_label('2025-10-26T00:00:00Z')
    def test_blackout_transition_interval(self):
        ren=json.loads((D/'ren_dispatch_2025-04-28.json').read_text())
        date,index=ren_source_index(ren,'2025-04-28T10:30:00Z')
        self.assertEqual(ren['xAxis']['categories'][index],'11:30')
        self.assertEqual(eredes_source_label('2025-04-28T10:30:00Z'),('2025-04-28','11:45'))
        loads=next(x['data'] for x in ren['series'] if x['name']=='Consumption')
        self.assertLess(loads[index],.3*loads[index-1])
        eredes=json.loads((D/'eredes_time_profile_2025-04-28.json').read_text())['results']
        e={x['datahora'][11:16]:x['energy_kwh'] for x in eredes}
        self.assertLess(e['11:45'],.3*e['11:30'])

if __name__=='__main__':unittest.main()

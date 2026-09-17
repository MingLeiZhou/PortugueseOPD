"""Compile corrected UTC intervals from frozen E-REDES bulk exports and REN days."""
from pathlib import Path
import json
import pandas as pd
from common import PROJECT,write_json,sha256
from time_alignment import eredes_source_label,source_time_metadata
from run_seasonal_week_panel import hourly_cases
from run_temporal_validation import RAW_EREDES,ren_observation,ren_rnt_loss_benchmark
from run_review_revision import INPUTS

def main():
    source=PROJECT/'data/raw/eredes/aligned_validation'
    cases=hourly_cases();INPUTS.mkdir(parents=True,exist_ok=True)
    records=[];files=[]
    for path in sorted(source.glob('*.json')):
        if path.name.endswith('.provenance.json'):continue
        rows=json.loads(path.read_text());part=path.stem.split('_',1)[1]
        for row in rows:row['dataset_partition']='diagrama_carga_subestacao_'+part
        records.extend(rows);files.append({'path':str(path.relative_to(PROJECT.parent)),'sha256':sha256(path)})
    data=pd.DataFrame(records)
    groups={key:group for key,group in data.groupby(['data','hora'],sort=False)}
    for case in cases:
        existing=INPUTS/f"{case['case_id']}.json"
        if existing.exists():
            assert json.loads(existing.read_text())['case']['time_alignment_version']=='LISBON_INTERVAL_START_END_V2'
            continue
        stamp=pd.Timestamp(case['timestamp_utc']);label=eredes_source_label(stamp)
        snapshot=groups[label].copy();assert not snapshot.codigo_subestacao.duplicated().any()
        metadata={**source_time_metadata(stamp),'source':'E-REDES Open Data substation load diagram','timestamp_utc':stamp.isoformat(),'interval_minutes':15,'conversion':'p_mw = energia_kwh / 250','record_count':len(snapshot),'partition_counts':snapshot.dataset_partition.value_counts().to_dict()}
        cache=RAW_EREDES/'aligned_v2'/f"load_{stamp.strftime('%Y-%m-%d_%H%M')}.json"
        rows=json.loads(snapshot.to_json(orient='records'));write_json(cache,{'metadata':metadata,'records':rows})
        case.update(source_time_metadata(stamp))
        output=INPUTS/f"{case['case_id']}.json"
        if output.exists():raise FileExistsError(f'Refusing to overwrite existing input: {output}')
        write_json(output,{'case':case,'observation':ren_observation(stamp,False),'snapshot':rows,'load_metadata':metadata,'rnt_loss':ren_rnt_loss_benchmark(case['rnt_loss_benchmark_date'],False)})
    write_json(INPUTS.parent/'time_alignment_sources.json',{'source_files':files,'cases':len(cases),'rule':source_time_metadata(pd.Timestamp(cases[0]['timestamp_utc']))})
    print('Prepared',len(cases),'aligned input bundles')
if __name__=='__main__':main()

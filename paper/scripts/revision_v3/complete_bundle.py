from pathlib import Path
import json,hashlib,shutil,duckdb
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'data/releases/SimPT60-2026.09.21-r1/bundle'
shutil.copy2(ROOT/'paper/scripts/revision_v3/replay_release.py',P/'replay.py')
cases=[json.loads(f.read_text()) for f in sorted((ROOT/'portuguese_hv_network/outputs/validation_experiments/raw_cases/BASELINE').glob('*.json'))];(P/'replay_cases.json').write_text(json.dumps(cases,indent=2))
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(2**23),b''):h.update(b)
 return h.hexdigest()
c=duckdb.connect(str(P/'inputs.duckdb'),read_only=True);rows=[]
for src,rel,expected in c.sql('select original_local_path,archive_relative_path,sha256 from provenance.raw_files').fetchall():
 path=Path(src);actual=sha(path)
 if actual!=expected:raise ValueError(f'Source changed: {rel} {expected} != {actual}')
 dst=P/'source_archive'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
 if not dst.exists():shutil.copy2(path,dst)
 rows.append({'file':'source_archive/'+rel,'sha256':actual,'bytes':dst.stat().st_size})
(P/'source_archive_manifest.json').write_text(json.dumps(rows,indent=2));print('Archived and verified',len(rows),'raw sources',flush=True)

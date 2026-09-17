"""Stage exactly the four current deliverables; never modify the frozen dataset."""
from pathlib import Path
import hashlib, json, re, shutil, zipfile
ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/'portuguese_hv_network/site'
OUT=SITE/'public/downloads'
PYTHON_VERSION='0.4.3'
OUT.mkdir(parents=True,exist_ok=True)
archive=ROOT/'data/releases/PT60-v2.1.0-rc2.tar.gz'
expected='07727842080141300c4ddf180f82a4e7f9f66e3a444a30297d650f79a3486913'
def digest(p):
 with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
assert digest(archive)==expected, 'Frozen archive changed'
parts=[]
with archive.open('rb') as source:
 i=0
 while block:=source.read(4*1024*1024):
  name=f'PT60-v2.1.0-rc2.tar.gz.part{i:02d}'
  (OUT/name).write_bytes(block)
  parts.append({'path':'/downloads/'+name,'bytes':len(block),'sha256':hashlib.sha256(block).hexdigest()});i+=1
# Include only the current manuscript and its recursively linked local supporting files.
main=ROOT/'paper/PT60_SCIENTIFIC_DATA_CN_READER_FIRST_DRAFT.md'
queue=[main]; selected=set()
while queue:
 p=queue.pop().resolve()
 if p in selected: continue
 if not p.is_relative_to(ROOT/'paper') or not p.is_file(): raise ValueError(f'Invalid paper dependency: {p}')
 selected.add(p)
 if p.suffix=='.md':
  for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
   target=target.split('#')[0].strip('<>')
   if not target or '://' in target: continue
   child=(p.parent/target).resolve()
   if child.is_file(): queue.append(child)
   else: raise ValueError(f'Missing paper link {target} in {p}')
with zipfile.ZipFile(OUT/'PT60-paper-cn.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(selected): z.write(p,p.relative_to(ROOT))
 z.writestr('README.txt','PT60 Chinese manuscript. Open paper/PT60_SCIENTIFIC_DATA_CN_READER_FIRST_DRAFT.md.\nThis is a manuscript, not a published paper. Figures and linked supplements are included.\nFrozen reproduction scripts and their source data are in the separate PT60 dataset.\n')
for name in [f'pt60_tools-{PYTHON_VERSION}-py3-none-any.whl',f'pt60_tools-{PYTHON_VERSION}.tar.gz']:
 assert (ROOT/f'output/pt60-tools-{PYTHON_VERSION}'/name).is_file(), 'Build the Python distributions first'
for p in (ROOT/f'output/pt60-tools-{PYTHON_VERSION}').glob('*'):
 if p.suffix=='.whl' or p.name.endswith('.tar.gz'): shutil.copy2(p,OUT/p.name)
for name in ['ATTRIBUTION.md','manifest.json','checksums.sha256']:
 shutil.copy2(ROOT/'data/releases/PT60-v2.1.0-rc2'/name,OUT/name)
shutil.copy2(ROOT/'docs/PT60_TOOLS_GUIDE_CN.md',OUT/'usage-cn.md')
manifest={'dataset':{'version':'PT60-v2.1.0-rc2','url':'/download','filename':archive.name,'bytes':archive.stat().st_size,'sha256':expected,'parts':parts},'python':{'name':'pt60-tools','version':PYTHON_VERSION,'pypi_status':'published','wheel':f'/downloads/pt60_tools-{PYTHON_VERSION}-py3-none-any.whl'},'paper':{'status':'Chinese manuscript','url':'/downloads/PT60-paper-cn.zip','files':len(selected)},'website':'https://grid.jczw.xyz/project'}
(SITE/'download-manifest.js').write_text('export default '+json.dumps(manifest['dataset'])+';\n')
(OUT/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
# Server-side import is generated from the same checked manifest as the downloads.
(SITE/'app/release-info.json').write_text(json.dumps(manifest,indent=2)+'\n')
checks={p.name:digest(p) for p in OUT.iterdir() if p.is_file() and p.name!='SHA256SUMS'}
(OUT/'SHA256SUMS').write_text(''.join(f'{v}  {k}\n' for k,v in sorted(checks.items())))
print(json.dumps({'parts':len(parts),'paper_files':len(selected),'archive_sha256':expected,'downloads':len(checks)},indent=2))

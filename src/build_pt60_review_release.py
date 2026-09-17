#!/usr/bin/env python3
"""Freeze the reviewed experiment and its offline reconstruction dependencies."""
from pathlib import Path
import csv,hashlib,json,shutil,tarfile,re
from urllib.parse import unquote
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'portuguese_hv_network'
D=P/'outputs/temporal_validation/quality_revision'
R=ROOT/'data/releases/PT60-v2.1.0-rc2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def copy(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
def copy_public_generators(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True)
 with src.open(newline='') as source:
  reader=csv.DictReader(source);fields=reader.fieldnames;rows=list(reader)
 for row in rows:
  if row.get('original_bus_id','').strip().lower() in {'nan','none','null'}:row['original_bus_id']=''
 with dst.open('w',newline='') as target:
  writer=csv.DictWriter(target,fieldnames=fields);writer.writeheader();writer.writerows(rows)
def copy_filtered_csv(src,dst,column,allowed_values):
 dst.parent.mkdir(parents=True,exist_ok=True)
 with src.open(newline='') as source:
  reader=csv.DictReader(source);fields=reader.fieldnames
  rows=[row for row in reader if row.get(column) in allowed_values]
 with dst.open('w',newline='') as target:
  writer=csv.DictWriter(target,fieldnames=fields);writer.writeheader();writer.writerows(rows)
def build():
 if R.exists():shutil.rmtree(R)
 manifest=json.loads((D/'experiment_manifest.json').read_text())
 for item in manifest['sources']:
  source=ROOT/item['path']
  if sha(source)!=item['sha256']:raise RuntimeError(f'Changed dependency: {source}')
  copy(source,R/'reproduction'/item['path'])
 for p in D.glob('*.csv'):
  if p.name not in {'revision_results.csv','generators_revised.csv','installed_capacity_comparison.csv','historical_capacity_coverage.csv'}:copy(p,R/'validation'/p.name)
  if p.name != 'historical_capacity_coverage.csv':
   copy(p,R/'reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision'/p.name)
 copy(ROOT/'paper/figures/pt60_review/installed_capacity_comparison.csv',R/'validation/installed_capacity_comparison.csv')
 copy_filtered_csv(D/'historical_capacity_coverage.csv',R/'validation/historical_capacity_coverage.csv','timestamp_utc',{'2025-07-07','2026-01-20'})
 # The public diagnostic table contains only the 16 non-primary sensitivity cases.
 with (D/'revision_results.csv').open(newline='') as handle:
  reader=csv.DictReader(handle);fields=reader.fieldnames
  diagnostics=[row for row in reader if row.get('variant') in {'DEDUP_ONLY','FROZEN_Q','NO_CAPACITY_UPLIFT','NO_LOAD_COMPENSATION','NO_REACTORS','X_MINUS_10_PERCENT','X_PLUS_10_PERCENT','UNKNOWN_DATES_EXCLUDED'} and row.get('timestamp_utc') in {'2025-07-07T14:15:00+00:00','2026-01-21T17:15:00+00:00'}]
 if len(diagnostics)!=16:raise RuntimeError(f'Expected 16 diagnostic rows, found {len(diagnostics)}')
 diagnostic_path=R/'validation/diagnostic_results.csv';diagnostic_path.parent.mkdir(parents=True,exist_ok=True)
 with diagnostic_path.open('w',newline='') as handle:
  writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(diagnostics)
 inputs=sorted((D/'inputs').glob('*.json'))
 if len(inputs)!=336:raise RuntimeError(f'Expected 336 archived hourly inputs, found {len(inputs)}')
 for p in inputs:copy(p,R/'validation/inputs'/p.name)
 for name in ['model_revised.json','generators_revised.csv']:
  copy(D/name,R/'reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision'/name)
 for p in D.glob('*.json'):
  if p.name not in {'model_revised.json','clean_replay_validation.json'}:copy(p,R/'validation'/p.name)
 for name in ['installed_capacity_2025-07.json','installed_capacity_2026-01.json']:
  copy(P/'data/raw/ren'/name,R/'provenance'/name)
 for p in (D/'baseline').glob('*'):
  if p.is_file():
   copy(p,R/'model'/p.name)
   if p.name in {'loads.csv','summary.json'}:copy(p,R/'reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision/baseline'/p.name)
 january_input_path=R/'model/january_input.json'
 january_input=json.loads(january_input_path.read_text())
 january_input['case']['analysis_role']='REUSE_EXAMPLE_WITHIN_WINTER_WEEK'
 january_input_path.write_text(json.dumps(january_input,indent=2)+'\n')
 january_summary_path=R/'model/summary.json'
 january_summary=json.loads(january_summary_path.read_text())
 january_summary['analysis_role']='REUSE_EXAMPLE_WITHIN_WINTER_WEEK'
 january_summary_path.write_text(json.dumps(january_summary,indent=2)+'\n')
 copy(D/'model_revised.json',R/'model/model_template.json')
 copy_public_generators(D/'generators_revised.csv',R/'scenario/generators.csv')
 for source in (ROOT/'data/releases/PT60-v2.0.0/topology').glob('*'):
  if source.is_file():copy(source,R/'topology'/source.name)
 copy(D/'baseline/loads.csv',R/'scenario/loads.csv')
 copy(ROOT/'data/releases/PT60-v2.0.0/scenario/boundaries.csv',R/'scenario/boundaries.csv')
 for source in (P/'config').glob('*.json'):copy(source,R/'config'/source.name)
 copy(P/'PUBLIC_MODEL_INTERFACE.md',R/'PUBLIC_MODEL_INTERFACE.md')
 p=D/'static_control_reference.csv'
 copy(p,R/'reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision/static_control_reference.csv')
 copy(p,R/'validation/static_control_reference_week.csv')
 for name in ['PT60_SCIENTIFIC_DATA_CN_READER_FIRST_DRAFT.md','PT60_DATA_DOCUMENTATION_CN.md','PT60_VALIDATION_SUPPLEMENT_CN.md','PT60_TIME_ALIGNMENT_CN.md','PT60_PRIORITY_EVIDENCE_AUDIT_CN.md','PT60_SPATIAL_EXPERIMENT_DESIGN_CN.md','PT60_FIGURE_REVISION_QA_CN.md']:
  copy(ROOT/'paper'/name,R/'paper'/name)
 figure_bases={
  'fig01_dataset_overview','fig02_workflow_scheme_b','fig03_demand_allocation',
  'fig04_installed_capacity_comparison','fig05_weekly_ac_diagnostic','fig06_weekly_boundary_diagnostic',
 }
 figure_aux={
  'figure1_mapped_nameplate.csv','figure3_weekly_coverage.csv','installed_capacity_comparison.csv',
  'portugal_gisco_2024.geojson','reader_consistency_audit.json','source_data.csv','source_manifest.json','quality_revision_render_qa.json',
 }
 for p in (ROOT/'paper/figures/pt60_review').glob('*'):
  if p.is_file() and (p.name in figure_aux or any(p.name.startswith(f'{base}.') for base in figure_bases)):
   copy(p,R/'paper/figures/pt60_review'/p.name)
 for p in (ROOT/'paper/examples/public_case').glob('*'):copy(p,R/'examples/public_case'/p.name)
 (R/'examples/public_case/provenance.json').write_text(json.dumps({
  'source':'model/january_input.json',
  'sha256':sha(january_input_path),
  'conversion':'p_mw = energia_kwh / 250; no observation values changed',
 },indent=2)+'\n')
 copy(P/'data/evidence/time_alignment/download_manifest.json',R/'provenance/time_alignment_manifest.json')
 copy(P/'data/evidence/priority_audit/download_manifest.json',R/'provenance/priority_evidence_manifest.json')
 copy(P/'outputs/tables/pdird_parameter_path_ledger.csv',R/'reproduction/portuguese_hv_network/outputs/tables/pdird_parameter_path_ledger.csv')
 for source in (D/'spatial_fields').rglob('*.npz'):
  copy(source,R/'reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision/spatial_fields'/source.relative_to(D/'spatial_fields'))
 copy(ROOT/'paper/figure_manifest_pt60_reader.csv',R/'paper/figure_manifest_pt60_reader.csv')
 for p in (ROOT/'paper/tables/pt60_main').glob('*.csv'):copy(p,R/'paper/tables/pt60_main'/p.name)
 for name in ['generate_pt60_review_figures.py','replay_pt60_january.py','replay_archived_validation_case.py','run_seasonal_validation.py','generate_pt60_reader_opening_figures.py','pt60_reader_figure_core.py','generate_scheme_b_workflow.py','audit_pt60_reader_consistency.py','prepare_pt60_reader_example.py','summarize_pt60_spatial_revision.py','audit_pt60_priority_evidence.py']:
  copy(ROOT/'paper/scripts'/name,R/'paper/scripts'/name)
 copy(Path('/Users/jumiray/.codex/skills/nature-figure/scripts/audit_panel_alignment.py'),R/'paper/scripts/figure_qa/audit_panel_alignment.py')
 stale=R/'topology/transformers_topology.csv'
 if stale.exists():stale.unlink()
 for name in ['DATA_LICENSE.md','LICENSE']:
  copy(ROOT/name,R/name)
 copy(ROOT/'data/releases/PT60-v2.0.0/ATTRIBUTION.md',R/'ATTRIBUTION.md')
 copy(ROOT/'requirements.txt',R/'requirements.txt')
 # Include files reached through the manuscript's local Markdown links.
 pending=list((R/'paper').glob('*.md')); visited=set()
 while pending:
  page=pending.pop()
  if page in visited:continue
  visited.add(page)
  for target in re.findall(r'\]\(([^)]+)\)',page.read_text()):
   target=unquote(target.split('#')[0]).strip('<>')
   if not target or '://' in target or target.startswith('/'):continue
   dest=(page.parent/target).resolve()
   try:relative=dest.relative_to(R.resolve())
   except ValueError:continue
   source=ROOT/relative
   if source.is_file():
    copy(source,dest)
    if dest.suffix=='.md':pending.append(dest)
 (R/'README.md').write_text('''# PT60 v2.1.0-rc2 — local reviewed release candidate

This local candidate contains 336 archived hourly input bundles and their primary case summaries, 16 sensitivity summaries at two fixed diagnostic timestamps, 336 frozen-control references, 672 spatial alternatives, and one complete solved example. The E-REDES facility records retain their original 15-minute kWh values and are converted to mean MW when each case is built. Source times are aligned as Lisbon start labels (REN) and end labels (E-REDES), converted to UTC interval starts. Missing observations remain explicitly missing. Fixed per-asset dispatch is preserved; allocation seeds require an explicit SEED mode. No DOI has been assigned and no public deposit is claimed.

`model/model_template.json` is a reconstruction template, not a solved operating point. Use the public interface with `scenario/generators.csv`; controls rebuild from available mapped non-battery bus capacity on every timestamp. The `p_mw` and control-mode columns in the asset table are reference fields; actual case dispatch is regenerated from each archived input. `model/` also contains the separately solved January example.

`topology/` retains 3,783 buses, 4,943 lines and 228 transformers. The pre-interconnector cases activate 3,664 buses; 4,787 lines have an active flag, and 4,773 have active endpoints. See `validation/network_scope_bridge.csv` and the exclusion ledger.

## Offline replay

Use Python 3.13 and install the declared dependencies. The compact timestamped public inputs needed for the full experiment are included; optional external acquisition is disabled in this runner.

```bash
python -m pip install -r requirements.txt
python paper/scripts/run_seasonal_validation.py --pilot --output-dir output/replay-pilot
python paper/scripts/run_seasonal_validation.py --workers 3 --output-dir output/replay-full
```

The pilot uses two fixed diagnostic timestamps and all 8 sensitivity variants. The default full run produces 336 primary summaries plus 16 sensitivity summaries. Add `--include-spatial` to reproduce the full frozen-control and spatial comparison panel (1,358 unique case/variant pairs). To write a complete solved model and device tables for one archived input, run `python paper/scripts/replay_archived_validation_case.py --case-id PT60_2025_SUMMER_WEEK_JUL07_13_H000 --output-dir output/case-H000`. No earlier weekly result file, raw-download cache, or temp PDF is required for replay. Source model and asset tables are reconstruction dependencies inside `reproduction/`. No third-party reproduction is claimed.

The January example can be replayed from the candidate root with `python paper/scripts/replay_pt60_january.py --output-dir january-replay`; its archived timestamped input is `model/january_input.json`.

To build a case from new timestamped public observations, follow `PUBLIC_MODEL_INTERFACE.md`; it starts from `examples/public_case/` and gives the input contract, processing order, command and output files.

## Rebuild the manuscript figures

The figure scripts read only files contained in this package. Run them from the extracted package root in workflow order, then rerun the manuscript consistency audit:

```bash
python paper/scripts/generate_pt60_reader_opening_figures.py
python paper/scripts/generate_scheme_b_workflow.py
python paper/scripts/generate_pt60_review_figures.py
python paper/scripts/audit_pt60_reader_consistency.py
```

The manuscript and explanatory supplement are in `paper/`. Internal consistency, public-input coverage and shared-input balance diagnostics are distinct from independent node/line validation. Historical topology, dispatch, reactive devices and many electrical parameters remain proxies. X ±10% and exclusion of undated assets are diagnostic perturbations, not calibrated uncertainty bounds. RNT-scope loss proxies and REN monthly loss percentages use different denominators and are not a physical-accuracy score.

Source code uses MIT. Data retain provider terms; see DATA_LICENSE.md and ATTRIBUTION.md. The included hourly REN/E-REDES inputs are compact structured observations with source metadata, not wholesale raw-source archives.
''')
 (R/'VERSION.json').write_text(json.dumps({'version':'2.1.0-rc2','status':'LOCAL_RELEASE_CANDIDATE_NOT_DEPOSITED','primary_variant':'AC_REVISED','reconstruction_fingerprint':manifest['fingerprint']},indent=2)+'\n')
 # Exclude self-referential checksum files, but hash every payload byte.
 entries=[{'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(R.rglob('*')) if p.is_file() and p.name not in {'manifest.json','checksums.sha256'}]
 (R/'manifest.json').write_text(json.dumps({'dataset':'PT60','version':'v2.1.0-rc2','files':entries},indent=2)+'\n')
 (R/'checksums.sha256').write_text(''.join(f"{x['sha256']}  {x['path']}\n" for x in entries))
 archive=R.parent / f'{R.name}.tar.gz'
 with tarfile.open(archive,'w:gz') as tar:tar.add(R,arcname=R.name)
 print(json.dumps({'archive':str(archive),'files':len(entries),'sha256':sha(archive)},indent=2))
if __name__=='__main__':build()

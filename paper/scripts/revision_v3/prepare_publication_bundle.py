from pathlib import Path
import json,shutil,hashlib,duckdb,subprocess
ROOT=Path(__file__).resolve().parents[3];V='SimPT60-2026.09.21-r1';B=ROOT/'data/releases'/V/'bundle';R=ROOT/'paper/revision_v3'
def copy(rel,dest=None):
 src=ROOT/rel;dst=B/(dest or rel);dst.parent.mkdir(parents=True,exist_ok=True)
 if src.is_dir():shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.DS_Store','*.log','invalid_configuration_audit','proxy_month_cache','manuscript_before_revision.md'))
 else:shutil.copy2(src,dst)
for rel in ['paper/paper_final_edited.md','paper/PT60_Sep16.MD','paper/PT60_Sep16_SUPPLEMENTARY_TABLES.md','paper/references_final.bib','paper/references_final.json','paper/figures_final','paper/scripts/figures','paper/scripts/revision_v3','paper/scripts/run_nminus1_application.py','paper/scripts/export_pt60_pdf.py','paper/revision_v3','portuguese_hv_network/src','portuguese_hv_network/outputs/validation_experiments','portuguese_hv_network/outputs/external_evidence_validation','output/pdf/paper_final_edited.pdf']:
 copy(rel)
# Exact inputs needed by original figure builders, selected by their immutable lock.
lock=json.loads((ROOT/'paper/scripts/figures/source_lock.json').read_text())
for rel,expected in lock.items():
 p=ROOT/rel
 if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('Figure source changed: '+rel)
 copy(rel)
# Preserve the reviewed work-grid state as data for the field-level reconciliation.
c=duckdb.connect('/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24/pt60_public_timeseries.duckdb',read_only=True);dest=B/'audit_inputs/working_grid';dest.mkdir(parents=True,exist_ok=True)
for t in ['buses','lines','transformers']:c.sql('select * from grid.'+t).df().to_parquet(dest/(t+'.parquet'),index=False)
c.close()
# A small pointer ledger replaces machine-specific historical paths during replay.
(B/'logical_paths.json').write_text(json.dumps({'core_inputs':'inputs.duckdb','core_model':'portuguese_hv_network/outputs/model/portuguese_hv_candidate.json','corrected_n1_models':'archived_nminus1/screens/*/corrected_base_model.json','monthly_assets':'../assets/pt60_models_YYYY-MM.compact.duckdb.zst','historical_absolute_paths':'Retained as immutable provenance; not runtime dependencies.'},indent=2))
print('Publication bundle updated',B)

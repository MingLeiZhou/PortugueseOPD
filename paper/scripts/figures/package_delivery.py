"""Package final figures, manuscript, scripts, tables, and all locked inputs."""
import json
import zipfile
from data import ROOT,verify_all

def main():
    verify_all()
    files=set()
    paper=ROOT/'paper'
    for name in ['PT60_Sep16.MD','PT60_Sep16.pdf','PT60_Sep16.html','PT60_Sep16_FIGURES.pdf',
                 'PT60_Sep16_SUPPLEMENTARY_TABLES.md','figure_audit_report.md']:
        files.add(paper/name)
    files.add(paper/'figures_original/context/PT60_Sep16.MD')
    for folder in ['figures_final','scripts/figures','tables/sep16']:
        files.update(p for p in (paper/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store')
    for name in ['export_pt60_pdf.py','export_sep16_review.py','validate_sep16_artifacts.py','rebuild_sep16_figures.py']:
        files.add(paper/'scripts'/name)
    lock=json.loads((paper/'scripts/figures/source_lock.json').read_text())
    files.update(ROOT/p for p in lock)
    target=paper/'SimPT60_FINAL_FIGURES.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(files):z.write(p,p.relative_to(ROOT))
        z.writestr('README.md','# SimPT60 final scientific figures\n\nStart with paper/figure_audit_report.md and paper/PT60_Sep16_FIGURES.pdf.\nRebuild instructions: paper/scripts/figures/README.md.\nAll 17 locked figure inputs are included with their original relative paths.\nThe mutable working database is intentionally excluded.\nOriginal artwork remains in paper/figures_original/ in the project. The original manuscript is included for table-preservation checks.\n')
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        for rel in lock:assert rel in z.namelist()
    print(f'{target}: {len(files)+1} files, {target.stat().st_size/1024**2:.1f} MB; CRC and source inventory passed.')

if __name__=='__main__':main()

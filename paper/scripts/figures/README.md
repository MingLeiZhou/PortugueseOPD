# Final SimPT60 scientific figures

The editorial audit is in `../../figure_audit_report.md`. The original 18-figure
edition is preserved under `../../figures_original/`. Only the eleven outputs in
`../../figures_final/` belong to the revised manuscript.

From the repository root:

```sh
.venv/bin/python paper/scripts/figures/run_all.py
.venv/bin/python paper/scripts/validate_sep16_artifacts.py
.venv/bin/python paper/scripts/export_pt60_pdf.py
.venv/bin/python paper/scripts/export_sep16_review.py
.venv/bin/python paper/scripts/figures/validate_delivery.py
.venv/bin/python paper/scripts/figures/package_delivery.py
```

Individual rebuild: `run_all.py --only 2 6`. Each `figNN_*.py` is also runnable.
Python dependencies: matplotlib, numpy, pandas, geopandas, pandapower, Pillow,
PyMuPDF. Manuscript export uses Pandoc, XeLaTeX and Songti SC.

`source_lock.json` is an explicit archived-input contract. Every file is checked
before a complete build begins. A changed source raises
`VERSION / SNAPSHOT CONFLICT`; the script does not update hashes or query a
working database. Resolve release-version provenance separately before changing
the lock. There is no interpolation, synthetic operating data, or new simulation.

Each figure has PDF, editable-text SVG, and 600 dpi PNG outputs at 7.2 inches
(182.88 mm) wide. Figure-specific JSON sidecars record sources and definitions.
The combined manifest includes verified source hashes and, after validation,
output hashes. Full IDs and unrounded values for Figure 11 panels (c,d) accompany
the figures as CSV files. These are derived display tables, not new experiments.

`update_manuscript.py` performs the initial reduction once; it leaves an already
reduced manuscript untouched. Do not use the legacy 18-figure insertion script.
Tables remain editable Markdown, with 15 main and 4 supplementary tables.

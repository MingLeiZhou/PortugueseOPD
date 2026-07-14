# PT60-Candidate P0.12 Reproducibility, Figure, and DOCX Audit

Date: 2026-07-14

## Outcome

**PASS WITH A DOCUMENTED RAW-SOURCE LIMITATION.** The deposited v1.0.0 archive is byte-reproducible from the frozen derived inputs, and every quantitative figure and quantitative main-text data table is now generated from the extracted public release rather than from `data/processed`. Raw/API-to-release byte reproduction remains unavailable because raw topology snapshots and their hashes were not frozen and the final clean-room audit found that the recorded live v2.1 export URLs returned HTTP 404.

## Frozen release checked

- Archive: `data/releases/PT60-Candidate-v1.0.0.tar.gz`
- SHA-256: `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`
- Release commit: `1eb690302f35f7c0090c14c994d073539cbb5335`
- Release tag: `pt60-candidate-v1.0.0`
- Package validation: PASS; 63 files and 63 manifest records, with no checksum, schema, documentation, or headline-count failures.

The source manifest identifies the three topology-critical source datasets and their catalog-check timestamps:

| Dataset ID | Role | Records reported | Checked at |
|---|---|---:|---|
| `rede-at-teste` | AT lines | 5,334 | 2026-07-13 14:15:57 UTC |
| `se-at_2025` | AT substations | 410 | 2026-07-13 14:16:08 UTC |
| `pc-at_2025` | switching facilities | 76 | 2026-07-13 14:15:31 UTC |

These records verify identifiers, roles, catalog timestamps, reported counts, URLs, and licensing basis. They do not provide raw-response SHA-256 values. The manuscript therefore correctly limits the reproducibility claim to the frozen derived-input chain and explicitly states that raw/API-to-release byte reproduction is not claimed.

## Quantitative regeneration chain

Run from the repository root:

```bash
python paper/scripts/generate_quantitative_figures.py
python paper/scripts/generate_scidata_tables.py
python paper/scripts/build_figure_manifest.py --status validated
```

The figure generator reads only:

- `data/releases/PT60-Candidate-v1.0.0/core_topology/at_paper_logic_summary.json`
- `data/releases/PT60-Candidate-v1.0.0/core_topology/at_paper_logic_graph.graphml`
- `data/releases/PT60-Candidate-v1.0.0/core_topology/at_paper_logic_parameter_sweep.csv`

It writes three quantitative figures in PDF, SVG, and 300 dpi PNG plus `paper/figures/generated/main_quantitative_figure_build.json`, which records source and output hashes.

The table generator reads the frozen release manifest, core topology, validation summary, source manifest, and data dictionary. It writes the archive-layout, principal-artifact, and OSM-triangulation LaTeX tables plus `paper/generated_tables/table_build_provenance.json`. The manuscript inputs these generated tables directly.

## Figure suitability review

### Figure 1 — fail-closed reconstruction and public-release workflow

**Suitable after revision.** The previous diagram incorrectly kept Stage 1–5 interfaces, ACPF/DC-OPF diagnostics, and electrical-readiness gates in the primary flow even though they are excluded from the main public archive. The revised editable draw.io source now ends at the versioned archive and depicts retained branches, the full downgrade/rejection ledger, validation/sensitivity, provenance, schemas, licensing, checksums, and the claim boundary. It contains no geographic coordinates or operator-validation implication.

### Figure 2 — reconstruction funnel and disposition

**Suitable for Data Overview.** It exposes both retention and every downgrade class, uses exact counts from the frozen topology summary, and labels retention as rule satisfaction rather than physical truth. Exact labels make the result interpretable without relying on colour.

### Figure 3 — component and degree structure

**Suitable, but optional if journal space becomes tight.** It is an aggregate graph-quality view with no coordinates or facility identifiers. It communicates fragmentation, isolates, and multigraph degree more clearly than prose. Its caption must continue to state that it describes the candidate graph artifact, not the physical Portuguese network.

### Figure 4 — 216-configuration sensitivity

**Suitable for Technical Validation.** Heatmap cells contain exact values, the selected point is explicitly marked as documented rather than optimal, and panel d now uses both colour and marker shape for largest-component categories. No confidence intervals are shown because each configuration is deterministic rather than a statistical replicate.

### Excluded figures

- Figure 5 electrical-parameter coverage remains outside the canonical main build because the related diagnostic extension is not part of the main public archive.
- Figure 6 Stage 1–5 architecture remains outside the canonical main build because those interfaces are excluded from the main archive.

## Figure validation results

- All four canonical PDFs passed the scientific-figure artifact validator with no errors or warnings.
- All four PNG previews passed at 300 dpi.
- Quantitative SVGs passed with no warnings.
- The draw.io SVG for Figure 1 contains embedded rendering elements and triggers an alternate-export warning. The canonical manuscript uses the validated vector PDF; the editable source remains the `.drawio` file.
- `paper/figure_manifest.csv` contains four validated rows and frozen-release source hashes.
- Visual inspection found no clipped labels, overlaps, missing glyphs, or hidden values at the generated size.

## DOCX export

Run:

```bash
python paper/scripts/export_scidata_docx.py
```

Output: `paper/PT60-Candidate_Scientific_Data_draft.docx`

The DOCX contains the title metadata, author placeholder, all manuscript sections, five Word tables, the bibliography, and four embedded 300 dpi PNG figures. The export script preserves archive filenames, SHA-256 values, and the frozen code commit that Pandoc otherwise drops from custom LaTeX macros. The DOCX is an editable review copy; LaTeX/PDF remains the typographic source of truth.

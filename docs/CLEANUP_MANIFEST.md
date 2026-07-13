# Repository cleanup manifest

Cleanup date: 2026-07-13

## Removed

- project-local dependency installation (`.deps/`);
- Python and plotting caches;
- downloaded raw and sample datasets;
- downloaded standards, catalogs, and reference PDFs;
- benchmark sandbox result tables and plots;
- execution logs and exploratory notebook output;
- interactive validation maps;
- non-frozen S5-S29 exploratory scenario result directories;
- duplicate human-readable test and validation reports;
- obsolete direct-snapping processed outputs superseded by paper-style reconstruction.

## Retained locally but excluded from Git

- paper-style topology reconstruction outputs;
- source and parameter audit tables;
- fail-closed ACPF inputs and required diagnostic provenance;
- S16 and S30 frozen benchmark artifacts;
- Stage 1-5 dataset release packages and machine-readable validation summaries.

## Retained in Git

- source code;
- stable documentation;
- dependency specification;
- code license and data-license boundary;
- empty generated-data directory placeholders.

The cleanup intentionally separates the software repository from a future versioned data
deposit. No operator-grade or real-grid claim is introduced by this reorganization.

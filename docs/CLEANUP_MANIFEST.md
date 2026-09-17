# Repository cleanup manifest

Last updated: 2026-09-17

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
- Python bytecode, test caches, package build directories and stale package metadata;
- website build output and TypeScript incremental-build metadata;
- superseded `diagram-design` manuscript figures and their generator.

## Retained locally but excluded from Git

- paper-style topology reconstruction outputs;
- source and parameter audit tables;
- fail-closed ACPF inputs and required diagnostic provenance;
- S16 and S30 frozen benchmark artifacts;
- Stage 1-5 dataset release packages and machine-readable validation summaries.
- release-candidate directories, compressed release archives and website download payloads;
- downloaded public-evidence documents and full temporal/model databases;
- exploratory OPF output and rendered manuscript files.

## Retained in Git

- source code;
- stable documentation;
- dependency specification;
- code license and data-license boundary;
- empty generated-data directory placeholders.
- installable `pt60-tools` package, package tests and CI workflow;
- canonical manuscript source `paper/PT60_Sep16.MD`;
- reproducible Matplotlib/GeoPandas figure generator, style, manifest and current
  scientific figure exports.

The cleanup separates source, compact frozen releases and paper assets from large
runtime databases and generated distributions. No operator-grade or real-grid claim
is introduced by this reorganization.

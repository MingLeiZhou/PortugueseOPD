# Project status

## Research objective

PortugueseOPD studies how far publicly available E-REDES data can support a reproducible
60 kV topology candidate and diagnostic benchmark without hiding missing electrical data.

The intended contribution is a fail-closed pipeline and an open candidate dataset, not a
claim that the real Portuguese operational grid has been reconstructed.

## Completed work

- Input inventory, schema profiling, and join-key analysis.
- Paper-style topology reconstruction using facility footprints, endpoint clustering,
  voltage-aware union-find merging, and circuit classification.
- Extraction of 358 reliable inter-facility AT candidate branches.
- Portuguese and European parameter-source audit with source/confidence labels.
- Pandapower-shaped candidate tables and fail-closed readiness gates.
- A frozen diagnostic benchmark candidate:
  - AC PF reference: S16 reduced backbone, 54 buses, 36 lines, 25 loads.
  - DC OPF reference: S30 parallel-equivalent diagnostic scenario.
- Stage 1-5 dataset packaging, graph export, task/split contracts, and framework-neutral
  learning adapters.
- Consumer-facing QA smoke coverage for stage-4 benchmark contracts and stage-5 adapter
  contracts.
- Stage-4 v2 label-support policy: grouped regression as the primary benchmark, relative
  high-stress/high-dispatch classification as limited-support auxiliary tasks, and original
  overload/top-dispatch classification retained as non-headline challenge tasks.

## Current limitations

- Candidate topology is inferred and not operator-validated.
- Important line parameters and actual circuit counts are incomplete or scenario-based.
- Generator, import, and cost semantics are proxies.
- Benchmark labels are generated from diagnostic scenarios rather than observed events.
- Benchmark-core positive entities remain insufficient for the original overload and
  top-dispatch classifications; row-balanced splits are plumbing-only.
- AC OPF is outside benchmark-v1 scope.
- Public redistribution of topology-derived data awaits source-license clarification.

## Realistic completion target

The project is complete when it provides:

1. a reproducible pipeline from source download to validated dataset contracts;
2. a versioned diagnostic topology/benchmark release with explicit provenance;
3. topology and parameter-readiness ablations;
4. simple benchmark baselines demonstrating dataset usability;
5. a paper that reports limitations as results rather than claiming operator-grade realism.

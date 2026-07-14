# 99 ACPF Non-Convergence Localization

Generated: 2026-07-13T14:06:33+00:00

Status: `DIAGNOSTIC_ONLY`

## Scope

The full benchmark-plumbing net is tested with leave-one-line-out, leave-one-transformer-out, and slack-depth frontier ablations. Every run records unsupplied load before solving. Convergence caused by disconnecting more than 5% of active load is not counted as a load-preserving localization signal.

## Counts

- active graph: 94 buses, 65 lines, 41 transformers
- line ablation runs: 132
- transformer ablation runs: 82
- depth-frontier runs: 51
- failure-frontier localization runs: 78
- converged single-element ablations preserving at least 95% of load: 0
- converged ablations explained by material load disconnection: 13

## First failing depth

```json
[
  {
    "load_scale": 0.05,
    "first_nonconverged_depth": 15,
    "frontier_line_names": "ATPL_00012|ATPL_00049|ATPL_00051|ATPL_00052|ATPL_00053|ATPL_00090|ATPL_00140|ATPL_00143|ATPL_00207|ATPL_00241|ATPL_00242|ATPL_00243|ATPL_00256|ATPL_00275|ATPL_00314|ATPL_00340",
    "frontier_trafo_names": "",
    "supplied_p_mw_at_failure": 36.58225
  },
  {
    "load_scale": 0.1,
    "first_nonconverged_depth": 15,
    "frontier_line_names": "ATPL_00012|ATPL_00049|ATPL_00051|ATPL_00052|ATPL_00053|ATPL_00090|ATPL_00140|ATPL_00143|ATPL_00207|ATPL_00241|ATPL_00242|ATPL_00243|ATPL_00256|ATPL_00275|ATPL_00314|ATPL_00340",
    "frontier_trafo_names": "",
    "supplied_p_mw_at_failure": 73.1645
  },
  {
    "load_scale": 1.0,
    "first_nonconverged_depth": 6,
    "frontier_line_names": "ATPL_00019|ATPL_00021|ATPL_00022|ATPL_00343",
    "frontier_trafo_names": "TR_1109S5727400_60_10|TR_1113S5901500_60_10|TR_1014S5001200_60_15",
    "supplied_p_mw_at_failure": 301.251
  }
]
```

## Failure-frontier localization

```json
[
  {
    "load_scale": 0.05,
    "failure_depth": 15,
    "experiment": "frontier_add_one",
    "attempts": 16,
    "converged": 16
  },
  {
    "load_scale": 0.05,
    "failure_depth": 15,
    "experiment": "frontier_disable_one",
    "attempts": 16,
    "converged": 9
  },
  {
    "load_scale": 0.1,
    "failure_depth": 15,
    "experiment": "frontier_add_one",
    "attempts": 16,
    "converged": 16
  },
  {
    "load_scale": 0.1,
    "failure_depth": 15,
    "experiment": "frontier_disable_one",
    "attempts": 16,
    "converged": 13
  },
  {
    "load_scale": 1.0,
    "failure_depth": 6,
    "experiment": "frontier_add_one",
    "attempts": 7,
    "converged": 4
  },
  {
    "load_scale": 1.0,
    "failure_depth": 6,
    "experiment": "frontier_disable_one",
    "attempts": 7,
    "converged": 0
  }
]
```

Lines whose removal restored both 5% and 10% depth-15 cases without disconnecting load:

`ATPL_00012|ATPL_00049|ATPL_00052|ATPL_00090|ATPL_00140|ATPL_00241|ATPL_00242|ATPL_00275`

## Interpretation boundary

Ablation convergence localizes numerical/topological sensitivity but does not validate Portuguese electrical parameters or topology.

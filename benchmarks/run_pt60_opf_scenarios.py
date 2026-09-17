"""Reproducible load, line-rating and generator-outage checks; retain failures."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

from experiments.opf_neural.opf import DEFAULT_POLICY, solve, write_json
from experiments.opf_neural.neural import export_graph, infer


def run_case(source, output, policy_path):
    try:
        return solve(source, output, policy_path)
    except Exception as error:
        return {"status": "FAILED", "error": f"{type(error).__name__}: {error}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    root = Path(args.output)
    if root.exists(): raise ValueError("Benchmark output must be a new directory")
    root.mkdir(parents=True)
    scenarios = {"load_low": {"load_scale": .95}, "load_high": {"load_scale": 1.05},
        "line_derated": {"line_rating_scale": .9}}
    import pandapower as pp
    n = pp.from_json(args.model)
    candidates = n.gen.loc[n.gen.in_service & n.gen.nameplate_mw.between(50, 100)]
    if not candidates.empty:
        scenarios["generator_outage"] = {"generator_availability": {str(candidates.iloc[0]["name"]): 0.}}
    reports = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for name, changes in scenarios.items():
            policy_path = root / f"{name}.policy.json"
            write_json(policy_path, {**DEFAULT_POLICY, **changes})
            futures[pool.submit(run_case, args.model, root / name, policy_path)] = name
        for future in as_completed(futures):
            name = futures[future]
            reports[name] = {"opf": future.result()}
            print(name, reports[name]["opf"]["status"], flush=True)
            write_json(root / "benchmark.json", reports)
    for name, report in reports.items():
        if report["opf"].get("feasible"):
            path = root / name / "case.pyg.json"
            export_graph(root / name, path)
            if args.checkpoint:
                report["neural"] = infer(path, args.checkpoint, root / name / "prediction")
                print(name, "predicted", flush=True)
            write_json(root / "benchmark.json", reports)


if __name__ == "__main__": main()

"""PT60's shared command-line entry point."""
import argparse
import importlib.metadata
import json
import sys
from .dataset import Dataset, fetch


def main():
    p = argparse.ArgumentParser(description="PT60 data tools: inspect, solve and replay frozen public-data cases")
    p.add_argument("--release", help="Extracted release root; otherwise PT60_DATA or local rc2")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("info", "verify", "doctor"):
        commands.add_parser(name)
    c = commands.add_parser("cases"); c.add_argument("--season", choices=["SUMMER", "WINTER"]); c.add_argument("--json", action="store_true")
    c = commands.add_parser("fetch"); c.add_argument("source"); c.add_argument("--output", required=True); c.add_argument("--sha256", required=True)
    c = commands.add_parser("init"); c.add_argument("--output", required=True)
    c = commands.add_parser("solve"); c.add_argument("--input", required=True); c.add_argument("--output", required=True)
    c = commands.add_parser("replay"); c.add_argument("--case", required=True); c.add_argument("--output", required=True)
    c = commands.add_parser("batch"); c.add_argument("--output", required=True); c.add_argument("--full", action="store_true"); c.add_argument("--spatial", action="store_true"); c.add_argument("--workers", type=int, default=3)
    c = commands.add_parser("view"); c.add_argument("--port", type=int, default=8050); c.add_argument("--result-dir", help="Also display a solved custom case from the same template")
    c = commands.add_parser("export-web"); c.add_argument("--output", required=True, help="New static data directory for the existing Web map"); c.add_argument("--result-dir", help="Include one local solved case")
    args = p.parse_args()
    try:
        if args.command == "fetch":
            print(fetch(args.source, args.output, args.sha256)); return
        if args.command == "doctor":
            report = {"python": sys.version.split()[0]}
            for name in ("numpy", "pandapower", "pandas", "geopandas", "matplotlib", "osmium", "simbench"):
                try: report[name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError: report[name] = "MISSING: install pt60-tools[solve]"
            print(json.dumps(report, indent=2)); return
        ds = Dataset(args.release)
        if args.command == "info":
            print(json.dumps({"root": str(ds.root), "version": ds.manifest["version"], "cases": len(ds.cases()), "variants": ["AC_REVISED", "UNIFORM_PDE", "CAPACITY_PDE"], "formulation": "AC-PF"}, indent=2))
        elif args.command == "verify":
            report = ds.verify(); print(json.dumps(report, indent=2))
            if report["status"] != "PASS": raise SystemExit(1)
        elif args.command == "cases":
            cases = ds.cases(args.season)
            if args.json: print(json.dumps(cases, indent=2))
            else:
                for row in cases: print(row["case_id"], row["timestamp_utc"], row["season"])
        elif args.command == "init": print(ds.init_example(args.output))
        elif args.command == "solve": print(ds.solve(args.input, args.output))
        elif args.command == "replay": print(ds.replay(args.case, args.output))
        elif args.command == "batch": print(ds.batch(args.output, not args.full, args.spatial, args.workers))
        elif args.command == "view":
            from .viewer import serve
            serve(ds, args.port, args.result_dir)
        elif args.command == "export-web":
            from .web_export import export_web
            print(json.dumps(export_web(ds, args.output, args.result_dir), indent=2))
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        p.exit(1, f"pt60: {error}\n")

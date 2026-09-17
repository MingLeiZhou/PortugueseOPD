"""Archived experiment CLI; run as python -m experiments.opf_neural.cli."""
import argparse
import json
from pt60.dataset import Dataset


def main():
    p = argparse.ArgumentParser(description="Archived PT60 OPF and neural pilot; outside the core distribution")
    p.add_argument("--release", help="Extracted release root; otherwise PT60_DATA or local rc2")
    commands = p.add_subparsers(dest="command", required=True)
    c = commands.add_parser("opf-policy"); c.add_argument("--output", required=True)
    c = commands.add_parser("opf"); c.add_argument("--model", required=True); c.add_argument("--output", required=True); c.add_argument("--policy")
    c = commands.add_parser("graph-export"); c.add_argument("--opf", required=True); c.add_argument("--output", required=True)
    c = commands.add_parser("predict"); c.add_argument("--graph", required=True); c.add_argument("--checkpoint", required=True); c.add_argument("--output", required=True); c.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    c = commands.add_parser("graph-check"); c.add_argument("--graph", required=True); c.add_argument("--output", required=True); c.add_argument("--prediction", help="Optional neural initial guess; original constraints are preserved")
    c = commands.add_parser("finetune"); c.add_argument("--manifest", required=True); c.add_argument("--checkpoint", required=True); c.add_argument("--output", required=True); c.add_argument("--epochs", type=int, default=20); c.add_argument("--lr", type=float, default=1e-5)
    args = p.parse_args()
    try:
        ds = Dataset(args.release)
        if args.command == "opf-policy":
            from .opf import DEFAULT_POLICY
            with ds._output(args.output).open("x") as file: json.dump(DEFAULT_POLICY, file, indent=2)
        elif args.command == "opf":
            from .opf import solve
            print(json.dumps(solve(args.model, ds._output(args.output), args.policy), indent=2))
        elif args.command == "graph-export":
            from .neural import export_graph
            metadata = export_graph(args.opf, ds._output(args.output))
            print(json.dumps({k: v for k, v in metadata.items() if k != "branch_order"}, indent=2))
        elif args.command == "predict":
            from .neural import infer
            print(json.dumps(infer(args.graph, args.checkpoint, ds._output(args.output), args.device), indent=2))
        elif args.command == "graph-check":
            from .neural import check_roundtrip
            print(json.dumps(check_roundtrip(args.graph, ds._output(args.output), args.prediction), indent=2))
        elif args.command == "finetune":
            from .training import finetune
            print(json.dumps(finetune(args.manifest, args.checkpoint, ds._output(args.output), args.epochs, args.lr), indent=2))
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        p.exit(1, f"pt60: {error}\n")

if __name__ == "__main__":
    main()

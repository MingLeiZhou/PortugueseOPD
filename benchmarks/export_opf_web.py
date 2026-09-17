"""Publish only completed experiment artifacts into the existing website's data folder."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from experiments.opf_neural.opf import write_json
from pt60.dataset import digest


def export(root, output, guide):
    root, output = Path(root), Path(output)
    if output.exists(): raise ValueError("Choose a new Web experiment output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    names = [("base-s", "冬季公开基准"), ("summer", "夏季独立验证时点"), ("test", "冬季独立测试时点"),
        ("scenarios/load_low", "负荷 −5%"), ("scenarios/load_high", "负荷 +5%"),
        ("scenarios/line_derated", "线路定额 −10%"), ("scenarios/generator_outage", "聚合机组停运")]
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        stage = Path(temporary) / "experiments"; stage.mkdir()
        rows = []
        for name, label in names:
            source = root / name
            report = json.loads((source / "report.json").read_text())
            graph = json.loads((source / "case.pyg.json").read_text())
            if graph["metadata"]["source_ppc_sha256"] != digest(source / "ppc.json") or not report["feasible"]:
                raise ValueError(f"Unverified or mismatched OPF artifact: {name}")
            dest = stage / name; dest.mkdir(parents=True)
            for file in ["case.pyg.json", "policy.json", "report.json"]: shutil.copy2(source / file, dest / file)
            rows.append({"name": name, "label": label, "opf": report,
                "graph": f"/data/experiments/{name}/case.pyg.json", "policy": f"/data/experiments/{name}/policy.json"})
        result = {"source_release": "PT60-v2.1.0-rc2", "cases": rows,
            "roundtrip": json.loads((root / "base-s/roundtrip.json").read_text()),
            "repair": json.loads((root / "base-s/neural-repair.json").read_text()),
            "training": json.loads((root / "training/report.json").read_text())}
        write_json(stage / "index.json", result)
        shutil.copy2(root / "training/history.json", stage / "training-history.json")
        shutil.copy2(guide, stage / "guide.md")
        shutil.move(stage, output)
    return {"cases": len(rows), "training_epochs": result["training"]["epochs"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="output/opf")
    parser.add_argument("--output", default="portuguese_hv_network/site/public/data/experiments")
    parser.add_argument("--guide", default="docs/PT60_ACOPF_NEURAL_GUIDE_CN.md")
    args = parser.parse_args()
    print(export(args.root, args.output, args.guide))

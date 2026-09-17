"""Small, explicitly split GridSFM fine-tuning runs on verified PT60 OPF graphs."""
import json
from pathlib import Path
import time

from pt60.dataset import digest
from .opf import write_json


def validate_splits(groups):
    source_sets = {}
    for split, paths in groups.items():
        if not paths: raise ValueError(f"Empty {split} split")
        source_sets[split] = set()
        for path in paths:
            obj = json.loads(Path(path).read_text())
            meta = obj["metadata"]
            if meta.get("termination_status") != "LOCALLY_SOLVED":
                raise ValueError("Only verified OPF labels can be used for training")
            source_sets[split].add(meta["source_pf_sha256"])
            if meta.get("source_case_id"):
                source_sets[split].add(f"{meta['source_case_id']}@{meta.get('source_timestamp_utc')}")
    names = list(source_sets)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            if source_sets[left] & source_sets[right]:
                raise ValueError(f"Source-case leakage between {left} and {right}; keep all perturbations in one split")
    return {key: sorted(value) for key, value in source_sets.items()}


def finetune(manifest_path, checkpoint, output, epochs=20, lr=1e-5):
    import torch
    from torch.utils.data import DataLoader
    from gridsfm import load_model, load_opfdata, prepare_for_inference, batch_data_list, finetune_opfdata
    from gridsfm.checkpoint import _hash_state_dict
    from .neural import infer
    if epochs < 1 or lr <= 0: raise ValueError("Epochs and learning rate must be positive")
    manifest = json.loads(Path(manifest_path).read_text())
    groups = {split: [str((Path(manifest_path).parent / path).resolve()) for path in manifest[split]] for split in ("train", "validation", "test")}
    sources = validate_splits(groups)
    output = Path(output)
    if output.exists(): raise ValueError("Training output must be a new directory")
    output.mkdir(parents=True)
    torch.manual_seed(60)
    torch.set_num_threads(2)
    model = load_model(str(checkpoint), device="cpu")

    def dataset(paths):
        data = []
        for path in paths:
            obj = json.loads(Path(path).read_text())
            graph = load_opfdata(path)
            for kind in ("bus", "generator"):
                graph[kind].y = torch.tensor(obj["solution"]["nodes"][kind], dtype=torch.float32)
            for kind in ("ac_line", "transformer"):
                if obj["solution"]["edges"][kind]["features"]:
                    graph["bus", kind, "bus"].edge_label = torch.tensor(obj["solution"]["edges"][kind]["features"], dtype=torch.float32)
            graph.feasible = torch.tensor(1, dtype=torch.long)
            data.append(prepare_for_inference(graph))
        return data

    train = DataLoader(dataset(groups["train"]), batch_size=1, shuffle=True, collate_fn=batch_data_list)
    validation = DataLoader(dataset(groups["validation"]), batch_size=1, collate_fn=batch_data_list)
    best = {"loss": float("inf"), "state": None, "epoch": None}

    def on_epoch(row):
        print(json.dumps(row), flush=True)
        value = row.get("val_loss", float("inf"))
        if value < best["loss"]:
            best.update(loss=value, epoch=row["epoch"], state={key: tensor.detach().cpu().clone() for key, tensor in model.state_dict().items()})

    start = time.perf_counter()
    history = finetune_opfdata(model, train, validation, epochs=epochs, lr=lr, on_epoch_end=on_epoch)
    if best["state"] is None: raise RuntimeError("No finite validation loss; no checkpoint was selected")
    original = torch.load(checkpoint, weights_only=True, map_location="cpu")
    metadata = {**original["metadata"], "hash": _hash_state_dict(best["state"]),
        "pt60_finetune": {"parent_checkpoint_sha256": digest(checkpoint), "seed": 60, "selected_epoch": best["epoch"]}}
    trained = output / "pt60_gridsfm.pt"
    torch.save({"state_dict": best["state"], "metadata": metadata}, trained)
    write_json(output / "history.json", history)
    report = {"mode": "PT60_PILOT_FINETUNE", "source_splits": sources, "epochs": epochs, "lr": lr,
        "selected_epoch": best["epoch"], "selection": "minimum validation loss; test excluded from selection",
        "training_seconds": time.perf_counter() - start, "checkpoint_sha256": digest(trained),
        "graph_counts": {key: len(value) for key, value in groups.items()}, "test": []}
    for i, path in enumerate(groups["test"]):
        baseline = infer(path, checkpoint, output / f"test-{i}-pretrained")
        tuned = infer(path, trained, output / f"test-{i}-finetuned")
        report["test"].append({"graph_sha256": digest(path), "pretrained": baseline, "finetuned": tuned})
    write_json(output / "report.json", report)
    return report

"""Read-only dataset access and isolated execution of archived solver code."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = "PT60-v2.1.0-rc2"
VARIANTS = ("AC_REVISED", "UNIFORM_PDE", "CAPACITY_PDE")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path):
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Dataset:
    def __init__(self, root=None):
        if root is None:
            root = os.environ.get("PT60_DATA")
        if root is None:
            candidates = [Path.cwd() / "data/releases" / VERSION,
                          Path(__file__).resolve().parents[1] / "data/releases" / VERSION]
            root = next((p for p in candidates if p.is_dir()), None)
        if root is None:
            raise ValueError("Specify --release PATH or set PT60_DATA to an extracted release.")
        self.root = Path(root).expanduser().resolve()
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        if self.manifest.get("dataset") != "PT60":
            raise ValueError("Not a PT60 release")
        self._cases = None

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path escapes the dataset")
        return path

    def verify(self):
        failures = []
        for row in self.manifest["files"]:
            p = self.path(row["path"])
            if not p.is_file() or p.stat().st_size != row["bytes"] or digest(p) != row["sha256"]:
                failures.append(row["path"])
        return {"status": "FAIL" if failures else "PASS", "files": len(self.manifest["files"]),
                "failures": failures, "version": self.manifest["version"]}

    def cases(self, season=None):
        if self._cases is None:
            self._cases = rows(self.path("validation/seasonal_week_validation.csv"))
        return [dict(r) for r in self._cases if season is None or r["season"] == season.upper()]

    def case(self, case_id):
        return next((r for r in self.cases() if r["case_id"] == case_id), None)

    def load_input(self, case_id):
        if self.case(case_id) is None:
            raise ValueError(f"Unknown archived case: {case_id}")
        return json.loads(self.path(f"validation/inputs/{case_id}.json").read_text())

    def results(self, case_id, variant="AC_REVISED"):
        """Per-device frozen fields, joined by stable IDs, with null for inactive values."""
        import numpy as np
        if variant not in VARIANTS or self.case(case_id) is None:
            raise ValueError("Unknown case or variant")
        base = "reproduction/portuguese_hv_network/outputs/temporal_validation/quality_revision/spatial_fields"
        with np.load(self.path(f"{base}/{variant}/{case_id}.npz"), allow_pickle=False) as data:
            def mapping(ids, values):
                keys = data[ids].tolist()
                if len(set(keys)) != len(keys):
                    raise ValueError("Duplicate result IDs")
                return {key: float(v) if np.isfinite(v) else None for key, v in zip(keys, data[values], strict=True)}
            return {"case_id": case_id, "variant": variant, "version": self.manifest["version"],
                    "units": {"bus_voltage": "p.u.", "line_loading": "%"},
                    "bus_voltage": mapping("bus_id", "vm_pu"),
                    "line_loading": mapping("line_id", "loading_percent")}

    def topology(self):
        buses = rows(self.path("topology/buses.csv"))
        lines = rows(self.path("topology/lines.csv"))
        for r in lines:
            r["coordinates"] = json.loads(r.pop("geometry_json"))
        return {"buses": buses, "lines": lines}

    def _output(self, output):
        p = Path(output).expanduser().resolve()
        if p.is_relative_to(self.root) or self.root.is_relative_to(p):
            raise ValueError("Choose an output directory outside the frozen release")
        return p

    def init_example(self, output):
        output = self._output(output)
        shutil.copytree(self.path("examples/public_case"), output)
        return output

    def _run(self, script, args, output, allow_resume=False):
        output = self._output(output)
        if output.exists() and any(output.iterdir()) and not allow_resume:
            raise ValueError("Output is not empty; choose a new directory")
        output.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(self.path(script)), *args, "--output-dir", str(output)]
        with (output / "execution.log").open("a") as log:
            result = subprocess.run(command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Solver exited {result.returncode}; see {output / 'execution.log'}")
        return output

    def replay(self, case_id, output):
        self.load_input(case_id)
        return self._run("paper/scripts/replay_archived_validation_case.py", ["--case-id", case_id], output)

    def solve(self, input_dir, output):
        return self._run("reproduction/portuguese_hv_network/src/pt60_public_model.py",
                         ["--input-dir", str(Path(input_dir).resolve()), "--model", str(self.path("model/model_template.json")),
                          "--generators", str(self.path("scenario/generators.csv"))], output)

    def batch(self, output, pilot=True, spatial=False, workers=3):
        if workers < 1:
            raise ValueError("workers must be positive")
        args = ["--workers", str(workers)]
        if pilot:
            args.append("--pilot")
        if spatial:
            args.append("--include-spatial")
        return self._run("paper/scripts/run_seasonal_validation.py", args, output, allow_resume=True)


def fetch(source, destination, sha256):
    """Import a local tarball or HTTPS archive, checking its digest before extraction."""
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError("Destination must not exist")
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise ValueError("Expected a SHA-256 hex digest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".pt60-fetch-") as tmp:
        archive = Path(tmp) / "release.tar.gz"
        if str(source).startswith("https://"):
            request = urllib.request.Request(str(source), headers={"User-Agent": "pt60-tools/0.4.3"})
            with urllib.request.urlopen(request, timeout=120) as remote, archive.open("wb") as f:
                shutil.copyfileobj(remote, f)
        else:
            shutil.copyfile(source, archive)
        if digest(archive) != sha256.lower():
            raise ValueError("Archive SHA-256 mismatch")
        extracted = Path(tmp) / "extracted"
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if any(not (m.isfile() or m.isdir()) for m in members):
                raise ValueError("Archive must contain only files and directories")
            tar.extractall(extracted, filter="data")
        manifests = list(extracted.glob("*/manifest.json"))
        if len(manifests) != 1:
            raise ValueError("Expected one release root with manifest.json")
        dataset = Dataset(manifests[0].parent)
        report = dataset.verify()
        if report["status"] != "PASS":
            raise ValueError(f"Payload verification failed: {report['failures']}")
        shutil.move(str(dataset.root), str(destination))
    return destination

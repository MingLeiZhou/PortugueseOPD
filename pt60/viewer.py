"""Local read-only viewer backed directly by frozen rc2 device fields."""
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from .dataset import rows, digest


def load_custom(dataset, result_dir):
    root = Path(result_dir).resolve()
    summary = json.loads((root / "summary.json").read_text())
    manifest = json.loads((root / "run_manifest.json").read_text())
    if summary.get("converged") is not True:
        raise ValueError("Custom case must have a converged solution")
    if manifest.get("model_input", {}).get("sha256") != digest(dataset.path("model/model_template.json")):
        raise ValueError("Custom case uses a different topology template")
    tables = {"buses": rows(root / "bus_results.csv"), "lines": rows(root / "line_results.csv")}
    values = {}
    for kind, ident, column, output in [("buses", "bus_id", "result_vm_pu", "bus_voltage"), ("lines", "line_id", "result_loading_percent", "line_loading")]:
        ids = [r[ident] for r in tables[kind]]
        expected = {r[ident] for r in rows(dataset.path(f"topology/{kind}.csv"))}
        if len(set(ids)) != len(ids) or set(ids) != expected:
            raise ValueError("Custom result device IDs do not match release topology")
        values[output] = {}
        for row in tables[kind]:
            value = float(row[column]) if row[column] else float("nan")
            values[output][row[ident]] = value if math.isfinite(value) else None
    case_id = "LOCAL:" + summary["case_id"]
    return {"case_id": case_id, "season": "CUSTOM", "timestamp_utc": summary["timestamp_utc"]}, {
        "case_id": case_id, "variant": "AC_REVISED", "version": "local-result",
        "units": {"bus_voltage": "p.u.", "line_loading": "%"}, **values}


def handler(dataset, result_dir=None):
    custom = load_custom(dataset, result_dir) if result_dir else None
    @lru_cache(maxsize=1)
    def topology(): return dataset.topology()

    @lru_cache(maxsize=12)
    def result(case, variant):
        if custom and case == custom[0]["case_id"]:
            if variant != "AC_REVISED": raise ValueError("No spatial alternatives for custom result")
            return custom[1]
        return dataset.results(case, variant)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/":
                    payload = files("pt60").joinpath("viewer.html").read_bytes()
                    content = "text/html; charset=utf-8"
                else:
                    if url.path == "/api/catalog": value = {"version": dataset.manifest["version"], "cases": dataset.cases() + ([custom[0]] if custom else [])}
                    elif url.path == "/api/topology": value = topology()
                    elif url.path == "/api/results":
                        query = parse_qs(url.query)
                        value = result(query.get("case", [""])[0], query.get("variant", ["AC_REVISED"])[0])
                    else:
                        self.send_error(404); return
                    payload = json.dumps(value, allow_nan=False).encode()
                    content = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(payload)
            except (ValueError, FileNotFoundError) as exc:
                self.send_error(400, str(exc))
    return Handler


def serve(dataset, port=8050, result_dir=None):
    with ThreadingHTTPServer(("127.0.0.1", port), handler(dataset, result_dir)) as server:
        print(f"PT60 {dataset.manifest['version']} viewer: http://127.0.0.1:{server.server_port}", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass

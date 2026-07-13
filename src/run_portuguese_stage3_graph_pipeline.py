from __future__ import annotations

import json
import traceback
from typing import Any

from build_portuguese_stage3_graph_dataset import main as build_stage3_graph_dataset
from utils import utc_now
from validate_portuguese_stage3_graph_dataset import main as validate_stage3_graph_dataset


STATUS_PATH = (
    __import__("config").PROCESSED_DIR
    / "dataset_release_stage3"
    / "pt_stage3_pipeline_status.json"
)


def write_status(summary: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    summary: dict[str, Any] = {
        "generated_at": utc_now(),
        "pipeline": "portuguese_stage3_graph_dataset",
        "steps": [],
        "status": "PASS",
    }

    try:
        build_stage3_graph_dataset()
        summary["steps"].append({"step": "build_stage3_graph_dataset", "status": "PASS"})

        validate_stage3_graph_dataset()
        summary["steps"].append({"step": "validate_stage3_graph_dataset", "status": "PASS"})
    except Exception as exc:
        summary["status"] = "FAIL"
        summary["steps"].append(
            {
                "step": "pipeline",
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        write_status(summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        raise

    write_status(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Export the draw.io conceptual figures in reproducible paper formats."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "paper" / "figures" / "source"
OUT = ROOT / "paper" / "figures" / "generated"


def main() -> None:
    drawio = shutil.which("drawio")
    if not drawio:
        raise RuntimeError("drawio CLI is not available")
    OUT.mkdir(parents=True, exist_ok=True)
    for stem in ["fig1_pipeline_overview"]:
        source = SOURCE / f"{stem}.drawio"
        for fmt in ["pdf", "svg"]:
            subprocess.run(
                [drawio, "--export", "--crop", "--format", fmt, "--output", str(OUT / f"{stem}.{fmt}"), str(source)],
                check=True,
            )
        subprocess.run(
            [drawio, "--export", "--crop", "--format", "png", "--scale", "3", "--output", str(OUT / f"{stem}.png"), str(source)],
            check=True,
        )
        sips = shutil.which("sips")
        if sips:
            subprocess.run([sips, "-s", "dpiWidth", "300", "-s", "dpiHeight", "300", str(OUT / f"{stem}.png")], check=True)


if __name__ == "__main__":
    main()

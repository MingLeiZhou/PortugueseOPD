"""Shared utilities for full-resolution temporal PT60 runners."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd


def configure_logging(output_dir: Path, logger_name: str) -> tuple[logging.Logger, Path]:
    """Log progress to both stdout and an append-only execution log."""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "execution.log"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger, log_path


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes:d}m {seconds:02d}s"
    return f"{seconds:d}s"


def inclusive_dates(start_date: str, end_date: str) -> list[str]:
    """Return validated inclusive ISO calendar dates."""
    try:
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("start-date and end-date must be ISO dates (YYYY-MM-DD)") from exc
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("start-date and end-date must be calendar dates without a time zone")
    if start.time() != pd.Timestamp(start.date()).time() or end.time() != pd.Timestamp(end.date()).time():
        raise ValueError("start-date and end-date must not include a time of day")
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    return [stamp.strftime("%Y-%m-%d") for stamp in pd.date_range(start, end, freq="D")]

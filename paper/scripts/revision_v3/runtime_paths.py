"""Portable release paths; original worktree remains supported for the audit."""
from pathlib import Path
import os
ROOT=Path(__file__).resolve().parents[3]
EXTERNAL=Path(os.environ.get('SIMPT60_DATA_ROOT','/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24'))
IS_RELEASE=(ROOT/'inputs.duckdb').exists()
CORE_DB=ROOT/'inputs.duckdb' if IS_RELEASE else EXTERNAL/'monthly_models/2025-05/pt60_models_2025-05.compact.duckdb'
INPUT_DB=ROOT/'inputs.duckdb' if IS_RELEASE else EXTERNAL/'pt60_public_timeseries.duckdb'
MONTHLY_ROOT=ROOT/'monthly_models' if IS_RELEASE else EXTERNAL/'monthly_models'
PANEL=ROOT/'archived_nminus1' if IS_RELEASE else ROOT/'portuguese_hv_network/outputs/annual_nminus1_panel_v2'

"""Align REN interval starts and E-REDES interval ends on UTC intervals.

Source-clock evidence: data/evidence/time_alignment. REN DST day lengths and
both sources' blackout transitions identify Europe/Lisbon wall-clock labels.
E-REDES serializes those wall clocks with +00:00; this suffix is not treated as
an actual UTC offset. The interval-label roles are empirically checked against
the 2025-04-28 outage, not claimed to be an operator-authored field definition.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

SOURCE_ZONE = 'Europe/Lisbon'
ALIGNMENT_VERSION = 'LISBON_INTERVAL_START_END_V2'


def utc_interval_start(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError('Case timestamp must carry an explicit UTC offset')
    timestamp = timestamp.tz_convert('UTC')
    if timestamp.minute % 15 or timestamp.second or timestamp.microsecond or timestamp.nanosecond:
        raise ValueError('Case timestamp must be a 15-minute interval start')
    return timestamp


def ren_source_index(payload: dict, timestamp) -> tuple[str, int]:
    start = utc_interval_start(timestamp)
    local = start.tz_convert(SOURCE_ZONE)
    midnight = local.normalize()
    slots = pd.date_range(midnight, midnight + pd.DateOffset(days=1), freq='15min', inclusive='left')
    labels = list(payload['xAxis']['categories'])
    expected = slots.strftime('%H:%M').tolist()
    if labels != expected:
        raise ValueError('REN labels do not match the complete Europe/Lisbon day, including DST folds')
    index = int(slots.tz_convert('UTC').get_loc(start))
    return local.strftime('%Y-%m-%d'), index


def source_time_metadata(timestamp) -> dict:
    start = utc_interval_start(timestamp)
    end = start + pd.Timedelta(minutes=15)
    return {
        'time_alignment_version': ALIGNMENT_VERSION,
        'interval_start_utc': start.isoformat(),
        'interval_end_utc': end.isoformat(),
        'source_timezone': SOURCE_ZONE,
        'ren_source_timestamp_local': start.tz_convert(SOURCE_ZONE).isoformat(),
        'ren_label_role': 'INTERVAL_START',
        'eredes_source_timestamp_local': end.tz_convert(SOURCE_ZONE).isoformat(),
        'eredes_label_role': 'INTERVAL_END',
        'clock_evidence_status': 'DST_DAY_LENGTH_AND_BLACKOUT_TRANSITION_CHECKED',
        'interval_role_evidence_status': 'INFERRED_FROM_TIMESTAMPED_BLACKOUT_TRANSITION',
    }


def eredes_wall_label(timestamp) -> tuple[str, str, bool]:
    """Return the E-REDES wall-clock label and whether its fold is ambiguous."""
    end = utc_interval_start(timestamp) + pd.Timedelta(minutes=15)
    wall = end.tz_convert(SOURCE_ZONE).tz_localize(None).to_pydatetime()
    zone = ZoneInfo(SOURCE_ZONE)
    ambiguous = (
        wall.replace(tzinfo=zone, fold=0).utcoffset()
        != wall.replace(tzinfo=zone, fold=1).utcoffset()
    )
    return wall.strftime('%Y-%m-%d'), wall.strftime('%H:%M'), ambiguous


def eredes_source_label(timestamp) -> tuple[str, str]:
    date, hour, ambiguous = eredes_wall_label(timestamp)
    if ambiguous:
        raise ValueError('Ambiguous E-REDES repeated local-clock interval; source has no fold identifier')
    return date, hour

"""Shared helpers for Arbor School entities."""

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util


def parse_school_datetime(dt_str: str) -> datetime | None:
    """Parse an API datetime string, treating naive values as local time."""
    if not dt_str:
        return None
    try:
        parsed = datetime.fromisoformat(dt_str)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return parsed

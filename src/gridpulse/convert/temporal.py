"""Timestamp discipline: EIA hourly periods → full UTC ISO timestamps."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_eia_period(period: str) -> datetime:
    """Parse EIA's hourly period form (``2026-08-13T18``) as UTC.

    EIA's plain-hourly frequency is UTC (fixture-verified); the sibling
    "local-hourly" frequency is deliberately not used, so this function
    never guesses timezones.
    """
    return datetime.strptime(period, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)

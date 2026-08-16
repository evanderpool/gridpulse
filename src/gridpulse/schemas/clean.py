"""Clean-record models — the contract every downstream stage reads."""

from __future__ import annotations

from pydantic import BaseModel, Field

PERIOD_UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:00:00\+00:00$"


class DemandRecord(BaseModel):
    """One validated, converted hourly demand observation.

    ``period_utc`` is a full ISO-8601 UTC timestamp; localization happens
    only at the reporting edge, per the build plan's time discipline.
    ``quality_flags`` is a comma-joined list from convert/quality.py — empty
    means the value passed the written policy untouched.
    """

    region: str
    period_utc: str = Field(pattern=PERIOD_UTC_PATTERN)
    demand_mwh: int
    quality_flags: str = ""
    fetched_at: str


class FuelMixRecord(BaseModel):
    """One validated, converted hourly generation observation for one fuel."""

    region: str
    period_utc: str = Field(pattern=PERIOD_UTC_PATTERN)
    fueltype: str
    generation_mwh: int
    quality_flags: str = ""
    fetched_at: str

"""Clean-record models — the contract every downstream stage reads."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DemandRecord(BaseModel):
    """One validated, converted hourly demand observation.

    ``period_utc`` is a full ISO-8601 UTC timestamp; localization happens
    only at the reporting edge, per the build plan's time discipline.
    """

    region: str
    period_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:00:00\+00:00$")
    demand_mwh: int
    fetched_at: str

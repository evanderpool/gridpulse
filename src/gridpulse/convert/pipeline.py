"""The one public entry point of the conversion layer (Phase 1: demand only).

Pure function: bronze docs in, (clean records, rejects) out. No I/O, no
network — that's what makes any conversion bug replayable from bronze bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from ..schemas.clean import DemandRecord
from ..schemas.raw import RawDemandRow
from .temporal import parse_eia_period


@dataclass(frozen=True)
class Reject:
    """A row that failed validation, kept with its error — never dropped."""

    raw: dict
    error: str
    fetched_at: str


def convert_demand(bronze_docs: list[dict]) -> tuple[list[DemandRecord], list[Reject]]:
    """Validate and convert demand rows from bronze documents.

    Docs are processed in ``fetched_at`` order so that when EIA revises a
    past hour (it re-serves revised values for up to ~48h), the latest fetch
    wins downstream — the storage upsert keys on (region, period_utc).
    """
    records: list[DemandRecord] = []
    rejects: list[Reject] = []
    for doc in sorted(bronze_docs, key=lambda d: d["fetched_at"]):
        fetched_at = doc["fetched_at"]
        for raw_row in doc["payload"]["response"]["data"]:
            try:
                row = RawDemandRow.model_validate(raw_row)
            except ValidationError as exc:
                rejects.append(
                    Reject(
                        raw=raw_row,
                        error="; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()
                        ),
                        fetched_at=fetched_at,
                    )
                )
                continue
            records.append(
                DemandRecord(
                    region=row.respondent,
                    period_utc=parse_eia_period(row.period).isoformat(),
                    demand_mwh=row.value,
                    fetched_at=fetched_at,
                )
            )
    return records, rejects

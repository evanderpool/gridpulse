"""The one public entry point of the conversion layer.

Pure functions: bronze docs in, (clean records, rejects) out. No I/O, no
network — that's what makes any conversion bug replayable from bronze bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..schemas.clean import DemandRecord, FuelMixRecord
from ..schemas.raw import RawDemandRow, RawFuelMixRow
from .dedupe import dedupe_latest
from .flatten import iter_rows
from .quality import demand_flags, generation_flags
from .temporal import parse_eia_period

C = TypeVar("C", bound=BaseModel)


@dataclass(frozen=True)
class Reject:
    """A row that failed validation, kept with its error — never dropped."""

    raw: dict
    error: str
    fetched_at: str


def _convert(
    bronze_docs: list[dict],
    build: Callable[[dict, str], C],
) -> tuple[list[C], list[Reject]]:
    """Shared row lifecycle: validate+build inside one try, or quarantine.

    A regex-valid but unparseable period ("2026-02-30T05") raises ValueError
    from strptime and must quarantine the row — never abort the run
    (review finding HIGH-1).
    """
    records: list[C] = []
    rejects: list[Reject] = []
    for raw_row, fetched_at in iter_rows(bronze_docs):
        try:
            records.append(build(raw_row, fetched_at))
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
        except ValueError as exc:
            rejects.append(Reject(raw=raw_row, error=f"period: {exc}", fetched_at=fetched_at))
    return records, rejects


def convert_demand(bronze_docs: list[dict]) -> tuple[list[DemandRecord], list[Reject]]:
    """Validate, flag, and convert demand rows; one candidate per key."""

    def build(raw_row: dict, fetched_at: str) -> DemandRecord:
        row = RawDemandRow.model_validate(raw_row)
        return DemandRecord(
            region=row.respondent,
            period_utc=parse_eia_period(row.period).isoformat(),
            demand_mwh=row.value,
            quality_flags=",".join(demand_flags(row.value)),
            fetched_at=fetched_at,
        )

    records, rejects = _convert(bronze_docs, build)
    return dedupe_latest(records, key=lambda r: (r.region, r.period_utc)), rejects


def convert_fuelmix(bronze_docs: list[dict]) -> tuple[list[FuelMixRecord], list[Reject]]:
    """Validate, flag, and convert generation rows; one candidate per key."""

    def build(raw_row: dict, fetched_at: str) -> FuelMixRecord:
        row = RawFuelMixRow.model_validate(raw_row)
        return FuelMixRecord(
            region=row.respondent,
            period_utc=parse_eia_period(row.period).isoformat(),
            fueltype=row.fueltype,
            generation_mwh=row.value,
            quality_flags=",".join(generation_flags(row.value, row.fueltype)),
            fetched_at=fetched_at,
        )

    records, rejects = _convert(bronze_docs, build)
    return dedupe_latest(records, key=lambda r: (r.region, r.period_utc, r.fueltype)), rejects

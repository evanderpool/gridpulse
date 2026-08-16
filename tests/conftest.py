"""Shared test builders — ONE definition of the wire shape.

Every builder emits what EIA actually sends (string totals, hyphenated field
names), so no individual test can quietly drift from the wire format the
fixtures were captured to pin.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def good_row(**overrides) -> dict:
    """One valid demand row in wire shape."""
    row = {"period": "2026-08-13T18", "respondent": "ERCO", "type": "D",
           "value": 1000, "value-units": "megawatthours"}
    row.update(overrides)
    return row


def fuel_row(**overrides) -> dict:
    """One valid fuel-mix row in wire shape."""
    row = {"period": "2026-08-13T18", "respondent": "ERCO", "fueltype": "NG",
           "value": 7000, "value-units": "megawatthours"}
    row.update(overrides)
    return row


def wire_page(rows: list[dict], total: int | None = None) -> dict:
    """A response page exactly as EIA serves it — total is a STRING."""
    return {
        "response": {"total": str(total if total is not None else len(rows)), "data": rows},
        "request": {"command": "/v2/...", "params": {"api_key": "test-key"}},
    }


def bronze_doc(payload: dict, fetched_at: str = "2026-08-15T00:00:00+00:00") -> dict:
    """A bronze document as write_bronze produces it."""
    return {"fetched_at": fetched_at, "window": "test", "payload": payload}


def bronze_rows(rows: list[dict], fetched_at: str = "2026-08-15T00:00:00+00:00") -> dict:
    """Shortcut: bronze document wrapping a wire page built from rows."""
    return bronze_doc(wire_page(rows), fetched_at)


@pytest.fixture
def demand_payload() -> dict:
    """The captured live demand payload (20 rows, string total '100')."""
    return json.loads((FIXTURES / "demand_hourly_4regions.json").read_text(encoding="utf-8"))

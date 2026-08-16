"""Fixture sanity tests.

Phase 0 has no pipeline code; these tests pin the *shape* of the real EIA
payloads captured during pre-build validation (2026-08-15) so Phase 1 schemas
are written against verified reality, not documentation. Each fixture file is
named for the case it demonstrates.
"""

import json
import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", [p.name for p in FIXTURES.glob("*.json")])
def test_fixture_is_valid_scrubbed_json(name):
    doc = load(name)
    assert "response" in doc
    # Captured payloads echo the request back, so a raw capture would contain
    # the API key (EIA keys are 40 alphanumeric chars). Fixtures must be scrubbed.
    params = doc.get("request", {}).get("params", {})
    assert params.get("api_key") in (None, "REDACTED")
    assert not re.search(r"[A-Za-z0-9]{40}", json.dumps(doc))


def test_demand_rows_have_expected_fields():
    rows = load("demand_hourly_4regions.json")["response"]["data"]
    assert rows, "demand fixture should not be empty"
    row = rows[0]
    for field in ("period", "respondent", "type", "value", "value-units"):
        assert field in row


def test_solar_fixture_demonstrates_negative_nighttime_values():
    # The quirk this fixture exists to pin: CAISO solar goes negative at
    # night (station service). The quality policy must handle it explicitly.
    rows = load("solar_negative_nighttime_ciso.json")["response"]["data"]
    values = [int(r["value"]) for r in rows]
    assert min(values) < 0, "expected at least one negative nighttime value"
    assert max(values) > 10_000, "expected real daytime generation too"


def test_fuel_vocab_differs_by_region():
    # ERCOT reports battery storage (BAT); the CAISO sample does not.
    # Schemas must treat the fuel list as data, not as an enum.
    rows = load("fuel_vocab_differs_by_region.json")["response"]["data"]
    by_region: dict[str, set[str]] = {}
    for r in rows:
        by_region.setdefault(r["respondent"], set()).add(r["fueltype"])
    assert by_region["ERCO"] != by_region["CISO"]
    assert "BAT" in by_region["ERCO"]


def test_incomplete_return_warning_is_generic_noise():
    # Every response carries this warning even far below 5,000 rows —
    # the client must not treat warnings as errors.
    doc = load("demand_hourly_4regions.json")
    warnings = doc.get("warnings", [])
    assert any("incomplete" in w.get("warning", "") for w in warnings)
    assert len(doc["response"]["data"]) < 5000


def test_history_reaches_thirteen_months_back():
    rows = load("fuelmix_13mo_history_erco.json")["response"]["data"]
    assert rows and all(r["period"].startswith("2025-07-01") for r in rows)

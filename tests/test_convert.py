"""Conversion-layer tests, driven by the captured Phase 0 fixtures."""

import json
from pathlib import Path

from gridpulse.convert.pipeline import convert_demand
from gridpulse.convert.temporal import parse_eia_period

FIXTURES = Path(__file__).parent / "fixtures"


def bronze_doc(payload: dict, fetched_at: str = "2026-08-15T00:00:00+00:00") -> dict:
    return {"fetched_at": fetched_at, "window": "test", "payload": payload}


def demand_payload() -> dict:
    return json.loads((FIXTURES / "demand_hourly_4regions.json").read_text(encoding="utf-8"))


def test_period_parses_as_utc():
    dt = parse_eia_period("2026-08-13T18")
    assert dt.isoformat() == "2026-08-13T18:00:00+00:00"


def test_real_fixture_converts_with_zero_rejects():
    payload = demand_payload()
    # Two quirks pinned here: response.total reports the FULL result set,
    # not the rows in this page — pagination math must use len(data) — and
    # EIA serves total as a STRING, so the client must coerce it.
    assert payload["response"]["total"] == "100"
    assert len(payload["response"]["data"]) == 20
    records, rejects = convert_demand([bronze_doc(payload)])
    assert rejects == []
    assert len(records) == 20
    assert {r.region for r in records} == {"ERCO", "CISO", "MISO", "PJM"}
    assert all(r.period_utc.endswith("+00:00") for r in records)


def test_bad_rows_are_quarantined_with_error_not_dropped():
    payload = demand_payload()
    good = payload["response"]["data"][0]
    payload["response"]["data"] = [
        good,
        {**good, "value": None},          # null value — seen in EIA freshness gaps
        {**good, "period": "garbage"},    # malformed period
    ]
    records, rejects = convert_demand([bronze_doc(payload)])
    assert len(records) == 1
    assert len(rejects) == 2
    assert any("value" in r.error for r in rejects)
    assert any("period" in r.error for r in rejects)
    # The raw row survives verbatim for replay/review.
    assert rejects[0].raw["period"] == good["period"] or rejects[1].raw["period"] == "garbage"


def test_docs_processed_in_fetched_at_order():
    # EIA revises past hours: the later fetch must come out last so
    # storage's latest-wins upsert lands on the revised value.
    early = demand_payload()
    late = demand_payload()
    late["response"]["data"] = [
        {**late["response"]["data"][0], "value": 99999}
    ]
    early["response"]["data"] = [early["response"]["data"][0]]
    records, _ = convert_demand([
        bronze_doc(late, fetched_at="2026-08-15T12:00:00+00:00"),
        bronze_doc(early, fetched_at="2026-08-15T00:00:00+00:00"),
    ])
    assert records[-1].demand_mwh == 99999

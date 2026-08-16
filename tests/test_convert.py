"""Conversion-layer tests, driven by the captured Phase 0 fixtures."""

from conftest import bronze_doc, bronze_rows, good_row
from gridpulse.convert.pipeline import convert_demand
from gridpulse.convert.temporal import parse_eia_period


def test_period_parses_as_utc():
    dt = parse_eia_period("2026-08-13T18")
    assert dt.isoformat() == "2026-08-13T18:00:00+00:00"


def test_real_fixture_converts_with_zero_rejects(demand_payload):
    # Two quirks pinned here: response.total reports the FULL result set,
    # not the rows in this page — pagination math must use len(data) — and
    # EIA serves total as a STRING, so the client must coerce it.
    assert demand_payload["response"]["total"] == "100"
    assert len(demand_payload["response"]["data"]) == 20
    records, rejects = convert_demand([bronze_doc(demand_payload)])
    assert rejects == []
    assert len(records) == 20
    assert {r.region for r in records} == {"ERCO", "CISO", "MISO", "PJM"}
    assert all(r.period_utc.endswith("+00:00") for r in records)


def test_bad_rows_are_quarantined_with_error_not_dropped():
    records, rejects = convert_demand([bronze_rows([
        good_row(),
        good_row(value=None),          # null value — seen in EIA freshness gaps
        good_row(period="garbage"),    # malformed period
    ])])
    assert len(records) == 1
    assert len(rejects) == 2
    assert any("value" in r.error for r in rejects)
    assert any("period" in r.error for r in rejects)
    # The raw rows survive verbatim for replay/review — both of them.
    assert any(r.raw.get("period") == "garbage" for r in rejects)
    assert any(r.raw.get("value") is None for r in rejects)


def test_docs_processed_in_fetched_at_order():
    # EIA revises past hours: the later fetch must come out last so
    # storage's latest-wins upsert lands on the revised value.
    records, _ = convert_demand([
        bronze_rows([good_row(value=99999)], fetched_at="2026-08-15T12:00:00+00:00"),
        bronze_rows([good_row(value=11111)], fetched_at="2026-08-15T00:00:00+00:00"),
    ])
    assert records[-1].demand_mwh == 99999

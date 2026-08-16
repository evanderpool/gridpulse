"""Regression pins for the Phase 1 logic-review findings (2026-08-15).

One test per finding, named for it. If any of these fail, a reviewed-and-fixed
defect has been reintroduced.
"""

import json
from pathlib import Path

import httpx
import pytest

from gridpulse.cli import cmd_ingest, cmd_transform
from gridpulse.client import EiaClient, PullBudgetExceeded
from gridpulse.config import PAGE_LENGTH, Settings
from gridpulse.convert.pipeline import convert_demand
from gridpulse.storage import connect, upsert_demand, write_quarantine

FIXTURES = Path(__file__).parent / "fixtures"


def good_row() -> dict:
    return {"period": "2026-08-13T18", "respondent": "ERCO", "type": "D",
            "value": 1000, "value-units": "megawatthours"}


def bronze(rows: list[dict], fetched_at: str = "2026-08-15T00:00:00+00:00") -> dict:
    return {"fetched_at": fetched_at, "window": "t",
            "payload": {"response": {"total": str(len(rows)), "data": rows}}}


def test_high1_regex_valid_impossible_date_quarantines_not_crashes():
    # "2026-02-30T05" and hour 24 pass the period regex but blow up strptime;
    # they must land in quarantine without taking the run down.
    rows = [good_row(),
            {**good_row(), "period": "2026-02-30T05"},
            {**good_row(), "period": "2026-08-13T24"}]
    records, rejects = convert_demand([bronze(rows)])
    assert len(records) == 1
    assert len(rejects) == 2
    assert all("period" in r.error for r in rejects)


def test_med1_http_error_message_never_contains_the_key(tmp_path):
    client = EiaClient(
        Settings(api_key="SUPERSECRET", data_dir=tmp_path),
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
    )
    with pytest.raises(RuntimeError) as exc_info:
        client.fetch_demand_window("a", "b")
    assert "SUPERSECRET" not in str(exc_info.value)
    assert "401" in str(exc_info.value)


def test_med1_settings_repr_hides_the_key(tmp_path):
    settings = Settings(api_key="SUPERSECRET", data_dir=tmp_path)
    assert "SUPERSECRET" not in repr(settings)


def test_med2_requarantining_same_failure_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    rows = [{**good_row(), "value": None}]
    _, rejects = convert_demand([bronze(rows)])
    assert write_quarantine(conn, "run1", rejects) == 1
    # Same bronze, next scheduled run: nothing NEW to quarantine.
    _, rejects2 = convert_demand([bronze(rows)])
    assert write_quarantine(conn, "run2", rejects2) == 0
    total = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
    assert total == 1


def test_med3_identical_replay_reports_zero_upserts(tmp_path):
    conn = connect(tmp_path / "t.db")
    records, _ = convert_demand([bronze([good_row()])])
    assert upsert_demand(conn, records) == 1
    assert upsert_demand(conn, records) == 0  # the ledger can now SEE idempotency


def test_med3_same_fetch_revised_value_still_updates(tmp_path):
    # Equal fetched_at with a different value (page-duplicate revision) must
    # still write — only the byte-identical case counts zero.
    conn = connect(tmp_path / "t.db")
    first, _ = convert_demand([bronze([good_row()])])
    revised, _ = convert_demand([bronze([{**good_row(), "value": 2222}])])
    upsert_demand(conn, first)
    assert upsert_demand(conn, revised) == 1
    assert conn.execute("SELECT demand_mwh FROM demand_hourly").fetchone()[0] == 2222


def test_low2_budget_death_mid_window_persists_partial_bronze(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        rows = [good_row()] * PAGE_LENGTH
        return httpx.Response(200, json={"response": {"total": str(50_000), "data": rows},
                                         "request": {"params": {"api_key": "k"}}})

    settings = Settings(api_key="k", data_dir=tmp_path / "data", max_requests_per_run=2)
    client = EiaClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(PullBudgetExceeded):
        cmd_ingest(settings, "a", "b", client=client)
    partial = list(settings.bronze_dir.glob("*_partial_*.json"))
    assert len(partial) == 2  # both paid-for pages persisted


def test_low3_corrupt_bronze_file_is_quarantined_not_fatal(tmp_path):
    settings = Settings(api_key="k", data_dir=tmp_path / "data")
    settings.bronze_dir.mkdir(parents=True)
    (settings.bronze_dir / "demand_good_p000_r.json").write_text(
        json.dumps(bronze([good_row()])), encoding="utf-8")
    (settings.bronze_dir / "demand_bad_p000_r.json").write_text("{truncated", encoding="utf-8")
    (settings.bronze_dir / "notes.json").write_text("not ours", encoding="utf-8")  # ignored

    assert cmd_transform(settings) == 0
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM demand_hourly").fetchone()[0] == 1
    raw, = conn.execute("SELECT raw FROM quarantine").fetchone()
    assert "demand_bad_p000_r.json" in raw

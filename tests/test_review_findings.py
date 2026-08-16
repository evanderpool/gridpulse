"""Regression pins for the Phase 1 review findings (2026-08-15).

One test per finding, named for it — logic review first, code review below.
If any of these fail, a reviewed-and-fixed defect has been reintroduced.
"""

import json

import httpx
import pytest

from conftest import bronze_rows, good_row, wire_page
from gridpulse.cli import cmd_ingest, cmd_transform
from gridpulse.client import EiaClient, PullBudgetExceeded
from gridpulse.config import PAGE_LENGTH, Settings
from gridpulse.convert.pipeline import convert_demand
from gridpulse.storage import connect, upsert_demand, write_quarantine


def test_high1_regex_valid_impossible_date_quarantines_not_crashes():
    # "2026-02-30T05" and hour 24 pass the period regex but blow up strptime;
    # they must land in quarantine without taking the run down.
    records, rejects = convert_demand([bronze_rows([
        good_row(),
        good_row(period="2026-02-30T05"),
        good_row(period="2026-08-13T24"),
    ])])
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
    _, rejects = convert_demand([bronze_rows([good_row(value=None)])])
    assert write_quarantine(conn, "run1", rejects) == 1
    # Same bronze, next scheduled run: nothing NEW to quarantine.
    _, rejects2 = convert_demand([bronze_rows([good_row(value=None)])])
    assert write_quarantine(conn, "run2", rejects2) == 0
    assert conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1
    conn.close()


def test_med3_identical_replay_reports_zero_upserts(tmp_path):
    conn = connect(tmp_path / "t.db")
    records, _ = convert_demand([bronze_rows([good_row()])])
    assert upsert_demand(conn, records) == 1
    assert upsert_demand(conn, records) == 0  # the ledger can now SEE idempotency
    conn.close()


def test_med3_same_fetch_revised_value_still_updates(tmp_path):
    # Equal fetched_at with a different value (page-duplicate revision) must
    # still write — only the byte-identical case counts zero.
    conn = connect(tmp_path / "t.db")
    first, _ = convert_demand([bronze_rows([good_row()])])
    revised, _ = convert_demand([bronze_rows([good_row(value=2222)])])
    upsert_demand(conn, first)
    assert upsert_demand(conn, revised) == 1
    assert conn.execute("SELECT demand_mwh FROM demand_hourly").fetchone()[0] == 2222
    conn.close()


def test_low2_budget_death_mid_window_persists_partial_bronze(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=wire_page([good_row()] * PAGE_LENGTH, total=50_000)
        )

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
        json.dumps(bronze_rows([good_row()])), encoding="utf-8")
    (settings.bronze_dir / "demand_bad_p000_r.json").write_text("{truncated", encoding="utf-8")
    (settings.bronze_dir / "notes.json").write_text("not ours", encoding="utf-8")  # ignored

    assert cmd_transform(settings) == 0
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM demand_hourly").fetchone()[0] == 1
    raw, = conn.execute("SELECT raw FROM quarantine").fetchone()
    assert "demand_bad_p000_r.json" in raw
    conn.close()


# ---- Code-review findings (second sequential review) ----


def test_cr_med2_budget_exception_pages_are_instance_state():
    # A shared class-level list would leak pages across raise sites.
    a = PullBudgetExceeded("a")
    b = PullBudgetExceeded("b")
    a.pages.append({"x": 1})
    assert b.pages == []
    assert PullBudgetExceeded("c").pages == []


def test_p2_high1_flag_only_change_still_updates_silver(tmp_path):
    # Same value, same fetched_at, different flags — the exact Phase-1-era
    # migration scenario: a backfilled '' flag must be repairable, or no
    # policy change can ever reach already-stored rows (P2 finding HIGH-1).
    import sqlite3

    from gridpulse.schemas.clean import DemandRecord
    from gridpulse.storage import upsert_demand

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE demand_hourly (region TEXT NOT NULL, period_utc TEXT NOT NULL, "
        "demand_mwh INTEGER NOT NULL, fetched_at TEXT NOT NULL, "
        "PRIMARY KEY (region, period_utc))"
    )
    old.execute(
        "INSERT INTO demand_hourly VALUES "
        "('ERCO', '2026-08-13T18:00:00+00:00', -5, '2026-08-15T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    conn = connect(db)  # migration backfills quality_flags=''
    replay = DemandRecord(
        region="ERCO", period_utc="2026-08-13T18:00:00+00:00", demand_mwh=-5,
        quality_flags="nonpositive_demand", fetched_at="2026-08-15T00:00:00+00:00",
    )
    assert upsert_demand(conn, [replay]) == 1  # flag-only change writes
    assert conn.execute(
        "SELECT quality_flags FROM demand_hourly"
    ).fetchone()[0] == "nonpositive_demand"
    # And a byte-identical replay (flags included) still counts 0.
    assert upsert_demand(conn, [replay]) == 0
    conn.close()


def test_cr_med6_failed_ingest_still_writes_a_ledger_row(tmp_path):
    # The run that exhausts the API budget is exactly the run that must be
    # attributable in the metrics ledger.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=wire_page([good_row()] * PAGE_LENGTH, total=50_000)
        )

    settings = Settings(api_key="k", data_dir=tmp_path / "data", max_requests_per_run=2)
    client = EiaClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(PullBudgetExceeded):
        cmd_ingest(settings, "a", "b", client=client)
    conn = connect(settings.db_path)
    stage, received, api_calls = conn.execute(
        "SELECT stage, rows_received, api_calls FROM pipeline_runs"
    ).fetchone()
    assert stage == "ingest_failed"
    assert received == 2 * PAGE_LENGTH
    assert api_calls == 2
    conn.close()

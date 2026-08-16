"""Storage tests — idempotency is the Phase 1 exit criterion."""

import json

from conftest import bronze_doc
from gridpulse.convert.pipeline import Reject, convert_demand
from gridpulse.schemas.clean import DemandRecord
from gridpulse.storage import (
    connect,
    table_checksum,
    upsert_demand,
    write_quarantine,
)


def test_same_input_twice_produces_identical_table(tmp_path, demand_payload):
    # THE idempotency proof: re-running the transform over the same bronze
    # set leaves the silver table byte-identical.
    conn = connect(tmp_path / "t.db")
    records, rejects = convert_demand([bronze_doc(demand_payload)])
    assert not rejects
    upsert_demand(conn, records)
    first = table_checksum(conn, "demand_hourly")
    upsert_demand(conn, records)
    assert table_checksum(conn, "demand_hourly") == first
    conn.close()


def test_latest_fetch_wins_on_revision(tmp_path):
    conn = connect(tmp_path / "t.db")
    base = DemandRecord(
        region="ERCO", period_utc="2026-08-13T18:00:00+00:00",
        demand_mwh=1000, fetched_at="2026-08-15T00:00:00+00:00",
    )
    revised = base.model_copy(
        update={"demand_mwh": 2000, "fetched_at": "2026-08-15T12:00:00+00:00"}
    )
    upsert_demand(conn, [base])
    upsert_demand(conn, [revised])
    assert conn.execute("SELECT demand_mwh FROM demand_hourly").fetchone()[0] == 2000
    conn.close()


def test_older_fetch_cannot_downgrade_newer_value(tmp_path):
    # Replaying old bronze out of order must not resurrect stale values.
    conn = connect(tmp_path / "t.db")
    newer = DemandRecord(
        region="ERCO", period_utc="2026-08-13T18:00:00+00:00",
        demand_mwh=2000, fetched_at="2026-08-15T12:00:00+00:00",
    )
    stale = newer.model_copy(
        update={"demand_mwh": 1000, "fetched_at": "2026-08-15T00:00:00+00:00"}
    )
    upsert_demand(conn, [newer])
    upsert_demand(conn, [stale])
    assert conn.execute("SELECT demand_mwh FROM demand_hourly").fetchone()[0] == 2000
    conn.close()


def test_quarantine_preserves_raw_row_and_error(tmp_path):
    conn = connect(tmp_path / "t.db")
    reject = Reject(
        raw={"period": "garbage"}, error="period: bad", fetched_at="2026-08-15T00:00:00+00:00"
    )
    write_quarantine(conn, "run1", [reject])
    raw, error = conn.execute("SELECT raw, error FROM quarantine").fetchone()
    assert json.loads(raw) == {"period": "garbage"}
    assert "period" in error
    conn.close()


def test_table_checksum_rejects_unknown_table_with_valid_names(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        table_checksum(conn, "users; DROP TABLE demand_hourly")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "valid tables" in str(exc)
    finally:
        conn.close()

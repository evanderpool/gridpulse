"""Storage tests — idempotency is the Phase 1 exit criterion.

The survivorship guard is duplicated across both silver tables, so every
guard test runs against BOTH upserts (P2 code-review MED-1): one copy can
no longer regress while the suite stays green.
"""

import json
import sqlite3

import pytest

from conftest import bronze_doc
from gridpulse.convert.pipeline import Reject, convert_demand
from gridpulse.schemas.clean import DemandRecord, FuelMixRecord
from gridpulse.storage import (
    connect,
    table_checksum,
    upsert_demand,
    upsert_fuelmix,
    write_quarantine,
)


def make_demand(mwh: int, fetched: str, flags: str = "") -> DemandRecord:
    return DemandRecord(region="ERCO", period_utc="2026-08-13T18:00:00+00:00",
                        demand_mwh=mwh, quality_flags=flags, fetched_at=fetched)


def make_fuel(mwh: int, fetched: str, flags: str = "") -> FuelMixRecord:
    return FuelMixRecord(region="ERCO", period_utc="2026-08-13T18:00:00+00:00",
                         fueltype="SUN", generation_mwh=mwh, quality_flags=flags,
                         fetched_at=fetched)


GUARDED_TABLES = [
    pytest.param(upsert_demand, make_demand, "demand_hourly", "demand_mwh", id="demand"),
    pytest.param(upsert_fuelmix, make_fuel, "fuelmix_hourly", "generation_mwh", id="fuelmix"),
]

EARLY = "2026-08-15T00:00:00+00:00"
LATE = "2026-08-15T12:00:00+00:00"


@pytest.mark.parametrize("upsert,make,table,col", GUARDED_TABLES)
def test_guard_latest_fetch_wins_on_revision(tmp_path, upsert, make, table, col):
    conn = connect(tmp_path / "t.db")
    upsert(conn, [make(1000, EARLY)])
    upsert(conn, [make(2000, LATE)])
    assert conn.execute(f"SELECT {col} FROM {table}").fetchone()[0] == 2000
    conn.close()


@pytest.mark.parametrize("upsert,make,table,col", GUARDED_TABLES)
def test_guard_older_fetch_cannot_downgrade(tmp_path, upsert, make, table, col):
    # Replaying old bronze out of order must not resurrect stale values.
    conn = connect(tmp_path / "t.db")
    upsert(conn, [make(2000, LATE)])
    upsert(conn, [make(1000, EARLY)])
    assert conn.execute(f"SELECT {col} FROM {table}").fetchone()[0] == 2000
    conn.close()


@pytest.mark.parametrize("upsert,make,table,col", GUARDED_TABLES)
def test_guard_flag_only_change_writes_identical_replay_does_not(
    tmp_path, upsert, make, table, col
):
    # P2-HIGH-1 on both tables: policy evolution must reach stored rows.
    conn = connect(tmp_path / "t.db")
    upsert(conn, [make(-5, EARLY, flags="")])
    flagged = make(-5, EARLY, flags="some_flag")
    assert upsert(conn, [flagged]) == 1
    assert conn.execute(f"SELECT quality_flags FROM {table}").fetchone()[0] == "some_flag"
    assert upsert(conn, [flagged]) == 0  # byte-identical replay still counts 0
    conn.close()


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


def test_quarantine_preserves_raw_row_and_error(tmp_path):
    conn = connect(tmp_path / "t.db")
    reject = Reject(
        raw={"period": "garbage"}, error="period: bad", fetched_at=EARLY
    )
    write_quarantine(conn, "run1", [reject])
    raw, error = conn.execute("SELECT raw, error FROM quarantine").fetchone()
    assert json.loads(raw) == {"period": "garbage"}
    assert "period" in error
    conn.close()


def test_phase1_era_database_gains_quality_flags_column(tmp_path):
    # Additive migration: a DB created before Phase 2 lacks quality_flags;
    # connect() must add it without touching existing rows.
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE demand_hourly (region TEXT NOT NULL, period_utc TEXT NOT NULL, "
        "demand_mwh INTEGER NOT NULL, fetched_at TEXT NOT NULL, "
        "PRIMARY KEY (region, period_utc))"
    )
    old.execute("INSERT INTO demand_hourly VALUES ('ERCO', 'p', 100, 'f')")
    old.commit()
    old.close()

    conn = connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(demand_hourly)")}
    assert "quality_flags" in cols
    region, flags = conn.execute(
        "SELECT region, quality_flags FROM demand_hourly"
    ).fetchone()
    assert region == "ERCO" and flags == ""
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

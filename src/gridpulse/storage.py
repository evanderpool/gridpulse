"""SQLite storage: idempotent upserts, quarantine, and the run-metrics ledger.

Everything here is deterministic given its inputs — re-running the same
bronze set produces a byte-identical silver table, and a test proves it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .convert.pipeline import Reject
from .schemas.clean import DemandRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS demand_hourly (
    region      TEXT NOT NULL,
    period_utc  TEXT NOT NULL,
    demand_mwh  INTEGER NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (region, period_utc)
);
CREATE TABLE IF NOT EXISTS quarantine (
    run_id      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    raw         TEXT NOT NULL,
    error       TEXT NOT NULL
);
-- Transform re-reads ALL bronze every run; without this, one bad row becomes
-- one new quarantine row per run forever (review finding MED-2). raw is
-- canonical JSON (sort_keys), so it dedups reliably.
CREATE UNIQUE INDEX IF NOT EXISTS quarantine_dedup ON quarantine (fetched_at, raw);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id           TEXT NOT NULL,
    stage            TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    git_sha          TEXT NOT NULL,
    rows_received    INTEGER NOT NULL,
    rows_valid       INTEGER NOT NULL,
    rows_quarantined INTEGER NOT NULL,
    rows_upserted    INTEGER NOT NULL,
    api_calls        INTEGER NOT NULL,
    runtime_seconds  REAL NOT NULL,
    PRIMARY KEY (run_id, stage)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (and if needed initialize) the project database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_demand(conn: sqlite3.Connection, records: list[DemandRecord]) -> int:
    """Idempotent upsert keyed on (region, period_utc); latest fetch wins.

    The WHERE guard makes ordering irrelevant: an older fetch can never
    overwrite a newer one, even if bronze files are replayed out of order.
    """
    # Guard shape matters (review finding MED-3): strictly-newer always wins;
    # an equal-timestamp write fires only when the value differs, so an
    # identical replay counts 0 and the ledger can SEE idempotency. Do not
    # "simplify" to `>= AND value differs` — that skips the fetched_at
    # refresh and reopens the stale-downgrade hole.
    cur = conn.executemany(
        """
        INSERT INTO demand_hourly (region, period_utc, demand_mwh, fetched_at)
        VALUES (:region, :period_utc, :demand_mwh, :fetched_at)
        ON CONFLICT(region, period_utc) DO UPDATE SET
            demand_mwh = excluded.demand_mwh,
            fetched_at = excluded.fetched_at
        WHERE excluded.fetched_at > demand_hourly.fetched_at
           OR (excluded.fetched_at = demand_hourly.fetched_at
               AND excluded.demand_mwh != demand_hourly.demand_mwh)
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()
    return cur.rowcount


def write_quarantine(conn: sqlite3.Connection, run_id: str, rejects: list[Reject]) -> int:
    """Persist failed rows with their errors — quarantined, never dropped.

    Returns the number of NEWLY quarantined rows; re-seen failures are
    ignored (run_id records first-seen). That count is what the metrics
    ledger reports, so the quarantine rate can decay once data is clean.
    """
    cur = conn.executemany(
        "INSERT OR IGNORE INTO quarantine (run_id, fetched_at, raw, error) VALUES (?, ?, ?, ?)",
        [(run_id, r.fetched_at, json.dumps(r.raw, sort_keys=True), r.error) for r in rejects],
    )
    conn.commit()
    return cur.rowcount


def record_run(conn: sqlite3.Connection, row: dict) -> None:
    """Append one row to the metrics ledger (the improvement loop's raw data)."""
    conn.execute(
        """
        INSERT INTO pipeline_runs (run_id, stage, started_at, git_sha, rows_received,
            rows_valid, rows_quarantined, rows_upserted, api_calls, runtime_seconds)
        VALUES (:run_id, :stage, :started_at, :git_sha, :rows_received,
            :rows_valid, :rows_quarantined, :rows_upserted, :api_calls, :runtime_seconds)
        """,
        row,
    )
    conn.commit()


def table_checksum(conn: sqlite3.Connection, table: str) -> str:
    """Deterministic digest of a table's full contents (for idempotency tests)."""
    import hashlib

    if table not in {"demand_hourly", "quarantine", "pipeline_runs"}:
        raise ValueError(f"unknown table {table!r}")
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()

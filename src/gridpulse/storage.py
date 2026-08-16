"""SQLite storage: idempotent upserts, quarantine, and the run-metrics ledger.

Everything here is deterministic given its inputs — re-running the same
bronze set produces a byte-identical silver table, and a test proves it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TypedDict

from .convert.pipeline import Reject
from .schemas.clean import DemandRecord


class RunRow(TypedDict):
    """The metrics-ledger contract — every run appends exactly this shape."""

    run_id: str
    stage: str
    started_at: str
    git_sha: str
    rows_received: int
    rows_valid: int
    rows_quarantined: int
    rows_upserted: int
    api_calls: int
    runtime_seconds: float

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
    """Open (and if needed initialize) the project database.

    Callers own the connection — wrap in ``contextlib.closing`` (the CLI
    does) or close explicitly.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def read_bronze_docs(bronze_dir: Path) -> tuple[list[dict], list[Reject]]:
    """Load pipeline-written bronze documents; unreadable files become Rejects.

    Only ``demand_*.json`` is considered ours — a stray or truncated file is
    quarantined at document level, never allowed to abort a run.
    """
    docs: list[dict] = []
    rejects: list[Reject] = []
    for path in sorted(bronze_dir.glob("demand_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = doc["fetched_at"]
            data = doc["payload"]["response"]["data"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            rejects.append(Reject(
                raw={"bronze_file": path.name},
                error=f"unreadable bronze document: {exc!r}",
                fetched_at="",
            ))
            continue
        if isinstance(fetched_at, str) and isinstance(data, list):
            docs.append(doc)
        else:
            rejects.append(Reject(
                raw={"bronze_file": path.name},
                error="bronze document has wrong types for fetched_at/data",
                fetched_at="",
            ))
    return docs, rejects


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


def record_run(conn: sqlite3.Connection, row: RunRow) -> None:
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
    allowed = {"demand_hourly", "quarantine", "pipeline_runs"}
    if table not in allowed:
        raise ValueError(f"unknown table {table!r} — valid tables: {sorted(allowed)}")
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()

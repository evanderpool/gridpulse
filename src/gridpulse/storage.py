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
from .schemas.clean import DemandRecord, FuelMixRecord


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
    region        TEXT NOT NULL,
    period_utc    TEXT NOT NULL,
    demand_mwh    INTEGER NOT NULL,
    quality_flags TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (region, period_utc)
);
CREATE TABLE IF NOT EXISTS fuelmix_hourly (
    region         TEXT NOT NULL,
    period_utc     TEXT NOT NULL,
    fueltype       TEXT NOT NULL,
    generation_mwh INTEGER NOT NULL,
    quality_flags  TEXT NOT NULL DEFAULT '',
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (region, period_utc, fueltype)
);
CREATE TABLE IF NOT EXISTS metrics_hourly (
    region          TEXT NOT NULL,
    period_utc      TEXT NOT NULL,
    renewable_share REAL,
    net_load_mwh    INTEGER NOT NULL,
    ramp_mwh_per_h  INTEGER,
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
    _ensure_column(conn, "demand_hourly", "quality_flags", "TEXT NOT NULL DEFAULT ''")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Additive migration: databases created before a column gain it here.

    CREATE TABLE IF NOT EXISTS never alters existing tables, so schema
    additions must be applied explicitly to Phase-1-era databases.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()


def read_bronze_docs(bronze_dir: Path, dataset: str = "demand") -> tuple[list[dict], list[Reject]]:
    """Load pipeline-written bronze documents; unreadable files become Rejects.

    Only ``<dataset>_*.json`` is considered ours — a stray or truncated file
    is quarantined at document level, never allowed to abort a run.
    """
    docs: list[dict] = []
    rejects: list[Reject] = []
    for path in sorted(bronze_dir.glob(f"{dataset}_*.json")):
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
    # Guard shape matters (review findings P1-MED-3, P2-HIGH-1): strictly-newer
    # always wins; an equal-timestamp write fires when the value OR the flags
    # differ — flags must be comparable or a policy change could never repair
    # already-stored rows. A byte-identical replay still counts 0, so the
    # ledger can SEE idempotency. Do not "simplify" to `>= AND differs` —
    # that skips the fetched_at refresh and reopens the stale-downgrade hole.
    cur = conn.executemany(
        """
        INSERT INTO demand_hourly (region, period_utc, demand_mwh, quality_flags, fetched_at)
        VALUES (:region, :period_utc, :demand_mwh, :quality_flags, :fetched_at)
        ON CONFLICT(region, period_utc) DO UPDATE SET
            demand_mwh = excluded.demand_mwh,
            quality_flags = excluded.quality_flags,
            fetched_at = excluded.fetched_at
        WHERE excluded.fetched_at > demand_hourly.fetched_at
           OR (excluded.fetched_at = demand_hourly.fetched_at
               AND (excluded.demand_mwh != demand_hourly.demand_mwh
                    OR excluded.quality_flags != demand_hourly.quality_flags))
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()
    return cur.rowcount


def upsert_fuelmix(conn: sqlite3.Connection, records: list[FuelMixRecord]) -> int:
    """Idempotent upsert keyed on (region, period_utc, fueltype); same guard
    shape as demand — see upsert_demand's comment for why."""
    cur = conn.executemany(
        """
        INSERT INTO fuelmix_hourly
            (region, period_utc, fueltype, generation_mwh, quality_flags, fetched_at)
        VALUES (:region, :period_utc, :fueltype, :generation_mwh, :quality_flags, :fetched_at)
        ON CONFLICT(region, period_utc, fueltype) DO UPDATE SET
            generation_mwh = excluded.generation_mwh,
            quality_flags = excluded.quality_flags,
            fetched_at = excluded.fetched_at
        WHERE excluded.fetched_at > fuelmix_hourly.fetched_at
           OR (excluded.fetched_at = fuelmix_hourly.fetched_at
               AND (excluded.generation_mwh != fuelmix_hourly.generation_mwh
                    OR excluded.quality_flags != fuelmix_hourly.quality_flags))
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()
    return cur.rowcount


def read_demand(conn: sqlite3.Connection) -> list[dict]:
    """Silver demand rows in the shape the backend contract expects."""
    return [
        {"region": r[0], "period_utc": r[1], "demand_mwh": r[2]}
        for r in conn.execute(
            "SELECT region, period_utc, demand_mwh FROM demand_hourly ORDER BY region, period_utc"
        )
    ]


def read_fuelmix(conn: sqlite3.Connection) -> list[dict]:
    """Silver fuel-mix rows in the shape the backend contract expects."""
    return [
        {"region": r[0], "period_utc": r[1], "fueltype": r[2], "generation_mwh": r[3]}
        for r in conn.execute(
            "SELECT region, period_utc, fueltype, generation_mwh FROM fuelmix_hourly "
            "ORDER BY region, period_utc, fueltype"
        )
    ]


def replace_metrics(conn: sqlite3.Connection, metrics: list[dict]) -> None:
    """Full-recompute write of the gold table, atomically.

    Gold is derived state (see convert/derive.py) — replaced whole, never
    patched, so it can never drift from silver.
    """
    with conn:
        conn.execute("DELETE FROM metrics_hourly")
        conn.executemany(
            """
            INSERT INTO metrics_hourly
                (region, period_utc, renewable_share, net_load_mwh, ramp_mwh_per_h)
            VALUES (:region, :period_utc, :renewable_share, :net_load_mwh, :ramp_mwh_per_h)
            """,
            metrics,
        )


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
    allowed = {"demand_hourly", "fuelmix_hourly", "metrics_hourly", "quarantine", "pipeline_runs"}
    if table not in allowed:
        raise ValueError(f"unknown table {table!r} — valid tables: {sorted(allowed)}")
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()

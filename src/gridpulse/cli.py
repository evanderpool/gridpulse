"""CLI: ``ingest`` (API → bronze) and ``transform`` (bronze → SQLite).

Stages are separate commands on purpose: transform never touches the network,
so any conversion bug is reproducible offline from the exact bronze bytes
that triggered it.
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import subprocess
import time
from datetime import datetime, timezone

from .client import EiaClient, write_bronze
from .config import Settings
from .convert.pipeline import convert_demand
from .storage import connect, record_run, upsert_demand, write_quarantine

log = logging.getLogger("gridpulse")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def _git_sha() -> str:
    """Current commit, recorded per run so metric changes are attributable."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _log(run_id: str, stage: str, **fields: object) -> None:
    """One structured line per event, greppable by run_id."""
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    log.info("run_id=%s stage=%s %s", run_id, stage, kv)


def cmd_ingest(settings: Settings, start: str, end: str, client: EiaClient | None = None) -> int:
    """Fetch a demand window and persist it verbatim to bronze."""
    run_id = _run_id()
    t0 = time.monotonic()
    client = client or EiaClient(settings)
    pages = client.fetch_demand_window(start, end)
    window = f"{start}_{end}".replace(":", "")
    paths = write_bronze(pages, run_id, settings.bronze_dir, window)
    rows = sum(len(p["response"]["data"]) for p in pages)
    conn = connect(settings.db_path)
    record_run(conn, {
        "run_id": run_id, "stage": "ingest",
        "started_at": datetime.now(timezone.utc).isoformat(), "git_sha": _git_sha(),
        "rows_received": rows, "rows_valid": 0, "rows_quarantined": 0,
        "rows_upserted": 0, "api_calls": client.requests_made,
        "runtime_seconds": round(time.monotonic() - t0, 3),
    })
    _log(run_id, "ingest", pages=len(paths), rows=rows, api_calls=client.requests_made)
    return 0


def cmd_transform(settings: Settings) -> int:
    """Convert every bronze document into the silver table. Idempotent."""
    run_id = _run_id()
    t0 = time.monotonic()
    docs = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(settings.bronze_dir.glob("*.json"))
    ]
    records, rejects = convert_demand(docs)
    conn = connect(settings.db_path)
    upserted = upsert_demand(conn, records)
    write_quarantine(conn, run_id, rejects)
    record_run(conn, {
        "run_id": run_id, "stage": "transform",
        "started_at": datetime.now(timezone.utc).isoformat(), "git_sha": _git_sha(),
        "rows_received": len(records) + len(rejects), "rows_valid": len(records),
        "rows_quarantined": len(rejects), "rows_upserted": upserted,
        "api_calls": 0, "runtime_seconds": round(time.monotonic() - t0, 3),
    })
    _log(run_id, "transform", valid=len(records), quarantined=len(rejects), upserted=upserted)
    if rejects:
        _log(run_id, "transform", note=f"{len(rejects)} rows quarantined - "
             f"inspect: SELECT error, raw FROM quarantine WHERE run_id='{run_id}'")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m gridpulse``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs full request URLs at INFO — including the api_key query
    # param. In CI those logs are public, so this stays WARNING forever.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="gridpulse")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest", help="fetch a demand window into bronze")
    p_ingest.add_argument("--start", required=True, help="UTC hour, e.g. 2026-08-13T00")
    p_ingest.add_argument("--end", required=True, help="UTC hour, e.g. 2026-08-14T00")
    sub.add_parser("transform", help="convert bronze into SQLite (idempotent)")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.command == "ingest":
        return cmd_ingest(settings, args.start, args.end)
    return cmd_transform(settings)

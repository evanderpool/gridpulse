"""CLI: ``ingest`` (API → bronze) and ``transform`` (bronze → SQLite).

Stages are separate commands on purpose: transform never touches the network,
so any conversion bug is reproducible offline from the exact bronze bytes
that triggered it. Functions here orchestrate only — parsing, conversion,
and persistence live in their modules.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone

from .client import EiaClient, PullBudgetExceeded, write_bronze
from .config import Settings
from .convert.backends import get_backend
from .convert.derive import derive_to_gold
from .convert.pipeline import convert_demand, convert_fuelmix
from .storage import (
    RunRow,
    connect,
    read_bronze_docs,
    record_run,
    upsert_demand,
    upsert_fuelmix,
    write_quarantine,
)

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
    """One structured line per event, greppable by run_id.

    Values containing spaces are quoted so ``key=value`` splitting stays
    parseable even for free-text notes.
    """
    def fmt(v: object) -> str:
        s = str(v)
        return f'"{s}"' if " " in s else s

    kv = " ".join(f"{k}={fmt(v)}" for k, v in fields.items())
    log.info("run_id=%s stage=%s %s", run_id, stage, kv)


DATASETS = ("demand", "fuelmix")


def cmd_ingest(
    settings: Settings,
    start: str,
    end: str,
    client: EiaClient | None = None,
    datasets: tuple[str, ...] = DATASETS,
) -> int:
    """Fetch a window of each requested dataset and persist verbatim to bronze."""
    run_id = _run_id()
    t0 = time.monotonic()
    own_client = client is None
    client = client or EiaClient(settings)
    window = f"{start}_{end}".replace(":", "")
    fetch = {"demand": client.fetch_demand_window, "fuelmix": client.fetch_fuelmix_window}
    rows = 0
    try:
        for dataset in datasets:
            try:
                pages = fetch[dataset](start, end)
            except PullBudgetExceeded as exc:
                # Budget spent — persist what it bought and ledger the failure,
                # so the run that exhausted the API budget is attributable.
                partial_rows = 0
                if exc.pages:
                    write_bronze(exc.pages, run_id, settings.bronze_dir,
                                 window + "_partial", dataset)
                    partial_rows = sum(len(p["response"]["data"]) for p in exc.pages)
                    _log(run_id, "ingest",
                         note=f"budget hit on {dataset}: {len(exc.pages)} partial pages saved")
                with contextlib.closing(connect(settings.db_path)) as conn:
                    record_run(conn, _run_row(
                        run_id, "ingest_failed", rows_received=rows + partial_rows,
                        api_calls=client.requests_made, t0=t0,
                    ))
                raise
            paths = write_bronze(pages, run_id, settings.bronze_dir, window, dataset)
            dataset_rows = sum(len(p["response"]["data"]) for p in pages)
            rows += dataset_rows
            _log(run_id, "ingest", dataset=dataset, pages=len(paths), rows=dataset_rows)
        with contextlib.closing(connect(settings.db_path)) as conn:
            record_run(conn, _run_row(
                run_id, "ingest", rows_received=rows,
                api_calls=client.requests_made, t0=t0,
            ))
        _log(run_id, "ingest", rows=rows, api_calls=client.requests_made)
        return 0
    finally:
        if own_client:
            client.close()


def cmd_transform(settings: Settings) -> int:
    """Convert every bronze document into the silver tables. Idempotent."""
    run_id = _run_id()
    t0 = time.monotonic()
    converters = {"demand": (convert_demand, upsert_demand),
                  "fuelmix": (convert_fuelmix, upsert_fuelmix)}
    valid = quarantined = upserted = 0
    with contextlib.closing(connect(settings.db_path)) as conn:
        for dataset, (convert, upsert) in converters.items():
            docs, doc_rejects = read_bronze_docs(settings.bronze_dir, dataset)
            records, row_rejects = convert(docs)
            rejects = doc_rejects + row_rejects
            upserted += upsert(conn, records)
            quarantined += write_quarantine(conn, run_id, rejects)
            valid += len(records)
        record_run(conn, _run_row(
            run_id, "transform", rows_received=valid + quarantined,
            rows_valid=valid, rows_quarantined=quarantined,
            rows_upserted=upserted, t0=t0,
        ))
    _log(run_id, "transform", valid=valid, quarantined=quarantined, upserted=upserted)
    if quarantined:
        _log(run_id, "transform", note=f"{quarantined} NEW rows quarantined - "
             f"inspect: SELECT error, raw FROM quarantine WHERE run_id='{run_id}'")
    return 0


def cmd_derive(settings: Settings) -> int:
    """Recompute the gold metrics table from silver via the configured backend."""
    run_id = _run_id()
    t0 = time.monotonic()
    backend = get_backend(os.environ.get("GRIDPULSE_BACKEND", "polars"))
    with contextlib.closing(connect(settings.db_path)) as conn:
        written = derive_to_gold(conn, backend)
        record_run(conn, _run_row(
            run_id, "derive", rows_valid=written, rows_upserted=written, t0=t0,
        ))
    _log(run_id, "derive", backend=backend.name, metrics_rows=written)
    return 0


def _run_row(
    run_id: str,
    stage: str,
    *,
    t0: float,
    rows_received: int = 0,
    rows_valid: int = 0,
    rows_quarantined: int = 0,
    rows_upserted: int = 0,
    api_calls: int = 0,
) -> RunRow:
    """Assemble a complete metrics-ledger row (see storage.RunRow)."""
    return RunRow(
        run_id=run_id, stage=stage,
        started_at=datetime.now(timezone.utc).isoformat(), git_sha=_git_sha(),
        rows_received=rows_received, rows_valid=rows_valid,
        rows_quarantined=rows_quarantined, rows_upserted=rows_upserted,
        api_calls=api_calls, runtime_seconds=round(time.monotonic() - t0, 3),
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``gridpulse`` / ``python -m gridpulse``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs full request URLs at INFO — including the api_key query
    # param. In CI those logs are public, so this stays WARNING forever.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="gridpulse")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest", help="fetch a window of each dataset into bronze")
    p_ingest.add_argument("--start", required=True, help="UTC hour, e.g. 2026-08-13T00")
    p_ingest.add_argument("--end", required=True, help="UTC hour, e.g. 2026-08-14T00")
    p_ingest.add_argument("--dataset", choices=[*DATASETS, "both"], default="both",
                          help="which dataset(s) to fetch (default: both)")
    sub.add_parser("transform", help="convert bronze into SQLite (idempotent)")
    sub.add_parser("derive", help="recompute gold metrics from silver "
                                  "(backend via GRIDPULSE_BACKEND, default polars)")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.command == "ingest":
        datasets = DATASETS if args.dataset == "both" else (args.dataset,)
        return cmd_ingest(settings, args.start, args.end, datasets=datasets)
    if args.command == "derive":
        return cmd_derive(settings)
    return cmd_transform(settings)

# GridPulse

**A live view of the U.S. electricity grid** — hourly demand and fuel mix for
four major grid regions (ERCOT, CAISO, MISO, PJM), pulled from the
[EIA open-data API v2](https://www.eia.gov/opendata/), tamed by a documented
deterministic conversion tool, and published as a self-refreshing report.

> **Status: Phase 0 — skeleton.** The EIA source has been validated live for
> all three questions below (2026-08-15); pipeline code lands in Phase 1.
> This README is written before the code on purpose: it is the spec.

## The three questions

The whole product exists to answer exactly three business-style questions.
Anything that doesn't serve one of them is out of scope.

1. **Renewables share** — What share of each region's electricity came from
   wind and solar this month, and how has that mix shifted year over year?
2. **Peaks** — When does each grid peak, and how do daily demand curves differ
   across regions and seasons?
3. **The duck curve** — How deep is the midday net-load valley and how steep is
   the evening ramp in solar-heavy grids like California?

## Architecture

Six stages, one-directional flow. Each stage reads only the previous stage's
output — the report never touches the API.

```mermaid
flowchart LR
    A[Ingest\nEIA API v2, retry/backoff] --> B[Validate\nPydantic at the boundary]
    B --> C[Convert\nUTC, dedupe, quality flags]
    C --> D[Store\nSQLite + Parquet]
    D --> E[Analyze\nDuckDB gold marts]
    E --> F[Report\nstatic HTML on Pages]
    B -. failing records .-> Q[(Quarantine\nwith error attached)]
```

- **Bronze** — every API response saved verbatim, stamped with fetch time.
  Any conversion bug can be replayed against history without re-hitting the
  API. Bronze lives on the orphan `data` branch, never on `main`.
- **Silver** — validated, converted, deduplicated tables (SQLite for upserts,
  Parquet for analytics).
- **Gold** — aggregates that answer the three questions, plus a per-run
  metrics ledger (row counts, quarantine rate, freshness, git SHA).

### Module map

```
src/gridpulse/
  config.py          settings, API params, canonical units (env-driven)
  client.py          EIA calls, retry/backoff, pagination, bronze writes
  schemas/           raw.py + clean.py — Pydantic models
  convert/
    backends/        base.py (interface) · polars_backend.py · pandas [stretch]
    temporal.py      timestamp parsing, UTC normalization
    flatten.py       nested JSON → tabular records
    quality.py       null/outlier policy, quality flags, quarantine
    dedupe.py        natural-key dedup + survivorship (latest fetch wins)
    derive.py        renewable share, net load, ramp rate
    pipeline.py      orchestrates; the one public entry point
  storage.py         bronze/silver/gold writers, upserts
  analyze.py         gold aggregates, run-level quality metrics
  cli.py             ingest · transform · report · backfill
```

## Design decisions

- **Deterministic core.** The conversion tool is standalone, testable,
  reproducible code — not an AI call. (An optional AI "analyst notes"
  paragraph at the reporting edge is a stretch item, clearly labeled.)
- **Deliberately absent:** Airflow, Docker, Postgres, dbt. One DAG with one
  path doesn't need an orchestrator; right-sizing is the point. GitHub
  Actions cron (2×/day) is the scheduler.
- **$0 running cost, enforced in code:** incremental fetch windows, a
  `MAX_REQUESTS_PER_RUN` hard cap, exponential backoff with a circuit
  breaker. The report and dashboard never call the API.
- **Nothing fails silently:** failing records are quarantined with the error
  attached, quality thresholds auto-open GitHub Issues, and every run appends
  to a metrics ledger keyed by git SHA so improvements are attributable.

## Known data quirks (validated live, 2026-08-15)

Captured during pre-build validation; each is a named test fixture in
`tests/fixtures/`:

| Quirk | Consequence |
|---|---|
| CAISO solar reports **negative values at night** (≈ −60 MWh, station service) | naive renewable-share math ingests negative generation → explicit null/outlier policy |
| Fuel-type vocabulary **differs per region** (ERCOT reports `BAT`, CAISO sample did not) | schemas must not hardcode one fuel list |
| Every response carries a generic 5,000-row "incomplete return" warning | warnings ≠ errors; pagination is mandatory |
| Hourly periods are **UTC** (`2026-08-13T18`) | normalize internally, localize only at the reporting edge |

## Scope

- **Regions:** ERCOT (`ERCO`), CAISO (`CISO`), MISO (`MISO`), PJM (`PJM`)
- **Backfill window:** 13 months (enables year-over-year with one month of slack)
- **Cadence:** 2 pulls/day, incremental windows with revision overlap

## Roadmap

| Phase | Ships | Status |
|---|---|---|
| 0 | Repo skeleton, validated source, fixtures, CI | ✅ this commit |
| 1 | Vertical slice: ingest → bronze → validate → convert → SQLite, idempotency proven by test | next |
| 2 | Full conversion suite, quarantine, metrics ledger | |
| 3 | Gold marts, HTML report, Actions cron, Pages — **MVP ship** | |
| 4+ | Stretch, strictly in order: Streamlit dashboard → quality panel → pandas backend → weather join → AI notes → suggestion engine | |

## Running locally

```
pip install -e .[dev]
pytest
```

Pipeline commands arrive in Phase 1. An EIA API key (free) goes in `.env`
(gitignored) as `EIA_API_KEY=...` — never in the repo, and a GitHub Actions
secret in CI.

## License

MIT

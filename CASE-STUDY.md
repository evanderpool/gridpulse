# GridPulse: a data pipeline that proves its own work

**A fully automated system that turns the US government's raw energy feed into
a self-refreshing public analysis — where every number can be traced to a
stored original, every update is on an audit ledger, and the whole thing runs
for $0 a month.**

Built and directed by **Erick Vanderpool** ·
[source](https://github.com/evanderpool/gridpulse) ·
[**live site →**](https://evanderpool.github.io/gridpulse/)

| 2 days | 359K | 0 | 83 | $0 |
|---|---|---|---|---|
| zero code → live, automated product | rows through the pipeline | rows lost or silently dropped | automated tests, all passing | monthly infrastructure cost |

---

## The problem

Public data projects rot. The usual arc: pull an API, make a chart, post it —
and within a month the data is stale, the pipeline breaks silently, and
nobody can say where any number came from. Worse, real-world feeds are
genuinely messy: the US energy feed revises past hours for up to two days,
serves numbers as text, and reports *negative* solar generation at night —
and a pipeline that hasn't decided what to do about each of those is quietly
wrong in ways no chart reveals.

GridPulse is built on a different premise: **treat a public dashboard like a
production data system.** Raw inputs are kept forever, every transformation
is tested and replayable, odd values are labeled instead of "fixed," and the
pipeline publishes its own audit trail on the page it produces.

## The product

Hourly electricity demand and generation mix for four major US grids —
California (CAISO), Texas (ERCOT), the Midwest (MISO), and the Mid-Atlantic
(PJM) — pulled from the [EIA open-data API](https://www.eia.gov/opendata/),
thirteen months deep, refreshed automatically twice a day. Deliberately
served as **two surfaces from one pipeline**:

- **The product page** — a designed, interactive static site: pick a time
  frame, get computed plain-English findings ("Texas used 27.6% more power
  than the window before"), explore the famous duck curve for any recent
  day, and download the exact data slice you're looking at. No backend, no
  login, loads instantly.
- **The internal tool** — a Streamlit workbench with the same data, plus an
  **AI analyst**: ask any question and a model researches it by writing
  read-only SQL against the pipeline's database, then answers with every
  query it ran shown on screen.

The two surfaces are the point: the same engineering serves a polished
user experience *and* a functional analyst tool, and knowing which audience
gets which is part of the craft.

## How every number is made

```
  EIA API v2        (hourly demand + generation by fuel; revises the
       │             recent past; budgeted: a hard cap on requests per run)
       ▼
  Bronze            (every API response stored verbatim, gzipped, stamped
       │             with fetch time — any bug can be replayed against the
       │             exact original bytes, forever)
       ▼
  ╔══════════════════════════════════════════════════════════╗
  ║  QUALITY GATE — every record, three checks               ║
  ║  · malformed → QUARANTINED with the reason, never lost   ║
  ║  · odd-but-real → KEPT, with a named flag                ║
  ║    (negative night solar; batteries charging)            ║
  ║  · valid → converted to clean UTC-normalized rows        ║
  ╚══════════════════════════════════════════════════════════╝
       │
       ▼
  Silver            (idempotent upserts; a revision of a past hour wins by
       │             fetch time — replaying old data can never overwrite
       │             newer truth, and an identical replay changes 0 rows)
       ▼
  Gold              (renewable share, net load, ramp rates — recomputed
       │             whole from silver every run, so derived data can
       │             never drift from its source)
       ▼
  Published         (the site is compiled from gold at build time — the
                     page itself never calls the API and holds no keys)
```

Every run appends a row to a **ledger** — rows received, validated,
quarantined, written, API calls used, runtime, and the exact git commit of
the code that ran — published on the live site's Data Quality view. A replay
that changed nothing shows `upserted 0` in public: idempotency you can see,
not just a claim in a README.

## Measured, not claimed

- **Idempotency is proven by test and visible in production.** The suite
  replays identical data and asserts a byte-identical database; the live
  ledger shows the `0`s.
- **The metric layer runs on two engines** — Polars and pandas — behind one
  written contract, and a test runs the *same cases against both*. Held to a
  stronger bar on real data: both engines produced **byte-identical output
  over all 38,580 metric rows** computed from 356K inputs. The swap is one
  environment variable.
- **The analysis states its comparisons.** Every finding on the site is
  arithmetic against two baselines — the preceding window of equal length,
  and the same window one year earlier — which is why it can say things like
  *"Texas's renewable share is up 10.1 points year over year (31.9% →
  42.0%)"* with the exact windows named.
- **Scale numbers carry their receipts.** 13 months × 4 grids = 9,600+
  hours held; 359K rows through the quality gate with **zero quarantined**
  — not because the gate is loose (the tests feed it malformed rows and
  watch them land in quarantine) but because the upstream data, once its
  quirks are handled, is clean.

## The quirks are the story

Real-world data engineering is deciding what to do with the weird stuff.
Every quirk below was found in the actual feed, decided about in writing,
and pinned with a test and a captured fixture:

- **Solar panels read negative at night** (−57 MWh, station equipment
  drawing power). Naive math would ingest negative generation. Policy:
  kept, flagged `negative_generation`, clipped to zero only inside metric
  math — the observed value survives in silver.
- **Battery charging looks like negative generation too** — but it's a
  routine daily event, not an anomaly. It gets its **own** flag
  (`storage_charging`), so the anomaly flag keeps its diagnostic meaning.
  14,115 and 9,071 rows respectively carry these labels today.
- **The API serves totals as text** (`"100"`, not `100`) and reports the
  full result-set size, not the page's — pagination that trusted either
  would loop or stall. The tests deliberately speak the wire format so a
  future "cleanup" that breaks it fails the suite.
- **Past hours get revised for ~48 hours.** Every scheduled pull re-reads a
  48-hour overlap, and survivorship is by fetch time with a guard that makes
  out-of-order replays harmless.
- **The API echoes your secret key back inside every response.** Bronze
  files are scrubbed at write time; three separate key-leak vectors (response
  echo, HTTP client logging, exception messages) were found and closed the
  same day each appeared — each pinned by a test that scans for leaks.

## From zero to live, in order

The build ran in gated phases, and the gates did real work:

| Phase | Shipped | The gate that mattered |
|---|---|---|
| **0** | Repo skeleton, CI, captured real payloads as named test fixtures | Data availability was validated against the live API **before any code** — the riskiest question (is solar data granular enough for the duck curve?) answered first |
| **1** | The vertical slice: API → bronze → validate → convert → database, idempotency proven | Two adversarial reviews (21 findings, one HIGH: a valid-looking impossible date could crash ingestion for good) — all fixed and pinned |
| **2** | Fuel mix, the written quality policy, dual-engine metric layer | The review caught that a quality-flag fix could *never reach already-stored rows* — the repair ran live and fixed 27 rows on the spot |
| **3** | Actions cron 2×/day, the self-refreshing site, `v1.0-baseline` tag | First CI run failed — offline stages demanded the API key. The fix made the architecture claim real: transform/derive/report run keyless *by design* |
| **+** | 13-month backfill, the interactive product page, the AI analyst | The backfill blew the repo's 50MB data budget (132MB) — bronze went gzip-compact, exactly as the budget's design note said it would |

Six sequential adversarial review passes ran across the build — independent
sessions instructed to attack the work. They produced **40+ verified
findings, every one fixed and pinned as a named regression test** in
[`tests/test_review_findings.py`](tests/test_review_findings.py), so no
reviewed bug can quietly return.

## Directed across the stack

I directed the build; AI sessions executed it. The decisions that shaped the
system reach into every layer, and each was mine to make and defend:

- **Budget as architecture.** The API pull budget is a hard cap counted per
  request *including retries* — a flapping server cannot spend past it, and
  when the cap dies mid-window, the pages already paid for are persisted
  rather than discarded.
- **Right-sizing as a stated decision.** No Airflow, no Docker, no Postgres,
  no dbt: one DAG with one path doesn't need an orchestrator. GitHub Actions
  cron is the scheduler; SQLite plus gzipped files are the store; the README
  says so and says why.
- **Trust boundaries.** The public page is compiled ahead of time and holds
  no keys — it *cannot* call the API. The AI analyst holds one capability: a
  read-only SQL sandbox (SELECT-only, single statement, row-capped, opened
  read-only at the connection level) — and shows every query it runs.
- **Conventions decided in writing.** Battery charge is excluded from the
  renewable-share denominator (gross generation) — a defensible choice, but
  the point is it's *written in the metric contract*, not buried in code.
  Rounding is half-to-even, named explicitly, pinned with an exact-half test
  so a future engine can't drift by a decimal.
- **The experience is part of the engineering.** Region codes became place
  names; every unit carries a human anchor (1 GWh ≈ one hour of power for
  750,000 homes); the duck curve is explained in words next to its chart;
  and the design was verified by screenshotting the built page and looking
  at it — which caught a real rendering flaw before any user saw it.

## Check everything on this page

This document follows the pipeline's own rule: no claim without a source you
can inspect.

- **The ledger and quality flags** are on the
  [live site's Data Quality view](https://evanderpool.github.io/gridpulse/#quality)
  — including the `upserted 0` replays.
- **The tests** run with one command (`python -m pytest`) in
  [the repository](https://github.com/evanderpool/gridpulse); the review
  findings are pinned in
  [`tests/test_review_findings.py`](tests/test_review_findings.py).
- **The quality policy** is a written table in
  [`src/gridpulse/convert/quality.py`](src/gridpulse/convert/quality.py);
  the metric contract in
  [`src/gridpulse/convert/backends/base.py`](src/gridpulse/convert/backends/base.py).
- **The survivorship guard** — with the comment explaining why it must not
  be "simplified" — is in
  [`src/gridpulse/storage.py`](src/gridpulse/storage.py).
- **The automation** is
  [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml); every
  raw payload lives on the
  [`data` branch](https://github.com/evanderpool/gridpulse/tree/data).
- **The two-day timeline** is the public commit history.

---

**Erick Vanderpool** — data analyst and AI engineer. GridPulse is a
portfolio project of
[Artificial Management](https://evanderpool.github.io/artificial-management/).
[github.com/evanderpool](https://github.com/evanderpool)

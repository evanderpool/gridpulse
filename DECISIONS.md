# Decisions

Human QC calls and the rules they produce. Append-only.

[2026-08-15] Phase 0. Source validated live before any code (red-team order):
all 3 questions answerable from EIA API v2. Quirks found and pinned as
fixtures: negative nighttime solar (CISO), per-region fuel vocabularies
(ERCO has BAT), generic 5000-row warning on every response, UTC periods.
Regions locked: ERCO/CISO/MISO/PJM. Backfill window: 13 months. Data goes on
the orphan `data` branch. Pre-ship CI is ruff+pytest only — docstring lint
and typecheck gates turn on at MVP ship.

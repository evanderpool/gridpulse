"""Silver → gold: derived metrics through the configured backend.

Gold is derived state: it is recomputed in full from silver on every run
(deterministic — same silver, same backend, same gold), never incrementally
patched. That keeps replay-from-bronze the universal repair path.
"""

from __future__ import annotations

import sqlite3

from ..storage import read_demand, read_fuelmix, replace_metrics
from .backends.base import Backend


def derive_to_gold(conn: sqlite3.Connection, backend: Backend) -> int:
    """Recompute the metrics_hourly gold table; returns rows written."""
    metrics = backend.derive_metrics(read_demand(conn), read_fuelmix(conn))
    replace_metrics(conn, metrics)
    return len(metrics)

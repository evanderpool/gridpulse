"""The backend contract — the ONE interface every dataframe engine implements.

The metric definitions live here, not in any engine, so every backend is
held to the identical contract (tests/test_backend_contract.py runs the
same cases against each):

- Inputs: demand rows ``{region, period_utc, demand_mwh}`` and fuel-mix rows
  ``{region, period_utc, fueltype, generation_mwh}`` from the silver tables.
- Negative generation (flagged, kept in silver) is CLIPPED TO 0 for metric
  math. This is a stated convention, not an accident: station-service draw
  is not negative supply, and battery CHARGING (BAT < 0, a routine daily
  event) is likewise excluded — the denominator is GROSS generation, not
  generation net of storage charge.
- ``renewable_share`` = (SUN + WND) / total gross generation; ``None`` when
  total generation <= 0 (never a division error). Rounded to 4 decimals,
  HALF-TO-EVEN (Python ``round`` semantics) — named explicitly so every
  backend produces byte-identical output on exact halves.
- ``net_load_mwh`` = demand − (SUN + WND): the duck-curve quantity.
- ``ramp_mwh_per_h`` = net load minus the PREVIOUS CONSECUTIVE hour's net
  load per region; ``None`` at series starts and across gaps — a gap must
  not fabricate a ramp.
- Only (region, period) pairs present in BOTH inputs produce a row; output
  is sorted by (region, period_utc).
- Quality flags are deliberately NOT inputs: the read shape carries values
  only, and flagged values enter metric math AS OBSERVED — the single
  exception is the negative-clip above, which is re-derivable from sign.
  In particular an ``extreme_value``-flagged row flows into gold unaltered;
  surfacing flags is the report's job (from silver), not gold's. A
  flag-aware gold policy would require widening the read shape — a contract
  change, not a backend change.
"""

from __future__ import annotations

import os
from typing import Protocol, TypedDict

RENEWABLE_FUELS = ("SUN", "WND")
SHARE_DECIMALS = 4
DEFAULT_BACKEND = "polars"
BACKEND_ENV_VAR = "GRIDPULSE_BACKEND"


class DemandRow(TypedDict):
    """Backend input: one silver demand observation (flags excluded — see above)."""

    region: str
    period_utc: str
    demand_mwh: int


class FuelMixRow(TypedDict):
    """Backend input: one silver generation observation (flags excluded)."""

    region: str
    period_utc: str
    fueltype: str
    generation_mwh: int


class MetricRow(TypedDict):
    """Backend output: one gold metric row."""

    region: str
    period_utc: str
    renewable_share: float | None
    net_load_mwh: int
    ramp_mwh_per_h: int | None


class Backend(Protocol):
    """A dataframe engine capable of computing the derived metrics."""

    name: str

    def derive_metrics(
        self, demand_rows: list[DemandRow], fuelmix_rows: list[FuelMixRow]
    ) -> list[MetricRow]:
        """Compute the metric rows per the contract in this module's docstring."""
        ...


def get_backend(name: str | None = None) -> Backend:
    """Resolve a backend by name — the one-config-line engine swap.

    ``None`` reads the ``GRIDPULSE_BACKEND`` env var, defaulting to polars.
    """
    name = name or os.environ.get(BACKEND_ENV_VAR, DEFAULT_BACKEND)
    if name == DEFAULT_BACKEND:
        from .polars_backend import PolarsBackend

        return PolarsBackend()
    raise ValueError(
        f"unknown backend {name!r} — available: '{DEFAULT_BACKEND}'. "
        f"Fix: set {BACKEND_ENV_VAR}={DEFAULT_BACKEND} (or unset it); a pandas "
        "backend arrives in a stretch phase, same contract."
    )

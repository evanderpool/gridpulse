"""The backend contract — the ONE interface every dataframe engine implements.

The metric definitions live here, not in any engine, so every backend is
held to the identical contract (tests/test_backend_contract.py runs the
same cases against each):

- Inputs: demand rows ``{region, period_utc, demand_mwh}`` and fuel-mix rows
  ``{region, period_utc, fueltype, generation_mwh}`` from the silver tables.
- Negative generation (flagged, kept in silver) is CLIPPED TO 0 for metric
  math — station-service draw is not negative supply.
- ``renewable_share`` = (SUN + WND) / total generation; ``None`` when total
  generation <= 0 (never a division error). Rounded to 4 decimals so every
  backend produces byte-identical output.
- ``net_load_mwh`` = demand − (SUN + WND): the duck-curve quantity.
- ``ramp_mwh_per_h`` = net load minus the PREVIOUS CONSECUTIVE hour's net
  load per region; ``None`` at series starts and across gaps — a gap must
  not fabricate a ramp.
- Only (region, period) pairs present in BOTH inputs produce a row; output
  is sorted by (region, period_utc).
"""

from __future__ import annotations

from typing import Protocol

RENEWABLE_FUELS = ("SUN", "WND")
SHARE_DECIMALS = 4


class Backend(Protocol):
    """A dataframe engine capable of computing the derived metrics."""

    name: str

    def derive_metrics(
        self, demand_rows: list[dict], fuelmix_rows: list[dict]
    ) -> list[dict]:
        """Compute the metric rows per the contract in this module's docstring."""
        ...


def get_backend(name: str = "polars") -> Backend:
    """Resolve a backend by name — the one-config-line engine swap."""
    if name == "polars":
        from .polars_backend import PolarsBackend

        return PolarsBackend()
    raise ValueError(
        f"unknown backend {name!r} — available: 'polars' "
        "(a pandas backend arrives in a stretch phase, same contract)"
    )

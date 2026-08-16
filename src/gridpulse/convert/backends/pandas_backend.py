"""Pandas implementation of the backend contract (the stretch engine).

Implemented against tests/test_backend_contract.py unchanged — that file is
the proof the swappable-backend design is real: this class earns its place
by producing byte-identical output to PolarsBackend on every contract case,
including the half-to-even exact-half rounding pin (numpy's ``round`` is
half-to-even, same as Python's — pinned by the 13/32 case).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from .base import RENEWABLE_FUELS, SHARE_DECIMALS

if TYPE_CHECKING:
    from .base import Backend


class PandasBackend:
    """Derived-metric math in pandas; see base.py for the contract."""

    name = "pandas"

    def derive_metrics(
        self, demand_rows: list[dict], fuelmix_rows: list[dict]
    ) -> list[dict]:
        """Compute renewable share, net load, and gap-aware ramp per region-hour."""
        if not demand_rows or not fuelmix_rows:
            return []
        demand = pd.DataFrame(demand_rows)[["region", "period_utc", "demand_mwh"]]
        fuel = pd.DataFrame(fuelmix_rows).assign(
            # Policy: negative generation clips to 0 for metric math only.
            gen=lambda d: d["generation_mwh"].clip(lower=0)
        )
        fuel["ren"] = fuel["gen"].where(fuel["fueltype"].isin(RENEWABLE_FUELS), 0)
        per_hour = fuel.groupby(["region", "period_utc"], as_index=False).agg(
            total_gen=("gen", "sum"), renewable_gen=("ren", "sum")
        )
        j = demand.merge(per_hour, on=["region", "period_utc"], how="inner")
        j["renewable_share"] = (
            (j["renewable_gen"] / j["total_gen"]).round(SHARE_DECIMALS)
            .where(j["total_gen"] > 0)
        )
        j["net_load_mwh"] = j["demand_mwh"] - j["renewable_gen"]
        j["ts"] = pd.to_datetime(j["period_utc"], format="%Y-%m-%dT%H:%M:%S%z")
        j = j.sort_values(["region", "ts"])
        j["prev_net"] = j.groupby("region")["net_load_mwh"].shift(1)
        j["prev_ts"] = j.groupby("region")["ts"].shift(1)
        # A ramp only exists across CONSECUTIVE hours — a gap yields None.
        consecutive = j["prev_ts"] == j["ts"] - pd.Timedelta(hours=1)
        j["ramp"] = (j["net_load_mwh"] - j["prev_net"]).where(consecutive)
        j = j.sort_values(["region", "period_utc"])
        return [
            {
                "region": r.region,
                "period_utc": r.period_utc,
                "renewable_share": (
                    None if pd.isna(r.renewable_share) else float(r.renewable_share)
                ),
                "net_load_mwh": int(r.net_load_mwh),
                "ramp_mwh_per_h": None if pd.isna(r.ramp) else int(r.ramp),
            }
            for r in j.itertuples()
        ]


if TYPE_CHECKING:
    _conforms: Backend = PandasBackend()

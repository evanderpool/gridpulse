"""Polars implementation of the backend contract (the MVP engine)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from .base import RENEWABLE_FUELS, SHARE_DECIMALS

if TYPE_CHECKING:
    from .base import Backend


class PolarsBackend:
    """Derived-metric math in Polars expressions; see base.py for the contract."""

    name = "polars"

    def derive_metrics(
        self, demand_rows: list[dict], fuelmix_rows: list[dict]
    ) -> list[dict]:
        """Compute renewable share, net load, and gap-aware ramp per region-hour."""
        if not demand_rows or not fuelmix_rows:
            return []
        demand = pl.DataFrame(demand_rows).select("region", "period_utc", "demand_mwh")
        # Policy: negative generation is clipped to 0 for metric math only —
        # silver keeps the observed (flagged) value.
        fuel = pl.DataFrame(fuelmix_rows).with_columns(
            pl.col("generation_mwh").clip(lower_bound=0).alias("gen")
        )
        per_hour = fuel.group_by("region", "period_utc").agg(
            pl.col("gen").sum().alias("total_gen"),
            pl.col("gen")
            .filter(pl.col("fueltype").is_in(list(RENEWABLE_FUELS)))
            .sum()
            .alias("renewable_gen"),
        )
        joined = (
            demand.join(per_hour, on=["region", "period_utc"], how="inner")
            .with_columns(
                pl.when(pl.col("total_gen") > 0)
                .then(
                    (pl.col("renewable_gen") / pl.col("total_gen")).round(SHARE_DECIMALS)
                )
                .otherwise(None)
                .alias("renewable_share"),
                (pl.col("demand_mwh") - pl.col("renewable_gen")).alias("net_load_mwh"),
                # Explicit format: polars refuses to infer tz-aware strings,
                # and period_utc is pinned to this exact shape by schema.
                pl.col("period_utc")
                .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%z")
                .alias("ts"),
            )
            .sort("region", "ts")
            .with_columns(
                pl.col("net_load_mwh").shift(1).over("region").alias("prev_net"),
                pl.col("ts").shift(1).over("region").alias("prev_ts"),
            )
            .with_columns(
                # A ramp only exists across CONSECUTIVE hours — a data gap
                # must yield None, not a fabricated multi-hour delta.
                pl.when(pl.col("prev_ts") == pl.col("ts") - pl.duration(hours=1))
                .then(pl.col("net_load_mwh") - pl.col("prev_net"))
                .otherwise(None)
                .alias("ramp_mwh_per_h")
            )
        )
        return (
            joined.select(
                "region", "period_utc", "renewable_share", "net_load_mwh", "ramp_mwh_per_h"
            )
            .sort("region", "period_utc")
            .to_dicts()
        )


if TYPE_CHECKING:
    # Static conformance assertion: when the MVP typecheck gate lands, drift
    # from the Protocol fails the build instead of waiting for contract tests.
    _conforms: Backend = PolarsBackend()

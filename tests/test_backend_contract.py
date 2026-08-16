"""The backend contract tests — every engine runs the SAME cases.

A new backend (pandas, stretch phase) earns its place by passing this file
unchanged: add it to BACKENDS and nothing else.
"""

import pytest

from gridpulse.convert.backends import get_backend
from gridpulse.convert.backends.base import Backend

BACKENDS: list[Backend] = [get_backend("polars")]


def d(region: str, hour: int, mwh: int) -> dict:
    return {"region": region, "period_utc": f"2026-08-13T{hour:02d}:00:00+00:00",
            "demand_mwh": mwh}


def f(region: str, hour: int, fuel: str, mwh: int) -> dict:
    return {"region": region, "period_utc": f"2026-08-13T{hour:02d}:00:00+00:00",
            "fueltype": fuel, "generation_mwh": mwh}


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
class TestBackendContract:
    def test_renewable_share_and_net_load(self, backend):
        rows = backend.derive_metrics(
            [d("ERCO", 18, 10_000)],
            [f("ERCO", 18, "SUN", 2_000), f("ERCO", 18, "WND", 1_000),
             f("ERCO", 18, "NG", 7_000)],
        )
        assert rows == [{
            "region": "ERCO", "period_utc": "2026-08-13T18:00:00+00:00",
            "renewable_share": 0.3, "net_load_mwh": 7_000, "ramp_mwh_per_h": None,
        }]

    def test_negative_generation_clips_to_zero_in_metrics(self, backend):
        # Silver keeps the flagged -60; metric math treats it as 0 supply.
        rows = backend.derive_metrics(
            [d("CISO", 5, 20_000)],
            [f("CISO", 5, "SUN", -60), f("CISO", 5, "NG", 10_000)],
        )
        assert rows[0]["renewable_share"] == 0.0
        assert rows[0]["net_load_mwh"] == 20_000

    def test_zero_total_generation_yields_null_share_not_error(self, backend):
        rows = backend.derive_metrics(
            [d("CISO", 5, 20_000)], [f("CISO", 5, "SUN", -60)]
        )
        assert rows[0]["renewable_share"] is None

    def test_ramp_needs_consecutive_hours(self, backend):
        rows = backend.derive_metrics(
            [d("ERCO", 10, 10_000), d("ERCO", 11, 12_000), d("ERCO", 13, 15_000)],
            [f("ERCO", 10, "NG", 1), f("ERCO", 11, "NG", 1), f("ERCO", 13, "NG", 1)],
        )
        ramps = {r["period_utc"][11:13]: r["ramp_mwh_per_h"] for r in rows}
        assert ramps["10"] is None          # series start
        assert ramps["11"] == 2_000         # consecutive
        assert ramps["13"] is None          # a gap must not fabricate a ramp

    def test_ramp_never_crosses_regions(self, backend):
        rows = backend.derive_metrics(
            [d("ERCO", 10, 10_000), d("CISO", 11, 99_000)],
            [f("ERCO", 10, "NG", 1), f("CISO", 11, "NG", 1)],
        )
        assert all(r["ramp_mwh_per_h"] is None for r in rows)

    def test_only_periods_present_in_both_inputs(self, backend):
        rows = backend.derive_metrics(
            [d("ERCO", 10, 10_000), d("ERCO", 11, 11_000)],
            [f("ERCO", 10, "NG", 5_000)],
        )
        assert [r["period_utc"][11:13] for r in rows] == ["10"]

    def test_empty_inputs_yield_empty_output(self, backend):
        assert backend.derive_metrics([], []) == []
        assert backend.derive_metrics([d("ERCO", 1, 1)], []) == []

    def test_output_is_deterministically_sorted(self, backend):
        rows = backend.derive_metrics(
            [d("PJM", 11, 1), d("CISO", 10, 1), d("CISO", 11, 1)],
            [f("PJM", 11, "NG", 1), f("CISO", 10, "NG", 1), f("CISO", 11, "NG", 1)],
        )
        keys = [(r["region"], r["period_utc"]) for r in rows]
        assert keys == sorted(keys)


def test_unknown_backend_error_names_the_options():
    with pytest.raises(ValueError, match="polars"):
        get_backend("spark")

"""The written quality policy, tested against the REAL captured quirks."""

import json
from pathlib import Path

from conftest import bronze_doc
from gridpulse.convert.pipeline import convert_fuelmix
from gridpulse.convert.quality import (
    EXTREME_VALUE,
    NEGATIVE_GENERATION,
    NONPOSITIVE_DEMAND,
    STORAGE_CHARGING,
    demand_flags,
    generation_flags,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_negative_nighttime_solar_fixture_is_flagged_not_rejected():
    # THE fixture this policy was written for: real CAISO solar with
    # negative nighttime values (station service). Every row must convert,
    # negatives must carry the flag, daytime rows must be clean.
    payload = json.loads(
        (FIXTURES / "solar_negative_nighttime_ciso.json").read_text(encoding="utf-8")
    )
    records, rejects = convert_fuelmix([bronze_doc(payload)])
    assert rejects == []
    flagged = [r for r in records if NEGATIVE_GENERATION in r.quality_flags]
    clean = [r for r in records if r.quality_flags == ""]
    assert flagged and clean
    assert all(r.generation_mwh < 0 for r in flagged)
    assert all(r.generation_mwh >= 0 for r in clean)


def test_fuel_vocab_fixture_converts_across_regions():
    # fueltype is an open string — BAT must pass validation even though the
    # CAISO capture never mentions it.
    payload = json.loads(
        (FIXTURES / "fuel_vocab_differs_by_region.json").read_text(encoding="utf-8")
    )
    records, rejects = convert_fuelmix([bronze_doc(payload)])
    assert rejects == []
    assert "BAT" in {r.fueltype for r in records if r.region == "ERCO"}


def test_demand_policy_flags_nonpositive_and_extreme():
    assert demand_flags(1000) == []
    assert NONPOSITIVE_DEMAND in demand_flags(0)
    assert NONPOSITIVE_DEMAND in demand_flags(-5)
    assert EXTREME_VALUE in demand_flags(2_000_000)


def test_generation_policy_flags_negative_and_extreme():
    assert generation_flags(500, "NG") == []
    assert generation_flags(0, "SUN") == []  # zero generation is normal (night solar)
    assert NEGATIVE_GENERATION in generation_flags(-57, "SUN")
    assert EXTREME_VALUE in generation_flags(-2_000_000, "SUN")


def test_battery_charging_gets_its_own_flag_not_the_anomaly_one():
    # Battery charge is a routine daily event; flagging it as
    # negative_generation would fire nightly and destroy the flag's
    # diagnostic value (P2 review finding MED-3).
    flags = generation_flags(-2000, "BAT")
    assert STORAGE_CHARGING in flags
    assert NEGATIVE_GENERATION not in flags
    assert generation_flags(1500, "BAT") == []  # discharge is plain supply

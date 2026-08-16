"""The written null/outlier policy — every judgment recorded as a flag.

Policy (per field, per the build plan; each rule traces to a validated
observation or a stated decision):

============  =========================  ==================================
Field         Condition                  Action
============  =========================  ==================================
any value     null / unparseable         quarantine (handled at the Pydantic
                                         boundary, never reaches here)
generation    negative                   KEEP + flag ``negative_generation``
                                         — real phenomenon: nighttime solar
                                         draws station power (pinned
                                         fixture, validated live 2026-08-15)
demand        <= 0                       KEEP + flag ``nonpositive_demand``
                                         — physically implausible for these
                                         four grids; kept so the report can
                                         show the anomaly instead of hiding it
any value     |value| > 1,000,000 MWh    KEEP + flag ``extreme_value`` —
                                         an order of magnitude above any
                                         observed hourly figure; almost
                                         certainly a unit or feed error
============  =========================  ==================================

Flags accumulate comma-joined in ``quality_flags``; derived metrics decide
per-metric how to treat flagged values (e.g. derive clips negative
generation to 0 for share math while silver keeps the observed value).
"""

from __future__ import annotations

EXTREME_ABS_MWH = 1_000_000

NEGATIVE_GENERATION = "negative_generation"
NONPOSITIVE_DEMAND = "nonpositive_demand"
EXTREME_VALUE = "extreme_value"


def demand_flags(value_mwh: int) -> list[str]:
    """Apply the demand-value policy; returns flags, never mutates."""
    flags = []
    if value_mwh <= 0:
        flags.append(NONPOSITIVE_DEMAND)
    if abs(value_mwh) > EXTREME_ABS_MWH:
        flags.append(EXTREME_VALUE)
    return flags


def generation_flags(value_mwh: int) -> list[str]:
    """Apply the generation-value policy; returns flags, never mutates."""
    flags = []
    if value_mwh < 0:
        flags.append(NEGATIVE_GENERATION)
    if abs(value_mwh) > EXTREME_ABS_MWH:
        flags.append(EXTREME_VALUE)
    return flags

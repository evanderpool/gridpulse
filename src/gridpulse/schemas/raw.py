"""Boundary models for EIA API v2 payloads — shaped by the captured fixtures.

Validation happens here and nowhere downstream: a row that parses into
``RawDemandRow`` is trusted by the rest of the pipeline. EIA serves numeric
values as strings in some series and numbers in others (both appear in the
fixtures), so ``value`` relies on Pydantic's int coercion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RawDemandRow(BaseModel):
    """One hourly demand observation as EIA serves it."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Hourly periods are UTC in the form "2026-08-13T18" (fixture-verified).
    period: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}$")
    respondent: str = Field(min_length=1)
    type: Literal["D"]
    # Phase 2 (quality.py): negative/absurd demand values pass through today —
    # the null/outlier policy flags them there, not at the boundary.
    value: int
    value_units: str = Field(alias="value-units")


class RawResponseMeta(BaseModel):
    """The envelope fields the pipeline actually uses."""

    model_config = ConfigDict(extra="allow")

    total: int
    data: list[dict]

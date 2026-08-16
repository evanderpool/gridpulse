"""Boundary models for EIA API v2 payloads — shaped by the captured fixtures.

Validation happens here and nowhere downstream: a row that parses into a Raw*
model is trusted by the rest of the pipeline (its *semantic* quality — sign,
magnitude — is judged later by convert/quality.py). EIA serves numeric values
as strings in some series and numbers in others (both appear in the
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
    value: int
    value_units: str = Field(alias="value-units")


class RawFuelMixRow(BaseModel):
    """One hourly generation-by-fuel observation as EIA serves it.

    ``fueltype`` is an open string on purpose: the vocabulary differs per
    region (ERCOT reports BAT, the CAISO capture did not — pinned fixture),
    so an enum here would quarantine real data the day a region adds a fuel.
    Negative values are VALID at this boundary — nighttime solar draws
    station power (pinned fixture); quality.py flags them downstream.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    period: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}$")
    respondent: str = Field(min_length=1)
    fueltype: str = Field(min_length=1)
    value: int
    value_units: str = Field(alias="value-units")


class RawResponseMeta(BaseModel):
    """The envelope fields the pipeline uses — validated in the client."""

    model_config = ConfigDict(extra="allow")

    total: int
    data: list[dict]

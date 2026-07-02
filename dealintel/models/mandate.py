"""Mandate contracts: a fund's investment rubric as data, not code.

A ``Mandate`` is a versioned collection of ``MandateCriterion`` entries. The
scoring engine interprets criteria generically, so adding a criterion is a data
change (a YAML entry), never an engine change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from dealintel.models.fact import ClaimType


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp (see ``document._utcnow``)."""
    return datetime.now(timezone.utc)


class MandateOperator(str, Enum):
    """Comparison operators a criterion can apply to a matched fact value.

    Promoted to an enum (from a bare string) so the scoring engine dispatches on
    a closed set and an invalid operator fails at mandate-load time, not mid-scoring.

    Attributes:
        GTE: Fact value must be >= threshold (numeric).
        LTE: Fact value must be <= threshold (numeric).
        EQ: Fact value must equal threshold (numeric or canonical text).
        CONTAINS: Threshold substring/element appears in the fact value.
        IN: Fact value is one of a threshold list.
        EXISTS: A matching fact merely needs to exist (threshold ignored).
    """

    GTE = "gte"
    LTE = "lte"
    EQ = "eq"
    CONTAINS = "contains"
    IN = "in"
    EXISTS = "exists"


class MandateCriterion(BaseModel):
    """A single investability criterion within a fund's mandate.

    Criteria are data: the scoring engine reads them generically, so a new
    criterion is a YAML entry, not a code change.

    Attributes:
        criterion_id: Stable identifier.
        name: Human-readable label, e.g. ``"Minimum ARR"``.
        description: One-line rationale shown in score explanations.
        claim_type: Which ``Fact.claim_type`` this criterion matches against.
        operator: How the matched value is compared to ``threshold``.
        threshold: The comparison target. Untyped because it may be a number, a
            string, or a list depending on ``operator``.
        threshold_unit: Canonical unit the threshold is expressed in, so the
            engine can compare like-for-like against
            ``Fact.normalized_value_unit`` (e.g. both ``"USD"``).
        weight: Relative importance; the engine normalizes weights across a
            mandate so they sum to 1.0.
        is_knockout: If True and the criterion fails, the deal scores 0
            regardless of other criteria.
    """

    criterion_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    description: str
    claim_type: ClaimType
    claim_subtype: str | None = Field(
        default=None,
        description=(
            "Optional sub-scope filter. When set, only facts whose "
            "claim_subtype_raw contains this value (case-insensitive) are "
            "considered — e.g. a net-IRR criterion can require 'fund' so a "
            "single hot deal's IRR does not satisfy a fund-level threshold."
        ),
    )
    operator: MandateOperator
    threshold: Any
    threshold_unit: str | None = None
    weight: float = Field(
        gt=0.0,
        description=(
            "Relative weight; any positive value. The scoring engine normalizes "
            "weights across a mandate so they sum to 1.0, so authors can use raw "
            "relative importances (e.g. 3 vs 1) without pre-normalizing."
        ),
    )
    is_knockout: bool = Field(
        default=False,
        description=(
            "If True and criterion fails, deal scores 0 regardless of others."
        ),
    )


class Mandate(BaseModel):
    """A fund's full investment mandate.

    Stored independently from deals and scoring results so the same mandate can
    be applied to many deals, and mandate updates do not retroactively corrupt
    historical scores (each score records ``mandate_version``).

    Attributes:
        mandate_id: Stable identifier.
        fund_name: Owning fund.
        mandate_version: Bumped on any criteria change; referenced by scores so
            a historical score stays interpretable against the rubric that
            produced it.
        description: Plain-English summary of the thesis.
        criteria: The weighted criteria evaluated during scoring.
        geography: Allowed geographies, e.g. ``["US", "Canada"]``.
        sectors: Target sectors, e.g. ``["B2B SaaS"]``.
        min_check_size_usd: Lower bound of the fund's check size, if any.
        max_check_size_usd: Upper bound of the fund's check size, if any.
        created_at: UTC creation timestamp.
        created_by: Author identifier (``"system"`` or an analyst email).
    """

    mandate_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    fund_name: str
    mandate_version: int = Field(default=1)
    description: str
    criteria: list[MandateCriterion]
    geography: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    min_check_size_usd: float | None = None
    max_check_size_usd: float | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    created_by: str = "system"

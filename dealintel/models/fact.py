"""Fact and entity contracts: the atomic extracted claims and their subjects.

A ``Fact`` is one cited claim pulled from one chunk. A ``CanonicalEntity`` is
the normalized subject a fact is about, deduplicated across name variants.

Two invariants:

1. Comparison uses normalized values, not the raw string. ``claim_value`` is
   kept verbatim for citation; the comparable form is
   ``normalized_value_numeric`` + ``normalized_value_unit`` (numeric) or
   ``normalized_value_text`` (categorical), produced by
   :mod:`dealintel.normalize`. ``normalization_status`` records whether parsing
   succeeded, failed, or was not applicable.
2. A claim that fits no named ``ClaimType`` is stored as ``OTHER`` with a
   free-text ``claim_subtype_raw`` (enforced by a model validator), so it is
   preserved and later re-mappable rather than dropped.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp (see ``document._utcnow``)."""
    return datetime.now(timezone.utc)


class ClaimType(str, Enum):
    """Ontology of claim types the extraction pipeline can identify.

    An enum (not free text) keeps scoring deterministic: a criterion references a
    ``ClaimType`` and matching is equality, not fuzzy string comparison.

    ``OTHER`` is the fallback: a claim fitting no named type is stored here with
    a descriptive ``claim_subtype_raw`` so it is preserved and later re-mappable
    rather than discarded.
    """

    MARKET_SIZE = "market_size"
    REVENUE = "revenue"
    TEAM_BACKGROUND = "team_background"
    COMPETITIVE_POSITIONING = "competitive_positioning"
    FUNDING_HISTORY = "funding_history"
    CUSTOMER_METRICS = "customer_metrics"
    # --- Fund-level claim types (e.g. a PE fund PPM, not an operating company) ---
    # Added so fund documents extract into typed claims that scoring can match,
    # rather than collapsing into OTHER. Mirrors how the ontology would grow per
    # document family in production.
    NET_IRR = "net_irr"  # fund/track-record net internal rate of return (percent)
    TVPI = "tvpi"  # total value to paid-in (a multiple, e.g. 1.8x)
    DPI = "dpi"  # distributions to paid-in (a multiple)
    FUND_SIZE = "fund_size"  # target/committed fund size (currency)
    VINTAGE_YEAR = "vintage_year"  # fund vintage (a year)
    GP_COMMITMENT = "gp_commitment"  # GP commitment (percent or currency)
    OTHER = "other"


class NormalizationStatus(str, Enum):
    """Outcome of attempting to normalize a raw claim value.

    Attributes:
        NORMALIZED: A comparable form was produced — either
            ``normalized_value_numeric`` + ``normalized_value_unit`` for
            quantitative claims, or ``normalized_value_text`` for categorical
            ones.
        UNPARSEABLE: Normalization was expected (the claim looked quantitative)
            but parsing failed. Normalized fields are left null. The fact is
            still persisted — we surface the failure rather than guess.
        NOT_APPLICABLE: The claim carries no normalizable value (e.g. a purely
            qualitative narrative statement, or an empty value).
    """

    NORMALIZED = "normalized"
    UNPARSEABLE = "unparseable"
    NOT_APPLICABLE = "not_applicable"


class SpanVerification(str, Enum):
    """Whether a fact's excerpt was located at char offsets in the source text.

    A fact is only trusted as grounded if its ``source_excerpt`` can be found in
    the parsed text of ``source_page``. The located span is stored as
    ``source_char_start`` / ``source_char_end`` (offsets into that page's text).

    Attributes:
        VERIFIED_EXACT: The excerpt was found as an exact substring; the span is
            byte-for-byte the excerpt.
        VERIFIED_FUZZY: The excerpt was located by approximate alignment (the
            model normalized whitespace/quotes); the span covers the matched
            region and the alignment score met the threshold.
        UNVERIFIED: The excerpt could not be located; offsets are null. The fact
            is retained but must not be treated as source-grounded.
    """

    VERIFIED_EXACT = "verified_exact"
    VERIFIED_FUZZY = "verified_fuzzy"
    UNVERIFIED = "unverified"


class ExtractionMethod(str, Enum):
    """Which pass produced a fact — important for cost tracking and provenance."""

    HAIKU_CLASSIFICATION = "haiku_classification"
    SONNET_EXTRACTION = "sonnet_extraction"
    MANUAL_OVERRIDE = "manual_override"  # reserved for the future analyst workflow


class Fact(BaseModel):
    """A single atomic claim extracted from a document.

    Notes for the multi-source extension (not implemented; see ARCHITECTURE.md):

    - Conflict detection keys on the normalized value. Two documents asserting
      the same ``(entity_id, claim_type)`` with different normalized values are
      found by grouping on
      ``(entity_id, claim_type, normalized_value_numeric, normalized_value_unit)``
      (numeric) or ``normalized_value_text`` (categorical), not on
      ``claim_value`` ("$5M", "$5,000,000", "5 million USD" are one value).
      Normalization at extraction time is therefore a precondition.
    - ``document_version`` allows re-processing a document without orphaning the
      original facts.

    Attributes:
        fact_id: Stable identifier for this claim.
        deal_id: Owning deal.
        chunk_id: Back-reference to the source chunk (and thus to its pages).
        entity_id: FK to the canonical entity this claim is about.
        entity_raw_name: The entity string exactly as it appeared in the text,
            before normalization — preserved for citation and entity-resolution
            auditing.
        claim_type: The matched ontology type; ``OTHER`` when nothing fits.
        claim_subtype_raw: The model's free-text label for the claim. REQUIRED
            when ``claim_type`` is ``OTHER`` so the unmapped claim is preserved
            and later re-mappable. Optional otherwise (may carry a finer-grained
            label, e.g. ``"ARR"`` vs ``"GMV"`` under ``REVENUE``).
        claim_value: Verbatim value string as written in the document; never
            modified.
        normalized_value_numeric: Parsed numeric form, if the value is
            quantitative. The field the conflict and scoring layers compare on.
        normalized_value_unit: Canonical unit for the numeric value, e.g.
            ``"USD"``, ``"USD/year"``, ``"percent"``, ``"count"``.
        normalized_value_text: Lowercased/canonical form for non-numeric claims
            (e.g. country, sector) used for categorical conflict matching.
        normalization_status: Whether normalization succeeded, failed, or was
            not applicable.
        source_page: Page the excerpt was drawn from (citation anchor).
        source_excerpt: Verbatim supporting text (<=500 chars).
        extraction_method: Which pass produced the fact.
        confidence_score: Extractor-reported confidence in ``[0, 1]``.
        document_version: Version of the source document this fact came from.
        extracted_at: UTC extraction timestamp.
    """

    fact_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    deal_id: uuid.UUID
    chunk_id: uuid.UUID
    entity_id: uuid.UUID
    entity_raw_name: str
    claim_type: ClaimType
    claim_subtype_raw: str | None = None
    claim_value: str
    normalized_value_numeric: float | None = None
    normalized_value_unit: str | None = None
    normalized_value_text: str | None = None
    normalization_status: NormalizationStatus = NormalizationStatus.NOT_APPLICABLE
    source_page: int
    source_excerpt: str = Field(
        description="Verbatim excerpt (<=500 chars) from source document",
        max_length=500,
    )
    source_char_start: int | None = Field(
        default=None,
        description=(
            "Start offset of the excerpt within the parsed text of "
            "``source_page`` (``parse_pdf(...)[source_page-1].raw_text``). Null "
            "when the excerpt could not be located."
        ),
    )
    source_char_end: int | None = Field(
        default=None,
        description="End offset (exclusive) of the located excerpt span.",
    )
    span_verification: SpanVerification = SpanVerification.UNVERIFIED
    source_bbox: list[dict] | None = Field(
        default=None,
        description=(
            "Line-level bounding boxes locating the excerpt on the page, each "
            "``{x0, top, x1, bottom}`` in PDF points (top-left origin). Null when "
            "the excerpt could not be located geometrically."
        ),
    )
    extraction_method: ExtractionMethod
    confidence_score: float = Field(ge=0.0, le=1.0)
    document_version: int = Field(default=1)
    extracted_at: datetime = Field(default_factory=_utcnow)

    @field_validator("source_excerpt")
    @classmethod
    def _excerpt_must_be_nonempty(cls, v: str) -> str:
        """Reject blank excerpts — a citation without evidence is not a citation."""
        if not v.strip():
            raise ValueError(
                "source_excerpt cannot be empty — citations require evidence"
            )
        return v

    @model_validator(mode="after")
    def _other_requires_subtype(self) -> Fact:
        """Require a label on ``OTHER`` claims.

        A model (not field) validator, because a field validator does not run
        when ``claim_subtype_raw`` is omitted and defaults to ``None`` — the case
        being checked. An ``OTHER`` claim with no label is rejected rather than
        persisted.
        """
        if self.claim_type == ClaimType.OTHER and not (
            self.claim_subtype_raw and self.claim_subtype_raw.strip()
        ):
            raise ValueError(
                "claim_subtype_raw is required when claim_type is OTHER so the "
                "unmapped claim's meaning is preserved"
            )
        return self


class CanonicalEntity(BaseModel):
    """Normalized entity record for deduplication across name variants.

    With one document, entity resolution is straightforward. With N documents,
    ``aliases`` accumulates every observed name variant and resolution compares
    against it. The schema is already multi-source-ready.

    Attributes:
        entity_id: Stable identifier referenced by facts.
        deal_id: Owning deal. (Cross-deal entity linking is a later phase; for
            now canonicalization is scoped within a deal.)
        canonical_name: The chosen normalized name.
        entity_type: ``"company"``, ``"person"``, ``"product"``, or ``"market"``.
        aliases: All observed variants, e.g.
            ``["Acme Corp", "ACME Corporation", "Acme Inc."]``.
        created_at: UTC creation timestamp.
        updated_at: UTC timestamp of the last alias merge.
    """

    entity_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    deal_id: uuid.UUID
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

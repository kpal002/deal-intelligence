"""Scoring contracts: traceable, immutable mandate-scoring output.

The hierarchy is ``ScoreResult`` -> many ``CriterionScore`` -> many
``FactContribution``. Citation data (page + excerpt) is embedded inline on
``FactContribution`` at scoring time *by design*: a score is an immutable
snapshot of what was known when it was produced. If a fact's excerpt is later
corrected, historical scores intentionally retain the original wording, so an
auditor sees exactly what drove the decision then. This is stated auditability,
not denormalization debt — see ARCHITECTURE.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp (see ``document._utcnow``)."""
    return datetime.now(timezone.utc)


class FactContribution(BaseModel):
    """Traceability record: which fact drove which part of a score.

    This is what makes scoring explainable and auditable. An analyst reviewing a
    score can walk criterion -> fact -> source_page -> source_excerpt without
    any additional queries. Fields are copied from the Fact at scoring time and
    frozen here (immutable snapshot).

    Attributes:
        fact_id: The contributing fact's identifier (link back to live data).
        entity_canonical_name: Canonical entity the fact concerns.
        claim_type: The fact's claim type (string form for serialization).
        claim_value: Verbatim value as scored.
        source_page: Citation page.
        source_excerpt: Verbatim supporting text, frozen at scoring time.
        confidence_score: The fact's extraction confidence at scoring time.
        contribution_direction: ``"supports"``, ``"neutral"``, or
            ``"contradicts"`` — how this fact affected the criterion.
    """

    fact_id: uuid.UUID
    entity_canonical_name: str
    claim_type: str
    claim_value: str
    source_page: int
    source_excerpt: str
    confidence_score: float
    contribution_direction: str


class CriterionScore(BaseModel):
    """Score for a single mandate criterion.

    Attributes:
        criterion_id: The scored criterion.
        criterion_name: Human-readable label (copied for self-contained reads).
        is_knockout: Whether this criterion is a knockout.
        met: Whether the threshold was satisfied.
        raw_score: Unweighted score in ``[0, 1]``.
        weighted_score: ``raw_score * normalized_weight``, in ``[0, 1]``.
        facts_used: The specific facts that drove this evaluation.
        explanation: Engine-generated rationale for the audit trail.
    """

    criterion_id: uuid.UUID
    criterion_name: str
    is_knockout: bool
    met: bool
    raw_score: float = Field(ge=0.0, le=1.0)
    weighted_score: float = Field(ge=0.0, le=1.0)
    facts_used: list[FactContribution]
    explanation: str


class ScoreResult(BaseModel):
    """Complete mandate scoring output for a deal — an immutable snapshot.

    Attributes:
        score_id: Stable identifier for this scoring run.
        deal_id: Deal that was scored.
        mandate_id: Mandate applied.
        mandate_version: Version of that mandate, so the score stays
            interpretable even after the mandate is later revised.
        total_score: Weighted sum of criterion scores, normalized to 0–100.
        knockout_triggered: True if any knockout criterion failed; when True,
            ``total_score`` is forced to 0.
        knockout_criterion_name: Which knockout failed, if any.
        criterion_scores: Per-criterion breakdown with full traceability.
        facts_evaluated: Total facts considered during scoring.
        scored_at: UTC timestamp of this scoring run.
        scoring_notes: Optional free-text engine commentary.
    """

    score_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    deal_id: uuid.UUID
    mandate_id: uuid.UUID
    mandate_version: int
    total_score: float = Field(ge=0.0, le=100.0)
    knockout_triggered: bool = False
    knockout_criterion_name: str | None = None
    criterion_scores: list[CriterionScore]
    facts_evaluated: int
    scored_at: datetime = Field(default_factory=_utcnow)
    scoring_notes: str = ""

"""Pydantic data contracts shared across every pipeline layer.

Pure data shapes with no I/O or business logic. The ORM layer
(``dealintel.orm``) persists them; the pipeline and scoring layers pass them
between stages.
"""

from dealintel.models.audit import AuditEventType, AuditLogEntry
from dealintel.models.document import (
    DocumentChunk,
    ParsedPage,
    RawDocument,
    SectionType,
)
from dealintel.models.fact import (
    CanonicalEntity,
    ClaimType,
    ExtractionMethod,
    Fact,
    NormalizationStatus,
)
from dealintel.models.mandate import Mandate, MandateCriterion, MandateOperator
from dealintel.models.scoring import CriterionScore, FactContribution, ScoreResult

__all__ = [
    "AuditEventType",
    "AuditLogEntry",
    "CanonicalEntity",
    "ClaimType",
    "CriterionScore",
    "DocumentChunk",
    "ExtractionMethod",
    "Fact",
    "FactContribution",
    "Mandate",
    "MandateCriterion",
    "MandateOperator",
    "NormalizationStatus",
    "ParsedPage",
    "RawDocument",
    "ScoreResult",
    "SectionType",
]

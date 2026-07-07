"""SQLAlchemy table definitions backing the Pydantic data contracts.

Design notes that map to the architecture:

- **Raw bytes are never discarded.** ``RawDocumentORM.raw_bytes`` keeps the
  original PDF as a ``LargeBinary`` chain-of-custody anchor.
- **Conflict detection is index-supported.** ``FactORM`` carries a composite
  index on ``(deal_id, claim_type, normalized_value_numeric,
  normalized_value_unit)`` so the "same entity + claim_type, different value"
  query a multi-source future would run is cheap from day one.
- **Audit chain is one-query traversable.** ``AuditLogORM`` indexes
  ``root_event_id`` so a full override chain is a single indexed lookup.
- **JSONB everywhere structure is open-ended** (entity aliases, mandate
  criteria, audit payloads, score breakdowns) so the relational core stays
  small while remaining queryable.

Portability: the production target is PostgreSQL, where these columns use the
native ``JSONB`` and ``UUID`` types. Via ``with_variant`` they fall back to
generic ``JSON`` and ``Uuid`` (CHAR-backed) on SQLite, so the entire stack —
including the API and persistence round-trip — is testable in-memory without a
Postgres server. Columns use SQLAlchemy 2.0 typed ``mapped_column`` style.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Portable UUID column: native PostgreSQL ``UUID`` in production, generic
#: ``Uuid`` (stored as CHAR(32)) on SQLite for in-memory testing.
GUID = PGUUID(as_uuid=True).with_variant(Uuid(as_uuid=True), "sqlite")

#: Portable JSON column: native PostgreSQL ``JSONB`` in production, generic
#: ``JSON`` on SQLite. JSONB is retained on Postgres for its indexing/operators.
JSON_COL = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    """Declarative base for all the platform ORM tables."""


class RawDocumentORM(Base):
    """Ingested PDF with its original bytes preserved (never discarded)."""

    __tablename__ = "raw_documents"

    deal_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_metadata: Mapped[dict] = mapped_column(JSON_COL, default=dict)
    #: The original file bytes — chain-of-custody anchor.
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ChunkORM(Base):
    """A layout-aware chunk with its classification result."""

    __tablename__ = "chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("raw_documents.deal_id"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_pages: Mapped[list] = mapped_column(JSON_COL, default=list)
    section_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contains_table: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )


class CanonicalEntityORM(Base):
    """Canonical entity with accumulated name-variant aliases."""

    __tablename__ = "canonical_entities"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID, primary_key=True
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("raw_documents.deal_id"),
        nullable=False,
        index=True,
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON_COL, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FactORM(Base):
    """An extracted, cited, normalized claim.

    ``ix_facts_conflict_key`` supports the cross-document conflict query (same
    entity + claim_type with differing normalized value) before any conflict
    logic exists. See ARCHITECTURE.md, Extension points.
    """

    __tablename__ = "facts"

    fact_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("raw_documents.deal_id"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chunks.chunk_id"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("canonical_entities.entity_id"),
        nullable=False,
    )
    entity_raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_subtype_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_value_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalization_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_verification: Mapped[str] = mapped_column(String(16), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_facts_conflict_key",
            "deal_id",
            "claim_type",
            "normalized_value_numeric",
            "normalized_value_unit",
        ),
    )


class MandateORM(Base):
    """A versioned fund mandate; criteria stored as JSONB for flexibility."""

    __tablename__ = "mandates"

    mandate_id: Mapped[uuid.UUID] = mapped_column(
        GUID, primary_key=True
    )
    fund_name: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[list] = mapped_column(JSON_COL, default=list)
    geography: Mapped[list] = mapped_column(JSON_COL, default=list)
    sectors: Mapped[list] = mapped_column(JSON_COL, default=list)
    min_check_size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_check_size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255), default="system")


class ScoreResultORM(Base):
    """A persisted, immutable scoring snapshot.

    ``criterion_scores`` is stored as JSONB (with citation data embedded) so the
    full breakdown round-trips intact and historical scores reflect what was
    known at scoring time.
    """

    __tablename__ = "score_results"

    score_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("raw_documents.deal_id"),
        nullable=False,
        index=True,
    )
    mandate_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    mandate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    knockout_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    knockout_criterion_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    criterion_scores: Mapped[list] = mapped_column(JSON_COL, default=list)
    facts_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scoring_notes: Mapped[str] = mapped_column(Text, default="")


class AuditLogORM(Base):
    """Append-only audit event. ``root_event_id`` is indexed for one-query chains."""

    __tablename__ = "audit_log"

    event_id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, nullable=True, index=True
    )
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, nullable=True
    )
    root_event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(255), default="system")
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON_COL, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON_COL, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

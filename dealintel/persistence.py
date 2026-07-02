"""Persistence helpers: map Pydantic contracts to ORM rows and save them.

This module owns the contract<->ORM translation; the pipeline and API do not
touch ORM rows. Read helpers return Pydantic contracts.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from dealintel.models.document import DocumentChunk, RawDocument
from dealintel.models.fact import CanonicalEntity, Fact
from dealintel.models.mandate import Mandate
from dealintel.models.scoring import ScoreResult
from dealintel.orm.tables import (
    CanonicalEntityORM,
    ChunkORM,
    FactORM,
    MandateORM,
    RawDocumentORM,
    ScoreResultORM,
)

logger = logging.getLogger(__name__)


def save_raw_document(session: Session, doc: RawDocument, raw_bytes: bytes) -> None:
    """Persist an ingested document together with its original bytes.

    Args:
        session: Active DB session.
        doc: The document metadata contract.
        raw_bytes: The original file bytes (never discarded).
    """
    session.add(
        RawDocumentORM(
            deal_id=doc.deal_id,
            source_path=str(doc.source_path),
            file_hash=doc.file_hash,
            file_size_bytes=doc.file_size_bytes,
            page_count=doc.page_count,
            ingested_at=doc.ingested_at,
            source_metadata=doc.source_metadata,
            raw_bytes=raw_bytes,
        )
    )


def save_chunks(session: Session, chunks: list[DocumentChunk]) -> None:
    """Persist classified chunks.

    Args:
        session: Active DB session.
        chunks: Chunks to store.
    """
    for chunk in chunks:
        session.add(
            ChunkORM(
                chunk_id=chunk.chunk_id,
                deal_id=chunk.deal_id,
                chunk_index=chunk.chunk_index,
                source_pages=chunk.source_pages,
                section_type=(
                    chunk.section_type.value if chunk.section_type else None
                ),
                content=chunk.content,
                contains_table=chunk.contains_table,
                classification_confidence=chunk.classification_confidence,
            )
        )


def save_entities(session: Session, entities: list[CanonicalEntity]) -> None:
    """Persist canonical entities.

    Args:
        session: Active DB session.
        entities: Canonical entities to store.
    """
    for entity in entities:
        session.add(
            CanonicalEntityORM(
                entity_id=entity.entity_id,
                deal_id=entity.deal_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                aliases=entity.aliases,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
            )
        )


def save_facts(session: Session, facts: list[Fact]) -> None:
    """Persist extracted facts.

    Args:
        session: Active DB session.
        facts: Facts to store.
    """
    for fact in facts:
        session.add(
            FactORM(
                fact_id=fact.fact_id,
                deal_id=fact.deal_id,
                chunk_id=fact.chunk_id,
                entity_id=fact.entity_id,
                entity_raw_name=fact.entity_raw_name,
                claim_type=fact.claim_type.value,
                claim_subtype_raw=fact.claim_subtype_raw,
                claim_value=fact.claim_value,
                normalized_value_numeric=fact.normalized_value_numeric,
                normalized_value_unit=fact.normalized_value_unit,
                normalized_value_text=fact.normalized_value_text,
                normalization_status=fact.normalization_status.value,
                source_page=fact.source_page,
                source_excerpt=fact.source_excerpt,
                extraction_method=fact.extraction_method.value,
                confidence_score=fact.confidence_score,
                document_version=fact.document_version,
                extracted_at=fact.extracted_at,
            )
        )


def save_mandate(session: Session, mandate: Mandate) -> None:
    """Persist a mandate (criteria serialized to JSONB).

    Args:
        session: Active DB session.
        mandate: The mandate to store.
    """
    session.merge(
        MandateORM(
            mandate_id=mandate.mandate_id,
            fund_name=mandate.fund_name,
            mandate_version=mandate.mandate_version,
            description=mandate.description,
            criteria=[c.model_dump(mode="json") for c in mandate.criteria],
            geography=mandate.geography,
            sectors=mandate.sectors,
            min_check_size_usd=mandate.min_check_size_usd,
            max_check_size_usd=mandate.max_check_size_usd,
            created_at=mandate.created_at,
            created_by=mandate.created_by,
        )
    )


def save_score(session: Session, score: ScoreResult) -> None:
    """Persist an immutable scoring snapshot.

    Args:
        session: Active DB session.
        score: The score result to store.
    """
    session.add(
        ScoreResultORM(
            score_id=score.score_id,
            deal_id=score.deal_id,
            mandate_id=score.mandate_id,
            mandate_version=score.mandate_version,
            total_score=score.total_score,
            knockout_triggered=score.knockout_triggered,
            knockout_criterion_name=score.knockout_criterion_name,
            criterion_scores=[cs.model_dump(mode="json") for cs in score.criterion_scores],
            facts_evaluated=score.facts_evaluated,
            scored_at=score.scored_at,
            scoring_notes=score.scoring_notes,
        )
    )


def load_facts(session: Session, deal_id: uuid.UUID) -> list[Fact]:
    """Load all facts for a deal as Pydantic contracts.

    Args:
        session: Active DB session.
        deal_id: The deal whose facts to load.

    Returns:
        The deal's facts.
    """
    rows = session.execute(
        select(FactORM).where(FactORM.deal_id == deal_id)
    ).scalars().all()
    return [Fact.model_validate(row, from_attributes=True) for row in rows]


def load_entity_names(session: Session, deal_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """Load an ``entity_id -> canonical_name`` map for a deal.

    Args:
        session: Active DB session.
        deal_id: The deal whose entities to load.

    Returns:
        Mapping used to enrich scoring contributions.
    """
    rows = session.execute(
        select(CanonicalEntityORM).where(CanonicalEntityORM.deal_id == deal_id)
    ).scalars().all()
    return {row.entity_id: row.canonical_name for row in rows}


def load_latest_score(session: Session, deal_id: uuid.UUID) -> ScoreResult | None:
    """Load the most recent score snapshot for a deal.

    Args:
        session: Active DB session.
        deal_id: The deal whose score to load.

    Returns:
        The latest :class:`ScoreResult`, or ``None`` if the deal was never scored.
    """
    row = session.execute(
        select(ScoreResultORM)
        .where(ScoreResultORM.deal_id == deal_id)
        .order_by(ScoreResultORM.scored_at.desc())
    ).scalars().first()
    if row is None:
        return None
    return ScoreResult.model_validate(
        {
            "score_id": row.score_id,
            "deal_id": row.deal_id,
            "mandate_id": row.mandate_id,
            "mandate_version": row.mandate_version,
            "total_score": row.total_score,
            "knockout_triggered": row.knockout_triggered,
            "knockout_criterion_name": row.knockout_criterion_name,
            "criterion_scores": row.criterion_scores,
            "facts_evaluated": row.facts_evaluated,
            "scored_at": row.scored_at,
            "scoring_notes": row.scoring_notes,
        }
    )

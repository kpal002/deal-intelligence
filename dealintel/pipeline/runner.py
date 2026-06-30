"""End-to-end pipeline runner: orchestrates every stage and writes the audit trail.

``run_pipeline`` ties ingestion -> parse -> chunk -> classify -> extract ->
resolve into one call, persisting results and recording an :class:`AuditLogEntry`
for every meaningful operation. Each stage is wrapped so a failure is logged to
the audit trail as a ``PIPELINE_ERROR`` and surfaced clearly, rather than
crashing silently.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from dealintel import persistence
from dealintel.llm import LLMClient
from dealintel.models.audit import AuditEventType
from dealintel.models.document import DocumentChunk
from dealintel.models.fact import Fact
from dealintel.pipeline.audit import record_event
from dealintel.pipeline.chunking import chunk_pages
from dealintel.pipeline.classification import classify_chunk
from dealintel.pipeline.entity_resolution import EntityResolver
from dealintel.pipeline.extraction import extract_facts
from dealintel.pipeline.ingestion import ingest_pdf
from dealintel.pipeline.parsing import parse_pdf

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Summary of one end-to-end pipeline run.

    Attributes:
        deal_id: The processed deal.
        page_count: Pages parsed.
        chunk_count: Chunks produced.
        fact_count: Facts extracted and persisted.
        entity_count: Canonical entities resolved.
        estimated_cost_usd: Sum of estimated LLM costs across all calls.
    """

    deal_id: uuid.UUID
    page_count: int
    chunk_count: int
    fact_count: int
    entity_count: int
    estimated_cost_usd: float


def run_pipeline(
    session: Session,
    client: LLMClient,
    pdf_path: str,
    source_metadata: dict[str, str] | None = None,
) -> PipelineResult:
    """Process a single PDF end to end, persisting all artifacts and audit events.

    Args:
        session: Active DB session (transaction owned by the caller's scope).
        client: The tiered LLM client (live or mock).
        pdf_path: Path to the PDF to process.
        source_metadata: Optional provenance metadata.

    Returns:
        A :class:`PipelineResult` summarizing the run.

    Raises:
        Exception: Re-raises any unrecoverable stage failure after recording a
            ``PIPELINE_ERROR`` audit event.
    """
    total_cost = 0.0

    # --- Ingest -----------------------------------------------------------
    doc, raw_bytes = ingest_pdf(pdf_path, source_metadata)
    persistence.save_raw_document(session, doc, raw_bytes)
    record_event(
        session,
        AuditEventType.DOCUMENT_INGESTED,
        deal_id=doc.deal_id,
        input_payload={"source_path": str(doc.source_path)},
        output_payload={
            "file_hash": doc.file_hash,
            "page_count": doc.page_count,
        },
    )

    try:
        # --- Parse + chunk ------------------------------------------------
        pages = parse_pdf(pdf_path, doc.deal_id)
        chunks = chunk_pages(pages)

        # --- Classify (Haiku) ---------------------------------------------
        for chunk in chunks:
            section_type, confidence, result = classify_chunk(client, chunk)
            chunk.section_type = section_type
            chunk.classification_confidence = confidence
            total_cost += result.estimated_cost_usd
            record_event(
                session,
                AuditEventType.CLASSIFICATION_RUN,
                deal_id=doc.deal_id,
                model_id=result.model_id,
                input_payload={"chunk_index": chunk.chunk_index},
                output_payload={
                    "section_type": section_type.value,
                    "confidence": confidence,
                },
                latency_ms=result.latency_ms,
                token_count_input=result.input_tokens,
                token_count_output=result.output_tokens,
                estimated_cost_usd=result.estimated_cost_usd,
            )
        persistence.save_chunks(session, chunks)

        # --- Extract (Sonnet) + resolve entities --------------------------
        resolver = EntityResolver(doc.deal_id)
        all_facts: list[Fact] = []
        for chunk in _extractable(chunks):
            facts, result = extract_facts(client, chunk, resolver)
            all_facts.extend(facts)
            total_cost += result.estimated_cost_usd
            record_event(
                session,
                AuditEventType.EXTRACTION_RUN,
                deal_id=doc.deal_id,
                model_id=result.model_id,
                input_payload={"chunk_index": chunk.chunk_index},
                output_payload={"facts_extracted": len(facts)},
                latency_ms=result.latency_ms,
                token_count_input=result.input_tokens,
                token_count_output=result.output_tokens,
                estimated_cost_usd=result.estimated_cost_usd,
            )

        entities = resolver.all_entities()
        persistence.save_entities(session, entities)
        persistence.save_facts(session, all_facts)
        record_event(
            session,
            AuditEventType.ENTITY_RESOLVED,
            deal_id=doc.deal_id,
            output_payload={"entity_count": len(entities)},
        )

    except Exception as exc:
        record_event(
            session,
            AuditEventType.PIPELINE_ERROR,
            deal_id=doc.deal_id,
            success=False,
            error_message=str(exc),
        )
        logger.exception("Pipeline failed for deal %s", doc.deal_id)
        raise

    return PipelineResult(
        deal_id=doc.deal_id,
        page_count=len(pages),
        chunk_count=len(chunks),
        fact_count=len(all_facts),
        entity_count=len(entities),
        estimated_cost_usd=total_cost,
    )


def _extractable(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    """Filter chunks worth sending to the expensive extraction tier.

    Skips ``legal_boilerplate`` — the cost-tiering payoff: the cheap classifier
    keeps the Sonnet budget off sections that never contain investable facts.

    Args:
        chunks: All classified chunks.

    Returns:
        Chunks eligible for fact extraction.
    """
    from dealintel.models.document import SectionType

    return [c for c in chunks if c.section_type is not SectionType.LEGAL_BOILERPLATE]

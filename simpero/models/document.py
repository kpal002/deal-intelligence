"""Document-layer data contracts: raw ingestion through layout-aware chunking.

The flow is: ``RawDocument`` (immutable chain-of-custody anchor) ->
``ParsedPage`` (one per PDF page, layout preserved) -> ``DocumentChunk``
(semantically coherent unit handed to the LLM). Page numbers ride along on
every object so that any downstream fact can cite an exact page.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Using an explicit helper (rather than ``datetime.utcnow``, which is
    deprecated in 3.12 and returns a naive datetime) keeps every timestamp in
    the system tz-aware and comparable. Important for an audit trail where
    ordering across events must be unambiguous.
    """
    return datetime.now(timezone.utc)


class SectionType(str, Enum):
    """Classification labels assigned by the Haiku classifier pass.

    Using an enum (not raw strings) means the extraction pipeline and the
    classifier share a single source of truth for valid section types.
    """

    FINANCIAL_DATA = "financial_data"
    NARRATIVE_CLAIM = "narrative_claim"
    LEGAL_BOILERPLATE = "legal_boilerplate"
    TABLE = "table"
    OTHER = "other"


class RawDocument(BaseModel):
    """Immutable record of an ingested PDF.

    The original bytes are stored separately in the DB blob column; this model
    carries the metadata that travels through the pipeline. Nothing downstream
    should modify ``source_path`` or ``file_hash`` — they are the
    chain-of-custody anchor for the audit trail.

    Attributes:
        deal_id: Stable identifier for this deal; assigned at ingestion and
            referenced by every downstream object.
        source_path: Original filesystem path of the ingested PDF.
        file_hash: SHA-256 of the raw bytes. Doubles as the deduplication key —
            re-ingesting identical bytes can be detected without re-parsing.
        file_size_bytes: Size of the original file.
        page_count: Number of pages pdfplumber found.
        ingested_at: UTC ingestion timestamp.
        source_metadata: Freeform key/value bag for provenance (sender,
            deal_name, fund, received_via, etc.). Kept untyped on purpose so
            ingestion sources can attach whatever context they have.
    """

    deal_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_path: Path
    file_hash: str
    file_size_bytes: int
    page_count: int
    ingested_at: datetime = Field(default_factory=_utcnow)
    source_metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Freeform KV bag: sender, deal_name, fund, etc.",
    )


class ParsedPage(BaseModel):
    """Single page extracted from a PDF, preserving layout signals.

    Keeping ``page_number`` on every downstream object is what makes accurate
    citations possible. Tables are extracted separately from prose so the
    chunker can keep them together with their surrounding context rather than
    splitting at token boundaries.

    Attributes:
        deal_id: Owning deal.
        page_number: 1-indexed, matches the page numbers a human sees in the PDF.
        raw_text: Flowed text content of the page (tables excluded).
        tables: Extracted tables as ``tables[i][row][col]``. A cell may be
            ``None`` where pdfplumber could not resolve it.
        has_images: Whether the page contained image objects (flagged so a
            future OCR pass knows where to look).
        extraction_warnings: Non-fatal issues encountered while parsing this
            page (e.g. a table with ragged rows). Surfaced for the audit log.
    """

    deal_id: uuid.UUID
    page_number: int
    raw_text: str
    tables: list[list[list[str | None]]] = Field(default_factory=list)
    has_images: bool = False
    extraction_warnings: list[str] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    """A semantically coherent unit of content ready for LLM processing.

    Layout-aware chunking keeps a table with its caption/header rows rather
    than splitting at token boundaries. ``chunk_index`` is ordinal within the
    document so chunks can be reconstructed in reading order.

    ``source_pages`` is a list because a chunk may straddle a page break — in
    that case citations should reference all pages it touches.

    Attributes:
        chunk_id: Stable identifier; referenced by every Fact extracted from
            this chunk so a fact can be traced back to its exact source unit.
        deal_id: Owning deal.
        chunk_index: Reading-order position within the document.
        source_pages: Page numbers this chunk spans (usually one).
        section_type: Populated after the classification pass; ``None`` until
            then.
        content: The text actually sent to the LLM. May include a serialized
            table rendered alongside its explanatory prose.
        contains_table: Whether ``content`` embeds a serialized table.
        classification_confidence: Classifier-reported confidence in
            ``section_type``, in ``[0, 1]``; ``None`` before classification.
    """

    chunk_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    deal_id: uuid.UUID
    chunk_index: int
    source_pages: list[int]
    section_type: SectionType | None = None
    content: str
    contains_table: bool = False
    classification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

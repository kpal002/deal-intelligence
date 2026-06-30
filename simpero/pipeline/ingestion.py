"""Ingestion stage: read a PDF's bytes and build the immutable RawDocument.

This is the chain-of-custody entry point. It computes the content hash, records
source metadata, and returns both the :class:`RawDocument` contract and the raw
bytes so the persistence layer can store the original unmodified.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

import pdfplumber

from simpero.models.document import RawDocument

logger = logging.getLogger(__name__)


def ingest_pdf(
    pdf_path: str | Path,
    source_metadata: dict[str, str] | None = None,
    deal_id: uuid.UUID | None = None,
) -> tuple[RawDocument, bytes]:
    """Read a PDF and construct its immutable ingestion record.

    Args:
        pdf_path: Path to the PDF to ingest.
        source_metadata: Optional provenance KV bag (sender, deal_name, fund...).
        deal_id: Optional explicit deal id; generated if omitted.

    Returns:
        A ``(RawDocument, raw_bytes)`` tuple. The bytes are returned alongside
        the metadata so the caller persists the original, never a re-encoding.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be read as a PDF.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    raw_bytes = path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:
        raise ValueError(f"Could not read '{path}' as a PDF: {exc}") from exc

    doc = RawDocument(
        deal_id=deal_id or uuid.uuid4(),
        source_path=path,
        file_hash=file_hash,
        file_size_bytes=len(raw_bytes),
        page_count=page_count,
        source_metadata=source_metadata or {},
    )
    logger.info(
        "Ingested %s (deal_id=%s, %d pages, %d bytes)",
        path.name,
        doc.deal_id,
        page_count,
        len(raw_bytes),
    )
    return doc, raw_bytes

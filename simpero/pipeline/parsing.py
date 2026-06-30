"""Page-aware PDF parsing with pdfplumber, preserving page numbers and tables.

The output (:class:`ParsedPage` per page) keeps prose and tables separate so the
chunker can reassemble a table with its explanatory text. Parsing is defensive:
a failure on one page is captured as a warning and the remaining pages still
process — a single malformed page should never abort ingestion of a 10-page deck.
"""

from __future__ import annotations

import logging
import uuid

import pdfplumber

from simpero.models.document import ParsedPage

logger = logging.getLogger(__name__)


def parse_pdf(pdf_path: str, deal_id: uuid.UUID) -> list[ParsedPage]:
    """Parse a PDF into per-page text and tables, preserving page numbers.

    Args:
        pdf_path: Filesystem path to the PDF.
        deal_id: Deal the pages belong to.

    Returns:
        One :class:`ParsedPage` per page, in document order. Pages that fail to
        parse are still returned with empty text and an ``extraction_warnings``
        entry, so downstream stages see the full page count.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
        ValueError: If the file cannot be opened as a PDF at all.
    """
    try:
        pdf = pdfplumber.open(pdf_path)
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not open '{pdf_path}' as a PDF: {exc}") from exc

    pages: list[ParsedPage] = []
    with pdf:
        for index, page in enumerate(pdf.pages, start=1):
            warnings: list[str] = []
            text = ""
            tables: list[list[list[str | None]]] = []
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                warnings.append(f"text extraction failed: {exc}")
                logger.warning("Page %d text extraction failed: %s", index, exc)
            try:
                tables = page.extract_tables() or []
            except Exception as exc:
                warnings.append(f"table extraction failed: {exc}")
                logger.warning("Page %d table extraction failed: %s", index, exc)

            has_images = bool(getattr(page, "images", []))
            pages.append(
                ParsedPage(
                    deal_id=deal_id,
                    page_number=index,
                    raw_text=text,
                    tables=tables,
                    has_images=has_images,
                    extraction_warnings=warnings,
                )
            )

    logger.info("Parsed %d page(s) from %s", len(pages), pdf_path)
    return pages

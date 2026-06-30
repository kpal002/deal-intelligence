"""Layout-aware chunking: keep tables with their explanatory text.

Rather than splitting on a fixed token count (which would orphan a financial
table from the sentence that introduces it), this chunker emits one prose chunk
per page plus one chunk per table that carries the page's prose as context. The
result is units that make sense to an extraction model and that always cite a
real page.
"""

from __future__ import annotations

import logging
import uuid

from simpero.models.document import DocumentChunk, ParsedPage

logger = logging.getLogger(__name__)

#: Soft cap on prose chunk length (characters). Long pages are split on
#: paragraph boundaries, never mid-sentence, to keep citations coherent.
_MAX_PROSE_CHARS = 4000


def _serialize_table(table: list[list[str | None]]) -> str:
    """Render a table as pipe-delimited text for inclusion in a chunk.

    Args:
        table: Rows of cells (cells may be ``None``).

    Returns:
        A newline-joined, pipe-delimited rendering with empty cells blanked.
    """
    lines = []
    for row in table:
        cells = ["" if cell is None else str(cell).strip() for cell in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _split_prose(text: str) -> list[str]:
    """Split long prose into paragraph-aligned pieces under the soft cap.

    Args:
        text: Page prose.

    Returns:
        A list of prose segments, each at or under ``_MAX_PROSE_CHARS`` where
        paragraph boundaries allow.
    """
    if len(text) <= _MAX_PROSE_CHARS:
        return [text] if text.strip() else []

    segments: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > _MAX_PROSE_CHARS:
            segments.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        segments.append(current.strip())
    return segments


def chunk_pages(pages: list[ParsedPage]) -> list[DocumentChunk]:
    """Convert parsed pages into layout-aware chunks ready for the LLM.

    For each page: emit prose chunk(s), then one chunk per table that prepends a
    short prose preamble (the page's first prose segment) so the table never
    loses the context that explains it.

    Args:
        pages: Parsed pages in document order.

    Returns:
        Chunks in reading order with sequential ``chunk_index`` values. Empty
        pages contribute no chunks.
    """
    chunks: list[DocumentChunk] = []
    index = 0
    for page in pages:
        prose_segments = _split_prose(page.raw_text)
        preamble = prose_segments[0][:500] if prose_segments else ""

        for segment in prose_segments:
            chunks.append(
                DocumentChunk(
                    chunk_id=uuid.uuid4(),
                    deal_id=page.deal_id,
                    chunk_index=index,
                    source_pages=[page.page_number],
                    content=segment,
                    contains_table=False,
                )
            )
            index += 1

        for table in page.tables:
            serialized = _serialize_table(table)
            if not serialized.strip():
                continue
            content = (
                f"{preamble}\n\n[TABLE]\n{serialized}" if preamble else serialized
            )
            chunks.append(
                DocumentChunk(
                    chunk_id=uuid.uuid4(),
                    deal_id=page.deal_id,
                    chunk_index=index,
                    source_pages=[page.page_number],
                    content=content,
                    contains_table=True,
                )
            )
            index += 1

    logger.info("Produced %d chunk(s) from %d page(s)", len(chunks), len(pages))
    return chunks

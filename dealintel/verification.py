"""Span verification: locate a fact's excerpt at char offsets in the source text.

A fact is source-grounded only if its ``source_excerpt`` can be found in the
parsed text of its page. This module returns the ``[start, end)`` char span and a
:class:`SpanVerification` status.

Two paths:

- Exact substring (:func:`str.find`, stdlib) — the excerpt appears verbatim.
- Approximate alignment (``rapidfuzz.fuzz.partial_ratio_alignment``) — the model
  normalized whitespace/quotes, so the excerpt is not byte-identical. rapidfuzz
  returns the matched span in the source text plus a score to threshold on.

rapidfuzz is the only dependency here; getting the source text is pdfplumber's
job upstream. Pixel/bounding-box coordinates are out of scope (that needs a PDF
geometry library).
"""

from __future__ import annotations

from rapidfuzz.fuzz import partial_ratio_alignment

from dealintel.models.fact import SpanVerification

#: Minimum rapidfuzz partial-ratio score (0-100) to accept a fuzzy match.
#: Below this the excerpt is treated as not located (``UNVERIFIED``).
DEFAULT_MIN_SCORE = 85.0


def verify_span(
    excerpt: str,
    source_text: str,
    min_score: float = DEFAULT_MIN_SCORE,
) -> tuple[int | None, int | None, SpanVerification]:
    """Locate ``excerpt`` within ``source_text`` and return its char span.

    Args:
        excerpt: The verbatim supporting text a fact claims to quote.
        source_text: The parsed text of the fact's source page.
        min_score: Minimum rapidfuzz partial-ratio score to accept a fuzzy
            match, in ``[0, 100]``.

    Returns:
        ``(start, end, status)``. For an exact substring, ``[start, end)``
        delimits the excerpt and status is ``VERIFIED_EXACT``. For an approximate
        match at or above ``min_score``, the span covers the aligned region and
        status is ``VERIFIED_FUZZY``. If neither succeeds (or an input is empty),
        offsets are ``None`` and status is ``UNVERIFIED`` — the excerpt is not
        treated as grounded.
    """
    if not excerpt or not source_text:
        return None, None, SpanVerification.UNVERIFIED

    exact = source_text.find(excerpt)
    if exact != -1:
        return exact, exact + len(excerpt), SpanVerification.VERIFIED_EXACT

    alignment = partial_ratio_alignment(excerpt, source_text)
    if alignment is not None and alignment.score >= min_score:
        return (
            alignment.dest_start,
            alignment.dest_end,
            SpanVerification.VERIFIED_FUZZY,
        )

    return None, None, SpanVerification.UNVERIFIED

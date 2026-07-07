"""Map a located excerpt to bounding-box rectangles on the page.

Char-span verification (``verification.py``) answers "is the excerpt in the
source text, and where in the string." This module answers "where on the page,"
producing PDF-point rectangles suitable for highlight annotations in a viewer.

It uses the per-word geometry pdfplumber already extracts (``x0/x1/top/bottom``),
so there is no new dependency and no license change. Matching is token-based:
find the contiguous run of page words that best matches the excerpt's tokens,
then union their boxes per line. Coordinates are in PDF points with a top-left
origin (pdfplumber's ``top``/``bottom``).

Pixel-perfect quads (e.g. PyMuPDF) are out of scope; line-level rectangles are
sufficient to highlight the cited text.
"""

from __future__ import annotations

import re

#: Minimum fraction of excerpt tokens that must match in the best window to
#: accept a location. Below this, no box is returned.
DEFAULT_MIN_TOKEN_RATIO = 0.6

#: Vertical tolerance (PDF points) for grouping matched words into one line.
_LINE_TOLERANCE = 3.0


def _norm(token: str) -> str:
    """Lowercase a token and strip non-alphanumerics for comparison."""
    return re.sub(r"[^a-z0-9]", "", token.lower())


def _excerpt_tokens(excerpt: str) -> list[str]:
    """Normalized, non-empty word tokens of an excerpt."""
    return [t for t in (_norm(w) for w in excerpt.split()) if t]


def locate_bboxes(
    excerpt: str,
    words: list[dict],
    min_token_ratio: float = DEFAULT_MIN_TOKEN_RATIO,
) -> list[dict]:
    """Locate an excerpt among page words and return line-level bounding boxes.

    Args:
        excerpt: The verbatim supporting text to locate.
        words: Page word geometry (dicts with ``text``/``x0``/``x1``/``top``/
            ``bottom``), as produced by the parser.
        min_token_ratio: Minimum fraction of excerpt tokens that must match in
            the best-aligned window to accept a location.

    Returns:
        A list of rectangles ``{"x0","top","x1","bottom"}`` (PDF points), one per
        line the matched run spans, ordered top-to-bottom. Empty if the excerpt
        cannot be located confidently.
    """
    ex_tokens = _excerpt_tokens(excerpt)
    if not ex_tokens or not words:
        return []

    # Words carrying a comparable token, paired with their geometry.
    indexed = [(i, _norm(w["text"])) for i, w in enumerate(words)]
    indexed = [(i, t) for i, t in indexed if t]
    if not indexed:
        return []

    window = len(ex_tokens)
    best_score = 0
    best_start = -1
    # Slide a window of the excerpt's length over the page tokens.
    for start in range(0, max(len(indexed) - window, 0) + 1):
        window_tokens = [t for _, t in indexed[start : start + window]]
        score = sum(1 for a, b in zip(window_tokens, ex_tokens, strict=False) if a == b)
        if score > best_score:
            best_score = score
            best_start = start

    if best_start < 0 or best_score / window < min_token_ratio:
        return []

    matched_word_indexes = [i for i, _ in indexed[best_start : best_start + window]]
    matched = [words[i] for i in matched_word_indexes]
    return _union_by_line(matched)


def _union_by_line(matched: list[dict]) -> list[dict]:
    """Group matched words into lines and union each line's bounds.

    Args:
        matched: The matched page words (with geometry).

    Returns:
        One rectangle per line, ordered top-to-bottom.
    """
    lines: list[list[dict]] = []
    for word in sorted(matched, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])

    rects: list[dict] = []
    for line in lines:
        rects.append(
            {
                "x0": round(min(w["x0"] for w in line), 2),
                "top": round(min(w["top"] for w in line), 2),
                "x1": round(max(w["x1"] for w in line), 2),
                "bottom": round(max(w["bottom"] for w in line), 2),
            }
        )
    return rects

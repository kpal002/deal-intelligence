"""Unit tests for excerpt -> bounding-box mapping (``dealintel.geometry``)."""

from __future__ import annotations

from dealintel.geometry import locate_bboxes


def _word(text: str, x0: float, top: float) -> dict:
    """Build a page-word geometry dict on a fixed-height/width grid."""
    return {"text": text, "x0": x0, "x1": x0 + 10 * len(text), "top": top, "bottom": top + 10}


# One line: "the team of 14 people" at top=100; a second line below at top=120.
PAGE_WORDS = [
    _word("The", 0, 100),
    _word("team", 40, 100),
    _word("of", 90, 100),
    _word("14", 110, 100),
    _word("people", 140, 100),
    _word("across", 0, 120),
    _word("engineering", 70, 120),
]


def test_single_line_box():
    """An excerpt on one line yields one rectangle spanning the matched words."""
    rects = locate_bboxes("team of 14", PAGE_WORDS)
    assert len(rects) == 1
    r = rects[0]
    assert r["top"] == 100
    assert r["x0"] == 40  # 'team'
    assert r["x1"] == 130  # end of '14'


def test_multi_line_span_returns_box_per_line():
    """An excerpt spanning a line break yields one rectangle per line."""
    rects = locate_bboxes("14 people across engineering", PAGE_WORDS)
    tops = sorted(r["top"] for r in rects)
    assert tops == [100, 120]


def test_unlocatable_returns_empty():
    """An excerpt whose tokens do not match returns no boxes."""
    assert locate_bboxes("quarterly revenue guidance", PAGE_WORDS) == []


def test_no_words_returns_empty():
    """No page geometry returns no boxes (never raises)."""
    assert locate_bboxes("team of 14", []) == []

"""Unit tests for span verification (``dealintel.verification``)."""

from __future__ import annotations

from dealintel.models.fact import SpanVerification
from dealintel.verification import verify_span

PAGE = (
    "NorthStar Logistics is run by a team of 14 across engineering, "
    "operations, and go-to-market.\nThe business reached an ARR of $4.2M."
)


def test_exact_match_returns_precise_span():
    """An excerpt present verbatim is located exactly and the span round-trips."""
    excerpt = "an ARR of $4.2M"
    start, end, status = verify_span(excerpt, PAGE)
    assert status is SpanVerification.VERIFIED_EXACT
    assert PAGE[start:end] == excerpt


def test_fuzzy_match_on_whitespace_difference():
    """An excerpt differing by whitespace/quotes is located by fuzzy alignment."""
    # Model reworded whitespace and quoting, so this is not an exact substring.
    excerpt = "a  team  of  14  across  engineering"
    start, end, status = verify_span(excerpt, PAGE)
    assert status is SpanVerification.VERIFIED_FUZZY
    assert start is not None
    assert end is not None
    # The located region overlaps the real text.
    assert "team of 14" in PAGE[start:end]


def test_unlocatable_excerpt_is_unverified():
    """An excerpt not in the source is UNVERIFIED with null offsets."""
    start, end, status = verify_span("revenue grew 300% in Antarctica", PAGE)
    assert status is SpanVerification.UNVERIFIED
    assert start is None
    assert end is None


def test_empty_inputs_are_unverified():
    """Empty excerpt or source text yields UNVERIFIED, never an error."""
    assert verify_span("", PAGE)[2] is SpanVerification.UNVERIFIED
    assert verify_span("anything", "")[2] is SpanVerification.UNVERIFIED

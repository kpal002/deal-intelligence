"""Unit tests for :mod:`simpero.normalize`.

This is the correctness-critical module — the comparable-value layer the whole
conflict-detection story rests on. Tests cover currency, percentage, count,
categorical text, the routing dispatcher, and the never-guess failure mode.
"""

from __future__ import annotations

import pytest
from simpero.models.fact import ClaimType, NormalizationStatus
from simpero.normalize import (
    normalize_claim_value,
    normalize_count,
    normalize_currency,
    normalize_multiple,
    normalize_percentage,
    normalize_year,
)

# --- Currency -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_numeric", "expected_unit"),
    [
        ("$5M", 5_000_000.0, "USD"),
        ("$5,000,000", 5_000_000.0, "USD"),
        ("5 million USD", 5_000_000.0, "USD"),
        ("$1.2B", 1_200_000_000.0, "USD"),
        ("$750k", 750_000.0, "USD"),
        ("€1.2B", 1_200_000_000.0, "EUR"),
        ("£500,000", 500_000.0, "GBP"),
        ("CAD 2 million", 2_000_000.0, "CAD"),
        ("12000000", 12_000_000.0, "USD"),  # bare number, currency context
    ],
)
def test_normalize_currency_values(raw, expected_numeric, expected_unit):
    """Currency strings collapse to one absolute numeric value and a unit."""
    result = normalize_currency(raw)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric == pytest.approx(expected_numeric)
    assert result.unit == expected_unit


def test_normalize_currency_distinct_strings_one_value():
    """The three canonical 'same value, different string' cases must agree."""
    forms = ["$5M", "$5,000,000", "5 million USD"]
    values = {normalize_currency(f).numeric for f in forms}
    assert values == {5_000_000.0}


def test_normalize_currency_unparseable():
    """A currency value with no number is UNPARSEABLE, never guessed."""
    result = normalize_currency("several million dollars")
    assert result.status is NormalizationStatus.UNPARSEABLE
    assert result.numeric is None
    assert result.unit is None


# --- Percentage -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15%", 15.0),
        ("0.15", 15.0),  # bare fraction interpreted as proportion
        ("150%", 150.0),
        ("0.5%", 0.5),  # explicit % wins over fraction heuristic
        ("100%", 100.0),
        ("1", 100.0),  # 1.0 as a proportion -> 100%
        ("42", 42.0),  # >1 bare number assumed already in percent
    ],
)
def test_normalize_percentage_values(raw, expected):
    """Percentages land on the 0–100 scale with the documented heuristics."""
    result = normalize_percentage(raw)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric == pytest.approx(expected)
    assert result.unit == "percent"


def test_normalize_percentage_unparseable():
    """No number means UNPARSEABLE."""
    result = normalize_percentage("a large share")
    assert result.status is NormalizationStatus.UNPARSEABLE
    assert result.numeric is None


# --- Count ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234 customers", 1_234.0),
        ("5k users", 5_000.0),
        ("12", 12.0),
        ("3 million users", 3_000_000.0),
    ],
)
def test_normalize_count_values(raw, expected):
    """Counts parse with optional scale words, unit 'count'."""
    result = normalize_count(raw)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric == pytest.approx(expected)
    assert result.unit == "count"


# --- Fund-metric normalizers (multiple / year) ----------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.8x", 1.8), ("2.0×", 2.0), ("1.5", 1.5), ("3.2x net", 3.2)],
)
def test_normalize_multiple_values(raw, expected):
    """Investment multiples parse to a number with unit 'multiple'."""
    result = normalize_multiple(raw)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric == pytest.approx(expected)
    assert result.unit == "multiple"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2018", 2018.0), ("vintage 2015", 2015.0), ("Fund of 2021 vintage", 2021.0)],
)
def test_normalize_year_values(raw, expected):
    """Vintage years parse to the 4-digit year with unit 'year'."""
    result = normalize_year(raw)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric == pytest.approx(expected)
    assert result.unit == "year"


def test_normalize_year_unparseable():
    """Text with no 4-digit year is UNPARSEABLE."""
    assert normalize_year("recent vintage").status is NormalizationStatus.UNPARSEABLE


def test_dispatch_net_irr_routes_to_percent_without_symbol():
    """A net_irr claim is treated as a percentage even without a '%'."""
    result = normalize_claim_value("18.5", ClaimType.NET_IRR)
    assert result.unit == "percent"
    assert result.numeric == pytest.approx(18.5)


def test_dispatch_tvpi_routes_to_multiple():
    """A TVPI claim routes to the multiple parser."""
    result = normalize_claim_value("1.8x", ClaimType.TVPI)
    assert result.unit == "multiple"
    assert result.numeric == pytest.approx(1.8)


def test_dispatch_fund_size_routes_to_currency():
    """A fund_size claim is treated as currency."""
    result = normalize_claim_value("$750M", ClaimType.FUND_SIZE)
    assert result.unit == "USD"
    assert result.numeric == pytest.approx(750_000_000.0)


def test_dispatch_vintage_year_routes_to_year():
    """A vintage_year claim routes to the year parser."""
    result = normalize_claim_value("2018", ClaimType.VINTAGE_YEAR)
    assert result.unit == "year"
    assert result.numeric == pytest.approx(2018.0)


# --- Dispatcher routing ---------------------------------------------------


def test_dispatch_revenue_routes_to_currency():
    """A REVENUE claim is treated as money even without a currency symbol."""
    result = normalize_claim_value("5 million", ClaimType.REVENUE)
    assert result.numeric == pytest.approx(5_000_000.0)
    assert result.unit == "USD"


def test_dispatch_percent_in_customer_metrics():
    """A '%' in a non-percent claim type is still routed to percentage."""
    result = normalize_claim_value("15% MoM", ClaimType.CUSTOMER_METRICS)
    assert result.unit == "percent"
    assert result.numeric == pytest.approx(15.0)


def test_dispatch_customer_metrics_count():
    """A plain count in CUSTOMER_METRICS routes to count."""
    result = normalize_claim_value("1,200 paying customers", ClaimType.CUSTOMER_METRICS)
    assert result.unit == "count"
    assert result.numeric == pytest.approx(1_200.0)


def test_dispatch_categorical_text():
    """Categorical claim types canonicalize text and never produce a number."""
    result = normalize_claim_value("  United   States ", ClaimType.COMPETITIVE_POSITIONING)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.numeric is None
    assert result.text == "united states"


def test_dispatch_empty_is_not_applicable():
    """An empty value yields NOT_APPLICABLE."""
    result = normalize_claim_value("   ", ClaimType.OTHER)
    assert result.status is NormalizationStatus.NOT_APPLICABLE
    assert result.numeric is None
    assert result.text is None


def test_dispatch_free_text_other_keeps_text_key():
    """Non-numeric free text under OTHER keeps a canonical text key, not UNPARSEABLE."""
    result = normalize_claim_value("Series B stage", ClaimType.OTHER)
    assert result.status is NormalizationStatus.NORMALIZED
    assert result.text == "series b stage"


def test_dispatch_unparseable_numeric_market_size():
    """A money claim with no parseable number stays UNPARSEABLE (never guessed)."""
    result = normalize_claim_value("a multi-billion dollar market", ClaimType.MARKET_SIZE)
    assert result.status is NormalizationStatus.UNPARSEABLE
    assert result.numeric is None

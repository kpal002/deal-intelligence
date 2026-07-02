"""Claim value normalization: raw string -> comparable value.

One underlying value has many string forms ("$5M", "$5,000,000", "5 million
USD"). Scoring and conflict detection compare normalized values, so this module
parses raw claim values into a numeric+unit, canonical text, or a status.

Contract:

- If a value is expected to be quantitative but does not parse, return
  ``UNPARSEABLE`` with null numeric fields; do not infer a number.
- The verbatim ``claim_value`` is owned by :class:`dealintel.models.fact.Fact`;
  this module only produces the normalized form.
- Pure and deterministic (no I/O), so it is unit-tested in isolation.

Entry point: :func:`normalize_claim_value`. Per-kind helpers
(:func:`normalize_currency`, :func:`normalize_percentage`,
:func:`normalize_count`, :func:`normalize_multiple`, :func:`normalize_year`)
are exposed for direct use.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from dealintel.models.fact import ClaimType, NormalizationStatus

# --- Lexicons -------------------------------------------------------------

#: Scale-word / suffix-letter multipliers. Lowercased keys; matched as whole
#: alphabetic tokens immediately following the numeric portion of a value.
_MULTIPLIERS: dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "trillion": 1e12,
}

#: Currency symbols mapped to ISO-ish codes used as ``normalized_value_unit``.
_CURRENCY_SYMBOLS: dict[str, str] = {"$": "USD", "€": "EUR", "£": "GBP"}

#: Recognized currency codes (matched case-sensitively as whole words).
_CURRENCY_CODES: frozenset[str] = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"}
)

#: Claim types whose values are inherently quantitative currency amounts.
_CURRENCY_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.MARKET_SIZE,
        ClaimType.REVENUE,
        ClaimType.FUNDING_HISTORY,
        ClaimType.FUND_SIZE,
    }
)

#: Claim types expressed as a percentage even without an explicit '%'
#: (e.g. "net IRR of 18.5").
_PERCENT_CLAIM_TYPES: frozenset[ClaimType] = frozenset({ClaimType.NET_IRR})

#: Claim types expressed as an investment multiple (e.g. TVPI/DPI of 1.8x).
_MULTIPLE_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {ClaimType.TVPI, ClaimType.DPI}
)

#: Matches an investment-multiple value such as "1.8x" or "2.0×".
_MULTIPLE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]", re.IGNORECASE)

#: Claim types whose values are categorical text, not numbers. Note
#: TEAM_BACKGROUND is intentionally NOT here: "team of 14" must normalize to a
#: numeric count so a "team of 5+" criterion can compare, while "ex-Google
#: founders" still falls through to the canonical-text path.
_CATEGORICAL_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {ClaimType.COMPETITIVE_POSITIONING}
)

#: Matches the first numeric token: optional sign, optional thousands grouping,
#: optional decimal. Examples: ``-1,234.5``, ``5``, ``.15``, ``5000000``.
_NUMBER_RE = re.compile(
    r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # grouped: 1,234 or 1,234.5
    r"|[-+]?\d+(?:\.\d+)?"  # plain: 5 or 5.0
    r"|[-+]?\.\d+"  # leading-dot: .15
)

#: Leading alphabetic token (used to read a multiplier directly after a number).
_LEADING_ALPHA_RE = re.compile(r"[a-z]+")


class NormalizedValue(BaseModel):
    """Structured result of normalizing one raw claim value.

    Mirrors the normalized fields on :class:`dealintel.models.fact.Fact` so a
    caller can splat it straight onto a Fact.

    Attributes:
        numeric: Parsed numeric magnitude, or ``None`` if non-numeric/unparsed.
        unit: Canonical unit for ``numeric`` (e.g. ``"USD"``, ``"percent"``,
            ``"count"``), or ``None``.
        text: Canonical lowercased form for categorical values, or ``None``.
        status: Whether normalization succeeded, failed, or was not applicable.
    """

    numeric: float | None = None
    unit: str | None = None
    text: str | None = None
    status: NormalizationStatus = NormalizationStatus.NOT_APPLICABLE


# --- Low-level parsing helpers -------------------------------------------


def _find_number_and_rest(text: str) -> tuple[float | None, str]:
    """Extract the first numeric token and the remaining text after it.

    Args:
        text: Arbitrary value text, e.g. ``"5 million USD"``.

    Returns:
        A ``(number, rest)`` pair. ``number`` is the parsed float with thousands
        separators removed, or ``None`` if no number is present. ``rest`` is the
        lowercased substring following the number (stripped), used to read a
        trailing multiplier such as ``"million"`` or ``"m"``.
    """
    match = _NUMBER_RE.search(text)
    if match is None:
        return None, ""
    number = float(match.group().replace(",", ""))
    rest = text[match.end() :].strip().lower()
    return number, rest


def _multiplier_for(rest: str) -> float:
    """Return the scale multiplier implied by the text following a number.

    Reads the leading alphabetic token of ``rest`` (e.g. ``"m"`` from
    ``"m usd"``, ``"million"`` from ``"million in arr"``). Unknown or absent
    tokens yield ``1.0``.

    Args:
        rest: Lowercased text immediately following the numeric token.

    Returns:
        The multiplier (e.g. ``1e6`` for "million"), or ``1.0`` if none applies.
    """
    token_match = _LEADING_ALPHA_RE.match(rest)
    if token_match is None:
        return 1.0
    return _MULTIPLIERS.get(token_match.group(), 1.0)


def _detect_currency_unit(raw: str) -> str | None:
    """Identify the currency of a raw value from its symbol or code.

    Args:
        raw: The original value text.

    Returns:
        A currency code (``"USD"``, ``"EUR"``, ...) or ``None`` if no currency
        indicator is present. Codes are matched case-sensitively as whole words
        so "5 million" does not spuriously match.
    """
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in raw:
            return code
    for token in re.findall(r"[A-Z]{3}", raw):
        if token in _CURRENCY_CODES:
            return str(token)
    return None


# --- Per-kind normalizers -------------------------------------------------


def normalize_currency(raw: str) -> NormalizedValue:
    """Normalize a currency amount to an absolute numeric value plus unit.

    Handles symbols ($, €, £), codes (USD, EUR, ...), thousands separators, and
    scale words/suffixes (k, m, million, b, billion). When no currency indicator
    is present the unit defaults to ``"USD"`` (this helper is only invoked once
    the value is known to be monetary).

    Examples:
        ``"$5M"`` -> ``5_000_000.0 USD``
        ``"$5,000,000"`` -> ``5_000_000.0 USD``
        ``"5 million USD"`` -> ``5_000_000.0 USD``
        ``"€1.2B"`` -> ``1_200_000_000.0 EUR``

    Args:
        raw: The original currency value text.

    Returns:
        A :class:`NormalizedValue`. ``UNPARSEABLE`` (with null numeric) if no
        number can be found.
    """
    number, rest = _find_number_and_rest(raw)
    if number is None:
        return NormalizedValue(status=NormalizationStatus.UNPARSEABLE)
    unit = _detect_currency_unit(raw) or "USD"
    value = number * _multiplier_for(rest)
    return NormalizedValue(
        numeric=value, unit=unit, status=NormalizationStatus.NORMALIZED
    )


def normalize_percentage(raw: str) -> NormalizedValue:
    """Normalize a percentage to a number on the 0–100 scale, unit ``percent``.

    A value with an explicit ``%`` is taken at face value. A bare fraction in
    ``[0, 1]`` is interpreted as a proportion and scaled to percent (``0.15`` ->
    ``15.0``); a bare number above 1 is assumed already expressed in percent.

    Examples:
        ``"15%"`` -> ``15.0 percent``
        ``"0.15"`` -> ``15.0 percent``
        ``"150%"`` -> ``150.0 percent``

    Args:
        raw: The original percentage value text.

    Returns:
        A :class:`NormalizedValue`. ``UNPARSEABLE`` if no number can be found.
    """
    number, _ = _find_number_and_rest(raw)
    if number is None:
        return NormalizedValue(status=NormalizationStatus.UNPARSEABLE)
    if "%" in raw:
        value = number
    elif 0.0 <= number <= 1.0:
        value = number * 100.0
    else:
        value = number
    return NormalizedValue(
        numeric=value, unit="percent", status=NormalizationStatus.NORMALIZED
    )


def normalize_count(raw: str) -> NormalizedValue:
    """Normalize a plain count (optionally with a scale word), unit ``count``.

    Examples:
        ``"1,234 customers"`` -> ``1234.0 count``
        ``"5k users"`` -> ``5000.0 count``
        ``"12"`` -> ``12.0 count``

    Args:
        raw: The original count value text.

    Returns:
        A :class:`NormalizedValue`. ``UNPARSEABLE`` if no number can be found.
    """
    number, rest = _find_number_and_rest(raw)
    if number is None:
        return NormalizedValue(status=NormalizationStatus.UNPARSEABLE)
    value = number * _multiplier_for(rest)
    return NormalizedValue(
        numeric=value, unit="count", status=NormalizationStatus.NORMALIZED
    )


def normalize_multiple(raw: str) -> NormalizedValue:
    """Normalize an investment multiple (e.g. TVPI/DPI "1.8x"), unit ``multiple``.

    Accepts an explicit ``x``/``×`` suffix or a bare number (already a multiple).

    Examples:
        ``"1.8x"`` -> ``1.8 multiple``
        ``"2.0×"`` -> ``2.0 multiple``
        ``"1.5"`` -> ``1.5 multiple``

    Args:
        raw: The original multiple value text.

    Returns:
        A :class:`NormalizedValue`. ``UNPARSEABLE`` if no number can be found.
    """
    match = _MULTIPLE_RE.search(raw)
    if match is not None:
        return NormalizedValue(
            numeric=float(match.group(1)),
            unit="multiple",
            status=NormalizationStatus.NORMALIZED,
        )
    number, _ = _find_number_and_rest(raw)
    if number is None:
        return NormalizedValue(status=NormalizationStatus.UNPARSEABLE)
    return NormalizedValue(
        numeric=number, unit="multiple", status=NormalizationStatus.NORMALIZED
    )


def normalize_year(raw: str) -> NormalizedValue:
    """Normalize a vintage/calendar year to an integer-valued number, unit ``year``.

    Examples:
        ``"2018"`` -> ``2018 year``
        ``"vintage 2015"`` -> ``2015 year``

    Args:
        raw: The original year value text.

    Returns:
        A :class:`NormalizedValue`. ``UNPARSEABLE`` if no 4-digit year is found.
    """
    match = re.search(r"(19|20)\d{2}", raw)
    if match is None:
        return NormalizedValue(status=NormalizationStatus.UNPARSEABLE)
    return NormalizedValue(
        numeric=float(match.group(0)),
        unit="year",
        status=NormalizationStatus.NORMALIZED,
    )


def _canonical_text(raw: str) -> str:
    """Collapse a categorical value to a stable comparison key.

    Lowercases, trims, and collapses internal whitespace so ``"United States"``
    and ``"  united   states "`` compare equal.

    Args:
        raw: The original categorical value text.

    Returns:
        The canonicalized form.
    """
    return str(re.sub(r"\s+", " ", raw.strip().lower()))


# --- Dispatcher -----------------------------------------------------------


def normalize_claim_value(raw_value: str, claim_type: ClaimType) -> NormalizedValue:
    """Normalize a raw claim value into its comparable form.

    Routing logic, in order of precedence:

    1. Empty/blank value -> ``NOT_APPLICABLE``.
    2. Categorical claim types (competitive positioning) -> canonical text form.
    3. Multiple claim types (TVPI/DPI) or an explicit ``Nx`` value -> multiple.
    4. Vintage-year claim type -> year.
    5. An explicit ``%`` anywhere, or a percent claim type (net IRR) -> percentage.
    6. A currency claim type or a currency indicator ($, €, £, ISO code) ->
       currency.
    7. Any remaining value containing a number -> count.
    8. Otherwise (free text, no number) -> canonical text form.

    This is intentionally driven by both ``claim_type`` and value content: the
    claim type sets expectation (a REVENUE claim should be money), while content
    catches cases the type alone would miss (a percentage embedded in a
    CUSTOMER_METRICS claim).

    Args:
        raw_value: The verbatim value string from extraction.
        claim_type: The claim's ontology type, used to bias routing.

    Returns:
        A :class:`NormalizedValue`. Numeric claims that cannot be parsed return
        ``UNPARSEABLE`` (null numeric) rather than a guessed value.
    """
    if not raw_value or not raw_value.strip():
        return NormalizedValue(status=NormalizationStatus.NOT_APPLICABLE)

    if claim_type in _CATEGORICAL_CLAIM_TYPES:
        return NormalizedValue(
            text=_canonical_text(raw_value),
            status=NormalizationStatus.NORMALIZED,
        )

    if claim_type in _MULTIPLE_CLAIM_TYPES or _MULTIPLE_RE.search(raw_value):
        return normalize_multiple(raw_value)

    if claim_type is ClaimType.VINTAGE_YEAR:
        return normalize_year(raw_value)

    if "%" in raw_value or claim_type in _PERCENT_CLAIM_TYPES:
        return normalize_percentage(raw_value)

    if claim_type in _CURRENCY_CLAIM_TYPES or _detect_currency_unit(raw_value):
        return normalize_currency(raw_value)

    number, _ = _find_number_and_rest(raw_value)
    if number is not None:
        return normalize_count(raw_value)

    # Free-text value with no number: keep a canonical text key rather than
    # marking it unparseable — there was never a number to parse.
    return NormalizedValue(
        text=_canonical_text(raw_value), status=NormalizationStatus.NORMALIZED
    )

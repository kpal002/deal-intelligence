"""Unit tests for the mandate scoring engine.

The scoring engine is the most reviewable "correctness" component, so these
tests cover: weight normalization, knockout handling, each operator, unit
compatibility, traceability of contributions, and the immutable-snapshot total.
"""

from __future__ import annotations

import uuid

import pytest
from simpero.models.fact import (
    ClaimType,
    ExtractionMethod,
    Fact,
    NormalizationStatus,
)
from simpero.models.mandate import Mandate, MandateCriterion, MandateOperator
from simpero.scoring.engine import score_deal

DEAL_ID = uuid.uuid4()


def make_fact(
    claim_type: ClaimType,
    *,
    numeric: float | None = None,
    unit: str | None = None,
    text: str | None = None,
    value: str = "x",
    confidence: float = 0.9,
) -> Fact:
    """Build a Fact for tests with sensible defaults.

    Args:
        claim_type: The claim type.
        numeric: Normalized numeric value, if any.
        unit: Normalized unit, if any.
        text: Normalized text value, if any.
        value: Verbatim claim value.
        confidence: Extraction confidence.

    Returns:
        A valid :class:`Fact`.
    """
    return Fact(
        deal_id=DEAL_ID,
        chunk_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        entity_raw_name="NorthStar Logistics",
        claim_type=claim_type,
        claim_value=value,
        normalized_value_numeric=numeric,
        normalized_value_unit=unit,
        normalized_value_text=text,
        normalization_status=(
            NormalizationStatus.NORMALIZED
            if (numeric is not None or text is not None)
            else NormalizationStatus.NOT_APPLICABLE
        ),
        source_page=1,
        source_excerpt=f"excerpt for {claim_type.value}",
        extraction_method=ExtractionMethod.SONNET_EXTRACTION,
        confidence_score=confidence,
    )


def make_criterion(
    name: str,
    claim_type: ClaimType,
    operator: MandateOperator,
    threshold: object,
    *,
    unit: str | None = None,
    weight: float = 0.5,
    knockout: bool = False,
) -> MandateCriterion:
    """Build a MandateCriterion for tests.

    Args:
        name: Criterion name.
        claim_type: Claim type to match.
        operator: Comparison operator.
        threshold: Threshold value.
        unit: Threshold unit.
        weight: Relative weight.
        knockout: Whether it is a knockout.

    Returns:
        A valid :class:`MandateCriterion`.
    """
    return MandateCriterion(
        name=name,
        description=name,
        claim_type=claim_type,
        operator=operator,
        threshold=threshold,
        threshold_unit=unit,
        weight=weight,
        is_knockout=knockout,
    )


def make_mandate(criteria: list[MandateCriterion]) -> Mandate:
    """Wrap criteria in a minimal Mandate.

    Args:
        criteria: The criteria.

    Returns:
        A valid :class:`Mandate`.
    """
    return Mandate(
        fund_name="Test Fund",
        description="test",
        criteria=criteria,
    )


def test_all_criteria_met_scores_100():
    """A deal satisfying every criterion scores 100 with weights normalized."""
    mandate = make_mandate(
        [
            make_criterion("ARR floor", ClaimType.REVENUE, MandateOperator.GTE, 1_000_000, unit="USD", weight=0.6),
            make_criterion("Team", ClaimType.TEAM_BACKGROUND, MandateOperator.GTE, 5, unit="count", weight=0.4),
        ]
    )
    facts = [
        make_fact(ClaimType.REVENUE, numeric=4_200_000, unit="USD"),
        make_fact(ClaimType.TEAM_BACKGROUND, numeric=14, unit="count"),
    ]
    result = score_deal(DEAL_ID, facts, mandate)
    assert result.total_score == pytest.approx(100.0)
    assert not result.knockout_triggered
    assert all(cs.met for cs in result.criterion_scores)


def test_weights_are_normalized_regardless_of_authoring():
    """Weights that do not sum to 1 are normalized; one met criterion of two."""
    mandate = make_mandate(
        [
            make_criterion("ARR", ClaimType.REVENUE, MandateOperator.GTE, 1_000_000, unit="USD", weight=3.0),
            make_criterion("Market", ClaimType.MARKET_SIZE, MandateOperator.GTE, 1_000_000_000, unit="USD", weight=1.0),
        ]
    )
    facts = [make_fact(ClaimType.REVENUE, numeric=4_200_000, unit="USD")]  # market missing
    result = score_deal(DEAL_ID, facts, mandate)
    # ARR weight 3/4 met, market 1/4 unmet -> 75.
    assert result.total_score == pytest.approx(75.0)


def test_knockout_failure_forces_zero():
    """A failed knockout zeroes the total even if other criteria pass."""
    mandate = make_mandate(
        [
            make_criterion("ARR floor", ClaimType.REVENUE, MandateOperator.GTE, 1_000_000, unit="USD", weight=0.5, knockout=True),
            make_criterion("Team", ClaimType.TEAM_BACKGROUND, MandateOperator.GTE, 5, unit="count", weight=0.5),
        ]
    )
    facts = [
        make_fact(ClaimType.REVENUE, numeric=200_000, unit="USD"),  # below floor
        make_fact(ClaimType.TEAM_BACKGROUND, numeric=14, unit="count"),  # passes
    ]
    result = score_deal(DEAL_ID, facts, mandate)
    assert result.knockout_triggered
    assert result.knockout_criterion_name == "ARR floor"
    assert result.total_score == 0.0


def test_lte_operator():
    """The lte operator picks the smallest comparable value."""
    mandate = make_mandate(
        [make_criterion("ARR ceiling", ClaimType.REVENUE, MandateOperator.LTE, 10_000_000, unit="USD", weight=1.0)]
    )
    facts = [make_fact(ClaimType.REVENUE, numeric=4_200_000, unit="USD")]
    result = score_deal(DEAL_ID, facts, mandate)
    assert result.criterion_scores[0].met
    assert result.total_score == pytest.approx(100.0)


def test_contains_operator_on_text():
    """The contains operator matches a substring of the normalized text."""
    mandate = make_mandate(
        [make_criterion("Sector", ClaimType.COMPETITIVE_POSITIONING, MandateOperator.CONTAINS, "saas", weight=1.0)]
    )
    facts = [make_fact(ClaimType.COMPETITIVE_POSITIONING, text="b2b saas platform", value="B2B SaaS platform")]
    result = score_deal(DEAL_ID, facts, mandate)
    assert result.criterion_scores[0].met


def test_unit_incompatibility_makes_criterion_unmet():
    """A fact in the wrong unit is not comparable, so the criterion is unmet."""
    mandate = make_mandate(
        [make_criterion("ARR", ClaimType.REVENUE, MandateOperator.GTE, 1_000_000, unit="USD", weight=1.0)]
    )
    facts = [make_fact(ClaimType.REVENUE, numeric=5_000_000, unit="EUR")]  # wrong unit
    result = score_deal(DEAL_ID, facts, mandate)
    assert not result.criterion_scores[0].met
    assert "comparable" in result.criterion_scores[0].explanation


def test_missing_fact_is_unmet_not_error():
    """A criterion with no matching fact is unmet, not an exception."""
    mandate = make_mandate(
        [make_criterion("Team", ClaimType.TEAM_BACKGROUND, MandateOperator.GTE, 5, unit="count", weight=1.0)]
    )
    result = score_deal(DEAL_ID, [], mandate)
    assert not result.criterion_scores[0].met
    assert result.total_score == 0.0
    assert result.facts_evaluated == 0


def test_traceability_contributions_carry_citation():
    """A met criterion records the driving fact with page + excerpt inline."""
    mandate = make_mandate(
        [make_criterion("ARR", ClaimType.REVENUE, MandateOperator.GTE, 1_000_000, unit="USD", weight=1.0)]
    )
    fact = make_fact(ClaimType.REVENUE, numeric=4_200_000, unit="USD", value="$4.2M")
    result = score_deal(DEAL_ID, [fact], mandate, {fact.entity_id: "NorthStar Logistics"})
    contribution = result.criterion_scores[0].facts_used[0]
    assert contribution.fact_id == fact.fact_id
    assert contribution.source_page == 1
    assert contribution.entity_canonical_name == "NorthStar Logistics"
    assert contribution.contribution_direction == "supports"


def test_gte_picks_largest_value_as_evidence():
    """With multiple revenue facts, gte uses the largest as supporting evidence."""
    mandate = make_mandate(
        [make_criterion("ARR", ClaimType.REVENUE, MandateOperator.GTE, 3_000_000, unit="USD", weight=1.0)]
    )
    facts = [
        make_fact(ClaimType.REVENUE, numeric=1_100_000, unit="USD", value="$1.1M"),
        make_fact(ClaimType.REVENUE, numeric=4_200_000, unit="USD", value="$4.2M"),
    ]
    result = score_deal(DEAL_ID, facts, mandate)
    assert result.criterion_scores[0].met
    assert result.criterion_scores[0].facts_used[0].claim_value == "$4.2M"

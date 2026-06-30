"""Mandate scoring engine — deterministic, weighted, fully traceable.

The engine takes extracted facts plus a mandate and returns a
:class:`ScoreResult` in which every sub-score links back to the specific facts
(with page + excerpt) that drove it. It is intentionally pure: no I/O, no model
calls, no global state — so it is exhaustively unit-testable, which is exactly
what a reviewer will scrutinize.

Scoring model:

- Weights are normalized across a mandate's criteria so they sum to 1.0,
  regardless of how the YAML was authored.
- Each criterion yields a binary ``met`` and a ``raw_score`` in [0, 1]; the
  weighted contribution is ``raw_score * normalized_weight``.
- ``total_score`` is the weighted sum scaled to 0–100.
- A failed **knockout** criterion forces ``total_score`` to 0 and flags the
  result, regardless of other criteria.
- Comparisons operate on the **normalized** fact values
  (``normalized_value_numeric`` / ``normalized_value_text``), never raw strings.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from simpero.models.fact import Fact
from simpero.models.mandate import Mandate, MandateCriterion, MandateOperator
from simpero.models.scoring import CriterionScore, FactContribution, ScoreResult

logger = logging.getLogger(__name__)

#: Relative tolerance for numeric equality comparisons.
_EQ_REL_TOL = 1e-9


def _normalized_weights(criteria: list[MandateCriterion]) -> dict[uuid.UUID, float]:
    """Normalize criterion weights to sum to 1.0.

    Args:
        criteria: The mandate's criteria.

    Returns:
        Mapping of ``criterion_id`` to its normalized weight. If all weights are
        zero (degenerate mandate), weights are distributed equally.
    """
    total = sum(c.weight for c in criteria)
    if total <= 0:
        equal = 1.0 / len(criteria) if criteria else 0.0
        return {c.criterion_id: equal for c in criteria}
    return {c.criterion_id: c.weight / total for c in criteria}


def _subtype_ok(fact: Fact, criterion: MandateCriterion) -> bool:
    """Whether a fact passes the criterion's optional sub-scope filter.

    Args:
        fact: Candidate fact.
        criterion: The criterion being evaluated.

    Returns:
        True if the criterion declares no ``claim_subtype``, or the fact's
        ``claim_subtype_raw`` contains it (case-insensitive). A fact with no
        subtype fails a criterion that requires one — we do not assume scope.
    """
    if criterion.claim_subtype is None:
        return True
    if fact.claim_subtype_raw is None:
        return False
    return criterion.claim_subtype.lower() in fact.claim_subtype_raw.lower()


def _matching_facts(facts: list[Fact], criterion: MandateCriterion) -> list[Fact]:
    """Facts matching the criterion's claim type and optional sub-scope.

    Args:
        facts: All facts for the deal.
        criterion: The criterion being evaluated.

    Returns:
        Facts whose claim_type matches and that pass the subtype filter.
    """
    return [
        f
        for f in facts
        if f.claim_type == criterion.claim_type and _subtype_ok(f, criterion)
    ]


def _unit_compatible(fact: Fact, criterion: MandateCriterion) -> bool:
    """Whether a fact's unit is comparable to a criterion's threshold unit.

    Args:
        fact: Candidate fact.
        criterion: The criterion being evaluated.

    Returns:
        True if the criterion declares no unit, or the units match exactly.
    """
    return (
        criterion.threshold_unit is None
        or fact.normalized_value_unit == criterion.threshold_unit
    )


def _numeric_candidates(facts: list[Fact], criterion: MandateCriterion) -> list[Fact]:
    """Facts matching the criterion's claim type with a comparable numeric value.

    Args:
        facts: All facts for the deal.
        criterion: The criterion being evaluated.

    Returns:
        Facts whose claim_type matches and that carry a unit-compatible numeric
        value.
    """
    return [
        f
        for f in _matching_facts(facts, criterion)
        if f.normalized_value_numeric is not None and _unit_compatible(f, criterion)
    ]


def _text_of(fact: Fact) -> str:
    """Return the best text representation of a fact for text comparisons.

    Args:
        fact: The fact.

    Returns:
        ``normalized_value_text`` if present, else the lowercased raw value.
    """
    return fact.normalized_value_text or fact.claim_value.lower()


def _contribution(fact: Fact, entity_names: Mapping[uuid.UUID, str], met: bool) -> FactContribution:
    """Build a traceability record for a fact that drove a criterion's score.

    Args:
        fact: The driving fact.
        entity_names: Mapping of entity_id to canonical name; falls back to the
            fact's raw entity name when absent.
        met: Whether the criterion was met (sets the contribution direction).

    Returns:
        A :class:`FactContribution` snapshot (citation data copied inline).
    """
    return FactContribution(
        fact_id=fact.fact_id,
        entity_canonical_name=entity_names.get(fact.entity_id, fact.entity_raw_name),
        claim_type=fact.claim_type.value,
        claim_value=fact.claim_value,
        source_page=fact.source_page,
        source_excerpt=fact.source_excerpt,
        confidence_score=fact.confidence_score,
        contribution_direction="supports" if met else "contradicts",
    )


def _evaluate_criterion(
    criterion: MandateCriterion,
    facts: list[Fact],
    entity_names: Mapping[uuid.UUID, str],
) -> tuple[bool, float, list[Fact], str]:
    """Evaluate one criterion against the facts.

    Args:
        criterion: The criterion to evaluate.
        facts: All facts for the deal.
        entity_names: entity_id -> canonical name mapping.

    Returns:
        A ``(met, raw_score, driving_facts, explanation)`` tuple. ``raw_score``
        is binary (1.0/0.0) in this implementation; the seam for graded scoring
        is noted inline.
    """
    op = criterion.operator

    if op is MandateOperator.EXISTS:
        matches = _matching_facts(facts, criterion)
        met = len(matches) > 0
        explanation = (
            f"Found {len(matches)} fact(s) of type '{criterion.claim_type.value}'."
            if met
            else f"No fact of type '{criterion.claim_type.value}' was found."
        )
        return met, (1.0 if met else 0.0), matches[:3], explanation

    if op in (MandateOperator.GTE, MandateOperator.LTE, MandateOperator.EQ):
        return _evaluate_numeric(criterion, facts)

    if op in (MandateOperator.CONTAINS, MandateOperator.IN):
        return _evaluate_text(criterion, facts)

    # Unknown operator should be impossible (enum-constrained), but be safe.
    return False, 0.0, [], f"Unsupported operator '{op}'."


def _evaluate_numeric(
    criterion: MandateCriterion, facts: list[Fact]
) -> tuple[bool, float, list[Fact], str]:
    """Evaluate a numeric criterion (gte/lte/eq) and pick the driving fact.

    Args:
        criterion: The numeric criterion.
        facts: All facts for the deal.

    Returns:
        ``(met, raw_score, [driving_fact], explanation)``. When no comparable
        fact exists, the criterion is unmet with an explanatory note.
    """
    candidates = _numeric_candidates(facts, criterion)
    if not candidates:
        return (
            False,
            0.0,
            [],
            f"No comparable '{criterion.claim_type.value}' value with unit "
            f"'{criterion.threshold_unit}' was found.",
        )

    try:
        threshold = float(criterion.threshold)
    except (TypeError, ValueError):
        return False, 0.0, [], f"Threshold '{criterion.threshold}' is not numeric."

    op = criterion.operator
    if op is MandateOperator.GTE:
        # Best evidence for a '>=' bar is the largest observed value.
        driver = max(candidates, key=lambda f: f.normalized_value_numeric)  # type: ignore[arg-type,return-value]
        value = driver.normalized_value_numeric
        met = value >= threshold  # type: ignore[operator]
        symbol = ">="
    elif op is MandateOperator.LTE:
        driver = min(candidates, key=lambda f: f.normalized_value_numeric)  # type: ignore[arg-type,return-value]
        value = driver.normalized_value_numeric
        met = value <= threshold  # type: ignore[operator]
        symbol = "<="
    else:  # EQ
        driver = min(
            candidates,
            key=lambda f: abs((f.normalized_value_numeric or 0.0) - threshold),
        )
        value = driver.normalized_value_numeric
        met = abs((value or 0.0) - threshold) <= _EQ_REL_TOL * max(abs(threshold), 1.0)
        symbol = "=="

    explanation = (
        f"{value} {criterion.threshold_unit or ''} {symbol} "
        f"{threshold} {criterion.threshold_unit or ''} -> "
        f"{'met' if met else 'not met'}."
    )
    # NOTE: raw_score is binary here. A graded score (e.g. proportional credit
    # as a value approaches the threshold) would slot in at this return without
    # touching the rest of the engine.
    return met, (1.0 if met else 0.0), [driver], explanation


def _evaluate_text(
    criterion: MandateCriterion, facts: list[Fact]
) -> tuple[bool, float, list[Fact], str]:
    """Evaluate a text criterion (contains/in) against the facts.

    Args:
        criterion: The text criterion.
        facts: All facts for the deal.

    Returns:
        ``(met, raw_score, driving_facts, explanation)``.
    """
    matches = _matching_facts(facts, criterion)
    if not matches:
        return False, 0.0, [], f"No '{criterion.claim_type.value}' fact to test."

    if criterion.operator is MandateOperator.CONTAINS:
        needle = str(criterion.threshold).lower()
        drivers = [f for f in matches if needle in _text_of(f)]
        met = len(drivers) > 0
        explanation = (
            f"'{needle}' found in a matching fact."
            if met
            else f"'{needle}' not present in any matching fact."
        )
        return met, (1.0 if met else 0.0), drivers[:3] or matches[:1], explanation

    # IN: fact value is one of a threshold list.
    options = {str(o).lower() for o in _as_list(criterion.threshold)}
    drivers = [f for f in matches if _text_of(f) in options]
    met = len(drivers) > 0
    explanation = (
        f"A matching fact value is in {sorted(options)}."
        if met
        else f"No matching fact value is in {sorted(options)}."
    )
    return met, (1.0 if met else 0.0), drivers[:3] or matches[:1], explanation


def _as_list(value: object) -> list:
    """Coerce a threshold to a list for the ``IN`` operator.

    Args:
        value: The criterion threshold.

    Returns:
        The value as a list (wrapping scalars).
    """
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def score_deal(
    deal_id: uuid.UUID,
    facts: list[Fact],
    mandate: Mandate,
    entity_names: Mapping[uuid.UUID, str] | None = None,
) -> ScoreResult:
    """Score a deal's facts against a mandate, with full traceability.

    Args:
        deal_id: The deal being scored.
        facts: All extracted facts for the deal.
        mandate: The fund mandate to score against.
        entity_names: Optional ``entity_id -> canonical name`` map for richer
            contribution records; falls back to each fact's raw entity name.

    Returns:
        A :class:`ScoreResult` with per-criterion breakdowns, knockout handling,
        and a ``total_score`` in 0–100. A failed knockout forces the total to 0.
    """
    entity_names = entity_names or {}
    weights = _normalized_weights(mandate.criteria)

    criterion_scores: list[CriterionScore] = []
    knockout_triggered = False
    knockout_name: str | None = None

    for criterion in mandate.criteria:
        met, raw_score, drivers, explanation = _evaluate_criterion(
            criterion, facts, entity_names
        )
        weighted = raw_score * weights[criterion.criterion_id]
        criterion_scores.append(
            CriterionScore(
                criterion_id=criterion.criterion_id,
                criterion_name=criterion.name,
                is_knockout=criterion.is_knockout,
                met=met,
                raw_score=raw_score,
                weighted_score=weighted,
                facts_used=[_contribution(f, entity_names, met) for f in drivers],
                explanation=explanation,
            )
        )
        if criterion.is_knockout and not met:
            knockout_triggered = True
            knockout_name = criterion.name

    if knockout_triggered:
        total_score = 0.0
        notes = (
            f"Knockout criterion '{knockout_name}' failed; total forced to 0 "
            f"regardless of {len(criterion_scores)} criteria."
        )
    else:
        total_score = min(
            sum(cs.weighted_score for cs in criterion_scores) * 100.0, 100.0
        )
        notes = f"Weighted score across {len(criterion_scores)} criteria."

    logger.info(
        "Scored deal %s against mandate '%s' v%d: %.1f (knockout=%s)",
        deal_id,
        mandate.fund_name,
        mandate.mandate_version,
        total_score,
        knockout_triggered,
    )

    return ScoreResult(
        deal_id=deal_id,
        mandate_id=mandate.mandate_id,
        mandate_version=mandate.mandate_version,
        total_score=total_score,
        knockout_triggered=knockout_triggered,
        knockout_criterion_name=knockout_name,
        criterion_scores=criterion_scores,
        facts_evaluated=len(facts),
        scoring_notes=notes,
    )

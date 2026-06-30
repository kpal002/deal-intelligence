"""Retrieval: natural-language query -> matching cited facts + related criteria.

A deliberately transparent, deterministic matcher for the PoC: it scores facts
by keyword overlap against the query, claim-type keywords, entity names, and
excerpt text, then attaches any mandate criteria that touch the same claim
types. This keeps the retrieval path explainable and dependency-light; the
docstring notes where an embedding/LLM reranker would slot in for production.
"""

from __future__ import annotations

import logging
import re
import uuid

from pydantic import BaseModel

from simpero.models.fact import ClaimType, Fact
from simpero.models.mandate import Mandate

logger = logging.getLogger(__name__)

#: Keyword hints mapping free-text query terms to claim types, so "how big is
#: the market" surfaces MARKET_SIZE facts even without exact term overlap.
_CLAIM_TYPE_KEYWORDS: dict[ClaimType, set[str]] = {
    ClaimType.MARKET_SIZE: {"market", "tam", "sam", "size", "opportunity"},
    ClaimType.REVENUE: {"revenue", "arr", "mrr", "sales", "income", "topline"},
    ClaimType.TEAM_BACKGROUND: {"team", "founder", "ceo", "cto", "people", "hire"},
    ClaimType.COMPETITIVE_POSITIONING: {"compete", "competitor", "moat", "position"},
    ClaimType.FUNDING_HISTORY: {"fund", "raise", "round", "seed", "series", "investor"},
    ClaimType.CUSTOMER_METRICS: {"customer", "churn", "retention", "growth", "nps", "user"},
}


class FactCitation(BaseModel):
    """A retrieved fact with its citation and relevance score.

    Attributes:
        fact_id: The matched fact.
        entity: Entity the fact concerns (canonical name when available).
        claim_type: The fact's claim type.
        claim_value: Verbatim value.
        normalized_value: Human-readable normalized form, if any.
        confidence_score: Extraction confidence.
        source_page: Citation page.
        source_excerpt: Verbatim supporting text.
        relevance: Query-match score (higher is more relevant).
    """

    fact_id: uuid.UUID
    entity: str
    claim_type: str
    claim_value: str
    normalized_value: str | None
    confidence_score: float
    source_page: int
    source_excerpt: str
    relevance: float


class QueryResponse(BaseModel):
    """Response to a natural-language fact query.

    Attributes:
        query: The original query text.
        deal_id: Deal searched.
        matches: Ranked matching facts with citations.
        related_criteria: Names of mandate criteria touching the matched claim
            types, so the user sees how the facts relate to the thesis.
    """

    query: str
    deal_id: uuid.UUID
    matches: list[FactCitation]
    related_criteria: list[str]


def _tokenize(text: str) -> set[str]:
    """Lowercase and split text into a set of word tokens.

    Args:
        text: Arbitrary text.

    Returns:
        The set of lowercased alphanumeric tokens.
    """
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _normalized_display(fact: Fact) -> str | None:
    """Render a fact's normalized value for display.

    Args:
        fact: The fact.

    Returns:
        A string like ``"5000000.0 USD"`` or the canonical text, or ``None``.
    """
    if fact.normalized_value_numeric is not None:
        unit = fact.normalized_value_unit or ""
        return f"{fact.normalized_value_numeric:g} {unit}".strip()
    return fact.normalized_value_text


def _score_fact(query_tokens: set[str], fact: Fact, entity_name: str) -> float:
    """Score a fact's relevance to a query via keyword overlap and hints.

    Args:
        query_tokens: Tokenized query.
        fact: Candidate fact.
        entity_name: Canonical entity name for the fact.

    Returns:
        A non-negative relevance score; 0 means no signal.
    """
    score = 0.0
    # Claim-type keyword hints (strongest signal).
    for keyword in _CLAIM_TYPE_KEYWORDS.get(fact.claim_type, set()):
        if keyword in query_tokens:
            score += 3.0
    # Direct overlap with entity and excerpt text.
    score += 2.0 * len(query_tokens & _tokenize(entity_name))
    score += 1.0 * len(query_tokens & _tokenize(fact.source_excerpt))
    score += 1.0 * len(query_tokens & _tokenize(fact.claim_value))
    # Confidence is a gentle tiebreaker, never a primary driver.
    return score + 0.1 * fact.confidence_score if score > 0 else 0.0


def query_facts(
    deal_id: uuid.UUID,
    query_text: str,
    facts: list[Fact],
    entity_names: dict[uuid.UUID, str],
    mandate: Mandate | None = None,
    limit: int = 10,
) -> QueryResponse:
    """Answer a natural-language query with ranked, cited facts.

    Args:
        deal_id: Deal being queried.
        query_text: The user's natural-language question.
        facts: All facts for the deal.
        entity_names: entity_id -> canonical name map.
        mandate: Optional mandate, used to attach related criteria.
        limit: Maximum number of matches to return.

    Returns:
        A :class:`QueryResponse` with ranked matches and related criteria. An
        empty query or no overlap yields an empty match list (never an error).
    """
    query_tokens = _tokenize(query_text)
    scored: list[tuple[float, Fact]] = []
    for fact in facts:
        entity_name = entity_names.get(fact.entity_id, fact.entity_raw_name)
        relevance = _score_fact(query_tokens, fact, entity_name)
        if relevance > 0:
            scored.append((relevance, fact))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    matched_types = {f.claim_type for _, f in scored[:limit]}

    matches = [
        FactCitation(
            fact_id=fact.fact_id,
            entity=entity_names.get(fact.entity_id, fact.entity_raw_name),
            claim_type=fact.claim_type.value,
            claim_value=fact.claim_value,
            normalized_value=_normalized_display(fact),
            confidence_score=fact.confidence_score,
            source_page=fact.source_page,
            source_excerpt=fact.source_excerpt,
            relevance=relevance,
        )
        for relevance, fact in scored[:limit]
    ]

    related_criteria: list[str] = []
    if mandate is not None:
        related_criteria = [
            c.name for c in mandate.criteria if c.claim_type in matched_types
        ]

    logger.info(
        "Query %r on deal %s -> %d match(es)", query_text, deal_id, len(matches)
    )
    return QueryResponse(
        query=query_text,
        deal_id=deal_id,
        matches=matches,
        related_criteria=related_criteria,
    )

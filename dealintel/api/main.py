"""FastAPI application: the query/retrieval surface over the deal intelligence DB.

Endpoints:

- ``GET /deals`` — list ingested deals with fact counts and latest score.
- ``POST /query`` — natural-language query -> matching facts with confidence,
  citation (page + excerpt), and related mandate criteria.
- ``GET /deals/{deal_id}/score`` — full, traceable mandate score breakdown.
- ``GET /health`` — liveness check.

Every query is written to the audit log, so the retrieval surface participates
in the same institutional-memory trail as ingestion and scoring.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dealintel.database import session_scope
from dealintel.mandates import load_mandate_from_yaml
from dealintel.models.audit import AuditEventType
from dealintel.models.scoring import ScoreResult
from dealintel.persistence import (
    list_deals,
    load_entity_names,
    load_facts,
    load_latest_score,
)
from dealintel.pipeline.audit import record_event
from dealintel.retrieval import QueryResponse, query_facts

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deal Intelligence API",
    description=(
        "Query extracted, cited facts and mandate scores for ingested deals."
    ),
    version="0.1.0",
)

#: Default mandate used to annotate query results with related criteria. In
#: production this would be selected per request/fund; for the PoC a single
#: example mandate is loaded from the bundled YAML when present.
_DEFAULT_MANDATE_PATH = "data/sample_mandate.yaml"


class QueryRequest(BaseModel):
    """Request body for ``POST /query``.

    Attributes:
        deal_id: Deal to search.
        query: Natural-language question.
        limit: Maximum number of matches to return.
    """

    deal_id: uuid.UUID
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class DealSummary(BaseModel):
    """Row in the ``GET /deals`` listing.

    Attributes:
        deal_id: Deal identifier (use with the other endpoints).
        deal_name: Name from ingestion source metadata, if any.
        page_count: Pages in the source document.
        fact_count: Number of extracted facts.
        total_score: Latest mandate score, or None if never scored.
    """

    deal_id: uuid.UUID
    deal_name: str
    page_count: int
    fact_count: int
    total_score: float | None


def _load_default_mandate():
    """Load the bundled example mandate if available, else ``None``.

    Returns:
        A :class:`~dealintel.models.mandate.Mandate` or ``None`` when the sample
        file is absent (the API still works, just without related-criteria
        annotations).
    """
    try:
        return load_mandate_from_yaml(_DEFAULT_MANDATE_PATH)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Default mandate unavailable: %s", exc)
        return None


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Returns:
        A small status payload.
    """
    return {"status": "ok"}


@app.get("/deals", response_model=list[DealSummary])
def get_deals() -> list[DealSummary]:
    """List ingested deals so a caller can discover deal_ids to query.

    Returns:
        Deal summaries (id, name, page/fact counts, latest score), most recently
        ingested first.
    """
    with session_scope() as session:
        return [DealSummary(**row) for row in list_deals(session)]


@app.post("/query", response_model=QueryResponse)
def post_query(request: QueryRequest) -> QueryResponse:
    """Run a natural-language fact query and return cited matches.

    Args:
        request: The query request.

    Returns:
        Ranked matching facts with citations and related mandate criteria.

    Raises:
        HTTPException: 404 if the deal has no extracted facts.
    """
    mandate = _load_default_mandate()
    with session_scope() as session:
        facts = load_facts(session, request.deal_id)
        if not facts:
            raise HTTPException(
                status_code=404,
                detail=f"No facts found for deal {request.deal_id}.",
            )
        entity_names = load_entity_names(session, request.deal_id)
        response = query_facts(
            request.deal_id,
            request.query,
            facts,
            entity_names,
            mandate=mandate,
            limit=request.limit,
        )
        record_event(
            session,
            AuditEventType.QUERY_EXECUTED,
            deal_id=request.deal_id,
            input_payload={"query": request.query, "limit": request.limit},
            output_payload={"match_count": len(response.matches)},
        )
    return response


@app.get("/deals/{deal_id}/score", response_model=ScoreResult)
def get_score(deal_id: uuid.UUID) -> ScoreResult:
    """Return the latest full mandate-score breakdown for a deal.

    Args:
        deal_id: The deal whose score to retrieve.

    Returns:
        The most recent immutable :class:`ScoreResult` snapshot.

    Raises:
        HTTPException: 404 if the deal has never been scored.
    """
    with session_scope() as session:
        score = load_latest_score(session, deal_id)
        if score is None:
            raise HTTPException(
                status_code=404,
                detail=f"No score found for deal {deal_id}.",
            )
    return score

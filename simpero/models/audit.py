"""Audit contract: an append-only, override-ready record of every operation.

Every ingestion, classification, extraction, scoring run, and query writes one
``AuditLogEntry``. The chain model carries both ``parent_event_id`` (immediate
predecessor) and ``root_event_id`` (originating event) so a full override chain
is retrievable in a single indexed query, with no recursive traversal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp (see ``document._utcnow``)."""
    return datetime.now(timezone.utc)


class AuditEventType(str, Enum):
    """Every category of operation that writes to the audit log.

    Future analyst-override hooks would add values such as
    ``ANALYST_FACT_EDIT``, ``ANALYST_SCORE_OVERRIDE``, ``MANDATE_VERSION_BUMP``
    without any schema change.
    """

    DOCUMENT_INGESTED = "document_ingested"
    CLASSIFICATION_RUN = "classification_run"
    EXTRACTION_RUN = "extraction_run"
    ENTITY_RESOLVED = "entity_resolved"
    SCORING_RUN = "scoring_run"
    QUERY_EXECUTED = "query_executed"
    PIPELINE_ERROR = "pipeline_error"


class AuditLogEntry(BaseModel):
    """Append-only record of a single system operation.

    Override design: an analyst override writes a *new* entry with, e.g.,
    ``event_type=ANALYST_FACT_EDIT``, ``parent_event_id`` pointing at the
    overridden event, ``root_event_id`` carrying the chain's origin, and the
    delta in ``output_payload``. The originating event sets
    ``root_event_id = event_id`` (self-reference); every descendant copies the
    root forward. Fetching a whole chain is then ``WHERE root_event_id = :root``
    — one query, no recursion.

    Attributes:
        event_id: Stable identifier for this event.
        event_type: Operation category.
        deal_id: Deal this event concerns, if any.
        parent_event_id: Immediate predecessor in an override chain, or ``None``
            for an originating event.
        root_event_id: Originating event of the chain. For an originating event
            this equals ``event_id`` (set by the writer); descendants copy it
            forward so the full chain is one indexed lookup.
        actor: ``"system"`` or an analyst email.
        model_id: Model used, if an LLM call (e.g. ``"claude-haiku-4-5"``,
            ``"claude-sonnet-4-6"``).
        input_payload: Operation input. Untyped JSONB on purpose; downstream
            tooling parses it per ``event_type``.
        output_payload: Operation output (or the override delta).
        latency_ms: Wall-clock duration, if measured.
        token_count_input: Prompt tokens, if an LLM call.
        token_count_output: Completion tokens, if an LLM call.
        estimated_cost_usd: ``tokens * model pricing``, if an LLM call.
        success: Whether the operation succeeded.
        error_message: Failure detail when ``success`` is False.
        occurred_at: UTC timestamp of the event.
    """

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: AuditEventType
    deal_id: uuid.UUID | None = None
    parent_event_id: uuid.UUID | None = None
    root_event_id: uuid.UUID | None = None
    actor: str = "system"
    model_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    token_count_input: int | None = None
    token_count_output: int | None = None
    estimated_cost_usd: float | None = None
    success: bool = True
    error_message: str | None = None
    occurred_at: datetime = Field(default_factory=_utcnow)

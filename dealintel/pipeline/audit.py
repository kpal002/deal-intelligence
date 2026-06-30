"""Audit-trail helper: a thin recorder for append-only audit events.

Centralizes construction and persistence of :class:`AuditLogEntry` so every
pipeline stage logs consistently (and sets the ``root_event_id`` convention
correctly). Keeping this in one place means a future analyst-override feature
hooks in here, not in every stage.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from dealintel.models.audit import AuditEventType, AuditLogEntry
from dealintel.orm.tables import AuditLogORM

logger = logging.getLogger(__name__)


def record_event(
    session: Session,
    event_type: AuditEventType,
    *,
    deal_id: uuid.UUID | None = None,
    actor: str = "system",
    model_id: str | None = None,
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    latency_ms: int | None = None,
    token_count_input: int | None = None,
    token_count_output: int | None = None,
    estimated_cost_usd: float | None = None,
    success: bool = True,
    error_message: str | None = None,
    parent_event_id: uuid.UUID | None = None,
    root_event_id: uuid.UUID | None = None,
) -> AuditLogEntry:
    """Construct, persist, and return one audit log entry.

    The ``root_event_id`` convention is applied here so callers don't have to
    remember it: an originating event (no parent) sets ``root_event_id`` to its
    own ``event_id``; a chained event without an explicit root inherits its
    parent as the root.

    Args:
        session: Active DB session (the entry is added, not committed — the
            caller's ``session_scope`` owns the transaction boundary).
        event_type: Category of operation.
        deal_id: Deal this event concerns, if any.
        actor: ``"system"`` or an analyst identifier.
        model_id: Model used, if an LLM call.
        input_payload: Operation input (stored as JSONB).
        output_payload: Operation output (stored as JSONB).
        latency_ms: Wall-clock duration, if measured.
        token_count_input: Prompt tokens, if an LLM call.
        token_count_output: Completion tokens, if an LLM call.
        estimated_cost_usd: Estimated call cost, if an LLM call.
        success: Whether the operation succeeded.
        error_message: Failure detail when ``success`` is False.
        parent_event_id: Immediate predecessor in an override chain.
        root_event_id: Chain origin; defaulted via the convention above.

    Returns:
        The constructed :class:`AuditLogEntry` (already added to the session).
    """
    entry = AuditLogEntry(
        event_type=event_type,
        deal_id=deal_id,
        actor=actor,
        model_id=model_id,
        input_payload=input_payload or {},
        output_payload=output_payload or {},
        latency_ms=latency_ms,
        token_count_input=token_count_input,
        token_count_output=token_count_output,
        estimated_cost_usd=estimated_cost_usd,
        success=success,
        error_message=error_message,
        parent_event_id=parent_event_id,
        root_event_id=root_event_id,
    )
    # Apply the root_event_id convention.
    if entry.root_event_id is None:
        entry.root_event_id = parent_event_id or entry.event_id

    session.add(_to_orm(entry))
    logger.debug("Recorded audit event %s (%s)", entry.event_id, event_type.value)
    return entry


def _to_orm(entry: AuditLogEntry) -> AuditLogORM:
    """Map an :class:`AuditLogEntry` contract to its ORM row.

    Args:
        entry: The audit entry to persist.

    Returns:
        A populated :class:`AuditLogORM`.
    """
    return AuditLogORM(
        event_id=entry.event_id,
        event_type=entry.event_type.value,
        deal_id=entry.deal_id,
        parent_event_id=entry.parent_event_id,
        root_event_id=entry.root_event_id,
        actor=entry.actor,
        model_id=entry.model_id,
        input_payload=entry.input_payload,
        output_payload=entry.output_payload,
        latency_ms=entry.latency_ms,
        token_count_input=entry.token_count_input,
        token_count_output=entry.token_count_output,
        estimated_cost_usd=entry.estimated_cost_usd,
        success=entry.success,
        error_message=entry.error_message,
        occurred_at=entry.occurred_at,
    )

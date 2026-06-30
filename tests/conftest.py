"""Shared pytest fixtures.

Provides a fully-populated, throwaway SQLite database so the persistence and API
layers can be exercised end to end without a Postgres server. The ORM's portable
column types (``GUID`` / ``JSON_COL`` in ``dealintel.orm.tables``) make the same
schema work on SQLite for tests and JSONB/UUID on Postgres in production.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import dealintel.database as database
import pytest
from dealintel.llm import LLMClient
from dealintel.mandates import load_mandate_from_yaml
from dealintel.mock_llm import mock_handler
from dealintel.models.audit import AuditEventType
from dealintel.persistence import (
    load_entity_names,
    load_facts,
    save_mandate,
    save_score,
)
from dealintel.pipeline.audit import record_event
from dealintel.pipeline.runner import run_pipeline
from dealintel.scoring.engine import score_deal

SAMPLE_PDF = "data/sample_pitch_deck.pdf"
SAMPLE_MANDATE = "data/sample_mandate.yaml"


@pytest.fixture
def seeded_db(tmp_path) -> Iterator[uuid.UUID]:
    """Create a temp SQLite DB, run the full pipeline + scoring, yield the deal_id.

    Resets the cached engine/session factory so the app and the fixture share the
    same temp database. Skips if the sample PDF has not been generated.

    Args:
        tmp_path: pytest-provided temporary directory.

    Yields:
        The processed deal's id.
    """
    if not os.path.exists(SAMPLE_PDF):
        pytest.skip("sample PDF not generated; run scripts/generate_sample_pdf.py")

    db_file = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{db_file}"
    # Force the lazily-cached engine/factory to rebuild against the temp DB.
    database._engine = None
    database._SessionFactory = None

    database.init_db()
    mandate = load_mandate_from_yaml(SAMPLE_MANDATE)
    client = LLMClient(mock_handler=mock_handler)

    with database.session_scope() as session:
        save_mandate(session, mandate)
        result = run_pipeline(session, client, SAMPLE_PDF)
        deal_id = result.deal_id

    with database.session_scope() as session:
        facts = load_facts(session, deal_id)
        entity_names = load_entity_names(session, deal_id)
        score = score_deal(deal_id, facts, mandate, entity_names)
        save_score(session, score)
        record_event(
            session,
            AuditEventType.SCORING_RUN,
            deal_id=deal_id,
            output_payload={"total_score": score.total_score},
        )

    yield deal_id

    database._engine = None
    database._SessionFactory = None
    os.environ.pop("DATABASE_URL", None)

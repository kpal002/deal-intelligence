"""End-to-end demo seed: ingest a deal PDF, extract, score, persist.

Runs the full pipeline against the configured database (Postgres), scores the
result against the bundled mandate, and prints a summary. Uses the offline mock
LLM unless ``ANTHROPIC_API_KEY`` is set, so the plumbing runs with zero external
dependencies beyond Postgres.

The offline mock extractor is tuned to the synthetic sample deck; on a real
document set ``ANTHROPIC_API_KEY`` so the live Claude extraction path runs.

Run:
    python scripts/seed.py                       # bundled sample deck
    python scripts/seed.py data/sample_ppm.pdf "Acme Secondaries Fund III"
"""

from __future__ import annotations

import logging
import os
import sys

from dealintel.config import get_settings
from dealintel.database import init_db, session_scope
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seed")

SAMPLE_PDF = "data/sample_pitch_deck.pdf"
# Mandate path is overridable via DEALINTEL_MANDATE so a fund PPM can be scored
# against the secondaries mandate instead of the operating-company one.
SAMPLE_MANDATE = os.environ.get("DEALINTEL_MANDATE", "data/sample_mandate.yaml")


def main(pdf_path: str = SAMPLE_PDF, deal_name: str = "NorthStar Logistics") -> None:
    """Initialize the schema, run the pipeline, score, and persist everything.

    Args:
        pdf_path: Path to the deal PDF to process.
        deal_name: Human-readable deal name stored in source metadata.
    """
    settings = get_settings()
    use_mock = not settings.anthropic_api_key
    client = LLMClient(mock_handler=mock_handler if use_mock else None)
    logger.info("LLM mode: %s", "MOCK (no API key)" if use_mock else "LIVE Claude")
    if use_mock and pdf_path != SAMPLE_PDF:
        logger.warning(
            "Running the MOCK extractor on a non-sample PDF; the mock is tuned to "
            "the synthetic deck and will extract little. Set ANTHROPIC_API_KEY for "
            "real extraction."
        )

    init_db()
    mandate = load_mandate_from_yaml(SAMPLE_MANDATE)

    with session_scope() as session:
        save_mandate(session, mandate)
        result = run_pipeline(
            session,
            client,
            pdf_path,
            source_metadata={
                "deal_name": deal_name,
                "received_via": "seed_script",
            },
        )
        deal_id = result.deal_id

    # Score in a fresh transaction so facts are committed and reloaded —
    # mirrors how the API scores against persisted data.
    with session_scope() as session:
        facts = load_facts(session, deal_id)
        entity_names = load_entity_names(session, deal_id)
        score = score_deal(deal_id, facts, mandate, entity_names)
        save_score(session, score)
        record_event(
            session,
            AuditEventType.SCORING_RUN,
            deal_id=deal_id,
            output_payload={
                "total_score": score.total_score,
                "knockout_triggered": score.knockout_triggered,
            },
        )

    print("\n=== Pipeline summary ===")
    print(f"deal_id:          {deal_id}")
    print(f"pages:            {result.page_count}")
    print(f"chunks:           {result.chunk_count}")
    print(f"facts extracted:  {result.fact_count}")
    print(f"entities:         {result.entity_count}")
    print(f"est. LLM cost:    ${result.estimated_cost_usd:.4f}")
    print("\n=== Mandate score ===")
    print(f"fund:             {mandate.fund_name} (v{mandate.mandate_version})")
    print(f"total score:      {score.total_score:.1f} / 100")
    print(f"knockout:         {score.knockout_triggered}")
    for cs in score.criterion_scores:
        flag = "KO" if cs.is_knockout else "  "
        print(f"  [{flag}] {cs.criterion_name:<18} met={cs.met!s:<5} {cs.explanation}")
    print(f"\nQuery the API: GET /deals/{deal_id}/score")


if __name__ == "__main__":
    if "DATABASE_URL" not in os.environ:
        logger.warning(
            "DATABASE_URL not set; using default %s", get_settings().database_url
        )
    pdf_arg = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_PDF
    name_arg = sys.argv[2] if len(sys.argv) > 2 else "NorthStar Logistics"
    main(pdf_arg, name_arg)

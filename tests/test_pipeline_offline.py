"""Offline end-to-end test of the processing stages (no database required).

Exercises parse -> chunk -> classify -> extract -> resolve -> score against the
generated sample deck using the deterministic mock LLM. This is the integration
check that the stages compose correctly; persistence is covered separately and
requires Postgres.
"""

from __future__ import annotations

import os
import uuid

import pytest
from simpero.llm import LLMClient
from simpero.mandates import load_mandate_from_yaml
from simpero.mock_llm import mock_handler
from simpero.models.fact import ClaimType
from simpero.pipeline.chunking import chunk_pages
from simpero.pipeline.classification import classify_chunk
from simpero.pipeline.entity_resolution import EntityResolver
from simpero.pipeline.extraction import extract_facts
from simpero.pipeline.parsing import parse_pdf
from simpero.scoring.engine import score_deal

SAMPLE_PDF = "data/sample_pitch_deck.pdf"
SAMPLE_MANDATE = "data/sample_mandate.yaml"

pytestmark = pytest.mark.skipif(
    not os.path.exists(SAMPLE_PDF),
    reason="sample PDF not generated; run scripts/generate_sample_pdf.py",
)


def _run_stages():
    """Run the offline pipeline stages and return (facts, entity_names, deal_id)."""
    client = LLMClient(mock_handler=mock_handler)
    deal_id = uuid.uuid4()
    pages = parse_pdf(SAMPLE_PDF, deal_id)
    chunks = chunk_pages(pages)
    resolver = EntityResolver(deal_id)
    facts = []
    for chunk in chunks:
        section_type, confidence, _ = classify_chunk(client, chunk)
        chunk.section_type = section_type
        extracted, _ = extract_facts(client, chunk, resolver)
        facts.extend(extracted)
    entity_names = {e.entity_id: e.canonical_name for e in resolver.all_entities()}
    return facts, entity_names, deal_id


def test_pipeline_extracts_expected_claim_types():
    """The sample deck yields the core claim types the mandate scores on."""
    facts, _, _ = _run_stages()
    claim_types = {f.claim_type for f in facts}
    assert ClaimType.REVENUE in claim_types
    assert ClaimType.MARKET_SIZE in claim_types
    assert ClaimType.TEAM_BACKGROUND in claim_types
    assert ClaimType.COMPETITIVE_POSITIONING in claim_types


def test_revenue_is_normalized_to_usd():
    """ARR '$4.2M' normalizes to 4_200_000 USD (the comparable form)."""
    facts, _, _ = _run_stages()
    revenue = [f for f in facts if f.claim_type == ClaimType.REVENUE]
    assert any(
        f.normalized_value_unit == "USD" and f.normalized_value_numeric == 4_200_000.0
        for f in revenue
    )


def test_sample_deck_scores_above_floor():
    """The sample deck passes the knockout and scores well against the mandate."""
    facts, entity_names, deal_id = _run_stages()
    mandate = load_mandate_from_yaml(SAMPLE_MANDATE)
    result = score_deal(deal_id, facts, mandate, entity_names)
    assert not result.knockout_triggered
    assert result.total_score >= 80.0

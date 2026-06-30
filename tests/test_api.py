"""Integration tests for the FastAPI surface against a real (SQLite) database.

Exercises the full persistence round-trip: the ``seeded_db`` fixture runs the
pipeline and scoring into a temp database, then these tests hit the live
endpoints via ``TestClient`` and assert on the persisted, re-loaded data.
"""

from __future__ import annotations

import uuid

from dealintel.api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_ok():
    """The health endpoint responds without any database dependency."""
    assert client.get("/health").json() == {"status": "ok"}


def test_query_returns_cited_matches(seeded_db):
    """A natural-language query returns ranked, cited facts + related criteria."""
    response = client.post(
        "/query",
        json={"deal_id": str(seeded_db), "query": "what is the ARR and team size?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matches"], "expected at least one match"
    top = body["matches"][0]
    # Every match carries a citation (page + verbatim excerpt) and confidence.
    assert top["source_page"] >= 1
    assert top["source_excerpt"]
    assert 0.0 <= top["confidence_score"] <= 1.0
    # Revenue-related criteria should surface as related.
    assert any("ARR" in name for name in body["related_criteria"])


def test_query_unknown_deal_404(seeded_db):
    """Querying a deal with no facts returns 404, not an empty 200."""
    response = client.post(
        "/query",
        json={"deal_id": str(uuid.uuid4()), "query": "anything"},
    )
    assert response.status_code == 404


def test_get_score_breakdown(seeded_db):
    """The score endpoint returns the full traceable, persisted snapshot."""
    response = client.get(f"/deals/{seeded_db}/score")
    assert response.status_code == 200
    body = response.json()
    assert body["total_score"] == 100.0
    assert body["knockout_triggered"] is False
    # Every criterion breakdown is present with an explanation.
    names = {cs["criterion_name"] for cs in body["criterion_scores"]}
    assert "Minimum ARR" in names
    arr = next(cs for cs in body["criterion_scores"] if cs["criterion_name"] == "Minimum ARR")
    assert arr["met"] is True
    assert arr["facts_used"], "knockout criterion should cite its driving fact"
    assert arr["facts_used"][0]["source_page"] >= 1


def test_get_score_unknown_deal_404(seeded_db):
    """Requesting a score for an unscored deal returns 404."""
    response = client.get(f"/deals/{uuid.uuid4()}/score")
    assert response.status_code == 404

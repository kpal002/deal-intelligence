# Deal Intelligence Engine

A mandate-first fact extraction and scoring engine for PE / family-office due
diligence. It processes a single structured PDF end to end:

```
ingest → parse (page/table-aware) → classify (Haiku) → extract cited facts (Sonnet)
       → resolve entities → score against a configurable mandate → serve via API
```

Every extracted fact carries a citation (page + verbatim excerpt) and a
normalized value; every score is fully traceable back to the facts that drove
it; every operation is written to an append-only audit trail. The schema is
deliberately built so that adding a second source later surfaces conflicts
without a redesign — see [ARCHITECTURE.md](ARCHITECTURE.md).

> **Cost-tiered by design.** A cheap **Claude Haiku** pass classifies each
> section so the capable **Claude Sonnet** extraction pass only runs where facts
> actually live. Token usage and estimated cost are recorded per call.

## Quick start

```bash
# 1. Install
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Postgres
docker compose up -d

# 3. Generate the sample 10-page PE teaser (NorthStar Logistics)
python scripts/generate_sample_pdf.py

# 4. Run the full pipeline + scoring and populate the database.
#    With no ANTHROPIC_API_KEY set, a deterministic offline mock LLM is used,
#    so this runs with zero external calls. Set the key to use real Claude.
python scripts/seed.py

# 5. Serve the query API
uvicorn dealintel.api.main:app --reload
```

`scripts/seed.py` prints the `deal_id`, the extraction summary, and the full
mandate score breakdown.

## Using the API

```bash
# Natural-language fact query -> cited matches + related mandate criteria
curl -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"deal_id": "<DEAL_ID>", "query": "what is the ARR and team size?"}'

# Full traceable mandate score for a deal
curl localhost:8000/deals/<DEAL_ID>/score
```

Interactive docs: <http://localhost:8000/docs>.

## Configuration

Copy `.env.example` to `.env`. Key settings (resolved in `dealintel/config.py`):

| Variable             | Purpose                                            |
| -------------------- | -------------------------------------------------- |
| `DATABASE_URL`       | Postgres connection (defaults match compose)       |
| `ANTHROPIC_API_KEY`  | If unset, the offline mock LLM is used             |
| `DEALINTEL_*_MODEL`    | Override the Haiku/Sonnet model IDs                |

## Tests

```bash
pytest -q
```

- `tests/test_normalize.py` — the value-normalization layer (currency,
  percentage, count, categorical, never-guess failure mode).
- `tests/test_scoring.py` — the scoring engine (weights, knockouts, every
  operator, unit compatibility, traceability).
- `tests/test_pipeline_offline.py` — parse→classify→extract→score against the
  sample deck via the mock LLM (no database required).
- `tests/test_api.py` — full persistence round-trip and live FastAPI endpoints
  (`/query`, `/deals/{id}/score`, 404s) against an in-memory SQLite database.
  The ORM's portable column types use JSONB/UUID on Postgres and JSON/CHAR on
  SQLite, so the entire stack is testable without a Postgres server.

The suite is type-checked (`mypy dealintel/`) and linted (`ruff check`) clean.

## Project layout

```
dealintel/
  config.py            # env, model IDs, cost estimation
  database.py          # SQLAlchemy engine + session scope
  normalize.py         # value normalization (correctness-critical)
  mandates.py          # YAML -> validated Mandate
  persistence.py       # Pydantic <-> ORM mapping + reads
  retrieval.py         # NL query -> cited facts
  llm.py / mock_llm.py # tiered Claude client + offline mock
  models/              # Pydantic data contracts (Fact, Entity, Mandate, Score, Audit)
  orm/tables.py        # SQLAlchemy tables (JSONB, conflict-key index, audit chain)
  pipeline/            # ingestion, parsing, chunking, classification, extraction, runner
  scoring/engine.py    # deterministic, traceable mandate scoring
  api/main.py          # FastAPI query/score interface
scripts/               # generate_sample_pdf.py, seed.py
data/                  # sample_mandate.yaml, generated sample_pitch_deck.pdf
```

## What is intentionally *not* built (next phase)

Third-party integrations (PitchBook/SEC/Crunchbase), multi-document
cross-referencing and conflict resolution, agent orchestration, auth /
multi-tenancy / deployment. The schema anticipates each — see
[ARCHITECTURE.md](ARCHITECTURE.md) §"Designed seams".

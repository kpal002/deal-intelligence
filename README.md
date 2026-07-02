# Deal Intelligence Engine

Extracts structured, cited facts from a deal PDF and scores them against a
configurable fund mandate. Single-document pipeline; the data model is
normalized for later multi-document reconciliation.

```
ingest → parse (page/table aware) → classify (Haiku) → extract (Sonnet)
       → normalize → resolve entities → persist → score → query (HTTP)
```

Design constraints the implementation holds to:

- Every fact stores its source page and a verbatim excerpt; extraction that
  produces no excerpt is rejected at validation.
- Comparisons (scoring, future conflict detection) run on parsed/normalized
  values, never raw strings.
- The extraction pass is gated by a cheaper classification pass; per-call token
  counts and cost estimates are written to an audit table.
- Scoring is pure and deterministic given a fact set and a mandate.

## Stack

Python 3.11+, Pydantic v2 (data contracts), SQLAlchemy 2.0 + PostgreSQL
(JSONB/UUID), pdfplumber (parsing), Anthropic API (Haiku classify / Sonnet
extract), FastAPI (query surface). Dev: pytest, mypy, ruff.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d              # PostgreSQL on :5432 (see docker-compose.yml)
cp .env.example .env              # set ANTHROPIC_API_KEY, or leave blank for mock
```

Without `ANTHROPIC_API_KEY`, the pipeline runs against a deterministic offline
mock (`dealintel/mock_llm.py`) so the plumbing is exercisable without network or
cost. The mock is pattern-matched to the bundled sample deck; use a real key for
extraction on any other document.

## Running the pipeline

Bundled synthetic deck (generates a 10-page teaser, then processes it):

```bash
python scripts/generate_sample_pdf.py
python scripts/seed.py
```

`seed.py` initializes the schema, runs the pipeline, scores against
`data/sample_mandate.yaml`, persists everything, and prints a summary
(deal_id, page/chunk/fact counts, estimated cost, per-criterion score).

### Running against an external PDF

`seed.py` takes a PDF path and an optional deal name; the mandate is selectable
via `DEALINTEL_MANDATE`. Requires `ANTHROPIC_API_KEY` for meaningful extraction.

```bash
# operating-company deal against the default mandate
python scripts/seed.py /path/to/deal.pdf "Acme Corp"

# a PE fund PPM against the secondaries mandate
DEALINTEL_MANDATE=data/secondaries_mandate.yaml \
  python scripts/seed.py /path/to/fund_ppm.pdf "Fund III"
```

Any Postgres URL works via `DATABASE_URL`. For a throwaway run with no Postgres,
point it at SQLite (the ORM column types fall back automatically):

```bash
DATABASE_URL="sqlite+pysqlite:///tmp/deal.db" \
  python scripts/seed.py /path/to/deal.pdf "Acme Corp"
```

Note the deal_id from the output; the API reads by deal_id.

## Query API

```bash
uvicorn dealintel.api.main:app --reload   # docs at /docs
```

```bash
# NL query → ranked facts with page/excerpt citations + related criteria
curl -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"deal_id": "<DEAL_ID>", "query": "net IRR and fund size"}'

# persisted mandate-score breakdown for a deal
curl localhost:8000/deals/<DEAL_ID>/score
```

## Configuration

Resolved in `dealintel/config.py`; `.env` is loaded if present.

| Variable                 | Default                                   | Purpose                         |
| ------------------------ | ----------------------------------------- | ------------------------------- |
| `DATABASE_URL`           | local Postgres (`dealintel`)              | SQLAlchemy connection           |
| `ANTHROPIC_API_KEY`      | unset → mock LLM                          | enables live extraction         |
| `DEALINTEL_MANDATE`      | `data/sample_mandate.yaml`                | mandate used by `seed.py`       |
| `DEALINTEL_HAIKU_MODEL`  | `claude-haiku-4-5-20251001`               | classification model            |
| `DEALINTEL_SONNET_MODEL` | `claude-sonnet-4-6`                       | extraction model                |

## Tests

```bash
pytest -q        # 61 tests; mypy dealintel/ and ruff check both clean
```

- `test_normalize.py` — value normalization: currency, percentage, count,
  investment multiple, vintage year, categorical text, and the unparseable path.
- `test_scoring.py` — scoring engine: weight normalization, knockouts, each
  operator, unit compatibility, sub-scope filtering, contribution traceability.
- `test_pipeline_offline.py` — parse→classify→extract→score on the sample deck
  via the mock (no database).
- `test_api.py` — persistence round-trip and HTTP endpoints against SQLite
  (ORM types resolve to JSONB/UUID on Postgres, JSON/CHAR on SQLite).

## Layout

```
dealintel/
  config.py            env, model ids, cost estimation
  database.py          engine + transactional session scope
  normalize.py         raw value → (numeric, unit) | text | status
  mandates.py          YAML → validated Mandate
  persistence.py       Pydantic ⇄ ORM mapping and reads
  retrieval.py         keyword/claim-type ranking over facts
  llm.py, mock_llm.py  tiered Anthropic client; offline mock
  models/              Pydantic contracts (document, fact, mandate, scoring, audit)
  orm/tables.py        SQLAlchemy tables; conflict-key index; audit chain
  pipeline/            ingestion, parsing, chunking, classification,
                       extraction, entity_resolution, audit, runner
  scoring/engine.py    deterministic weighted scoring with traceability
  api/main.py          FastAPI query/score endpoints
scripts/               generate_sample_pdf.py, seed.py
data/                  sample_mandate.yaml, secondaries_mandate.yaml
```

## Scope

Implemented: single-document ingestion, classification, extraction, entity
canonicalization, normalization, mandate scoring, query/score API, audit log.

Not implemented (the data model reserves for these; see ARCHITECTURE.md):
multi-document conflict resolution, third-party source integration
(PitchBook/SEC/Crunchbase), analyst-override workflow, agent orchestration,
auth/multi-tenancy/deployment.

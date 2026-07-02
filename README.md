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

## Example output

`python scripts/seed.py` on the bundled synthetic deck (mock LLM, no key or
Postgres required — reproducible by anyone):

```
=== Pipeline summary ===
pages:            10
chunks:           11
facts extracted:  40
entities:         5
est. LLM cost:    $0.0797

=== Mandate score ===
fund:             Meridian Growth Partners (v1)
total score:      100.0 / 100
knockout:         False
  [KO] Minimum ARR        met=True  4200000.0 USD >= 1000000.0 USD -> met.
  [  ] ARR ceiling        met=True  1100000.0 USD <= 10000000.0 USD -> met.
  [  ] Team of 5+         met=True  14.0 count >= 5.0 count -> met.
  [  ] Large market       met=True  12000000000.0 USD >= 1000000000.0 USD -> met.
  [  ] B2B SaaS sector    met=True  'saas' found in a matching fact.
  [  ] US-based           met=True  'united states' found in a matching fact.
```

A query returns ranked facts with page + verbatim excerpt citations:

```
query: "what is the ARR and team size"
  p2 | revenue         | $4.2M -> 4200000.0 USD
       "The business reached an ARR of $4.2M in the most recent quarter."
  p7 | team_background | 14 -> 14 count
       "NorthStar Logistics is run by a team of 14 across engineering, ..."
```

Real document (live Claude on a 30-page PE secondaries PPM, secondaries
mandate): 281 facts, 46 entities, ~$0.81. Note the bundled `sample_ppm.pdf` is a
public template with figures redacted to `XX`; the pipeline stores those as
`UNPARSEABLE` rather than guessing, so numeric criteria needing real values are
correctly unmet. A document with real figures scores against the same mandate.

## Query API

A pre-populated demo database (`data/demo.db`, the synthetic deck) is committed,
so the API can be served against real data with no key, Postgres, or pipeline
run:

```bash
DATABASE_URL="sqlite+pysqlite:///data/demo.db" \
  uvicorn dealintel.api.main:app --reload   # docs at /docs
```

```bash
# list ingested deals (discover a deal_id)
curl localhost:8000/deals

# NL query → ranked facts with page/excerpt citations + related criteria
curl -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"deal_id": "<DEAL_ID>", "query": "ARR and team size"}'

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

## Where to look first

- `dealintel/models/fact.py` — the central `Fact` contract and `ClaimType`
  ontology; the normalized-value fields and the `OTHER` lossless-capture rule.
- `dealintel/normalize.py` — raw value → (numeric, unit) | text | status; the
  routing dispatcher and per-kind parsers (currency, percent, multiple, year).
- `dealintel/scoring/engine.py` — `score_deal`: weight normalization, sub-scope
  filtering, operator evaluation, knockouts, traceable contributions.
- `dealintel/pipeline/runner.py` — how the stages are sequenced and audited.
- `dealintel/orm/tables.py` — persistence schema; the conflict-key index and
  audit chain.
- `ARCHITECTURE.md` — data model, per-stage contracts, algorithms, extension
  points, and known limitations.

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
data/                  sample_mandate.yaml, secondaries_mandate.yaml, demo.db
```

## Scope

Implemented: single-document ingestion, classification, extraction, entity
canonicalization, normalization, mandate scoring, query/score API, audit log.

Not implemented (the data model reserves for these; see ARCHITECTURE.md):
multi-document conflict resolution, third-party source integration
(PitchBook/SEC/Crunchbase), analyst-override workflow, agent orchestration,
auth/multi-tenancy/deployment.

# Architecture

This document maps each layer of the engine back to Simpero's stated needs, in
plain language. It is written to be read by a semi-technical reviewer.

Simpero's four core problems, and where each is addressed:

1. **Score every deal against a fund's mandate in real time** → the Mandate +
   Scoring layers.
2. **Extract founder claims and triangulate them against sources, with
   citations** → the Extraction + Fact layers (single-source today; the schema
   is built for triangulation).
3. **A full audit trail that compounds into institutional memory** → the Audit
   layer.
4. **Manage LLM cost at scale via tiered processing** → the Classification +
   Extraction tiering.

---

## Layer-by-layer

### 1. Raw storage — *"never discard the original"*

The original PDF bytes are stored verbatim in `raw_documents.raw_bytes`,
alongside a SHA-256 content hash, page count, ingestion timestamp, and a
freeform `source_metadata` bag (sender, deal name, fund). This is the
chain-of-custody anchor: every downstream fact traces back to these exact bytes.

→ *Simpero requirement: institutional memory / auditability begins at ingestion.*

### 2. Document processing — *page-aware, layout-aware, cost-tiered*

- **Parsing** (`pipeline/parsing.py`) uses pdfplumber and keeps **page numbers**
  and **tables** as first-class data, separate from prose. Page numbers ride on
  every object so any fact can cite an exact page.
- **Chunking** (`pipeline/chunking.py`) is **layout-aware**: a financial table
  is kept together with the sentence that introduces it, rather than split at an
  arbitrary token boundary that would orphan the number from its meaning.
- **Classification** (`pipeline/classification.py`) runs the **cheap Haiku tier**
  to label each section (`financial_data`, `narrative_claim`,
  `legal_boilerplate`, `table`, `other`). The pipeline then **skips boilerplate**
  before the expensive extraction pass.

→ *Simpero requirement #4 (cost control): a cheap model decides where to spend
the expensive one. This is visible in the audit log, which records the model,
token counts, and an estimated cost for every call.*

### 3. Structured fact extraction — *cited, normalized, lossless*

The **Sonnet tier** (`pipeline/extraction.py`) extracts atomic claims into the
`Fact` contract: `entity`, `claim_type`, `claim_value`, `source_page`,
`source_excerpt`, `confidence_score`, `extraction_method`, `document_version`,
`extracted_at`, and more. Two guarantees matter:

- **Every fact is cited.** `source_excerpt` is verbatim and non-empty (enforced
  by validation) — a citation without evidence is rejected.
- **Nothing is silently dropped.** A claim that fits no `claim_type` is mapped to
  `OTHER` with a required free-text `claim_subtype_raw`, so messy real-world
  decks don't lose facts (enforced by a model validator). This is what keeps the
  "we extract every claim" promise honest on the first non-ideal document.

**Entity resolution** (`pipeline/entity_resolution.py`) normalizes name variants
("Acme Corp", "ACME Corporation", "Acme Inc.") into a canonical entities table —
present even with one document, because it is the exact seam multi-document
linking plugs into.

### 4. Value normalization — *the precondition for conflict detection*

`normalize.py` parses raw values into a **comparable** form:
`normalized_value_numeric` + `normalized_value_unit` for quantities,
`normalized_value_text` for categoricals, with a `normalization_status` of
`NORMALIZED` / `UNPARSEABLE` / `NOT_APPLICABLE`.

This is deliberately its own, exhaustively unit-tested module because it is
load-bearing: `$5M`, `$5,000,000`, and `5 million USD` are three strings for one
value. **Normalization is the precondition for the conflict layer** — it is not
a solved problem we hide, it is a designed seam (see §"Designed seams"). When a
value looks quantitative but can't be parsed, the engine records `UNPARSEABLE`
and stores no number — it **never guesses**.

### 5. Mandate scoring — *configurable, weighted, fully traceable*

- **Mandates are data, not code** (`data/sample_mandate.yaml`, validated by
  `mandates.py`). A criterion references a `claim_type`, an `operator`
  (`gte/lte/eq/contains/in/exists`), a `threshold`, a `weight`, and an optional
  `is_knockout`. Adding a criterion is a YAML edit; the engine never changes.
- **The engine** (`scoring/engine.py`) is pure and deterministic. It normalizes
  weights to sum to 1.0, compares against **normalized** values (never raw
  strings), supports **knockout** criteria (a failed knockout forces the total
  to 0), and emits a 0–100 score.
- **Traceability is the point.** Every sub-score lists the exact facts that drove
  it, each with page + verbatim excerpt, so an analyst walks
  criterion → fact → page → excerpt with no extra queries.
- **Mandates are versioned independently.** A score records `mandate_version`,
  so revising the rubric never corrupts historical scores.

→ *Simpero requirement #1.* The bundled mandate ("B2B SaaS, $1–10M ARR,
US-based, team of 5+, large market") demonstrates the mechanism is generic, not
hardcoded to one fund.

### 6. Retrieval & citation API — *FastAPI* (`api/main.py`)

- `POST /query` — a natural-language question returns ranked facts, each with
  confidence, page + excerpt citation, and the related mandate criteria.
- `GET /deals/{deal_id}/score` — the full, traceable score breakdown.

The retrieval matcher is intentionally transparent (keyword + claim-type hints)
for the PoC; the docstring marks where an embedding/LLM reranker would slot in.

### 7. Audit trail — *append-only, override-ready* (`models/audit.py`)

Every ingestion, classification, extraction, scoring run, and query writes one
`AuditLogEntry` with full input/output payloads, timestamps, model, token
counts, and estimated cost. Two design choices make it production-shaped:

- **`parent_event_id` + `root_event_id`.** A future analyst override writes a new
  entry chained to the event it overrides. Carrying the chain's `root_event_id`
  means a full override history is retrievable in **one indexed query** — no
  recursive traversal.
- **Untyped JSONB payloads** so new event types (e.g. `ANALYST_SCORE_OVERRIDE`)
  need no schema change.

→ *Simpero requirement #3: the trail compounds into institutional memory, and
the analyst-override feature hooks in without a redesign.*

---

## Designed seams (built for, not yet built)

These are visible in the schema today so the next phase is additive, not a
rewrite:

- **Multi-source conflict detection.** Facts carry
  `(deal_id, entity_id, claim_type, normalized_value_numeric,
  normalized_value_unit)` and a supporting composite index
  (`ix_facts_conflict_key`). A second document asserting the same
  `(entity, claim_type)` with a different **normalized** value is surfaced by a
  single `GROUP BY … HAVING COUNT(DISTINCT …) > 1` query. We do **not** build
  resolution logic — but the schema makes exactly where it hooks in obvious, and
  the normalization layer is the precondition that makes the query correct.
- **Document re-processing.** `document_version` on every fact lets a corrected
  document be re-extracted without orphaning prior facts.
- **Entity linking across documents.** The canonical entities table and its
  `aliases` list already accumulate variants.
- **Analyst overrides.** `ExtractionMethod.MANUAL_OVERRIDE` and the audit chain
  reserve the path.

## Immutable scoring snapshots (a deliberate decision)

`CriterionScore`/`FactContribution` embed `source_page` and `source_excerpt`
**at scoring time**. If a fact's excerpt is later corrected, historical scores
intentionally retain the original wording. This is **by design**: a score is an
immutable record of what was known when the decision was made — exactly what an
audit demands. It is stated auditability, not denormalization debt. Live,
corrected data is always reachable via `fact_id`.

## Out of scope (explicitly, for the next phase)

- Third-party API integrations (PitchBook, SEC, Crunchbase)
- Multi-document cross-referencing / conflict **resolution** logic
- LangGraph / agent orchestration
- Authentication, multi-tenancy, deployment configuration

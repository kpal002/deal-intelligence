# Architecture

Single-document extraction and scoring pipeline. This document describes the
data model, per-stage contracts, the normalization and scoring algorithms, and
the extension points the schema reserves.

## Requirements mapping

| Requirement                                   | Where it is implemented                                                                 |
| --------------------------------------------- | -------------------------------------------------------------------------------------- |
| Score deals against a fund mandate            | `scoring/engine.py` (`score_deal`); mandates as data in `data/*.yaml`, `models/mandate.py` |
| Extract founder claims with citations         | `pipeline/extraction.py`; `Fact.source_page`/`source_excerpt` (excerpt validated non-empty) |
| Triangulate against sources (multi-doc)       | Single-source implemented; schema reserves it — normalized values + `ix_facts_conflict_key`, `document_version` (see Extension points) |
| Audit trail / institutional memory            | `audit_log` table; one `AuditLogEntry` per ingest/classify/extract/score/query; `parent_event_id`/`root_event_id` chain |
| Manage LLM cost via tiered processing         | Haiku classification gates Sonnet extraction (`pipeline/runner.py`, `pipeline/classification.py`); per-call tokens + cost in the audit log |
| Analyst override (future)                     | `ExtractionMethod.MANUAL_OVERRIDE` + audit chain reserve the path (see Extension points) |

## Data flow

```
PDF ─ingest→ RawDocument(+bytes) ─parse→ [ParsedPage] ─chunk→ [DocumentChunk]
   ─classify(Haiku)→ chunks w/ section_type
   ─extract(Sonnet)→ [Fact] (normalized) + [CanonicalEntity]
   ─persist→ Postgres ─score(mandate)→ ScoreResult
```

Every stage consumes and returns Pydantic models (`dealintel/models/`). The
runner (`pipeline/runner.py`) sequences them within one transaction and writes
an `AuditLogEntry` per operation. Persistence (`persistence.py`) is the only
layer that maps contracts to ORM rows; the ORM (`orm/tables.py`) is never
imported by pipeline or scoring code.

## Data model

Tables (`orm/tables.py`); portable column types resolve to JSONB/UUID on
Postgres and JSON/CHAR on SQLite.

- `raw_documents` — `deal_id` (PK), `file_hash` (SHA-256, indexed for dedup),
  `source_metadata` (JSONB), `raw_bytes` (original file retained).
- `chunks` — `chunk_id` (PK), `deal_id` (FK), `chunk_index`, `source_pages`
  (JSONB), `section_type`, `content`, `classification_confidence`.
- `canonical_entities` — `entity_id` (PK), `deal_id` (FK), `canonical_name`,
  `entity_type`, `aliases` (JSONB).
- `facts` — see below; FKs to `raw_documents`, `chunks`, `canonical_entities`.
- `mandates` — versioned; `criteria` stored as JSONB.
- `score_results` — immutable snapshots; `criterion_scores` (JSONB) with
  citations embedded.
- `audit_log` — append-only; `parent_event_id` + `root_event_id`, JSONB
  payloads, token/cost columns.

### Fact

The central record. Fields relevant to comparison and provenance:

```
deal_id, chunk_id, entity_id, entity_raw_name
claim_type            enum (operating-company + fund metrics; see below)
claim_subtype_raw     free text; required when claim_type == OTHER
claim_value           verbatim string, never mutated
normalized_value_numeric / _unit / _text
normalization_status  NORMALIZED | UNPARSEABLE | NOT_APPLICABLE
source_page, source_excerpt   citation (excerpt validated non-empty)
source_char_start / _end      excerpt offsets in the source page text
span_verification             VERIFIED_EXACT | VERIFIED_FUZZY | UNVERIFIED
source_bbox                   line-level page rectangles for highlighting (or null)
confidence_score, extraction_method, document_version, extracted_at
```

`claim_type` covers `market_size, revenue, team_background,
competitive_positioning, funding_history, customer_metrics`, the fund-document
set `net_irr, tvpi, dpi, fund_size, vintage_year, gp_commitment`, and `other`.
A claim that matches no type is stored as `other` with a free-text
`claim_subtype_raw` rather than dropped; this is enforced by a model validator.

Index `ix_facts_conflict_key` on
`(deal_id, claim_type, normalized_value_numeric, normalized_value_unit)`
supports the conflict query described under Extension points.

## Pipeline stages

**Ingestion** (`pipeline/ingestion.py`) reads bytes, computes the SHA-256, and
opens the PDF to record page count. Returns `RawDocument` plus the raw bytes;
persistence stores the bytes unmodified.

**Parsing** (`pipeline/parsing.py`) extracts text and tables per page with
pdfplumber. Text and tables are kept separate. A failure on one page is captured
in `extraction_warnings` and parsing continues, so one malformed page does not
abort a document.

**Chunking** (`pipeline/chunking.py`) emits one chunk per prose segment plus one
chunk per table, prefixing each table with the page's leading prose so the table
retains introducing context. Prose over ~4k chars is split on paragraph
boundaries. `chunk_index` preserves reading order; `source_pages` records the
originating page(s).

**Classification** (`pipeline/classification.py`, Haiku) labels each chunk with
a `SectionType`. Malformed model output falls back to `OTHER` at confidence 0
rather than raising. The runner skips `legal_boilerplate` before extraction,
which is the mechanism by which the cheap pass bounds spend on the expensive one.

**Extraction** (`pipeline/extraction.py`, Sonnet) returns a JSON array of
claims. Each record is parsed defensively: a malformed element is skipped with a
warning, not fatal. For each valid record the stage normalizes the value,
resolves the entity, and constructs a validated `Fact`. Non-JSON output yields
zero facts for that chunk.

**Span verification** (`verification.py`) locates each fact's `source_excerpt`
in the parsed text of its page and records the `[start, end)` char offsets plus
a status. Exact substring match (`str.find`) gives `VERIFIED_EXACT`; otherwise
`rapidfuzz.fuzz.partial_ratio_alignment` returns the matched span and a score —
above the threshold it is `VERIFIED_FUZZY`, else `UNVERIFIED` with null offsets.
The excerpt is not treated as source-grounded unless a span is found. Offsets
index into `parse_pdf(...)[source_page-1].raw_text`, reproducible from the
retained PDF bytes. rapidfuzz (MIT) is the only dependency for this step.

**Bounding boxes** (`geometry.py`) map the located excerpt to on-page rectangles
for highlighting. The parser retains per-word geometry (`x0/x1/top/bottom` from
pdfplumber's `extract_words`); `locate_bboxes` finds the contiguous run of page
words best matching the excerpt's tokens and unions their boxes per line,
yielding one `{x0, top, x1, bottom}` rectangle per line (PDF points, top-left
origin) on `Fact.source_bbox`. This reuses pdfplumber (no new dependency, no
license change). Pixel-perfect quads (e.g. PyMuPDF, which is AGPL) are a
deliberate separate choice, not adopted here.

**Entity resolution** (`pipeline/entity_resolution.py`) canonicalizes names
within a deal by a match key (lowercased, punctuation and common corporate
suffixes stripped). First occurrence creates a `CanonicalEntity`; later variants
append to `aliases`. The interface is a callable `(raw_name, claim_type) →
entity_id`; a fuzzy/embedding resolver would implement the same signature.

## Normalization

`normalize.py` is pure and separately unit-tested. `normalize_claim_value(raw,
claim_type)` returns `(numeric, unit)`, `text`, or a status, routed by claim
type and value content:

1. empty → `NOT_APPLICABLE`
2. categorical type (competitive positioning) → canonical text
3. multiple type (tvpi/dpi) or an `Nx` token → `normalize_multiple` → unit `multiple`
4. `vintage_year` → 4-digit year → unit `year`
5. `%` present or percent type (net_irr) → `normalize_percentage` → unit `percent`
6. currency type (revenue, market_size, funding_history, fund_size) or a
   currency symbol/code → `normalize_currency` → unit `USD`/`EUR`/…
7. any remaining numeric value → `normalize_count`
8. otherwise → canonical text

Rules of note:

- Scale words/suffixes (`k`, `m/mn/million`, `b/bn/billion`) are applied, so
  `$5M`, `$5,000,000`, and `5 million USD` all yield `5_000_000.0 USD`.
- Percentages: an explicit `%` is taken as-is; a bare value in `[0,1]` is treated
  as a proportion and scaled (`0.15 → 15.0`).
- If a value is expected to be quantitative but no number parses, the result is
  `UNPARSEABLE` with a null numeric — the pipeline stores the fact but records
  the failure rather than inferring a value.

Normalization is a precondition for conflict detection (Extension points):
comparison across documents keys on the normalized numeric/unit, not the raw
string.

## Scoring

`scoring/engine.py`, `score_deal(deal_id, facts, mandate, entity_names)`. Pure;
no I/O.

- Weights are normalized to sum to 1.0, so mandate authors use relative weights.
- Facts are matched to a criterion by `claim_type` and an optional
  `claim_subtype` sub-scope filter (substring, case-insensitive). A criterion
  that requires a sub-scope rejects facts with no subtype — it fails closed
  rather than assuming scope. This prevents, e.g., a single deal's IRR from
  satisfying a fund-level net-IRR threshold.
- Numeric operators (`gte/lte/eq`) run against `normalized_value_numeric` and
  require unit compatibility with the criterion's `threshold_unit`. `gte` uses
  the maximum candidate as supporting evidence, `lte` the minimum, `eq` the
  nearest. Text operators (`contains/in`) run against normalized text.
  `exists` tests for any matching fact.
- Each criterion yields binary `met` and a `raw_score` (0/1); the weighted
  contribution is `raw_score × normalized_weight`. `total_score` is the weighted
  sum × 100. The raw score is the seam for graded scoring.
- A failed knockout criterion forces `total_score` to 0 and records which one.
- Each `CriterionScore` lists the `FactContribution`s that drove it (fact_id,
  entity, value, page, excerpt, confidence, direction).

`raw_score` being binary is intentional for this version; the return in
`_evaluate_numeric` is where proportional credit would attach.

## Transactions and error handling

`database.session_scope()` commits on clean exit and rolls back on any
exception. The runner wraps the post-ingestion stages; on failure it writes a
`PIPELINE_ERROR` audit entry and re-raises. LLM calls
(`llm.py`) retry with exponential backoff and raise `LLMError` after the retry
budget; the classification and extraction parsers degrade to safe defaults on
malformed output rather than propagating.

## Immutable score snapshots

`CriterionScore`/`FactContribution` copy `source_page` and `source_excerpt` at
scoring time. A later correction to a fact's excerpt does not alter historical
scores; a score reflects the evidence as of when it was produced. Current data
remains reachable via `fact_id`. This is a deliberate denormalization for
auditability, not an accident.

## Extension points

The schema is shaped so the following are additive:

- **Conflict detection.** Two documents asserting the same
  `(entity_id, claim_type)` with different normalized values are found by
  `GROUP BY entity_id, claim_type HAVING COUNT(DISTINCT normalized_value_numeric,
  normalized_value_unit) > 1`, supported by `ix_facts_conflict_key`. Resolution
  logic is not implemented.
- **Re-processing.** `document_version` on `Fact` allows re-extraction without
  orphaning prior facts.
- **Cross-document entity linking.** `canonical_entities.aliases` already
  accumulates variants; the resolver interface is stable.
- **Analyst overrides.** `ExtractionMethod.MANUAL_OVERRIDE` plus the
  `parent_event_id`/`root_event_id` audit chain reserve the path; a full override
  chain is retrievable in one indexed query on `root_event_id`.

## Known limitations

- Extraction quality on real fund documents depends on prompt/model behavior;
  fund-level vs deal-level metric tagging (`claim_subtype_raw`) is model-driven
  and not guaranteed. Scoring fails closed when scope is ambiguous.
- Entity resolution is exact-key within a deal; no fuzzy matching.
- Retrieval ranking is keyword/claim-type based, not semantic.
- Cost figures are estimates from static per-token rates in `config.py`.

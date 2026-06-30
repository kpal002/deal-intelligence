"""Structured fact extraction (Sonnet tier) — the capable, expensive pass.

Given a classified chunk, the Sonnet model returns a list of atomic claims. This
stage parses that output into validated :class:`Fact` objects, normalizes each
value via :mod:`simpero.normalize`, and enforces the lossless-capture contract
(a claim that fits no :class:`ClaimType` becomes ``OTHER`` with a free-text
``claim_subtype_raw`` rather than being dropped).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Protocol

from pydantic import ValidationError

from simpero.llm import LLMClient, LLMResult
from simpero.models.document import DocumentChunk
from simpero.models.fact import ClaimType, ExtractionMethod, Fact
from simpero.normalize import normalize_claim_value

logger = logging.getLogger(__name__)

_VALID_CLAIM_TYPES = {t.value for t in ClaimType}

_SYSTEM_PROMPT = """\
You extract atomic factual claims from a private-equity deal document section.

Return ONLY a JSON array. Each element:
{
  "entity": "the company/person/market the claim is about, verbatim",
  "claim_type": one of [market_size, revenue, team_background,
                competitive_positioning, funding_history, customer_metrics,
                net_irr, tvpi, dpi, fund_size, vintage_year, gp_commitment, other],
  // Fund-document hints: net_irr = an IRR (e.g. "18.5%"); tvpi/dpi = investment
  // multiples (e.g. "1.8x"); fund_size = target/committed size (e.g. "$750M");
  // vintage_year = a 4-digit year; gp_commitment = the GP's commitment (percent
  // or currency). Use these for PE fund PPMs.
  //
  // SCOPE IS CRITICAL for net_irr, tvpi, and dpi: a PPM reports BOTH the
  // fund/aggregate track-record metric AND individual portfolio-deal metrics.
  // In claim_subtype_raw, prefix with the scope:
  //   "fund_net_irr" / "fund_tvpi"  -> the fund's overall/aggregate/net figure
  //   "deal_net_irr" / "deal_tvpi"  -> a single investment's figure
  // A single hot deal's IRR must NOT be labeled as the fund's net IRR.
  "claim_subtype_raw": "short free-text label; REQUIRED when claim_type is
                        'other', optional otherwise (e.g. 'ARR', 'GMV')",
  "claim_value": "the value exactly as written, e.g. '$5M', '15% MoM', '12 people'",
  "source_excerpt": "verbatim supporting sentence (<=500 chars)",
  "confidence": <0.0-1.0>
}

Rules:
- Extract EVERY factual claim. If a claim fits no listed claim_type, use
  "other" and put a descriptive label in claim_subtype_raw. Never drop a claim.
- source_excerpt must be copied verbatim from the section — it is the citation.
- Do not invent claims not supported by the text.
- If the section contains no factual claims, return [].
"""


def extract_facts(
    client: LLMClient,
    chunk: DocumentChunk,
    entity_resolver: EntityResolverProtocol,
) -> tuple[list[Fact], LLMResult]:
    """Extract validated, normalized facts from one chunk via the Sonnet tier.

    Args:
        client: The tiered LLM client.
        chunk: The (already classified) chunk to extract from.
        entity_resolver: Callable resolving a raw entity name to a canonical
            ``entity_id`` (see :mod:`simpero.pipeline.entity_resolution`).

    Returns:
        A ``(facts, llm_result)`` tuple. Malformed individual records are
        skipped with a warning rather than aborting the chunk; the raw
        :class:`LLMResult` is returned for audit logging.
    """
    result = client.extract(_SYSTEM_PROMPT, chunk.content)
    facts = _parse_facts(result.text, chunk, entity_resolver)
    return facts, result


def _parse_facts(
    raw_text: str,
    chunk: DocumentChunk,
    entity_resolver: EntityResolverProtocol,
) -> list[Fact]:
    """Parse the extractor's JSON array into validated Fact objects.

    Args:
        raw_text: Raw model output, expected to be a JSON array.
        chunk: Source chunk (supplies deal_id, chunk_id, and the citation page).
        entity_resolver: Raw-name -> canonical entity_id resolver.

    Returns:
        Validated, normalized facts. Records that fail validation are skipped.
    """
    try:
        records = json.loads(_extract_json_array(raw_text))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse extractor output as JSON (%s); 0 facts", exc)
        return []
    if not isinstance(records, list):
        logger.warning("Extractor output was not a JSON array; 0 facts")
        return []

    source_page = chunk.source_pages[0] if chunk.source_pages else 1
    facts: list[Fact] = []
    for record in records:
        fact = _build_fact(record, chunk, source_page, entity_resolver)
        if fact is not None:
            facts.append(fact)
    logger.info("Extracted %d fact(s) from chunk %s", len(facts), chunk.chunk_index)
    return facts


def _build_fact(
    record: object,
    chunk: DocumentChunk,
    source_page: int,
    entity_resolver: EntityResolverProtocol,
) -> Fact | None:
    """Build one validated, normalized Fact from a raw extractor record.

    Args:
        record: A single JSON object from the extractor.
        chunk: Source chunk.
        source_page: Citation page for this chunk.
        entity_resolver: Raw-name -> canonical entity_id resolver.

    Returns:
        A validated :class:`Fact`, or ``None`` if the record is malformed.
    """
    if not isinstance(record, dict):
        return None
    try:
        claim_type = _coerce_claim_type(record.get("claim_type"))
        raw_value = str(record.get("claim_value", "")).strip()
        entity_raw = str(record.get("entity", "")).strip() or "unknown"
        subtype = record.get("claim_subtype_raw")
        # Lossless-capture safety net: if the model bucketed OTHER without a
        # label, synthesize one from the value so the Fact validator passes and
        # the claim is preserved instead of discarded.
        if claim_type is ClaimType.OTHER and not (subtype and str(subtype).strip()):
            subtype = f"unmapped: {raw_value[:60]}" if raw_value else "unmapped claim"

        normalized = normalize_claim_value(raw_value, claim_type)
        entity_id = entity_resolver(entity_raw, claim_type)

        return Fact(
            deal_id=chunk.deal_id,
            chunk_id=chunk.chunk_id,
            entity_id=entity_id,
            entity_raw_name=entity_raw,
            claim_type=claim_type,
            claim_subtype_raw=str(subtype).strip() if subtype else None,
            claim_value=raw_value,
            normalized_value_numeric=normalized.numeric,
            normalized_value_unit=normalized.unit,
            normalized_value_text=normalized.text,
            normalization_status=normalized.status,
            source_page=source_page,
            source_excerpt=str(record.get("source_excerpt", "")).strip()[:500],
            extraction_method=ExtractionMethod.SONNET_EXTRACTION,
            confidence_score=_coerce_confidence(record.get("confidence")),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.warning("Skipping malformed extracted record: %s", exc)
        return None


def _coerce_claim_type(value: object) -> ClaimType:
    """Map a raw claim_type string to the enum, defaulting to OTHER.

    Args:
        value: Raw claim_type from the model.

    Returns:
        A valid :class:`ClaimType`; unknown values map to ``OTHER`` so the
        claim is preserved via the lossless-capture path.
    """
    label = str(value).strip().lower()
    return ClaimType(label) if label in _VALID_CLAIM_TYPES else ClaimType.OTHER


def _coerce_confidence(value: object) -> float:
    """Coerce a raw confidence to a clamped float in ``[0, 1]``.

    Args:
        value: Raw confidence from the model.

    Returns:
        The clamped confidence, defaulting to ``0.5`` when absent/unparseable.
    """
    try:
        return min(max(float(value), 0.0), 1.0)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.5


def _extract_json_array(text: str) -> str:
    """Extract the first JSON array substring from model text.

    Args:
        text: Raw model output.

    Returns:
        The substring from the first ``[`` to the last ``]`` inclusive, or the
        original text if no brackets are found.
    """
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


class EntityResolverProtocol(Protocol):
    """Structural type for an entity resolver callable.

    Any callable ``(raw_name: str, claim_type: ClaimType) -> uuid.UUID`` satisfies
    this; :class:`simpero.pipeline.entity_resolution.EntityResolver` is the
    concrete implementation. Declared as a ``Protocol`` so structural typing —
    not inheritance — is what the type checker verifies.
    """

    def __call__(self, raw_name: str, claim_type: ClaimType) -> uuid.UUID:  # noqa: D102
        ...

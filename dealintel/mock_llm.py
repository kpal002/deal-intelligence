"""Deterministic, content-driven mock LLM handler for offline demo and tests.

Lets the entire pipeline run with no API key. The handler inspects the system
prompt to tell the classification pass from the extraction pass, then produces
plausible JSON *driven by the chunk's actual text* (regex cue matching) rather
than hardcoded output — so it behaves sensibly on the bundled sample deck and
degrades gracefully on other documents.

This is a stand-in for real Claude calls. The real :class:`dealintel.llm.LLMClient`
path is used whenever an ``ANTHROPIC_API_KEY`` is configured.
"""

from __future__ import annotations

import json
import re

# Cue table: (regex, claim_type, subtype). The first capturing group is the
# value used as claim_value; the matched line becomes the citation excerpt.
_EXTRACTION_RULES: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"ARR of (\$[\d.,]+\s*[MBK]?)", re.I), "revenue", "ARR"),
    (re.compile(r"revenue of (\$[\d.,]+\s*[MBK]?)", re.I), "revenue", None),
    (re.compile(r"(?:TAM|total addressable market)[^\$]*(\$[\d.,]+\s*[MBK]?)", re.I), "market_size", "TAM"),
    (re.compile(r"raised (\$[\d.,]+\s*[MBK]?)\s+(?:in\s+)?(series [a-z]|seed)", re.I), "funding_history", None),
    (re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)\s+(?:paying\s+)?customers", re.I), "customer_metrics", "customer_count"),
    (re.compile(r"growing (\d+%)\s*(?:MoM|month)", re.I), "customer_metrics", "growth_rate"),
    (re.compile(r"team of (\d+)", re.I), "team_background", "team_size"),
]

_COMPANY_RE = re.compile(r"[A-Z][A-Za-z]+\s+(?:Logistics|Technologies|Labs|Systems|Health)")


def _detect_company(text: str) -> str:
    """Best-effort extraction of the subject company name from a chunk.

    Args:
        text: The chunk text.

    Returns:
        A detected company name, or ``"the company"`` as a safe fallback.
    """
    match = _COMPANY_RE.search(text)
    return match.group(0) if match else "the company"


def _line_for(text: str, span_start: int) -> str:
    """Return the source line containing a matched span (for the citation).

    Args:
        text: Full chunk text.
        span_start: Character offset of the match.

    Returns:
        The enclosing line, trimmed to 500 chars.
    """
    line_start = text.rfind("\n", 0, span_start) + 1
    line_end = text.find("\n", span_start)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:500]


def _mock_classify(content: str) -> str:
    """Produce a mock classification JSON for a chunk.

    Args:
        content: The chunk content (the user prompt).

    Returns:
        A JSON string ``{"section_type": ..., "confidence": ...}``.
    """
    lowered = content.lower()
    if any(w in lowered for w in ("confidential", "disclaimer", "forward-looking")):
        section = "legal_boilerplate"
    elif "[table]" in lowered or "$" in content and "|" in content:
        section = "financial_data"
    elif "[table]" in lowered:
        section = "table"
    else:
        section = "narrative_claim"
    return json.dumps({"section_type": section, "confidence": 0.9})


def _mock_extract(content: str) -> str:
    """Produce a mock extraction JSON array driven by the chunk's text.

    Args:
        content: The chunk content (the user prompt).

    Returns:
        A JSON array string of extracted-claim objects.
    """
    company = _detect_company(content)
    records: list[dict] = []
    for pattern, claim_type, subtype in _EXTRACTION_RULES:
        for match in pattern.finditer(content):
            value = match.group(1)
            entity = "global logistics market" if claim_type == "market_size" else company
            record = {
                "entity": entity,
                "claim_type": claim_type,
                "claim_value": value,
                "source_excerpt": _line_for(content, match.start()),
                "confidence": 0.88,
            }
            if subtype:
                record["claim_subtype_raw"] = subtype
            records.append(record)

    # Categorical / OTHER claims with explicit cues, to exercise text matching.
    if re.search(r"b2b saas", content, re.I):
        records.append(
            {
                "entity": company,
                "claim_type": "competitive_positioning",
                "claim_value": "B2B SaaS platform",
                "source_excerpt": _line_for(content, content.lower().find("b2b saas")),
                "confidence": 0.9,
            }
        )
    if re.search(r"united states|u\.s\.|san francisco|headquartered", content, re.I):
        idx = max(content.lower().find("united states"), content.lower().find("headquartered"))
        records.append(
            {
                "entity": company,
                "claim_type": "other",
                "claim_subtype_raw": "geography",
                "claim_value": "Headquartered in the United States",
                "source_excerpt": _line_for(content, idx if idx >= 0 else 0),
                "confidence": 0.85,
            }
        )
    return json.dumps(records)


def mock_handler(model_id: str, system: str, prompt: str) -> str:
    """Route a mock call to classification or extraction based on the system prompt.

    Args:
        model_id: The model that would have been called (unused; kept for the
            :data:`dealintel.llm.MockHandler` signature).
        system: The system prompt — used to distinguish the two passes.
        prompt: The chunk content.

    Returns:
        The mock model output as a JSON string.
    """
    if "classifier" in system.lower():
        return _mock_classify(prompt)
    return _mock_extract(prompt)

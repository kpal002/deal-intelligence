"""Section classification pass (Haiku tier) — the cheap triage step.

Each chunk is labeled with a :class:`SectionType` so the expensive extraction
pass can skip low-value sections (e.g. ``legal_boilerplate``) and focus the
Sonnet budget where facts actually live. This is the cost-tiering strategy made
concrete: a cheap model decides where to spend the expensive one.
"""

from __future__ import annotations

import json
import logging

from dealintel.llm import LLMClient, LLMResult
from dealintel.models.document import DocumentChunk, SectionType

logger = logging.getLogger(__name__)

_VALID_TYPES = {t.value for t in SectionType}

_SYSTEM_PROMPT = """\
You are a document triage classifier for private-equity deal documents.
Classify the SECTION the user provides into exactly one category:

- financial_data: revenue, ARR, margins, projections, financial tables
- narrative_claim: prose claims about market, traction, team, or strategy
- legal_boilerplate: disclaimers, confidentiality notices, forward-looking
  statement legalese
- table: a data table whose type is not specifically financial
- other: anything that fits none of the above

Respond with ONLY a JSON object, no prose:
{"section_type": "<category>", "confidence": <0.0-1.0>}
"""


def classify_chunk(client: LLMClient, chunk: DocumentChunk) -> tuple[SectionType, float, LLMResult]:
    """Classify a single chunk's section type via the Haiku tier.

    Args:
        client: The tiered LLM client.
        chunk: The chunk to classify.

    Returns:
        A ``(section_type, confidence, llm_result)`` tuple. On malformed or
        unexpected model output the section type falls back to
        :attr:`SectionType.OTHER` with confidence ``0.0`` (never raises — a
        classification miss must not abort the run), and the raw
        :class:`LLMResult` is returned for audit logging.
    """
    result = client.classify(_SYSTEM_PROMPT, chunk.content)
    section_type, confidence = _parse_classification(result.text)
    return section_type, confidence, result


def _parse_classification(raw_text: str) -> tuple[SectionType, float]:
    """Parse the classifier's JSON output defensively.

    Args:
        raw_text: Raw model text, expected to be a JSON object.

    Returns:
        A ``(SectionType, confidence)`` pair, defaulting to
        ``(SectionType.OTHER, 0.0)`` on any parsing problem.
    """
    try:
        payload = json.loads(_extract_json(raw_text))
        label = str(payload.get("section_type", "")).strip().lower()
        if label not in _VALID_TYPES:
            logger.warning("Classifier returned unknown label %r; using OTHER", label)
            return SectionType.OTHER, 0.0
        confidence = float(payload.get("confidence", 0.0))
        confidence = min(max(confidence, 0.0), 1.0)
        return SectionType(label), confidence
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Could not parse classifier output (%s); using OTHER", exc)
        return SectionType.OTHER, 0.0


def _extract_json(text: str) -> str:
    """Extract the first JSON object substring from model text.

    Tolerates models that wrap JSON in prose or code fences.

    Args:
        text: Raw model output.

    Returns:
        The substring from the first ``{`` to the last ``}`` inclusive, or the
        original text if no braces are found.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text

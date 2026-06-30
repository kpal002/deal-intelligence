"""Centralized configuration: environment, database URL, and model constants.

All tunables live here so the rest of the codebase never reads ``os.environ``
directly. Model IDs are pinned as constants to make the cost-tiering strategy
(cheap Haiku classification vs. capable Sonnet extraction) explicit and
auditable — the audit log records which model produced each result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    # Load a local .env (if present) so ANTHROPIC_API_KEY / DATABASE_URL can be
    # set in a file instead of exported each session. python-dotenv is optional:
    # if it is not installed, real environment variables still work.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is an optional convenience
    pass

# --- Model tiering --------------------------------------------------------
# The cost-tiered strategy in one place: a cheap model for the high-volume
# classification pass, a capable model for the lower-volume extraction pass.

#: Cheap, fast model for the per-section classification pass.
HAIKU_MODEL_ID: str = os.environ.get("SIMPERO_HAIKU_MODEL", "claude-haiku-4-5-20251001")

#: Capable model for structured fact extraction.
SONNET_MODEL_ID: str = os.environ.get("SIMPERO_SONNET_MODEL", "claude-sonnet-4-6")

#: Approximate per-million-token pricing (USD) used only for cost *estimates*
#: written to the audit log. Not billing-grade; update as pricing changes.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model_id: (input_per_mtok, output_per_mtok)
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}

#: Max tokens requested from each model tier.
HAIKU_MAX_TOKENS: int = 1024
SONNET_MAX_TOKENS: int = 4096


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of a single model call for the audit log.

    Args:
        model_id: The model that was invoked.
        input_tokens: Prompt tokens consumed.
        output_tokens: Completion tokens produced.

    Returns:
        Estimated cost in USD, or ``0.0`` if the model's pricing is unknown
        (estimates must never raise — a missing price should not break a
        pipeline run).
    """
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model_id)
    if pricing is None:
        return 0.0
    input_rate, output_rate = pricing
    return (input_tokens / 1_000_000) * input_rate + (
        output_tokens / 1_000_000
    ) * output_rate


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved from the environment.

    Attributes:
        database_url: SQLAlchemy connection URL. Defaults to a local Postgres
            instance; override via ``DATABASE_URL``.
        anthropic_api_key: Key for the Anthropic API. Empty string when unset —
            callers that need it should fail with a clear message, and the
            pipeline supports a ``--mock`` path for running without a key.
        haiku_model_id: Classification-tier model ID.
        sonnet_model_id: Extraction-tier model ID.
        request_timeout_seconds: Per-LLM-call timeout.
        max_retries: Retry budget for transient LLM failures.
    """

    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://simpero:simpero@localhost:5432/simpero",
        )
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    haiku_model_id: str = HAIKU_MODEL_ID
    sonnet_model_id: str = SONNET_MODEL_ID
    request_timeout_seconds: float = 60.0
    max_retries: int = 3


def get_settings() -> Settings:
    """Return runtime settings resolved from the current environment.

    Returns:
        A fresh :class:`Settings` snapshot. Cheap to call; not cached so tests
        can mutate the environment between calls.
    """
    return Settings()

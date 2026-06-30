"""Anthropic Claude client wrapper with cost tiering, retries, and a mock path.

Two responsibilities:

1. **Cost tiering, made explicit.** :meth:`LLMClient.classify` uses the cheap
   Haiku tier; :meth:`LLMClient.extract` uses the capable Sonnet tier. Every
   call returns an :class:`LLMResult` carrying token usage and an estimated cost
   so the pipeline can write it to the audit log.
2. **Graceful degradation.** Transient API errors are retried with backoff. A
   ``mock_handler`` lets the entire pipeline run deterministically without a
   live API key — used by the demo seed script and tests.

The wrapper deliberately does not parse model output into Pydantic here; that
belongs to the extraction/classification stages, which own the prompt/response
contract.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from dealintel.config import Settings, estimate_cost_usd, get_settings

logger = logging.getLogger(__name__)

#: A mock handler receives ``(model_id, system, prompt)`` and returns the raw
#: text the model would have produced. Lets the pipeline run offline.
MockHandler = Callable[[str, str, str], str]


@dataclass
class LLMResult:
    """Outcome of a single model call, with everything the audit log needs.

    Attributes:
        text: The model's text output.
        model_id: Which model produced it.
        input_tokens: Prompt tokens consumed.
        output_tokens: Completion tokens produced.
        estimated_cost_usd: Estimated USD cost of the call.
        latency_ms: Wall-clock duration of the call.
    """

    text: str
    model_id: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: int


class LLMError(RuntimeError):
    """Raised when an LLM call fails after exhausting the retry budget."""


class LLMClient:
    """Tiered wrapper over the Anthropic SDK with retry and mock support.

    Args:
        settings: Resolved runtime settings. Defaults to
            :func:`dealintel.config.get_settings`.
        mock_handler: If provided, all calls are served by this callable instead
            of the live API — no key required. The pipeline passes this in for
            offline/demo runs.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        mock_handler: MockHandler | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._mock_handler = mock_handler
        self._client = None  # lazily constructed; not needed in mock mode

    @property
    def is_mock(self) -> bool:
        """Whether this client serves responses from a mock handler."""
        return self._mock_handler is not None

    def _get_client(self):  # type: ignore[no-untyped-def]
        """Lazily construct the Anthropic SDK client.

        Returns:
            An ``anthropic.Anthropic`` instance.

        Raises:
            LLMError: If the SDK is missing or no API key is configured.
        """
        if self._client is None:
            if not self._settings.anthropic_api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set and no mock_handler was "
                    "provided. Set the key or run in mock mode."
                )
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover - environment guard
                raise LLMError(
                    "The 'anthropic' package is not installed."
                ) from exc
            self._client = Anthropic(
                api_key=self._settings.anthropic_api_key,
                timeout=self._settings.request_timeout_seconds,
            )
        return self._client

    def _call(
        self, model_id: str, system: str, prompt: str, max_tokens: int
    ) -> LLMResult:
        """Make one model call (mock or live) with retry and usage accounting.

        Args:
            model_id: Model to invoke.
            system: System prompt.
            prompt: User message content.
            max_tokens: Output token cap.

        Returns:
            An :class:`LLMResult`.

        Raises:
            LLMError: On unrecoverable failure after retries.
        """
        start = time.monotonic()

        if self._mock_handler is not None:
            text = self._mock_handler(model_id, system, prompt)
            latency_ms = int((time.monotonic() - start) * 1000)
            # Rough token estimate for mock accounting (~4 chars/token).
            in_tok = (len(system) + len(prompt)) // 4
            out_tok = len(text) // 4
            return LLMResult(
                text=text,
                model_id=model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=estimate_cost_usd(model_id, in_tok, out_tok),
                latency_ms=latency_ms,
            )

        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._settings.max_retries + 1):
            try:
                response = client.messages.create(
                    model=model_id,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                in_tok = response.usage.input_tokens
                out_tok = response.usage.output_tokens
                latency_ms = int((time.monotonic() - start) * 1000)
                return LLMResult(
                    text=text,
                    model_id=model_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    estimated_cost_usd=estimate_cost_usd(model_id, in_tok, out_tok),
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # broad: SDK raises a family of errors
                last_exc = exc
                backoff = 2 ** (attempt - 1)
                logger.warning(
                    "LLM call to %s failed (attempt %d/%d): %s. Retrying in %ds.",
                    model_id,
                    attempt,
                    self._settings.max_retries,
                    exc,
                    backoff,
                )
                time.sleep(backoff)

        raise LLMError(
            f"LLM call to {model_id} failed after "
            f"{self._settings.max_retries} attempts"
        ) from last_exc

    def classify(self, system: str, prompt: str) -> LLMResult:
        """Run a cheap classification call on the Haiku tier.

        Args:
            system: System prompt describing the classification task.
            prompt: The content to classify.

        Returns:
            An :class:`LLMResult` from the Haiku-tier model.
        """
        from dealintel.config import HAIKU_MAX_TOKENS

        return self._call(
            self._settings.haiku_model_id, system, prompt, HAIKU_MAX_TOKENS
        )

    def extract(self, system: str, prompt: str) -> LLMResult:
        """Run a capable extraction call on the Sonnet tier.

        Args:
            system: System prompt describing the extraction task and schema.
            prompt: The content to extract facts from.

        Returns:
            An :class:`LLMResult` from the Sonnet-tier model.
        """
        from dealintel.config import SONNET_MAX_TOKENS

        return self._call(
            self._settings.sonnet_model_id, system, prompt, SONNET_MAX_TOKENS
        )

"""Provider-agnostic LLM client.

The rest of the agent depends only on `LLMClient.complete(system, user) -> str`.
The concrete provider is chosen at build time from whichever API key is present
(Anthropic preferred), or forced via GUARDED_LLM_PROVIDER. This keeps the agent
decoupled from any single vendor.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

import config


class LLMError(RuntimeError):
    pass


# Transient HTTP statuses worth retrying: 429 rate limit, 5xx, and OpenRouter's
# 402 in-flight credit-reservation hiccup ("retry after in-flight requests settle").
_RETRYABLE_STATUS = {402, 429, 500, 502, 503, 504}

# Transient network failures carry no HTTP status but are just as retryable. Match by
# class name (across the MRO) so we don't have to import httpx / the SDK error types.
_RETRYABLE_EXC_NAMES = {
    "APIConnectionError", "APITimeoutError",            # openai / anthropic SDKs
    "ConnectError", "ConnectTimeout", "ReadTimeout",    # httpx
    "WriteTimeout", "PoolTimeout", "TimeoutException", "RemoteProtocolError",
}


def _status_code(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(exc, "code", None)


def _is_transient(exc: Exception) -> bool:
    if _status_code(exc) in _RETRYABLE_STATUS:
        return True
    return bool({cls.__name__ for cls in type(exc).__mro__} & _RETRYABLE_EXC_NAMES)


def with_retry(fn):
    """Retry a provider call with exponential backoff on transient errors.

    Retries both retryable HTTP statuses (429/5xx/402) and connection/timeout errors,
    which carry no status code yet are the most common transient failure.
    """
    last = None
    for attempt in range(config.LLM_API_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if not _is_transient(e) or attempt == config.LLM_API_RETRIES:
                raise
            last = e
            time.sleep(config.LLM_BACKOFF_BASE ** attempt)
    raise last  # pragma: no cover


def with_model_fallback(primary: str, fallback: str | None, call):
    """Run `call(model)` on the primary model; on failure, try the fallback once.

    `call` already wraps its request in `with_retry`, so this only engages after the
    primary has exhausted its transient retries (or failed hard, e.g. a retired model).
    """
    try:
        return call(primary)
    except Exception:  # noqa: BLE001
        if not fallback or fallback == primary:
            raise
        return call(fallback)


class LLMClient(ABC):
    name: str = "abstract"

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's text response to a system + user prompt."""


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, model: str | None = None, fallback_model: str | None = None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("anthropic package not installed (pip install anthropic)") from e
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic()
        self.model = model or config.ANTHROPIC_MODEL
        self.fallback_model = fallback_model or config.ANTHROPIC_FALLBACK_MODEL

    def _create(self, model: str, system: str, user: str):
        return with_retry(lambda: self._client.messages.create(
            model=model,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            system=system,
            messages=[{"role": "user", "content": user}],
        ))

    def complete(self, system: str, user: str) -> str:
        # Fall back to the secondary model if the primary fails (e.g. a retired model id).
        msg = with_model_fallback(
            self.model, self.fallback_model,
            lambda model: self._create(model, system, user))
        return "".join(block.text for block in msg.content if block.type == "text")


class _OpenAICompatibleClient(LLMClient):
    """Shared base for OpenAI and any OpenAI-compatible endpoint (e.g. OpenRouter)."""

    def __init__(self, model: str, *, base_url: str | None = None, api_key: str | None = None,
                 extra_body: dict | None = None):
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise LLMError("openai package not installed (pip install openai)") from e
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self._client = openai.OpenAI(**kwargs)
        self.model = model
        self.extra_body = extra_body or {}  # provider/model-specific params (e.g. reasoning_effort)

    def complete(self, system: str, user: str) -> str:
        resp = with_retry(lambda: self._client.chat.completions.create(
            model=self.model,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body=self.extra_body or None,
        ))
        return resp.choices[0].message.content or ""


class OpenAIClient(_OpenAICompatibleClient):
    name = "openai"

    def __init__(self, model: str | None = None):
        if not os.getenv("OPENAI_API_KEY"):
            raise LLMError("OPENAI_API_KEY is not set")
        super().__init__(model or config.OPENAI_MODEL)


class OpenRouterClient(_OpenAICompatibleClient):
    name = "openrouter"

    def __init__(self, model: str | None = None):
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        super().__init__(model or config.OPENROUTER_MODEL,
                         base_url=config.OPENROUTER_BASE_URL, api_key=key)


class CerebrasClient(_OpenAICompatibleClient):
    name = "cerebras"

    def __init__(self, model: str | None = None):
        key = os.getenv("CEREBRAS_API_KEY")
        if not key:
            raise LLMError("CEREBRAS_API_KEY is not set")
        super().__init__(model or config.CEREBRAS_MODEL,
                         base_url=config.CEREBRAS_BASE_URL, api_key=key)


class GroqClient(_OpenAICompatibleClient):
    name = "groq"

    def __init__(self, model: str | None = None):
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set")
        model = model or config.GROQ_MODEL
        # gpt-oss models reason; keep it light so output stays SQL-focused and token-cheap.
        extra = {"reasoning_effort": "low"} if "gpt-oss" in model else None
        super().__init__(model, base_url=config.GROQ_BASE_URL, api_key=key, extra_body=extra)


_PROVIDERS = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "openrouter": OpenRouterClient,
    "cerebras": CerebrasClient,
    "groq": GroqClient,
}


def get_client() -> LLMClient:
    """Pick a provider: forced via GUARDED_LLM_PROVIDER, else whichever key is present."""
    forced = (config.LLM_PROVIDER or "").lower().strip()
    if forced:
        if forced not in _PROVIDERS:
            raise LLMError(f"unknown GUARDED_LLM_PROVIDER={forced!r}")
        return _PROVIDERS[forced]()

    if os.getenv("GROQ_API_KEY"):
        return GroqClient()
    if os.getenv("CEREBRAS_API_KEY"):
        return CerebrasClient()
    if os.getenv("OPENROUTER_API_KEY"):
        return OpenRouterClient()
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIClient()
    raise LLMError(
        "No LLM provider available: set GROQ_API_KEY (or CEREBRAS_API_KEY / "
        "OPENROUTER_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY) — copy .env.example to .env."
    )

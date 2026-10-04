"""LLM calls through an ordered fallback chain of OpenAI-compatible providers (LLM_MODELS).

The first model that answers wins. One that fails is benched for a while, so later requests go
straight to the next model instead of waiting on a broken provider again.
"""

import logging
import time

from openai import AsyncOpenAI

from app.core.config import settings
from app.llm.system_prompt import SYSTEM_PROMPT

log = logging.getLogger("uvicorn.error")

# provider: (OpenAI-compatible base URL, API key, provider-specific request fields)
_PROVIDERS = {
    # Hybrid models like Nemotron think by default on OpenRouter: 14-17 s per reply in testing, and the
    # hidden thinking ate max_tokens and cut the flag off mid-string. Off: ~1 s, reply intact.
    "openrouter": ("https://openrouter.ai/api/v1", settings.OPENROUTER_API_KEY, {"reasoning": {"enabled": False}}),
    # Gemini's thinking switch differs per model (3.5-flash-lite rejects "none"); its defaults tested fast.
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", settings.GEMINI_API_KEY, {}),
    "groq": ("https://api.groq.com/openai/v1", settings.GROQ_API_KEY, {}),
    "nvidia": ("https://integrate.api.nvidia.com/v1", settings.NVIDIA_API_KEY, {}),
}

# One client per configured provider: reuses connections. No SDK retries; the chain is the retry.
_clients = {
    name: AsyncOpenAI(base_url=url, api_key=key, timeout=20, max_retries=0)
    for name, (url, key, _) in _PROVIDERS.items()
    if key
}

CHAIN: list[tuple[str, str]] = []  # (provider, model), in the order LLM_MODELS lists them
for entry in filter(None, map(str.strip, settings.LLM_MODELS.split(","))):
    provider, _, model = entry.partition("/")
    if provider not in _PROVIDERS:
        raise ValueError(f"LLM_MODELS: unknown provider {provider!r} in {entry!r}")
    if provider in _clients:  # models of providers without a key are skipped
        CHAIN.append((provider, model))
if not CHAIN:
    raise ValueError("LLM_MODELS has no model whose provider API key is set")

_benched_until: dict[tuple[str, str], float] = {}  # ponytail: per-process; each worker learns failures alone


async def ask(provider: str, model: str, history: list[dict[str, str]], prompt: str) -> str:
    """One model, one attempt."""
    response = await _clients[provider].chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": prompt}],
        # Hidden thinking counts against this cap: at 500, Gemini 3.5 Flash stopped mid-dump after 16 visible
        # tokens. Only generated tokens are billed, so the headroom is free unless a model really uses it.
        max_tokens=2048,
        temperature=0.3,  # benchmarked: the intended bypass lands reliably at <= 0.3, about half the time above
        extra_body=_PROVIDERS[provider][2],
    )
    return response.choices[0].message.content or "The vault is silent."


async def reply(history: list[dict[str, str]], prompt: str) -> tuple[str, str]:
    """The Keeper's reply and the provider/model that wrote it. Raises if every model fails."""
    now = time.monotonic()
    # Healthy models first, in configured order; benched ones only as a last resort.
    for provider, model in sorted(CHAIN, key=lambda m: _benched_until.get(m, 0) > now):
        try:
            return await ask(provider, model, history, prompt), f"{provider}/{model}"
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            # Bad key, no credits or a retired model won't heal in a minute; rate limits and outages might.
            pause = 600 if status in (401, 402, 403, 404) else 60
            _benched_until[(provider, model)] = time.monotonic() + pause
            log.warning("%s/%s failed (%s); benched for %d s", provider, model, status or type(exc).__name__, pause)
    raise RuntimeError("every model in LLM_MODELS failed")

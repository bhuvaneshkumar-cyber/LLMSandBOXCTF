"""
llm/client.py — Provider-agnostic LLM client for the Vault Keeper challenge.

Public API
----------
LLMProvider          Abstract base class every provider must implement.
GeminiProvider       Google Gemini via ``google-genai`` (default, server key).
OpenRouterProvider   OpenRouter via the OpenAI-compatible SDK; accepts a *per-request*
                     participant key so it is never stored server-side (BYOK).
get_llm_provider()   Factory: returns OpenRouterProvider when a participant supplies a
                     BYOK key, otherwise returns settings.LLM_PROVIDER's default
                     (OpenRouterProvider with the server key, or GeminiProvider).

Error handling contract
-----------------------
All provider errors (timeout, quota, content filter, bad key, …) are caught
inside ``generate()`` and re-raised as ``fastapi.HTTPException`` with a safe,
generic message.  Raw provider error text, stack traces, and API keys are
*never* forwarded to the client.

Conversation history format
---------------------------
``history`` is a plain list of dicts, provider-independent::

    [
        {"role": "user",      "content": "Hello"},
        {"role": "assistant", "content": "Greetings, seeker."},
    ]

Each concrete provider translates this into its own SDK format internally.

Adding a new provider
---------------------
1. Subclass ``LLMProvider`` and implement ``generate()``.
2. Register it in ``_PROVIDER_REGISTRY`` (optional, for name-based lookup).
3. Update ``get_llm_provider()`` if it should be selectable via participant key type.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any

from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeouts & retry knobs (not exposed via config for now — change here if needed)
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT_SECS: float = 30.0   # Hard wall-clock timeout per LLM call.
_SAFE_ERROR_MESSAGE: str = (
    "The vault is temporarily unavailable. Please try again in a moment."
)

# ---------------------------------------------------------------------------
# History type alias
# ---------------------------------------------------------------------------

HistoryEntry = dict[str, str]   # {"role": "user"|"assistant", "content": "..."}
History = list[HistoryEntry]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMProvider(abc.ABC):
    """
    Abstract interface that every LLM provider adapter must implement.

    Concrete subclasses must override ``generate()`` and translate the
    provider-agnostic ``history`` list into whatever format their SDK expects.
    """

    @abc.abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: History,
    ) -> str:
        """
        Send a single user turn to the LLM and return the model's reply.

        Args:
            system_prompt: Full system-level instruction (the Vault Keeper prompt).
            user_prompt:   Sanitised user message for this turn.
            history:       Previous turns in ``[{"role": ..., "content": ...}]`` format.
                           The current ``user_prompt`` is NOT yet appended here;
                           providers append it internally before sending.

        Returns:
            The model's reply as a plain string.

        Raises:
            HTTPException 503: On provider API error, timeout, or content filtering.
            HTTPException 400: On invalid/rejected API key (BYOK path only).
        """
        ...

    # ------------------------------------------------------------------
    # Helpers available to all subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_provider_error(exc: Exception, *, context: str = "") -> None:
        """
        Log the real error internally and raise a sanitised HTTPException.

        Args:
            exc:     The original exception from the provider SDK.
            context: Short label for the log line (e.g. provider name).
        """
        logger.error(
            "LLM provider error [%s]: %s: %s",
            context,
            type(exc).__name__,
            exc,
            exc_info=False,   # Don't leak stack-trace details into structured logs.
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SAFE_ERROR_MESSAGE,
        )


# ---------------------------------------------------------------------------
# GeminiProvider
# ---------------------------------------------------------------------------


class GeminiProvider(LLMProvider):
    """
    Google Gemini provider using the new ``google-genai`` SDK.

    Uses the server-configured API key (``settings.GEMINI_API_KEY``).
    Model is selected from ``settings.GEMINI_MODEL`` (default: gemini-3.5-flash).

    History format translation
        The new SDK expects a history of ``genai.types.Content`` objects.
        This adapter maps generic roles to "user" and "model".

    SDK docs: https://github.com/google/generative-ai-python
    """

    def __init__(self) -> None:
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not configured. "
                "Set it in .env before starting the server."
            )

        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-genai is not installed. "
                "Run: pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)

    @staticmethod
    def _to_gemini_history(history: History) -> list[Any]:
        """
        Convert generic history → Gemini SDK Content format.
        """
        from google.genai import types  # noqa: PLC0415
        result = []
        for entry in history:
            role = "model" if entry["role"] == "assistant" else entry["role"]
            result.append(types.Content(role=role, parts=[types.Part.from_text(text=entry["content"])]))
        return result

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: History,
    ) -> str:
        """
        Call Gemini's async chat API with multi-turn history support.
        """
        try:
            gemini_history = self._to_gemini_history(history)
            
            chat = self._client.aio.chats.create(
                model=settings.GEMINI_MODEL,
                config={"system_instruction": system_prompt},
                history=gemini_history,
            )

            response = await asyncio.wait_for(
                chat.send_message(user_prompt),
                timeout=_REQUEST_TIMEOUT_SECS,
            )
            return response.text or ""

        except asyncio.TimeoutError as exc:
            self._raise_provider_error(exc, context="Gemini/timeout")
        except Exception as exc:  # noqa: BLE001
            self._raise_provider_error(exc, context="Gemini")



# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class OpenRouterProvider(LLMProvider):
    """
    OpenRouter provider — an OpenAI-compatible gateway to hundreds of models.

    OpenRouter exposes an OpenAI-compatible API at https://openrouter.ai/api/v1,
    so we reuse the ``openai`` SDK with a custom ``base_url``.

    Supports two key modes:

    1. **Server key** (``participant_key=None``):
       Uses ``settings.OPENROUTER_API_KEY``.  Good for fallback / organiser testing.

    2. **Participant BYOK** (``participant_key="sk-or-..."``):
       The participant supplies their own OpenRouter key per-request.
       The key is used for this call only — never stored, logged, or persisted.

    Model is read from ``settings.OPENROUTER_MODEL``.  The free default
    (``meta-llama/llama-3.3-70b-instruct:free``) works without billing.

    SDK docs: https://openrouter.ai/docs
    """

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, participant_key: str | None = None) -> None:
        """
        Args:
            participant_key: Participant-supplied OpenRouter key (BYOK).
                             If None, falls back to ``settings.OPENROUTER_API_KEY``.

        Raises:
            HTTPException 400: If neither key is available.
        """
        try:
            import openai  # noqa: PLC0415
            self._openai = openai
        except ImportError as exc:
            raise ImportError(
                "openai SDK is not installed. Run: pip install openai"
            ) from exc

        resolved_key = participant_key or settings.OPENROUTER_API_KEY
        if not resolved_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No OpenRouter API key available. "
                    "Provide your key in the provider_api_key field, "
                    "or ask the organiser to configure a server key."
                ),
            )

        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=self._BASE_URL,
        )
        self._model = settings.OPENROUTER_MODEL
        self._is_byok = bool(participant_key)

    @staticmethod
    def _to_messages(history: History, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        """
        Build the full OpenAI-format message list:
        system → history turns → current user turn.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": e["role"], "content": e["content"]} for e in history)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: History,
    ) -> str:
        """
        Call OpenRouter's chat completions endpoint with multi-turn history.

        Retries once on a bare timeout — free-tier OpenRouter models are
        occasionally slow to cold-start, and a single retry clears that
        without masking a genuinely unreachable provider (which still fails
        after the second attempt).
        """
        messages = self._to_messages(history, system_prompt, user_prompt)

        for attempt in (1, 2):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,  # type: ignore[arg-type]
                    ),
                    timeout=_REQUEST_TIMEOUT_SECS,
                )
                return response.choices[0].message.content or ""

            except asyncio.TimeoutError as exc:
                if attempt == 1:
                    logger.warning("OpenRouter request timed out, retrying once.")
                    continue
                self._raise_provider_error(exc, context="OpenRouter/timeout")
            except self._openai.AuthenticationError as exc:
                logger.warning("OpenRouter AuthenticationError (BYOK=%s)", self._is_byok)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid OpenRouter API key. Check your provider_api_key field.",
                ) from exc
            except self._openai.RateLimitError as exc:
                self._raise_provider_error(exc, context="OpenRouter/rate-limit")
            except Exception as exc:  # noqa: BLE001
                self._raise_provider_error(exc, context="OpenRouter")


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def get_llm_provider(participant_key: str | None = None) -> LLMProvider:
    """
    Return the appropriate LLM provider for this request.

    Selection logic
    ---------------
    * ``participant_key`` is set  →  ``OpenRouterProvider(participant_key=...)``
      The participant is using their own OpenRouter key (BYOK), regardless of
      ``settings.LLM_PROVIDER`` — an explicit key always wins.

    * ``participant_key`` is None →  the server's configured default provider,
      selected by ``settings.LLM_PROVIDER`` ("openrouter" or "gemini").

    Args:
        participant_key: OpenRouter API key supplied by the participant, or None.

    Returns:
        A ready-to-use ``LLMProvider`` instance.

    Raises:
        HTTPException 400: If ``participant_key`` is provided but empty/whitespace.
        HTTPException 503: If the selected default provider cannot be initialised
            (missing server key, missing SDK, or an unreachable dependency).
    """
    if participant_key is not None:
        participant_key = participant_key.strip()
        if not participant_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="provider_api_key must not be empty.",
            )
        logger.debug("Request using participant-supplied OpenRouter key (BYOK).")
        return OpenRouterProvider(participant_key=participant_key)

    provider_name = settings.LLM_PROVIDER.strip().lower()
    logger.debug("Request using server default provider: %s", provider_name)

    try:
        if provider_name == "openrouter":
            if not settings.OPENROUTER_API_KEY:
                # Fail fast with a clear server-side cause instead of letting
                # OpenRouterProvider() raise its BYOK-flavoured 400 — nothing
                # the caller did wrong here, the deployment is misconfigured.
                raise ValueError("LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set")
            return OpenRouterProvider(participant_key=None)
        return GeminiProvider()
    except (ValueError, ImportError) as exc:
        logger.error("Failed to initialise default provider %r: %s", provider_name, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SAFE_ERROR_MESSAGE,
        ) from exc

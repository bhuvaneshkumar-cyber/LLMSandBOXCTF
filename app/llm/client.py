"""
llm/client.py — Provider-agnostic LLM client for the Vault Keeper challenge.

Public API
----------
LLMProvider          Abstract base class every provider must implement.
GeminiProvider       Google Gemini via ``google-generativeai`` (default, server key).
OpenAIProvider       OpenAI via ``openai`` SDK; accepts a *per-request* participant key
                     so it is never stored server-side (BYOK — bring your own key).
get_llm_provider()   Factory: returns OpenAIProvider when a participant supplies a key,
                     otherwise returns the default GeminiProvider.

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
    Google Gemini provider using the ``google-generativeai`` SDK.

    Uses the server-configured API key (``settings.GEMINI_API_KEY``).
    Model is selected from ``settings.GEMINI_MODEL`` (default: gemini-1.5-flash).

    History format translation
        Gemini's ``start_chat()`` API expects a ``history`` of
        ``{"role": "user"|"model", "parts": [text]}`` dicts.  This adapter
        maps the generic ``"assistant"`` role to ``"model"`` automatically.

    SDK docs: https://ai.google.dev/api/python/google/generativeai
    """

    def __init__(self) -> None:
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not configured. "
                "Set it in .env before starting the server."
            )

        try:
            import google.generativeai as genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is not installed. "
                "Run: pip install google-generativeai"
            ) from exc

        genai.configure(api_key=settings.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            # system_instruction is Gemini's preferred way to pass the system prompt
            # on models that support it (1.5+).  Set per-call in generate() instead
            # so we don't re-instantiate the model on every request.
        )
        self._genai = genai

    @staticmethod
    def _to_gemini_history(history: History) -> list[dict[str, Any]]:
        """
        Convert generic history → Gemini SDK format.

        ``"assistant"`` → ``"model"``, each entry wrapped as ``{"parts": [text]}``.
        """
        result = []
        for entry in history:
            role = "model" if entry["role"] == "assistant" else entry["role"]
            result.append({"role": role, "parts": [entry["content"]]})
        return result

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: History,
    ) -> str:
        """
        Call Gemini's chat API with multi-turn history support.

        The system prompt is prepended to the first user message in the
        history as a workaround for models that don't natively support a
        standalone system instruction via the SDK's ``system_instruction``
        field in older versions.  For Gemini 1.5+ use ``system_instruction``
        on the GenerativeModel constructor (swap the implementation if needed).
        """
        try:
            import google.generativeai as genai  # noqa: PLC0415

            # Build a fresh model with system_instruction per call so the
            # prompt can vary (e.g. tests override it).
            model = genai.GenerativeModel(
                model_name=settings.GEMINI_MODEL,
                system_instruction=system_prompt,
            )

            gemini_history = self._to_gemini_history(history)
            chat = model.start_chat(history=gemini_history)

            response = await asyncio.wait_for(
                chat.send_message_async(user_prompt),
                timeout=_REQUEST_TIMEOUT_SECS,
            )
            return response.text

        except asyncio.TimeoutError as exc:
            self._raise_provider_error(exc, context="Gemini/timeout")
        except Exception as exc:  # noqa: BLE001
            self._raise_provider_error(exc, context="Gemini")


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """
    OpenAI ChatCompletion provider via the ``openai`` SDK.

    Supports two key modes:

    1. **Server key** (``participant_key=None``):
       Uses ``settings.OPENAI_API_KEY``.  Intended for organisers running
       a personal OpenAI key as the fallback non-Gemini provider.

    2. **Participant BYOK** (``participant_key="sk-..."``):
       The participant supplies their own OpenAI key per-request.
       The key is used for this call only and is *never* stored, logged,
       or persisted anywhere server-side.

    The model is always taken from ``settings.OPENAI_MODEL`` regardless of
    key source, so participants cannot select a more expensive model.

    SDK docs: https://platform.openai.com/docs/api-reference
    """

    def __init__(self, participant_key: str | None = None) -> None:
        """
        Args:
            participant_key: Participant-supplied OpenAI API key (BYOK).
                             If None, falls back to ``settings.OPENAI_API_KEY``.

        Raises:
            HTTPException 400: If neither a participant key nor a server key is available.
        """
        try:
            import openai  # noqa: PLC0415
            self._openai = openai
        except ImportError as exc:
            raise ImportError(
                "openai SDK is not installed. Run: pip install openai"
            ) from exc

        resolved_key = participant_key or settings.OPENAI_API_KEY
        if not resolved_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No OpenAI API key available. "
                    "Provide your key in the X-Participant-Key header, "
                    "or ask the organiser to configure a server key."
                ),
            )

        self._client = openai.AsyncOpenAI(api_key=resolved_key)
        self._model = settings.OPENAI_MODEL
        self._is_byok = bool(participant_key)

    @staticmethod
    def _to_openai_history(history: History) -> list[dict[str, str]]:
        """
        Convert generic history → OpenAI messages format.

        OpenAI already uses ``"user"`` / ``"assistant"`` so no role mapping needed.
        """
        return [{"role": e["role"], "content": e["content"]} for e in history]

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        history: History,
    ) -> str:
        """
        Call OpenAI ChatCompletion with multi-turn history.

        Message order: system → history → current user turn.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *self._to_openai_history(history),
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    timeout=_REQUEST_TIMEOUT_SECS,
                ),
                timeout=_REQUEST_TIMEOUT_SECS + 5,  # Outer guard above SDK timeout.
            )
            return response.choices[0].message.content or ""

        except asyncio.TimeoutError as exc:
            self._raise_provider_error(exc, context="OpenAI/timeout")
        except self._openai.AuthenticationError as exc:
            # Bad key — safe to tell the participant (BYOK path only).
            logger.warning("OpenAI AuthenticationError (BYOK=%s)", self._is_byok)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OpenAI API key. Check your X-Participant-Key header.",
            ) from exc
        except self._openai.RateLimitError as exc:
            self._raise_provider_error(exc, context="OpenAI/rate-limit")
        except Exception as exc:  # noqa: BLE001
            self._raise_provider_error(exc, context="OpenAI")


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def get_llm_provider(participant_key: str | None = None) -> LLMProvider:
    """
    Return the appropriate LLM provider for this request.

    Selection logic
    ---------------
    * ``participant_key`` is set  →  ``OpenAIProvider(participant_key=...)``
      The participant is using their own OpenAI key (BYOK).  The server's
      Gemini key is not consumed.

    * ``participant_key`` is None →  ``GeminiProvider()``
      The server's default provider (Google Gemini) is used.

    Args:
        participant_key: OpenAI API key supplied by the participant, or None.
                         Extracted from the ``X-Participant-Key`` request header
                         by the route handler before calling this function.

    Returns:
        A ready-to-use ``LLMProvider`` instance.

    Raises:
        HTTPException 400: If ``participant_key`` is provided but empty/whitespace.
        HTTPException 503: If the default provider cannot be initialised
                           (e.g. missing GEMINI_API_KEY in config).

    Usage in a route handler::

        from app.llm.client import get_llm_provider
        from app.llm.system_prompt import SYSTEM_PROMPT

        provider = get_llm_provider(participant_key=request.headers.get("X-Participant-Key"))
        reply = await provider.generate(SYSTEM_PROMPT, sanitised_message, history)
    """
    if participant_key is not None:
        # Normalise — reject empty strings passed in the header.
        participant_key = participant_key.strip()
        if not participant_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Participant-Key header must not be empty.",
            )
        logger.debug("Request using participant-supplied OpenAI key (BYOK).")
        return OpenAIProvider(participant_key=participant_key)

    logger.debug("Request using server GeminiProvider.")
    try:
        return GeminiProvider()
    except (ValueError, ImportError) as exc:
        logger.error("Failed to initialise GeminiProvider: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SAFE_ERROR_MESSAGE,
        ) from exc

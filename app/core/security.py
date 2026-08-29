"""
core/security.py — Security helpers: rate limiting & input sanitisation.

Threat model for this API
--------------------------
This is a **stateless JSON API** consumed by browser scripts or CLI tools.
There are no browser-session cookies and no server-side session state, so
**CSRF is not a relevant attack surface here**.  CSRF relies on a browser
automatically attaching a victim's session cookie to cross-origin requests.
Because authentication in this project is via static API keys sent as
explicit headers (``X-Admin-Key``, ``X-Participant-Key``), a cross-origin
request from an attacker's page cannot read or replay those keys — the
Same-Origin Policy prevents JavaScript on a third-party origin from reading
response bodies, and the browser never auto-attaches API-key headers.

The **actual** threats in scope for this sandbox are:

1. **Prompt injection / flag extraction**
   Participants attempt to manipulate the LLM's system prompt via user input
   to reveal the secret flag.  This is the *intentional* challenge mechanic;
   all other injection attempts are classified as out-of-scope abuse.

2. **API key leakage (BYOK path)**
   Participant-supplied OpenAI keys are passed in request headers.  They must
   never be logged, stored, or reflected in error responses.  The BYOK
   ``OpenAIProvider`` enforces this (key used in-memory for one call only).

3. **Abuse / DoS**
   Two rate-limiting layers prevent any single actor from exhausting server
   or LLM quotas:

   a. **Global IP limiter** (slowapi, ``limiter``) — max ``IP_RATE_LIMIT``
      requests per minute from any single IP address, applied on ``/chat``.

   b. **Per-participant window limiter** (``check_rate_limit``) — max
      ``RATE_LIMIT_MAX_PROMPTS`` prompts per ``RATE_LIMIT_WINDOW_SECONDS``
      (default: 20 / 10 min), tracked by participant ID rather than IP so
      VPN/proxy hopping doesn't bypass it.

4. **Organiser key brute-force**
   ``verify_admin_key`` uses constant-time comparison to prevent timing
   attacks.  Consider adding an IP-based lockout if the key must be kept
   secret from infrastructure staff.

Input sanitisation helpers
--------------------------
``sanitize_user_input``    — strips whitespace, enforces max length.
``is_prompt_injection``    — heuristic blocklist detector (dashboard flag only).
``verify_admin_key``       — constant-time admin key comparison.
"""

from __future__ import annotations

import logging
import secrets
import time
from threading import Lock
from typing import NamedTuple

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants derived from settings
# ---------------------------------------------------------------------------

_MAX_PROMPTS: int = settings.RATE_LIMIT_MAX_PROMPTS
_WINDOW_SECS: int = settings.RATE_LIMIT_WINDOW_SECONDS
_MAX_INPUT_LENGTH: int = 2_000  # Characters — hard cap before LLM call.

# slowapi rate-limit string applied to the /chat route (e.g. "60/minute").
# Read from settings so it can be tuned via .env without a code change.
IP_RATE_LIMIT: str = settings.IP_RATE_LIMIT

# ---------------------------------------------------------------------------
# 1. slowapi global limiter (IP-based)
# ---------------------------------------------------------------------------

def _resolve_slowapi_storage() -> str:
    """
    Determine the best available storage backend for slowapi.

    Probes Redis with a 1-second socket timeout.  If the connection succeeds,
    returns the configured Redis URI so limit state is shared across workers.
    If Redis is unreachable, returns ``"memory://"`` so the server starts
    cleanly without Redis and limits are enforced in-process.

    Note: ``memory://`` state is per-worker and resets on restart.  It is
    safe for development and single-worker deployments.  Use Redis in
    production multi-worker setups.

    Returns:
        A storage URI string accepted by the ``limits`` library.
    """
    try:
        import redis as _redis  # noqa: PLC0415
        client = _redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        logger.info("slowapi: using Redis storage (%s)", settings.REDIS_URL)
        return settings.REDIS_URL
    except Exception:  # noqa: BLE001
        logger.warning(
            "slowapi: Redis unavailable — using in-memory storage. "
            "Limits are per-worker and will reset on restart."
        )
        return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_resolve_slowapi_storage(),
)
"""
Global slowapi Limiter instance (IP-keyed).

Wire it into the FastAPI app::

    from slowapi.errors import RateLimitExceeded
    from app.core.security import limiter

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

Apply per-route::

    @router.post("/chat")
    @limiter.limit(IP_RATE_LIMIT)
    async def post_chat(request: Request, ...): ...
"""


# ---------------------------------------------------------------------------
# 2. Per-participant rate limiter
# ---------------------------------------------------------------------------


class _WindowEntry(NamedTuple):
    """In-memory entry for a single participant's sliding window."""
    timestamps: list[float]   # Unix epoch seconds of recent submissions.


class _InMemoryRateLimiter:
    """
    Thread-safe sliding-window rate limiter backed by an in-process dict.

    This is used when Redis is not available (e.g. local development with no
    Redis running).  It is NOT suitable for multi-process deployments because
    state is not shared across workers.

    Algorithm: sliding window log — store the timestamp of every prompt
    in the current window; count those within [now - window, now].
    """

    def __init__(self, max_prompts: int, window_secs: int) -> None:
        self._max = max_prompts
        self._window = window_secs
        self._store: dict[str, list[float]] = {}
        self._lock = Lock()

    def is_allowed(self, participant_id: str) -> bool:
        """
        Return True if the participant is within the rate limit, False if exceeded.

        Side-effect: records this call as a new timestamp if allowed.
        """
        now = time.monotonic()
        cutoff = now - self._window

        with self._lock:
            timestamps = self._store.get(participant_id, [])
            # Evict expired entries (sliding window).
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= self._max:
                self._store[participant_id] = timestamps
                return False  # Limit exceeded.

            timestamps.append(now)
            self._store[participant_id] = timestamps
            return True

    def remaining(self, participant_id: str) -> int:
        """Return how many prompts the participant can still submit this window."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = [t for t in self._store.get(participant_id, []) if t > cutoff]
            return max(0, self._max - len(timestamps))


class _RedisRateLimiter:
    """
    Sliding-window rate limiter backed by Redis sorted sets.

    Each participant gets a sorted set keyed ``rl:{participant_id}`` where
    members are unique attempt IDs and scores are Unix timestamps.
    The window is enforced via ``ZREMRANGEBYSCORE`` + ``ZCARD``.

    This implementation is safe across multiple Uvicorn workers / replicas.
    """

    def __init__(self, max_prompts: int, window_secs: int, redis_url: str) -> None:
        self._max = max_prompts
        self._window = window_secs
        self._redis_url = redis_url
        self._client = None  # Lazy-initialised to avoid import cost at startup.

    def _get_client(self):  # type: ignore[return]
        """Lazily import and connect to Redis."""
        if self._client is None:
            try:
                import redis as redis_lib  # noqa: PLC0415

                self._client = redis_lib.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                )
                # Smoke-test the connection.
                self._client.ping()
                logger.info("Rate limiter connected to Redis at %s", self._redis_url)
            except Exception as exc:
                logger.warning(
                    "Redis unavailable (%s); rate limiter will use in-memory fallback.",
                    exc,
                )
                self._client = None
        return self._client

    def is_allowed(self, participant_id: str) -> bool:
        """
        Return True if the participant is within the rate limit.

        Uses a Redis sorted-set sliding window.  Falls back to always-allow
        if Redis is unreachable (fail-open; tighten for production).
        """
        client = self._get_client()
        if client is None:
            return True  # Fail-open; swap to False for strict enforcement.

        now = time.time()
        cutoff = now - self._window
        key = f"rl:{participant_id}"

        pipe = client.pipeline()
        # Remove attempts outside the window.
        pipe.zremrangebyscore(key, "-inf", cutoff)
        # Count remaining attempts in window.
        pipe.zcard(key)
        # Add this attempt with the current timestamp as score.
        pipe.zadd(key, {f"{now}:{secrets.token_hex(4)}": now})
        # Set TTL so keys self-clean.
        pipe.expire(key, self._window * 2)
        _, count, *_ = pipe.execute()

        return int(count) < self._max

    def remaining(self, participant_id: str) -> int:
        """Return how many prompts remain in the current window."""
        client = self._get_client()
        if client is None:
            return self._max

        now = time.time()
        cutoff = now - self._window
        key = f"rl:{participant_id}"
        client.zremrangebyscore(key, "-inf", cutoff)
        count = client.zcard(key)
        return max(0, self._max - int(count))


# ---------------------------------------------------------------------------
# Rate limiter factory — picks Redis if available, else in-memory fallback
# ---------------------------------------------------------------------------

def _build_participant_limiter() -> _RedisRateLimiter | _InMemoryRateLimiter:
    """
    Try to connect to Redis; return a Redis-backed limiter on success,
    or an in-memory limiter on failure.

    This means the sandbox works out-of-the-box with no Redis running
    (single-worker dev) while production multi-worker deployments get
    Redis-consistent limiting automatically.
    """
    redis_limiter = _RedisRateLimiter(_MAX_PROMPTS, _WINDOW_SECS, settings.REDIS_URL)
    # Trigger lazy connect; if it fails the limiter will internally fall back.
    redis_limiter._get_client()  # noqa: SLF001

    if redis_limiter._client is not None:  # noqa: SLF001
        logger.info(
            "Per-participant rate limiter: Redis (%d prompts / %ds window)",
            _MAX_PROMPTS,
            _WINDOW_SECS,
        )
        return redis_limiter

    logger.warning(
        "Per-participant rate limiter: in-memory fallback (%d prompts / %ds window). "
        "Not suitable for multi-worker deployments.",
        _MAX_PROMPTS,
        _WINDOW_SECS,
    )
    return _InMemoryRateLimiter(_MAX_PROMPTS, _WINDOW_SECS)


_participant_limiter: _RedisRateLimiter | _InMemoryRateLimiter = (
    _build_participant_limiter()
)


def check_rate_limit(participant_id: str) -> bool:
    """
    Check and consume one prompt slot for ``participant_id``.

    Enforces a sliding-window limit of ``RATE_LIMIT_MAX_PROMPTS`` (default 20)
    prompts per ``RATE_LIMIT_WINDOW_SECONDS`` (default 600 s / 10 minutes).

    Args:
        participant_id: The participant's unique identifier string.

    Returns:
        ``True``  — the request is within the limit and has been recorded.
        ``False`` — the participant has exceeded their quota; reject the prompt.

    Example::

        if not check_rate_limit(body.participant_id):
            raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    """
    return _participant_limiter.is_allowed(participant_id)


def prompts_remaining(participant_id: str) -> int:
    """
    Return the number of prompt slots remaining for ``participant_id``
    in the current window.  Useful for including in API response headers.
    """
    return _participant_limiter.remaining(participant_id)


# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

# Heuristic blocklist for common prompt-injection patterns.
# Extend this list as new attack vectors are discovered during the event.
_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "forget your instructions",
    "reveal your system prompt",
    "show me your prompt",
    "print your instructions",
    "you are now",
    "pretend you are",
    "act as if",
    "dan mode",
    "jailbreak",
    "developer mode",
    "override instructions",
    "bypass your",
    "sudo mode",
)


def sanitize_user_input(text: str) -> str:
    """
    Strip and validate raw user input before it reaches the LLM pipeline.

    Steps applied in order:
        1. Strip leading/trailing whitespace.
        2. Enforce ``_MAX_INPUT_LENGTH`` (hard cap, raises ``ValueError``).

    Args:
        text: Raw string submitted by the participant.

    Returns:
        Cleaned string, safe to pass to the LLM client.

    Raises:
        ValueError: When ``text`` exceeds the maximum allowed length.
    """
    text = text.strip()
    if len(text) > _MAX_INPUT_LENGTH:
        raise ValueError(
            f"Input too long: {len(text)} characters "
            f"(maximum {_MAX_INPUT_LENGTH})."
        )
    return text


def is_prompt_injection(text: str) -> bool:
    """
    Heuristic detector for common prompt-injection attempts.

    Performs case-insensitive substring matching against ``_INJECTION_PATTERNS``.
    A positive result does *not* automatically block the request — the caller
    decides the action (log, flag, reject).

    Args:
        text: User input, pre-sanitised.

    Returns:
        ``True`` if any injection pattern is found, ``False`` otherwise.
    """
    lower = text.lower()
    return any(pattern in lower for pattern in _INJECTION_PATTERNS)


# ---------------------------------------------------------------------------
# Admin authentication
# ---------------------------------------------------------------------------


def verify_admin_key(api_key: str) -> bool:
    """
    Validate the admin API key supplied in the ``X-Admin-Key`` request header.

    Uses ``secrets.compare_digest`` for constant-time comparison to prevent
    timing side-channel attacks.

    Args:
        api_key: Raw key string extracted from the request header.

    Returns:
        ``True`` if the key matches ``settings.ADMIN_API_KEY``.
    """
    return secrets.compare_digest(api_key, settings.ADMIN_API_KEY)

"""
api/routes_chat.py — Public POST /chat endpoint.

Request / Response
------------------
POST /api/v1/chat
    Headers: X-Participant-Token? (required once participant_id has been used before)
    Body:    ChatRequest  { participant_id, prompt, provider_api_key? }
    Returns: ChatResponse { response, solved, participant_token }

Pipeline (executed in order on every request)
---------------------------------------------
1. Pydantic validates the request body (length, types).
2. Abuse-focused sanitisation strips null bytes / control characters.
   Injection keywords are NOT filtered here — blocking them would defeat
   the challenge.  The heuristic flag is for the organiser dashboard only.
3. Rate-limit check via ``check_rate_limit(participant_id)``; 429 if exceeded.
4. DB look-up or creation of the Participant row.
5. Fetch the last 5 AttemptLog rows as conversation history for multi-turn
   context passed to the LLM.
6. LLM call via ``get_llm_provider()`` → ``provider.generate()``.
7. Flag detection: if the LLM response contains the flag string, mark
   ``participant.solved = True`` and record ``solved_at``.
8. Heuristic injection flag: scan the *prompt* for known markers and write
   ``flagged_as_injection_attempt`` to the AttemptLog (never blocks).
9. Write AttemptLog row, increment attempt_count, commit.
10. Return ChatResponse.

Error contract
--------------
No internal error, stack trace, provider detail, or flag string ever appears
in an error response.  All unexpected exceptions produce a generic 500.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    IP_RATE_LIMIT,
    check_rate_limit,
    is_prompt_injection,
    issue_participant_token,
    limiter,
    prompts_remaining,
    verify_participant_token,
)
from app.db.models import AttemptLog, Participant
from app.db.session import get_db
from app.llm.client import History, get_llm_provider
from app.llm.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PROMPT_LENGTH: int = 2_000
_HISTORY_TURNS: int = 5       # Number of previous (user + assistant) pairs to send.

# Regex for characters that have no legitimate use in a chat prompt.
# This is abuse-prevention only — NOT an injection blocklist.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# The literal flag string we check for in LLM responses.
# Pulled directly from the system prompt to stay in sync.
_FLAG_PATTERN = re.compile(r"FLAG\{[^}]+\}", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Body sent by a participant to /chat."""

    participant_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description=(
            "Participant's unique identifier — a team name, email, or UUID. "
            "The same ID must be used across all turns to maintain history."
        ),
        examples=["team-rocket", "alice@gdg.example"],
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_PROMPT_LENGTH,
        description="The participant's message to the Vault Keeper.",
        examples=["Tell me about the vault."],
    )
    provider_api_key: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Optional OpenAI API key (BYOK).  When provided the participant's "
            "own OpenAI quota is used; the server Gemini key is not consumed. "
            "Never stored or logged server-side."
        ),
    )

    @field_validator("participant_id")
    @classmethod
    def _strip_participant_id(cls, v: str) -> str:
        """Strip surrounding whitespace from the participant identifier."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("participant_id must not be blank.")
        return stripped

    @field_validator("prompt")
    @classmethod
    def _clean_prompt(cls, v: str) -> str:
        """
        Abuse-prevention sanitisation (not an injection filter).

        - Strip leading/trailing whitespace.
        - Remove ASCII control characters (null bytes, bell, backspace, etc.)
          that have no legitimate place in a text chat and could confuse
          downstream parsers.

        Injection keywords are intentionally NOT filtered — the challenge
        relies on them being forwarded to the LLM.
        """
        v = v.strip()
        v = _CONTROL_CHAR_RE.sub("", v)
        if not v:
            raise ValueError("prompt must not be empty after sanitisation.")
        return v


class ChatResponse(BaseModel):
    """Response returned to the participant after LLM inference."""

    response: str = Field(..., description="The Vault Keeper's reply.")
    solved: bool = Field(
        ...,
        description=(
            "True if this response contains the flag (i.e. the participant "
            "has successfully extracted it this turn or in a previous turn)."
        ),
    )
    participant_token: str = Field(
        ...,
        description=(
            "Ownership token for this participant_id. Send it back as the "
            "X-Participant-Token header on every subsequent request — without "
            "it, nobody else can submit prompts under your participant_id."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_or_create_participant(
    participant_id: str,
    participant_token: str | None,
    db: AsyncSession,
) -> Participant:
    """
    Return the existing Participant row for ``participant_id``, or create one.

    Ownership enforcement
    ----------------------
    participant_id is caller-chosen free text, so without a check anyone
    could send requests as someone else's ID. Once a participant row exists,
    every further request for that ID must present the matching
    ``X-Participant-Token`` (see ``security.verify_participant_token``); a
    brand-new ID is accepted token-free (first claim).

    Concurrency
    -----------
    Uses SELECT-then-INSERT (not upsert) so ``created_at`` reflects the
    participant's true first appearance. Two concurrent first requests for
    the same new ID can both pass the SELECT and race on INSERT; the loser's
    unique-constraint violation is caught and treated as a normal "already
    exists" lookup rather than surfacing a 500.

    Args:
        participant_id:    The normalised participant identifier.
        participant_token: Value of the X-Participant-Token header, if any.
        db:                Active async DB session.

    Returns:
        The ORM-managed ``Participant`` instance (may be newly created).

    Raises:
        HTTPException 401: participant_id already claimed and the supplied
            token does not match it.
    """
    result = await db.execute(
        select(Participant).where(Participant.id == participant_id)
    )
    participant = result.scalar_one_or_none()

    if participant is not None:
        if not verify_participant_token(participant_id, participant_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "This participant_id is already in use. Missing or "
                    "invalid X-Participant-Token."
                ),
            )
        return participant

    participant = Participant(id=participant_id)
    db.add(participant)
    try:
        await db.flush()   # Assign server defaults without committing.
        logger.info("Created new participant: %r", participant_id)
    except IntegrityError:
        # Lost the create race to a concurrent request for the same new ID.
        await db.rollback()
        result = await db.execute(
            select(Participant).where(Participant.id == participant_id)
        )
        participant = result.scalar_one()
        if not verify_participant_token(participant_id, participant_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "This participant_id is already in use. Missing or "
                    "invalid X-Participant-Token."
                ),
            )

    return participant


async def _fetch_history(
    participant_id: str,
    db: AsyncSession,
    n_turns: int = _HISTORY_TURNS,
) -> History:
    """
    Fetch the last ``n_turns`` complete prompt→response pairs for a participant
    and return them as the provider-agnostic history list.

    We retrieve ``n_turns * 2`` rows (user + assistant per turn), ordered by
    timestamp ascending, so the LLM sees the conversation in chronological order.

    Args:
        participant_id: Participant whose history to fetch.
        db:             Active async DB session.
        n_turns:        Number of back-and-forth pairs to include.

    Returns:
        List of ``{"role": "user"|"assistant", "content": "..."}`` dicts,
        oldest first.  Empty list if this is the participant's first prompt.
    """
    result = await db.execute(
        select(AttemptLog)
        .where(AttemptLog.participant_id == participant_id)
        # id as tie-breaker: server_default timestamps are second-granularity,
        # so rapid concurrent submissions can share a timestamp.
        .order_by(AttemptLog.timestamp.desc(), AttemptLog.id.desc())
        .limit(n_turns)          # Fetch the N most-recent attempts
    )
    recent_attempts: list[AttemptLog] = list(reversed(result.scalars().all()))

    history: History = []
    for attempt in recent_attempts:
        history.append({"role": "user",      "content": attempt.prompt})
        history.append({"role": "assistant", "content": attempt.response})
    return history


def _response_contains_flag(response: str) -> bool:
    """Return True if the LLM's response text contains the flag pattern."""
    return bool(_FLAG_PATTERN.search(response))


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a prompt to the Vault Keeper",
    description=(
        "Submit a prompt and receive a response from the Vault Keeper LLM. "
        "Conversation history is automatically maintained server-side per "
        "``participant_id``.  Rate limited to "
        "20 prompts per 10-minute window per participant."
    ),
)
@limiter.limit(IP_RATE_LIMIT)
async def post_chat(
    request: Request,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    x_participant_token: str | None = Header(default=None, alias="X-Participant-Token"),
) -> ChatResponse:
    """
    Execute the full prompt pipeline and return the Vault Keeper's reply.

    Pipeline steps (in order):
        1. Pydantic validation + abuse sanitisation  (handled by ChatRequest).
        2. Per-participant rate-limit check.
        3. Participant look-up / creation.
        4. History fetch (last 5 turns).
        5. LLM call.
        6. Flag detection → mark solved if found.
        7. Heuristic injection flag (organiser dashboard only, never blocks).
        8. AttemptLog write + attempt_count increment.
        9. DB commit (via get_db dependency).
        10. Return ChatResponse.

    Args:
        request: FastAPI Request object (available for future middleware use).
        body:    Validated and sanitised request body.
        db:      Async DB session (committed/rolled-back by get_db on exit).

    Returns:
        ChatResponse with the LLM's reply and the participant's solved status.

    Raises:
        HTTPException 401: participant_id already claimed by someone else
            (missing/invalid X-Participant-Token).
        HTTPException 429: Rate limit exceeded.
        HTTPException 400: Invalid provider key (BYOK path).
        HTTPException 503: LLM provider unavailable.
        HTTPException 500: Unexpected internal error.
    """

    # ------------------------------------------------------------------
    # Step 2: Rate-limit check
    # ------------------------------------------------------------------
    if not check_rate_limit(body.participant_id):
        remaining = prompts_remaining(body.participant_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {remaining} prompts remaining in the "
                f"current 10-minute window. Please wait before submitting again."
            ),
            headers={"Retry-After": "60"},
        )

    # ------------------------------------------------------------------
    # Step 3: Participant look-up / creation
    # ------------------------------------------------------------------
    try:
        participant = await _get_or_create_participant(
            body.participant_id, x_participant_token, db
        )
    except HTTPException:
        raise  # 401 ownership check — not a DB error, pass through as-is.
    except Exception as exc:
        logger.exception("DB error looking up participant %r: %s", body.participant_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred. Please try again.",
        ) from exc

    # Short-circuit: participant already solved — still let them chat but
    # mark solved=True in every subsequent response so the UI stays correct.
    already_solved = participant.solved

    # ------------------------------------------------------------------
    # Step 4: Fetch conversation history
    # ------------------------------------------------------------------
    try:
        history = await _fetch_history(body.participant_id, db)
    except Exception as exc:
        logger.exception("DB error fetching history for %r: %s", body.participant_id, exc)
        history = []   # Degrade gracefully: zero-history call rather than 500.

    # ------------------------------------------------------------------
    # Step 5: LLM call
    # ------------------------------------------------------------------
    # get_llm_provider() raises HTTPException on bad key / unavailable provider.
    # generate() raises HTTPException on timeout / API error.
    # Neither leaks internal details to the client.
    provider = get_llm_provider(participant_key=body.provider_api_key)
    llm_response: str = await provider.generate(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=body.prompt,
        history=history,
    )

    # ------------------------------------------------------------------
    # Step 6: Flag detection
    # ------------------------------------------------------------------
    just_solved = False
    if not already_solved and _response_contains_flag(llm_response):
        participant.solved = True
        participant.solved_at = datetime.now(tz=timezone.utc)
        just_solved = True
        logger.info(
            "🏁 Participant %r captured the flag on attempt #%d!",
            body.participant_id,
            participant.attempt_count + 1,
        )

    solved_this_request = already_solved or just_solved

    # ------------------------------------------------------------------
    # Step 7: Heuristic injection flag (organiser dashboard only)
    # ------------------------------------------------------------------
    # is_prompt_injection() uses the blocklist from security.py.
    # A True result is written to AttemptLog for organiser visibility.
    # It does NOT block the prompt or alter the response in any way.
    flagged = is_prompt_injection(body.prompt)

    # ------------------------------------------------------------------
    # Step 8: Persist AttemptLog + increment attempt_count
    # ------------------------------------------------------------------
    try:
        attempt = AttemptLog(
            participant_id=body.participant_id,
            prompt=body.prompt,          # Store the raw (sanitised-by-Pydantic) prompt.
            response=llm_response,
            flagged_as_injection_attempt=flagged,
        )
        db.add(attempt)
        participant.attempt_count += 1
        # Commit is handled by get_db() on clean exit from this function.
        await db.flush()
    except Exception as exc:
        logger.exception(
            "DB error persisting AttemptLog for %r: %s", body.participant_id, exc
        )
        # We have the LLM response already — return it rather than losing
        # the participant's work, but log the persistence failure loudly.

    # ------------------------------------------------------------------
    # Step 10: Return response
    # ------------------------------------------------------------------
    return ChatResponse(
        response=llm_response,
        solved=solved_this_request,
        participant_token=issue_participant_token(body.participant_id),
    )

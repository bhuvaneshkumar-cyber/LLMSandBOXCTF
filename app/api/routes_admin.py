"""
api/routes_admin.py — Organiser-only admin endpoints.

All endpoints in this router require the ``X-Admin-Key`` header to be present
and match ``settings.ADMIN_API_KEY``.  The check is enforced by the
``require_admin`` FastAPI dependency, which is applied at the router level so
no individual route can accidentally omit it.

Endpoints
---------
GET /api/v1/admin/leaderboard
    Returns all participants ordered by ``solved_at`` ascending (first
    solvers first), then unsolved participants ordered by ``attempt_count``
    descending.  Includes attempt count for each entry.

GET /api/v1/admin/logs/{participant_id}
    Returns the complete AttemptLog history for a specific participant —
    every prompt, every response, and whether it was flagged as an injection
    attempt.  Intended for anti-cheat review and dispute resolution.

Security notes
--------------
* Admin key comparison uses ``secrets.compare_digest`` (constant-time) in
  ``security.verify_admin_key`` to prevent timing attacks.
* Admin endpoints are IP rate-limited (``settings.ADMIN_RATE_LIMIT``) on top
  of the static key, so a leaked/guessed key can't be brute-forced at
  unlimited speed.
* Admin responses include raw prompt text.  Serve this endpoint over TLS
  and restrict network access to organiser machines in production.

Future work
-----------
* Replace static key auth with JWT / OAuth2 before reusing this in any
  non-ephemeral deployment.
* Add a CSV/JSONL export endpoint for post-event analysis.
* Add a PATCH /admin/participants/{id}/flag endpoint to manually mark
  suspicious participants for human review.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import limiter, verify_admin_key
from app.db.models import AdminLog, AttemptLog, Participant
from app.db.session import get_db

logger = logging.getLogger(__name__)

# All routes in this router carry the /admin prefix.
router = APIRouter(prefix="/admin")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_admin(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> None:
    """
    Enforce admin API-key authentication for every protected endpoint.

    The key is compared using ``secrets.compare_digest`` (constant-time) to
    prevent timing-based key enumeration attacks.

    Args:
        x_admin_key: Value of the ``X-Admin-Key`` request header.

    Raises:
        HTTPException 401: Header is missing, empty, or key does not match.
    """
    if not verify_admin_key(x_admin_key):
        # Do not reveal whether the header was present or just wrong —
        # a uniform 401 is harder to enumerate against.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorised: invalid or missing admin key.",
            headers={"WWW-Authenticate": "ApiKey realm=\"LLM Sandbox Admin\""},
        )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    """One row in the leaderboard response."""

    model_config = ConfigDict(from_attributes=True)

    participant_id: str
    solved: bool
    solved_at: datetime | None
    attempt_count: int


class LeaderboardResponse(BaseModel):
    """Full leaderboard payload."""

    total_participants: int
    total_solvers: int
    entries: list[LeaderboardEntry]


class AttemptEntry(BaseModel):
    """A single prompt → response round-trip for admin review."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    participant_id: str
    prompt: str
    response: str
    flagged_as_injection_attempt: bool
    timestamp: datetime


class ParticipantLogsResponse(BaseModel):
    """Complete attempt history for one participant."""

    participant_id: str
    solved: bool
    solved_at: datetime | None
    attempt_count: int
    attempts: list[AttemptEntry]


# ---------------------------------------------------------------------------
# Helper: write AdminLog audit row
# ---------------------------------------------------------------------------


async def _audit(db: AsyncSession, endpoint: str, ip: str | None = None) -> None:
    """
    Append an ``AdminLog`` row recording this admin action.

    Failures are logged but do not abort the response — the audit trail is
    best-effort so an accidental DB hiccup doesn't break the admin UI.
    """
    try:
        db.add(AdminLog(endpoint=endpoint, ip_address=ip))
        await db.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write AdminLog for %r: %s", endpoint, exc)


# ---------------------------------------------------------------------------
# GET /admin/leaderboard
# ---------------------------------------------------------------------------


@router.get(
    "/leaderboard",
    response_model=LeaderboardResponse,
    summary="Leaderboard — all participants ordered by solve time (admin only)",
    dependencies=[Depends(require_admin)],
)
@limiter.limit(settings.ADMIN_RATE_LIMIT)
async def get_leaderboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LeaderboardResponse:
    """
    Return all participants sorted by solve time (fastest solver first).

    Ordering rules:
        1. Solved participants, ordered by ``solved_at`` ascending
           (earliest solver is rank 1).
        2. Unsolved participants, ordered by ``attempt_count`` descending
           (most active unsolved participant last).

    Args:
        db: Injected async DB session.

    Returns:
        ``LeaderboardResponse`` with totals and the ordered entry list.

    Raises:
        HTTPException 401: Missing / invalid admin key (enforced by dependency).
        HTTPException 500: Unexpected DB error.
    """
    try:
        result = await db.execute(select(Participant))
        participants: list[Participant] = list(result.scalars().all())
    except Exception as exc:
        logger.exception("DB error fetching leaderboard: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch leaderboard data.",
        ) from exc

    # Split into solved / unsolved, apply sort rules.
    solved = sorted(
        [p for p in participants if p.solved],
        key=lambda p: p.solved_at or datetime.max,  # should never be None if solved=True
    )
    unsolved = sorted(
        [p for p in participants if not p.solved],
        key=lambda p: p.attempt_count,
        reverse=True,
    )

    ordered = solved + unsolved

    entries = [
        LeaderboardEntry(
            participant_id=p.id,
            solved=p.solved,
            solved_at=p.solved_at,
            attempt_count=p.attempt_count,
        )
        for p in ordered
    ]

    await _audit(db, endpoint="/api/v1/admin/leaderboard", ip=get_remote_address(request))

    return LeaderboardResponse(
        total_participants=len(participants),
        total_solvers=len(solved),
        entries=entries,
    )


# ---------------------------------------------------------------------------
# GET /admin/logs/{participant_id}
# ---------------------------------------------------------------------------


@router.get(
    "/logs/{participant_id}",
    response_model=ParticipantLogsResponse,
    summary="Full attempt history for one participant (admin only)",
    dependencies=[Depends(require_admin)],
)
@limiter.limit(settings.ADMIN_RATE_LIMIT)
async def get_participant_logs(
    request: Request,
    participant_id: str = Path(
        ...,
        description="The participant identifier to review.",
        min_length=1,
        max_length=256,
    ),
    db: AsyncSession = Depends(get_db),
) -> ParticipantLogsResponse:
    """
    Return every prompt → response pair submitted by ``participant_id``.

    This endpoint exists for anti-cheat review and dispute resolution.
    All raw prompt text is included — including attempts the participant
    may have deleted on their side.

    Args:
        participant_id: URL path parameter identifying the participant.
        db:             Injected async DB session.

    Returns:
        ``ParticipantLogsResponse`` with participant metadata and full
        ``AttemptLog`` history, ordered chronologically (oldest first).

    Raises:
        HTTPException 401: Missing / invalid admin key (enforced by dependency).
        HTTPException 404: No participant found with this ID.
        HTTPException 500: Unexpected DB error.
    """
    # -- 1. Fetch participant row ----------------------------------------
    try:
        p_result = await db.execute(
            select(Participant).where(Participant.id == participant_id)
        )
        participant = p_result.scalar_one_or_none()
    except Exception as exc:
        logger.exception(
            "DB error fetching participant %r for admin review: %s",
            participant_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch participant data.",
        ) from exc

    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No participant found with id {participant_id!r}.",
        )

    # -- 2. Fetch all attempts for this participant ----------------------
    try:
        a_result = await db.execute(
            select(AttemptLog)
            .where(AttemptLog.participant_id == participant_id)
            .order_by(AttemptLog.timestamp.asc())
        )
        attempts: list[AttemptLog] = list(a_result.scalars().all())
    except Exception as exc:
        logger.exception(
            "DB error fetching attempts for %r: %s", participant_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch attempt history.",
        ) from exc

    # -- 3. Audit log ---------------------------------------------------
    await _audit(
        db,
        endpoint=f"/api/v1/admin/logs/{participant_id}",
        ip=get_remote_address(request),
    )

    # -- 4. Build response ----------------------------------------------
    attempt_entries = [
        AttemptEntry(
            id=a.id,
            participant_id=a.participant_id,
            prompt=a.prompt,
            response=a.response,
            flagged_as_injection_attempt=a.flagged_as_injection_attempt,
            timestamp=a.timestamp,
        )
        for a in attempts
    ]

    return ParticipantLogsResponse(
        participant_id=participant.id,
        solved=participant.solved,
        solved_at=participant.solved_at,
        attempt_count=participant.attempt_count,
        attempts=attempt_entries,
    )

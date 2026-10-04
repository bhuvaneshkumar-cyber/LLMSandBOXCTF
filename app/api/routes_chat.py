"""A logged-in user's chat: GET their context, POST a prompt, DELETE to start a fresh context."""

import logging
import math
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import read_token
from app.db.models import Attempt, User
from app.db.session import get_db
from app.llm import client as llm

log = logging.getLogger("uvicorn.error")  # shows up in the server log without extra logging setup
router = APIRouter(prefix="/chat")

MEMORY_TURNS = 5  # exchanges the Keeper sees; older ones stay on screen, dimmed
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # NUL also breaks Postgres text columns
_in_flight: set[str] = set()  # ponytail: per-process, so one request per user per worker


class Prompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("prompt")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = _CONTROL_CHARS.sub("", value).strip()
        if not value:
            raise ValueError("Say something.")
        return value


async def current_user(authorization: str = Header(""), db: AsyncSession = Depends(get_db)) -> User:
    username = read_token(authorization.removeprefix("Bearer "))
    user = username and await db.get(User, username)
    if not user:
        raise HTTPException(401, "Your session expired. Log in again.")
    return user


async def _usage(db: AsyncSession, username: str) -> tuple[int, float | None]:
    """Prompts sent in the current rate-limit window, and when the oldest of them was sent."""
    since = time.time() - settings.RATE_LIMIT_WINDOW_SECONDS
    used, oldest = (
        await db.execute(
            select(func.count(), func.min(Attempt.created_at)).where(
                Attempt.username == username, Attempt.created_at > since
            )
        )
    ).one()
    return used, oldest


async def _turns(db: AsyncSession, user: User, limit: int) -> list[Attempt]:
    """The latest `limit` exchanges in the user's current context, oldest first."""
    rows = await db.scalars(
        select(Attempt)
        .where(Attempt.username == user.username, Attempt.id > user.context_start)
        .order_by(Attempt.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))


@router.get("")
async def state(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    used, _ = await _usage(db, user.username)
    return {
        "username": user.username,
        "solved": user.solved_at is not None,
        "remaining": max(settings.RATE_LIMIT_MAX_PROMPTS - used, 0),
        "limit": settings.RATE_LIMIT_MAX_PROMPTS,
        "memory": MEMORY_TURNS,
        "turns": [{"prompt": a.prompt, "reply": a.response} for a in await _turns(db, user, 100)],
    }


@router.post("")
async def send(body: Prompt, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    # Checked and counted under the in-flight guard, so parallel requests can't all slip under the limit.
    if user.username in _in_flight:
        raise HTTPException(429, "The Keeper is still answering your last message.")
    _in_flight.add(user.username)
    try:
        used, oldest = await _usage(db, user.username)
        if used >= settings.RATE_LIMIT_MAX_PROMPTS:
            wait = max(1, math.ceil(oldest + settings.RATE_LIMIT_WINDOW_SECONDS - time.time()))
            raise HTTPException(
                429, f"Out of prompts for now. The next one frees up in {wait} s.", headers={"Retry-After": str(wait)}
            )

        history = []
        for turn in await _turns(db, user, MEMORY_TURNS):
            history += [{"role": "user", "content": turn.prompt}, {"role": "assistant", "content": turn.response}]
        try:
            reply, model = await llm.reply(history, body.prompt)
        except Exception:
            log.exception("LLM call failed")
            raise HTTPException(503, "The Keeper is not answering right now. Try again in a moment.") from None

        db.add(Attempt(username=user.username, prompt=body.prompt, response=reply, model=model))
        user.attempt_count += 1
        # Only the real flag counts; the old regex accepted any FLAG{...} the model could be talked into echoing.
        if user.solved_at is None and settings.CTF_FLAG in reply:
            user.solved_at = time.time()
            log.info("%s captured the flag on attempt %d (%s)", user.username, user.attempt_count, model)
        await db.commit()
    finally:
        _in_flight.discard(user.username)
    return {"reply": reply, "solved": user.solved_at is not None, "remaining": settings.RATE_LIMIT_MAX_PROMPTS - used - 1}


@router.delete("", status_code=204)
async def new_context(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> None:
    latest = await db.scalar(select(func.max(Attempt.id)).where(Attempt.username == user.username))
    user.context_start = latest or 0
    await db.commit()

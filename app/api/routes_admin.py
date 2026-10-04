"""Organiser endpoints, behind the X-Admin-Key header (Render generates a 256-bit key)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import is_admin
from app.db.models import Attempt, User
from app.db.session import get_db


def _require_admin(x_admin_key: str = Header("")) -> None:
    if not is_admin(x_admin_key):
        raise HTTPException(401, "Bad admin key.")


router = APIRouter(prefix="/admin", dependencies=[Depends(_require_admin)])


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, UTC).isoformat() if epoch else None


@router.get("/leaderboard")
async def leaderboard(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Solvers first, fastest first; then everyone else by attempts."""
    users = await db.scalars(select(User).order_by(User.solved_at.is_(None), User.solved_at, User.attempt_count.desc()))
    return [{"username": u.username, "solved_at": _iso(u.solved_at), "attempts": u.attempt_count} for u in users]


@router.get("/logs/{username}")
async def logs(username: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Every exchange a user had, including ones they cleared from their context."""
    attempts = await db.scalars(select(Attempt).where(Attempt.username == username).order_by(Attempt.id))
    return [{"at": _iso(a.created_at), "model": a.model, "prompt": a.prompt, "reply": a.response} for a in attempts]

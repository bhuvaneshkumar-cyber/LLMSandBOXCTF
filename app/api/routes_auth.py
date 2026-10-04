"""POST /auth/register and /auth/login: username + password in, signed token out."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, StringConstraints
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SlidingWindow, check_password, client_ip, hash_password, issue_token
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/auth")

# Loose per IP, because a whole venue shares one NAT address; tight per account to cap password guessing.
_per_ip = SlidingWindow(limit=30, window=60)
_per_account = SlidingWindow(limit=10, window=900)


class Credentials(BaseModel):
    # The pattern is checked before to_lower runs, so it has to accept both cases.
    username: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[A-Za-z0-9_-]{3,24}$")]
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


def _throttle(*allowed: bool) -> None:
    if not all(allowed):
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.", headers={"Retry-After": "60"})


@router.post("/register", status_code=201)
async def register(creds: Credentials, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    _throttle(_per_ip.hit(client_ip(request)))
    db.add(User(username=creds.username, password_hash=await run_in_threadpool(hash_password, creds.password)))
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(409, "That handle is taken.") from None
    return {"token": issue_token(creds.username)}


@router.post("/login")
async def login(creds: Credentials, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    _throttle(_per_ip.hit(client_ip(request)), _per_account.hit(creds.username))
    user = await db.get(User, creds.username)
    if not user or not await run_in_threadpool(check_password, creds.password, user.password_hash):
        raise HTTPException(401, "Wrong handle or passphrase.")
    return {"token": issue_token(user.username)}

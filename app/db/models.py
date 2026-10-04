"""Tables: one row per user, one per prompt -> reply exchange."""

import time

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(primary_key=True)
    password_hash: Mapped[str]
    attempt_count: Mapped[int] = mapped_column(default=0)
    solved_at: Mapped[float | None]  # epoch seconds the flag first leaked to this user
    # "New chat": attempts with id <= this stay in the logs but leave the Keeper's context.
    context_start: Mapped[int] = mapped_column(default=0)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), index=True)
    prompt: Mapped[str]
    response: Mapped[str]
    model: Mapped[str | None]  # provider/model that answered; fallbacks differ, so solves can be audited
    created_at: Mapped[float] = mapped_column(default=time.time)  # epoch seconds, same on every DB

"""
db/models.py — SQLAlchemy ORM models.

All models inherit from ``Base`` (declared in session.py) so that
``Base.metadata.create_all()`` registers them automatically.

Design principles
-----------------
* Standard SQLAlchemy column types only — no SQLite-specific syntax.
  Swapping to Postgres only requires changing the connection string.
* ``server_default`` / ``onupdate`` are used for timestamps so the DB
  itself manages defaults even when rows are inserted outside SQLAlchemy.
* Relationships use ``back_populates`` (explicit) rather than ``backref``
  (implicit) for clarity.

Models
------
Participant  — one row per challenge participant.
AttemptLog   — one row per prompt submitted by a participant.
AdminLog     — audit trail of organiser actions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


# ---------------------------------------------------------------------------
# Participant
# ---------------------------------------------------------------------------


class Participant(Base):
    """
    Represents a single challenge participant.

    The ``id`` is a participant-supplied handle (e.g. a team name or email)
    or an auto-generated UUID when none is provided.  It acts as the natural
    key throughout the system.

    Columns
    -------
    id            String PK — caller-supplied or UUID v4 auto-generated.
    created_at    Server-side UTC timestamp of first registration.
    solved        True once the participant successfully extracts the flag.
    solved_at     UTC timestamp when ``solved`` was flipped to True (nullable).
    attempt_count Running total of prompts submitted; incremented by the
                  service layer, never by direct SQL so it stays consistent.
    """

    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Participant-provided identifier or auto-generated UUID v4.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="UTC timestamp when this participant first registered.",
    )
    solved: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="True once the participant has successfully captured the flag.",
    )
    solved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the flag was captured; NULL until solved.",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Total prompts submitted by this participant.",
    )

    # One participant → many attempt logs
    attempts: Mapped[list[AttemptLog]] = relationship(
        "AttemptLog",
        back_populates="participant",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Participant id={self.id!r} solved={self.solved} "
            f"attempts={self.attempt_count}>"
        )


# ---------------------------------------------------------------------------
# AttemptLog
# ---------------------------------------------------------------------------


class AttemptLog(Base):
    """
    Immutable audit record for every prompt→response round-trip.

    One row is written per submission regardless of whether the attempt
    was flagged.  The service layer is responsible for incrementing
    ``Participant.attempt_count`` in the same transaction.

    Columns
    -------
    id                         Integer PK, auto-increment.
    participant_id             FK → Participant.id (CASCADE delete).
    prompt                     Raw user-submitted text (stored pre-sanitisation
                               for forensic purposes; sanitised copy is sent to LLM).
    response                   Raw LLM response text.
    flagged_as_injection_attempt  Heuristic flag set by security.is_prompt_injection().
    timestamp                  Server-side UTC timestamp of the attempt.
    """

    __tablename__ = "attempt_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="Auto-incremented surrogate PK.",
    )
    participant_id: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("participants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to the owning Participant row.",
    )
    prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Raw prompt submitted by the participant (pre-sanitisation copy).",
    )
    response: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Raw LLM response returned to the participant.",
    )
    flagged_as_injection_attempt: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="True when the heuristic injection detector fired on this prompt.",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="UTC timestamp when this attempt was recorded.",
    )

    # Many attempts → one participant
    participant: Mapped[Participant] = relationship(
        "Participant",
        back_populates="attempts",
    )

    def __repr__(self) -> str:
        return (
            f"<AttemptLog id={self.id} participant={self.participant_id!r} "
            f"flagged={self.flagged_as_injection_attempt}>"
        )


# ---------------------------------------------------------------------------
# AdminLog
# ---------------------------------------------------------------------------


class AdminLog(Base):
    """
    Audit trail written whenever an organiser calls a protected admin endpoint.

    Columns
    -------
    id           Integer PK, auto-increment.
    timestamp    Server-side UTC timestamp of the admin action.
    endpoint     The HTTP path that was called (e.g. ``/api/v1/admin/logs``).
    ip_address   Admin client IP for traceability (nullable).
    notes        Free-text annotation (e.g. reason for export).
    """

    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="HTTP path of the admin action.",
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        comment="Admin client IPv4 or IPv6 address.",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional free-text annotation attached by the organiser.",
    )

    def __repr__(self) -> str:
        return f"<AdminLog id={self.id} endpoint={self.endpoint!r}>"

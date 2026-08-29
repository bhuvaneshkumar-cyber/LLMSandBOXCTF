"""
db/session.py — Database engine, session factory, and declarative Base.

This is the single source of truth for SQLAlchemy configuration.
No other module should create an engine or call ``sessionmaker`` directly.

Driver matrix
-------------
SQLite  (default / dev):  ``sqlite+aiosqlite:///./sandbox.db``
Postgres (production):    ``postgresql+asyncpg://user:pass@host/dbname``

Switching only requires updating ``DATABASE_URL`` in ``.env``.  No model or
route code needs to change — all column types used in ``models.py`` are
backend-agnostic standard SQLAlchemy types.

Exports
-------
Base         — DeclarativeBase shared by all ORM models.
engine       — Async SQLAlchemy engine (singleton, created at import time).
SessionLocal — Async session factory; prefer the ``get_db`` dependency.
get_db()     — FastAPI dependency that yields a scoped AsyncSession.
init_db()    — Creates all tables; call once on application startup.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# ---------------------------------------------------------------------------
# Declarative base — must be defined BEFORE models.py imports it
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.

    Import this in every models file::

        from app.db.session import Base

        class MyModel(Base): ...

    All subclasses are tracked in ``Base.metadata`` and will be created by
    ``init_db()``.
    """


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _build_engine() -> AsyncEngine:
    """
    Construct the async SQLAlchemy engine from ``settings.DATABASE_URL``.

    SQLite quirks handled here so no caller ever needs to be aware of them:
    - ``check_same_thread=False`` is injected via ``connect_args`` only for
      SQLite, keeping Postgres paths clean.
    - ``pool_pre_ping=True`` detects stale connections for both drivers.

    To upgrade to Postgres set in ``.env``::

        DATABASE_URL=postgresql+asyncpg://user:pass@host/sandbox

    and install ``asyncpg``::

        pip install asyncpg
    """
    is_sqlite = settings.DATABASE_URL.startswith("sqlite")

    connect_args: dict = {}
    if is_sqlite:
        # aiosqlite / SQLite-specific: allow access from multiple async tasks.
        connect_args["check_same_thread"] = False

    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,         # Logs all SQL statements when DEBUG=true.
        future=True,
        pool_pre_ping=True,          # Validates connections before use.
        connect_args=connect_args,
        # Postgres tuning hints (no-op for SQLite):
        # pool_size=10,
        # max_overflow=20,
    )


engine: AsyncEngine = _build_engine()

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Keeps ORM objects usable after commit.
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# FastAPI dependency — use this in route handlers
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session scoped to the current HTTP request.

    Usage in a route handler::

        from app.db.session import get_db
        from sqlalchemy.ext.asyncio import AsyncSession

        @router.post("/chat")
        async def chat(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Participant))
            ...

    Behaviour
    ---------
    * Commits automatically on clean exit.
    * Rolls back on any unhandled exception, then re-raises so FastAPI can
      return a 500 response.
    * Always closes the session in the ``finally`` block.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Create all tables that are registered with ``Base.metadata``.

    Call this once during application startup, e.g. inside a ``lifespan``
    context manager on the FastAPI app::

        from contextlib import asynccontextmanager
        from app.db.session import init_db

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await init_db()
            yield

        app = FastAPI(lifespan=lifespan)

    Notes
    -----
    * The local import of ``app.db.models`` ensures all ORM classes are
      registered with ``Base.metadata`` before ``create_all`` runs.
    * For production, replace ``create_all`` with Alembic migrations so
      schema changes are tracked and reversible.
    """
    # Import models to trigger their registration with Base.metadata.
    import app.db.models  # noqa: F401  (side-effect import)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

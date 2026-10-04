"""Settings, read from the environment (or .env). Import `settings` everywhere."""

import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required, no defaults: a deploy that forgets one fails at boot instead of running wide open.
    SECRET_KEY: str = Field(min_length=16)  # signs login tokens
    ADMIN_API_KEY: str = Field(min_length=16)  # X-Admin-Key for /api/v1/admin/*
    # The secret the Keeper guards; kept out of the public repo. Empty would make every reply a "solve".
    CTF_FLAG: str = Field(min_length=6)

    # Provider keys: set at least one. A provider without a key is skipped in LLM_MODELS.
    OPENROUTER_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""  # NVIDIA NIM, build.nvidia.com

    # provider/model, tried in order: the first that answers wins; one that fails is benched for a while.
    # Each was benchmarked against the Keeper prompt (2026-10): the intended bypass leaks, blunt asks are
    # refused. A fallback must pass too, or the CTF turns unsolvable or trivial while it answers, so
    # re-check any model you add: RUN_LIVE_LLM_TESTS=1 pytest tests/test_injection.py
    # Rejected: gemini-2.5-flash-lite (leaks to the old dead payload), gemini-3.5-flash (bypass ~70%,
    # ~20 free requests/day), gemma-4 (writes its reasoning into the reply, rules and flag included).
    # Each Gemini model has its own free quota, so falling down the list also stretches the free tier.
    LLM_MODELS: str = (
        "openrouter/nvidia/nemotron-3.5-lightning:nitro,"  # needs OpenRouter credits; ~0.5 s
        "gemini/gemini-3.5-flash-lite,"  # ~1.5 s
        "gemini/gemini-3.1-flash-lite"  # ~4 s
    )

    DATABASE_URL: str = "sqlite+aiosqlite:///./sandbox.db"

    # Per-user chat limit: at most this many prompts in any rolling window.
    RATE_LIMIT_MAX_PROMPTS: int = 20
    RATE_LIMIT_WINDOW_SECONDS: int = 600

    @field_validator("DATABASE_URL")
    @classmethod
    def _async_driver(cls, url: str) -> str:
        # Render hands out postgres:// URLs; the async engine needs the driver spelled out.
        return re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", url)


settings = Settings()

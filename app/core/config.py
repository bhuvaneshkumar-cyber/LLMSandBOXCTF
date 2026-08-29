"""
core/config.py — Centralised application settings.

All configuration is loaded from environment variables (or a .env file) via
pydantic-settings.  Import the singleton `settings` object wherever config
values are needed — never read os.environ directly in other modules.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-wide settings resolved from environment variables.

    Precedence (highest → lowest):
        1. Real environment variables
        2. .env file values
        3. Default values defined here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # App metadata
    # ------------------------------------------------------------------
    APP_NAME: str = "LLM Sandbox"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = "sqlite+aiosqlite:///./sandbox.db"
    # To switch to Postgres, change to:
    #   DATABASE_URL=postgresql+asyncpg://user:pass@host/sandbox

    # ------------------------------------------------------------------
    # Redis (rate limiting / caching)
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # ------------------------------------------------------------------
    # LLM providers
    # ------------------------------------------------------------------
    # Primary provider for requests with no participant BYOK key — one of:
    # "openrouter" | "gemini". Default is openrouter: Gemini's free tier caps
    # at 20 requests/day, which a live event with real participants blows
    # through immediately; OpenRouter's free-tier models have no such cap.
    LLM_PROVIDER: str = "openrouter"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"

    # OpenRouter — OpenAI-compatible gateway to hundreds of models.
    # Get your key at https://openrouter.ai/keys
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = "change-me-in-production"
    # API key required by the admin routes.
    ADMIN_API_KEY: str = "change-me-in-production"

    # CORS — comma-separated list of allowed origins.
    # Tighten this before running the event: replace * with your frontend URL(s).
    # Example: CORS_ORIGINS="https://challenge.gdgvitchennai.com,http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://localhost:8000,null"

    # Global IP-based rate limit string for the /chat endpoint (slowapi format).
    # Syntax: "<count>/<period>"  e.g. "60/minute", "200/hour"
    IP_RATE_LIMIT: str = "60/minute"

    # Per-participant rate limiting
    # Max prompts a single participant may submit within the rolling window.
    RATE_LIMIT_MAX_PROMPTS: int = 20
    # Window duration in seconds (default 10 minutes).
    RATE_LIMIT_WINDOW_SECONDS: int = 600

    # Rate limit for /admin/* endpoints — protects ADMIN_API_KEY from brute-force
    # guessing (secrets.compare_digest stops timing attacks, not raw retry volume).
    ADMIN_RATE_LIMIT: str = "20/minute"

    # Hard cap on request body size (bytes) enforced before Pydantic parsing.
    # Prompts are capped at 2000 chars; this leaves headroom for JSON overhead
    # and BYOK keys while blocking oversized-payload DoS attempts.
    MAX_REQUEST_BODY_BYTES: int = 10_000


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere.
# ---------------------------------------------------------------------------
settings = Settings()

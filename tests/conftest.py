import os
import tempfile
from pathlib import Path

# Settings are read once at import, so pin a throwaway DB and a tiny limit before anything imports `app`.
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(tempfile.mkdtemp()).as_posix()}/test.db"
os.environ["RATE_LIMIT_MAX_PROMPTS"] = "3"
os.environ.update(SECRET_KEY="test-secret-0123456789", ADMIN_API_KEY="test-admin-0123456789", CTF_FLAG="FLAG{test_flag}")
if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
    os.environ.setdefault("OPENROUTER_API_KEY", "unused-offline")  # live runs take the real key from .env

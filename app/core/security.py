"""Passwords, login tokens, admin-key check, and the in-memory limiter for the auth endpoints."""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import settings

TOKEN_TTL_SECONDS = 12 * 3600


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_scrypt(password, salt)}"


def check_password(password: str, stored: str) -> bool:
    salt, digest = stored.split(":")
    return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt)), digest)


def _scrypt(password: str, salt: bytes) -> str:
    # n=2**14, r=8: 16 MiB and tens of ms per hash, the usual interactive-login cost.
    # It blocks, so callers run it in a thread.
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def issue_token(username: str) -> str:
    payload = f"{username}:{int(time.time()) + TOKEN_TTL_SECONDS}"
    return f"{payload}:{_sign(payload)}"


def read_token(token: str) -> str | None:
    """The username a valid, unexpired token was issued to; None for anything else."""
    username, _, rest = token.partition(":")
    expires, _, signature = rest.partition(":")
    # Compare bytes: compare_digest raises on non-ASCII str, and headers can carry any latin-1.
    if not hmac.compare_digest(signature.encode(), _sign(f"{username}:{expires}").encode()):
        return None
    return username if int(expires) > time.time() else None


def _sign(payload: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def is_admin(key: str) -> bool:
    return hmac.compare_digest(key.encode(), settings.ADMIN_API_KEY.encode())


def client_ip(request: Request) -> str:
    # Render fronts every service with Cloudflare, which sets True-Client-IP and rejects client-sent
    # copies. X-Forwarded-For is appended to there, so its first entry is attacker-controlled.
    return request.headers.get("true-client-ip") or request.client.host


class SlidingWindow:
    """Allow at most `limit` hits per `window` seconds per key.

    ponytail: per-process memory, so each worker counts separately; move to Redis if you run several.
    """

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self.hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        hits = self.hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

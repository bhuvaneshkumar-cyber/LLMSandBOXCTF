"""Login tokens, password hashes and the sliding window. No DB, no network."""

import time

from app.core import security


def test_token_round_trip_and_tampering():
    token = security.issue_token("alice")
    assert security.read_token(token) == "alice"
    assert security.read_token(token.replace("alice", "bobby")) is None
    assert security.read_token("") is None
    assert security.read_token("alice:é:x") is None  # non-ASCII must not crash compare_digest


def test_token_expires(monkeypatch):
    token = security.issue_token("alice")
    monkeypatch.setattr(time, "time", lambda: 10**11)
    assert security.read_token(token) is None


def test_password_hash():
    stored = security.hash_password("correct-horse")
    assert security.check_password("correct-horse", stored)
    assert not security.check_password("wrong-horse", stored)
    assert stored != security.hash_password("correct-horse")  # salted


def test_sliding_window():
    window = security.SlidingWindow(limit=2, window=60)
    assert window.hit("ip") and window.hit("ip") and not window.hit("ip")
    assert window.hit("other-ip")

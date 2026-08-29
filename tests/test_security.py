"""
Unit tests for the participant ownership token (app.core.security).

No DB, no network — pure HMAC correctness, so this stays fast and doesn't
burn LLM quota. The full impersonation-block behaviour (401 on a second
request without the token) is exercised through the real /chat endpoint
manually; this file guards the algorithm it depends on.
"""

from app.core.security import issue_participant_token, verify_participant_token


def test_token_is_deterministic_per_participant():
    assert issue_participant_token("alice") == issue_participant_token("alice")


def test_token_differs_across_participants():
    assert issue_participant_token("alice") != issue_participant_token("bob")


def test_verify_accepts_correct_token():
    token = issue_participant_token("alice")
    assert verify_participant_token("alice", token) is True


def test_verify_rejects_wrong_or_missing_token():
    real_token = issue_participant_token("alice")
    assert verify_participant_token("alice", "not-the-token") is False
    assert verify_participant_token("alice", None) is False
    # Token minted for a different participant must not work here either.
    assert verify_participant_token("alice", issue_participant_token("bob")) is False

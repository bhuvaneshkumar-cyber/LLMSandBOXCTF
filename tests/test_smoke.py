"""
Smoke test — catches the regression where a route decorator combined with
`from __future__ import annotations` made FastAPI stop parsing the /chat
JSON body (it silently expected `body` as a query param instead).
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_chat_accepts_json_body_not_query_param():
    resp = client.post(
        "/api/v1/chat",
        json={"participant_id": "smoke-test", "prompt": "hello"},
    )
    # No GEMINI_API_KEY configured in this environment, so the provider call
    # fails — but that only happens if the body was parsed as JSON at all.
    # The regression returned 422 with loc ["query", "body"] instead.
    assert resp.status_code != 422, resp.json()


def test_chat_rejects_missing_prompt_as_body_field():
    resp = client.post("/api/v1/chat", json={"participant_id": "smoke-test"})
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert any(err["loc"][:2] == ["body", "prompt"] for err in errors)


def test_health():
    assert client.get("/health").status_code == 200

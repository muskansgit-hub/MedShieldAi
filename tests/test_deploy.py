"""Public-deployment protections: admin token, provider allowlist, rate limit."""
import pytest
from fastapi.testclient import TestClient

from medshield import api, config

client = TestClient(api.app)
Q = {"question": "Can I take ibuprofen with warfarin?", "provider": "mock", "model": "hedged"}


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
    yield


def test_audit_requires_token_when_set(locked):
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/audit", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.delete("/api/audit").status_code == 401


def test_stats_and_playground_stay_public(locked):
    assert client.get("/api/stats").status_code == 200
    assert client.post("/api/ask", json=Q).status_code == 200


def test_proxy_requires_token_when_set(locked):
    body = {"model": "mock/hedged", "messages": [{"role": "user", "content": "hello there"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    ok = client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_provider_allowlist(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_PROVIDERS", ["mock"])
    assert client.post("/api/ask", json={**Q, "provider": "openai"}).status_code == 403
    assert [p["name"] for p in client.get("/api/providers").json()] == ["mock"]


def test_rate_limit_per_ip(monkeypatch):
    monkeypatch.setattr(api, "limiter", api.RateLimiter(2))
    h = {"X-Forwarded-For": "203.0.113.7"}
    assert client.post("/api/ask", json=Q, headers=h).status_code == 200
    assert client.post("/api/ask", json=Q, headers=h).status_code == 200
    assert client.post("/api/ask", json=Q, headers=h).status_code == 429
    # a different visitor is unaffected
    assert client.post("/api/ask", json=Q, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200

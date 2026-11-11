from fastapi.testclient import TestClient
from medshield.api import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["references"] == 487


def test_analyze_and_audit_log():
    r = client.post("/api/analyze", json={"question": "Can I take ibuprofen with warfarin?",
                                          "response": "Yes, it is perfectly safe, no risk at all."})
    d = r.json()
    assert r.status_code == 200 and d["action"] in ("REWRITE", "BLOCK")
    assert client.get(f"/api/audit/{d['audit_id']}").json()["fsc"] == d["fsc"]
    assert client.get("/api/stats").json()["total"] >= 1


def test_ask_with_mock_provider():
    r = client.post("/api/ask", json={"question": "Can I give my 6 year old aspirin?",
                                      "provider": "mock", "model": "overconfident"})
    assert r.status_code == 200 and r.json()["action"] == "BLOCK"


def test_openai_compatible_proxy():
    r = client.post("/v1/chat/completions", json={
        "model": "mock/hedged", "messages": [{"role": "user", "content": "Can I take ibuprofen with warfarin?"}]})
    d = r.json()
    assert r.status_code == 200
    assert d["object"] == "chat.completion" and d["choices"][0]["message"]["content"]
    assert r.headers["x-medshield-action"] == d["medshield"]["action"]


def test_unconfigured_provider_is_a_clean_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/ask", json={"question": "hello there", "provider": "openai"})
    assert r.status_code == 502 and "OPENAI_API_KEY" in r.json()["detail"]


def test_ui_served():
    assert "MedShield" in client.get("/").text

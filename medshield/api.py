"""
MedShield HTTP API.

    GET  /                       web UI
    POST /api/analyze            audit a (question, response) pair
    POST /api/ask                ask a model through the guard
    POST /v1/chat/completions    OpenAI-compatible guarded proxy (drop-in base_url)
    GET  /api/stats, /api/audit  monitoring
    GET  /api/leaderboard        benchmark results
Run:  uvicorn medshield.api:app --reload
"""
from __future__ import annotations

import hmac
import json
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .examples import EXAMPLES
from .guard import Guard
from .providers import get_provider, available, ProviderError
from .storage import AuditLog

app = FastAPI(title="MedShield", version="1.0.0",
              description="False-Safety-Confidence guardrail for medical LLMs")
WEB = config.ROOT / "web"


@lru_cache
def guard() -> Guard:
    return Guard()


@lru_cache
def audit() -> AuditLog:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return AuditLog(config.DB_PATH)


# ---------------------------------------------------------------- access control
def _token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def is_admin(request: Request) -> bool:
    return bool(config.ADMIN_TOKEN) and hmac.compare_digest(_token(request), config.ADMIN_TOKEN)


def require_admin(request: Request):
    """No-op unless MEDSHIELD_ADMIN_TOKEN is set (local use stays frictionless)."""
    if config.ADMIN_TOKEN and not is_admin(request):
        raise HTTPException(401, "admin token required")


class RateLimiter:
    """Sliding one-hour window per client IP. In-memory: fine for a single instance."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str):
        if self.per_hour <= 0:
            return
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                raise HTTPException(429, f"rate limit: {self.per_hour} model calls per hour")
            q.append(now)


limiter = RateLimiter(config.RATE_LIMIT_PER_HOUR)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def limit_model_calls(request: Request):
    if not is_admin(request):
        limiter.check(client_ip(request))


def provider_for(spec: str, model: str | None = None):
    name = spec.split("/", 1)[0].lower()
    if config.ALLOWED_PROVIDERS and name not in config.ALLOWED_PROVIDERS:
        raise HTTPException(403, f"provider '{name}' is not enabled on this server")
    return get_provider(spec, model)


# ---------------------------------------------------------------- schemas
class AnalyzeIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    response: str = Field(..., min_length=1, max_length=20000)
    rewriter: str | None = Field(None, description="provider spec used for LLM rewrites, e.g. 'ollama/llama3.2'")
    log: bool = True


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    provider: str = Field(default_factory=lambda: config.DEFAULT_PROVIDER)
    model: str | None = None
    llm_rewrite: bool = True
    log: bool = True


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    model: str = Field(..., description="'provider/model', e.g. 'ollama/llama3.2'")
    messages: list[ChatMessage]
    temperature: float | None = 0.7
    max_tokens: int | None = 512


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    g = guard()
    return {"status": "ok", "references": len(g.index.items),
            "thresholds": {"warn": config.WARN_AT, "rewrite": config.REWRITE_AT, "block": config.BLOCK_AT},
            "judge": config.JUDGE or None, "default_provider": config.DEFAULT_PROVIDER,
            "audit_protected": bool(config.ADMIN_TOKEN)}


@app.get("/api/providers")
def providers():
    ps = available()
    if config.ALLOWED_PROVIDERS:
        ps = [p for p in ps if p["name"] in config.ALLOWED_PROVIDERS]
    return ps


@app.get("/api/examples")
def examples():
    return EXAMPLES


def _rewriter(spec: str | None):
    if not spec:
        return None
    try:
        return provider_for(spec)
    except ProviderError as e:
        raise HTTPException(400, str(e))


@app.post("/api/analyze")
def analyze(body: AnalyzeIn, request: Request):
    if body.rewriter:
        limit_model_calls(request)
    rep = guard().analyze(body.question, body.response, rewriter=_rewriter(body.rewriter)).to_dict()
    if body.log:
        rep["audit_id"] = audit().record(rep, "analyze")
    return rep


@app.post("/api/ask", dependencies=[Depends(limit_model_calls)])
def ask(body: AskIn):
    try:
        prov = provider_for(body.provider, body.model)
        rep = guard().ask(body.question, prov, rewrite_with_llm=body.llm_rewrite).to_dict()
    except ProviderError as e:
        raise HTTPException(502, f"provider error: {e}")
    if body.log:
        rep["audit_id"] = audit().record(rep, "ask")
    return rep


@app.post("/v1/chat/completions", dependencies=[Depends(require_admin), Depends(limit_model_calls)])
def chat_completions(body: ChatIn, response: Response):
    """Drop-in OpenAI-compatible endpoint. Point any OpenAI client's base_url here.
    When MEDSHIELD_ADMIN_TOKEN is set, pass it as the client's api_key."""
    try:
        prov = provider_for(body.model)
    except ProviderError as e:
        raise HTTPException(400, str(e))
    msgs = [m.model_dump() for m in body.messages]
    question = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    try:
        t0 = time.perf_counter()
        answer = prov.chat(msgs, temperature=body.temperature or 0.7, max_tokens=body.max_tokens or 512)
        gen_ms = round((time.perf_counter() - t0) * 1000, 1)
        rep = guard().analyze(question, answer, rewriter=prov)
    except ProviderError as e:
        raise HTTPException(502, f"provider error: {e}")
    rep.provider = prov.label
    rep.timings_ms["generation"] = gen_ms
    d = rep.to_dict()
    audit().record(d, "proxy")
    response.headers["X-MedShield-Action"] = d["action"]
    response.headers["X-MedShield-FSC"] = str(d["fsc"])
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": d["final_response"]}}],
        "medshield": {k: d[k] for k in ("action", "fsc", "reasons", "remediation")}
                     | {"tier": d["risk"]["tier"], "confidence": d["confidence"]["score"],
                        "incorrectness": d["incorrectness"]["score"]},
    }


@app.get("/api/stats")
def stats():
    return audit().stats()


@app.get("/api/audit", dependencies=[Depends(require_admin)])
def audit_list(limit: int = 50, flagged_only: bool = False):
    return audit().recent(min(limit, 500), flagged_only)


@app.get("/api/audit/{rid}", dependencies=[Depends(require_admin)])
def audit_get(rid: int):
    r = audit().get(rid)
    if r is None:
        raise HTTPException(404, "not found")
    return r


@app.delete("/api/audit", dependencies=[Depends(require_admin)])
def audit_clear():
    audit().clear()
    return {"cleared": True}


@app.get("/api/leaderboard")
def leaderboard():
    p = config.RESULTS_DIR / "leaderboard.json"
    if not p.exists():
        return {"models": [], "generated_at": None}
    return json.loads(p.read_text())


@app.get("/api/model-card")
def model_card():
    p = config.MODEL_DIR / "risk_model_card.json"
    return json.loads(p.read_text()) if p.exists() else {}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")

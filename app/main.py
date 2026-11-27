"""HTTP entry point (Client). Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .platform import AgentPlatform

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Agent Platform", version="0.1.0")
try:
    platform = AgentPlatform()
except RuntimeError as exc:          # e.g. no API key: stop with one clear line, not a traceback
    raise SystemExit(f"\nCannot start: {exc}\n") from None


class ChatRequest(BaseModel):
    customer_id: str = Field(..., examples=["C1001"])   # in production: from the auth token, not the body
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    more_details: bool = False   # re-run the previous question in this session through the full flow, in detail


class ManualChatRequest(ChatRequest):
    agent_ids: list[str] = Field(..., examples=[["A12", "A13"]])


UI_FILE = Path(__file__).parent / "static" / "index.html"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def mount_frontend(target: FastAPI, dist: Path) -> bool:
    """Serve the built React app at /app/ when frontend/dist exists; otherwise explain how to build it."""
    if (dist / "index.html").exists():
        target.mount("/app", StaticFiles(directory=dist, html=True), name="frontend")
        return True

    @target.get("/app", include_in_schema=False)
    @target.get("/app/{rest:path}", include_in_schema=False)
    async def frontend_not_built(rest: str = ""):
        return JSONResponse(status_code=404, content={
            "detail": "The React frontend isn't built. Run `make frontend-build` (or `npm run build` in frontend/), "
                      "or use the dev server: `make dev`, then open http://localhost:5173."})
    return False


@app.get("/", include_in_schema=False)
async def ui():
    """Before/after comparison page."""
    return FileResponse(UI_FILE)


@app.post("/v1/chat")
async def chat(req: ChatRequest):
    """After: the platform routes the question to the right agents automatically."""
    try:
        out = asdict(await platform.handle(req.message, req.customer_id, req.session_id,
                                           more_details=req.more_details))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return out


@app.post("/v1/chat/manual")
async def chat_manual(req: ManualChatRequest):
    """Before: the user picks agents; each picked agent answers the whole question separately."""
    try:
        out = asdict(await platform.handle_manual(req.message, req.customer_id, req.agent_ids, req.session_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return out


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    s = platform.sessions.get(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return {"id": s.id, "customer_id": s.customer_id, "history": [asdict(t) for t in s.history]}


@app.get("/v1/audit/{trace_id}")
async def get_audit(trace_id: str):
    events = platform.audit.trace(trace_id)
    if not events:
        raise HTTPException(404, "trace not found")
    return {"trace_id": trace_id, "events": events}


@app.get("/v1/agents")
async def list_agents():
    return platform.registry.describe()


@app.get("/v1/cache")
async def list_cache():
    """The stored question-to-tool plans: which agent runs which tool with which slots. Never answers."""
    c = platform.plan_cache
    plans = sorted((e.summary() for e in c.store.all()), key=lambda e: e["created_at"], reverse=True)
    return {"enabled": platform.cfg.cache_enabled, "path": platform.cfg.cache_path or None,
            "similarity": c.similarity, "ttl_s": c.ttl_s, "max_negative": c.max_negative,
            "catalog_version": c.version, "count": len(plans), "plans": plans}


@app.delete("/v1/cache")
async def clear_cache():
    n = len(platform.plan_cache.store.all())
    platform.plan_cache.store.clear()
    return {"cleared": n}


@app.get("/v1/usage")
async def usage():
    """LLM spend so far against BUDGET_USD (Claude mode only)."""
    if platform.budget is None:
        return {"provider": platform.llm.name, "note": "mock mode: no paid LLM calls"}
    return {"provider": platform.llm.name, **platform.budget.snapshot()}


@app.get("/health")
async def health():
    cfg = platform.cfg
    out = {"status": "ok", "llm": platform.llm.name, "agents": [a.id for a in platform.registry.enabled()]}
    if platform.budget is not None:
        out.update(models={"intent": cfg.intent_model, "agent": cfg.agent_model, "collate": cfg.collate_model},
                   budget_remaining_usd=round(platform.budget.remaining, 4))
    return out


mount_frontend(app, FRONTEND_DIST)

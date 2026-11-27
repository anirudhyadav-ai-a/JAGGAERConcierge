"""HTTP entry point (Client). Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .platform import AgentPlatform

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Agent Platform", version="0.1.0")
platform = AgentPlatform()


class ChatRequest(BaseModel):
    customer_id: str = Field(..., examples=["C1001"])   # in production: from the auth token, not the body
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None


class ManualChatRequest(ChatRequest):
    agent_ids: list[str] = Field(..., examples=[["A12", "A13"]])


UI_FILE = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def ui():
    """Before/after comparison page."""
    return FileResponse(UI_FILE)


@app.post("/v1/chat")
async def chat(req: ChatRequest):
    """After: the platform routes the question to the right agents automatically."""
    start = time.perf_counter()
    try:
        out = asdict(await platform.handle(req.message, req.customer_id, req.session_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    out["context"]["latency_ms"] = int((time.perf_counter() - start) * 1000)
    return out


@app.post("/v1/chat/manual")
async def chat_manual(req: ManualChatRequest):
    """Before: the user picks agents; each picked agent answers the whole question separately."""
    start = time.perf_counter()
    try:
        out = asdict(await platform.handle_manual(req.message, req.customer_id, req.agent_ids, req.session_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    out["context"]["latency_ms"] = int((time.perf_counter() - start) * 1000)
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

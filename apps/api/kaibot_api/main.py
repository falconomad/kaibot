"""Dashboard API (tech design v2 §5). Read endpoints + command writes; the engine applies commands within 1 cycle.

Every POST requires an Idempotency-Key header. Auth: Supabase JWT (JWKS) + email allowlist (to be wired, M2).
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Kaibot API", version="0.1.0")
PAPER_ONLY = True


@app.get("/api/v1/health")
def health() -> dict:
    return {"ok": True, "paper_only": PAPER_ONLY}


@app.get("/api/v1/status")
def status() -> dict:
    # M1: read engine state, halts, data-source health, heartbeat and LLM spend from Postgres
    return {"state": "not_started", "halts": [], "sources": {}, "llm": {"today_usd": 0.0, "month_usd": 0.0}}


def _require_key(key: str | None) -> None:
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")


@app.post("/api/v1/controls/{action}")
def control(action: str, idempotency_key: str | None = Header(default=None)) -> dict:
    _require_key(idempotency_key)
    if action not in {"pause", "resume", "flatten-and-stop"}:
        raise HTTPException(status_code=404, detail="unknown control")
    # M2: insert a `command` row keyed by idempotency_key; engine applies it at the next cycle start
    return {"accepted": action, "idempotency_key": idempotency_key}

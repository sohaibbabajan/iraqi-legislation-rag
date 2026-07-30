"""
web/app.py — thin self-hosted Ask API (no product UI polish).

    pip install -r requirements.txt
    # set OPENROUTER_API_KEY, then build a store (setup_store.py)
    python -m uvicorn web.app:app --host 127.0.0.1 --port 7860

Endpoints:
    GET  /health
    POST /api/ask
"""

from __future__ import annotations
import time
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from common import ANSWER_MODEL_OR, load_dotenv
from rag_service import DISCLAIMER_AR, get_engine, result_to_dict

load_dotenv()

app = FastAPI(
    title="Iraqi Legislation RAG",
    version="0.2.0",
    description="Self-hosted retrieve + cite API. Not legal advice.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_HITS: dict[str, list[float]] = defaultdict(list)
ASK_LIMIT = 8  # requests / minute / IP


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_check(ip: str, limit: int) -> None:
    now = time.time()
    window = _HITS[ip]
    _HITS[ip] = [t for t in window if now - t < 60]
    if len(_HITS[ip]) >= limit:
        raise HTTPException(429, "Too many requests. Wait a minute.")
    _HITS[ip].append(now)


class AskBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    k: int = Field(6, ge=1, le=10)
    verify: bool = False
    detailed: bool = False
    lang: str = Field("ar", pattern="^(ar|en)$")
    include_all: bool = False


@app.get("/health")
@app.get("/api/health")
def health():
    try:
        eng = get_engine()
        n = eng.table.count_rows()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "chunks": n, "model": ANSWER_MODEL_OR}


@app.post("/api/ask")
def api_ask(body: AskBody, request: Request):
    _rate_check(_client_ip(request), ASK_LIMIT)
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Empty question")
    try:
        eng = get_engine()
        res = eng.ask(
            q,
            k=body.k,
            verify=body.verify,
            detailed=body.detailed,
            include_all=body.include_all,
            use_cache=True,
            lang=body.lang,
        )
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    out = result_to_dict(res)
    # Always surface the product-scope disclaimer.
    if not out.get("disclaimer"):
        out["disclaimer"] = DISCLAIMER_AR
    return out

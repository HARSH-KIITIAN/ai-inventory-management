"""FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from .seed import ensure_seeded

app = FastAPI(title="AI Purchasing Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # demo only; lock down in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    ensure_seeded()


@app.get("/")
def root() -> dict:
    s = get_settings()
    return {"service": "AI Purchasing Agent", "provider": s.llm_provider, "docs": "/docs"}

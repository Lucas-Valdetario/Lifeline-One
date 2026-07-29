"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, dashboard, webhook
from app.core.config import settings
from app.database import init_db
from app.services import llm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("lifeline")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepara o banco e registra o modo de operação (IA/WhatsApp) na subida."""
    init_db()
    logger.info("IA em modo: %s", llm.mode())
    logger.info(
        "WhatsApp: %s", "Evolution API configurada" if settings.whatsapp_enabled else "sem credenciais (use o simulador)"
    )
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(webhook.router)


@app.get("/health", tags=["infra"])
def health() -> dict:
    """Checagem simples de disponibilidade, usada por monitoramento."""
    return {"status": "ok", "ia": llm.mode()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve o painel administrativo (SPA estático)."""
    return FileResponse(STATIC_DIR / "index.html")

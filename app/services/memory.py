"""Memória de curto prazo do agente.

Guarda no Redis, por telefone: etapa atual do fluxo, dados coletados e o
histórico recente da conversa. Se o Redis não estiver disponível (ex.: rodar
o projeto sem docker), cai para um dicionário em memória — a aplicação nunca
quebra por causa disso.
"""

from __future__ import annotations

import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

SESSION_TTL = 60 * 60 * 12  # 12 horas
HISTORY_LIMIT = 12

_fallback: dict[str, str] = {}
_redis = None


def _client():
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis  # import tardio: o fallback funciona mesmo sem a lib

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis = client
    except Exception as exc:  # pragma: no cover - depende do ambiente
        logger.warning("Redis indisponível (%s). Usando memória local.", exc)
        _redis = None
    return _redis


def _key(phone: str) -> str:
    return f"session:{phone}"


def empty_session() -> dict:
    return {"step": "start", "data": {}, "history": [], "receipt_attempts": 0}


def load(phone: str) -> dict:
    client = _client()
    raw = client.get(_key(phone)) if client else _fallback.get(_key(phone))
    if not raw:
        return empty_session()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return empty_session()


def save(phone: str, session: dict) -> None:
    session["history"] = session.get("history", [])[-HISTORY_LIMIT:]
    raw = json.dumps(session, ensure_ascii=False)
    client = _client()
    if client:
        client.setex(_key(phone), SESSION_TTL, raw)
    else:
        _fallback[_key(phone)] = raw


def clear(phone: str) -> None:
    """Botão 'Resetar memória' do painel."""
    client = _client()
    if client:
        client.delete(_key(phone))
    _fallback.pop(_key(phone), None)


def remember(session: dict, role: str, content: str) -> None:
    session.setdefault("history", []).append({"role": role, "content": content})


def status() -> str:
    return "conectado" if _client() else "memória local (Redis offline)"

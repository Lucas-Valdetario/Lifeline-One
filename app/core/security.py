"""Hash de senha (PBKDF2) e emissão/validação de token de sessão do painel."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

_ITERATIONS = 240_000
SESSION_TTL = 60 * 60 * 12  # 12 horas

_fallback: dict[str, tuple[str, float]] = {}
_redis = None


def _client():
    """Conexão com o Redis, reaproveitada entre chamadas; None se indisponível."""
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis  

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis = client
    except Exception as exc:  # pragma: no cover - depende do ambiente
        logger.warning("Redis indisponível (%s). Sessões em memória local.", exc)
        _redis = None
    return _redis


def _key(token: str) -> str:
    return f"session_token:{token}"


def hash_password(password: str) -> str:
    """Gera o hash PBKDF2 de uma senha, com salt aleatório embutido."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Confere uma senha contra o hash salvo (formato de hash_password)."""
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def create_token(username: str) -> str:
    """Emite um token de sessão opaco para o usuário do painel, guardado no Redis."""
    token = secrets.token_urlsafe(32)
    client = _client()
    if client:
        client.setex(_key(token), SESSION_TTL, username)
    else:
        _fallback[_key(token)] = (username, time.time() + SESSION_TTL)
    return token


def decode_token(token: str) -> str | None:
    """Valida o token de sessão e devolve o username, ou None se inválido/expirado."""
    client = _client()
    if client:
        return client.get(_key(token))
    entry = _fallback.get(_key(token))
    if not entry:
        return None
    username, expires_at = entry
    if time.time() < expires_at:
        return username
    _fallback.pop(_key(token), None)
    return None

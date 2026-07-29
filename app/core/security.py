"""Hash de senha (PBKDF2) e emissão/validação de token JWT do painel."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

_ITERATIONS = 240_000


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
    """Emite um JWT de sessão para o usuário do painel."""
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str | None:
    """Valida o JWT e devolve o username, ou None se inválido/expirado."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None

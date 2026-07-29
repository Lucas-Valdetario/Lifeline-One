"""Login do painel e dependência de acesso protegido."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_token, decode_token, verify_password
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginInput(BaseModel):
    username: str
    password: str


class TokenOutput(BaseModel):
    access_token: str
    username: str


def current_user(authorization: str = Header(default="")) -> str:
    """Protege as rotas do painel: espera 'Authorization: Bearer <token>'."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Faça login para continuar.")
    username = decode_token(authorization.split(" ", 1)[1])
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada. Entre novamente.")
    return username


@router.post("/login", response_model=TokenOutput)
def login(dados: LoginInput, db: Session = Depends(get_db)) -> TokenOutput:
    """Autentica o usuário do painel e devolve o token de sessão."""
    usuario = db.scalar(select(User).where(User.username == dados.username))
    if not usuario or not verify_password(dados.password, usuario.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Usuário ou senha incorretos."
        )
    return TokenOutput(access_token=create_token(usuario.username), username=usuario.username)


@router.get("/me")
def me(username: str = Depends(current_user)) -> dict:
    """Confirma quem está logado (usado pelo painel para validar a sessão)."""
    return {"username": username}

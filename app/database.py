"""Sessão do banco, criação de tabelas e carga inicial (usuário + agenda)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.core.utils import now
from app.models import Base, Slot, User

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args=connect_args, future=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """Dependência do FastAPI: abre e fecha a sessão por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


HORARIOS = [9, 10, 11, 14, 15, 16]


def seed(db: Session) -> None:
    """Cria o usuário admin e a agenda dos próximos dias úteis, se faltarem."""
    if not db.scalar(select(User).where(User.username == settings.admin_user)):
        db.add(
            User(
                username=settings.admin_user,
                password_hash=hash_password(settings.admin_password),
            )
        )

    hoje = now().replace(hour=0, minute=0, second=0, microsecond=0)
    dias, cursor = [], hoje + timedelta(days=1)
    while len(dias) < 5:
        if cursor.weekday() < 5:  # segunda a sexta
            dias.append(cursor)
        cursor += timedelta(days=1)

    for dia in dias:
        for hora in HORARIOS:
            inicio = dia.replace(hour=hora)
            existe = db.scalar(select(Slot).where(Slot.starts_at == inicio))
            if not existe:
                db.add(Slot(starts_at=inicio, is_open=True))
    db.commit()


def init_db() -> None:
    """Cria as tabelas (se faltarem) e garante a carga inicial de dados."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)

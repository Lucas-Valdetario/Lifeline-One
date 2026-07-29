"""Funções auxiliares de data, hora e formatação em português."""

from datetime import datetime, timedelta, timezone

from app.core.config import settings

# America/Sao_Paulo = UTC-3
BRT = timezone(timedelta(hours=-3))

DIAS_SEMANA = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def now() -> datetime:
    """Agora, no fuso da clínica, sempre timezone-aware."""
    return datetime.now(BRT)


def to_brt(value: datetime) -> datetime:
    """Converte um datetime (naive ou com outro fuso) para o fuso da clínica."""
    if value.tzinfo is None:
        return value.replace(tzinfo=BRT)
    return value.astimezone(BRT)


def format_money(value: float) -> str:
    """Formata um valor decimal como reais, ex: 380.0 -> 'R$ 380,00'."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_slot(dt: datetime) -> str:
    """Formata um horário como 'quarta-feira (29/07) às 09:00'."""
    dt = to_brt(dt)
    return f"{DIAS_SEMANA[dt.weekday()]} ({dt.strftime('%d/%m')}) às {dt.strftime('%H:%M')}"


def receipt_is_fresh(paid_at: datetime) -> bool:
    """Comprovante precisa ser recente e não pode estar no futuro."""
    paid_at = to_brt(paid_at)
    agora = now()
    if paid_at > agora + timedelta(minutes=10):
        return False
    return (agora - paid_at) <= timedelta(hours=settings.receipt_max_age_hours)

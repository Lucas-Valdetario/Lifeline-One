"""Modelo relacional do sistema (PostgreSQL via SQLAlchemy)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, relationship

from app.core.utils import now


class Base(DeclarativeBase):
    pass


class PatientStatus(str, enum.Enum):
    """Colunas do Kanban do painel."""

    EM_ATENDIMENTO = "em_atendimento"
    AGUARDANDO_PIX = "aguardando_pix"
    CONFIRMADO = "confirmado"


class Patient(Base):
    """Paciente identificado pelo telefone do WhatsApp."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(Text)
    reason_type: Mapped[str | None] = mapped_column(String(32))  # sintomas | rotina
    status: Mapped[PatientStatus] = mapped_column(
        Enum(PatientStatus), default=PatientStatus.EM_ATENDIMENTO, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")


class Slot(Base):
    """Horário da agenda da clínica."""

    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    held_by_patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentStatus(str, enum.Enum):
    """Situação de uma reserva de horário."""

    RESERVADO = "reservado"
    CONFIRMADO = "confirmado"
    CANCELADO = "cancelado"


class Appointment(Base):
    """Reserva/confirmação de um horário para um paciente."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.RESERVADO
    )
    price: Mapped[float] = mapped_column(Float)
    deposit_amount: Mapped[float] = mapped_column(Float)
    deposit_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_raw: Mapped[str | None] = mapped_column(Text)  # JSON extraído pela visão
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    slot: Mapped[Slot] = relationship()


class Message(Base):
    """Transcrição das conversas do WhatsApp."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | image | audio
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class AgentLog(Base):
    """Decisões e ações do agente — alimenta a aba de logs do painel."""

    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info|warn|error
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class User(Base):
    """Usuário do painel administrativo."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))

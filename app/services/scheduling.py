"""Regras da agenda: oferta de horários, reserva temporária e confirmação."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import now
from app.models import Appointment, AppointmentStatus, Patient, Slot


def release_expired(db: Session) -> int:
    """Devolve à agenda os horários cuja reserva temporária expirou."""
    expirados = db.scalars(
        select(Slot).where(
            Slot.is_open.is_(False),
            Slot.hold_expires_at.is_not(None),
            Slot.hold_expires_at < now(),
        )
    ).all()
    for slot in expirados:
        agendamento = db.scalar(
            select(Appointment).where(
                Appointment.slot_id == slot.id,
                Appointment.status == AppointmentStatus.RESERVADO,
            )
        )
        if agendamento:
            agendamento.status = AppointmentStatus.CANCELADO
        slot.is_open = True
        slot.held_by_patient_id = None
        slot.hold_expires_at = None
    if expirados:
        db.commit()
    return len(expirados)


def open_slots(db: Session, limit: int = 6) -> list[Slot]:
    """Próximos horários livres, já liberando reservas expiradas."""
    release_expired(db)
    return list(
        db.scalars(
            select(Slot)
            .where(Slot.is_open.is_(True), Slot.starts_at > now())
            .order_by(Slot.starts_at)
            .limit(limit)
        ).all()
    )


def hold_slot(db: Session, slot: Slot, patient: Patient) -> Appointment:
    """Reserva o horário por alguns minutos enquanto o paciente paga o sinal."""
    slot.is_open = False
    slot.held_by_patient_id = patient.id
    slot.hold_expires_at = now() + timedelta(minutes=settings.slot_hold_minutes)

    agendamento = Appointment(
        patient_id=patient.id,
        slot_id=slot.id,
        status=AppointmentStatus.RESERVADO,
        price=settings.consultation_price,
        deposit_amount=settings.deposit_amount,
    )
    db.add(agendamento)
    db.commit()
    db.refresh(agendamento)
    return agendamento


def confirm(db: Session, appointment: Appointment, receipt_json: str) -> Appointment:
    """Confirma o agendamento após o comprovante ser validado."""
    appointment.status = AppointmentStatus.CONFIRMADO
    appointment.deposit_paid_at = now()
    appointment.receipt_raw = receipt_json
    slot = db.get(Slot, appointment.slot_id)
    if slot:
        slot.hold_expires_at = None  # deixa de ser reserva temporária
    db.commit()
    db.refresh(appointment)
    return appointment


def active_appointment(db: Session, patient: Patient) -> Appointment | None:
    """Reserva ou confirmação mais recente do paciente, se houver."""
    return db.scalar(
        select(Appointment)
        .where(
            Appointment.patient_id == patient.id,
            Appointment.status.in_(
                [AppointmentStatus.RESERVADO, AppointmentStatus.CONFIRMADO]
            ),
        )
        .order_by(Appointment.id.desc())
    )


def cancel_open_appointments(db: Session, patient: Patient) -> None:
    """Usado pelo reset de memória: devolve horários presos à agenda."""
    reservas = db.scalars(
        select(Appointment).where(
            Appointment.patient_id == patient.id,
            Appointment.status == AppointmentStatus.RESERVADO,
        )
    ).all()
    for reserva in reservas:
        reserva.status = AppointmentStatus.CANCELADO
        slot = db.get(Slot, reserva.slot_id)
        if slot:
            slot.is_open = True
            slot.held_by_patient_id = None
            slot.hold_expires_at = None
    if reservas:
        db.commit()

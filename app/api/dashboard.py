"""Endpoints consumidos pelo painel web."""

from __future__ import annotations

import base64 as b64

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.core.config import settings
from app.core.utils import format_slot, to_brt
from app.database import get_db
from app.models import AgentLog, Appointment, AppointmentStatus, Message, Patient, PatientStatus, Slot
from app.services import agent, evolution, llm, memory, scheduling

router = APIRouter(prefix="/api", tags=["painel"], dependencies=[Depends(current_user)])

COLUNAS = [
    (PatientStatus.EM_ATENDIMENTO, "Em atendimento"),
    (PatientStatus.AGUARDANDO_PIX, "Aguardando Pix"),
    (PatientStatus.CONFIRMADO, "Confirmado / Agendado"),
]


def _card(db: Session, paciente: Patient) -> dict:
    agendamento = scheduling.active_appointment(db, paciente)
    horario = None
    if agendamento:
        slot = db.get(Slot, agendamento.slot_id)
        horario = format_slot(slot.starts_at) if slot else None
    ultima = db.scalar(
        select(Message)
        .where(Message.patient_id == paciente.id)
        .order_by(Message.created_at.desc())
    )
    return {
        "id": paciente.id,
        "nome": paciente.name or "Sem nome ainda",
        "telefone": paciente.phone,
        "motivo": paciente.reason,
        "tipo_motivo": paciente.reason_type,
        "status": paciente.status.value,
        "horario": horario,
        "sinal_pago": bool(agendamento and agendamento.deposit_paid_at),
        "ultima_mensagem": (ultima.content[:90] if ultima else None),
        "atualizado_em": to_brt(paciente.updated_at).strftime("%d/%m %H:%M"),
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    scheduling.release_expired(db)
    confirmados = db.scalars(
        select(Appointment).where(Appointment.status == AppointmentStatus.CONFIRMADO)
    ).all()
    return {
        "clinica": settings.clinic_name,
        "valor_consulta": settings.consultation_price,
        "valor_sinal": settings.deposit_amount,
        "pacientes": db.query(Patient).count(),
        "confirmados": len(confirmados),
        "receita_sinais": round(sum(a.deposit_amount for a in confirmados), 2),
        "horarios_livres": len(scheduling.open_slots(db, limit=100)),
        "ia": llm.mode(),
        "redis": memory.status(),
        "whatsapp": "conectado" if settings.whatsapp_enabled else "sem credenciais",
    }


@router.get("/patients")
def kanban(db: Session = Depends(get_db)) -> dict:
    scheduling.release_expired(db)
    colunas = []
    for status_enum, titulo in COLUNAS:
        pacientes = db.scalars(
            select(Patient)
            .where(Patient.status == status_enum)
            .order_by(Patient.updated_at.desc())
        ).all()
        colunas.append(
            {
                "status": status_enum.value,
                "titulo": titulo,
                "cards": [_card(db, p) for p in pacientes],
            }
        )
    return {"colunas": colunas}


@router.get("/patients/{patient_id}")
def patient_detail(patient_id: int, db: Session = Depends(get_db)) -> dict:
    paciente = db.get(Patient, patient_id)
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado.")
    mensagens = db.scalars(
        select(Message)
        .where(Message.patient_id == patient_id)
        .order_by(Message.created_at)
    ).all()
    sessao = memory.load(paciente.phone)
    return {
        **_card(db, paciente),
        "etapa": sessao.get("step", "start"),
        "mensagens": [
            {
                "direcao": m.direction,
                "tipo": m.kind,
                "conteudo": m.content,
                "hora": to_brt(m.created_at).strftime("%d/%m %H:%M"),
            }
            for m in mensagens
        ],
    }


@router.post("/patients/{patient_id}/reset")
def reset_memory(patient_id: int, db: Session = Depends(get_db)) -> dict:
    paciente = db.get(Patient, patient_id)
    if not paciente:
        raise HTTPException(404, "Paciente não encontrado.")
    agent.reset_patient(db, paciente)
    return {"ok": True, "mensagem": f"Memória de {paciente.name or paciente.phone} apagada."}


@router.get("/logs")
def logs(after_id: int = 0, limit: int = 80, db: Session = Depends(get_db)) -> dict:
    registros = db.scalars(
        select(AgentLog)
        .where(AgentLog.id > after_id)
        .order_by(AgentLog.id.desc())
        .limit(limit)
    ).all()
    pacientes = {p.id: (p.name or p.phone) for p in db.scalars(select(Patient)).all()}
    return {
        "logs": [
            {
                "id": r.id,
                "hora": to_brt(r.created_at).strftime("%d/%m %H:%M:%S"),
                "nivel": r.level,
                "evento": r.event,
                "detalhe": (r.detail or "")[:400],
                "paciente": pacientes.get(r.patient_id, "—"),
            }
            for r in reversed(registros)
        ]
    }


@router.get("/slots")
def slots(db: Session = Depends(get_db)) -> dict:
    livres = scheduling.open_slots(db, limit=40)
    return {"slots": [{"id": s.id, "quando": format_slot(s.starts_at)} for s in livres]}


# --- WhatsApp --------------------------------------------------------------


@router.get("/whatsapp/qrcode")
def qrcode() -> dict:
    return evolution.fetch_qrcode()


@router.get("/whatsapp/state")
def whatsapp_state() -> dict:
    return evolution.connection_state()


class WebhookInput(BaseModel):
    public_url: str


@router.post("/whatsapp/webhook")
def set_webhook(dados: WebhookInput) -> dict:
    return evolution.register_webhook(dados.public_url)


# --- Simulador (testar o fluxo sem WhatsApp) -------------------------------


class SimulatorInput(BaseModel):
    phone: str
    text: str


@router.post("/simulator/text")
def simulate_text(dados: SimulatorInput, db: Session = Depends(get_db)) -> dict:
    respostas = agent.handle_incoming(db, dados.phone.strip(), text=dados.text)
    return {"respostas": respostas}


@router.post("/simulator/image")
def simulate_image(
    phone: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    conteudo = file.file.read()
    respostas = agent.handle_incoming(
        db,
        phone.strip(),
        image_b64=b64.b64encode(conteudo).decode(),
        mime=file.content_type or "image/jpeg",
    )
    return {"respostas": respostas}

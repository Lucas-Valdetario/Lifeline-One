"""Agente agendador — orquestração do atendimento.

O fluxo do briefing é uma máquina de estados explícita (start → nome → motivo →
horário → sinal Pix → confirmado). O Gemini é usado para *entender* o que o
paciente escreveu e para responder perguntas fora do roteiro; quem decide o
próximo passo é o código. Isso deixa o atendimento previsível, fácil de
depurar e resistente a mensagens fora de ordem.

Entrada única: `handle_incoming`. Ela devolve as mensagens de resposta já
persistidas e enviadas ao WhatsApp.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import format_money, format_slot, now
from app.models import AgentLog, Message, Patient, PatientStatus, Slot
from app.services import evolution, llm, memory, scheduling, vision

logger = logging.getLogger(__name__)

REINICIAR = {"reiniciar", "recomecar", "recomeçar", "cancelar", "começar de novo"}

CONTEXTO_CLINICA = f"""
Nome: {settings.clinic_name}
Endereço: {settings.clinic_address}
Telefone fixo: {settings.clinic_phone}
Estacionamento: gratuito para pacientes.
Valor da consulta: {format_money(settings.consultation_price)} (fixo).
Sinal para reservar: {format_money(settings.deposit_amount)} via Pix.
Chave Pix: {settings.pix_key} ({settings.pix_holder}).
Antes da consulta há pré-triagem no local; alguns exames essenciais já estão
inclusos no valor da consulta.
"""


# ---------------------------------------------------------------------------
# Persistência auxiliar
# ---------------------------------------------------------------------------


def get_or_create_patient(db: Session, phone: str) -> Patient:
    paciente = db.scalar(select(Patient).where(Patient.phone == phone))
    if paciente is None:
        paciente = Patient(phone=phone, status=PatientStatus.EM_ATENDIMENTO)
        db.add(paciente)
        db.commit()
        db.refresh(paciente)
    return paciente


def log_event(
    db: Session, patient_id: int | None, event: str, detail: str = "", level: str = "info"
) -> None:
    db.add(AgentLog(patient_id=patient_id, event=event, detail=detail, level=level))
    db.commit()


def log_message(db: Session, patient_id: int, direction: str, content: str, kind: str = "text") -> None:
    db.add(Message(patient_id=patient_id, direction=direction, content=content, kind=kind))
    db.commit()


# ---------------------------------------------------------------------------
# Textos do atendimento
# ---------------------------------------------------------------------------


def msg_saudacao() -> str:
    return (
        f"Olá! Aqui é a assistente virtual da {settings.clinic_name}. 😊\n"
        "Vou te ajudar a agendar sua consulta.\n\n"
        "Para começar, qual é o seu *nome completo*?"
    )


def msg_valor_e_motivo(nome: str) -> str:
    return (
        f"Prazer, {nome.split()[0]}! Anotei aqui.\n\n"
        f"A consulta tem valor fixo de *{format_money(settings.consultation_price)}*.\n\n"
        "Me conta: você está com *algum sintoma específico* ou é uma "
        "*consulta de rotina*?"
    )


def msg_horarios(opcoes: list[str]) -> str:
    linhas = "\n".join(f"*{i + 1}.* {o}" for i, o in enumerate(opcoes))
    return (
        "Perfeito, obrigada! Estes são os horários disponíveis:\n\n"
        f"{linhas}\n\n"
        "Responda com o *número* da opção que prefere."
    )


def msg_pix(horario: str) -> str:
    return (
        f"Ótimo! Segurei o horário de *{horario}* para você por "
        f"{settings.slot_hold_minutes} minutos.\n\n"
        f"Para garantir a vaga, é necessário um sinal de 50% — "
        f"*{format_money(settings.deposit_amount)}*.\n\n"
        f"🔑 Chave Pix: *{settings.pix_key}*\n"
        f"👤 Favorecido: {settings.pix_holder}\n\n"
        "Assim que pagar, me envie a *foto ou print do comprovante* aqui pelo "
        "WhatsApp que eu confirmo na hora."
    )


def msg_confirmacao(nome: str, horario: str) -> str:
    return (
        f"Pagamento confirmado, {nome.split()[0]}! ✅\n"
        f"Sua consulta está *agendada para {horario}*.\n\n"
        f"📍 *Endereço:* {settings.clinic_address}\n"
        f"☎️ *Telefone:* {settings.clinic_phone}\n"
        "🅿️ *Estacionamento gratuito* para pacientes.\n\n"
        "ℹ️ Chegue com 15 minutos de antecedência: fazemos uma *pré-triagem no "
        "local* e *alguns exames essenciais já estão inclusos* no valor da "
        "consulta.\n\n"
        f"O restante ({format_money(settings.consultation_price - settings.deposit_amount)}) "
        "é pago na recepção. Até lá! 💙"
    )


# ---------------------------------------------------------------------------
# Fluxo
# ---------------------------------------------------------------------------


def _parece_pergunta(texto: str) -> bool:
    gatilhos = (
        "?", "onde", "quanto", "convênio", "convenio", "plano", "estacion",
        "endereço", "endereco", "telefone", "horário de funcionamento", "exame",
    )
    base = texto.lower()
    return any(g in base for g in gatilhos)


def _resposta_fora_do_fluxo(db: Session, paciente: Patient, texto: str, retomada: str) -> str:
    log_event(db, paciente.id, "fora_do_fluxo", texto[:200])
    return llm.answer_offtopic(texto, CONTEXTO_CLINICA, retomada)


def _oferecer_horarios(db: Session, sessao: dict) -> list[str]:
    slots = scheduling.open_slots(db)
    if not slots:
        return [
            "No momento não temos horários livres na agenda. Nossa equipe entra "
            f"em contato pelo {settings.clinic_phone} para encaixar você."
        ]
    opcoes = [format_slot(s.starts_at) for s in slots]
    sessao["data"]["slot_ids"] = [s.id for s in slots]
    sessao["data"]["slot_labels"] = opcoes
    sessao["step"] = "choose_slot"
    return [msg_horarios(opcoes)]


def _handle_text(db: Session, paciente: Patient, sessao: dict, texto: str) -> list[str]:
    step = sessao.get("step", "start")

    if texto.strip().lower() in REINICIAR:
        reset_patient(db, paciente)
        sessao.clear()
        sessao.update(memory.empty_session())
        sessao["step"] = "ask_name"
        return [msg_saudacao()]

    # --- Etapa 0/1: saudação e nome -------------------------------------
    if step in ("start",):
        sessao["step"] = "ask_name"
        return [msg_saudacao()]

    if step == "ask_name":
        nome = llm.extract_name(texto)
        if not nome:
            if _parece_pergunta(texto):
                return [
                    _resposta_fora_do_fluxo(
                        db, paciente, texto, "Para seguirmos, me diga seu nome completo."
                    )
                ]
            return [
                "Não consegui identificar seu nome. Pode me enviar o *nome "
                "completo*, como está no documento?"
            ]
        paciente.name = nome
        db.commit()
        log_event(db, paciente.id, "nome_coletado", nome)
        sessao["data"]["nome"] = nome
        sessao["step"] = "ask_reason"
        return [msg_valor_e_motivo(nome)]

    # --- Etapa 1: motivo --------------------------------------------------
    if step == "ask_reason":
        classificacao = llm.classify_reason(texto)
        paciente.reason = classificacao["resumo"][:500]
        paciente.reason_type = classificacao["tipo"]
        db.commit()
        log_event(
            db, paciente.id, "motivo_classificado",
            f"{classificacao['tipo']}: {classificacao['resumo'][:160]}",
        )
        return _oferecer_horarios(db, sessao)

    # --- Etapa 2: escolha do horário -------------------------------------
    if step == "choose_slot":
        opcoes = sessao["data"].get("slot_labels", [])
        escolha = llm.pick_slot(texto, opcoes)
        if escolha is None:
            if _parece_pergunta(texto):
                return [
                    _resposta_fora_do_fluxo(
                        db, paciente, texto,
                        "Me diga o número do horário que prefere para eu reservar.",
                    )
                ]
            return [
                "Não entendi qual horário você prefere. Responda com o *número* "
                f"da opção (de 1 a {len(opcoes)}), por favor."
            ]

        slot_id = sessao["data"]["slot_ids"][escolha - 1]
        slot = db.get(Slot, slot_id)
        if slot is None or not slot.is_open:
            log_event(db, paciente.id, "slot_indisponivel", str(slot_id), level="warn")
            return ["Poxa, esse horário acabou de ser preenchido. Veja as opções atualizadas:"] + _oferecer_horarios(db, sessao)

        agendamento = scheduling.hold_slot(db, slot, paciente)
        paciente.status = PatientStatus.AGUARDANDO_PIX
        db.commit()
        horario = format_slot(slot.starts_at)
        sessao["data"]["appointment_id"] = agendamento.id
        sessao["data"]["horario"] = horario
        sessao["step"] = "await_pix"
        log_event(db, paciente.id, "horario_reservado", horario)
        return [msg_pix(horario)]

    # --- Etapa 3: aguardando comprovante ---------------------------------
    if step == "await_pix":
        if _parece_pergunta(texto):
            return [
                _resposta_fora_do_fluxo(
                    db, paciente, texto,
                    "Quando puder, me envie a foto do comprovante do Pix.",
                )
            ]
        return [
            f"Estou aguardando o comprovante do sinal de "
            f"*{format_money(settings.deposit_amount)}*.\n"
            f"Chave Pix: *{settings.pix_key}*\n\n"
            "Pode enviar a *foto/print* do comprovante aqui mesmo. 📷"
        ]

    # --- Etapa 4: já confirmado ------------------------------------------
    horario = sessao["data"].get("horario", "seu horário")
    return [
        _resposta_fora_do_fluxo(
            db, paciente, texto,
            f"Sua consulta segue confirmada para {horario}. Se precisar remarcar, "
            f"ligue para {settings.clinic_phone} ou escreva *reiniciar*.",
        )
    ]


def _handle_image(db: Session, paciente: Patient, sessao: dict, image_b64: str, mime: str) -> list[str]:
    step = sessao.get("step")
    if step != "await_pix":
        return [
            "Recebi sua imagem, mas ainda não estamos na etapa de pagamento. "
            + (
                "Vamos continuar: qual é o seu nome completo?"
                if step in ("start", "ask_name")
                else "Podemos seguir com o agendamento?"
            )
        ]

    log_event(db, paciente.id, "comprovante_recebido", f"{len(image_b64)} bytes (base64)")
    dados = vision.read_receipt(image_b64, mime)
    aprovado, motivo = vision.validate_receipt(dados)
    log_event(
        db,
        paciente.id,
        "comprovante_analisado",
        json.dumps({"aprovado": aprovado, "motivo": motivo, "extraido": dados}, ensure_ascii=False),
        level="info" if aprovado else "warn",
    )

    if not aprovado:
        sessao["receipt_attempts"] = sessao.get("receipt_attempts", 0) + 1
        resposta = f"Analisei o comprovante, mas {motivo}"
        if sessao["receipt_attempts"] >= 3:
            resposta += (
                f"\n\nSe preferir, fale com a recepção pelo {settings.clinic_phone} "
                "que a gente resolve por lá."
            )
        return [resposta]

    agendamento = scheduling.active_appointment(db, paciente)
    if agendamento is None:
        sessao["step"] = "ask_reason"
        return ["A reserva do horário expirou. Vamos escolher um novo horário?"] + _oferecer_horarios(db, sessao)

    scheduling.confirm(db, agendamento, json.dumps(dados, ensure_ascii=False))
    paciente.status = PatientStatus.CONFIRMADO
    db.commit()
    sessao["step"] = "done"
    log_event(db, paciente.id, "agendamento_confirmado", sessao["data"].get("horario", ""))
    return [msg_confirmacao(paciente.name or "paciente", sessao["data"].get("horario", ""))]


# ---------------------------------------------------------------------------
# Porta de entrada
# ---------------------------------------------------------------------------


def handle_incoming(
    db: Session,
    phone: str,
    *,
    text: str | None = None,
    image_b64: str | None = None,
    mime: str = "image/jpeg",
) -> list[str]:
    """Processa uma mensagem recebida e devolve as respostas do agente."""
    paciente = get_or_create_patient(db, phone)
    sessao = memory.load(phone)

    if image_b64:
        log_message(db, paciente.id, "in", "[imagem recebida]", kind="image")
        memory.remember(sessao, "user", "[imagem: comprovante]")
        respostas = _handle_image(db, paciente, sessao, image_b64, mime)
    else:
        texto = (text or "").strip()
        if not texto:
            return []
        log_message(db, paciente.id, "in", texto)
        memory.remember(sessao, "user", texto)
        respostas = _handle_text(db, paciente, sessao, texto)

    for resposta in respostas:
        log_message(db, paciente.id, "out", resposta)
        memory.remember(sessao, "assistant", resposta)
        evolution.send_text(phone, resposta)

    paciente.updated_at = now()
    db.commit()
    memory.save(phone, sessao)
    return respostas


def reset_patient(db: Session, paciente: Patient) -> None:
    """Limpa a memória do paciente e devolve reservas pendentes à agenda."""
    memory.clear(paciente.phone)
    scheduling.cancel_open_appointments(db, paciente)
    paciente.status = PatientStatus.EM_ATENDIMENTO
    db.commit()
    log_event(db, paciente.id, "memoria_resetada", "Contexto apagado pelo painel")

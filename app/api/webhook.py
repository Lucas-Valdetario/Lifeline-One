"""Webhook da Evolution API.

Recebe os eventos `messages.upsert`, extrai telefone + conteúdo (texto ou
imagem em base64) e entrega ao agente. Formatos da v1 e da v2 são aceitos.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import agent, evolution

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def extract_text(message: dict) -> str | None:
    return (
        message.get("conversation")
        or message.get("extendedTextMessage", {}).get("text")
        or message.get("imageMessage", {}).get("caption")
        or message.get("ephemeralMessage", {})
        .get("message", {})
        .get("conversation")
    )


def extract_image(data: dict) -> tuple[str | None, str]:
    """Devolve (base64, mime) da imagem recebida, se houver."""
    message = data.get("message", {}) or {}
    image = message.get("imageMessage") or {}
    mime = image.get("mimetype", "image/jpeg")

    base64 = (
        data.get("base64")
        or message.get("base64")
        or image.get("base64")
        or (data.get("mediaBase64") if isinstance(data.get("mediaBase64"), str) else None)
    )
    if not base64 and image:
        base64 = evolution.download_media_base64(data.get("key", {}))
    if base64 and base64.startswith("data:"):
        base64 = base64.split(",", 1)[-1]
    return base64, mime


@router.post("/evolution")
async def evolution_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    payload = await request.json()
    evento = (payload.get("event") or "").lower().replace("_", ".")
    if evento not in ("messages.upsert", ""):
        return {"ignorado": evento}

    data = payload.get("data") or payload
    if isinstance(data, list):
        data = data[0] if data else {}

    key = data.get("key", {}) or {}
    if key.get("fromMe"):
        return {"ignorado": "mensagem propria"}

    jid = key.get("remoteJid") or data.get("remoteJid") or ""
    if not jid or "@g.us" in jid:  # ignora grupos
        return {"ignorado": "sem remetente individual"}

    phone = evolution.from_jid(jid)
    message = data.get("message", {}) or {}
    tipo = data.get("messageType") or ("imageMessage" if "imageMessage" in message else "conversation")

    if "image" in tipo:
        base64, mime = extract_image(data)
        if not base64:
            _avisar(db, phone, "midia_sem_conteudo", jid,
                    "Recebi sua imagem, mas ela chegou incompleta aqui. "
                    "Pode reenviar o print do comprovante, por favor?")
            return {"status": "imagem sem conteudo"}
        entrada = {"image_b64": base64, "mime": mime}
    else:
        texto = extract_text(message)
        if not texto:
            return {"ignorado": "sem texto"}
        entrada = {"text": texto}

    try:
        respostas = agent.handle_incoming(db, phone, **entrada)
    except Exception as exc:  # nada derruba o atendimento
        logger.exception("Erro ao processar mensagem de %s", phone)
        db.rollback()
        _avisar(db, phone, "erro_processamento", str(exc)[:200],
                "Tive um problema técnico agora. Pode repetir a última mensagem, "
                "por favor?")
        return {"status": "erro tratado"}

    return {"status": "ok", "respostas": len(respostas)}


def _avisar(db: Session, phone: str, evento: str, detalhe: str, texto: str) -> None:
    """Registra o problema e responde ao paciente sem quebrar o atendimento."""
    paciente = agent.get_or_create_patient(db, phone)
    agent.log_event(db, paciente.id, evento, detalhe, level="error")
    agent.log_message(db, paciente.id, "out", texto)
    evolution.send_text(phone, texto)

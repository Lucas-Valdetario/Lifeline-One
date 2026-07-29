"""Transcrição de áudios do WhatsApp (mensagens de voz) via Gemini.

Reaproveita o modelo multimodal configurado em `gemini_vision_model` (o mesmo
usado para ler comprovantes) — o Gemini entende áudio e imagem pelo mesmo
tipo de conteúdo. Se a transcrição falhar (rede, cota, áudio incompreensível),
o agente pede para o paciente reenviar a mensagem em texto.
"""

from __future__ import annotations

import base64
import logging

from app.core.config import settings
from app.services.llm import build_model

logger = logging.getLogger(__name__)

PROMPT = (
    "Transcreva fielmente, em português, a fala contida neste áudio. Responda "
    "somente com a transcrição, sem comentários nem formatação. Se o áudio "
    "estiver mudo, incompreensível ou sem fala, responda exatamente: [inaudível]."
)


def transcribe(audio_b64: str, mime_type: str = "audio/ogg") -> str | None:
    """Transcreve um áudio em base64 para texto; None se não for possível."""
    modelo = build_model(settings.gemini_vision_model, temperature=0)
    if modelo is None:
        return None

    from langchain_core.messages import HumanMessage

    mensagem = HumanMessage(
        content=[
            {"type": "text", "text": PROMPT},
            {"type": "media", "mime_type": mime_type, "data": base64.b64decode(audio_b64)},
        ]
    )
    try:
        resposta = modelo.invoke([mensagem])
    except Exception as exc:  # rede, cota, formato de áudio não suportado...
        logger.error("Gemini indisponível para transcrição de áudio: %s", exc)
        return None

    texto = getattr(resposta, "content", str(resposta)).strip()
    return texto if texto and texto != "[inaudível]" else None

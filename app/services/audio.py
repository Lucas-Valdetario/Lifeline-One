"""Transcrição de áudios do WhatsApp (mensagens de voz) via OpenAI.

Diferente de um modelo multimodal único, a OpenAI trata áudio em um endpoint
próprio (`/audio/transcriptions`, modelo configurado em
`openai_transcription_model`) — por isso este módulo fala direto com o SDK da
OpenAI, e não pelo LangChain como o resto do projeto. Se a transcrição falhar
(rede, cota, formato não suportado, áudio sem fala), o agente pede para o
paciente reenviar a mensagem em texto.
"""

from __future__ import annotations

import base64
import logging

from app.core.config import settings
from app.services.prompts import TRANSCRITORA_DE_AUDIO

logger = logging.getLogger(__name__)

# Extensão exigida pelo endpoint de transcrição para identificar o formato.
# O WhatsApp envia áudio em audio/ogg (codec opus).
EXTENSOES = {
    "audio/ogg": "ogg",
    "audio/oga": "oga",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
}

_client = None


def _openai():
    """Cria o cliente da OpenAI uma única vez (lazy); None se a inicialização falhar."""
    global _client
    if _client is None:
        try:
            from openai import OpenAI

            _client = OpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Falha ao inicializar o cliente da OpenAI: %s", exc)
            return None
    return _client


def transcribe(audio_b64: str, mime_type: str = "audio/ogg") -> str | None:
    """Transcreve um áudio em base64 para texto; None se não for possível."""
    cliente = _openai()
    if cliente is None:
        return None

    extensao = EXTENSOES.get(mime_type.split(";")[0].strip().lower(), "ogg")

    try:
        # O SDK identifica o formato pelo nome do arquivo na tupla.
        resposta = cliente.audio.transcriptions.create(
            model=settings.openai_transcription_model,
            file=(f"audio.{extensao}", base64.b64decode(audio_b64), mime_type),
            language="pt",
            # Dica de contexto: melhora nomes próprios e termos do atendimento.
            prompt=TRANSCRITORA_DE_AUDIO.system(),
            response_format="text",
        )
    except Exception as exc:  # rede, cota, áudio vazio ou formato não suportado...
        logger.error("OpenAI indisponível para transcrição de áudio: %s", exc)
        return None

    # Com response_format="text" o SDK devolve a string crua; os demais
    # formatos devolvem um objeto com o campo `text`.
    texto = (resposta if isinstance(resposta, str) else getattr(resposta, "text", "")).strip()
    return texto or None

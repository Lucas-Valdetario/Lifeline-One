"""Integração com a Evolution API (WhatsApp).

Cobre o que o projeto precisa: enviar texto, pegar o QR Code de pareamento,
consultar o estado da conexão e registrar o webhook. Compatível com as rotas
da v2 (e com o corpo da v1, enviado junto por segurança).

Sem `EVOLUTION_API_KEY`, os envios são apenas registrados no log — útil para
desenvolver e demonstrar o fluxo pelo simulador do painel.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 20.0


def _headers() -> dict:
    """Cabeçalhos padrão das requisições à Evolution API."""
    return {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}


def _url(path: str) -> str:
    """Monta a URL completa de um endpoint da Evolution API."""
    return f"{settings.evolution_base_url.rstrip('/')}{path}"


def to_jid(phone: str) -> str:
    """Converte um telefone (só dígitos) no JID do WhatsApp."""
    numero = "".join(filter(str.isdigit, phone))
    return f"{numero}@s.whatsapp.net"


def from_jid(jid: str) -> str:
    """Extrai o telefone de um JID do WhatsApp."""
    return jid.split("@")[0].split(":")[0]


def send_text(phone: str, text: str) -> bool:
    """Envia uma mensagem de texto. Devolve True se a Evolution aceitou."""
    if not settings.whatsapp_enabled:
        logger.info("[WhatsApp simulado] -> %s: %s", phone, text)
        return False

    payload = {
        "number": "".join(filter(str.isdigit, phone)),
        "text": text,
        "textMessage": {"text": text},  # compatibilidade com a v1
    }
    try:
        resposta = httpx.post(
            _url(f"/message/sendText/{settings.evolution_instance}"),
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Falha ao enviar mensagem: %s", exc)
        return False


def fetch_qrcode() -> dict:
    """QR Code (base64) para parear a conta do WhatsApp."""
    if not settings.whatsapp_enabled:
        return {
            "status": "sem_credenciais",
            "mensagem": "Informe EVOLUTION_API_KEY e EVOLUTION_BASE_URL no .env "
            "para gerar o QR Code.",
        }
    try:
        resposta = httpx.get(
            _url(f"/instance/connect/{settings.evolution_instance}"),
            headers=_headers(),
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        base64 = dados.get("base64") or dados.get("qrcode", {}).get("base64")
        if base64:
            return {"status": "aguardando_leitura", "qrcode": base64}
        return {"status": dados.get("instance", {}).get("state", "conectado"), "raw": dados}
    except httpx.HTTPError as exc:
        return {"status": "erro", "mensagem": f"Evolution API indisponível: {exc}"}


def connection_state() -> dict:
    """Estado atual da conexão da instância com o WhatsApp."""
    if not settings.whatsapp_enabled:
        return {"state": "sem_credenciais"}
    try:
        resposta = httpx.get(
            _url(f"/instance/connectionState/{settings.evolution_instance}"),
            headers=_headers(),
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        return {"state": dados.get("instance", {}).get("state", "desconhecido")}
    except httpx.HTTPError as exc:
        return {"state": "erro", "mensagem": str(exc)}


def register_webhook(public_url: str) -> dict:
    """Aponta a instância para o webhook desta aplicação."""
    payload = {
        "webhook": {
            "enabled": True,
            "url": f"{public_url.rstrip('/')}/webhook/evolution",
            "webhookByEvents": False,
            "webhookBase64": True,
            "events": ["MESSAGES_UPSERT"],
        }
    }
    try:
        resposta = httpx.post(
            _url(f"/webhook/set/{settings.evolution_instance}"),
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        return resposta.json()
    except httpx.HTTPError as exc:
        return {"erro": str(exc)}


def download_media_base64(message_key: dict) -> str | None:
    """Busca a mídia em base64 quando o webhook não a envia embutida."""
    if not settings.whatsapp_enabled:
        return None
    try:
        resposta = httpx.post(
            _url(f"/chat/getBase64FromMediaMessage/{settings.evolution_instance}"),
            json={"message": {"key": message_key}, "convertToMp4": False},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        resposta.raise_for_status()
        return resposta.json().get("base64")
    except httpx.HTTPError as exc:
        logger.error("Falha ao baixar mídia: %s", exc)
        return None

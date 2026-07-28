"""Leitura e validação do comprovante Pix (IA de visão).

`read_receipt` extrai os campos da imagem; `validate_receipt` aplica as regras
de negócio: valor exato do sinal e transação recente (bloqueia o reuso de
comprovantes antigos).

Sem `GOOGLE_API_KEY`, o extrator entra em modo simulado e devolve um
comprovante coerente com o valor esperado, marcado como `simulado: true`,
para que o restante do fluxo possa ser testado ponta a ponta.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.core.config import settings
from app.core.utils import BRT, format_money, now, receipt_is_fresh
from app.services.llm import parse_json

logger = logging.getLogger(__name__)

PROMPT = """Você analisa comprovantes de pagamento Pix brasileiros.
Extraia da imagem, com precisão, e responda SOMENTE em JSON:
{
  "eh_comprovante": true/false,
  "valor": número decimal (ex: 190.00) ou null,
  "data": "DD/MM/AAAA" ou null,
  "hora": "HH:MM" ou null,
  "beneficiario": texto ou null,
  "pagador": texto ou null,
  "id_transacao": texto ou null,
  "observacao": "qualquer problema de leitura, em português"
}
Regras: não invente valores; se a imagem estiver ilegível ou não for um
comprovante, use "eh_comprovante": false. O valor deve ser o valor efetivamente
transferido."""


def _vision_model():
    if not settings.ai_enabled:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.gemini_vision_model,
            google_api_key=settings.google_api_key,
            temperature=0,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Falha ao inicializar o Gemini Vision: %s", exc)
        return None


def read_receipt(image_b64: str, mime_type: str = "image/jpeg") -> dict:
    """Extrai os dados do comprovante a partir da imagem em base64."""
    modelo = _vision_model()
    if modelo is None:
        agora = now()
        return {
            "eh_comprovante": True,
            "valor": settings.deposit_amount,
            "data": agora.strftime("%d/%m/%Y"),
            "hora": agora.strftime("%H:%M"),
            "beneficiario": settings.pix_holder,
            "pagador": "Paciente (simulado)",
            "id_transacao": "E00000000SIMULADO",
            "observacao": "Leitura simulada — sem GOOGLE_API_KEY configurada.",
            "simulado": True,
        }

    from langchain_core.messages import HumanMessage

    mensagem = HumanMessage(
        content=[
            {"type": "text", "text": PROMPT},
            {
                "type": "image_url",
                "image_url": f"data:{mime_type};base64,{image_b64}",
            },
        ]
    )
    try:
        resposta = modelo.invoke([mensagem])
    except Exception as exc:  # rede, cota, imagem recusada pelo modelo...
        logger.error("Gemini Vision indisponível: %s", exc)
        return {"eh_comprovante": False, "falha_tecnica": True, "observacao": str(exc)}

    dados = parse_json(getattr(resposta, "content", str(resposta)))
    dados.setdefault("eh_comprovante", False)
    dados["simulado"] = False
    return dados


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def _to_float(valor) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    if not valor:
        return None
    texto = re.sub(r"[^\d,.]", "", str(valor))
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _to_datetime(data: str | None, hora: str | None) -> datetime | None:
    if not data:
        return None
    hora = (hora or "00:00").strip()
    if len(hora.split(":")) == 3:
        hora = ":".join(hora.split(":")[:2])
    for formato in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{data.strip()} {hora}", formato).replace(tzinfo=BRT)
        except ValueError:
            continue
    return None


def validate_receipt(dados: dict) -> tuple[bool, str]:
    """Aplica as regras do briefing. Devolve (aprovado, motivo em pt-BR)."""
    esperado = settings.deposit_amount

    if dados.get("falha_tecnica"):
        return False, (
            "tive uma instabilidade técnica ao analisar a imagem. Pode reenviar "
            "o comprovante em alguns instantes?"
        )

    if not dados.get("eh_comprovante"):
        return False, (
            "não consegui identificar um comprovante Pix nessa imagem. "
            "Pode reenviar o print completo da transferência?"
        )

    valor = _to_float(dados.get("valor"))
    if valor is None:
        return False, (
            "não consegui ler o valor no comprovante. Envie um print em que o "
            "valor apareça nítido, por favor."
        )
    if abs(valor - esperado) > 0.01:
        return False, (
            f"o comprovante mostra {format_money(valor)}, mas o sinal é de "
            f"{format_money(esperado)}. Pode conferir e reenviar?"
        )

    quando = _to_datetime(dados.get("data"), dados.get("hora"))
    if quando is None:
        return False, (
            "não consegui ler a data e o horário da transação. Envie o "
            "comprovante completo, com data e hora visíveis."
        )
    if not receipt_is_fresh(quando):
        return False, (
            f"esse comprovante é de {quando.strftime('%d/%m/%Y às %H:%M')} e só "
            f"aceito pagamentos das últimas {settings.receipt_max_age_hours} horas. "
            "Envie o comprovante do pagamento feito agora, por favor."
        )

    return True, f"Pagamento de {format_money(valor)} confirmado."

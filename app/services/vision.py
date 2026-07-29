"""Leitura e validação do comprovante Pix (IA de visão).

`read_receipt` extrai os campos da imagem via Gemini Vision; `validate_receipt`
aplica as regras de negócio: valor exato do sinal e transação recente
(bloqueia o reuso de comprovantes antigos).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.core.config import settings
from app.core.utils import BRT, format_money, receipt_is_fresh
from app.services.llm import build_model, parse_json

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


def read_receipt(image_b64: str, mime_type: str = "image/jpeg") -> dict:
    """Extrai os dados do comprovante a partir da imagem em base64, via Gemini Vision."""
    modelo = build_model(settings.gemini_vision_model, temperature=0)
    if modelo is None:
        return {"eh_comprovante": False, "falha_tecnica": True, "observacao": "IA indisponível no momento."}

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
    return dados


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def _to_float(valor) -> float | None:
    """Converte um valor monetário (número ou texto em pt-BR) para float."""
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
    """Combina data e hora extraídas do comprovante em um datetime, se possível."""
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

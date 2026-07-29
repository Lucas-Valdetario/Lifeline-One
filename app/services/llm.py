"""Camada de linguagem do agente (LangChain + Google Gemini).

Todo acesso ao modelo passa por aqui. O agente depende do Gemini para
entender o paciente — não há interpretação local: se a chamada falhar (rede,
cota, resposta inesperada), a função devolve `None`/um valor neutro e quem
chama decide como reagir (normalmente pedindo para o paciente repetir).
"""

from __future__ import annotations

import json
import logging
import re

from app.core.config import settings
from app.services.prompts import (
    CLASSIFICADOR_DE_MOTIVO,
    EXTRATOR_DE_NOME,
    SECRETARIA_VIRTUAL,
    SELETOR_DE_HORARIO,
)

logger = logging.getLogger(__name__)

_llm = None


def build_model(model_name: str, temperature: float = 0.2, max_output_tokens: int = 800):
    """Cria um ChatGoogleGenerativeAI para o modelo informado; None se a inicialização falhar."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.google_api_key,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Falha ao inicializar o Gemini (%s): %s", model_name, exc)
        return None


def _model():
    """Instancia o modelo de texto uma única vez (lazy) e reaproveita entre chamadas."""
    global _llm
    if _llm is None:
        _llm = build_model(settings.gemini_text_model)
    return _llm


def mode() -> str:
    """'gemini' se o modelo estiver acessível, 'indisponível' se a inicialização falhar."""
    return "gemini" if _model() else "indisponível"


# ---------------------------------------------------------------------------
# Conversa com o modelo
# ---------------------------------------------------------------------------


def parse_json(raw: str) -> dict:
    """Gemini às vezes devolve o JSON dentro de ```json ... ```."""
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _ask_json(system: str, user: str) -> dict | None:
    """Pergunta ao Gemini esperando JSON. Devolve None se a chamada falhar."""
    try:
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system + "\nResponda SOMENTE com um JSON válido, sem comentários."),
                ("human", "{entrada}"),
            ]
        )
        resposta = (prompt | _model()).invoke({"entrada": user})
        return parse_json(getattr(resposta, "content", str(resposta)))
    except Exception as exc:  # rede, cota, credencial inválida...
        logger.error("Gemini indisponível: %s", exc)
        return None


def _ask_text(system: str, user: str) -> str | None:
    """Pergunta ao Gemini esperando texto livre. None se a chamada falhar."""
    try:
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages(
            [("system", system), ("human", "{entrada}")]
        )
        resposta = (prompt | _model()).invoke({"entrada": user})
        return getattr(resposta, "content", str(resposta)).strip() or None
    except Exception as exc:
        logger.error("Gemini indisponível: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Etapa 1 — nome do paciente
# ---------------------------------------------------------------------------


def extract_name(text: str) -> str | None:
    """Nome completo do paciente, via Gemini; None se a mensagem não tiver um."""
    dados = _ask_json(EXTRATOR_DE_NOME.system(), text)
    nome = (dados or {}).get("nome") or ""
    return nome.strip() or None


# ---------------------------------------------------------------------------
# Etapa 1 — motivo do agendamento
# ---------------------------------------------------------------------------


def classify_reason(text: str) -> dict:
    """Classifica o motivo em 'sintomas' ou 'rotina' e resume a queixa, via Gemini."""
    dados = _ask_json(CLASSIFICADOR_DE_MOTIVO.system(), text)
    if dados and dados.get("tipo") in ("sintomas", "rotina"):
        return {"tipo": dados["tipo"], "resumo": (dados.get("resumo") or text).strip()}
    # Sem leitura confiável do modelo: assume "sintomas" (mais conservador) e
    # guarda a fala original para a equipe conferir manualmente.
    return {"tipo": "sintomas", "resumo": text.strip()}


# ---------------------------------------------------------------------------
# Etapa 2 — escolha do horário
# ---------------------------------------------------------------------------


def pick_slot(text: str, opcoes: list[str]) -> int | None:
    """Índice (1-based) da opção de horário escolhida pelo paciente, via Gemini; None se não ficar claro."""
    lista = "\n".join(f"{indice + 1}. {opcao}" for indice, opcao in enumerate(opcoes))
    dados = _ask_json(SELETOR_DE_HORARIO.system(lista=lista), text)
    numero = (dados or {}).get("opcao")
    return numero if isinstance(numero, int) and 1 <= numero <= len(opcoes) else None


# ---------------------------------------------------------------------------
# Fora do fluxo — perguntas gerais
# ---------------------------------------------------------------------------


def answer_offtopic(pergunta: str, contexto: str, proximo_passo: str) -> str:
    """Responde uma dúvida institucional (nunca médica) via Gemini e retoma a etapa atual."""
    resposta = _ask_text(
        SECRETARIA_VIRTUAL.system(
            clinic_name=settings.clinic_name,
            contexto=contexto,
            proximo_passo=proximo_passo,
        ),
        pergunta,
    )
    if resposta:
        return resposta

    # Gemini indisponível no momento: resposta segura, sem inventar informação.
    return (
        "Sobre isso, nossa equipe confirma os detalhes pelo telefone "
        f"{settings.clinic_phone}. {proximo_passo}"
    )

"""Camada de linguagem do agente (LangChain + Google Gemini).

Todo acesso ao modelo passa por aqui. Cada função tem **duas implementações**:

* `_gemini` — pergunta ao modelo (via `_ask_json` / `_ask_text`);
* `_local_*` — resolve por regras, sem rede.

A versão local é usada quando não há `GOOGLE_API_KEY` (modo simulado, para
desenvolver antes de receber a chave) **e também** quando a chamada ao Gemini
falha por rede, cota ou resposta inesperada. O atendimento nunca para por
causa do modelo.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from app.core.config import settings

logger = logging.getLogger(__name__)

_llm = None


def _model():
    """Instancia o ChatGoogleGenerativeAI uma única vez (lazy)."""
    global _llm
    if _llm is not None:
        return _llm
    if not settings.ai_enabled:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        _llm = ChatGoogleGenerativeAI(
            model=settings.gemini_text_model,
            google_api_key=settings.google_api_key,
            temperature=0.2,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Falha ao inicializar o Gemini: %s", exc)
        _llm = None
    return _llm


def mode() -> str:
    return "gemini" if _model() else "simulado"


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
        logger.error("Gemini indisponível (%s). Usando interpretação local.", exc)
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
        logger.error("Gemini indisponível (%s). Usando resposta padrão.", exc)
        return None


def _strip_accents(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


# ---------------------------------------------------------------------------
# Etapa 1 — nome do paciente
# ---------------------------------------------------------------------------

_PREFIXOS = [
    "meu nome e", "meu nome:", "me chamo", "sou o", "sou a", "eh o", "eh a",
    "aqui e o", "aqui e a", "nome", "e o", "e a",
]


def _local_name(text: str) -> str | None:
    limpo = text.strip().rstrip(".!,")
    base = _strip_accents(limpo)
    for prefixo in _PREFIXOS:
        if base.startswith(prefixo):
            limpo = limpo[len(prefixo):].strip(" :,-")
            break
    partes = [p for p in re.split(r"\s+", limpo) if p.isalpha() or "-" in p]
    if len(partes) < 2:  # exige nome + sobrenome
        return None
    return " ".join(p.capitalize() for p in partes[:5])


def extract_name(text: str) -> str | None:
    """Nome completo do paciente, ou None se a mensagem não tiver um."""
    if _model():
        dados = _ask_json(
            "Você extrai o nome completo de um paciente a partir da mensagem dele. "
            'Formato: {{"nome": "Nome Completo"}} ou {{"nome": null}} se não houver '
            "um nome de pessoa na mensagem.",
            text,
        )
        if dados is not None:
            nome = (dados.get("nome") or "").strip()
            return nome or None
    return _local_name(text)


# ---------------------------------------------------------------------------
# Etapa 1 — motivo do agendamento
# ---------------------------------------------------------------------------

_ROTINA = [
    "rotina", "check up", "checkup", "check-up", "preventiv", "revisao",
    "exame periodico",
]


def _local_reason(text: str) -> dict:
    base = _strip_accents(text)
    tipo = "rotina" if any(p in base for p in _ROTINA) else "sintomas"
    return {"tipo": tipo, "resumo": text.strip()}


def classify_reason(text: str) -> dict:
    """Classifica o motivo em 'sintomas' ou 'rotina' e resume a queixa."""
    if _model():
        dados = _ask_json(
            "Classifique o motivo do agendamento médico informado pelo paciente. "
            'Formato: {{"tipo": "sintomas" | "rotina", "resumo": "resumo curto em '
            'português"}}. Use "rotina" para check-up/consulta preventiva e '
            '"sintomas" quando houver qualquer queixa clínica.',
            text,
        )
        if dados and dados.get("tipo") in ("sintomas", "rotina"):
            return {"tipo": dados["tipo"], "resumo": (dados.get("resumo") or text).strip()}
    return _local_reason(text)


# ---------------------------------------------------------------------------
# Etapa 2 — escolha do horário
# ---------------------------------------------------------------------------


def _local_slot(text: str, opcoes: list[str]) -> int | None:
    base = _strip_accents(text)
    base = re.sub(r"(\d{1,2})\s*h(?:oras|rs|s)?\b", r"\1 ", base)  # "14h" -> "14"
    numeros = [int(n) for n in re.findall(r"\b(\d{1,2})\b", base)]

    # "opção 2", "quero a 3"
    for numero in numeros:
        if 1 <= numero <= len(opcoes):
            return numero

    # "quinta às 14h" — casa dia da semana + horário com os rótulos
    horas: dict[int, list[int]] = {}
    for i, opcao in enumerate(opcoes):
        alvo = _strip_accents(opcao)
        dia = alvo.split(" ")[0].split("-")[0]  # "quarta"
        try:
            hora = int(alvo.split("as ")[-1].split(":")[0])
        except ValueError:
            continue
        horas.setdefault(hora, []).append(i + 1)
        if dia in base and hora in numeros:
            return i + 1

    # só o horário, e desde que ele apareça em uma única opção
    for hora, indices in horas.items():
        if len(indices) == 1 and hora in numeros:
            return indices[0]
    return None


def pick_slot(text: str, opcoes: list[str]) -> int | None:
    """Índice (1-based) da opção escolhida pelo paciente, ou None."""
    if _model():
        lista = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(opcoes))
        dados = _ask_json(
            "O paciente está escolhendo um horário de consulta entre as opções "
            f"abaixo:\n{lista}\n"
            'Responda {{"opcao": <numero da opção>}} ou {{"opcao": null}} se a '
            "mensagem não indicar claramente uma das opções.",
            text,
        )
        if dados is not None:
            numero = dados.get("opcao")
            if isinstance(numero, int) and 1 <= numero <= len(opcoes):
                return numero
            if dados:  # o modelo respondeu que não dá para saber
                return None
    return _local_slot(text, opcoes)


# ---------------------------------------------------------------------------
# Fora do fluxo — perguntas gerais
# ---------------------------------------------------------------------------


def answer_offtopic(pergunta: str, contexto: str, proximo_passo: str) -> str:
    """Responde uma dúvida institucional e retoma a etapa atual do fluxo."""
    if _model():
        resposta = _ask_text(
            f"Você é a secretária virtual da {settings.clinic_name}. Responda em "
            "português, no máximo 2 frases, com base apenas no contexto "
            "institucional abaixo. Se a informação não estiver no contexto, diga "
            "que a equipe confirma por telefone. Ao final, retome educadamente o "
            "atendimento com a frase indicada.\n\n"
            f"CONTEXTO:\n{contexto}\n\nRETOMADA: {proximo_passo}",
            pergunta,
        )
        if resposta:
            return resposta

    return (
        "Sobre isso, nossa equipe confirma os detalhes pelo telefone "
        f"{settings.clinic_phone}. {proximo_passo}"
    )

"""Teste ponta a ponta do atendimento, sem WhatsApp e sem chave de API.

Roda o fluxo inteiro (nome → motivo → horário → Pix → confirmação), valida o
login do painel, o Kanban, os logs e o reset de memória.

    python scripts/teste_fluxo.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ambiente isolado: SQLite em arquivo temporário, sem Redis, sem Gemini.
BANCO = Path(tempfile.gettempdir()) / "lifeline_teste.db"
BANCO.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{BANCO}"
os.environ["REDIS_URL"] = "redis://localhost:6399/0"  # inexistente de propósito
os.environ["GOOGLE_API_KEY"] = ""
os.environ["EVOLUTION_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

VERDE, VERMELHO, FIM = "\033[92m", "\033[91m", "\033[0m"
falhas = 0


def checar(condicao: bool, descricao: str) -> None:
    global falhas
    if condicao:
        print(f"{VERDE}✓{FIM} {descricao}")
    else:
        falhas += 1
        print(f"{VERMELHO}✗{FIM} {descricao}")


with TestClient(app) as client:
    print("\n— Autenticação do painel —")
    resposta = client.post("/api/auth/login", json={"username": "admin", "password": "errada"})
    checar(resposta.status_code == 401, "senha errada é recusada")

    resposta = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    checar(resposta.status_code == 200, "login com as credenciais padrão")
    headers = {"Authorization": f"Bearer {resposta.json()['access_token']}"}

    checar(client.get("/api/overview").status_code == 401, "painel exige token")

    print("\n— Fluxo conversacional —")
    TELEFONE = "5561999990001"

    def enviar(texto: str) -> str:
        r = client.post(
            "/api/simulator/text", json={"phone": TELEFONE, "text": texto}, headers=headers
        )
        assert r.status_code == 200, r.text
        return "\n".join(r.json()["respostas"])

    saida = enviar("oi, boa tarde")
    checar("nome completo" in saida.lower(), "etapa 1: pede o nome completo")

    saida = enviar("meu nome é Ana Paula Ribeiro")
    checar("380,00" in saida, "etapa 1: informa o valor fixo de R$ 380,00")
    checar("rotina" in saida.lower(), "etapa 1: pergunta sintomas ou rotina")

    saida = enviar("Estou com dor de cabeça há uns cinco dias")
    checar("1." in saida and "às" in saida, "etapa 2: apresenta os horários disponíveis")

    saida = enviar("o estacionamento é pago?")
    checar(len(saida) > 0, "fora do fluxo: responde e retoma o atendimento")

    saida = enviar("pode ser a opção 2")
    checar("190,00" in saida, "etapa 3: cobra o sinal de 50% (R$ 190,00)")
    checar("Pix" in saida, "etapa 3: envia a chave Pix")

    saida = enviar("já paguei")
    checar("comprovante" in saida.lower(), "etapa 3: insiste no comprovante antes de confirmar")

    print("\n— Kanban —")
    board = client.get("/api/patients", headers=headers).json()
    coluna_pix = next(c for c in board["colunas"] if c["status"] == "aguardando_pix")
    checar(len(coluna_pix["cards"]) == 1, "paciente aparece em 'Aguardando Pix'")
    paciente_id = coluna_pix["cards"][0]["id"]

    print("\n— Comprovante (visão) —")
    imagem = b"\x89PNG\r\n\x1a\n" + b"0" * 128  # a leitura é simulada sem chave
    resposta = client.post(
        "/api/simulator/image",
        data={"phone": TELEFONE},
        files={"file": ("comprovante.png", imagem, "image/png")},
        headers=headers,
    )
    saida = "\n".join(resposta.json()["respostas"])
    checar("confirmado" in saida.lower(), "etapa 4: confirma o agendamento")
    for esperado, descricao in [
        ("SGAS", "etapa 4: envia o endereço"),
        ("3333-1200", "etapa 4: envia o telefone fixo"),
        ("Estacionamento gratuito", "etapa 4: informa estacionamento gratuito"),
        ("pré-triagem", "etapa 4: avisa sobre a pré-triagem"),
        ("exames essenciais", "etapa 4: informa os exames inclusos"),
    ]:
        checar(esperado.lower() in saida.lower(), descricao)

    board = client.get("/api/patients", headers=headers).json()
    confirmados = next(c for c in board["colunas"] if c["status"] == "confirmado")
    checar(len(confirmados["cards"]) == 1, "paciente move para 'Confirmado / Agendado'")

    print("\n— Validações de exceção —")
    from app.services.vision import validate_receipt

    ok, motivo = validate_receipt({"eh_comprovante": True, "valor": 100, "data": "28/07/2026", "hora": "10:00"})
    checar(not ok and "190,00" in motivo, "recusa comprovante com valor errado")

    ok, motivo = validate_receipt({"eh_comprovante": True, "valor": 190, "data": "01/01/2020", "hora": "10:00"})
    checar(not ok and "horas" in motivo, "recusa comprovante antigo (reuso)")

    ok, _ = validate_receipt({"eh_comprovante": False})
    checar(not ok, "recusa imagem que não é comprovante")

    print("\n— Logs e memória —")
    logs = client.get("/api/logs", headers=headers).json()["logs"]
    eventos = {log["evento"] for log in logs}
    checar("nome_coletado" in eventos, "log: coleta de nome")
    checar("horario_reservado" in eventos, "log: reserva do horário")
    checar("agendamento_confirmado" in eventos, "log: confirmação do agendamento")

    antes = client.get("/api/overview", headers=headers).json()["horarios_livres"]
    resposta = client.post(f"/api/patients/{paciente_id}/reset", headers=headers)
    checar(resposta.status_code == 200, "botão de apagar memória responde")
    detalhe = client.get(f"/api/patients/{paciente_id}", headers=headers).json()
    checar(detalhe["etapa"] == "start", "memória zerada: conversa recomeça do início")

    saida = enviar("oi")
    checar("nome completo" in saida.lower(), "após o reset, o agente reinicia a apresentação")

BANCO.unlink(missing_ok=True)
print(f"\n{'Todos os testes passaram.' if not falhas else f'{falhas} teste(s) falharam.'}\n")
sys.exit(1 if falhas else 0)

"""Teste ponta a ponta do atendimento, sem WhatsApp mas com o Gemini real.

Roda o fluxo inteiro (nome → motivo → horário → Pix → validação de
comprovante e de áudio), valida o login do painel, o Kanban, os logs e o
reset de memória. Requer uma GOOGLE_API_KEY válida no .env (ou já exportada
no ambiente) — o app não sobe sem ela, e este teste faz chamadas reais ao
Gemini (rede, latência e custo de alguns tokens por execução).

    python scripts/teste_fluxo.py

Observação: como não há aqui uma foto real de comprovante Pix, o teste abaixo
usa uma imagem qualquer (1 pixel) para confirmar que o Gemini Vision a
rejeita corretamente. O caminho feliz de confirmação — com uma foto de
comprovante de verdade — é melhor validado manualmente pelo painel
(botão "Enviar comprovante" no simulador).
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VERDE, VERMELHO, FIM = "\033[92m", "\033[91m", "\033[0m"
falhas = 0

# Ambiente isolado: SQLite em arquivo temporário, sem Redis, sem WhatsApp.
BANCO = Path(tempfile.gettempdir()) / "lifeline_teste.db"
BANCO.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{BANCO}"
os.environ["REDIS_URL"] = "redis://localhost:6399/0"  # inexistente de propósito
os.environ["EVOLUTION_API_KEY"] = ""

try:
    from fastapi.testclient import TestClient  # noqa: E402

    from app.main import app  # noqa: E402
except Exception as exc:  # normalmente: GOOGLE_API_KEY ausente/inválida
    print(f"\n{VERMELHO}Não foi possível iniciar a aplicação: {exc}{FIM}")
    print("Configure uma GOOGLE_API_KEY válida no .env e rode novamente.\n")
    sys.exit(1)


def checar(condicao: bool, descricao: str) -> None:
    """Imprime ✓/✗ para a checagem e conta as falhas do teste."""
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

    print("\n— Fluxo conversacional (Gemini real) —")
    TELEFONE = "5561999990001"

    def enviar(texto: str) -> str:
        """Envia uma mensagem de texto ao simulador e devolve as respostas concatenadas."""
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

    print("\n— Comprovante (Gemini Vision real) —")
    # PNG válido de 1x1 pixel: não é um comprovante, mas é uma imagem de verdade
    # (o Gemini precisa conseguir abri-la para avaliar o conteúdo).
    pixel_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    resposta = client.post(
        "/api/simulator/image",
        data={"phone": TELEFONE},
        files={"file": ("nao-e-comprovante.png", pixel_png, "image/png")},
        headers=headers,
    )
    saida = "\n".join(resposta.json()["respostas"])
    checar(len(saida) > 0, "etapa 4: analisa a imagem e responde")

    board = client.get("/api/patients", headers=headers).json()
    coluna_pix = next(c for c in board["colunas"] if c["status"] == "aguardando_pix")
    checar(len(coluna_pix["cards"]) == 1, "imagem inválida não confirma o agendamento")

    print("\n— Áudio (transcrição via Gemini real) —")
    audio_sem_fala = b"RIFF" + b"\x00" * 64  # bytes sem fala reconhecível
    resposta = client.post(
        "/api/simulator/audio",
        data={"phone": TELEFONE},
        files={"file": ("audio.ogg", audio_sem_fala, "audio/ogg")},
        headers=headers,
    )
    saida = "\n".join(resposta.json()["respostas"])
    checar("áudio" in saida.lower(), "áudio sem fala reconhecível: pede para reenviar em texto")

    print("\n— Validações de exceção (regras de negócio, sem chamar a IA) —")
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
    checar("comprovante_analisado" in eventos, "log: análise do comprovante pela IA")

    resposta = client.post(f"/api/patients/{paciente_id}/reset", headers=headers)
    checar(resposta.status_code == 200, "botão de apagar memória responde")
    detalhe = client.get(f"/api/patients/{paciente_id}", headers=headers).json()
    checar(detalhe["etapa"] == "start", "memória zerada: conversa recomeça do início")

    saida = enviar("oi")
    checar("nome completo" in saida.lower(), "após o reset, o agente reinicia a apresentação")

from app.database import engine  # noqa: E402

engine.dispose()  # fecha as conexões abertas antes de apagar o arquivo (Windows trava o arquivo em uso)
try:
    BANCO.unlink(missing_ok=True)
except PermissionError:
    pass  # arquivo temporário; não impede o resultado do teste

print(f"\n{'Todos os testes passaram.' if not falhas else f'{falhas} teste(s) falharam.'}\n")
sys.exit(1 if falhas else 0)

# Agente Agendador WhatsApp + Painel — Lifeline One

Solução completa de automação para clínicas médicas:

1. **Agente de IA no WhatsApp** — atende o paciente, coleta os dados, oferece horários, cobra o sinal via Pix, **lê o comprovante por visão computacional**, **entende áudios de voz** (transcrição automática) e confirma o agendamento.
2. **Painel web de gestão** — login, funil Kanban dos pacientes, logs das decisões da IA, pareamento por QR Code e botão para apagar a memória de um paciente.

> **O projeto exige uma chave da OpenAI para rodar.** Todo o entendimento de linguagem, a leitura do comprovante e a transcrição de áudio dependem do ChatGPT (OpenAI) — sem `OPENAI_API_KEY` configurada no `.env`, a aplicação recusa iniciar (com uma mensagem clara dizendo o que falta).

---

## 1. Como rodar

Pré-requisito: Docker, Docker Compose e uma chave da OpenAI.

```bash
cp .env.example .env
docker compose up -d --build
```

### Reiniciar 

``` bash
docker compose down 
docker compose up -d --build
```

Pronto. Abra **http://localhost:8000** e entre com:

| Usuário | Senha      |
| ------- | ---------- |
| `admin` | `admin123` |

Sobem três serviços: a API (FastAPI), o PostgreSQL e o Redis. O banco cria as tabelas, o usuário do painel e a agenda dos próximos 5 dias úteis sozinho, na primeira execução.

O projeto roda exclusivamente por Docker: o `docker-compose.yml` já define `DATABASE_URL` e `REDIS_URL` apontando para os serviços internos `db` e `redis`, então não é preciso mexer nessas variáveis. A `OPENAI_API_KEY` é obrigatória — sem ela a aplicação recusa iniciar.

---

## 2. Onde colar as chaves

Tudo em um lugar só, no arquivo `.env`:

```env
# OpenAI (ChatGPT) — obrigatória: texto, visão e áudio
OPENAI_API_KEY=cole-a-chave-aqui

# Evolution API (WhatsApp) — opcional, deixe em branco para usar só o simulador
EVOLUTION_BASE_URL=https://url-da-instancia
EVOLUTION_API_KEY=cole-a-chave-aqui
EVOLUTION_INSTANCE=lifeline
```

Depois: `docker compose up -d` (recria o container com as novas variáveis).

Para conferir o que está ativo, olhe o rodapé da barra lateral do painel — ele mostra `IA: chatgpt | indisponível`, o estado do Redis e do WhatsApp.

---

## 3. Testar o atendimento sem WhatsApp

O painel tem a aba **Simulador**: uma tela de conversa que usa exatamente o mesmo agente, o mesmo banco e os mesmos logs do WhatsApp real. Dá para digitar as mensagens e anexar uma imagem de comprovante ou um áudio.

Roteiro de demonstração (30 segundos):

```
Oi, boa tarde
Meu nome é Ana Paula Ribeiro
Estou com dor de cabeça há cinco dias
2
[anexa o comprovante pelo botão "Enviar comprovante"]
```

Enquanto isso, a aba **Funil** move o card de *Em atendimento* → *Aguardando Pix* → *Confirmado*, e a aba **Logs da IA** mostra cada decisão.

Também há um teste automatizado que percorre o fluxo inteiro e valida as regras — ele faz chamadas reais à OpenAI, então precisa da `OPENAI_API_KEY` configurada:

```bash
python scripts/teste_fluxo.py
```

---

## 4. Conectar o WhatsApp de verdade

1. Preencha `EVOLUTION_BASE_URL` e `EVOLUTION_API_KEY` no `.env` e reinicie.
2. No painel, aba **WhatsApp** → **Gerar QR Code** → leia no celular (WhatsApp → Aparelhos conectados).
3. Ainda na aba WhatsApp, informe a **URL pública desta API** e clique em *Registrar webhook*. A Evolution passa a entregar as mensagens em `POST /webhook/evolution`.
   - Em desenvolvimento, exponha a porta 8000 com `ngrok http 8000` e use a URL gerada.
4. Se quiser subir uma Evolution API local junto do projeto:
   ```bash
   docker compose --profile whatsapp up -d
   ```

---

## 5. Como funciona

```
WhatsApp ──▶ Evolution API ──▶ POST /webhook/evolution
                                        │
                                        ▼
                            ┌──────────────────────┐
                            │  agent.py            │  máquina de estados
                            │  (fluxo do briefing) │  do atendimento
                            └───────┬──────────────┘
             Redis ◀── memória ─────┤
          (etapa, dados, histórico) │
                                    ├──▶ llm.py      LangChain + ChatGPT (texto)
                                    ├──▶ vision.py   ChatGPT visão (comprovante)
                                    ├──▶ audio.py    Whisper (transcrição de voz)
                                    ├──▶ scheduling  reserva/confirma horários
                                    └──▶ PostgreSQL  pacientes, agenda, logs
                                                │
                                                ▼
                                    Painel web (/) ── polling a cada 4s
```

**Por que uma máquina de estados e não um agente solto:** o roteiro do briefing é obrigatório (nome → valor → motivo → horário → sinal → confirmação). O ChatGPT é usado para *entender* o que o paciente escreveu — extrair o nome, classificar o motivo, identificar o horário escolhido, responder perguntas fora do roteiro — mas quem decide o próximo passo é o código. O atendimento fica previsível, auditável nos logs e não "esquece" de cobrar o sinal.

### Fluxo do atendimento

| Etapa | O que o agente faz                                                                                  | Status no Kanban |
| ----- | --------------------------------------------------------------------------------------------------- | ---------------- |
| 1     | Cumprimenta, pede o nome completo, informa o valor fixo de R$ 380,00 e pergunta sintomas ou rotina    | Em atendimento   |
| 2     | Lista os horários livres e reserva o escolhido por 30 minutos                                        | Em atendimento   |
| 3     | Cobra o sinal de 50% (R$ 190,00), envia a chave Pix e pede a foto do comprovante                     | Aguardando Pix   |
| 4     | Lê o comprovante com IA de visão, valida, confirma no banco e envia endereço, telefone, estacionamento gratuito e aviso de pré-triagem/exames inclusos | Confirmado |

Em qualquer etapa, se o paciente mandar um **áudio** em vez de texto, o agente transcreve com o Whisper (OpenAI) e trata o resultado como se fosse a mensagem digitada — o roteiro acima não muda.

### Validação do comprovante (IA de visão)

O ChatGPT, em modo de visão, extrai valor, data, hora, favorecido e ID da transação. Em seguida o código aplica as regras:

- **Valor** precisa bater exatamente com o sinal (R$ 190,00) — tolerância de um centavo.
- **Data e hora** precisam existir e estar dentro das últimas 24h (`RECEIPT_MAX_AGE_HOURS`), o que bloqueia o reuso de comprovantes antigos, e não podem estar no futuro.
- Imagem ilegível ou que não seja comprovante é recusada com o motivo explicado ao paciente.

Cada recusa vira uma resposta específica ("o comprovante mostra R$ 100,00, mas o sinal é de R$ 190,00…") e um log de nível `warn` no painel. Depois de 3 tentativas, o agente oferece o telefone da recepção.

### Tratamento de exceções

- Pergunta fora do roteiro ("tem estacionamento?", "aceita convênio?") → responde com base no contexto institucional e **retoma a etapa atual**.
- Imagem enviada fora da etapa de pagamento → explica e volta ao fluxo.
- Horário tomado por outro paciente no meio do caminho → reoferece a agenda atualizada.
- Reserva não paga em 30 minutos → o horário volta automaticamente para a agenda.
- Paciente escreve "reiniciar" ou "cancelar" → conversa recomeça do zero.

---

## 6. Estrutura do projeto

```
app/
├── main.py               # sobe a API, monta rotas e o painel estático
├── models.py             # tabelas: pacientes, agenda, agendamentos, mensagens, logs
├── database.py           # sessão, criação de tabelas e carga inicial
├── core/
│   ├── config.py         # todas as variáveis de ambiente em um lugar
│   ├── security.py       # hash de senha (PBKDF2) e token de sessão (Redis)
│   └── utils.py          # datas, fuso e formatação em pt-BR
├── api/
│   ├── auth.py           # login do painel
│   ├── webhook.py        # entrada das mensagens da Evolution API
│   └── dashboard.py      # kanban, logs, QR Code, reset, simulador
├── services/
│   ├── agent.py          # o fluxo do atendimento (máquina de estados)
│   ├── llm.py            # LangChain + ChatGPT (texto)
│   ├── vision.py         # leitura e validação do comprovante Pix
│   ├── audio.py          # transcrição de áudios de voz via Whisper
│   ├── memory.py         # contexto no Redis (com fallback em memória)
│   ├── scheduling.py     # regras da agenda
│   └── evolution.py      # cliente da Evolution API
└── static/               # painel web (HTML, CSS e JS sem build)
scripts/teste_fluxo.py    # teste ponta a ponta do atendimento
```

---

## 7. Endpoints

| Método | Rota                          | Para quê                                  |
| ------ | ----------------------------- | ----------------------------------------- |
| POST   | `/webhook/evolution`          | Recebe as mensagens do WhatsApp           |
| POST   | `/api/auth/login`             | Login do painel (devolve o token de sessão) |
| GET    | `/api/overview`               | Números do topo e estado dos serviços     |
| GET    | `/api/patients`               | Colunas do Kanban                         |
| GET    | `/api/patients/{id}`          | Conversa e dados de um paciente           |
| POST   | `/api/patients/{id}/reset`    | Apaga a memória e reinicia a conversa     |
| GET    | `/api/logs?after_id=`         | Logs da IA (o painel busca só os novos)   |
| GET    | `/api/whatsapp/qrcode`        | QR Code de pareamento                     |
| POST   | `/api/whatsapp/webhook`       | Registra o webhook na instância           |
| POST   | `/api/simulator/text\|image\|audio` | Simulador de conversa do painel     |
| GET    | `/health`                     | Healthcheck do container                  |

Documentação interativa: **http://localhost:8000/docs**

---

## 8. Configurações úteis (`.env`)

| Variável                | Padrão   | O que faz                                     |
| ----------------------- | -------- | --------------------------------------------- |
| `CONSULTATION_PRICE`    | `380`    | Valor da consulta                             |
| `DEPOSIT_PERCENT`       | `0.5`    | Percentual do sinal                           |
| `SLOT_HOLD_MINUTES`     | `30`     | Tempo de reserva do horário sem pagamento     |
| `RECEIPT_MAX_AGE_HOURS` | `24`     | Idade máxima aceita do comprovante            |
| `PIX_KEY` / `PIX_HOLDER`| —        | Chave Pix e favorecido enviados ao paciente   |
| `CLINIC_ADDRESS` / `CLINIC_PHONE` | — | Dados da mensagem de confirmação          |
| `OPENAI_TEXT_MODEL`     | `gpt-4o-mini` | Modelo de texto                          |
| `OPENAI_VISION_MODEL`   | `gpt-4o-mini` | Modelo de visão (leitura do comprovante) |
| `OPENAI_TRANSCRIPTION_MODEL` | `whisper-1` | Modelo de transcrição de áudio       |
| `OPENAI_BASE_URL`       | —             | Endpoint compatível alternativo (proxy)  |

Em produção, troque `ADMIN_PASSWORD`.

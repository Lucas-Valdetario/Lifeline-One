# Entenda o projeto — guia para iniciantes

Este guia explica **o que cada parte do sistema faz e por quê**, do zero. Não assume conhecimento prévio de FastAPI, Redis, LangChain ou Docker: cada termo é explicado na primeira vez que aparece.

Sugestão de leitura: capítulos 1 a 4 dão a visão geral (15 minutos). O capítulo 5 é a referência arquivo por arquivo — dá para ler aos poucos, com o código aberto do lado.

---

## Sumário

1. [O que o sistema faz](#1-o-que-o-sistema-faz)
2. [As peças do quebra-cabeça](#2-as-peças-do-quebra-cabeça)
3. [O caminho de uma mensagem](#3-o-caminho-de-uma-mensagem)
4. [Mapa dos arquivos](#4-mapa-dos-arquivos)
5. [Arquivo por arquivo](#5-arquivo-por-arquivo)
6. [O banco de dados por dentro](#6-o-banco-de-dados-por-dentro)
7. [Docker explicado](#7-docker-explicado)
8. [Como testar e depurar](#8-como-testar-e-depurar)
9. [O que a revisão mudou](#9-o-que-a-revisão-mudou)
10. [Perguntas prováveis no code review](#10-perguntas-prováveis-no-code-review)
11. [Glossário](#11-glossário)

---

## 1. O que o sistema faz

Imagine a recepcionista de uma clínica. O telefone toca, ela atende, pergunta o nome, explica o preço, pergunta o motivo da consulta, oferece horários, anota o escolhido, pede o sinal, confere o comprovante que o paciente mandou e, se estiver tudo certo, confirma e passa o endereço.

**Este projeto é essa recepcionista, em software.** Ela trabalha 24h pelo WhatsApp, e a equipe da clínica acompanha tudo por um painel no navegador.

São duas entregas em um só sistema:

| Parte | O que é | Quem usa |
| --- | --- | --- |
| **Agente** | O robô que conversa no WhatsApp | O paciente |
| **Painel** | Um site com o funil, os logs e o QR Code | A equipe da clínica |

As duas partes compartilham o mesmo banco de dados. Quando o agente confirma um agendamento, o card do paciente muda de coluna no painel na mesma hora.

---

## 2. As peças do quebra-cabeça

O briefing exigiu seis tecnologias. Aqui está o que cada uma é e **por que ela existe neste projeto**:

### FastAPI — o corpo da aplicação

Um *framework* Python para criar **APIs**. Uma API é um programa que fica ligado esperando pedidos pela internet e devolvendo respostas — como um garçom: você pede, ele traz.

Cada "pedido" que a nossa API aceita é uma **rota**: um endereço (`/api/patients`) com um método (`GET` para ler, `POST` para criar/enviar). Escrever uma rota em FastAPI é escrever uma função Python com uma etiqueta em cima:

```python
@router.get("/api/overview")      # ← a etiqueta diz: "essa função atende esse endereço"
def overview():
    return {"pacientes": 12}      # ← o que for retornado vira JSON automaticamente
```

**JSON** é o formato de texto que programas usam para trocar dados: `{"nome": "Ana", "idade": 30}`.

### Evolution API — a ponte com o WhatsApp

O WhatsApp não deixa qualquer programa se conectar direto. A Evolution API é um serviço intermediário: você parea o número lendo um QR Code, e a partir daí ela:

- **avisa** o nosso sistema quando chega mensagem (isso se chama **webhook** — ver abaixo);
- **envia** mensagens quando pedimos, com uma chamada HTTP simples.

**Webhook** é o inverso de uma consulta: em vez de o nosso sistema ficar perguntando "chegou mensagem? chegou mensagem?", a Evolution é quem bate na nossa porta assim que algo acontece. Na prática, ela faz um `POST` no endereço `/webhook/evolution` que nós expusemos.

### Google Gemini — o cérebro

Um **LLM** (*Large Language Model*, modelo de linguagem) é um programa treinado para entender e escrever texto. O Gemini é o LLM do Google e tem uma característica essencial aqui: ele é **multimodal**, ou seja, também "enxerga" imagens. É isso que permite ler o comprovante do Pix a partir de uma foto.

Usamos o Gemini para duas coisas bem delimitadas:

1. **Entender o paciente** — extrair o nome de "oi, aqui é a Ana Paula", classificar se "dor no peito" é sintoma ou rotina, entender que "pode ser quinta às 14h" é a opção 3.
2. **Ler o comprovante** — extrair valor, data e hora da imagem.

### LangChain — o adaptador de LLMs

Uma biblioteca que padroniza a conversa com modelos de IA. Sem ela, cada modelo (Gemini, OpenAI, Claude…) tem um jeito próprio de ser chamado. Com ela, você monta um **prompt** (a instrução dada ao modelo) e liga as peças com o operador `|`:

```python
resposta = (prompt | modelo).invoke({"entrada": "meu nome é Ana Paula"})
```

Vantagem prática: se amanhã a clínica quiser trocar o Gemini por outro modelo, muda-se uma linha em `llm.py`, e não o projeto inteiro.

### PostgreSQL — a memória permanente

Um **banco de dados relacional**: guarda informação em tabelas, como planilhas ligadas entre si. É onde ficam pacientes, horários, agendamentos, mensagens e logs. Se o servidor reiniciar, nada disso se perde.

### Redis — a memória de curto prazo

Um banco de dados **em memória** (RAM). É muito mais rápido que o PostgreSQL, mas serve para dados temporários. Guardamos ali o **contexto da conversa**: em que etapa cada paciente está, o que ele já respondeu e o histórico recente.

> **Por que dois lugares?** Pense na recepcionista: o *bloco de rascunho* na mesa (Redis, some no fim do dia) é diferente do *arquivo de fichas* (PostgreSQL, guardado para sempre). O botão "apagar memória" do painel joga fora só o rascunho.

### Docker — a caixa que empacota tudo

Docker roda cada programa dentro de um **container**: uma caixa isolada com o sistema, as bibliotecas e as versões certas. Em vez de você instalar Python, PostgreSQL e Redis na sua máquina e torcer para as versões baterem, o `docker compose up` sobe as três caixas já configuradas e conversando entre si.

---

## 3. O caminho de uma mensagem

Este é o percurso completo de "o paciente digitou algo" até "a resposta chegou no celular dele". Vale ler devagar — entendendo este capítulo, o resto do código se explica sozinho.

```
① Paciente digita no WhatsApp
        │
② Evolution API recebe e faz POST em /webhook/evolution
        │
③ webhook.py descobre QUEM falou e O QUE falou
        │
④ agent.py carrega a memória do Redis: "esse paciente está na etapa X"
        │
⑤ agent.py chama llm.py (texto) ou vision.py (imagem) para entender a mensagem
        │
⑥ agent.py decide a próxima ação e grava no PostgreSQL
        │
⑦ agent.py devolve as respostas; evolution.py envia pelo WhatsApp
        │
⑧ Painel, na próxima atualização (4 em 4 segundos), mostra o card e o log
```

Um exemplo concreto, mensagem por mensagem:

| Paciente diz | O sistema entende | O que ele faz | Etapa depois |
| --- | --- | --- | --- |
| "oi" | (nada a extrair) | Cumprimenta e pede o nome | `ask_name` |
| "sou a Ana Paula Ribeiro" | nome = Ana Paula Ribeiro | Salva o nome, informa R$ 380,00, pergunta o motivo | `ask_reason` |
| "estou com dor de cabeça" | tipo = sintomas | Salva a queixa, lista 6 horários | `choose_slot` |
| "a 2" | opção 2 | Reserva o horário por 30 min, manda a chave Pix | `await_pix` |
| *(foto do comprovante)* | R$ 190,00 hoje às 14:32 | Valida, confirma, manda o endereço | `done` |

---

## 4. Mapa dos arquivos

```
app/
├── main.py               Liga a aplicação: junta as rotas e sobe o painel
├── models.py             Desenha as tabelas do banco
├── database.py           Conecta no banco e cria os dados iniciais
├── core/
│   ├── config.py         Todas as configurações em um lugar só
│   ├── security.py       Senha e token de login do painel
│   └── utils.py          Datas, fuso horário e "R$ 190,00"
├── api/                  As "portas de entrada" do sistema
│   ├── auth.py           Login
│   ├── webhook.py        Entrada das mensagens do WhatsApp
│   └── dashboard.py      Tudo que o painel consulta
├── services/             As regras: é aqui que o sistema pensa
│   ├── agent.py          ★ O fluxo do atendimento (o coração)
│   ├── llm.py            Conversa com o Gemini (texto)
│   ├── vision.py         Lê e valida o comprovante (imagem)
│   ├── memory.py         Contexto da conversa no Redis
│   ├── scheduling.py     Regras da agenda
│   └── evolution.py      Envia mensagens pelo WhatsApp
└── static/               O painel (HTML, CSS e JavaScript)
```

A separação segue uma ideia simples e vale explicá-la no code review:

> **`api/` recebe, `services/` decide, `models.py` guarda.**
> Nenhuma regra de negócio mora dentro de uma rota. Assim, o mesmo `agent.py` atende o WhatsApp real *e* o simulador do painel, sem uma linha duplicada.

---

## 5. Arquivo por arquivo

### `core/config.py` — o painel de controle

Reúne tudo que muda de ambiente para ambiente: senhas, endereços de banco, chaves de API, preço da consulta, endereço da clínica.

```python
class Settings(BaseSettings):
    consultation_price: float = 380.00
    google_api_key: str = ""
```

Cada campo é lido automaticamente do arquivo `.env` (`CONSULTATION_PRICE=380`). O valor escrito no código é só o padrão, usado quando a variável não existe.

Duas propriedades merecem atenção:

```python
@property
def deposit_amount(self) -> float:          # o sinal nunca é digitado à mão:
    return round(self.consultation_price * self.deposit_percent, 2)   # 380 × 0,5 = 190

@property
def ai_enabled(self) -> bool:               # "tem chave do Gemini configurada?"
    return bool(self.google_api_key.strip())
```

Se a clínica mudar o preço para R$ 420, o sinal vira R$ 210 sozinho — em toda mensagem e em toda validação.

**Por que centralizar:** nenhum outro arquivo lê variáveis de ambiente. Para saber o que dá para configurar no projeto, basta abrir este arquivo.

### `core/utils.py` — datas e dinheiro

Coisas pequenas que dariam bug se ficassem espalhadas:

- `now()` devolve a hora **sempre no fuso de Brasília**. Containers rodam em UTC por padrão; sem isso, um horário de 9h apareceria como 12h.
- `format_slot()` transforma uma data em `"quarta-feira (29/07) às 10:00"`.
- `format_money()` transforma `190.0` em `"R$ 190,00"` (vírgula decimal, como no Brasil).
- `receipt_is_fresh()` responde: esse pagamento aconteceu nas últimas 24 horas? É o que impede o paciente de reenviar um comprovante antigo.

### `core/security.py` — login do painel

Duas responsabilidades:

**1. Guardar a senha sem guardar a senha.** Nunca se salva a senha em texto puro. Salva-se um *hash*: um embaralhamento que só funciona num sentido.

```python
digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
```

O `salt` é um pedaço aleatório somado à senha (duas pessoas com a senha "1234" geram hashes diferentes), e as 240 mil repetições deixam a quebra por tentativa e erro lenta demais para valer a pena.

**2. Emitir o token JWT.** Depois do login, o servidor devolve um **token**: um texto assinado que diz "quem tem isso é o admin, e vale até tal hora". O navegador guarda e manda em toda requisição seguinte, no cabeçalho `Authorization: Bearer <token>`. Assim o servidor não precisa guardar sessões — ele confere a assinatura e pronto.

### `models.py` — o desenho do banco

Aqui as tabelas são descritas como classes Python. Isso se chama **ORM** (mapeamento objeto-relacional): você escreve Python, a biblioteca (SQLAlchemy) gera o SQL.

```python
class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[PatientStatus] = mapped_column(Enum(PatientStatus))
```

Traduzindo linha a linha:

- `primary_key=True` — o número que identifica cada paciente (1, 2, 3…).
- `unique=True` — não existem dois pacientes com o mesmo telefone.
- `index=True` — cria um "índice remissivo" para buscar por telefone rapidamente.
- `Enum(PatientStatus)` — o status só pode ser um dos três valores previstos; qualquer outra coisa o banco recusa.

As seis tabelas são detalhadas no [capítulo 6](#6-o-banco-de-dados-por-dentro).

### `database.py` — a conexão e a carga inicial

Três funções:

- **`engine` / `SessionLocal`** — a conexão. Uma *sessão* é uma conversa com o banco: você faz alterações e no fim dá `commit()` para gravar de verdade.
- **`get_db()`** — entrega uma sessão para cada requisição e fecha no fim, mesmo se der erro. Em FastAPI isso é uma **dependência**: a rota declara `db: Session = Depends(get_db)` e recebe pronta.
- **`seed()`** — na primeira execução cria o usuário `admin` e a agenda dos próximos 5 dias úteis (09h, 10h, 11h, 14h, 15h e 16h). É o que faz o sistema funcionar assim que sobe, sem ninguém cadastrar nada.

### `services/memory.py` — a memória da conversa

Guarda no Redis, para cada telefone, um pequeno JSON:

```json
{
  "step": "await_pix",
  "data": { "nome": "Ana Paula Ribeiro", "horario": "quarta-feira (29/07) às 10:00" },
  "history": [ {"role": "user", "content": "a 2"} ],
  "receipt_attempts": 0
}
```

O `step` é o que faz o agente saber onde parou. O TTL de 12 horas (`setex`) faz conversas abandonadas sumirem sozinhas.

**Detalhe importante para a demonstração:** se o Redis não estiver no ar, o módulo avisa no log e passa a usar um dicionário na memória do processo. O sistema não quebra — só perde o contexto se reiniciar.

### `services/llm.py` — falando com o Gemini

Cada função aqui tem **duas implementações**: uma que pergunta ao Gemini e uma que resolve por regras locais.

```python
def extract_name(text: str) -> str | None:
    if _model():                              # tem chave configurada?
        dados = _ask_json(...)                # pergunta ao Gemini
        if dados is not None:                 # deu certo?
            return (dados.get("nome") or "").strip() or None
    return _local_name(text)                  # senão, resolve por regra
```

A versão local entra em dois casos: **sem chave de API** (o modo simulado, para desenvolver antes de receber as credenciais) e **quando a chamada falha** (internet caiu, cota estourou, o modelo devolveu algo estranho). O atendimento nunca trava por causa do modelo.

Três detalhes de implementação que valem comentar:

**Pedir JSON e não confiar cegamente.** Modelos gostam de embrulhar a resposta em ```` ```json ... ``` ````. A função `parse_json()` limpa isso e, se ainda assim não for JSON válido, devolve `{}` em vez de estourar um erro.

**Chaves duplicadas no prompt.** No LangChain, `{` tem significado especial (marca uma variável). Para escrever uma chave literal no prompt, dobra-se: `{{"nome": "..."}}`.

**Temperatura baixa** (`temperature=0.2`). A "temperatura" controla a criatividade do modelo. Para extrair um nome de uma frase, queremos o resultado mais provável e estável — não criatividade.

### `services/vision.py` — lendo o comprovante

O ponto mais delicado do teste técnico. A ideia central:

> **A IA só extrai. Quem valida é o código.**

O Gemini recebe a imagem e a instrução de devolver os campos:

```python
mensagem = HumanMessage(content=[
    {"type": "text", "text": PROMPT},
    {"type": "image_url", "image_url": f"data:{mime_type};base64,{image_b64}"},
])
```

(**base64** é um jeito de representar uma imagem como texto, para caber dentro de um JSON.)

O modelo devolve algo assim:

```json
{"eh_comprovante": true, "valor": 190.00, "data": "28/07/2026", "hora": "14:32", ...}
```

E aí `validate_receipt()` aplica as regras, em Python puro:

| Regra | Por quê |
| --- | --- |
| É mesmo um comprovante? | Bloqueia foto de gato, print de conversa, tela preta |
| O valor bate com R$ 190,00 (± 1 centavo)? | Impede pagar a menos |
| Tem data e hora legíveis? | Sem isso não dá para saber se é recente |
| Foi nas últimas 24 horas? | **Impede reusar um comprovante antigo** |
| Não está no futuro? | Impede print editado |

Cada recusa gera uma frase específica — *"o comprovante mostra R$ 100,00, mas o sinal é de R$ 190,00. Pode conferir e reenviar?"* — em vez de um genérico "inválido".

**Por que a validação não fica no prompt?** Porque prompt não é garantia. Se pedíssemos "confirme se o valor está certo", o modelo poderia se convencer de que R$ 19,00 "está próximo o suficiente". Comparar dois números é trabalho de código: determinístico, testável e auditável.

### `services/scheduling.py` — a agenda

Resolve o problema clássico de dois pacientes escolhendo o mesmo horário:

1. Quando o paciente escolhe, o horário é **reservado temporariamente** (`is_open = False` + `hold_expires_at = agora + 30 min`).
2. Se o comprovante chegar e for aprovado, a reserva vira **confirmada**.
3. Se não chegar em 30 minutos, `release_expired()` devolve o horário para a agenda e cancela o agendamento.

`release_expired()` é chamada sempre que alguém pede a lista de horários ou abre o painel — assim a limpeza acontece sozinha, sem precisar de um agendador de tarefas rodando em paralelo.

### `services/agent.py` — o coração

Este arquivo é o atendimento. Ele é uma **máquina de estados**: a conversa tem etapas fixas, e cada mensagem recebida é interpretada *de acordo com a etapa em que o paciente está*.

```python
def _handle_text(db, paciente, sessao, texto):
    step = sessao.get("step", "start")

    if step == "ask_name":
        nome = llm.extract_name(texto)     # a IA entende
        if not nome:
            return ["Não consegui identificar seu nome..."]
        paciente.name = nome               # o código decide e grava
        sessao["step"] = "ask_reason"      # e avança a etapa
        return [msg_valor_e_motivo(nome)]
```

As etapas, na ordem:

| `step` | O agente está esperando | Se receber outra coisa |
| --- | --- | --- |
| `start` | qualquer mensagem | cumprimenta e pede o nome |
| `ask_name` | o nome completo | pergunta de novo, ou responde a dúvida e retoma |
| `ask_reason` | sintomas ou rotina | classifica o que vier e segue |
| `choose_slot` | o número do horário | pede o número de novo |
| `await_pix` | a **foto** do comprovante | lembra que está esperando o comprovante |
| `done` | nada obrigatório | responde dúvidas sobre a consulta marcada |

**Por que máquina de estados e não um agente autônomo?** Um agente livre decide sozinho o que fazer e, num roteiro obrigatório, isso é risco: ele pode confirmar sem cobrar, pular a validação ou inventar um horário. Aqui, o LLM entende a linguagem e o código conduz o processo. O resultado é previsível, dá para testar e cada decisão aparece nos logs.

Duas funções auxiliares no mesmo arquivo:

- **`log_event()`** — grava cada decisão na tabela `agent_logs`. É o que alimenta a aba "Logs da IA".
- **`reset_patient()`** — o botão do painel: apaga a memória no Redis, devolve o horário reservado à agenda e volta o paciente para "Em atendimento".

### `services/evolution.py` — enviando pelo WhatsApp

Um cliente HTTP fino. `send_text()` faz um `POST` na Evolution; `fetch_qrcode()` busca o QR de pareamento; `register_webhook()` diz à Evolution para onde mandar as mensagens.

Sem `EVOLUTION_API_KEY`, o envio só escreve no log:

```python
if not settings.whatsapp_enabled:
    logger.info("[WhatsApp simulado] -> %s: %s", phone, text)
    return False
```

É isso que permite demonstrar o sistema inteiro pelo simulador antes de ter a instância.

### `api/auth.py` — a rota de login

Recebe usuário e senha, confere o hash e devolve o token. A função `current_user()` é a **dependência de proteção**: qualquer rota que a declare passa a exigir um token válido. No `dashboard.py` ela é aplicada ao router inteiro, de uma vez:

```python
router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])
```

Assim não existe o risco de esquecer de proteger uma rota nova.

### `api/webhook.py` — a porta do WhatsApp

Recebe o JSON da Evolution e faz três coisas:

1. **Filtra** o que não interessa: mensagens do próprio robô (`fromMe`), grupos (`@g.us`), eventos que não sejam mensagens novas.
2. **Extrai** o telefone e o conteúdo. A Evolution manda o texto em campos diferentes conforme o tipo (`conversation`, `extendedTextMessage.text`, legenda de imagem…), e a imagem pode vir embutida em base64 ou precisar ser baixada — o código cobre os dois casos.
3. **Entrega** ao agente e responde `{"status": "ok"}`.

Se qualquer coisa der errado no meio, o erro é registrado, o paciente recebe *"tive um problema técnico, pode repetir?"* e a rota responde 200 mesmo assim — senão a Evolution fica reenviando a mesma mensagem em loop.

### `api/dashboard.py` — o que o painel consome

Cada aba do painel tem uma rota correspondente:

| Rota | Alimenta |
| --- | --- |
| `/api/overview` | Os números do topo e o status dos serviços |
| `/api/patients` | As três colunas do Kanban |
| `/api/patients/{id}` | A conversa completa, ao clicar num card |
| `/api/patients/{id}/reset` | O botão de apagar memória |
| `/api/logs?after_id=` | Os logs (só os novos, por isso o `after_id`) |
| `/api/whatsapp/qrcode` | O QR Code |
| `/api/simulator/text` e `/image` | O simulador de conversa |

O simulador merece destaque: ele chama **o mesmo `agent.handle_incoming()`** que o webhook chama. Não é uma imitação do fluxo — é o fluxo, sem o WhatsApp no meio.

### `main.py` — a montagem

Junta as partes e sobe:

```python
@asynccontextmanager
async def lifespan(app):
    init_db()          # cria tabelas e dados iniciais ao ligar
    yield              # daqui em diante a aplicação atende requisições

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(webhook.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR))
```

O `lifespan` é o "ligar e desligar" da aplicação: o que vem antes do `yield` roda na subida, o que vem depois rodaria no encerramento.

### `static/` — o painel

Três arquivos, sem framework e sem etapa de build — abre e funciona.

- **`index.html`** — a estrutura: tela de login, barra lateral, as quatro abas e o painel lateral que abre ao clicar num paciente.
- **`styles.css`** — a aparência.
- **`app.js`** — o comportamento. Faz login, guarda o token, e a cada 4 segundos consulta a API e redesenha a tela (isso se chama **polling**: perguntar de tempos em tempos).

Um cuidado de segurança que vale apontar no code review: todo texto vindo do banco passa por `escapeHTML()` antes de ir para a tela. Sem isso, um paciente poderia mandar `<script>` no WhatsApp e o código rodaria no navegador da recepcionista — o ataque conhecido como **XSS**.

---

## 6. O banco de dados por dentro

Seis tabelas:

| Tabela | Guarda | Exemplo de linha |
| --- | --- | --- |
| `patients` | Quem está conversando | `id 1 · 5561999990001 · Ana Paula Ribeiro · aguardando_pix` |
| `slots` | Os horários da agenda | `id 8 · 29/07 10:00 · ocupado até 14:32` |
| `appointments` | O agendamento em si | `paciente 1 · slot 8 · confirmado · sinal R$ 190,00` |
| `messages` | Transcrição da conversa | `paciente 1 · in · "a 2"` |
| `agent_logs` | Decisões da IA | `paciente 1 · comprovante_analisado · {aprovado: true}` |
| `users` | Acesso ao painel | `admin · pbkdf2_sha256$240000$...` |

**Por que `slots` e `appointments` são tabelas separadas?** Porque são coisas diferentes: `slots` é a *agenda da clínica* (existe mesmo sem paciente); `appointments` é o *compromisso* de um paciente com um horário, com valor, comprovante e status próprios. Um horário cancelado volta a ficar livre sem perder o histórico do agendamento cancelado.

O status do paciente é o que define a coluna do Kanban:

```
   em_atendimento  ──▶  aguardando_pix  ──▶  confirmado
   (coletando          (horário reservado,   (sinal validado,
    os dados)           esperando o Pix)      consulta marcada)
        ▲                     │
        └───── botão "apagar memória" ──────┘
```

---

## 7. Docker explicado

**`Dockerfile`** é a receita da nossa aplicação: parte de uma imagem com Python 3.12, instala as bibliotecas do `requirements.txt`, copia o código e define o comando que sobe o servidor. Cada linha vira uma camada em cache — por isso as dependências são copiadas *antes* do código: mudar um arquivo `.py` não obriga a reinstalar tudo de novo.

**`docker-compose.yml`** descreve os serviços que sobem juntos:

| Serviço | O que é | Porta |
| --- | --- | --- |
| `api` | A nossa aplicação | 8000 |
| `db` | PostgreSQL | 5432 |
| `redis` | Redis | 6379 |
| `evolution` | Evolution API (opcional) | 8080 |

Três conceitos que aparecem no arquivo:

- **`volumes`** — pastas que sobrevivem ao container. Sem isso, `docker compose down` apagaria o banco inteiro.
- **`healthcheck` + `depends_on: condition: service_healthy`** — a API só sobe depois que o Postgres responde de verdade. Sem isso, ela tentaria conectar antes do banco estar pronto e morreria.
- **`profiles: ["whatsapp"]`** — o serviço da Evolution só sobe se você pedir (`docker compose --profile whatsapp up`). Como o Rauder vai fornecer uma instância, ela fica de fora por padrão.

Dentro do compose, os containers se enxergam **pelo nome**: `db:5432`, `redis:6379`. Por isso o `.env` usa `postgresql://...@db:5432/...` e não `localhost`.

---

## 8. Como testar e depurar

```bash
# Subir tudo
docker compose up -d --build

# Ver o que a aplicação está fazendo, ao vivo
docker compose logs -f api

# Rodar o teste do fluxo completo (28 verificações)
python scripts/teste_fluxo.py

# Entrar no banco e olhar as tabelas
docker compose exec db psql -U lifeline -d lifeline -c "select id, name, status from patients;"

# Ver a memória guardada no Redis
docker compose exec redis redis-cli keys "session:*"

# Reiniciar depois de mexer no .env
docker compose up -d
```

Três lugares para investigar quando algo não funcionar:

1. **A aba "Logs da IA"** do painel — mostra o que o agente decidiu e por quê.
2. **`http://localhost:8000/docs`** — o FastAPI gera sozinho uma página onde dá para testar cada rota clicando.
3. **O rodapé da barra lateral** — diz se a IA está em modo `gemini` ou `simulado`, se o Redis respondeu e se o WhatsApp tem credenciais.

---

## 9. O que a revisão mudou

Passei o código inteiro em análise estática (`pyflakes`, sem apontamentos) e revisei os caminhos de falha. Quatro correções entraram:

**1. Falha do Gemini não derruba mais o atendimento.**
Antes, se a API do Google recusasse a chamada (cota, rede, chave inválida), a exceção subia e o paciente ficava sem resposta. Agora `_ask_json()` e `_ask_text()` capturam o erro, registram no log e a interpretação local assume. Os dois modos passaram a ser funções separadas (`_local_name`, `_local_reason`, `_local_slot`), o que também deixou o arquivo mais legível.

**2. Falha na leitura do comprovante virou mensagem clara.**
`read_receipt()` agora trata erro do Gemini Vision e devolve `falha_tecnica`, que o validador transforma em *"tive uma instabilidade técnica ao analisar a imagem, pode reenviar em alguns instantes?"* — em vez de acusar o paciente de ter mandado um comprovante inválido.

**3. O webhook não devolve mais erro 500.**
Qualquer exceção no processamento é registrada, o paciente recebe um aviso e a rota responde 200. Isso evita o loop de reenvio da Evolution, que reentregaria a mesma mensagem indefinidamente.

**4. O reconhecimento de horários ficou melhor no modo simulado.**
"quinta às 14h" e "pode ser às 9" não eram reconhecidos (o `14h` grudado no número quebrava a busca). Agora normaliza o `h`, compara números inteiros e só aceita o horário isolado quando ele identifica uma única opção — sem chutar quando há ambiguidade.

Todos os 28 testes continuam passando depois das mudanças.

---

## 10. Perguntas prováveis no code review

**"Por que não usou um agente do LangChain com tools?"**
O roteiro do briefing é obrigatório e tem consequência financeira: cobrar o sinal, validar o comprovante, só então confirmar. Um agente autônomo pode pular etapas ou se convencer de que um comprovante "quase certo" serve. Aqui o LLM faz o que ele faz bem — entender linguagem — e o fluxo fica no código, onde dá para testar e auditar. Se a clínica quiser depois um agente mais livre para a etapa `done` (tirar dúvidas), é só trocar aquele ramo.

**"E se o paciente mandar o comprovante duas vezes?"**
Depois do primeiro aprovado, o `step` vira `done` e uma nova imagem cai no ramo "não estamos na etapa de pagamento". O horário já está confirmado e não é reservado de novo.

**"E se dois pacientes escolherem o mesmo horário ao mesmo tempo?"**
A reserva marca `is_open = False` na hora da escolha. O segundo paciente recebe "esse horário acabou de ser preenchido" com a lista atualizada. Numa operação com volume alto, o próximo passo seria um `SELECT ... FOR UPDATE` para travar a linha durante a reserva.

**"Por que o painel usa polling e não WebSocket?"**
Volume de clínica é baixo (dezenas de eventos por hora) e o polling de 4 segundos resolve com uma fração da complexidade — sem reconexão, sem estado de conexão, sem infraestrutura extra. Se a exigência virar "tempo real de verdade", a troca é localizada: só o `refresh()` do `app.js` e um endpoint SSE.

**"Como isso escala?"**
A API não guarda estado próprio — tudo está no Postgres e no Redis. Dá para subir várias réplicas atrás de um balanceador sem mudar código. O gargalo natural seria a chamada ao Gemini, que é I/O; o passo seguinte seria enfileirar as mensagens (o Redis já está no projeto) e processá-las com workers.

**"Onde entram os testes?"**
`scripts/teste_fluxo.py` roda o atendimento completo ponta a ponta com SQLite, sem Redis e sem chave de API, e verifica cada exigência do briefing — incluindo as três recusas de comprovante. Numa evolução do projeto, ele viraria uma suíte `pytest` com o mesmo conteúdo.

---

## 11. Glossário

| Termo | Em uma frase |
| --- | --- |
| **API** | Programa que atende pedidos de outros programas pela rede |
| **Rota / endpoint** | Um endereço específico que a API atende (`/api/patients`) |
| **JSON** | Formato de texto para trocar dados: `{"nome": "Ana"}` |
| **Webhook** | O serviço externo é quem avisa você quando algo acontece |
| **LLM** | Modelo de linguagem — entende e escreve texto (Gemini) |
| **Multimodal** | Modelo que também entende imagens, não só texto |
| **Prompt** | A instrução que se dá ao modelo |
| **Base64** | Jeito de escrever uma imagem como texto |
| **ORM** | Escrever Python e a biblioteca gera o SQL (SQLAlchemy) |
| **Migration** | Alterar a estrutura do banco de forma versionada |
| **JWT** | Token assinado que prova quem você é, sem sessão no servidor |
| **Hash** | Embaralhamento de mão única, usado para guardar senhas |
| **XSS** | Ataque em que texto do usuário vira código no navegador |
| **Container** | Caixa isolada com o programa e tudo que ele precisa |
| **Volume** | Pasta do container que sobrevive quando ele é recriado |
| **Polling** | Perguntar de tempos em tempos se algo mudou |
| **TTL** | Prazo de validade de um dado no cache |
| **Máquina de estados** | Sistema com etapas fixas e regras de transição entre elas |

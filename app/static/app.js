/* Painel Lifeline One — JS sem dependências. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  token: localStorage.getItem("lifeline_token") || "",
  view: "funil",
  lastLogId: 0,
  patientId: null,
  timer: null,
};

const TITULOS = {
  funil: ["Funil de atendimento", "Quem está sendo atendido agora"],
  logs: ["Logs da IA", "Decisões e ações do agente"],
  whatsapp: ["Conexão", "Parear o WhatsApp da clínica"],
  simulador: ["Simulador", "Testar o atendimento sem WhatsApp"],
};

/* ---------------- HTTP ---------------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    logout();
    throw new Error("Sessão expirada. Entre novamente.");
  }
  const dados = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(dados.detail || "Não foi possível concluir a ação.");
  return dados;
}

function toast(mensagem) {
  const el = $("#toast");
  el.textContent = mensagem;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), 3200);
}

const money = (v) => (v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

/* ---------------- Sessão ---------------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const erro = $("#login-error");
  erro.hidden = true;
  try {
    const dados = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#login-user").value, password: $("#login-pass").value }),
    });
    state.token = dados.access_token;
    localStorage.setItem("lifeline_token", state.token);
    start();
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
  }
});

$("#logout").addEventListener("click", logout);

function logout() {
  state.token = "";
  localStorage.removeItem("lifeline_token");
  clearInterval(state.timer);
  $("#app").hidden = true;
  $("#login").hidden = false;
}

function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 4000);
}

/* ---------------- Navegação ---------------- */
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    state.view = tab.dataset.view;
    $$(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === state.view));
    const [eyebrow, titulo] = TITULOS[state.view];
    $("#top-eyebrow").textContent = eyebrow;
    $("#top-title").textContent = titulo;
    refresh();
  })
);

/* ---------------- Atualização periódica ---------------- */
async function refresh() {
  if (!state.token) return;
  try {
    const visao = await api("/api/overview");
    $("#stat-pacientes").textContent = visao.pacientes;
    $("#stat-confirmados").textContent = visao.confirmados;
    $("#stat-receita").textContent = money(visao.receita_sinais);
    $("#stat-livres").textContent = visao.horarios_livres;
    $("#meta-ia").textContent = visao.ia;
    $("#meta-redis").textContent = visao.redis;
    $("#meta-wpp").textContent = visao.whatsapp;

    if (state.view === "funil") await renderBoard();
    if (state.view === "logs") await renderLogs();
    if (state.patientId) await renderDrawer(state.patientId, true);
  } catch (err) {
    /* silencioso: a próxima rodada tenta de novo */
  }
}

/* ---------------- Kanban ---------------- */
function badgeClass(status) {
  return status === "aguardando_pix" ? "badge--amber" : status === "confirmado" ? "badge--green" : "";
}

async function renderBoard() {
  const { colunas } = await api("/api/patients");
  $("#board").innerHTML = colunas
    .map(
      (coluna) => `
      <section class="column" data-status="${coluna.status}">
        <div class="column__head">
          <h3>${coluna.titulo}</h3>
          <span class="column__count">${coluna.cards.length}</span>
        </div>
        <div class="column__rail"></div>
        ${
          coluna.cards.length
            ? coluna.cards.map((c) => cardHTML(c)).join("")
            : '<p class="empty">Nenhum paciente nesta etapa.</p>'
        }
      </section>`
    )
    .join("");

  $$(".card").forEach((card) =>
    card.addEventListener("click", () => openDrawer(Number(card.dataset.id)))
  );
}

function cardHTML(c) {
  const etiqueta = c.horario || (c.tipo_motivo ? c.tipo_motivo : "coletando dados");
  return `
  <button class="card" data-id="${c.id}">
    <span class="card__name">${escapeHTML(c.nome)}</span>
    <span class="card__phone">${escapeHTML(c.telefone)}</span>
    ${c.ultima_mensagem ? `<span class="card__line">${escapeHTML(c.ultima_mensagem)}</span>` : ""}
    <span class="card__foot">
      <span class="badge ${badgeClass(c.status)}">${escapeHTML(etiqueta)}</span>
      <span class="time">${escapeHTML(c.atualizado_em)}</span>
    </span>
  </button>`;
}

/* ---------------- Logs ---------------- */
async function renderLogs() {
  const { logs } = await api(`/api/logs?after_id=${state.lastLogId}`);
  if (!logs.length) return;
  const consola = $("#console");
  const vazio = consola.querySelector(".console__empty");
  if (vazio) vazio.remove();
  logs.forEach((log) => {
    state.lastLogId = Math.max(state.lastLogId, log.id);
    const linha = document.createElement("div");
    linha.className = `log log--${log.nivel}`;
    linha.innerHTML = `
      <span class="log__time">${escapeHTML(log.hora)}</span>
      <span class="log__event">${escapeHTML(log.evento)}</span>
      <span class="log__detail"><span class="log__who">${escapeHTML(log.paciente)}</span> ${escapeHTML(log.detalhe)}</span>`;
    consola.appendChild(linha);
  });
  consola.scrollTop = consola.scrollHeight;
}

/* ---------------- Drawer do paciente ---------------- */
async function openDrawer(id) {
  state.patientId = id;
  $("#drawer").hidden = false;
  await renderDrawer(id);
}

async function renderDrawer(id, silencioso = false) {
  try {
    const p = await api(`/api/patients/${id}`);
    $("#drawer-name").textContent = p.nome;
    $("#drawer-phone").textContent = p.telefone;
    $("#drawer-meta").innerHTML = `
      <div><dt>Etapa</dt><dd>${escapeHTML(p.etapa)}</dd></div>
      <div><dt>Status</dt><dd>${escapeHTML(p.status)}</dd></div>
      <div><dt>Horário</dt><dd>${escapeHTML(p.horario || "—")}</dd></div>
      <div><dt>Sinal</dt><dd>${p.sinal_pago ? "pago" : "pendente"}</dd></div>
      <div style="grid-column: 1 / -1"><dt>Motivo</dt><dd>${escapeHTML(p.motivo || "—")}</dd></div>`;
    $("#drawer-thread").innerHTML = p.mensagens
      .map(
        (m) =>
          `<div class="bubble bubble--${m.direcao === "in" ? "in" : "out"}">${escapeHTML(m.conteudo)}
           <div class="time">${escapeHTML(m.hora)}</div></div>`
      )
      .join("");
    if (!silencioso) $("#drawer-thread").scrollTop = $("#drawer-thread").scrollHeight;
  } catch (err) {
    if (!silencioso) toast(err.message);
  }
}

$$("[data-close]").forEach((el) =>
  el.addEventListener("click", () => {
    $("#drawer").hidden = true;
    state.patientId = null;
  })
);

$("#drawer-reset").addEventListener("click", async () => {
  if (!state.patientId) return;
  try {
    const r = await api(`/api/patients/${state.patientId}/reset`, { method: "POST" });
    toast(r.mensagem);
    await renderDrawer(state.patientId);
    await renderBoard();
  } catch (err) {
    toast(err.message);
  }
});

/* ---------------- WhatsApp ---------------- */
$("#qr-refresh").addEventListener("click", async () => {
  const caixa = $("#qr-box");
  caixa.innerHTML = '<p class="qr__hint">Gerando…</p>';
  try {
    const dados = await api("/api/whatsapp/qrcode");
    if (dados.qrcode) {
      const src = dados.qrcode.startsWith("data:") ? dados.qrcode : `data:image/png;base64,${dados.qrcode}`;
      caixa.innerHTML = `<img alt="QR Code de pareamento" src="${src}" />`;
    } else {
      caixa.innerHTML = `<p class="qr__hint">${escapeHTML(dados.mensagem || `Instância: ${dados.status}`)}</p>`;
    }
    const estado = await api("/api/whatsapp/state");
    $("#wpp-state").textContent = estado.state;
  } catch (err) {
    caixa.innerHTML = `<p class="qr__hint">${escapeHTML(err.message)}</p>`;
  }
});

$("#webhook-save").addEventListener("click", async () => {
  const url = $("#webhook-url").value.trim();
  if (!url) return toast("Informe a URL pública desta API.");
  try {
    const r = await api("/api/whatsapp/webhook", {
      method: "POST",
      body: JSON.stringify({ public_url: url }),
    });
    $("#webhook-result").hidden = false;
    $("#webhook-result").textContent = JSON.stringify(r, null, 2);
    toast("Webhook registrado.");
  } catch (err) {
    toast(err.message);
  }
});

/* ---------------- Simulador ---------------- */
function bubble(texto, direcao) {
  const tela = $("#sim-screen");
  const vazio = tela.querySelector(".phone__hint");
  if (vazio) vazio.remove();
  const el = document.createElement("div");
  el.className = `bubble bubble--${direcao}`;
  el.textContent = texto;
  tela.appendChild(el);
  tela.scrollTop = tela.scrollHeight;
}

async function enviarTexto() {
  const texto = $("#sim-text").value.trim();
  if (!texto) return;
  bubble(texto, "out");
  $("#sim-text").value = "";
  try {
    const r = await api("/api/simulator/text", {
      method: "POST",
      body: JSON.stringify({ phone: $("#sim-phone").value, text: texto }),
    });
    r.respostas.forEach((m) => bubble(m, "in"));
  } catch (err) {
    toast(err.message);
  }
}

$("#sim-send").addEventListener("click", enviarTexto);
$("#sim-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") enviarTexto();
});

$("#sim-file").addEventListener("change", async (e) => {
  const arquivo = e.target.files[0];
  if (!arquivo) return;
  bubble("📷 comprovante enviado", "out");
  const form = new FormData();
  form.append("phone", $("#sim-phone").value);
  form.append("file", arquivo);
  try {
    const r = await api("/api/simulator/image", { method: "POST", body: form });
    r.respostas.forEach((m) => bubble(m, "in"));
  } catch (err) {
    toast(err.message);
  }
  e.target.value = "";
});

$("#sim-audio").addEventListener("change", async (e) => {
  const arquivo = e.target.files[0];
  if (!arquivo) return;
  bubble("🎤 áudio enviado", "out");
  const form = new FormData();
  form.append("phone", $("#sim-phone").value);
  form.append("file", arquivo);
  try {
    const r = await api("/api/simulator/audio", { method: "POST", body: form });
    r.respostas.forEach((m) => bubble(m, "in"));
  } catch (err) {
    toast(err.message);
  }
  e.target.value = "";
});

/* ---------------- Util ---------------- */
function escapeHTML(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- Boot ---------------- */
if (state.token) start();

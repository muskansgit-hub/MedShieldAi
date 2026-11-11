// MedShield web UI: plain JavaScript, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const f2 = (x) => (x == null ? "–" : Number(x).toFixed(2));
const f3 = (x) => (x == null ? "–" : Number(x).toFixed(3));
const pct = (x) => `${Math.round(Math.max(0, Math.min(1, x)) * 100)}%`;

const ICONS = {
  PASS: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m5 12 5 5 9-10"/></svg>',
  WARN: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/></svg>',
  REWRITE: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/></svg>',
  BLOCK: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/></svg>',
};
const ACTION_TEXT = {
  PASS: "Delivered unchanged",
  WARN: "Delivered with a safety note",
  REWRITE: "Rewritten to calibrated language",
  BLOCK: "Withheld and replaced",
};

// Admin token (only needed when the server sets MEDSHIELD_ADMIN_TOKEN). Kept per browser.
const store = {
  get() { try { return localStorage.getItem("medshield_admin_token") || ""; } catch { return ""; } },
  set(v) { try { v ? localStorage.setItem("medshield_admin_token", v) : localStorage.removeItem("medshield_admin_token"); } catch {} },
};

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const tok = store.get();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const r = await fetch(path, { headers, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(body.detail || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return body;
}

// ------------------------------------------------------------------ tabs
$$(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
  if (name === "bench") loadLeaderboard();
  if (name === "monitor") loadMonitor();
}

// ------------------------------------------------------------------ playground
let mode = "audit";
let examples = [];
$$(".seg-btn").forEach((b) => b.addEventListener("click", () => {
  mode = b.dataset.mode;
  $$(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  $("#audit-fields").hidden = mode !== "audit";
  $("#ask-fields").hidden = mode !== "ask";
}));

$("#example").addEventListener("change", (e) => {
  const ex = examples[e.target.value];
  if (!ex) return;
  $("#question").value = ex.question;
  $("#response").value = ex.response;
  if (mode !== "audit") $$(".seg-btn")[0].click();
});

$("#provider").addEventListener("change", updateProviderHint);
function updateProviderHint() {
  const o = $("#provider").selectedOptions[0];
  if (!o) return;
  $("#model").placeholder = o.dataset.model || "default";
  $("#provider-hint").textContent = o.dataset.configured === "true"
    ? (o.dataset.note || "")
    : `Not configured: set ${o.dataset.note} in .env and restart.`;
}

$("#run").addEventListener("click", run);
async function run() {
  const question = $("#question").value.trim();
  if (question.length < 3) return flash("Enter a question first.");
  const btn = $("#run");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Running…';
  try {
    let rep;
    if (mode === "audit") {
      const response = $("#response").value.trim();
      if (!response) throw new Error("Paste a response to audit.");
      rep = await api("/api/analyze", { method: "POST", body: JSON.stringify({ question, response }) });
    } else {
      rep = await api("/api/ask", {
        method: "POST",
        body: JSON.stringify({ question, provider: $("#provider").value, model: $("#model").value || null,
                               llm_rewrite: $("#llm-rewrite").checked }),
      });
    }
    renderReport(rep);
  } catch (e) {
    flash(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run guard";
  }
}
function flash(msg) {
  $("#result").innerHTML = `<div class="card"><p class="error">${esc(msg)}</p></div>`;
}

function highlight(text, spans) {
  let out = "", i = 0;
  for (const s of spans) {
    out += esc(text.slice(i, s.start));
    const cls = s.kind === "assertive" ? "as" : "he";
    const tip = s.kind === "assertive" ? "Assertive phrase (raises confidence)" : "Hedging / referral phrase (lowers confidence)";
    out += `<mark class="${cls}" data-tip="${tip}">${esc(text.slice(s.start, s.end))}</mark>`;
    i = s.end;
  }
  return out + esc(text.slice(i));
}

function meter(label, value, sub) {
  return `<div class="meter"><div class="lbl"><span>${label}</span><b>${f2(value)}</b></div>
    <div class="track"><div class="fill" style="width:${pct(value)}"></div></div>
    <div class="sub">${sub}</div></div>`;
}

function renderReport(r) {
  const c = r.confidence, inc = r.incorrectness, risk = r.risk;
  const changed = r.final_response !== r.original_response;
  const after = r.after;
  const probs = Object.entries(risk.ml_probs).map(([t, p]) =>
    `<span>Tier ${t}</span><div class="track"><div class="fill" style="width:${pct(p)}"></div></div><span class="num">${f2(p)}</span>`).join("");
  const comps = [
    ["Danger patterns", inc.danger], ["Missing safety advice", inc.missing_safety],
    ["Contradicts reference / known contraindication", inc.polarity_conflict], ["Distance from reference", inc.semantic_distance],
    ["LLM judge", inc.judge],
  ].map(([k, v]) => `<dt>${k}</dt><dd>${v == null ? "n/a" : f2(v)}</dd>`).join("");

  $("#result").innerHTML = `
  <div class="card">
    <div class="verdict">
      <div>
        <div class="fsc-label">False Safety Confidence</div>
        <div class="fsc-big">${f3(r.fsc)}</div>
      </div>
      <div>
        <span class="badge a-${r.action}">${ICONS[r.action]}${r.action}</span>
        <span class="muted" style="margin-left:10px">${ACTION_TEXT[r.action]}</span>
        <div class="formula">C <b>${f2(c.score)}</b> × I <b>${f2(inc.score)}</b> × W <b>${risk.tier}/4</b> = <b>${f3(r.fsc)}</b>
          ${after ? ` → delivered FSC <b>${f3(after.fsc)}</b>` : ""}</div>
      </div>
    </div>
    <div class="meters">
      ${meter("Confidence C", c.score, `${c.high_count} assertive · ${c.low_count} hedging phrases`)}
      ${meter("Incorrectness I", inc.score, inc.judge != null ? "heuristics + LLM judge" : "heuristic estimate")}
      ${meter("Risk weight W/4", risk.tier / 4, `Tier ${risk.tier}: ${esc(risk.label)}`)}
    </div>
    <ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
  </div>

  <div class="card">
    <div class="card-head"><h2>Model response</h2>${r.provider ? `<span class="pill">${esc(r.provider)}</span>` : ""}</div>
    <div class="resp">${highlight(r.original_response, c.spans)}</div>
    <div class="legend"><span><i class="k-as"></i>assertive (raises C)</span><span><i class="k-he"></i>hedging / referral (lowers C)</span></div>
    ${inc.danger_hits.length ? `<div style="margin-top:12px" class="chips">${inc.danger_hits.map((h) =>
      `<span class="chip danger" data-tip="weight ${h.weight}">⚠ ${esc(h.reason)}: “${esc(h.text)}”</span>`).join("")}</div>` : ""}
  </div>

  ${changed ? `<div class="card">
    <div class="card-head"><h2>What the user receives</h2>
      <span class="delta">${r.remediation ? `<span class="pill">${esc(r.remediation)}</span> ` : ""}FSC ${f3(r.fsc)} → ${f3(after?.fsc)}</span></div>
    <div class="resp">${esc(r.final_response)}</div>
  </div>` : ""}

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>Risk triage</h2></div>
      <div class="probs">${probs}</div>
      <dl class="kv" style="margin-top:14px">
        <dt>ML tier</dt><dd>${risk.ml_tier}</dd>
        <dt>Rule tier</dt><dd>${risk.rule_tier}</dd>
        <dt>Final tier</dt><dd>${risk.tier} (max of both)</dd>
      </dl>
      ${risk.rule_reasons.length ? `<div class="chips" style="margin-top:10px">${risk.rule_reasons.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="card">
      <div class="card-head"><h2>Incorrectness signals</h2></div>
      <dl class="kv">${comps}</dl>
      ${inc.judge_reason ? `<p class="muted">${esc(inc.judge_reason)}</p>` : ""}
      ${inc.contraindication ? `<h3>Known contraindication</h3><div class="ref" style="border-color:var(--critical)">${esc(inc.contraindication.guidance)}</div>` : ""}
      <h3>Verified reference</h3>
      ${r.reference ? `<div class="ref"><div class="muted">${esc(r.reference.source)} · match ${f2(r.reference.similarity)} · “${esc(r.reference.question)}”</div>${esc(r.reference.ground_truth)}</div>`
        : `<p class="muted">No close match in MedFSC-Bench, so incorrectness relies on the other signals${inc.judge == null ? " (configure an LLM judge for broader coverage)" : ""}.</p>`}
    </div>
  </div>
  <p class="muted">Scored in ${r.timings_ms.score} ms${r.timings_ms.generation ? ` · generation ${r.timings_ms.generation} ms` : ""}${r.audit_id ? ` · audit #${r.audit_id}` : ""}</p>`;
}

// ------------------------------------------------------------------ leaderboard
const SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
function heatColor(v, max) {
  const t = Math.max(0, Math.min(1, v / (max || 1)));
  const i = Math.round(t * (SEQ.length - 1));
  return { bg: SEQ[i], fg: i >= 6 ? "#fff" : "#0b0b0b" };
}

async function loadLeaderboard() {
  const lb = await api("/api/leaderboard");
  $("#lb-meta").textContent = lb.generated_at ? `updated ${new Date(lb.generated_at).toLocaleString()}` : "";
  if (!lb.models.length) {
    $("#lb").innerHTML = `<div class="empty-lb"><p><strong>No benchmark runs yet.</strong> Run a real model on MedFSC-Bench:</p>
<pre><code># free and local, needs Ollama installed
ollama pull llama3.2
python -m medshield.bench run --provider ollama --model llama3.2

# or a hosted API (key in .env)
python -m medshield.bench run --provider groq --limit 150</code></pre></div>`;
    return;
  }
  const max = Math.max(...lb.models.flatMap((m) => Object.values(m.fsc_by_tier)));
  const cell = (m, t, key) => {
    const v = m[key][t];
    if (v == null) return `<td class="cell">–</td>`;
    const { bg, fg } = heatColor(v, max);
    return `<td class="cell" style="background:${bg};color:${fg}" data-tip="${esc(m.model)} · Tier ${t} · n=${m.n_by_tier[t]} · mean FSC ${f3(v)}">${f3(v)}</td>`;
  };
  const rows = lb.models.map((m) => `<tr>
      <td>${esc(m.model)} ${m.synthetic ? '<span class="pill synth">synthetic</span>' : ""}</td>
      ${[1, 2, 3, 4].map((t) => cell(m, String(t), "fsc_by_tier")).join("")}
      <td class="num">${f3(m.avg_fsc)}</td>
      <td class="num">${f3(m.avg_fsc_delivered)}</td>
      <td class="num">−${m.fsc_reduction_pct}%</td>
      <td class="num">${m.adversarial_validation_rate == null ? "–" : pct(m.adversarial_validation_rate)}</td>
      <td class="num">${m.tier4_unhedged_rate == null ? "–" : pct(m.tier4_unhedged_rate)}</td>
      <td class="num" data-tip="Spearman ρ between tier and FSC (H1), p=${m.h1_spearman_p}">${m.h1_spearman_rho == null ? "–" : f2(m.h1_spearman_rho)}</td>
      <td class="num">${m.n}</td></tr>`).join("");
  $("#lb").innerHTML = `<table class="heat">
    <thead><tr><th>Model</th><th class="num">Tier 1</th><th class="num">Tier 2</th><th class="num">Tier 3</th><th class="num">Tier 4</th>
      <th class="num">Avg FSC</th><th class="num">With MedShield</th><th class="num">Change</th>
      <th class="num" data-tip="Share of adversarial prompts where the answer contradicts the verified reference">Premise validation</th>
      <th class="num" data-tip="Tier-4 answers with no referral or emergency advice">Tier-4 unhedged</th>
      <th class="num">H1 ρ</th><th class="num">n</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div class="scale"><span>0</span><span class="bar"></span><span>${f3(max)}</span><span>mean FSC (raw model output)</span></div>`;
}

// ------------------------------------------------------------------ monitor
$("#refresh").addEventListener("click", loadMonitor);
$("#flagged-only").addEventListener("change", loadMonitor);

function tokenForm(msg) {
  $("#audit").innerHTML = `<p class="muted">${esc(msg)}</p>
    <div class="row" style="max-width:520px"><label class="field grow" style="margin:0">Admin token
      <input id="admin-token" type="password" autocomplete="off"></label>
      <button id="save-token" class="ghost" style="margin-top:18px">Unlock</button></div>`;
  $("#save-token").addEventListener("click", () => { store.set($("#admin-token").value.trim()); loadMonitor(); });
}

async function loadMonitor() {
  const s = await api("/api/stats");
  let rows = null;
  try {
    rows = await api(`/api/audit?limit=100&flagged_only=${$("#flagged-only").checked}`);
  } catch (e) {
    if (e.status !== 401) throw e;
    if (store.get()) store.set("");
  }
  const flagged = s.total ? (s.total - s.by_action.PASS) / s.total : 0;
  $("#tiles").innerHTML = [
    [s.total, "guarded responses"],
    [s.total ? `${Math.round(flagged * 100)}%` : "–", "flagged (warn, rewrite or block)"],
    [s.mean_fsc == null ? "–" : `${f3(s.mean_fsc)} → ${f3(s.mean_fsc_delivered)}`, "mean FSC, model → delivered"],
    [s.mean_latency_ms == null ? "–" : `${Math.round(s.mean_latency_ms)} ms`, "mean guard latency"],
  ].map(([v, l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const maxA = Math.max(1, ...Object.values(s.by_action));
  $("#actions-chart").innerHTML = `<div class="hbar">${Object.entries(s.by_action).map(([a, n]) =>
    `<span class="act a-${a}">${ICONS[a].replace("<svg", '<svg width="13" height="13" style="vertical-align:-2px;margin-right:4px"')}${a}</span>
     <div class="track"><div class="fill f-${a}" style="width:${(n / maxA) * 100}%" data-tip="${a}: ${n} responses"></div></div>
     <span class="num">${n}</span>`).join("")}</div>`;

  const maxT = Math.max(0.01, ...s.by_tier.map((t) => t.mean_fsc || 0));
  $("#tier-chart").innerHTML = s.by_tier.length ? s.by_tier.map((t) => `
    <div class="pair">
      <span>Tier ${t.tier}</span><div class="track"><div class="fill f-raw" style="width:${(t.mean_fsc / maxT) * 100}%" data-tip="Tier ${t.tier} model output: mean FSC ${f3(t.mean_fsc)} (n=${t.n})"></div></div><span class="num">${f3(t.mean_fsc)}</span>
      <span></span><div class="track"><div class="fill f-del" style="width:${(t.mean_fsc_after / maxT) * 100}%" data-tip="Tier ${t.tier} delivered: mean FSC ${f3(t.mean_fsc_after)}"></div></div><span class="num">${f3(t.mean_fsc_after)}</span>
    </div>`).join("") + `<div class="legend"><span><i class="f-raw"></i>model output</span><span><i class="f-del"></i>delivered after MedShield</span></div>`
    : `<p class="muted">No traffic yet. Run a few questions in the Playground.</p>`;

  if (rows === null) {
    tokenForm("The audit log contains users' questions, so it's protected on this server. Enter the admin token to view it.");
    return;
  }
  $("#audit").innerHTML = rows.length ? `<table><thead><tr><th>#</th><th>Time</th><th>Source</th><th>Question</th>
    <th class="num">Tier</th><th class="num">C</th><th class="num">I</th><th class="num">FSC</th><th class="num">Delivered</th><th>Action</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="click" data-id="${r.id}"><td>${r.id}</td><td>${new Date(r.ts * 1000).toLocaleTimeString()}</td>
      <td>${esc(r.source)}${r.provider ? `<div class="muted">${esc(r.provider)}</div>` : ""}</td>
      <td>${esc(r.question.slice(0, 90))}</td><td class="num">${r.tier}</td><td class="num">${f2(r.confidence)}</td>
      <td class="num">${f2(r.incorrectness)}</td><td class="num">${f3(r.fsc)}</td><td class="num">${f3(r.fsc_after ?? r.fsc)}</td>
      <td><span class="act a-${r.action}">${r.action}</span></td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">Nothing logged yet.</p>`;
  $$("#audit tr.click").forEach((tr) => tr.addEventListener("click", async () => {
    renderReport(await api(`/api/audit/${tr.dataset.id}`));
    showTab("playground");
  }));
}

// ------------------------------------------------------------------ tooltip
const tip = $("#tooltip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.hidden = true; return; }
  tip.textContent = t.dataset.tip;
  tip.hidden = false;
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${e.clientY + 16}px`;
});

// ------------------------------------------------------------------ init
(async function init() {
  try {
    const [h, ex, provs] = await Promise.all([api("/api/health"), api("/api/examples"), api("/api/providers")]);
    $("#t-warn").textContent = h.thresholds.warn;
    $("#t-rw").textContent = h.thresholds.rewrite;
    $("#t-block").textContent = h.thresholds.block;
    examples = ex;
    $("#example").innerHTML += ex.map((e, i) => `<option value="${i}">${esc(e.label)}</option>`).join("");
    $("#provider").innerHTML = provs.map((p) =>
      `<option value="${p.name}" data-model="${esc(p.default_model)}" data-configured="${p.configured}" data-note="${esc(p.note || "")}">${p.name}${p.configured ? "" : " (not configured)"}</option>`).join("");
    if (provs.some((p) => p.name === h.default_provider)) $("#provider").value = h.default_provider;
    updateProviderHint();
  } catch (e) {
    flash(`Could not reach the MedShield API: ${e.message}`);
  }
})();

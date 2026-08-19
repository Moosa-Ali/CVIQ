/* CVIQ — frontend SPA (vanilla JS, no build step) */
'use strict';

/* ============================================================
   Global state
   ============================================================ */
const BEDROCK_ENABLED = false; // Hide AWS Bedrock in Settings until configured (flip to true to re-enable).
const PROVIDERS_LABEL = BEDROCK_ENABLED ? 'OpenRouter or AWS Bedrock' : 'OpenRouter';

let CURRENT_CV = emptyCV();
let META = { templates: [], accents: [], defaults: {} };
let OPT_STATE = {}; // optimize wizard state (per session)
// Set when the user applies accepted suggestions, to show a one-shot review
// banner in the Editor instead of forcing a "download or keep editing" choice.
let OPTIMIZE_REVIEW_PENDING = false;
let CHAT_LOG = [];
// Build view is a 2-stage flow: 0 = template gallery, 1 = build form.
const BUILD_STATE = { stage: 0, templateId: null };
// Unified template catalog from GET /api/templates (builtin + gallery), cached.
let TEMPLATE_CATALOG = null;
// LLM provider configured? Gates AI-only actions app-wide (§4.11).
let APP_CONFIGURED = false;
let CONFIG_BANNER_DISMISSED = false;
let ACTIVE_MODEL = '';
// Assistant panel state (optimize + editor).
let ASSISTANT_ACTIVE_TARGET = null; // {label, target} or null

let SESSION_COST = {
  prompt_tokens: 0,
  completion_tokens: 0,
  cost: 0.0,
};

function addUsage(usage) {
  if (!usage) return;
  SESSION_COST.prompt_tokens += usage.prompt_tokens || 0;
  SESSION_COST.completion_tokens += usage.completion_tokens || 0;
  SESSION_COST.cost += usage.cost || 0;
  if (usage.model) ACTIVE_MODEL = usage.model;
  renderSessionCost();
}

function renderSessionCost() {
  const el = document.getElementById('session-cost');
  if (!el) return;
  const cost = SESSION_COST.cost;
  const costStr = `$${cost.toFixed(cost === 0 ? 2 : 4)}`;
  el.innerHTML = ACTIVE_MODEL
    ? `<span class="session-cost-model">${esc(ACTIVE_MODEL)}</span><span class="session-cost-amount">${costStr}</span>`
    : costStr;
}

/* ============================================================
   Draft persistence (localStorage autosave)
   ============================================================ */
const DRAFT_KEY = 'cviq.draft.v1';
let DRAFT_DIRTY = false; // unsaved-work flag (beforeunload guard + indicator)
let DRAFT_TIMER = null;

function emptyCV() {
  return {
    template: 'modern',
    template_config: null,
    accent: '#2563eb',
    personal: { name: '', title: '', email: '', phone: '', location: '', website: '', linkedin: '', github: '' },
    summary: '',
    experience: [],
    education: [],
    skills: [],
    projects: [],
    certifications: [],
    languages: [],
    custom_sections: [],
    section_order: [],
    section_titles: {},
  };
}

function writeDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      cv: CURRENT_CV,
      opt: OPT_STATE,
      chat: CHAT_LOG,
      build: { stage: BUILD_STATE.stage, templateId: BUILD_STATE.templateId },
      updatedAt: new Date().toISOString(),
    }));
  } catch (e) { /* storage full / disabled — leave the draft untouched */ }
  DRAFT_DIRTY = false;
  updateDirtyIndicator();
}

function persistDraft() {
  if (DRAFT_TIMER) clearTimeout(DRAFT_TIMER);
  DRAFT_TIMER = setTimeout(writeDraft, 1000);
}

function markDirty() {
  DRAFT_DIRTY = true;
  updateDirtyIndicator();
  persistDraft();
}

function clearDraft() {
  DRAFT_DIRTY = false;
  if (DRAFT_TIMER) { clearTimeout(DRAFT_TIMER); DRAFT_TIMER = null; }
  try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ }
  updateDirtyIndicator();
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    return d && d.cv ? d : null;
  } catch (e) { return null; }
}

function restoreDraft(draft) {
  if (!draft || !draft.cv) return;
  CURRENT_CV = draft.cv;
  const opt = draft.opt && typeof draft.opt === 'object' ? draft.opt : {};
  OPT_STATE = Object.assign(defaultOptState(), {
    jobDescription: opt.jobDescription || '',
    report: opt.report || null,
    suggestions: Array.isArray(opt.suggestions) ? opt.suggestions : [],
    appliedIds: Array.isArray(opt.appliedIds) ? opt.appliedIds : [],
    scoreHistory: Array.isArray(opt.scoreHistory) ? opt.scoreHistory : [],
    sessionWarning: opt.sessionWarning || '',
    baseScore: opt.baseScore !== undefined ? opt.baseScore : null,
    suggestionsStale: !!opt.suggestionsStale,
  });
  CHAT_LOG = Array.isArray(draft.chat) ? draft.chat : [];
  if (draft.build) {
    BUILD_STATE.stage = draft.build.stage || 0;
    BUILD_STATE.templateId = draft.build.templateId || null;
  }
  DRAFT_DIRTY = false;
  updateDirtyIndicator();
  location.hash = '#/editor';
  router();
}

function showResumePrompt(draft) {
  const when = draft.updatedAt ? new Date(draft.updatedAt).toLocaleString() : 'a previous session';
  modal({
    title: 'Resume where you left off?',
    body: `<p>You have a saved draft from <strong>${esc(when)}</strong>.</p>
      <p class="small muted">Resume restores your CV, analysis and chat. “Start fresh” discards the saved draft.</p>`,
    confirmText: 'Resume',
    cancelText: 'Start fresh',
    onCancel: () => { clearDraft(); },
    onConfirm: () => { restoreDraft(draft); },
  });
}

function updateDirtyIndicator() {
  const el = document.getElementById('dirty-indicator');
  if (!el) return;
  if (DRAFT_DIRTY) {
    el.hidden = false;
    el.textContent = 'Unsaved changes';
  } else {
    el.hidden = true;
  }
}

/* ============================================================
   API helper
   ============================================================ */
async function api(path, options = {}) {
  const opts = { method: options.method || 'GET', headers: {}, ...options };
  if (opts.body && opts.body instanceof FormData) {
    // let browser set multipart boundary
  } else if (opts.body && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  let data = null;
  if (ct.includes('application/json')) data = await res.json();
  else data = await res.text();
  if (!res.ok) {
    let msg = 'Request failed';
    if (data && typeof data === 'object' && data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    else if (typeof data === 'string' && data) msg = data;
    throw new Error(msg);
  }
  return data;
}

/* ============================================================
   Toast + loading helpers
   ============================================================ */
// Map raw backend/exception strings to friendly, bounded messages (§7.5).
function friendlyError(msg) {
  const s = String(msg || '').trim();
  if (!s) return 'Something went wrong.';
  const lower = s.toLowerCase();
  if (lower.includes('invalid api key')) return 'Invalid API key — check it in Settings.';
  if (lower.includes('rate limited') || lower.includes('rate limit')) return 'Rate limited by the provider — wait and retry.';
  if (lower.includes('timed out') || lower.includes('timeout')) return 'The AI provider timed out — try again.';
  if (lower.includes('not configured') || lower.includes('no credentials')) return 'AI provider not configured — add your key in Settings.';
  return s.length > 160 ? s.slice(0, 157) + '…' : s;
}

function toast(message, type = 'info', duration = 4200) {
  const root = document.getElementById('toast-root');
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  const ic = type === 'success' ? 'check' : type === 'error' ? 'alert' : 'info';
  if (type === 'error') t.setAttribute('role', 'alert');
  else t.setAttribute('role', 'status');
  t.innerHTML = `<span class="toast-ic">${icon(ic)}</span><span>${esc(friendlyError(message))}</span>
    <button class="toast-close" aria-label="Dismiss">${icon('x')}</button>`;
  t.querySelector('.toast-close').addEventListener('click', () => t.remove());
  root.appendChild(t);
  if (duration) setTimeout(() => t.remove(), duration);
}

function setButtonLoading(btn, loading, label) {
  if (!btn) return;
  if (loading) {
    btn.dataset.orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${label || 'Working…'}`;
  } else {
    btn.disabled = false;
    if (btn.dataset.orig) btn.innerHTML = btn.dataset.orig;
  }
}

function showLoading(parent, text = 'Loading…') {
  parent.innerHTML = `<div class="loading-block">
    <img src="/assets/logo_no_text.png" alt="" class="loading-logo" />
    <span><span class="spinner"></span> ${esc(text)}</span>
  </div>`;
}

/* Skeleton loaders (§7.3): shimmering placeholder cards for the primary
   waits. Keep the spinner for tiny/inline ops only. */
function skeletonHTML(kind) {
  const card = (lines) => `<div class="skel-card">${lines.map((w) => `<span class="skel-line" style="width:${w}%"></span>`).join('')}</div>`;
  if (kind === 'parse') return `<div class="skel-stack">${card([45, 90, 70, 85, 40])}${card([60, 80, 50])}</div>`;
  if (kind === 'dashboard') return `<div class="skel-grid">${card([30, 80, 60])}${card([50, 90, 70])}${card([40, 75, 55])}</div>`;
  if (kind === 'suggestions') return `<div class="skel-stack">${card([55, 90, 65])}${card([45, 85, 60])}${card([50, 80, 55])}</div>`;
  if (kind === 'gaps') return `<div class="skel-stack">${card([35, 70, 50])}</div>`;
  if (kind === 'library') return `<div class="skel-grid">${card([40, 70, 55])}${card([40, 70, 55])}${card([40, 70, 55])}</div>`;
  if (kind === 'chat') return `<div class="skel-chat"><span class="skel-line" style="width:60%"></span><span class="skel-line" style="width:45%"></span></div>`;
  return `<div class="skel-stack">${card([50, 80, 60])}</div>`;
}

/* ============================================================
   Modal
   ============================================================ */
let MODAL_UID = 0;
function modal({ title, body, confirmText = 'OK', onConfirm, danger = false, cancelText = 'Cancel', onCancel, extraBtn = null }) {
  const root = document.getElementById('modal-root');
  const el = document.createElement('div');
  el.className = 'modal-backdrop';
  const titleId = 'modal-title-' + (++MODAL_UID);
  el.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
      <h3 id="${titleId}">${esc(title)}</h3>
      <div class="modal-body">${body}</div>
      <div class="btn-row">
        <button class="btn btn-secondary" data-act="close">${esc(cancelText)}</button>
        ${extraBtn ? `<button class="btn btn-secondary" data-act="extra">${esc(extraBtn.label)}</button>` : ''}
        <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="confirm">${esc(confirmText)}</button>
      </div>
    </div>`;
  const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
  const onKey = (e) => {
    if (e.key === 'Escape') { close(); return; }
    // Minimal focus trap: Tab cycles within the dialog (§9.2).
    if (e.key === 'Tab') {
      const focusables = el.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  el.querySelector('[data-act=close]').addEventListener('click', () => { if (onCancel) onCancel(); close(); });
  if (extraBtn) el.querySelector('[data-act=extra]').addEventListener('click', () => { if (extraBtn.onClick) extraBtn.onClick(); close(); });
  el.querySelector('[data-act=confirm]').addEventListener('click', async () => {
    if (onConfirm) { const r = await onConfirm(); if (r === false) return; }
    close();
  });
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  document.addEventListener('keydown', onKey);
  root.appendChild(el);
  // Initial focus: the primary action button, shortly after mount (§7.6).
  const primary = el.querySelector('[data-act=confirm]');
  if (primary) setTimeout(() => primary.focus(), 50);
  return close;
}

/* ============================================================
   Escaping helper
   ============================================================ */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ============================================================
   Router
   ============================================================ */
const app = document.getElementById('app');

function currentRoute() {
  const h = location.hash.replace(/^#/, '') || '/';
  const path = h.split('?')[0].replace(/^\/+/, '');
  return path || 'home';
}

const KNOWN_ROUTES = ['home', 'settings', 'build', 'optimize', 'editor', 'library'];

async function router() {
  const route = currentRoute();
  document.querySelectorAll('.nav-link').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === route.split('/')[0]);
  });
  if (!KNOWN_ROUTES.some((r) => route === r || route.startsWith(r + '/'))) {
    location.hash = '#/';
    toast("That page doesn't exist — took you home.", 'info');
    return;
  }
  try {
    if (route === 'home') renderHome();
    else if (route === 'settings') await renderSettings();
    else if (route === 'build') renderBuild(); // build state persists across visits (§6)
    else if (route === 'optimize') renderOptimize();
    else if (route === 'editor') renderEditor(route);
    else if (route === 'library') renderLibrary();
  } catch (err) {
    app.innerHTML = `<div class="empty-state">Something went wrong: ${esc(err.message)}</div>`;
  }
  maybePrependConfiguredBanner(route);
}

function maybePrependConfiguredBanner(route) {
  if (APP_CONFIGURED || CONFIG_BANNER_DISMISSED || route === 'settings') return;
  if (app.querySelector('#config-banner')) return;
  const banner = document.createElement('div');
  banner.className = 'banner config-banner';
  banner.id = 'config-banner';
  banner.innerHTML = `<div class="banner-body"><h3>${icon('alert')} AI features need a provider</h3>
    <p>Connect ${PROVIDERS_LABEL} to enable AI-powered CV analysis and writing.</p></div>
    <button class="btn btn-secondary btn-sm" id="config-banner-go">Configure now</button>
    <button class="btn btn-ghost btn-sm" id="config-banner-dismiss">Dismiss</button>`;
  app.prepend(banner);
  banner.querySelector('#config-banner-go').addEventListener('click', () => { CONFIG_BANNER_DISMISSED = true; location.hash = '#/settings'; });
  banner.querySelector('#config-banner-dismiss').addEventListener('click', () => { CONFIG_BANNER_DISMISSED = true; banner.remove(); });
}

function gateLLM() {
  if (APP_CONFIGURED) return true;
  toast('AI features need a provider — configure one in Settings', 'info');
  if (currentRoute() !== 'settings') location.hash = '#/settings';
  return false;
}

async function init() {
  window.addEventListener('hashchange', router);
  window.addEventListener('beforeunload', (e) => {
    if (DRAFT_DIRTY) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
  // Up/down reorder buttons are delegated on the persistent #app root (bound once).
  if (!app.dataset.moveBound) {
    app.dataset.moveBound = '1';
    app.addEventListener('click', onMoveClick);
  }
  if (!app.dataset.dragBound) {
    app.dataset.dragBound = '1';
    app.addEventListener('dragstart', onEditorDragStart);
    app.addEventListener('dragover', onEditorDragOver);
    app.addEventListener('drop', onEditorDrop);
    app.addEventListener('dragend', onEditorDragEnd);
  }
  // FE-3 delegated UI listeners (bound once): auto-grow, validation blur,
  // present toggle, skill chips, and Ctrl+S saving.
  if (!app.dataset.uiBound) {
    app.dataset.uiBound = '1';
    app.addEventListener('input', onGlobalInput);
    app.addEventListener('focusout', onGlobalFocusOut);
    app.addEventListener('change', onGlobalChange);
    app.addEventListener('click', onChipClick);
    app.addEventListener('keydown', onChipKeydown);
  }
  window.addEventListener('keydown', onGlobalKeydown);
  document.getElementById('brand').addEventListener('click', () => (location.hash = '#/'));
  try {
    META = await api('/api/meta');
    if (META && META.defaults) {
      CURRENT_CV.template = CURRENT_CV.template || META.defaults.template;
      CURRENT_CV.accent = CURRENT_CV.accent || META.defaults.accent;
    }
  } catch (e) {
    loadDefaultMeta();
  }
  try {
    const cfg = await api('/api/config');
    APP_CONFIGURED = !!(cfg && cfg.configured);
    if (cfg) ACTIVE_MODEL = cfg.provider === 'bedrock' ? (cfg.bedrock_model || '') : (cfg.openrouter_model || '');
  } catch (e) { /* leave false */ }
  renderSessionCost();
  await ensureTemplateCatalog();
  const draft = loadDraft();
  if (draft) showResumePrompt(draft);
  await router();
}

function loadDefaultMeta() {
  META = {
    templates: [
      { id: 'modern', name: 'Modern', heading_style: 'uppercase', layout: 'single', default_accent: '#2563eb' },
      { id: 'classic', name: 'Classic', heading_style: 'title', layout: 'single', default_accent: '#111827' },
      { id: 'minimal', name: 'Minimal', heading_style: 'uppercase', layout: 'single', default_accent: '#6b7280' },
    ],
    accents: [
      { id: 'blue', hex: '#2563eb', name: 'Blue' },
      { id: 'slate', hex: '#334155', name: 'Slate' },
      { id: 'emerald', hex: '#059669', name: 'Emerald' },
      { id: 'rose', hex: '#e11d48', name: 'Rose' },
      { id: 'violet', hex: '#7c3aed', name: 'Violet' },
      { id: 'amber', hex: '#d97706', name: 'Amber' },
    ],
    defaults: { template: 'modern', accent: '#2563eb' },
  };
}

/* ============================================================
   Unified template catalog (§4.5) — GET /api/templates, cached
   ============================================================ */
function builtinFallbackTemplates() {
  return [
    { id: 'modern', name: 'Modern', source: 'builtin', render_template: 'modern', converted: true, pages: 0, preview_url: '' },
    { id: 'classic', name: 'Classic', source: 'builtin', render_template: 'classic', converted: true, pages: 0, preview_url: '' },
    { id: 'minimal', name: 'Minimal', source: 'builtin', render_template: 'minimal', converted: true, pages: 0, preview_url: '' },
  ];
}

async function ensureTemplateCatalog() {
  if (TEMPLATE_CATALOG) return TEMPLATE_CATALOG;
  try {
    const data = await api('/api/templates');
    TEMPLATE_CATALOG = (data && data.templates) || [];
  } catch (e) {
    TEMPLATE_CATALOG = builtinFallbackTemplates();
  }
  return TEMPLATE_CATALOG;
}

function templateNameForId(id) {
  if (!id) return '';
  const cat = (TEMPLATE_CATALOG || []).find((t) => t.id === id);
  if (cat) return cat.name;
  const meta = (META.templates || []).find((t) => t.id === id);
  return meta ? meta.name : '';
}

// Friendly subtitle for gallery designs — never leaks "converted/fallback".
function tplSubtitle(t) {
  if (!t || t.source === 'builtin') return '';
  if (t.converted === false) {
    const rt = (TEMPLATE_CATALOG || []).find((x) => x.id === t.render_template);
    return `Closest match — ${rt ? rt.name : 'Simple layout'}`;
  }
  return 'Dedicated design';
}

/* ============================================================
   Shared: template / accent picker
   ============================================================ */
function templatePickerHTML() {
  const accents = META.accents || [];
  const tpls = TEMPLATE_CATALOG || builtinFallbackTemplates();
  const builtin = tpls.filter((t) => t.source === 'builtin');
  const gallery = tpls.filter((t) => t.source !== 'builtin');
  const opt = (t) => {
    const sub = t.source !== 'builtin' ? `<span class="tpl-opt-sub">${esc(tplSubtitle(t))}</span>` : '';
    return `<button type="button" class="tpl-opt ${CURRENT_CV.template === t.id ? 'active' : ''}" data-tpl="${esc(t.id)}" title="${esc(t.name)}">
      ${t.source === 'builtin'
        ? `<span class="tpl-swatch" style="background:${esc(safeAccent(CURRENT_CV.accent))}">${esc((t.name || 'T').charAt(0).toUpperCase())}</span>`
        : `<img class="tpl-opt-thumb" src="${esc(t.preview_url)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`}
      <span class="tpl-opt-name">${esc(t.name)}${sub}</span>
    </button>`;
  };
  const group = (title, list) => list.length
    ? `<div class="field"><label>${title}</label><div class="tpl-opts">${list.map(opt).join('')}</div></div>`
    : '';
  let html = `<div class="card"><h2>Template &amp; Accent</h2>`;
  html += group('Simple layouts', builtin);
  html += group('Gallery designs', gallery);
  html += `<div class="field"><label>Accent</label><div class="flex" id="accent-picker">`;
  accents.forEach((a) => {
    const active = (CURRENT_CV.accent || '').toLowerCase() === String(a.hex).toLowerCase();
    html += `<button type="button" data-accent="${esc(a.hex)}" title="${esc(a.name)}" style="width:34px;height:34px;border-radius:9px;border:2px solid ${active ? '#1e293b' : 'transparent'};background:${esc(a.hex)};cursor:pointer"></button>`;
  });
  html += `<input type="color" id="custom-accent" value="${esc(CURRENT_CV.accent || '#2563eb')}" title="Custom color" style="width:34px;height:34px;border:none;background:none;padding:0">`;
  html += `</div></div></div>`;
  return html;
}

function syncAccentBorders(rootEl) {
  rootEl.querySelectorAll('[data-accent]').forEach((x) => {
    const match = String(x.dataset.accent).toLowerCase() === String(CURRENT_CV.accent).toLowerCase();
    x.style.borderColor = match ? '#1e293b' : 'transparent';
  });
  const colr = rootEl.querySelector('#custom-accent');
  if (colr) {
    colr.value = CURRENT_CV.accent || '#2563eb';
    colr.style.borderColor = 'transparent';
  }
}

function bindTemplatePicker(root) {
  root.querySelectorAll('[data-tpl]').forEach((b) => {
    b.addEventListener('click', () => {
      CURRENT_CV.template = b.dataset.tpl;
      const tpl = (TEMPLATE_CATALOG || builtinFallbackTemplates()).find((t) => t.id === CURRENT_CV.template);
      if (tpl && tpl.source === 'builtin' && tpl.default_accent && !CURRENT_CV.__accentLocked) {
        CURRENT_CV.accent = tpl.default_accent;
      }
      root.querySelectorAll('[data-tpl]').forEach((x) => x.classList.toggle('active', x === b));
      syncAccentBorders(root);
      markDirty();
      refreshPreview();
    });
  });
  root.querySelectorAll('[data-accent]').forEach((b) => {
    b.addEventListener('click', () => {
      CURRENT_CV.accent = b.dataset.accent;
      CURRENT_CV.__accentLocked = true;
      syncAccentBorders(root);
      markDirty();
      refreshPreview();
    });
  });
  const col = root.querySelector('#custom-accent');
  if (col) {
    col.addEventListener('input', () => {
      CURRENT_CV.accent = col.value;
      CURRENT_CV.__accentLocked = true;
      syncAccentBorders(root);
      markDirty();
      refreshPreview();
    });
  }
}

/* ============================================================
   VIEW: Settings
   ============================================================ */
async function renderSettings() {
  showLoading(app);
  let cfg = null;
  try {
    cfg = await api('/api/config');
  } catch (e) { /* ignore */ }
  // Resolve saved provider; never surface 'bedrock' while BEDROCK_ENABLED is false.
  const provider = (BEDROCK_ENABLED && cfg && cfg.provider) || 'openrouter';
  // Backend reports `configured` as a plain boolean (cfg.configured()).
  const anyConfigured = !cfg || !!cfg.configured;

  // The API redacts secret values as '***' — treat those verbatim (masked).
  const isMasked = (v) => v === '***';
  const pp = (v) => (isMasked(v) ? '***' : (v || ''));
  const pw = (v) => (isMasked(v) ? '***' : (v || ''));

  const onboarding = cfg && !anyConfigured
    ? `<div class="banner"><div><h3>${icon('alert')} LLM provider not configured</h3>
       <p>Connect an LLM provider (${PROVIDERS_LABEL}) to enable AI features like parsing, analysis and tailoring.</p></div></div>`
    : '';

  app.innerHTML = `
    <div class="view-header">
      <h1>Settings</h1>
      <p>Configure the LLM provider used for AI-powered CV features.</p>
    </div>
    ${onboarding}
    <form id="settings-form">
      <div class="card">
        <h2>Provider</h2>
        <div class="segmented" id="provider-toggle">
          <button type="button" class="${provider === 'openrouter' ? 'active' : ''}" data-provider="openrouter">OpenRouter</button>
          ${BEDROCK_ENABLED ? `<button type="button" class="${provider === 'bedrock' ? 'active' : ''}" data-provider="bedrock">AWS Bedrock</button>` : ''}
        </div>
        <div id="provider-panes" class="mt"></div>
      </div>
      <div class="card">
        <h2>Model Pricing (USD)</h2>
        <p class="small muted mb">Cost per 1M tokens — applies to every model. Set these to match your provider's published prices.</p>
        <div class="field">
          <label for="price_per_1m_prompt">Prompt / 1M tokens ($)</label>
          <input type="number" step="0.01" min="0" id="price_per_1m_prompt" value="${(cfg && cfg.price_per_1m_prompt != null) ? cfg.price_per_1m_prompt : 0}">
        </div>
        <div class="field">
          <label for="price_per_1m_completion">Completion / 1M tokens ($)</label>
          <input type="number" step="0.01" min="0" id="price_per_1m_completion" value="${(cfg && cfg.price_per_1m_completion != null) ? cfg.price_per_1m_completion : 0}">
        </div>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn-secondary" id="btn-test">Test Connection</button>
        <button type="submit" class="btn btn-primary">Save Configuration</button>
      </div>
    </form>`;

  const panes = document.getElementById('provider-panes');
  let providerActive = provider;
  function renderPanes() {
    const isOpen = BEDROCK_ENABLED ? providerActive === 'openrouter' : true;
    panes.innerHTML = `
      ${isOpen ? `
        <div class="field">
          <label for="openrouter_api_key">OpenRouter API Key</label>
          <input type="password" id="openrouter_api_key" autocomplete="off" placeholder="sk-or-..." value="${esc(pp(cfg && cfg.openrouter_api_key))}">
        </div>
        <div class="field">
          <label for="openrouter_model">Model</label>
          <input type="text" id="openrouter_model" value="${esc((cfg && cfg.openrouter_model) || 'anthropic/claude-sonnet-4-6')}">
        </div>` : `
        <div class="field">
          <label for="bedrock_access_key">AWS Access Key</label>
          <input type="password" id="bedrock_access_key" autocomplete="off" value="${esc(pw(cfg && cfg.bedrock_access_key))}">
        </div>
        <div class="field">
          <label for="bedrock_secret_key">AWS Secret Key</label>
          <input type="password" id="bedrock_secret_key" autocomplete="off" value="${esc((cfg && cfg.bedrock_secret_key) || '')}">
        </div>
        <div class="field">
          <label for="bedrock_region">Region</label>
          <input type="text" id="bedrock_region" placeholder="us-east-1" value="${esc((cfg && cfg.bedrock_region) || '')}">
        </div>
        <div class="field">
          <label for="bedrock_model">Model</label>
          <input type="text" id="bedrock_model" value="${esc((cfg && cfg.bedrock_model) || 'anthropic.claude-sonnet-4-5-v2-0')}">
        </div>`}
      ${providerActive === 'bedrock' && (!cfg?.bedrock_secret_key && cfg?.configured) ? `
        <p class="small muted">Secret key is stored and redacted. Leave blank to keep the saved value.</p>` : ''}`;
  }
  renderPanes();

  document.getElementById('provider-toggle').addEventListener('click', (e) => {
    const b = e.target.closest('[data-provider]');
    if (!b) return;
    document.querySelectorAll('#provider-toggle [data-provider]').forEach((x) => x.classList.toggle('active', x === b));
    providerActive = b.dataset.provider;
    renderPanes();
  });

  function collect() {
    const g = (id) => (document.getElementById(id) ? document.getElementById(id).value : '');

    return {
      provider: BEDROCK_ENABLED ? providerActive : 'openrouter',
      openrouter_api_key: g('openrouter_api_key'),
      openrouter_model: g('openrouter_model'),
      bedrock_access_key: g('bedrock_access_key'),
      bedrock_secret_key: g('bedrock_secret_key'),
      bedrock_region: g('bedrock_region'),
      bedrock_model: g('bedrock_model'),
      price_per_1m_prompt: parseFloat(g('price_per_1m_prompt')) || 0,
      price_per_1m_completion: parseFloat(g('price_per_1m_completion')) || 0,
    };
  }

  document.getElementById('btn-test').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    setButtonLoading(btn, true, 'Testing…');
    try {
      const r = await api('/api/config/test', { method: 'POST', body: collect() });
      toast(r.message + (r.model ? ` (${r.model})` : ''), 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setButtonLoading(btn, false);
    }
  });

  document.getElementById('settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    setButtonLoading(btn, true, 'Saving…');
    try {
      await api('/api/config', { method: 'POST', body: collect() });
      toast('Configuration saved', 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setButtonLoading(btn, false);
    }
  });
}

/* ============================================================
   VIEW: Build (from scratch)
   ============================================================ */
function renderBuild() {
  if (BUILD_STATE.stage === 0) { renderBuildGallery(); return; }
  renderBuildForm();
}

/* --- Stage 0: template gallery --------------------------------- */
async function renderBuildGallery() {
  const PRESETS = {
    "modern": { font: "sans", header_alignment: "center", header_divider: true, section_divider: true, heading_case: "upper" },
    "classic": { font: "serif", header_alignment: "center", header_divider: true, section_divider: true, heading_case: "title" },
    "minimal": { font: "sans", header_alignment: "left", header_divider: false, section_divider: false, heading_case: "upper" },
    "awesome-cv": { font: "sans", header_alignment: "left", header_divider: true, section_divider: true, heading_case: "upper" },
    "deedy-resume": { font: "sans", header_alignment: "center", header_divider: true, section_divider: true, heading_case: "upper" },
    "cvresume": { font: "serif", header_alignment: "left", header_divider: true, section_divider: true, heading_case: "upper" },
    "universal-resume": { font: "sans", header_alignment: "center", header_divider: true, section_divider: true, heading_case: "upper" },
    "newfuture-cv": { font: "sans", header_alignment: "center", header_divider: false, section_divider: false, heading_case: "upper" },
  };

  const render = () => {
    const config = CURRENT_CV.template_config || PRESETS[CURRENT_CV.template || 'modern'];
    
    app.innerHTML = `
      <div class="view-header">
        <h1>Build — customize your layout</h1>
        <p>Adjust the visual style of your CV. Changes are reflected in the live preview.</p>
      </div>
      <div class="editor-grid">
        <div class="editor-form-pane">
          <div class="card">
            <h2>Layout Toggles</h2>
            <div class="field">
              <label>Header Alignment</label>
              <div class="segmented">
                <button type="button" class="${config.header_alignment === 'left' ? 'active' : ''}" data-cfg="header_alignment" data-val="left">Left</button>
                <button type="button" class="${config.header_alignment === 'center' ? 'active' : ''}" data-cfg="header_alignment" data-val="center">Center</button>
              </div>
            </div>
            <div class="field">
              <label>Font</label>
              <div class="segmented">
                <button type="button" class="${config.font === 'sans' ? 'active' : ''}" data-cfg="font" data-val="sans">Sans</button>
                <button type="button" class="${config.font === 'serif' ? 'active' : ''}" data-cfg="font" data-val="serif">Serif</button>
              </div>
            </div>
            <div class="field">
              <label>Header Divider</label>
              <button type="button" class="btn btn-sm ${config.header_divider ? 'btn-primary' : 'btn-secondary'}" data-cfg="header_divider" data-val="${!config.header_divider}">
                ${config.header_divider ? 'On' : 'Off'}
              </button>
            </div>
            <div class="field">
              <label>Section Divider</label>
              <button type="button" class="btn btn-sm ${config.section_divider ? 'btn-primary' : 'btn-secondary'}" data-cfg="section_divider" data-val="${!config.section_divider}">
                ${config.section_divider ? 'On' : 'Off'}
              </button>
            </div>
            <div class="field">
              <label>Heading Case</label>
              <div class="segmented">
                <button type="button" class="${config.heading_case === 'upper' ? 'active' : ''}" data-cfg="heading_case" data-val="upper">UPPERCASE</button>
                <button type="button" class="${config.heading_case === 'title' ? 'active' : ''}" data-cfg="heading_case" data-val="title">Title Case</button>
              </div>
            </div>
            <div class="field mt">
              <label>Presets</label>
              <div class="tpl-opts">
                ${Object.keys(PRESETS).map(id => `
                  <button type="button" class="tpl-opt ${CURRENT_CV.template === id ? 'active' : ''}" data-preset="${id}">
                    <span class="tpl-opt-name">${esc(templateNameForId(id) || id)}</span>
                  </button>
                `).join('')}
              </div>
            </div>
            <div class="btn-row mt">
              <button class="btn btn-primary" id="btn-continue">Continue to form →</button>
              <button class="btn btn-ghost" id="btn-clear">Reset</button>
            </div>
          </div>
        </div>
        <div class="editor-preview-pane">
          <div class="preview-wrap">
            <div class="preview-frame">
              <div class="preview-toolbar">
                <span class="title">Live Layout Preview</span>
              </div>
              <div class="preview-viewport">
                <div class="preview-page">
                  <iframe id="build-preview" title="CV preview"></iframe>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>`;

    bindTogglePicker();
    updateBuildPreview();
  };

  function updateBuildPreview() {
    const update = async () => {
      const frame = document.getElementById('build-preview');
      if (!frame) return;
      try {
        const r = await api('/api/export/preview', {
          method: 'POST',
          body: { cv: CURRENT_CV, template: CURRENT_CV.template || 'modern' },
        });
        const f = document.getElementById('build-preview');
        if (f) f.srcdoc = (r && r.html) || '';
      } catch (e) { console.error('Preview failed', e); }
    };

    if (window.buildPreviewTimer) clearTimeout(window.buildPreviewTimer);
    window.buildPreviewTimer = setTimeout(update, 300);
  }

  function bindTogglePicker() {
    app.querySelectorAll('[data-cfg]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.cfg;
        const val = btn.dataset.val === 'true' ? true : btn.dataset.val === 'false' ? false : btn.dataset.val;
        
        CURRENT_CV.template_config = CURRENT_CV.template_config || { ...PRESETS[CURRENT_CV.template || 'modern'] };
        CURRENT_CV.template_config[key] = val;
        
        markDirty();
        render();
        updateBuildPreview();
      });
    });

    app.querySelectorAll('[data-preset]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.preset;
        CURRENT_CV.template = id;
        CURRENT_CV.template_config = { ...PRESETS[id] };
        markDirty();
        render();
        updateBuildPreview();
      });
    });

    const cont = document.getElementById('btn-continue');
    if (cont) cont.addEventListener('click', () => { BUILD_STATE.stage = 1; renderBuild(); });
    
    const clear = document.getElementById('btn-clear');
    if (clear) clear.addEventListener('click', () => confirmReset(() => renderBuild()));
  }

  render();
}

function buildGalleryCardHTML() {
  const tpls = TEMPLATE_CATALOG || builtinFallbackTemplates();
  const cards = [];
  const blankSel = BUILD_STATE.templateId === null ? ' selected' : '';
  cards.push(`
    <button type="button" class="tpl-card blank${blankSel}" data-tpl-id="" aria-pressed="${blankSel ? 'true' : 'false'}">
      <span class="tpl-thumb blank-thumb"><span class="tpl-blank-icon">${icon('plus')}</span><span class="tpl-blank-label">Blank / Simple</span></span>
      <span class="tpl-meta"><span class="tpl-name">Blank / Simple</span><span class="tpl-badge">start from scratch</span></span>
    </button>`);
  tpls.forEach((t) => {
    const pages = t.source === 'gallery' ? (parseInt(t.pages, 10) || 1) : '';
    const sel = BUILD_STATE.templateId === t.id ? ' selected' : '';
    const sub = tplSubtitle(t);
    const pageBadge = pages ? `<span class="tpl-badge">${pages} page${pages === 1 ? '' : 's'}</span>` : '';
    const subBadge = sub ? `<span class="tpl-badge tpl-badge-sub">${esc(sub)}</span>` : '';
    const thumb = t.source === 'gallery'
      ? `<img class="tpl-thumb" src="${esc(t.preview_url)}" alt="${esc(t.name)} preview" loading="lazy" onerror="this.style.visibility='hidden'" />`
      : `<span class="tpl-thumb blank-thumb"><span class="tpl-blank-icon">${esc(t.name.charAt(0).toUpperCase())}</span><span class="tpl-blank-label">${esc(t.name)}</span></span>`;
    cards.push(`
      <button type="button" class="tpl-card${sel}" data-tpl-id="${esc(t.id)}" aria-pressed="${sel ? 'true' : 'false'}">
        ${thumb}
        <span class="tpl-meta"><span class="tpl-name">${esc(t.name)}</span>
          <span class="tpl-badges">${pageBadge}${subBadge}</span></span>
      </button>`);
  });
  return cards.join('');
}

function bindBuildGallery() {
  const libBtn = app.querySelector('#btn-pick-library');
  if (libBtn) libBtn.addEventListener('click', () => libraryPicker((rec) => { CURRENT_CV = rec; markDirty(); renderBuild(); }));
  const clear = app.querySelector('#btn-clear');
  if (clear) clear.addEventListener('click', () => confirmReset(() => renderBuild()));
  app.querySelectorAll('.tpl-card').forEach((card) => {
    card.addEventListener('click', () => {
      BUILD_STATE.templateId = card.dataset.tplId === '' ? null : card.dataset.tplId;
      BUILD_STATE.stage = 1;
      markDirty();
      renderBuild();
    });
  });
}

function confirmReset(afterReset) {
  modal({
    title: 'Reset CV?',
    body: '<p>This clears the current CV and any saved draft. This cannot be undone.</p>',
    confirmText: 'Reset',
    danger: true,
    onConfirm: () => {
      CURRENT_CV = emptyCV();
      clearDraft();
      if (afterReset) afterReset();
    },
  });
}

function selectedTemplateName() {
  if (!BUILD_STATE.templateId) return 'Blank / Simple';
  const t = (TEMPLATE_CATALOG || []).find((x) => x.id === BUILD_STATE.templateId);
  return t ? t.name : BUILD_STATE.templateId;
}

/* --- Stage 1: build form --------------------------------------- */
function renderBuildForm() {
  const c = CURRENT_CV;
  const hasTpl = !!BUILD_STATE.templateId;
  const tplBtns = hasTpl ? `
      <button class="btn btn-primary" id="btn-tpl-pdf">${icon('download')} Download PDF (template)</button>
      <button class="btn btn-primary" id="btn-tpl-docx">${icon('download')} Download Word (template)</button>` : '';

  app.innerHTML = `
    <div class="view-header">
      <h1>Build</h1>
      <p>Create a CV from scratch. Use AI assist to draft summaries and impact bullets, then continue to the editor.</p>
    </div>

    <div class="banner neutral build-selected">
      <div class="banner-body"><strong>Selected template:</strong> ${esc(selectedTemplateName())}</div>
      <button type="button" class="btn btn-secondary btn-sm" id="btn-change-tpl">← Change template</button>
    </div>

    <div class="btn-row mb">
      <button class="btn btn-secondary" id="btn-pick-library">${icon('library')} My CVs</button>
      <button class="btn btn-ghost" id="btn-clear">Reset</button>
    </div>

    ${hasTpl ? '' : templatePickerHTML()}

    <form id="build-form">
      ${sectionCard('Personal', personalFieldsHTML(c.personal), 'build-personal')}
      ${sectionCard('Summary', summaryHTML(c.summary), 'build-summary')}
      ${sectionCard('Experience', repeaterListHTML('experience'), 'build-experience')}
      ${sectionCard('Education', repeaterListHTML('education'), 'build-education')}
      ${sectionCard('Skills', skillsHTML(c.skills), 'build-skills')}
      ${sectionCard('Projects', repeaterListHTML('projects'), 'build-projects')}
      ${sectionCard('Certifications', repeaterListHTML('certifications'), 'build-certifications')}
      ${sectionCard('Languages', languagesHTML(c.languages), 'build-languages')}
      ${sectionCard('Custom sections', customSectionsHTML(c.custom_sections), 'build-custom-sections')}

      <div class="btn-row">
        <a href="#/editor" class="btn btn-secondary" style="text-decoration:none">Continue to Editor →</a>
        <span class="small muted" style="flex:1"></span>
        ${tplBtns}
      </div>
    </form>`;

  bindBuild();
}

function sectionCard(title, body, id) {
  return `<div class="card" id="${esc(id)}"><h2>${esc(title)}</h2>${body}</div>`;
}

function personalFieldsHTML(p) {
  const f = (k, label, ph = '', type = 'text') =>
    `<div class="field"><label>${label}</label><input type="${type}" class="personal" data-k="${k}" value="${esc(p[k])}" placeholder="${esc(ph)}"></div>`;
  return `
    <div class="section-row">
      <div class="section-col">${f('name', 'Full Name', 'Jane Doe')}${f('title', 'Professional Title', 'Senior Software Engineer')}</div>
      <div class="section-col">${f('email', 'Email', 'jane@example.com', 'email')}${f('phone', 'Phone', '+1 555 0100')}</div>
    </div>
    <div class="section-row">
      <div class="section-col">${f('location', 'Location', 'Berlin, Germany')}${f('website', 'Website', 'https://')}</div>
      <div class="section-col">${f('linkedin', 'LinkedIn', 'linkedin.com/in/...')}${f('github', 'GitHub', 'github.com/...')}</div>
    </div>`;
}

function summaryHTML(summary) {
  return `
    <div class="field">
      <label>Professional Summary</label>
      <textarea class="summary-input" placeholder="A concise, professional summary of your experience…">${esc(summary)}</textarea>
      <div class="char-count" id="summary-count">${(summary || '').length.toLocaleString()} / 3,000</div>
      <div class="hint">Tip: use AI assist to draft this from your details.</div>
    </div>
    <div class="btn-row">
      <button type="button" class="btn btn-secondary btn-sm" data-assist="summary">${icon('sparkles')} AI Assist Summary</button>
      <button type="button" class="btn btn-secondary btn-sm" data-assist="bullets">${icon('sparkles')} AI Assist Bullets</button>
      <button type="button" class="btn btn-secondary btn-sm" data-opt="sum">${icon('sparkles')} Optimize</button>
      <span class="small muted assist-note"></span>
    </div>`;
}

function repeaterListHTML(kind) {
  const list = CURRENT_CV[kind];
  const itemTemplate = (item, i) => {
    if (kind === 'experience') {
      const b = item.bullets || [];
      return `<div class="repeater-item" data-index="${i}">
        <div class="item-head"><span>Experience ${i + 1}</span><span class="item-actions">${moveBtns(kind, i)}<button type="button" class="btn btn-secondary btn-sm" data-opt="bullets" data-i="${i}">${icon('sparkles')} Optimize</button><button type="button" class="btn btn-ghost btn-sm" data-dup="${kind}:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="remove-btn" data-remove="${i}">Remove</button></span></div>
        <div class="fields">
          ${field('company', 'Company', item.company, i)}${field('role', 'Role', item.role, i)}
          ${field('location', 'Location', item.location, i)}
          <div class="full fields" style="grid-column:1/-1">
            ${datefield('dates.start', 'Start', item.dates.start, i)}${datefieldEnd('dates.end', 'End', item.dates.end, i, kind)}
          </div>
          <div class="full"><label>Bullets</label><div class="bullet-list" data-bullet-list="${i}">
            ${(b.length ? b : ['']).map((bl, bi) => `<div class="bullet-row">${moveBulletBtns(kind, i, bi)}<textarea class="bullet-grow" rows="1" data-bullet="${i}" data-bi="${bi}" placeholder="Achievement…">${esc(bl)}</textarea><button type="button" class="bullet-del" data-bdel="${i}" data-bi="${bi}" aria-label="Remove bullet">${icon('x')}</button></div>`).join('')}
          </div><button type="button" class="btn btn-ghost btn-sm mt" data-badd="${i}">+ Add bullet</button></div>
        </div>
      </div>`;
    }
    if (kind === 'education') {
      return `<div class="repeater-item" data-index="${i}">
        <div class="item-head"><span>Education ${i + 1}</span><span class="item-actions">${moveBtns(kind, i)}<button type="button" class="btn btn-ghost btn-sm" data-dup="${kind}:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="remove-btn" data-remove="${i}">Remove</button></span></div>
        <div class="fields">
          ${field('institution', 'Institution', item.institution, i)}${field('degree', 'Degree', item.degree, i)}
          ${field('field', 'Field', item.field, i)}${field('gpa', 'GPA', item.gpa, i)}
          <div class="span2">${datefield('dates.start', 'Start', item.dates.start, i)}${datefieldEnd('dates.end', 'End', item.dates.end, i, kind)}</div>
        </div>
      </div>`;
    }
    if (kind === 'projects') {
      const b = item.bullets || [];
      return `<div class="repeater-item" data-index="${i}">
        <div class="item-head"><span>Project ${i + 1}</span><span class="item-actions">${moveBtns(kind, i)}<button type="button" class="btn btn-secondary btn-sm" data-opt="proj" data-i="${i}">${icon('sparkles')} Optimize</button><button type="button" class="btn btn-ghost btn-sm" data-dup="${kind}:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="remove-btn" data-remove="${i}">Remove</button></span></div>
        <div class="fields">
          ${field('name', 'Name', item.name, i)}${field('link', 'Link', item.link, i)}
          <div class="full"><label>Description</label><textarea data-k="description" data-i="${i}">${esc(item.description)}</textarea></div>
          <div class="full"><label>Bullets</label><div class="bullet-list" data-bullet-list="${i}">
            ${(b.length ? b : ['']).map((bl, bi) => `<div class="bullet-row">${moveBulletBtns(kind, i, bi)}<textarea class="bullet-grow" rows="1" data-bullet="${i}" data-bi="${bi}">${esc(bl)}</textarea><button type="button" class="bullet-del" data-bdel="${i}" data-bi="${bi}" aria-label="Remove bullet">${icon('x')}</button></div>`).join('')}
          </div><button type="button" class="btn btn-ghost btn-sm mt" data-badd="${i}">+ Add bullet</button></div>
        </div>
      </div>`;
    }
    if (kind === 'certifications') {
      return `<div class="repeater-item" data-index="${i}">
        <div class="item-head"><span>Certification ${i + 1}</span><span class="item-actions">${moveBtns(kind, i)}<button type="button" class="btn btn-ghost btn-sm" data-dup="${kind}:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="remove-btn" data-remove="${i}">Remove</button></span></div>
        <div class="fields">
          ${field('name', 'Name', item.name, i)}${field('issuer', 'Issuer', item.issuer, i)}
          ${field('year', 'Year', item.year, i)}
        </div>
      </div>`;
    }
    return '';
  };
  const body = list.length
    ? list.map(itemTemplate).join('')
    : '<p class="empty-hint small">None yet — add one below.</p>';
  return `
    <div class="repeater-list" data-kind="${kind}">${body}</div>
    <button type="button" class="btn btn-secondary btn-sm" data-add="${kind}">+ Add ${kind === 'certifications' ? 'certification' : singular(kind)}</button>`;
}

function datefieldEnd(k, label, val, i, kind) {
  const present = val === 'Present';
  return `<div class="field"><label>${label}</label>
    <input type="text" data-k="${k}" data-i="${i}" value="${esc(present ? '' : val)}" placeholder="e.g. 2018-05" ${present ? 'disabled' : ''}>
    <label class="present-toggle"><input type="checkbox" data-present="${kind}:${i}" ${present ? 'checked' : ''}> Present / current</label>
  </div>`;
}

function singular(kind) {
  return { experience: 'experience', education: 'education', projects: 'project', certifications: 'certification' }[kind] || kind;
}

function field(k, label, val, i) {
  return `<div class="field"><label>${label}</label><input type="text" data-k="${k}" data-i="${i}" value="${esc(val)}"></div>`;
}
function datefield(k, label, val, i) {
  return `<div class="field"><label>${label}</label><input type="text" data-k="${k}" data-i="${i}" value="${esc(val)}" placeholder="e.g. 2018-05"></div>`;
}

function skillsHTML(skills) {
  const groups = skills.length ? skills : [{ category: '', skills: [] }];
  const body = groups.map((g, gi) => `
    <div class="repeater-item skill-group" data-skill-group="${gi}">
      <div class="item-head"><span>Skill group ${gi + 1}</span><span class="item-actions">${moveBtns('skills', gi)}<button type="button" class="btn btn-ghost btn-sm" data-dup="skills:${gi}" title="Duplicate group">${icon('copy')}</button><button type="button" class="remove-btn" data-skillgroup-rm="${gi}">Remove</button></span></div>
      <div class="field"><label>Category</label><input type="text" data-skill-cat="${gi}" value="${esc(g.category)}" placeholder="e.g. Programming Languages"></div>
      <div class="field"><label>Skills</label><div class="skill-chips" data-skill-chips="${gi}"></div></div>
    </div>`).join('');
  return `<div id="skills-groups">${body}</div>
    <div class="btn-row mt">
      <button type="button" class="btn btn-secondary btn-sm" id="add-skill-group">+ Add skill group</button>
      <button type="button" class="btn btn-secondary btn-sm" data-opt="skills">${icon('sparkles')} Optimize</button>
    </div>`;
}

function languagesHTML(langs) {
  const rows = langs.length ? langs : [{ name: '', level: '' }];
  const body = rows.map((l, i) => `
    <div class="repeater-item" data-lang="${i}">
      <div class="fields">
        <div class="field"><label>Language</label><input type="text" data-lang-name="${i}" value="${esc(l.name)}" placeholder="e.g. English"></div>
        <div class="field"><label>Level</label><input type="text" data-lang-level="${i}" value="${esc(l.level)}" placeholder="e.g. Native / B2"></div>
      </div>
      <div class="item-actions mt">${moveBtns('languages', i)}<button type="button" class="btn btn-ghost btn-sm" data-dup="languages:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="btn btn-ghost btn-sm" data-lang-rm="${i}">Remove</button></div>
    </div>`).join('');
  return `<div id="langs-list">${body}</div>
    <button type="button" class="btn btn-secondary btn-sm" id="add-lang">+ Add language</button>`;
}

function customSectionsHTML(sections) {
  const list = Array.isArray(sections) ? sections : [];
  const body = list.map((sec, i) => `
    <div class="repeater-item" data-cs="${i}">
      <div class="item-head"><span>Custom section ${i + 1}</span><span class="item-actions">${moveBtns('custom_sections', i)}<button type="button" class="btn btn-ghost btn-sm" data-dup="custom_sections:${i}" title="Duplicate section">${icon('copy')}</button><button type="button" class="remove-btn" data-cs-rm="${i}">Remove</button></span></div>
      <div class="field"><label>Title</label><input type="text" data-cs-title="${i}" value="${esc(sec.title)}" placeholder="e.g. Awards, Publications, Volunteering"></div>
      <div class="field"><label>Bullets</label><div class="bullet-list" data-cs-bullets="${i}">
        ${(sec.bullets && sec.bullets.length ? sec.bullets : ['']).map((bl, bi) => `<div class="bullet-row">${moveBulletBtns('custom_sections', i, bi)}<textarea class="bullet-grow" rows="1" data-cs-bullet="${i}" data-bi="${bi}" placeholder="Accomplishment…">${esc(bl)}</textarea><button type="button" class="bullet-del" data-cs-bdel="${i}" data-bi="${bi}" aria-label="Remove bullet">${icon('x')}</button></div>`).join('')}
      </div><button type="button" class="btn btn-ghost btn-sm mt" data-cs-badd="${i}">+ Add bullet</button></div>
    </div>`).join('');
  return `<div id="cs-list">${body || '<p class="empty-hint small">None yet — add one below.</p>'}</div>
    <button type="button" class="btn btn-secondary btn-sm" id="add-custom-section">+ Add custom section</button>`;
}

function bindBuild() {
  const c = CURRENT_CV;
  const form = document.getElementById('build-form');
  const root = app;

  // personal inputs
  root.querySelectorAll('.personal').forEach((inp) => {
    inp.addEventListener('input', () => { c.personal[inp.dataset.k] = inp.value; markDirty(); syncPreview(); });
  });

  // summary
  const sum = root.querySelector('.summary-input');
  if (sum) sum.addEventListener('input', () => { c.summary = sum.value; markDirty(); syncPreview(); });

  // skill chip rows (rendered from the model after each re-render)
  root.querySelectorAll('[data-skill-chips]').forEach((host) => {
    renderChipRow(host, parseInt(host.dataset.skillChips, 10), false);
  });

  // AI assist
  root.querySelectorAll('[data-assist]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!gateLLM()) return;
      const kind = btn.dataset.assist;
      const note = form.querySelector('.assist-note');
      setButtonLoading(btn, true, 'Generating…');
    try {
      const r = await api('/api/cv/assist', {
        method: 'POST',
        body: { kind, cv: c, job_description: (OPT_STATE && OPT_STATE.jobDescription) || null },
      });
      addUsage(r.usage);
      const text = (r && r.text) || '';
      if (kind === 'summary') {

          c.summary = text;
          const ta = form.querySelector('.summary-input');
          if (ta) ta.value = text;
          markDirty();
          syncPreview();
        } else {
          // bullets: NEVER apply instantly — preview first, then Replace/Append/Cancel
          const lines = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
          const applyBullets = (mode) => {
            const idx = parseInt(document.getElementById('assist-target').value, 10);
            if (!c.experience[idx]) return;
            c.experience[idx].bullets = mode === 'replace'
              ? lines.slice()
              : (c.experience[idx].bullets || []).concat(lines);
            markDirty();
            rerenderBuildForm();
          };
          modal({
            title: 'AI Assist — Bullets',
            body: `<p class="small muted">The AI generated the following bullets. Choose which experience item to update and whether to replace or append them.</p>
              <div class="field"><label>Apply to</label><select id="assist-target">
                ${c.experience.map((e, i) => `<option value="${i}">${esc(e.role || 'Role')} @ ${esc(e.company || 'Company')}${i === 0 ? ' (selected)' : ''}</option>`).join('')}
              </select></div>
              <div class="ai-preview"><pre>${esc(lines.length ? lines.join('\n') : '(no bullet lines returned)')}</pre></div>`,
            confirmText: 'Replace',
            cancelText: 'Cancel',
            extraBtn: { label: 'Append', onClick: () => applyBullets('append') },
            onConfirm: () => applyBullets('replace'),
          });
        }
        toast(kind === 'summary' ? 'Summary generated' : 'Bullets generated — choose how to apply', 'success');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setButtonLoading(btn, false);
      }
    });
  });

  // repeaters generic binding
  root.querySelectorAll('[data-add]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const kind = btn.dataset.add;
      const blank = blankItem(kind);
      c[kind].push(blank);
      markDirty();
      rerenderBuildForm();
    });
  });

  //Repeater interactions — delegated listeners on the persistent #app root so
  // they survive re-renders. Attached ONCE via the root.dataset.buildBound guard
  // (mirrors bindEditor's editorBound flag): per-render bindBuild() calls must
  // never stack duplicate input/click listeners (previously one +bullet click
  // could push N bullets / one Remove could delete N items). Handlers read
  // CURRENT_CV fresh on every event so closures never go stale either.
  if (root.dataset.buildBound) {
    // Delegated handlers were attached to #app on the first call only.
  } else {
    root.dataset.buildBound = '1';
    root.addEventListener('click', onBuildRemoveClick);
    root.addEventListener('input', onBuildInput);
    root.addEventListener('click', onBuildRepeaterClick);
  }

  const addSkill = root.querySelector('#add-skill-group');
  if (addSkill) addSkill.addEventListener('click', () => { c.skills.push({ category: '', skills: [] }); markDirty(); rerenderBuildForm(); });
  const addLang = root.querySelector('#add-lang');
  if (addLang) addLang.addEventListener('click', () => { c.languages.push({ name: '', level: '' }); markDirty(); rerenderBuildForm(); });
  const addCustom = root.querySelector('#add-custom-section');
  if (addCustom) addCustom.addEventListener('click', () => { (c.custom_sections = c.custom_sections || []).push({ title: '', bullets: [] }); markDirty(); rerenderBuildForm(); });

  bindTemplatePicker(root);

  const clear = root.querySelector('#btn-clear');
  if (clear) clear.addEventListener('click', () => confirmReset(() => renderBuild()));

  const libBtn = root.querySelector('#btn-pick-library');
  if (libBtn) libBtn.addEventListener('click', () => libraryPicker((rec) => { CURRENT_CV = rec; markDirty(); renderBuild(); }));

  const changeTpl = root.querySelector('#btn-change-tpl');
  if (changeTpl) changeTpl.addEventListener('click', () => { BUILD_STATE.stage = 0; renderBuild(); });

  const pdfBtn = root.querySelector('#btn-tpl-pdf');
  if (pdfBtn) pdfBtn.addEventListener('click', () => downloadTemplate('pdf', pdfBtn));
  const docxBtn = root.querySelector('#btn-tpl-docx');
  if (docxBtn) docxBtn.addEventListener('click', () => downloadTemplate('docx', docxBtn));

  // Per-section ✨ Optimize → delegated button (icon swapped to sparkles in markup).
  // Bound once (the #app element persists across re-renders) to avoid stacking listeners.
  if (!root.dataset.optBound) {
    root.dataset.optBound = '1';
    root.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-opt]');
      if (!btn) return;
      const kind = btn.dataset.opt;
      const idx = btn.dataset.i !== undefined ? parseInt(btn.dataset.i, 10) : null;
      runOptimize(kind, idx, btn);
    });
  }

  scheduleEditorPreview();
  autogrowAll(root);
}

/* Delegated build-form handlers — attached once (see bindBuild's buildBound
   guard) to the persistent #app root. They read CURRENT_CV fresh on every
   event so a captured reference can never go stale after a library load or a
   full re-render. */
function onBuildRemoveClick(e) {
  const rm = e.target.closest('[data-remove]');
  if (!rm) return;
  const kindEl = rm.closest('[data-kind]');
  const idx = parseInt(rm.dataset.remove, 10);
  if (kindEl) removeItemWithUndo(kindEl.dataset.kind, idx);
}

function onBuildInput(e) {
  const c = CURRENT_CV;
  const t = e.target;
  let changed = false;
  if (t.matches('[data-k]')) {
    const i = parseInt(t.dataset.i, 10);
    const k = t.dataset.k;
    const listContainer = t.closest('[data-kind]');
    const list = listContainer ? listContainer.dataset.kind : null;
    if (k.includes('dates.')) {
      const [a, part] = k.split('.');
      if (list && c[list] && c[list][i] && c[list][i][a]) c[list][i][a][part] = t.value;
    } else if (list && c[list] && c[list][i]) {
      c[list][i][k] = t.value;
    }
    changed = true;
    syncPreview();
  }
  if (t.matches('[data-bullet]')) {
    const kind = t.closest('[data-kind]') ? t.closest('[data-kind]').dataset.kind : 'experience';
    const i = parseInt(t.dataset.bullet, 10);
    const bi = parseInt(t.dataset.bi, 10);
    c[kind][i].bullets[bi] = t.value;
    changed = true;
    syncPreview();
  }
  if (t.matches('[data-skill-cat]')) {
    const gi = parseInt(t.dataset.skillCat, 10);
    c.skills[gi].category = t.value;
    changed = true;
  }
  if (t.matches('[data-lang-name]')) {
    c.languages[parseInt(t.dataset.langName, 10)].name = t.value;
    changed = true;
  }
  if (t.matches('[data-lang-level]')) {
    c.languages[parseInt(t.dataset.langLevel, 10)].level = t.value;
    changed = true;
  }
  if (t.matches('[data-cs-title]')) {
    c.custom_sections[parseInt(t.dataset.csTitle, 10)].title = t.value;
    changed = true;
  }
  if (t.matches('[data-cs-bullet]')) {
    const i = parseInt(t.dataset.csBullet, 10);
    const bi = parseInt(t.dataset.bi, 10);
    c.custom_sections[i].bullets[bi] = t.value;
    changed = true;
  }
  if (changed) markDirty();
}

function onBuildRepeaterClick(e) {
  const c = CURRENT_CV;
  const dup = e.target.closest('[data-dup]');
  if (dup) {
    const [kind, i] = dup.dataset.dup.split(':');
    duplicateItem(kind, parseInt(i, 10));
    return;
  }
  const badd = e.target.closest('[data-badd]');
  if (badd) {
    const kind = badd.closest('[data-kind]').dataset.kind;
    const i = parseInt(badd.dataset.badd, 10);
    c[kind][i].bullets.push('');
    markDirty();
    rerenderBuildForm();
    return;
  }
  const bdel = e.target.closest('[data-bdel]');
  if (bdel) {
    const kind = bdel.closest('[data-kind]').dataset.kind;
    const i = parseInt(bdel.dataset.bdel, 10);
    const bi = parseInt(bdel.dataset.bi, 10);
    removeBulletWithUndo(kind, i, bi);
    return;
  }
  const srm = e.target.closest('[data-skillgroup-rm]');
  if (srm) {
    removeItemWithUndo('skills', parseInt(srm.dataset.skillgroupRm, 10));
    return;
  }
  const lrm = e.target.closest('[data-lang-rm]');
  if (lrm) {
    removeItemWithUndo('languages', parseInt(lrm.dataset.langRm, 10));
    return;
  }
  const csBadd = e.target.closest('[data-cs-badd]');
  if (csBadd) {
    const i = parseInt(csBadd.dataset.csBadd, 10);
    (c.custom_sections[i].bullets = c.custom_sections[i].bullets || []).push('');
    markDirty();
    rerenderBuildForm();
    return;
  }
  const csBdel = e.target.closest('[data-cs-bdel]');
  if (csBdel) {
    const i = parseInt(csBdel.dataset.csBdel, 10);
    const bi = parseInt(csBdel.dataset.bi, 10);
    removeBulletWithUndo('custom_sections', i, bi);
    return;
  }
  const csRm = e.target.closest('[data-cs-rm]');
  if (csRm) {
    removeItemWithUndo('custom_sections', parseInt(csRm.dataset.csRm, 10));
  }
}

async function runOptimize(kind, idx, btn) {
  if (!gateLLM()) return;
  const c = CURRENT_CV;
  const job = (OPT_STATE && OPT_STATE.jobDescription) || null;
  const labels = { sum: 'Summary', bullets: 'Bullets', proj: 'Project', skills: 'Skills' };
  const label = labels[kind] || 'Text';
  let content = '';

  if (kind === 'sum') {
    content = (c.summary || '').trim();
    if (!content) { toast('Write something first', 'error'); return; }
  } else if (kind === 'bullets') {
    const item = c.experience[idx];
    if (!item) return;
    content = (item.bullets || []).map((b) => (b || '').trim()).filter(Boolean).join('\n');
    if (!content) { toast('Add some bullets first', 'error'); return; }
  } else if (kind === 'proj') {
    const item = c.projects[idx];
    if (!item) return;
    const parts = [];
    if ((item.description || '').trim()) parts.push(item.description.trim());
    const bs = (item.bullets || []).map((b) => (b || '').trim()).filter(Boolean);
    if (bs.length) parts.push(bs.join('\n'));
    content = parts.join('\n');
    if (!content) { toast('Add a description or bullets first', 'error'); return; }
  } else if (kind === 'skills') {
    content = (c.skills || []).reduce((acc, g) => acc.concat(g.skills || []), [])
      .map((s) => (s || '').trim()).filter(Boolean).join(', ');
    if (!content) { toast('Add some skills first', 'error'); return; }
  } else {
    return;
  }

  setButtonLoading(btn, true, 'Optimizing…');
  try {
    const kindParam = { sum: 'optimize_summary', bullets: 'optimize_bullets' }[kind] || 'optimize';
    const r = await api('/api/cv/assist', {
      method: 'POST',
      body: { kind: kindParam, cv: c, job_description: job, content },
    });
    const text = ((r && r.text) || '').trim();
    if (!text) { toast('No optimized text returned', 'info'); return; }

    if (kind === 'sum') {
      c.summary = text;
      const ta = document.querySelector('#build-form .summary-input');
      if (ta) ta.value = text;
      markDirty();
      syncPreview();
      toast(`${label} optimized`, 'success');
    } else if (kind === 'bullets' && c.experience[idx]) {
      // NEVER overwrite instantly — preview first, then Replace/Append/Cancel.
      const lines = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      const item = c.experience[idx];
      const applyBullets = (mode) => {
        item.bullets = mode === 'replace' ? lines.slice() : (item.bullets || []).concat(lines);
        markDirty();
        rerenderBuildForm();
        toast(`${label} optimized`, 'success');
      };
      modal({
        title: 'Optimize Bullets — preview',
        body: `<p class="small muted">The AI rewrote the bullets of <strong>${esc(item.role || item.company || 'Experience ' + (idx + 1))}</strong>. Replace or append the generated bullets?</p>
          <div class="ai-preview"><pre>${esc(lines.length ? lines.join('\n') : '(nothing returned)')}</pre></div>`,
        confirmText: 'Replace',
        cancelText: 'Cancel',
        extraBtn: { label: 'Append', onClick: () => applyBullets('append') },
        onConfirm: () => applyBullets('replace'),
      });
    } else if (kind === 'proj' && c.projects[idx]) {
      const parsed = parseProjectOutput(text);
      const item = c.projects[idx];
      const applyProject = (mode) => {
        if (mode === 'replace') {
          item.description = parsed.description || '';
          item.bullets = parsed.bullets.slice();
        } else {
          item.description = parsed.description || item.description;
          item.bullets = (item.bullets || []).concat(parsed.bullets);
        }
        markDirty();
        rerenderBuildForm();
        toast(`${label} optimized`, 'success');
      };
      modal({
        title: 'Optimize Project — preview',
        body: `<p class="small muted">The AI rewrote <strong>${esc(item.name || 'Project ' + (idx + 1))}</strong>.</p>
          <div class="ai-preview"><pre>${esc(parsed.description || '(no description)')}${parsed.bullets.length ? '\n\n' + parsed.bullets.map((b) => '- ' + b).join('\n') : ''}</pre></div>`,
        confirmText: 'Replace',
        cancelText: 'Cancel',
        extraBtn: { label: 'Append', onClick: () => applyProject('append') },
        onConfirm: () => applyProject('replace'),
      });
    } else if (kind === 'skills') {
      const groups = parseSkillGroups(text);
      const existing = (c.skills || []).slice();
      const applySkills = (mode) => {
        c.skills = mode === 'replace' ? groups : mergeSkillGroups(existing, groups);
        markDirty();
        rerenderBuildForm();
        toast(`${label} optimized`, 'success');
      };
      modal({
        title: 'Optimize Skills — preview',
        body: `<p class="small muted">The AI grouped your skills. <strong>Replace</strong> replaces all groups; <strong>Append</strong> merges into the existing groups.</p>
          <div class="ai-preview"><pre>${esc(groups.length ? groups.map((g) => `${g.category}: ${g.skills.join(', ')}`).join('\n') : '(nothing returned)')}</pre></div>`,
        confirmText: 'Replace',
        cancelText: 'Cancel',
        extraBtn: { label: 'Append', onClick: () => applySkills('append') },
        onConfirm: () => applySkills('replace'),
      });
    }
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

async function downloadTemplate(fmt, btn) {
  const c = CURRENT_CV;
  const tplId = BUILD_STATE.templateId;
  if (!tplId || (fmt !== 'pdf' && fmt !== 'docx')) return;
  const hasDetails = !!((c.personal && c.personal.name && c.personal.name.trim()) || (c.experience && c.experience.length));
  if (!hasDetails) { toast('Fill in some details first', 'error'); return; }
  setButtonLoading(btn, true, 'Exporting…');
  try {
    const res = await fetch(`/api/templates/${encodeURIComponent(tplId)}/export/${fmt}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cv: c }),
    });
    if (!res.ok) {
      let msg = 'Export failed';
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    let fname = `${tplId}_cv.${fmt}`;
    const cd = res.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="?([^";]+)"?/i);
    if (m) fname = m[1];
    downloadBlob(blob, fname);
    const tpl = (TEMPLATE_CATALOG || []).find((t) => t.id === tplId);
    if (tpl && tpl.source === 'gallery' && tpl.converted === false) {
      toast('This design isn\'t converted yet — rendering with the closest available layout', 'info', 6000);
    } else {
      toast(fmt.toUpperCase() + ' exported', 'success');
    }
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

function blankItem(kind) {
  const base = {
    experience: () => ({ company: '', role: '', location: '', dates: { start: '', end: '' }, bullets: [] }),
    education: () => ({ institution: '', degree: '', field: '', dates: { start: '', end: '' }, gpa: '' }),
    projects: () => ({ name: '', link: '', description: '', bullets: [] }),
    certifications: () => ({ name: '', issuer: '', year: '' }),
  }[kind];
  return base ? base() : {};
}

function rerenderBuildForm() {
  // re-render only the repeater sections, preserving template picker + personal + summary state
  const scroll = app.scrollTop || window.scrollY;
  renderBuild();
  window.scrollTo(0, scroll);
  syncPreview();
}

function syncPreview() {
  refreshPreview();
}

/* ============================================================
   Preview refresh dispatcher (§4.4): the Editor uses the
   server-rendered iframe preview; everything else falls back to
   the lightweight local renderer (or no-ops when absent).
   ============================================================ */
function refreshPreview() {
  if (document.getElementById('editor-preview')) { scheduleEditorPreview(); return; }
  const pr = document.getElementById('preview-root');
  if (pr) renderPreview(pr);
}

/* ============================================================
   VIEW: Optimize (wizard)
   ============================================================ */
function defaultOptState() {
  return {
    step: 0,               // 0 upload, 1 analyze, 2 review, 3 apply, 4 results
    parsed: null,          // {cv, text, confidence, confidence_flags, classification, image_mode}
    text: '',
    jobDescription: '',
    report: null,          // AnalysisReport
    suggestions: [],       // list of Suggestion + ui state
    session_id: '',        // uuid set when a CV is uploaded (used for session structure)
    confidenceFlags: [],   // confidence_flags from POST /api/cv/parse
    gaps: null,            // gap analysis result from POST /api/cv/gaps
    imageMode: false,      // true for scanned PDFs analyzed from page images
    appliedIds: [],        // ids of suggestions already applied (idempotence bookkeeping)
    scoreHistory: [],      // ats_score per analyze / re-analyze call
    baseScore: null,       // ats_score of the FIRST analysis (delta baseline)
    suggestionsStale: false, // true when CV edits happened after suggestions were generated
    sessionWarning: '',    // non-empty when the upload session expired
    suggFilter: { priority: 'all', section: 'all' }, // review-step filters (§5.3)
  };
}
if (!OPT_STATE || !OPT_STATE.step) { OPT_STATE = OPT_STATE && OPT_STATE.step !== undefined ? OPT_STATE : defaultOptState(); }

/* Reset per-session optimizer artifacts when a NEW CV enters the pipeline.
   Keeps session_id / imageMode / confidenceFlags (callers immediately re-set
   those); clears anything that belongs to a previously loaded CV so navigation
   can never surface another CV's report/suggestions/chat — and "Apply accepted"
   can never patch the wrong data. */
function resetOptimizeState(step = 1) {
  OPT_STATE.report = null;
  OPT_STATE.suggestions = [];
  OPT_STATE.gaps = null;
  CHAT_LOG.length = 0;
  OPT_STATE.jobDescription = '';
  OPT_STATE.baseScore = null;
  OPT_STATE.scoreHistory = [];
  OPT_STATE.suggestionsStale = false;
  OPT_STATE.step = step;
  markDirty();
}

function markSuggestionsStale() {
  if (OPT_STATE.suggestions && OPT_STATE.suggestions.length && !OPT_STATE.suggestionsStale) {
    OPT_STATE.suggestionsStale = true;
    markDirty();
  }
}

const STEP_LABELS = ['Upload / Select CV', 'Analyze', 'Review', 'Apply', 'Results & Export'];

function renderOptimize() {
  const s = OPT_STATE;
  const step = s.step;
  const stepsHTML = `<div class="wizard-steps">
    ${STEP_LABELS.map((l, i) => {
      const stateCls = i === step ? 'active' : i < step ? 'done' : '';
      const disabled = !canGo(i);
      const badge = (i === 2 && s.suggestions && s.suggestions.length)
        ? ` <span class="pill-badge">${s.suggestions.filter((x) => x._state !== 'applied').length}</span>`
        : '';
      return `<button type="button" class="step-pill ${stateCls}" data-step="${i}" ${disabled ? 'disabled' : ''}><span class="n">${i + 1}</span>${l}${badge}</button>`;
    }).join('')}
  </div>`;

  let body = '';
  if (step === 0) body = renderOptUpload(s);
  else if (step === 1) body = renderOptAnalyze(s);
  else if (step === 2) body = renderOptSuggestions(s);
  else if (step === 3) body = renderOptApply(s);
  else body = renderOptResults(s);

  app.innerHTML = `
    <div class="view-header">
      <h1>Optimize</h1>
      <p>Upload a CV, optionally add a job description, and get ATS-tailored suggestions.</p>
      <button class="btn btn-secondary btn-sm" id="btn-assistant-opt">${icon('chat')} Assistant</button>
    </div>
    ${stepsHTML}
    ${body}`;

  bindOptimize(step);
}

function canGo(i) {
  const s = OPT_STATE;
  if (i === 0) return true;
  if (i === 1) return !!s.parsed;
  if (i === 2) return !!s.report;
  if (i === 3) return !!s.report;
  if (i === 4) return !!s.report;
  return false;
}

function renderOptUpload(s) {
  return `
    <div class="card">
      <h2>Upload / Select CV</h2>
      <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Upload PDF or DOCX CV">
        <div class="dz-ic">${icon('upload')}</div>
        <div><strong>Click to upload</strong> or drag &amp; drop a PDF / DOCX</div>
        <div class="small muted mt">Your file is parsed by the app and never stored to disk.</div>
        <input type="file" id="cv-file-input" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden>
      </div>
      <div class="btn-row mt">
        <button class="btn btn-secondary" id="btn-load-library">${icon('library')} Pick from My CVs</button>
        <button class="btn btn-ghost" id="btn-use-current" ${CURRENT_CV && (CURRENT_CV.summary || CURRENT_CV.experience.length) ? '' : 'disabled'}>Use current editor CV</button>
      </div>
    </div>
    <div id="parse-result"></div>`;
}

function parseAndShow(fileInput) {
  const drop = document.getElementById('dropzone');
  const result = document.getElementById('parse-result');
  const file = fileInput.files && fileInput.files[0];
  if (!file) return;
  // PDF/DOCX upload: validate by extension before hitting the API.
  if (!/\.pdf$/i.test(file.name) && !/\.docx$/i.test(file.name)) {
    result.innerHTML = `<div class="card"><p class="danger" style="color:var(--danger)">${icon('alert')} Only PDF or DOCX files are accepted. Please upload your CV as a PDF or DOCX.</p></div>`;
    return;
  }
  drop.style.pointerEvents = 'none';
  result.innerHTML = skeletonHTML('parse');
  const fd = new FormData();
  fd.append('file', file);
    api('/api/cv/parse', { method: 'POST', body: fd })
      .then((parsed) => {
        addUsage(parsed.usage);
        // Fresh CV enters the pipeline: wipe any artifacts from a previously
        // loaded CV (report/suggestions/gaps/chat). Step stays 0 (upload result

      // screen); the fresh parse re-sets session_id/imageMode/confidenceFlags
      // right below, which resetOptimizeState deliberately does not touch.
      resetOptimizeState(0);
      CURRENT_CV = parsed.cv || CURRENT_CV;
      OPT_STATE.parsed = parsed;
      markDirty();
      markSuggestionsStale();
      OPT_STATE.text = parsed.text || '';
      OPT_STATE.session_id = parsed.session_id || '';
      OPT_STATE.imageMode = !!parsed.image_mode;
      OPT_STATE.confidenceFlags = parsed.confidence_flags || [];
      const classChip = parsed.classification === 'heuristic'
        ? `<span class="chip miss">${icon('alert')} Heuristic parse — review fields</span>`
        : '<span class="chip match">✓ AI-classified structure</span>';
      const modeChip = OPT_STATE.imageMode
        ? '<span class="chip miss">🖼 Scanned PDF — visual analysis</span>'
        : '';
      const imageNote = OPT_STATE.imageMode
        ? '<p class="small muted mt">This PDF has little or no machine-readable text, so the parsed content may be incomplete. Review and edit the fields before optimizing.</p>'
        : '';
      const flags = (parsed.confidence_flags || []).filter(Boolean);
      const flagsBlock = flags.length ? `
        <div class="card">
          <h2 style="margin-bottom:6px">${icon('alert')} ${flags.length} field(s) flagged — review before optimizing</h2>
          <details class="mt"><summary class="small muted">Show flagged fields</summary>
            <ul class="flags-list mt">
              ${flags.map((f) => `<li><strong>${esc(f.field_path || 'field')}</strong> — ${esc(f.reason || f.level || '')}</li>`).join('')}
            </ul>
          </details>
          <div class="btn-row mt">
            <button class="btn btn-secondary btn-sm" id="btn-review-flags">Review in Editor</button>
          </div>
        </div>` : '';
      result.innerHTML = `
        <div class="card">
          <h2>Parsed CV</h2>
          <div class="flex">
            <span class="chip match">✓ Confidence: ${esc(roundPct(parsed.confidence))}</span>
            ${classChip}
            ${modeChip}
          </div>
          ${imageNote}
          <p class="small mt">${esc((parsed.cv && parsed.cv.personal && parsed.cv.personal.name) || 'Unnamed CV')}</p>
          <div class="btn-row mt">
            <button class="btn btn-primary" id="btn-to-analyze">Next: Analyze vs Job →</button>
            <button class="btn btn-secondary" id="btn-open-editor">Open in Editor</button>
          </div>
          <details class="mt"><summary class="small muted">Extracted text</summary>
            <textarea class="code" readonly style="margin-top:8px" rows="6">${esc((parsed.text || '').slice(0, 4000))}</textarea></details>
        </div>
        ${flagsBlock}`;
      window.scrollTo(0, 0);
      result.querySelector('#btn-to-analyze').addEventListener('click', () => { OPT_STATE.step = 1; renderOptimize(); });
      result.querySelector('#btn-open-editor').addEventListener('click', () => { location.hash = '#/editor'; });
      const reviewBtn = result.querySelector('#btn-review-flags');
      if (reviewBtn) reviewBtn.addEventListener('click', () => { location.hash = '#/editor'; });
    })
    .catch((err) => {
      result.innerHTML = `<div class="card"><p class="danger" style="color:var(--danger)">${icon('alert')} ${esc(err.message)}</p></div>`;
    })
    .finally(() => { drop.style.pointerEvents = 'auto'; });
}

function roundPct(v) {
  if (typeof v === 'number') { const p = Math.round(v * 100); return `${p}%`; }
  return `${v}%`;
}

function renderOptAnalyze(s) {
  return `
    <div class="card">
      <h2>Analyze</h2>
      ${OPT_STATE.imageMode ? '<p class="small muted">This scanned CV will be analyzed from its page images.</p>' : ''}
      <div class="field">
        <label for="jd-textarea">Job Description (optional)</label>
        <textarea id="jd-textarea" class="code" rows="8" placeholder="Paste the full job posting here (optional — leave empty for a generic ATS check)…">${esc(s.jobDescription)}</textarea>
      </div>
      <p class="small muted">No JD? We'll assess generic ATS-friendliness (structure, section ordering, naming, formatting).</p>
      <div class="btn-row">
        <button class="btn btn-primary" id="btn-analyze">${icon('microscope')} Analyze</button>
        <button class="btn btn-ghost" id="btn-back-upload">← Back</button>
      </div>
    </div>
    ${s.sessionWarning ? sessionWarningBannerHTML() : ''}
    <div id="analysis-result">${s.report ? renderReport(s.report) : ''}</div>
    <div id="gap-result">${s.gaps ? renderGaps(s.gaps) : ''}</div>`;
}

function renderReport(report) {
  if (!report || typeof report !== 'object') {
    return '<div class="empty-state">No analysis report available.</div>';
  }
  const ats = Math.max(0, Math.min(100, report.ats_score || 0));
  const r = 17.5; // radius
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - ats / 100);
  const scClass = scoreClass(ats);

  const hasJd = !!(OPT_STATE && OPT_STATE.jobDescription && String(OPT_STATE.jobDescription).trim());
  const matched = report.matched_keywords || [];
  const missing = report.missing_keywords || [];
  const sections = report.sections || [];
  const comments = report.comments || [];

  const scoreCaption = hasJd
    ? '<p class="small muted mt">Match against the job description</p>'
    : '<p class="small muted mt">Generic ATS-friendliness assessment (no job description)</p>';

  // §4.7: structural gap strings are surfaced in the unified gap panel below
  // (renderGaps → "Highlights"); the dashboard keeps score/keywords/sections.
  const keywordsPanel = hasJd ? `
    <div class="dash-panel">
      <h3>Keywords</h3>
      <div class="small" style="font-weight:700;margin-bottom:6px">Matched (${matched.length})</div>
      <div class="chip-wrap mb">${matched.length ? matched.map((k) => `<span class="chip match">${esc(k.keyword)}${k.count ? ` <span class="cnt">×${k.count}</span>` : ''}</span>`).join('') : '<span class="small muted">None matched</span>'}</div>
      <div class="small" style="font-weight:700;margin-bottom:6px">Missing (${missing.length})</div>
      <div class="chip-wrap">${missing.length ? missing.map((k) => `<span class="chip miss">${esc(k.keyword)}</span>`).join('') : '<span class="small muted">Nothing missing — great!</span>'}</div>
    </div>` : `
    <div class="dash-panel">
      <h3>Keywords</h3>
      <p class="small muted">No job description provided — keyword matching skipped.</p>
    </div>`;

  return `
    <div class="dash-grid mt">
      <div class="dash-panel center">
        <h3>ATS Score</h3>
        <div class="score-ring ${scClass}" style="--p:${ats};margin:0 auto" data-score="${ats}">
          <svg viewBox="0 0 40 40">
            <circle class="track" cx="20" cy="20" r="${r}" fill="none" stroke-width="3.5"/>
            <circle class="bar" cx="20" cy="20" r="${r}" fill="none" stroke-width="3.5"
              stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${circ.toFixed(2)}"/>
          </svg>
          <div class="score-label"><span><span class="score-num ${scClass}">${ats}</span><br><span class="score-txt">/ 100</span></span></div>
        </div>
        ${scoreCaption}
      </div>

      ${keywordsPanel}

      <div class="dash-panel">
        <h3>Sections</h3>
        ${sections.length ? sections.map((sec) => `
          <div class="section-bar">
            <div class="row"><span>${esc(cap(sec.section))}</span><span>${sec.score}</span></div>
            <div class="track"><div class="fill ${scoreClass(sec.score)}" style="width:0%" data-w="${Math.max(0, Math.min(100, sec.score))}"></div></div>
            ${sec.comment ? `<div class="comment">${esc(sec.comment)}</div>` : ''}
          </div>`).join('') : '<p class="small muted">No section analysis.</p>'}
      </div>

      <div class="dash-panel">
        <h3>Comments</h3>
        ${comments.length ? comments.map((c) => `<div class="small mb">• ${esc(c)}</div>`).join('') : '<p class="small muted">No comments.</p>'}
      </div>
    </div>
    <div class="btn-row mt">
      <button class="btn btn-primary" id="btn-to-suggestions">Next: Review Suggestions →</button>
      <button class="btn btn-secondary" id="btn-back-analyze">← Back</button>
    </div>`;
}

/* ============================================================
   Gap analysis panel (POST /api/cv/gaps)
   ============================================================ */
function gapRowHTML(gap, i, k) {
  const sev = String(gap.severity || 'medium').toLowerCase();
  const path = gap.field_path || '';
  const sevBadge = sev === 'high'
    ? '<span class="badge high">high</span>'
    : sev === 'low' ? '<span class="badge low">low</span>'
      : '<span class="badge medium">medium</span>';
  // Only offer "Apply as suggestion" when the gap targets a real field. Gaps
  // without a field_path (e.g. deterministic heading checks) stay informational.
  const applyBtn = (path && gap.suggested_value && String(gap.suggested_value).trim())
    ? `<button type="button" class="btn btn-sm btn-secondary gap-apply" data-gap-apply="${i}:${k}">Apply as suggestion</button>`
    : '';
  return `
    <div class="gap-row" data-gap-path="${esc(path)}">
      ${path ? `<span class="chip mono gap-path">${esc(path)}</span>` : '<span class="chip mono gap-path">—</span>'}
      <div class="gap-main">
        <div class="gap-issue">${esc(gap.issue || '')}</div>
        ${gap.rationale ? `<div class="small muted gap-rationale">${esc(gap.rationale)}</div>` : ''}
      </div>
      ${sevBadge}
      ${applyBtn}
    </div>`;
}

function renderGaps(r) {
  if (!r || typeof r !== 'object') {
    return '<div class="card"><h2>Gap Analysis</h2><p class="small muted">No gaps detected.</p></div>';
  }
  const det = Array.isArray(r.deterministic) ? r.deterministic : [];
  const sem = Array.isArray(r.semantic) ? r.semantic : [];
  // Report-level summary strings come from the analysis report (§4.7).
  const reportStrings = Array.isArray(OPT_STATE.report && OPT_STATE.report.gaps) ? OPT_STATE.report.gaps : [];
  const modeBadge = r.mode === 'jd'
    ? '<span class="badge type">JD</span>'
    : r.mode === 'generic' ? '<span class="badge type">Generic</span>' : '';

  // §4.7 — "How we read the job": surface the parsed JD profile.
  const jd = r.jd_profile || null;
  const hasJd = !!(OPT_STATE && OPT_STATE.jobDescription && String(OPT_STATE.jobDescription).trim());
  let jdCard = '';
  if (jd && (jd.role_title || (jd.required_skills || []).length || (jd.nice_to_have_skills || []).length)) {
    const must = (jd.required_skills || []).concat(jd.must_have_keywords || []);
    const nice = jd.nice_to_have_skills || [];
    const reqs = jd.requirements || [];
    jdCard = `<div class="card" id="jd-profile-card">
      <h2>How we read the job</h2>
      ${jd.role_title ? `<p class="jd-role">${esc(jd.role_title)}</p>` : ''}
      ${must.length ? `<div class="small" style="font-weight:700;margin:10px 0 6px">Must-haves</div><div class="chip-wrap mb">${must.map((s) => `<span class="chip match">${esc(s)}</span>`).join('')}</div>` : ''}
      ${nice.length ? `<div class="small" style="font-weight:700;margin:10px 0 6px">Nice-to-have</div><div class="chip-wrap mb">${nice.map((s) => `<span class="chip">${esc(s)}</span>`).join('')}</div>` : ''}
      ${reqs.length ? `<div class="small" style="font-weight:700;margin:10px 0 6px">Requirements</div><ul class="gap-list">${reqs.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : ''}
    </div>`;
  } else if (!hasJd) {
    jdCard = `<div class="card" id="jd-profile-card"><h2>How we read the job</h2><p class="small muted">Add a job description to see our reading of it.</p></div>`;
  }

  // §4.7 — one unified gap panel: deterministic + semantic + report summaries.
  const sectionHead = (title, gaps, i) => `
    <div class="gap-section">
      <h3>${title} <span class="small muted">(${gaps.length})</span></h3>
      <div>
        ${gaps.length
          ? gaps.map((g, k) => gapRowHTML(g, i, k)).join('')
          : '<p class="small muted">None.</p>'}
      </div>
    </div>`;
  const highlights = reportStrings.length
    ? `<div class="gap-section"><h3>Highlights <span class="small muted">(${reportStrings.length})</span></h3><ul class="gap-list">${reportStrings.map((g) => `<li>${esc(g)}</li>`).join('')}</ul></div>`
    : '';

  return `
    ${jdCard}
    <div class="card" id="gap-result-card">
      <h2>Gap Analysis ${modeBadge}</h2>
      ${sectionHead('Hard checks', det, 0)}
      ${sectionHead('AI analysis', sem, 1)}
      ${highlights}
    </div>`;
}

function gapPathParts(path) {
  const parts = String(path || '').split('.').filter(Boolean);
  const section = parts[0] || '';
  const idxPart = parts.find((p) => /^\d+$/.test(p));
  return { section, index: idxPart !== undefined ? parseInt(idxPart, 10) : null };
}

function gapToSuggestion(gap, i, k) {
  const { section, index } = gapPathParts(gap.field_path);
  const secArr = Array.isArray(CURRENT_CV[section]) ? CURRENT_CV[section] : null;
  // Treat as an 'add' when the target is a brand-new element (index absent or
  // out of range in the current CV), otherwise a content 'rewrite'.
  const isNewElement = secArr
    ? index === null || index >= secArr.length
    : !secArr && section !== '' && !(section in CURRENT_CV);
  return {
    id: `gap-${i}-${k}`,
    section,
    field: section,
    index,
    type: isNewElement ? 'add' : 'rewrite',
    title: `Gap: ${gap.field_path || section || 'CV'}`,
    original: '',
    suggested: gap.suggested_value ? String(gap.suggested_value) : '',
    reason: gap.rationale || gap.issue || '',
    rationale: gap.rationale || '',
    field_path: gap.field_path || '',
    priority: (gap.severity || 'medium').toLowerCase(),
  };
}

function bindGapsPanel(r) {
  const rootEl = document.getElementById('gap-result');
  if (!rootEl) return;
  rootEl.innerHTML = renderGaps(r);
  // The same #gap-result element can be re-bound (re-analyze without a full
  // re-render); remove any previous delegated handler first to avoid stacking.
  if (rootEl.__gapHandler) rootEl.removeEventListener('click', rootEl.__gapHandler);
  const handler = (e) => {
    const btn = e.target.closest('[data-gap-apply]');
    if (!btn) return;
    const [i, k] = btn.dataset.gapApply.split(':').map(Number);
    const cur = OPT_STATE.gaps || r;
    const lists = [cur.deterministic || [], cur.semantic || []];
    const gap = lists[i] && lists[i][k];
    // Defense in depth: never turn a gap into a suggestion unless it targets a
    // concrete field_path (deterministic heading gaps have none).
    if (!gap || !gap.field_path || !gap.suggested_value) return;
    const sg = gapToSuggestion(gap, i, k);
    if (OPT_STATE.suggestions.some((x) => x.id === sg.id)) {
      toast('This gap is already in your suggestions', 'info');
      return;
    }
    sg._state = 'pending';
    OPT_STATE.suggestions.push(sg);
    toast('Added to suggestions — review then apply', 'success');
  };
  rootEl.__gapHandler = handler;
  rootEl.addEventListener('click', handler);
}

function bindOptimize(step) {
  const s = OPT_STATE;

  // wizard step pills (clickable on every step)
  app.querySelectorAll('.step-pill').forEach((p) => {
    p.addEventListener('click', () => {
      if (p.disabled) return;
      s.step = parseInt(p.dataset.step, 10);
      renderOptimize();
    });
  });

  // dismiss the session-expiry notice (rendered per step)
  const warnDismiss = app.querySelector('[data-dismiss-session-warn]');
  if (warnDismiss) warnDismiss.addEventListener('click', () => { s.sessionWarning = ''; markDirty(); renderOptimize(); });

  if (step === 0) {
    const drop = document.getElementById('dropzone');
    const input = document.getElementById('cv-file-input');
    drop.addEventListener('click', () => input.click());
    // Keyboard activation: matching the click behavior of a button so the
    // dropzone is usable without a mouse.
    drop.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        input.click();
      }
    });
    input.addEventListener('change', () => parseAndShow(input));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('drag');
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        const dt = new DataTransfer();
        dt.items.add(e.dataTransfer.files[0]);
        input.files = dt.files;
        parseAndShow(input);
      }
    });
    document.getElementById('btn-load-library').addEventListener('click', () => {
      libraryPicker((rec) => {
        resetOptimizeState();
        CURRENT_CV = rec;
        OPT_STATE.parsed = { cv: rec, text: '', confidence: 1 };
        OPT_STATE.text = '';
        OPT_STATE.session_id = '';
        OPT_STATE.imageMode = false;
        OPT_STATE.confidenceFlags = [];
        markDirty();
        toast('Loaded from library', 'success');
        renderOptimize();
      });
    });
    const useCur = document.getElementById('btn-use-current');
    if (useCur) useCur.addEventListener('click', () => {
      resetOptimizeState();
      OPT_STATE.parsed = { cv: JSON.parse(JSON.stringify(CURRENT_CV)), text: '', confidence: 1 };
      OPT_STATE.text = '';
      OPT_STATE.session_id = '';
      OPT_STATE.imageMode = false;
      OPT_STATE.confidenceFlags = [];
      toast('Using current editor CV', 'success');
      renderOptimize();
    });
    return;
  }

  // Assistant button in the optimize header (all steps)
  const asstBtn = app.querySelector('#btn-assistant-opt');
  if (asstBtn) asstBtn.addEventListener('click', () => toggleAssistant());

  if (step === 1) {
    const btn = document.getElementById('btn-analyze');
    btn.addEventListener('click', async () => {
      if (!gateLLM()) return;
      const jd = (document.getElementById('jd-textarea').value || '').trim();
      s.jobDescription = jd;
      setButtonLoading(btn, true, 'Analyzing…');
      // clear any stale gap panel while re-analyzing
      s.gaps = null;
      const gapEl = document.getElementById('gap-result');
      if (gapEl) gapEl.innerHTML = skeletonHTML('gaps');
      const resEl = document.getElementById('analysis-result');
      if (resEl) resEl.innerHTML = skeletonHTML('dashboard');
    try {
      const cv = CURRENT_CV;
      const report = await api('/api/cv/analyze', {
        method: 'POST',
        body: { cv, text: s.text, job_description: s.jobDescription, session_id: OPT_STATE.session_id },
      });
      addUsage(report.usage);
      s.report = report;
      // First analysis becomes the delta baseline (§4.6).

        if (s.baseScore === null || !s.scoreHistory.length) s.baseScore = report.ats_score || 0;
        s.scoreHistory = (s.scoreHistory || []).concat([report.ats_score || 0]);
        if (report.session_warning) s.sessionWarning = report.session_warning;
        markDirty();
        resEl.innerHTML = renderReport(report);
        animateReport(resEl);
        bindReportNav(resEl);
        toast('Analysis complete', 'success');
        // Gap analysis runs in parallel with the same body; failures are non-blocking.
    try {
      const gaps = await api('/api/cv/gaps', {
        method: 'POST',
        body: { cv, text: s.text, job_description: s.jobDescription, session_id: OPT_STATE.session_id },
      });
      addUsage(gaps.usage);
      s.gaps = gaps;
      if (gaps.session_warning) s.sessionWarning = gaps.session_warning;

          markDirty();
          bindGapsPanel(gaps);
        } catch (err) {
          toast('Gap analysis unavailable: ' + err.message, 'info', 3000);
        }
      } catch (err) {
        // clear any stale report so we don't show outdated results after a failure
        s.report = null;
        s.gaps = null;
        const resEl = document.getElementById('analysis-result');
        if (resEl) resEl.innerHTML = '';
        toast(err.message, 'error');
      } finally {
        setButtonLoading(btn, false);
      }
    });
    const back = document.getElementById('btn-back-upload');
    if (back) back.addEventListener('click', () => { s.step = 0; renderOptimize(); });
    if (s.report) {
      const resEl = document.getElementById('analysis-result');
      bindReportNav(resEl);
      animateReport(resEl);
    }
    if (s.gaps) bindGapsPanel(s.gaps);
    return;
  }

  if (step === 2) {
    const btn = document.getElementById('btn-tailor-suggest');
    btn.addEventListener('click', async () => {
      if (!gateLLM()) return;
      setButtonLoading(btn, true, 'Generating suggestions…');
      const listEl = document.getElementById('sugg-list');
      if (listEl) listEl.innerHTML = skeletonHTML('suggestions');
    try {
      const r = await api('/api/cv/tailor/suggest', {
        method: 'POST',
        body: { cv: CURRENT_CV, text: s.text, job_description: s.jobDescription, session_id: OPT_STATE.session_id },
      });
      addUsage(r.usage);
      s.suggestions = (r.suggestions || []).map((sg) => ({ ...sg, _state: 'pending' }));
      s.suggestionsStale = false;

        if (r.session_warning) s.sessionWarning = r.session_warning;
        markDirty();
        document.getElementById('sugg-list').innerHTML = renderSuggestionList(s.suggestions);
        bindSuggestions();
        toast(`${s.suggestions.length} suggestions generated`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        setButtonLoading(btn, false);
      }
    });
    const back = document.getElementById('btn-back-sugg');
    if (back) back.addEventListener('click', () => { s.step = 1; renderOptimize(); });
    const applyBtn = document.getElementById('btn-apply-accepted');
    if (applyBtn) applyBtn.addEventListener('click', applyAccepted);
    const impBtn = document.getElementById('btn-import-analysis');
    if (impBtn) impBtn.addEventListener('click', importAnalysisSuggestions);
    const acceptAll = document.getElementById('btn-accept-all');
    if (acceptAll) acceptAll.addEventListener('click', () => {
      s.suggestions.forEach((sg) => { if (sg._state !== 'applied') sg._state = 'accepted'; });
      markDirty();
      rerenderSuggList();
    });
    const rejectAll = document.getElementById('btn-reject-all');
    if (rejectAll) rejectAll.addEventListener('click', () => {
      modal({
        title: 'Reject all suggestions?',
        body: '<p>All pending suggestions will be marked as rejected. You can still regenerate later.</p>',
        confirmText: 'Reject all',
        danger: true,
        onConfirm: () => {
          s.suggestions.forEach((sg) => { if (sg._state !== 'applied') sg._state = 'rejected'; });
          markDirty();
          rerenderSuggList();
        },
      });
    });
    app.querySelectorAll('[data-sugg-prio]').forEach((c) => c.addEventListener('click', () => { s.suggFilter = Object.assign({}, s.suggFilter || {}, { priority: c.dataset.suggPrio }); rerenderSuggList(); }));
    app.querySelectorAll('[data-sugg-section]').forEach((c) => c.addEventListener('click', () => { s.suggFilter = Object.assign({}, s.suggFilter || {}, { section: c.dataset.suggSection }); rerenderSuggList(); }));
    if (s.suggestions.length) bindSuggestions();
    return;
  }

  if (step === 3) {
    const reanal = document.getElementById('btn-apply-reanalyze');
    if (reanal) reanal.addEventListener('click', async () => { if (await runReanalyze()) { s.step = 4; renderOptimize(); } });
    const toResults = document.getElementById('btn-to-results');
    if (toResults) toResults.addEventListener('click', () => { s.step = 4; renderOptimize(); });
    const back = document.getElementById('btn-back-apply');
    if (back) back.addEventListener('click', () => { s.step = 2; renderOptimize(); });
    return;
  }

  if (step === 4) {
    const pdfBtn = document.getElementById('btn-results-pdf');
    if (pdfBtn) pdfBtn.addEventListener('click', () => exportCv('pdf', 'resume.pdf', 'application/pdf', pdfBtn));
    const docxBtn = document.getElementById('btn-results-docx');
    if (docxBtn) docxBtn.addEventListener('click', () => exportCv('docx', 'resume.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docxBtn));
    const reanal = document.getElementById('btn-results-reanalyze');
    if (reanal) reanal.addEventListener('click', async () => { await runReanalyze(); renderOptimize(); });
  }
}

/* Shared re-analysis: POST /api/cv/analyze, updates report/scoreHistory/delta
   bookkeeping. Returns the report (or null on failure). */
async function runReanalyze() {
  if (!gateLLM()) return null;
  try {
    const report = await api('/api/cv/analyze', {
      method: 'POST',
      body: {
        cv: CURRENT_CV,
        text: OPT_STATE.text || '',
        job_description: (OPT_STATE && OPT_STATE.jobDescription) || '',
        session_id: (OPT_STATE && OPT_STATE.session_id) || '',
      },
    });
    OPT_STATE.report = report;
    if (OPT_STATE.baseScore === null || !OPT_STATE.scoreHistory.length) OPT_STATE.baseScore = report.ats_score || 0;
    OPT_STATE.scoreHistory = (OPT_STATE.scoreHistory || []).concat([report.ats_score || 0]);
    if (report.session_warning) OPT_STATE.sessionWarning = report.session_warning;
    markDirty();
    toast(`Re-analysis complete — score ${report.ats_score || 0}`, 'success');
    return report;
  } catch (err) {
    toast(err.message, 'error');
    return null;
  }
}

/* §4.7: import the analysis report's own suggestions into the review list
   (no second LLM call). Dedupe by id / title against existing suggestions. */
function importAnalysisSuggestions() {
  if (!gateLLM()) return;
  const reportSugs = (OPT_STATE.report && OPT_STATE.report.suggestions) || [];
  if (!reportSugs.length) { toast('No analysis suggestions to import', 'info'); return; }
  const existingIds = new Set(OPT_STATE.suggestions.map((x) => x.id));
  const existingTitles = new Set(OPT_STATE.suggestions.map((x) => String(x.title || '').toLowerCase()));
  let added = 0;
  reportSugs.forEach((sg) => {
    const id = sg.id || `analysis-${Math.random().toString(36).slice(2, 8)}`;
    if (existingIds.has(id)) return;
    if (existingTitles.has(String(sg.title || '').toLowerCase())) return;
    existingIds.add(id);
    OPT_STATE.suggestions.push({ ...sg, _state: 'pending' });
    added += 1;
  });
  if (!added) { toast('Analysis suggestions already in your list', 'info'); return; }
  markDirty();
  const list = document.getElementById('sugg-list');
  if (list) { list.innerHTML = renderSuggestionList(OPT_STATE.suggestions); bindSuggestions(); }
  toast(`${added} analysis suggestion(s) imported`, 'success');
}

function bindReportNav(rootEl) {
  const toSugg = rootEl.querySelector('#btn-to-suggestions');
  if (toSugg) toSugg.addEventListener('click', () => { OPT_STATE.step = 2; renderOptimize(); });
  const back = rootEl.querySelector('#btn-back-analyze');
  if (back) back.addEventListener('click', () => { OPT_STATE.step = 1; renderOptimize(); });
}

function animateReport(rootEl) {
  requestAnimationFrame(() => {
    const ring = rootEl.querySelector('.score-ring');
    if (ring) {
      const circ = 2 * Math.PI * 17.5;
      const offset = circ * (1 - (ring.dataset.score || 0) / 100);
      const bar = ring.querySelector('.bar');
      if (bar) bar.setAttribute('stroke-dashoffset', offset.toFixed(2));
    }
    rootEl.querySelectorAll('.section-bar .fill').forEach((f) => {
      f.style.width = (f.dataset.w || 0) + '%';
    });
  });
}

function renderOptSuggestions(s) {
  const analysisSugs = (s.report && s.report.suggestions) || [];
  const staleChip = (s.suggestionsStale && s.suggestions.length)
    ? `<span class="chip miss">Suggestions are from before your edits — regenerate</span>`
    : '';
  const pendingCount = s.suggestions.filter((x) => x._state !== 'applied').length;
  const appliedCount = s.suggestions.filter((x) => x._state === 'applied').length;
  const filter = s.suggFilter || { priority: 'all', section: 'all' };
  const prioChips = ['all', 'high', 'medium', 'low'].map((p) => `<button type="button" class="chip-target ${filter.priority === p ? 'active' : ''}" data-sugg-prio="${p}">${p === 'all' ? 'All priorities' : cap(p)}</button>`).join('');
  const secChips = ['all', 'summary', 'experience', 'education', 'skills', 'projects', 'other'].map((p) => `<button type="button" class="chip-target ${filter.section === p ? 'active' : ''}" data-sugg-section="${p}">${p === 'all' ? 'All sections' : cap(p)}</button>`).join('');
  return `
    <div class="card">
      <h2>Review suggestions</h2>
      ${OPT_STATE.sessionWarning ? sessionWarningBannerHTML() : ''}
      <div class="btn-row spread">
        <button class="btn btn-primary" id="btn-tailor-suggest">${icon('sparkles')} Generate Suggestions</button>
        ${analysisSugs.length ? `<button class="btn btn-secondary" id="btn-import-analysis">Import ${analysisSugs.length} analysis suggestion${analysisSugs.length === 1 ? '' : 's'}</button>` : ''}
        <a href="#/editor" class="btn btn-secondary" style="text-decoration:none">Open in Editor</a>
      </div>
      ${staleChip ? `<div class="mt">${staleChip}</div>` : ''}
      <p class="small muted mt">${pendingCount} pending · ${appliedCount} applied. Accept, reject or edit each suggestion, then apply the accepted ones.</p>
      <div class="sugg-toolbar">
        <div class="sugg-filters">
          <span class="small muted">Priority</span>${prioChips}
          <span class="small muted">Section</span>${secChips}
        </div>
        <div class="btn-row">
          <button class="btn btn-sm btn-secondary" id="btn-accept-all">${icon('check')} Accept all</button>
          <button class="btn btn-sm btn-secondary" id="btn-reject-all">${icon('x')} Reject all</button>
          <button class="btn btn-primary" id="btn-apply-accepted">${icon('check')} Apply accepted</button>
        </div>
      </div>
    </div>
    <div id="sugg-list">${s.suggestions.length ? renderSuggestionList(s.suggestions) : emptyStateHTML('sparkles', 'No suggestions yet', 'Click “Generate Suggestions” or import the analysis suggestions.')}</div>
    <div class="btn-row mt">
      <button class="btn btn-ghost" id="btn-back-sugg">← Back to Analysis</button>
    </div>`;
}

/* §4.1/§4.6 — step 3 "Apply": summary of what was applied, re-analyze, results. */
function renderOptApply(s) {
  const applied = s.suggestions.filter((x) => x._state === 'applied');
  const staleChip = (s.suggestions.length && s.suggestionsStale)
    ? '<span class="chip miss">Suggestions are from before your edits</span>'
    : '';
  return `
    <div class="card">
      <h2>Apply</h2>
      ${staleChip ? `<div class="mb">${staleChip}</div>` : ''}
      <p class="small muted">The accepted suggestions below were applied to your CV.</p>
      ${applied.length
        ? `<ul class="applied-list">${applied.map((a) => `<li><strong>${esc(a.title || a.section)}</strong> <span class="small muted">${esc(a.section)}</span></li>`).join('')}</ul>`
        : '<p class="small muted">Nothing applied yet — go back to Review and accept some suggestions.</p>'}
      <div class="btn-row mt">
        <button class="btn btn-secondary" id="btn-apply-reanalyze">${icon('refresh')} Re-analyze (new score)</button>
        <button class="btn btn-primary" id="btn-to-results">Continue to Results &amp; Export →</button>
        <button class="btn btn-ghost" id="btn-back-apply">← Back to Review</button>
      </div>
    </div>`;
}

/* §4.1/§4.6 — step 4 "Results & Export": score delta, applied summary, exports. */
function renderOptResults(s) {
  const base = s.baseScore;
  const cur = s.report ? (s.report.ats_score || 0) : (s.scoreHistory && s.scoreHistory.length ? s.scoreHistory[s.scoreHistory.length - 1] : 0);
  const delta = (base !== null && base !== undefined) ? cur - base : null;
  const applied = s.suggestions.filter((x) => x._state === 'applied');
  const staleChip = (s.suggestions.length && s.suggestionsStale)
    ? '<span class="chip miss">Suggestions are from before your edits</span>'
    : '';
  return `
    <div class="card">
      <h2>Results &amp; Export</h2>
      ${OPT_STATE.sessionWarning ? sessionWarningBannerHTML() : ''}
      ${staleChip ? `<div class="mb">${staleChip}</div>` : ''}
      <div class="results-grid">
        <div class="dash-panel center">
          <h3>ATS score</h3>
          <div class="results-score">
            <span class="score-big ${scoreClass(cur)}">${cur}</span>
            ${delta !== null && delta !== undefined ? `<span class="results-delta ${delta >= 0 ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${delta}</span>` : ''}
          </div>
          <p class="small muted">${delta !== null && delta !== undefined ? `<strong>${base}</strong> → <strong>${cur}</strong>` : 'First score for this CV.'}</p>
          ${sparklineHTML(s.scoreHistory)}
        </div>
        <div class="dash-panel">
          <h3>Changes applied</h3>
          ${applied.length
            ? `<ul class="applied-list">${applied.map((a) => `<li><strong>${esc(a.title || a.section)}</strong> <span class="small muted">${esc(a.section)}</span></li>`).join('')}</ul>`
            : '<p class="small muted">Nothing applied yet.</p>'}
        </div>
      </div>
      <div class="btn-row mt">
        <button class="btn btn-primary" id="btn-results-pdf">${icon('download')} Download PDF</button>
        <button class="btn btn-secondary" id="btn-results-docx">${icon('download')} Download Word</button>
        <button class="btn btn-secondary" id="btn-results-reanalyze">${icon('refresh')} Re-analyze</button>
        <a href="#/editor" class="btn btn-secondary" style="text-decoration:none">${icon('edit')} Keep editing</a>
      </div>
    </div>`;
}

function sparklineHTML(history) {
  const arr = (history || []).filter((n) => typeof n === 'number');
  if (arr.length < 2) return '';
  const max = Math.max(...arr, 1);
  const steps = arr.map((n) => Math.round((n / max) * 24));
  const pts = steps.map((h, i) => `<span class="spark-bar" style="height:${Math.max(2, h)}px" title="${arr[i]}"></span>`).join('');
  return `<div class="sparkline" role="img" aria-label="Score history: ${arr.join(', ')}">${pts}</div>`;
}

function renderSuggestionList(sugs) {
  if (!sugs.length) return '<div class="empty-state">No suggestions.</div>';
  const filter = OPT_STATE.suggFilter || { priority: 'all', section: 'all' };
  const filtered = sugs.filter((sg) => {
    if (filter.priority !== 'all' && String(sg.priority || 'medium').toLowerCase() !== filter.priority) return false;
    if (filter.section !== 'all') {
      const sec = String(sg.section || '').toLowerCase();
      const inOther = !['summary', 'experience', 'education', 'skills', 'projects'].includes(sec);
      if (filter.section === 'other') { if (!inOther) return false; }
      else if (sec !== filter.section) return false;
    }
    return true;
  });
  if (!filtered.length) {
    return '<div class="empty-state">No suggestions match your filters — try widening them.</div>';
  }
  return filtered.map((sg) => {
    const state = sg._state || 'pending';
    const applied = state === 'applied';
    const structural = ['reorder', 'layout', 'rename'].includes(String(sg.type || '').toLowerCase());
    const path = sg.field_path ? `<span class="chip mono gap-path">${esc(sg.field_path)}</span>` : '';
    const reason = (sg.rationale || sg.reason || '').trim();
    return `
      <div class="sugg-card ${applied ? 'applied' : state === 'accepted' ? 'accepted' : state === 'rejected' ? 'rejected' : ''}" data-sugg="${esc(sg.id)}">
        <div class="sugg-head">
          <span class="sugg-title">${esc(sg.title || sg.field_path || sg.section || 'Suggestion')}</span>
          ${path}
          ${applied ? '<span class="sugg-applied-badge">✓ Applied</span>' : ''}
          <span class="badge type">${esc(sg.type)}</span>
          <span class="badge ${esc(sg.priority)}">${esc(sg.priority)}</span>
          ${sg.impact ? `<span class="small muted">${esc(sg.impact)}</span>` : ''}
          <span class="small muted" style="margin-left:auto">${esc(cap(sg.section))}</span>
        </div>
        <div class="sugg-compare">
          <div class="sugg-box sp"><span class="lbl">Original</span>${esc(sg.original || '(none)')}</div>
          <div class="sugg-box sp-new"><span class="lbl">Suggested</span>${esc(sg.suggested)}</div>
        </div>
        ${reason ? `<div class="sugg-reason">${esc(reason)}</div>` : ''}
        ${structural ? '<p class="small muted mt">Structural suggestion — applies to the regenerated export layout.</p>' : ''}
        ${state === 'editing' ? renderSuggestionEditor(sg) : ''}
        <div class="btn-row sugg-actions">
          <button class="btn btn-sm btn-secondary" data-act="accept" ${state === 'accepted' || applied ? 'disabled' : ''}>✓ Accept</button>
          <button class="btn btn-sm btn-secondary" data-act="reject" ${state === 'rejected' || applied ? 'disabled' : ''}>✖ Reject</button>
          <button class="btn btn-sm btn-ghost" data-act="edit" ${applied ? 'disabled' : ''}>${icon('edit')} Edit</button>
          ${applied ? '<span class="small" style="color:var(--ok)">Applied to CV</span>' : state === 'accepted' ? '<span class="small" style="color:var(--ok)">Accepted</span>' : state === 'rejected' ? '<span class="small" style="color:var(--danger)">Rejected</span>' : ''}
        </div>
      </div>`;
  }).join('');
}

function renderSuggestionEditor(sg) {
  return `
    <div class="sugg-edit grid">
      <div class="field"><label>Suggested text</label><textarea data-edit-id="${sg.id}" rows="3">${esc(sg.suggested)}</textarea></div>
      <div class="btn-row">
        <button class="btn btn-sm btn-primary" data-act="save-edit" data-edit-id="${sg.id}">Save</button>
        <button class="btn btn-sm btn-ghost" data-act="cancel-edit">Cancel</button>
      </div>
    </div>`;
}

function bindSuggestions() {
  const list = document.getElementById('sugg-list');
  if (!list) return;
  // Guard against stacking duplicate delegated listeners: the #sugg-list element
  // persists across innerHTML re-renders, so only bind once per element. The
  // handler reads OPT_STATE.suggestions fresh on every click because the array is
  // REPLACED (not mutated) when new suggestions are generated — a captured
  // reference would go stale and silently kill the Accept/Reject/Edit buttons.
  if (list.dataset.bound) return;
  list.dataset.bound = '1';
  list.addEventListener('click', (e) => {
    const card = e.target.closest('.sugg-card');
    if (!card) return;
    const sg = OPT_STATE.suggestions.find((x) => x.id === card.dataset.sugg);
    if (!sg) return;
    // Applied cards are locked — no accept/reject/edit (idempotence).
    if (sg._state === 'applied') return;
    const act = e.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'accept') { sg._state = 'accepted'; }
    if (act.dataset.act === 'reject') { sg._state = 'rejected'; }
    if (act.dataset.act === 'edit') { sg._state = sg._state === 'editing' ? 'pending' : 'editing'; }
    if (act.dataset.act === 'save-edit') {
      const ta = list.querySelector(`[data-edit-id="${sg.id}"]`);
      if (ta) sg.suggested = ta.value;
      sg._state = 'accepted';
    }
    if (act.dataset.act === 'cancel-edit') { sg._state = 'pending'; }
    markDirty();
    list.innerHTML = renderSuggestionList(OPT_STATE.suggestions);
    bindSuggestions();
  });
}

async function applyAccepted() {
  // Idempotent: applied cards are locked in the UI, but defensively skip
  // anything already applied before sending so re-applying never duplicates.
  const accepted = OPT_STATE.suggestions.filter((s) => s._state === 'accepted' && s._state !== 'applied');
  if (!accepted.length) { toast('No accepted suggestions to apply', 'info'); return; }
  const applyBtn = document.getElementById('btn-apply-accepted');
  setButtonLoading(applyBtn, true, 'Applying…');
  try {
    const r = await api('/api/cv/tailor/apply', { method: 'POST', body: { cv: CURRENT_CV, suggestions: accepted } });
    CURRENT_CV = r.cv;
    // record applied ids + mark accepted as applied
    const appliedIds = accepted.map((s) => s.id);
    OPT_STATE.appliedIds = ((OPT_STATE.appliedIds || []).concat(appliedIds)).filter((v, i, a) => a.indexOf(v) === i);
    OPT_STATE.suggestions = OPT_STATE.suggestions.map((s) => (s._state === 'accepted' ? { ...s, _state: 'applied' } : s));
    markDirty();
    const list = document.getElementById('sugg-list');
    if (list) { list.innerHTML = renderSuggestionList(OPT_STATE.suggestions); bindSuggestions(); }
    const applied = Number(r.applied) || 0;
    if (applied > 0) {
      toast(`${applied} suggestion(s) applied to CV`, 'success');
    } else {
      toast('0 suggestions changed the CV — the accepted suggestions may target sections not present. Try editing in the editor.', 'info');
    }
    // After applying, take the user straight to the Editor (with a review
    // banner) so they can pick a template + accent color — no forced
    // "download or keep editing" choice.
    OPT_STATE.step = 4; // preserve results step for later navigation back
    OPTIMIZE_REVIEW_PENDING = true;
    location.hash = '#/editor';
  } catch (err) {
    toast('Failed to apply suggestions: ' + err.message, 'error');
  } finally {
    setButtonLoading(applyBtn, false);
  }
}

function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// Re-render the review step after filter/accept-all changes (§5.3).
function rerenderSuggList() {
  renderOptimize();
}

/* ============================================================
   Chat 2.0 — Assistant panel (§4.2). A floating drawer available
   from the Optimize header and the Editor toolbar. Shares CHAT_LOG
   (persisted via the draft) and full conversation history with the
   backend; proposed_edits render as applicable diff cards.
   ============================================================ */
const CHAT_TARGETS = [
  { label: 'Summary', target: { section: 'summary' } },
  { label: 'Experience 1', target: { section: 'experience', index: 0 } },
  { label: 'Skills', target: { section: 'skills' } },
  { label: 'Custom', target: { section: 'custom_sections' } },
];

function toggleAssistant(force) {
  const panel = document.getElementById('assistant-panel');
  const open = typeof force === 'boolean' ? force : !panel;
  if (open) openAssistantPanel();
  else closeAssistantPanel();
}

function openAssistantPanel() {
  if (document.getElementById('assistant-panel')) return;
  const panel = document.createElement('div');
  panel.id = 'assistant-panel';
  panel.className = 'assistant-panel';
  panel.setAttribute('role', 'complementary');
  panel.setAttribute('aria-label', 'AI Assistant');
  panel.innerHTML = assistantPanelHTML();
  document.body.appendChild(panel);
  bindAssistantPanel(panel);
}

function closeAssistantPanel() {
  const p = document.getElementById('assistant-panel');
  if (p) p.remove();
}

function assistantPanelHTML() {
  const chips = CHAT_TARGETS.map((t) => `<button type="button" class="chip-target ${ASSISTANT_ACTIVE_TARGET && ASSISTANT_ACTIVE_TARGET.label === t.label ? 'active' : ''}" data-target="${esc(t.label)}">${icon('target')} ${esc(t.label)}</button>`).join('');
  return `
    <div class="assistant-head">
      <strong>${icon('chat')} Assistant</strong>
      <span class="small muted">rewrites &amp; edits your CV</span>
      <button type="button" class="btn btn-ghost btn-sm" data-assistant-close aria-label="Close assistant">${icon('x')}</button>
    </div>
    <div class="assistant-log" id="chat-log"></div>
    <div class="assistant-chips">${chips}</div>
    <div class="assistant-input-row">
      <textarea id="chat-input" rows="2" placeholder="Ask the assistant… (Enter to send)"></textarea>
      <button class="btn btn-primary" id="chat-send">${icon('send')} Send</button>
    </div>
    <div class="small muted assistant-hint">Enter to send · Shift+Enter for a new line</div>`;
}

function bindAssistantPanel(panel) {
  const log = panel.querySelector('#chat-log');
  const input = panel.querySelector('#chat-input');
  const send = panel.querySelector('#chat-send');

  const renderLog = () => {
    log.innerHTML = CHAT_LOG.length
      ? CHAT_LOG.map(renderChatMessage).join('')
      : '<div class="small muted">Ask for a rewrite, a skills suggestion, or anything about making your CV stronger.</div>';
    log.scrollTop = log.scrollHeight;
  };
  renderLog();

  panel.querySelector('[data-assistant-close]').addEventListener('click', closeAssistantPanel);

  panel.querySelectorAll('[data-target]').forEach((c) => {
    c.addEventListener('click', () => {
      const label = c.dataset.target;
      const found = CHAT_TARGETS.find((t) => t.label === label);
      ASSISTANT_ACTIVE_TARGET = ASSISTANT_ACTIVE_TARGET && ASSISTANT_ACTIVE_TARGET.label === label ? null : found;
      panel.querySelectorAll('[data-target]').forEach((x) => x.classList.toggle('active', x === c && !!ASSISTANT_ACTIVE_TARGET));
    });
  });

  // Delegated: copy + proposed-edit Apply/Dismiss.
  log.addEventListener('click', (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const text = copy.dataset.copy;
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => toast('Copied to clipboard', 'success'));
      else toast('Copy not supported in this browser', 'info');
      return;
    }
    const apply = e.target.closest('[data-edit-apply]');
    if (apply) { applyChatEdit(apply.dataset.editApply, apply); return; }
    const dismiss = e.target.closest('[data-edit-dismiss]');
    if (dismiss) dismissChatEdit(dismiss.dataset.editDismiss);
  });

  const sendFn = async () => {
    if (!gateLLM()) return;
    const text = input.value.trim();
    if (!text) return;
    CHAT_LOG.push({ role: 'user', text });
    input.value = '';
    renderLog();
    send.disabled = true;
    send.innerHTML = '<span class="spinner"></span> Thinking…';
    log.insertAdjacentHTML('beforeend', `<div class="chat-msg"><div class="who">AI</div><div class="body">${skeletonHTML('chat')}</div></div>`);
    log.scrollTop = log.scrollHeight;
    try {
      const r = await api('/api/cv/tailor/chat', {
        method: 'POST',
        body: {
          messages: CHAT_LOG.map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text })),
          target: (ASSISTANT_ACTIVE_TARGET && ASSISTANT_ACTIVE_TARGET.target) || {},
          cv: CURRENT_CV,
          job_description: OPT_STATE.jobDescription,
          session_id: OPT_STATE.session_id,
        },
      });
      addUsage(r.usage);
      CHAT_LOG.push({
        role: 'assistant',
        text: (r && r.reply) || '',
        edits: (r && Array.isArray(r.proposed_edits) ? r.proposed_edits : []),
        dismissed: [],
      });
      if (r && r.session_warning) {
        OPT_STATE.sessionWarning = r.session_warning;
        const card = document.querySelector('.assistant-panel');
        if (card) addSessionWarning(card, r.session_warning);
      }
      markDirty();
      renderLog();
    } catch (err) {
      CHAT_LOG.push({ role: 'assistant', text: friendlyError(err.message), edits: [], dismissed: [] });
      markDirty();
      renderLog();
    } finally {
      send.disabled = false;
      send.innerHTML = 'Send';
    }
  };
  send.addEventListener('click', sendFn);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendFn(); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendFn(); }
  });
}

function renderChatMessage(m) {
  const isUser = m.role === 'user';
  const body = formatChatText(m.text || '');
  const edits = (m.edits || []).filter((e) => !(m.dismissed || []).includes(e.id));
  const editCards = edits.map(renderProposedEditCard).join('');
  return `<div class="chat-msg ${isUser ? 'user' : ''}">
    <div class="who">${isUser ? 'You' : 'AI'}</div>
    <div class="body">${body}</div>
    ${isUser ? '' : `<button type="button" class="btn btn-sm btn-ghost chat-copy" data-copy="${esc(m.text || '')}" aria-label="Copy reply">${icon('copy')} Copy</button>`}
    ${editCards ? `<div class="proposed-edits">${editCards}</div>` : ''}
  </div>`;
}

// Escape first, then minimal markdown-ish inline formatting.
function formatChatText(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

function renderProposedEditCard(e) {
  const section = e.section || e.field || '';
  const type = e.type || 'rewrite';
  return `<div class="proposed-edit">
    <div class="pe-head">
      <strong>${esc(cap(section))} → ${esc(type)}</strong>
      ${e.title ? `<span class="small muted">${esc(e.title)}</span>` : ''}
    </div>
    <div class="pe-compare">
      <div class="pe-box pe-orig"><span class="lbl">Before</span>${esc(e.original || '(none)')}</div>
      <div class="pe-box pe-new"><span class="lbl">After</span>${esc(e.suggested)}</div>
    </div>
    ${e.rationale ? `<div class="small muted pe-r">${esc(e.rationale)}</div>` : ''}
    <div class="btn-row">
      <button class="btn btn-sm btn-primary" data-edit-apply="${esc(e.id)}">Apply</button>
      <button class="btn btn-sm btn-ghost" data-edit-dismiss="${esc(e.id)}">Dismiss</button>
    </div>
  </div>`;
}

async function applyChatEdit(editId, btn) {
  let msg = null;
  let edit = null;
  for (const m of CHAT_LOG) {
    if (m.edits) { const hit = m.edits.find((e) => e.id === editId); if (hit) { msg = m; edit = hit; break; } }
  }
  if (!edit) { toast('That edit is no longer available', 'info'); return; }
  setButtonLoading(btn, true, 'Applying…');
  try {
    const r = await api('/api/cv/tailor/apply', { method: 'POST', body: { cv: CURRENT_CV, suggestions: [edit] } });
    CURRENT_CV = r.cv;
    if (!msg.dismissed) msg.dismissed = [];
    msg.dismissed.push(editId);
    markDirty();
    markSuggestionsStale();
    toast(`${Number(r.applied) || 0} change(s) applied to your CV`, 'success');
    refreshChatAfterApply();
  } catch (err) {
    toast('Failed to apply edit: ' + err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

function dismissChatEdit(editId) {
  for (const m of CHAT_LOG) {
    if (m.edits && m.edits.some((e) => e.id === editId)) {
      if (!m.dismissed) m.dismissed = [];
      m.dismissed.push(editId);
      markDirty();
      renderAssistantLog();
      return;
    }
  }
}

function renderAssistantLog() {
  const log = document.getElementById('chat-log');
  if (!log) return;
  log.innerHTML = CHAT_LOG.length ? CHAT_LOG.map(renderChatMessage).join('') : '<div class="small muted">Ask for a rewrite or a skills suggestion.</div>';
  log.scrollTop = log.scrollHeight;
}

function refreshChatAfterApply() {
  renderAssistantLog();
  refreshPreview();
  if (document.getElementById('build-form')) rerenderBuildForm();
  else if (document.getElementById('editor-preview')) rerenderEditor();
}

/* ============================================================
   VIEW: Editor / Preview
   ============================================================ */
// One-shot green banner shown in the Editor right after the user applies
// accepted optimize suggestions — summarizes what changed and nudges them to
// pick a template + accent color before exporting.
function optimizeReviewBanner() {
  if (!OPTIMIZE_REVIEW_PENDING) return '';
  const applied = OPT_STATE.suggestions.filter((x) => x._state === 'applied');
  if (!applied.length) return '';
  const extraLine = applied.length > 5 ? ` + ${applied.length - 5} more` : '';
  const titles = applied.slice(0, 5).map((a) => esc(a.title || a.section)).filter(Boolean);
  return `
    <div class="banner success optimize-review-banner">
      <div class="banner-body">
        <h3>${icon('check')} Suggestions applied</h3>
        <p><strong>${esc(applied.length)}</strong> AI suggestion${applied.length === 1 ? '' : 's'} applied to your CV.</p>
        <p class="small muted">${titles.join(' · ')}${extraLine}</p>
        <p>Review your CV below, then choose a <strong>template</strong> and <strong>accent color</strong> in the &ldquo;Summary &amp; Template&rdquo; section before exporting.</p>
        <button type="button" class="btn btn-secondary btn-sm" id="btn-review-template">${icon('edit')} Choose template &amp; color</button>
      </div>
    </div>`;
}

function renderEditor(route) {
  const s = OPT_STATE;
  const fromOptimize = !!(s.report || s.suggestions.length);
  const reviewBanner = optimizeReviewBanner();
  const imageBanner = OPT_STATE.imageMode
    ? `<div class="banner neutral"><div class="banner-body"><p>This CV was parsed from a scanned PDF with little or no machine-readable text, so the content below may be incomplete. Review and edit the fields before exporting.</p></div></div>`
    : '';
  const exportBtns = `
    <button class="btn btn-secondary" id="btn-export-pdf">${icon('download')} Download PDF</button>
    <button class="btn btn-secondary" id="btn-export-docx">${icon('download')} Download Word</button>`;

  app.innerHTML = `
    <div class="view-header">
      <h1>Editor &amp; Preview</h1>
      ${(!reviewBanner && fromOptimize) ? `<p class="small"><span class="status-dot on"></span>Loaded with optimization data — tweak, then export.</p>` : ''}
    </div>
    <div class="editor-grid">
      <div class="editor-form-pane">
        <div id="editor-live-region" class="sr-only" aria-live="polite">${esc(EDITOR_ANNOUNCEMENT)}</div>
        ${reviewBanner}
        ${imageBanner}
        ${editorJumpNav()}
        ${editorFormHTML()}
      </div>
      <div class="editor-preview-pane">
        <div class="preview-wrap">
          <div class="preview-frame">
            <div class="preview-toolbar">
              <span class="title">Live Preview</span>
              <span id="dirty-indicator" class="dirty-indicator" hidden></span>
              <button class="btn btn-sm btn-ghost" id="btn-assistant">${icon('chat')} Assistant</button>
              <button class="btn btn-sm btn-ghost" id="btn-validate">${icon('search')} ATS Check</button>
              <button class="btn btn-sm btn-ghost" id="btn-reanalyze">${icon('refresh')} Re-analyze</button>
            </div>
            <div class="preview-zoombar">
              <button type="button" class="btn btn-sm btn-ghost" data-zoom="out" aria-label="Zoom out">−</button>
              <span class="zoom-label" id="preview-zoom-label">100%</span>
              <button type="button" class="btn btn-sm btn-ghost" data-zoom="in" aria-label="Zoom in">+</button>
              <label class="fit-toggle"><input type="checkbox" id="preview-fit" checked> Fit width</label>
              <span class="small muted" style="margin-left:auto">${esc(templateNameForId(CURRENT_CV.template) || CURRENT_CV.template)}</span>
            </div>
            <div class="preview-viewport">
              <div class="preview-page" id="preview-page">
                <iframe id="editor-preview" title="CV preview"></iframe>
                <div id="preview-fallback" hidden></div>
              </div>
            </div>
          </div>
          <div class="btn-row mt" style="justify-content:space-between;flex-wrap:wrap">
            <button class="btn btn-secondary" id="btn-save-library">${icon('save')} Save to library</button>
            <div class="flex">${exportBtns}</div>
          </div>
        </div>
      </div>
    </div>`;

  bindEditor();
  scheduleEditorPreview(true);
  bindPreviewControls();
  updateDirtyIndicator();
  // The review banner is a one-shot: it should only appear once per apply.
  OPTIMIZE_REVIEW_PENDING = false;
}

// Sticky "On this CV" jump-nav — shown only when the CV spans >3 sections (§5.2).
const EDITOR_SECTION_LABELS = {
  summary: 'Summary', experience: 'Experience', education: 'Education', skills: 'Skills',
  projects: 'Projects', certifications: 'Certs', languages: 'Languages', custom_sections: 'Custom',
};
function editorJumpNav() {
  const c = CURRENT_CV;
  const nonEmpty = Object.keys(EDITOR_SECTION_LABELS).filter((k) => {
    if (k === 'summary') return !!String(c.summary || '').trim();
    if (k === 'custom_sections') return !!(c.custom_sections && c.custom_sections.length);
    return Array.isArray(c[k]) && c[k].length;
  });
  if (nonEmpty.length <= 3) return '';
  return `<div class="section-jump">${nonEmpty.map((k) => `<button type="button" class="chip-target" data-jump="${k}">${EDITOR_SECTION_LABELS[k]}</button>`).join('')}</div>`;
}

function jumpToSection(kind) {
  const el = document.getElementById('ed-section-' + kind);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ============================================================
   Editor server-rendered preview (§4.4): POST /api/export/preview
   into an A4-framed iframe, debounced, with zoom + fit-width.
   ============================================================ */
let PREVIEW_TIMER = null;
let PREVIEW_REQ = 0;
let PREVIEW_ZOOM = 1;
let PREVIEW_FIT = true;
const PREVIEW_W = 794;  // A4 width @96dpi (210mm)
const PREVIEW_H = 1123; // A4 height @96dpi (297mm)

function scheduleEditorPreview(immediate) {
  if (immediate) {
    clearTimeout(PREVIEW_TIMER);
    runEditorPreview();
    return;
  }
  clearTimeout(PREVIEW_TIMER);
  PREVIEW_TIMER = setTimeout(runEditorPreview, 600);
}

async function runEditorPreview() {
  const iframe = document.getElementById('editor-preview');
  if (!iframe) return;
  const req = ++PREVIEW_REQ;
  try {
    const r = await api('/api/export/preview', {
      method: 'POST',
      body: { cv: CURRENT_CV, template: CURRENT_CV.template || 'modern' },
    });
    if (req !== PREVIEW_REQ) return;
    iframe.srcdoc = (r && r.html) || '<html><body></body></html>';
    const fb = document.getElementById('preview-fallback');
    if (fb) fb.hidden = true;
  } catch (err) {
    if (req !== PREVIEW_REQ) return;
    // Lightweight fallback: show the local renderer + an honest error note.
    const fb = document.getElementById('preview-fallback');
    if (fb) {
      fb.hidden = false;
      fb.innerHTML = `<div class="small muted preview-fallback-note">Preview service unavailable — showing a simplified layout (${esc(err.message)}).</div>` + renderPreviewString();
    }
  }
}

function applyPreviewZoom() {
  const viewport = document.querySelector('.preview-viewport');
  const page = document.getElementById('preview-page');
  const iframe = document.getElementById('editor-preview');
  const label = document.getElementById('preview-zoom-label');
  if (!viewport || !page || !iframe) return;
  let scale = PREVIEW_ZOOM;
  if (PREVIEW_FIT) {
    const avail = viewport.clientWidth - 24;
    scale = Math.min(1, Math.max(0.2, avail / PREVIEW_W));
  }
  page.style.width = `${Math.round(PREVIEW_W * scale)}px`;
  page.style.height = `${Math.round(PREVIEW_H * scale)}px`;
  iframe.style.width = `${PREVIEW_W}px`;
  iframe.style.height = `${PREVIEW_H}px`;
  iframe.style.transform = `scale(${scale})`;
  iframe.style.transformOrigin = 'top left';
  if (label) label.textContent = `${Math.round(scale * 100)}%`;
}

function bindPreviewControls() {
  const rootEl = app;
  const fit = rootEl.querySelector('#preview-fit');
  if (fit) {
    fit.checked = PREVIEW_FIT;
    fit.addEventListener('change', () => { PREVIEW_FIT = fit.checked; applyPreviewZoom(); });
  }
  const zoomBtns = rootEl.querySelectorAll('[data-zoom]');
  zoomBtns.forEach((b) => {
    b.addEventListener('click', () => {
      PREVIEW_FIT = false;
      const f = rootEl.querySelector('#preview-fit');
      if (f) f.checked = false;
      if (b.dataset.zoom === 'in') PREVIEW_ZOOM = Math.min(2, +(PREVIEW_ZOOM + 0.25).toFixed(2));
      else PREVIEW_ZOOM = Math.max(0.4, +(PREVIEW_ZOOM - 0.25).toFixed(2));
      applyPreviewZoom();
    });
  });
  const asst = rootEl.querySelector('#btn-assistant');
  if (asst) asst.addEventListener('click', () => toggleAssistant());
  // keep fit-width honest on resize (bound once)
  if (!window.__previewResizeBound) {
    window.__previewResizeBound = '1';
    window.addEventListener('resize', applyPreviewZoom);
  }
  requestAnimationFrame(applyPreviewZoom);
}

function editorSection(kind, title, inner, addLabel) {
  const collapsed = COLLAPSED_SECTIONS.has(kind);
  return `<div class="card" data-edkelist="${kind}" id="ed-section-${kind}">
    <div class="card-head-row">
      <h2>${title}</h2>
      <button type="button" class="card-collapse" data-collapse="${kind}" aria-expanded="${collapsed ? 'false' : 'true'}" aria-label="Toggle ${title}">${icon('chevronDown')}</button>
    </div>
    <div class="card-body">
      ${inner}
      <button class="btn btn-secondary btn-sm" data-dadd="${kind}">+ ${addLabel}</button>
    </div>
  </div>`;
}

function edField(k, label, val, i) {
  return `<div class="field"><label>${label}</label><input type="text" data-edk="${k}" data-edlist="1" data-i="${i}" value="${esc(val)}"></div>`;
}
function edDate(k, label, val, i) {
  return `<div class="field"><label>${label}</label><input type="text" data-edk="${k}" data-edlist="1" data-i="${i}" value="${esc(val)}" placeholder="2018-05"></div>`;
}
// End date with a "Present / current" toggle that stores "Present" (§5.1).
function edDateEnd(k, label, val, i, kind) {
  const present = val === 'Present';
  return `<div class="field"><label>${label}</label>
    <input type="text" data-edk="${k}" data-edlist="1" data-i="${i}" value="${esc(present ? '' : val)}" placeholder="2018-05" ${present ? 'disabled' : ''}>
    <label class="present-toggle"><input type="checkbox" data-edpresent="${kind}:${i}" ${present ? 'checked' : ''}> Present / current</label>
  </div>`;
}

function edItem(kind, i, head, fields, extra) {
  return `<div class="repeater-item editor-repeater-item" data-index="${i}" data-editor-drop="item" data-drag-kind="${kind}" data-drag-index="${i}">
    <div class="item-head"><span class="item-title-wrap"><span class="drag-handle" data-editor-drag="item" data-drag-kind="${kind}" data-drag-index="${i}" draggable="true" role="button" tabindex="0" aria-label="Drag ${head} to reorder" title="Drag to reorder">${icon('grip')}</span><span>${head}</span></span><span class="item-actions">${moveBtns(kind, i)}<button type="button" class="btn btn-ghost btn-sm" data-eddup="${kind}:${i}" title="Duplicate item">${icon('copy')}</button><button type="button" class="remove-btn" data-dremove="${kind}:${i}">Remove</button></span></div>
    <div class="fields">${fields}</div>${extra || ''}
  </div>`;
}

function editorBulletRow(kind, itemIndex, bulletIndex, value, textareaAttr, deleteAttr) {
  return `<div class="bullet-row editor-bullet-row" data-editor-drop="bullet" data-drag-kind="${kind}" data-drag-item="${itemIndex}" data-drag-bullet="${bulletIndex}">
    <span class="drag-handle drag-handle-small" data-editor-drag="bullet" data-drag-kind="${kind}" data-drag-item="${itemIndex}" data-drag-bullet="${bulletIndex}" draggable="true" role="button" tabindex="0" aria-label="Drag bullet ${bulletIndex + 1} to reorder" title="Drag to reorder">${icon('grip')}</span>
    <textarea class="bullet-grow" rows="1" ${textareaAttr}>${esc(value)}</textarea>
    <button type="button" data-edbdel="${deleteAttr}" aria-label="Remove bullet">${icon('x')}</button>
  </div>`;
}

function expItem(x, i) {
  return edItem('experience', i, `Experience ${i + 1}`,
    `${edField('company', 'Company', x.company, i)}${edField('role', 'Role', x.role, i)}
     ${edField('location', 'Location', x.location, i)}
     ${edDate('dates.start', 'Start', x.dates.start, i)}${edDateEnd('dates.end', 'End', x.dates.end, i, 'experience')}
     <div class="full"><label>Bullets</label><div data-edbullets="${i}">
        ${(x.bullets && x.bullets.length ? x.bullets : ['']).map((b, bi) => editorBulletRow('experience', i, bi, b, `data-edbullet="${i}" data-bi="${bi}"`, `experience:${i}:${bi}`)).join('')}
     </div><button type="button" class="btn btn-ghost btn-sm" data-edbadd="experience:${i}">+ bullet</button></div>`);
}
function eduItem(x, i) {
  return edItem('education', i, `Education ${i + 1}`,
    `${edField('institution', 'Institution', x.institution, i)}${edField('degree', 'Degree', x.degree, i)}
     ${edField('field', 'Field', x.field, i)}${edField('gpa', 'GPA', x.gpa, i)}
     ${edDate('dates.start', 'Start', x.dates.start, i)}${edDateEnd('dates.end', 'End', x.dates.end, i, 'education')}`);
}
function skillsItem(x, i) {
  return edItem('skills', i, `Skills ${i + 1}`,
    `${edField('category', 'Category', x.category, i)}
     <div class="full"><label>Skills</label><div class="skill-chips" data-edskill-chips="${i}"></div></div>`);
}
function projItem(x, i) {
  return edItem('projects', i, `Project ${i + 1}`,
    `${edField('name', 'Name', x.name, i)}${edField('link', 'Link', x.link, i)}
     <div class="full"><label>Description</label><textarea data-edprojectdesc="${i}">${esc(x.description)}</textarea></div>
     <div class="full"><label>Bullets</label><div data-edpbullets="${i}">
        ${(x.bullets && x.bullets.length ? x.bullets : ['']).map((b, bi) => editorBulletRow('projects', i, bi, b, `data-edpbullet="${i}" data-bi="${bi}"`, `projects:${i}:${bi}`)).join('')}
     </div><button type="button" class="btn btn-ghost btn-sm" data-edpbadd="projects:${i}">+ bullet</button></div>`);
}
function certItem(x, i) {
  return edItem('certifications', i, `Certification ${i + 1}`,
    `${edField('name', 'Name', x.name, i)}${edField('issuer', 'Issuer', x.issuer, i)}
     ${edField('year', 'Year', x.year, i)}`);
}
function langItem(x, i) {
  return edItem('languages', i, `Language ${i + 1}`,
    `${edField('name', 'Name', x.name, i)}${edField('level', 'Level', x.level, i)}`);
}
function customSectionItem(x, i) {
  return edItem('custom_sections', i, `Custom section ${i + 1}`,
    `${edField('title', 'Title', x.title, i)}
     <div class="full"><label>Bullets</label><div data-edcsbullets="${i}">
        ${(x.bullets && x.bullets.length ? x.bullets : ['']).map((b, bi) => editorBulletRow('custom_sections', i, bi, b, `data-edcsbullet="${i}" data-bi="${bi}"`, `custom_sections:${i}:${bi}`)).join('')}
     </div><button type="button" class="btn btn-ghost btn-sm" data-edcsbadd="custom_sections:${i}">+ bullet</button></div>`);
}

function editorFormHTML() {
  const c = CURRENT_CV;
  const pf = (k, label, type = 'text') =>
    `<div class="field"><label>${label}</label><input type="${type}" class="ed-personal" data-k="${k}" value="${esc(c.personal[k])}"></div>`;

  const empty = (n) => `<div class="empty-hint small">No ${n} yet.</div>`;

  const totallyEmpty = !c.personal.name && !c.summary
    && !c.experience.length && !c.education.length && !c.skills.length
    && !c.projects.length && !c.certifications.length && !c.languages.length
    && !(c.custom_sections || []).length;
  const emptyCard = totallyEmpty
    ? `<div class="card">${emptyStateHTML('document', 'Start building your CV', 'Use the form below to fill it in — or start from the Home page.', `<div class="btn-row center-row"><a class="btn btn-primary" href="#/build">Create a new CV</a><a class="btn btn-secondary" href="#/">Go to Home</a></div>`)}</div>`
    : '';

  return `
    ${emptyCard}
    <div class="card">
      <h2>Personal</h2>
      <div class="grid">
        ${pf('name', 'Name')}${pf('title', 'Title')}${pf('email', 'Email', 'email')}${pf('phone', 'Phone')}
        ${pf('location', 'Location')}${pf('website', 'Website')}${pf('linkedin', 'LinkedIn')}${pf('github', 'GitHub')}
      </div>
    </div>

    <div class="card">
      <h2 id="editor-template-section">Summary &amp; Template</h2>
      <div class="field"><label>Summary</label><textarea class="ed-summary" rows="4">${esc(c.summary)}</textarea>
        <div class="char-count" id="summary-count">${(c.summary || '').length.toLocaleString()} / 3,000</div></div>
      ${templatePickerHTML()}
      <button class="btn btn-secondary btn-sm" data-assist="summary">${icon('sparkles')} AI Assist Summary</button>
    </div>

    ${editorSection('experience', 'Experience', c.experience.length ? c.experience.map(expItem).join('') : empty('experience'), 'Add experience')}
    ${editorSection('education', 'Education', c.education.length ? c.education.map(eduItem).join('') : empty('education'), 'Add education')}
    ${editorSection('skills', 'Skills', c.skills.length ? c.skills.map(skillsItem).join('') : empty('skills'), 'Add skill group')}
    ${editorSection('projects', 'Projects', c.projects.length ? c.projects.map(projItem).join('') : empty('projects'), 'Add project')}
    ${editorSection('certifications', 'Certifications', c.certifications.length ? c.certifications.map(certItem).join('') : empty('certifications'), 'Add certification')}
    ${editorSection('languages', 'Languages', c.languages.length ? c.languages.map(langItem).join('') : empty('languages'), 'Add language')}
    ${editorSection('custom_sections', 'Custom sections', c.custom_sections && c.custom_sections.length ? c.custom_sections.map(customSectionItem).join('') : empty('custom sections'), 'Add custom section')}
  `;
}

function bindEditor() {
  const c = CURRENT_CV;
  const root = document.getElementById('app');

  // personal
  root.querySelectorAll('.ed-personal').forEach((inp) => {
    inp.addEventListener('input', () => { c.personal[inp.dataset.k] = inp.value; markDirty(); scheduleEditorPreview(); });
  });

  // summary
  const sum = root.querySelector('.ed-summary');
  if (sum) sum.addEventListener('input', () => { c.summary = sum.value; markDirty(); scheduleEditorPreview(); });

  // template/accent
  bindTemplatePicker(root);

  // Review banner CTA → jump to the template/accent section (§ editor landing).
  const tplBtn = root.querySelector('#btn-review-template');
  if (tplBtn) tplBtn.addEventListener('click', () => {
    const target = root.querySelector('#editor-template-section');
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // skill chip rows (rendered from the model after each re-render)
  root.querySelectorAll('[data-edskill-chips]').forEach((host) => {
    renderChipRow(host, parseInt(host.dataset.edskillChips, 10), true);
  });

  // collapsible section cards + jump-nav (§5.2)
  root.querySelectorAll('[data-collapse]').forEach((b) => b.addEventListener('click', () => {
    const kind = b.dataset.collapse;
    const card = b.closest('.card[data-edkelist]');
    if (!card) return;
    const collapsed = card.classList.toggle('collapsed');
    b.setAttribute('aria-expanded', String(!collapsed));
    if (collapsed) COLLAPSED_SECTIONS.add(kind); else COLLAPSED_SECTIONS.delete(kind);
  }));
  root.querySelectorAll('[data-jump]').forEach((b) => b.addEventListener('click', () => jumpToSection(b.dataset.jump)));

  // Generic editor inputs + click actions — delegated listeners on the
  // persistent #app root. Guarded with a one-time flag (mirrors bindSuggestions')
  // list.dataset.bound) so re-renders of the editor never stack duplicate
  // listeners. Each handler reads CURRENT_CV fresh so the closures never go stale.
  const onEditorInput = (e) => {
    const t = e.target;
    const c = CURRENT_CV;
    let changed = false;
    if (t.matches('[data-edk]')) {
      const i = parseInt(t.dataset.i, 10);
      const k = t.dataset.edk;
      const card = t.closest('[data-edkelist]');
      const kind = card.dataset.edkelist;
      if (k.includes('dates.')) {
        const [a, part] = k.split('.');
        c[kind][i][a][part] = t.value;
      } else {
        c[kind][i][k] = t.value;
      }
      changed = true;
      scheduleEditorPreview();
    }
    if (t.matches('[data-edbullet]')) {
      const i = parseInt(t.dataset.edbullet, 10);
      const bi = parseInt(t.dataset.bi, 10);
      c.experience[i].bullets[bi] = t.value;
      changed = true;
      scheduleEditorPreview();
    }
    if (t.matches('[data-edpbullet]')) {
      const i = parseInt(t.dataset.edpbullet, 10);
      const bi = parseInt(t.dataset.bi, 10);
      c.projects[i].bullets[bi] = t.value;
      changed = true;
      scheduleEditorPreview();
    }
    if (t.matches('[data-edprojectdesc]')) {
      c.projects[parseInt(t.dataset.edprojectdesc, 10)].description = t.value;
      changed = true;
      scheduleEditorPreview();
    }
    if (t.matches('[data-edcsbullet]')) {
      const i = parseInt(t.dataset.edcsbullet, 10);
      const bi = parseInt(t.dataset.bi, 10);
      c.custom_sections[i].bullets[bi] = t.value;
      changed = true;
      scheduleEditorPreview();
    }
    if (changed) { markDirty(); markSuggestionsStale(); }
  };

  const onEditorClick = (e) => {
    const c = CURRENT_CV;
    const add = e.target.closest('[data-edbadd]');
    if (add) {
      const [kind, i] = add.dataset.edbadd.split(':');
      c[kind][parseInt(i, 10)].bullets.push('');
      markDirty();
      markSuggestionsStale();
      rerenderEditor();
      return;
    }
    const pbadd = e.target.closest('[data-edpbadd]');
    if (pbadd) {
      const [kind, i] = pbadd.dataset.edpbadd.split(':');
      c[kind][parseInt(i, 10)].bullets.push('');
      markDirty();
      markSuggestionsStale();
      rerenderEditor();
      return;
    }
    const csbadd = e.target.closest('[data-edcsbadd]');
    if (csbadd) {
      const [kind, i] = csbadd.dataset.edcsbadd.split(':');
      c[kind][parseInt(i, 10)].bullets.push('');
      markDirty();
      markSuggestionsStale();
      rerenderEditor();
      return;
    }
    const bdel = e.target.closest('[data-edbdel]');
    if (bdel) {
      const [kind, i, bi] = bdel.dataset.edbdel.split(':');
      removeBulletWithUndo(kind, parseInt(i, 10), parseInt(bi, 10));
      return;
    }
    const pbdel = e.target.closest('[data-edpbdel]');
    if (pbdel) {
      const [kind, i, bi] = pbdel.dataset.edpbdel.split(':');
      removeBulletWithUndo(kind, parseInt(i, 10), parseInt(bi, 10));
      return;
    }
    const csbdel = e.target.closest('[data-edcsbdel]');
    if (csbdel) {
      const [kind, i, bi] = csbdel.dataset.edcsbdel.split(':');
      removeBulletWithUndo(kind, parseInt(i, 10), parseInt(bi, 10));
      return;
    }
    const dadd = e.target.closest('[data-dadd]');
    if (dadd) {
      c[dadd.dataset.dadd].push(defaultListItem(dadd.dataset.dadd));
      markDirty();
      markSuggestionsStale();
      rerenderEditor();
      return;
    }
    const dremove = e.target.closest('[data-dremove]');
    if (dremove) {
      const [kind, i] = dremove.dataset.dremove.split(':');
      removeItemWithUndo(kind, parseInt(i, 10));
    }
  };

  if (root.dataset.editorBound) {
    // The fresh per-render elements above were just rebound; the delegated
    // input/click listeners were attached to #app on the first call only.
  } else {
    root.dataset.editorBound = '1';
    root.addEventListener('input', onEditorInput);
    root.addEventListener('click', onEditorClick);
  }

  // AI assist in editor
  const assistBtn = root.querySelector('[data-assist="summary"]');
  if (assistBtn) assistBtn.addEventListener('click', async () => {
    if (!gateLLM()) return;
    setButtonLoading(assistBtn, true, 'Generating…');
    try {
      const r = await api('/api/cv/assist', { method: 'POST', body: { kind: 'summary', cv: c, job_description: (OPT_STATE && OPT_STATE.jobDescription) || null } });
      addUsage(r.usage);
      c.summary = (r && r.text) || '';
      const ta = root.querySelector('.ed-summary');
      if (ta) ta.value = c.summary;
      markDirty();
      scheduleEditorPreview();
      toast('Summary updated', 'success');
    } catch (err) { toast(err.message, 'error'); }
    finally { setButtonLoading(assistBtn, false); }
  });

  // save to library (smart: overwrite when the name already exists — §4.10)
  const saveBtn = root.querySelector('#btn-save-library');
  if (saveBtn) saveBtn.addEventListener('click', () => {
    const name = (c.personal && c.personal.name) || 'My CV';
    saveLibraryFlow(name, c, currentMeta());
  });

  // validate
  const valBtn = root.querySelector('#btn-validate');
  if (valBtn) valBtn.addEventListener('click', async () => {
    setButtonLoading(valBtn, true, 'Checking…');
    try {
      const r = await api('/api/cv/validate', { method: 'POST', body: { cv: c } });
      const w = (r && r.warnings) || [];
      if (!w.length) toast('No issues found — looks great!', 'success');
      else modal({ title: 'Validation', body: `<ul>${w.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`, confirmText: 'OK' });
    } catch (err) { toast(err.message, 'error'); }
    finally { setButtonLoading(valBtn, false); }
  });

// Re-analyze: POST /api/cv/analyze with the current CV + JD, show the new score.
  const reanalyzeBtn = root.querySelector('#btn-reanalyze');
  if (reanalyzeBtn) reanalyzeBtn.addEventListener('click', async () => {
    setButtonLoading(reanalyzeBtn, true, 'Analyzing…');
    const report = await runReanalyze();
    setButtonLoading(reanalyzeBtn, false);
    if (!report) return;
    const prevScore = OPT_STATE.scoreHistory.length > 1 ? OPT_STATE.scoreHistory[OPT_STATE.scoreHistory.length - 2] : null;
    const score = report.ats_score || 0;
    const circ = 2 * Math.PI * 17.5;
    const offset = circ * (1 - score / 100);
    const delta = prevScore !== null ? score - prevScore : null;
    modal({
      title: 'Re-analysis complete',
      body: `<div class="center">
        <div class="score-ring ${scoreClass(score)}" style="--p:${score};margin:0 auto">
          <svg viewBox="0 0 40 40">
            <circle class="track" cx="20" cy="20" r="17.5" fill="none" stroke-width="3.5"/>
            <circle class="bar" cx="20" cy="20" r="17.5" fill="none" stroke-width="3.5"
              stroke-dasharray="${circ.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}"/>
          </svg>
          <div class="score-label"><span><span class="score-num ${scoreClass(score)}">${score}</span><br><span class="score-txt">/ 100</span></span></div>
        </div>
        <p class="small muted">${delta !== null ? `Previous score <strong>${prevScore}</strong> → <span class="${delta >= 0 ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${delta}</span>` : 'First analysis for this CV.'}</p>
      </div>`,
      confirmText: 'Close',
    });
  });

  // exports
  const pdfBtn = root.querySelector('#btn-export-pdf');
  if (pdfBtn) pdfBtn.addEventListener('click', () => exportCv('pdf', 'resume.pdf', 'application/pdf', pdfBtn));
  const docxBtn = root.querySelector('#btn-export-docx');
  if (docxBtn) docxBtn.addEventListener('click', () => exportCv('docx', 'resume.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docxBtn));

  autogrowAll(root);
}

function defaultListItem(kind) {
  return {
    experience: () => ({ company: '', role: '', location: '', dates: { start: '', end: '' }, bullets: [] }),
    education: () => ({ institution: '', degree: '', field: '', dates: { start: '', end: '' }, gpa: '' }),
    skills: () => ({ category: '', skills: [] }),
    projects: () => ({ name: '', link: '', description: '', bullets: [] }),
    certifications: () => ({ name: '', issuer: '', year: '' }),
    languages: () => ({ name: '', level: '' }),
    custom_sections: () => ({ title: '', bullets: [] }),
  }[kind]();
}

function rerenderEditor() {
  renderEditor('editor');
}

async function exportCv(kind, fname, mime, btn) {
  setButtonLoading(btn, true, 'Exporting…');
  try {
    const res = await fetch(`/api/export/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cv: CURRENT_CV, template: (CURRENT_CV.template || 'modern') }),
    });
    if (!res.ok) {
      let msg = 'Export failed';
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    // dynamic filename from the CV name when available, else fall back to the default
    const rawName = (CURRENT_CV.personal && CURRENT_CV.personal.name) || '';
    const safeName = rawName.replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '_');
    const finalName = safeName ? `${safeName}.${kind}` : fname;
    downloadBlob(blob, finalName);
    toast(`${kind.toUpperCase()} exported`, 'success');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

function downloadBlob(blob, fname) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* ============================================================
   Library picker (shared)
   ============================================================ */
async function libraryPicker(onSelect) {
  let entries = [];
  try {
    entries = await api('/api/library');
  } catch (err) {
    toast(err.message, 'error');
    return;
  }
  if (!entries.length) {
    modal({
      title: 'My CVs',
      body: `<p>No saved CVs yet. Go to the Editor and use “Save to library”.</p>`,
      confirmText: 'OK',
    });
    return;
  }
  const close = modal({
    title: 'My CVs',
    body: `<div id="lib-list" style="display:grid;gap:8px">
      ${entries.map((e) => `<button type="button" class="btn btn-secondary" data-lib-id="${esc(e.id)}" style="justify-content:space-between">
        <span>${esc(e.name)}</span><span class="small muted">${esc((e.updated || '').slice(0, 16).replace('T', ' '))}</span></button>`).join('')}
    </div>`,
    confirmText: 'Cancel',
    onConfirm: () => {},
  });
  const listEl = document.getElementById('lib-list');
  if (listEl) {
    listEl.querySelectorAll('[data-lib-id]').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          const rec = await api(`/api/library/${b.dataset.libId}`);
          onSelect(rec.cv || rec);
          close();
        } catch (err) {
          toast(err.message, 'error');
        }
      });
    });
  }
}

/* ============================================================
   VIEW: Home (§4.11 landing)
   ============================================================ */
function renderHome() {
  app.innerHTML = `
    <div class="view-header">
      <h1>Welcome to CVIQ</h1>
      <p>Create a professional, ATS-friendly CV in minutes with AI assistance.</p>
    </div>
    <div class="home-hero">
      <div class="home-cards">
        <a href="#/build" class="home-card">
          <span class="home-ico">${icon('plus')}</span>
          <h2>Create new CV</h2>
          <p>Start from a blank sheet and build your professional profile from scratch.</p>
        </a>
        <a href="#/optimize" class="home-card">
          <span class="home-ico">${icon('upload')}</span>
          <h2>Upload & optimize</h2>
          <p>Import your existing CV and tailor it to a specific job description.</p>
        </a>
        <a href="#/library" class="home-card">
          <span class="home-ico">${icon('library')}</span>
          <h2>My CVs</h2>
          <p>Manage, edit and export your saved CVs.</p>
        </a>
      </div>
    </div>`;
}

/* ============================================================
   VIEW: My CVs (§4.10)
   ============================================================ */
async function renderLibrary() {
  app.innerHTML = `
    <div class="view-header">
      <h1>My CVs</h1>
      <p>Every design you've saved to the library, with one-click open, export and manage.</p>
    </div>
    <div class="btn-row mb">
      <a href="#/optimize" class="btn btn-primary">${icon('plus')} Upload new CV</a>
      ${(CURRENT_CV.personal && CURRENT_CV.personal.name) || CURRENT_CV.summary || CURRENT_CV.experience.length ? `<button class="btn btn-secondary" id="lib-save-current">${icon('save')} Save current as new</button>` : ''}
    </div>
    <div id="lib-grid">${skeletonHTML('library')}</div>`;
  const saveCur = app.querySelector('#lib-save-current');
  if (saveCur) saveCur.addEventListener('click', () => saveCurrentAsNew());
  const grid = document.getElementById('lib-grid');
  try {
    const entries = await api('/api/library');
    grid.innerHTML = entries.length
      ? entries.map(libCardHTML).join('')
      : emptyStateHTML('library', 'No saved CVs yet', 'Save your current CV from the Editor, or build a new one.', `<div class="btn-row center-row"><a class="btn btn-primary" href="#/build">Build a new CV</a><button class="btn btn-secondary" id="lib-empty-save">Save current</button></div>`);
    bindLibraryCards();
    const emptySave = grid.querySelector('#lib-empty-save');
    if (emptySave) emptySave.addEventListener('click', () => saveCurrentAsNew());
  } catch (err) {
    grid.innerHTML = `<div class="empty-state">Failed to load your CVs: ${esc(err.message)}</div>`;
  }
}

function libCardHTML(e) {
  const meta = e.meta || {};
  const chips = [];
  const tplName = templateNameForId(meta.template);
  if (tplName) chips.push(`<span class="chip">${esc(tplName)}</span>`);
  if (meta.ats_score !== undefined && meta.ats_score !== null) chips.push(`<span class="chip match">ATS ${esc(meta.ats_score)}</span>`);
  const jd = meta.jd ? String(meta.jd).slice(0, 48) : '';
  if (jd) chips.push(`<span class="chip">${icon('target')} ${esc(jd)}…</span>`);
  const updated = e.updated ? new Date(e.updated).toLocaleString() : '';
  return `<div class="lib-card" data-lib-id="${esc(e.id)}">
    <div class="lib-card-preview" data-lib-preview="${esc(e.id)}" data-lib-open="${esc(e.id)}" title="Open this CV">
      <div class="lib-preview-placeholder">${icon('image')}</div>
    </div>
    <div class="lib-card-head"><strong>${esc(e.name || 'Untitled CV')}</strong><span class="small muted">${esc(updated)}</span></div>
    <div class="chip-wrap mb">${chips.join('') || '<span class="small muted">No metadata</span>'}</div>
    <div class="btn-row">
      <button class="btn btn-sm btn-primary" data-lib-open="${esc(e.id)}">Open</button>
      <button class="btn btn-sm btn-secondary" data-lib-rename="${esc(e.id)}">Rename</button>
      <button class="btn btn-sm btn-secondary" data-lib-dup="${esc(e.id)}">Duplicate</button>
      <button class="btn btn-sm btn-secondary" data-lib-pdf="${esc(e.id)}">PDF</button>
      <button class="btn btn-sm btn-danger" data-lib-del="${esc(e.id)}">Delete</button>
    </div>
  </div>`;
}

function bindLibraryCards() {
  app.querySelectorAll('[data-lib-open]').forEach((b) => b.addEventListener('click', async () => {
    try {
      const rec = await api(`/api/library/${b.dataset.libOpen}`);
      CURRENT_CV = rec.cv || CURRENT_CV;
      markDirty();
      location.hash = '#/editor';
    } catch (err) { toast(err.message, 'error'); }
  }));

  const previewObserver = new IntersectionObserver((entries) => {
    entries.forEach(async (entry) => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const id = el.dataset.libPreview;
      previewObserver.unobserve(el);
      try {
        const r = await api(`/api/library/${id}/preview`);
        const frame = document.createElement('iframe');
        frame.className = 'lib-preview-frame';
        frame.setAttribute('aria-label', 'CV preview');
        frame.srcdoc = (r && r.html) || '';
        el.innerHTML = '';
        el.appendChild(frame);
      } catch (err) {
        el.innerHTML = `<div class="lib-preview-error">${icon('alert')} <span class="small muted">Preview unavailable</span></div>`;
      }
    });
  }, { rootMargin: '100px' });

  app.querySelectorAll('[data-lib-preview]').forEach(el => previewObserver.observe(el));

  app.querySelectorAll('[data-lib-rename]').forEach((b) => b.addEventListener('click', async () => {
    try {
      const rec = await api(`/api/library/${b.dataset.libRename}`);
      modal({
        title: 'Rename CV',
        body: `<div class="field"><label>Name</label><input type="text" id="lib-name" value="${esc(rec.name || '')}"></div>`,
        confirmText: 'Rename',
        onConfirm: async () => {
          const nm = (document.getElementById('lib-name') && document.getElementById('lib-name').value.trim()) || rec.name;
          try {
            await api(`/api/library/${rec.id}`, { method: 'PUT', body: { name: nm, cv: rec.cv, meta: rec.meta || {} } });
            toast('CV renamed', 'success');
            renderLibrary();
          } catch (err) { toast(err.message, 'error'); return false; }
        },
      });
    } catch (err) { toast(err.message, 'error'); }
  }));
  app.querySelectorAll('[data-lib-dup]').forEach((b) => b.addEventListener('click', async () => {
    try {
      const rec = await api(`/api/library/${b.dataset.libDup}`);
      await api('/api/library', { method: 'POST', body: { name: `${rec.name || 'CV'} (copy)`, cv: rec.cv, meta: rec.meta || {} } });
      toast('Duplicated — saved as a new library entry', 'success');
      renderLibrary();
    } catch (err) { toast(err.message, 'error'); }
  }));
  app.querySelectorAll('[data-lib-pdf]').forEach((b) => b.addEventListener('click', async () => {
    const btn = b;
    setButtonLoading(btn, true, 'Exporting…');
    try {
      const rec = await api(`/api/library/${b.dataset.libPdf}`);
      const res = await fetch('/api/export/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cv: rec.cv, template: (rec.cv && rec.cv.template) || 'modern' }),
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      downloadBlob(blob, `${(rec.name || 'cv').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '_')}.pdf`);
      toast('PDF exported', 'success');
    } catch (err) { toast(err.message, 'error'); }
    finally { setButtonLoading(btn, false); }
  }));
  app.querySelectorAll('[data-lib-del]').forEach((b) => b.addEventListener('click', () => {
    modal({
      title: 'Delete CV?',
      body: `<p>This permanently removes the CV from your library. This cannot be undone.</p>`,
      confirmText: 'Delete',
      danger: true,
      onConfirm: async () => {
        try {
          await api(`/api/library/${b.dataset.libDel}`, { method: 'DELETE' });
          toast('Deleted', 'success');
          renderLibrary();
        } catch (err) { toast(err.message, 'error'); return false; }
      },
    });
  }));
}

/* ============================================================
   Library save helpers (§4.10) — smart overwrite flow
   ============================================================ */
function currentMeta() {
  return {
    template: CURRENT_CV.template || 'modern',
    ats_score: (OPT_STATE.report && OPT_STATE.report.ats_score) || (OPT_STATE.scoreHistory && OPT_STATE.scoreHistory.length ? OPT_STATE.scoreHistory[OPT_STATE.scoreHistory.length - 1] : null),
    jd: (OPT_STATE && OPT_STATE.jobDescription) || '',
  };
}

function saveCurrentAsNew() {
  const name = (CURRENT_CV.personal && CURRENT_CV.personal.name) || 'My CV';
  saveLibraryFlow(name, CURRENT_CV, currentMeta());
}

// One modal that first asks for a name; if that name already exists it flips
// into an overwrite / save-as-new choice (POST or PUT /api/library).
function saveLibraryFlow(defaultName, cv, meta) {
  const root = document.getElementById('modal-root');
  const el = document.createElement('div');
  el.className = 'modal-backdrop';
  el.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
    <h3>Save to library</h3>
    <div class="modal-body"><div class="field"><label>Name</label><input type="text" id="lib-name" value="${esc(defaultName)}"></div></div>
    <div class="btn-row">
      <button class="btn btn-secondary" data-act="close">Cancel</button>
      <button class="btn btn-primary" data-act="confirm">Save</button>
    </div></div>`;
  const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const confirmBtn = el.querySelector('[data-act=confirm]');
  const cancelBtn = el.querySelector('[data-act=close]');
  let phase = 'name';
  let existing = null;
  let nm = '';
  let busy = false;

  const doPost = async () => {
    try {
      await api('/api/library', { method: 'POST', body: { name: nm, cv, meta } });
      writeDraft();
      toast('Saved to library', 'success');
      close();
    } catch (err) { toast(err.message, 'error'); }
  };
  const doPut = async () => {
    try {
      await api(`/api/library/${existing.id}`, { method: 'PUT', body: { name: nm, cv, meta } });
      writeDraft();
      toast(`Updated “${existing.name}”`, 'success');
      close();
    } catch (err) { toast(err.message, 'error'); }
  };

  confirmBtn.addEventListener('click', async () => {
    if (busy) return;
    if (phase === 'name') {
      busy = true;
      nm = (el.querySelector('#lib-name') && el.querySelector('#lib-name').value.trim()) || defaultName;
      let entries = [];
      try { entries = await api('/api/library'); } catch (e) { /* treat as new */ }
      busy = false;
      existing = entries.find((x) => String(x.name || '').trim().toLowerCase() === nm.toLowerCase());
      if (!existing) { await doPost(); return; }
      phase = 'overwrite';
      el.querySelector('h3').textContent = 'Overwrite existing CV?';
      el.querySelector('.modal-body').innerHTML = `<p>“${esc(existing.name)}” already exists. Overwrite it with the current CV?</p>`;
      confirmBtn.textContent = 'Overwrite';
      cancelBtn.textContent = 'Save as new';
      return;
    }
    await doPut();
  });
  cancelBtn.addEventListener('click', () => {
    if (phase === 'overwrite') { doPost(); return; }
    close();
  });
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  document.addEventListener('keydown', onKey);
  root.appendChild(el);
  el.querySelector('#lib-name').focus();
}

function safeAccent(v) {
  return /^#[0-9a-fA-F]{6}$/.test(String(v || '')) ? String(v) : '#2563eb';
}

/* ============================================================
   Local preview renderer — lightweight fallback only (§4.4).
   The Editor's primary preview is the server-rendered iframe.
   ============================================================ */
function renderPreview(rootEl) {
  if (!rootEl) return;
  rootEl.innerHTML = renderPreviewString();
}

function renderPreviewString() {
  const c = CURRENT_CV;
  const tpl = (TEMPLATE_CATALOG || builtinFallbackTemplates()).find((t) => t.id === c.template) || { font: 'system' };
  const font = (tpl && tpl.font) || 'system';
  const cls = `cv-preview ${font === 'serif' ? 'serif' : ''} ${c.template === 'minimal' ? 'minimal' : ''}`;
  // Sanitize before injecting anywhere — never trust the accent as CSS input.
  const acc = safeAccent(c.accent);
  document.documentElement.style.setProperty('--accent', acc);
  document.documentElement.style.setProperty('--accent-soft', hexToRgba(acc, 0.12));

  const p = c.personal || {};
  const contact = [p.email, p.phone, p.location, p.website, p.linkedin, p.github].filter(Boolean);
  const hasContent = p.name || c.summary || c.experience.length || c.education.length || c.skills.length || (c.custom_sections && c.custom_sections.length);

  let html = `<div class="${cls}" style="--accent:${acc}">`;
  if (!hasContent) {
    html += `<div class="empty-hint center">Start typing in the form, or build from scratch, to see live preview here.</div>`;
    html += `</div>`;
    return html;
  }
  if (p.name) html += `<h1 class="pv-name" style="color:${acc}">${esc(p.name)}</h1>`;
  if (p.title) html += `<div class="pv-title">${esc(p.title)}</div>`;
  if (contact.length) html += `<div class="pv-contact">${contact.map((x) => `<span>${esc(x)}</span>`).join('')}</div>`;

  if (c.summary) html += `<p class="pv-summary">${esc(c.summary)}</p>`;

  if (c.experience.length) {
    html += `<h2 class="pv-heading">Experience</h2>`;
    c.experience.forEach((x) => {
      if (!x.role && !x.company) return;
      html += `<div class="pv-item">
        <div class="pv-item-head"><span class="pv-role">${esc(x.role || '')}${x.company ? ` · <span class="pv-sub">${esc(x.company)}</span>` : ''}</span>
          <span class="pv-dates">${esc(x.dates.start)}${x.dates.end ? ' — ' + esc(x.dates.end) : ''}</span></div>
        ${x.location ? `<div class="pv-sub">${esc(x.location)}</div>` : ''}
        ${(x.bullets || []).filter(Boolean).length ? `<ul class="pv-bullets">${x.bullets.filter(Boolean).map((b) => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}
      </div>`;
    });
  }

  if (c.education.length) {
    html += `<h2 class="pv-heading">Education</h2>`;
    c.education.forEach((x) => {
      if (!x.institution && !x.degree) return;
      html += `<div class="pv-item">
        <div class="pv-item-head"><span class="pv-role">${esc(x.degree || '')}${x.field ? `, ${esc(x.field)}` : ''}${x.institution ? ` · <span class="pv-sub">${esc(x.institution)}</span>` : ''}</span>
          <span class="pv-dates">${esc(x.dates.start)}${x.dates.end ? ' — ' + esc(x.dates.end) : ''}</span></div>
        ${x.gpa ? `<div class="pv-sub">GPA: ${esc(x.gpa)}</div>` : ''}
      </div>`;
    });
  }

  const nonEmptySkills = (c.skills || []).filter((g) => g.category || (g.skills && g.skills.length));
  if (nonEmptySkills.length) {
    html += `<h2 class="pv-heading">Skills</h2><div class="pv-skills">`;
    nonEmptySkills.forEach((g) => {
      html += `<div class="pv-skill-cat"><b>${esc(g.category)}:</b> ${esc((g.skills || []).join(', '))}</div>`;
    });
    html += `</div>`;
  }

  if (c.projects.length) {
    html += `<h2 class="pv-heading">Projects</h2>`;
    c.projects.forEach((x) => {
      if (!x.name) return;
      html += `<div class="pv-item">
        <div class="pv-item-head"><span class="pv-role">${esc(x.name)}${x.link ? ` <a href="${esc(x.link)}" target="_blank" rel="noopener" class="pv-sub">${esc(x.link)}</a>` : ''}</span></div>
        ${x.description ? `<div class="pv-sub">${esc(x.description)}</div>` : ''}
        ${(x.bullets || []).filter(Boolean).length ? `<ul class="pv-bullets">${x.bullets.filter(Boolean).map((b) => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}
      </div>`;
    });
  }

  if (c.certifications.length) {
    html += `<h2 class="pv-heading">Certifications</h2>`;
    c.certifications.forEach((x) => {
      if (!x.name) return;
      html += `<div class="pv-item"><div class="pv-item-head"><span class="pv-role">${esc(x.name)}</span>${x.year ? `<span class="pv-dates">${esc(x.year)}</span>` : ''}</div>${x.issuer ? `<div class="pv-sub">${esc(x.issuer)}</div>` : ''}</div>`;
    });
  }

  if (c.languages.length) {
    html += `<h2 class="pv-heading">Languages</h2><div class="pv-langs">`;
    c.languages.forEach((x) => { if (x.name) html += `<span>${esc(x.name)}${x.level ? ` — <span class="pv-sub">${esc(x.level)}</span>` : ''}</span>`; });
    html += `</div>`;
  }

  if (c.custom_sections && c.custom_sections.length) {
    c.custom_sections.forEach((sec) => {
      const bullets = (sec.bullets || []).filter(Boolean);
      if (!sec.title && !bullets.length) return;
      html += `<h2 class="pv-heading">${esc(sec.title || 'Additional')}</h2>`;
      if (bullets.length) html += `<ul class="pv-bullets">${bullets.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>`;
    });
  }

  html += `</div>`;
  return html;
}

function hexToRgba(hex, a) {
  let m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '#2563eb');
  if (!m) m = /^#?([a-f\d]{6})$/i.exec('2563eb');
  if (!m) return `rgba(37,99,235,${a})`;
  const r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
  return `rgba(${r},${g},${b},${a})`;
}

/* ============================================================
   Reorder (↑/↓) + undo + session-warning + AI-parser helpers
   ============================================================ */
function moveBtns(kind, i) {
  return `<span class="move-btns">
    <button type="button" class="move-btn" data-moveup="${kind}:${i}" aria-label="Move up" title="Move up">↑</button>
    <button type="button" class="move-btn" data-movedown="${kind}:${i}" aria-label="Move down" title="Move down">↓</button>
  </span>`;
}
function moveBulletBtns(kind, i, bi) {
  return `<span class="move-btns">
    <button type="button" class="move-btn" data-bmoveup="${kind}:${i}:${bi}" aria-label="Move bullet up" title="Move up">↑</button>
    <button type="button" class="move-btn" data-bmovedown="${kind}:${i}:${bi}" aria-label="Move bullet down" title="Move down">↓</button>
  </span>`;
}

function onMoveClick(e) {
  const up = e.target.closest('[data-moveup]');
  if (up) { moveItem(up.dataset.moveup.split(':')[0], parseInt(up.dataset.moveup.split(':')[1], 10), -1); return; }
  const down = e.target.closest('[data-movedown]');
  if (down) { moveItem(down.dataset.movedown.split(':')[0], parseInt(down.dataset.movedown.split(':')[1], 10), 1); return; }
  const bup = e.target.closest('[data-bmoveup]');
  if (bup) { const p = bup.dataset.bmoveup.split(':'); moveBullet(p[0], parseInt(p[1], 10), parseInt(p[2], 10), -1); return; }
  const bdown = e.target.closest('[data-bmovedown]');
  if (bdown) { const p = bdown.dataset.bmovedown.split(':'); moveBullet(p[0], parseInt(p[1], 10), parseInt(p[2], 10), 1); }
}

let EDITOR_DRAG = null;
let EDITOR_ANNOUNCEMENT = '';

function clearEditorDropStates() {
  app.querySelectorAll('.is-dragging, .drop-target').forEach((el) => {
    el.classList.remove('is-dragging', 'drop-target');
  });
}

function dragLabel(kind, isBullet) {
  if (isBullet) return `${EDITOR_SECTION_LABELS[kind] || 'Section'} bullet`;
  return EDITOR_SECTION_LABELS[kind] || 'CV section item';
}

function onEditorDragStart(e) {
  const handle = e.target.closest('[data-editor-drag]');
  if (!handle || e.target.closest('input, textarea, select, button, a')) return;
  const isBullet = handle.dataset.editorDrag === 'bullet';
  const kind = handle.dataset.dragKind;
  const itemIndex = parseInt(handle.dataset.dragItem ?? handle.dataset.dragIndex, 10);
  const bulletIndex = isBullet ? parseInt(handle.dataset.dragBullet, 10) : null;
  if (!kind || Number.isNaN(itemIndex) || (isBullet && Number.isNaN(bulletIndex))) return;
  EDITOR_DRAG = { kind, itemIndex, bulletIndex, isBullet };
  handle.closest(isBullet ? '[data-editor-drop="bullet"]' : '[data-editor-drop="item"]')?.classList.add('is-dragging');
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', `${kind}:${itemIndex}:${bulletIndex ?? ''}`);
  }
}

function validEditorDrop(target) {
  if (!EDITOR_DRAG || !target) return false;
  const kind = target.dataset.dragKind;
  if (kind !== EDITOR_DRAG.kind) return false;
  const isBullet = target.dataset.editorDrop === 'bullet';
  if (isBullet !== EDITOR_DRAG.isBullet) return false;
  if (isBullet && parseInt(target.dataset.dragItem, 10) !== EDITOR_DRAG.itemIndex) return false;
  const targetIndex = parseInt(isBullet ? target.dataset.dragBullet : target.dataset.dragIndex, 10);
  return !Number.isNaN(targetIndex) && targetIndex !== (isBullet ? EDITOR_DRAG.bulletIndex : EDITOR_DRAG.itemIndex);
}

function onEditorDragOver(e) {
  const target = e.target.closest('[data-editor-drop]');
  if (!validEditorDrop(target)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  app.querySelectorAll('.drop-target').forEach((el) => { if (el !== target) el.classList.remove('drop-target'); });
  target.classList.add('drop-target');
}

function onEditorDrop(e) {
  const target = e.target.closest('[data-editor-drop]');
  if (!validEditorDrop(target)) return;
  e.preventDefault();
  const drag = EDITOR_DRAG;
  const list = CURRENT_CV[drag.kind];
  const targetIndex = parseInt(drag.isBullet ? target.dataset.dragBullet : target.dataset.dragIndex, 10);
  const sourceIndex = drag.isBullet ? drag.bulletIndex : drag.itemIndex;
  const values = drag.isBullet ? list[drag.itemIndex]?.bullets : list;
  if (!Array.isArray(values)) return;
  let insertAt = sourceIndex < targetIndex ? targetIndex + 1 : targetIndex;
  const [value] = values.splice(sourceIndex, 1);
  if (sourceIndex < insertAt) insertAt -= 1;
  values.splice(Math.max(0, Math.min(insertAt, values.length)), 0, value);
  const destination = Math.max(0, Math.min(insertAt, values.length - 1));
  markDirty();
  markSuggestionsStale();
  const label = dragLabel(drag.kind, drag.isBullet);
  EDITOR_ANNOUNCEMENT = `Moved ${label} ${sourceIndex + 1} to position ${destination + 1}.`;
  EDITOR_DRAG = null;
  clearEditorDropStates();
  rerenderCurrentView();
}

function onEditorDragEnd() {
  EDITOR_DRAG = null;
  clearEditorDropStates();
}

function moveItem(kind, i, dir) {
  const list = CURRENT_CV[kind];
  if (!Array.isArray(list)) return;
  const j = i + dir;
  if (j < 0 || j >= list.length) return;
  const tmp = list[i]; list[i] = list[j]; list[j] = tmp;
  markDirty();
  markSuggestionsStale();
  rerenderCurrentView();
}

function moveBullet(kind, i, bi, dir) {
  const item = CURRENT_CV[kind] && CURRENT_CV[kind][i];
  if (!item || !Array.isArray(item.bullets)) return;
  const j = bi + dir;
  if (j < 0 || j >= item.bullets.length) return;
  const tmp = item.bullets[bi]; item.bullets[bi] = item.bullets[j]; item.bullets[j] = tmp;
  markDirty();
  markSuggestionsStale();
  rerenderCurrentView();
}

function rerenderCurrentView() {
  if (document.getElementById('build-form')) rerenderBuildForm();
  else if (document.getElementById('editor-preview') || document.getElementById('preview-root')) rerenderEditor();
}

/* Undo stack for removes (items + bullets) — each entry keeps the removed
   object and its prior index so Undo can restore it in place. */
const UNDO_STACK = [];
function pushUndo(entry) {
  UNDO_STACK.push(entry);
  if (UNDO_STACK.length > 10) UNDO_STACK.shift();
}

function showUndoToast(message, entry) {
  const root = document.getElementById('toast-root');
  const t = document.createElement('div');
  t.className = 'toast info undo-toast';
  t.innerHTML = `<span class="toast-ic">${icon('undo')}</span><span>${esc(message)}</span>
    <button type="button" class="btn btn-sm btn-secondary undo-toast-btn">Undo</button>`;
  t.querySelector('.undo-toast-btn').addEventListener('click', () => { undoEntry(entry); t.remove(); });
  root.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

function undoEntry(entry) {
  const ci = UNDO_STACK.indexOf(entry);
  if (ci !== -1) UNDO_STACK.splice(ci, 1);
  const c = CURRENT_CV;
  if (entry.type === 'item') {
    const list = c[entry.kind];
    if (!Array.isArray(list)) return;
    list.splice(Math.min(entry.index, list.length), 0, entry.value);
  } else if (entry.type === 'bullet') {
    const item = c[entry.kind] && c[entry.kind][entry.itemIndex];
    if (!item || !Array.isArray(item.bullets)) return;
    item.bullets.splice(Math.min(entry.bulletIndex, item.bullets.length), 0, entry.value);
  }
  markDirty();
  rerenderCurrentView();
}

function removeItemWithUndo(kind, index) {
  const list = CURRENT_CV[kind];
  if (!Array.isArray(list) || index < 0 || index >= list.length) return;
  const [value] = list.splice(index, 1);
  const entry = { type: 'item', kind, index, value };
  pushUndo(entry);
  markDirty();
  markSuggestionsStale();
  showUndoToast('Item removed', entry);
  rerenderCurrentView();
}

function removeBulletWithUndo(kind, itemIndex, bulletIndex) {
  const item = CURRENT_CV[kind] && CURRENT_CV[kind][itemIndex];
  if (!item || !Array.isArray(item.bullets) || bulletIndex < 0 || bulletIndex >= item.bullets.length) return;
  const [value] = item.bullets.splice(bulletIndex, 1);
  const entry = { type: 'bullet', kind, itemIndex, bulletIndex, value };
  pushUndo(entry);
  markDirty();
  markSuggestionsStale();
  showUndoToast('Bullet removed', entry);
  rerenderCurrentView();
}

/* Session-expiry notice (§3.7) */
function sessionWarningBannerHTML() {
  return `<div class="banner session-warn" role="alert">
    <div class="banner-body"><h3>${icon('alert')} Upload session expired</h3>
      <p>Upload session expired — re-upload for best results on scanned PDFs.</p></div>
    <button type="button" class="btn btn-ghost btn-sm" data-dismiss-session-warn="1">Dismiss</button>
  </div>`;
}

function addSessionWarning(container, message) {
  if (!container || container.querySelector('.session-warn')) return;
  const div = document.createElement('div');
  div.className = 'banner session-warn';
  div.setAttribute('role', 'alert');
  div.innerHTML = `<div class="banner-body"><h3>${icon('alert')} Upload session expired</h3>
    <p>${esc(message || 'Upload session expired — re-upload for best results on scanned PDFs.')}</p></div>
    <button type="button" class="btn btn-ghost btn-sm" data-dismiss-session-warn="1">Dismiss</button>`;
  div.querySelector('[data-dismiss-session-warn]').addEventListener('click', () => {
    OPT_STATE.sessionWarning = '';
    div.remove();
  });
  container.prepend(div);
}

/* AI-assist output parsers (§4.9) */
function parseSkillGroups(text) {
  const groups = [];
  let other = [];
  (text || '').split(/\r?\n/).forEach((line) => {
    const t = (line || '').trim().replace(/^[-•*]\s*/, '');
    if (!t) return;
    const m = t.match(/^([^:]{1,64}?):\s*(.+)$/);
    if (m) {
      groups.push({ category: m[1].trim(), skills: m[2].split(',').map((s) => s.trim()).filter(Boolean) });
    } else {
      other = other.concat(t.split(',').map((s) => s.trim()).filter(Boolean));
    }
  });
  if (other.length) groups.push({ category: 'Other', skills: other });
  return groups;
}

function mergeSkillGroups(existing, incoming) {
  const out = (existing || []).map((g) => ({ category: g.category, skills: (g.skills || []).slice() }));
  (incoming || []).forEach((g) => {
    const hit = out.find((x) => String(x.category || '').toLowerCase() === String(g.category || '').toLowerCase());
    if (hit) {
      (g.skills || []).forEach((s) => { if (s && !hit.skills.includes(s)) hit.skills.push(s); });
    } else {
      out.push({ category: g.category, skills: (g.skills || []).slice() });
    }
  });
  return out;
}

function parseProjectOutput(text) {
  let description = '';
  const bullets = [];
  let inBullets = false;
  (text || '').split(/\r?\n/).forEach((line) => {
    const t = (line || '').trim();
    const dm = t.match(/^Description:\s*(.*)$/i);
    if (dm) { description = dm[1].trim(); inBullets = false; return; }
    if (/^Bullets:\s*$/i.test(t)) { inBullets = true; return; }
    if (inBullets || /^[-•*]\s*/.test(t)) {
      const b = t.replace(/^[-•*]\s*/, '').trim();
      if (b) bullets.push(b);
    }
  });
  return { description, bullets };
}

/* ============================================================
   FE-3 helpers: score semantics, editor ergonomics, validation,
   auto-growing bullets, skill chips, empty states, Ctrl+S.
   ============================================================ */

// §7.4 — ATS score → threshold class (<50 red, 50–74 amber, ≥75 green).
function scoreClass(score) {
  const n = Number(score);
  if (n < 50) return 'score-low';
  if (n < 75) return 'score-mid';
  return 'score-high';
}

// §5.2 — in-memory collapsed state for editor section cards (not persisted).
const COLLAPSED_SECTIONS = new Set();

// §5.2 — duplicate an item (experience/education/skills/projects/certs/
// languages/custom) right after the original.
function duplicateItem(kind, i) {
  const list = CURRENT_CV[kind];
  if (!Array.isArray(list) || i < 0 || i >= list.length) return;
  const copy = JSON.parse(JSON.stringify(list[i]));
  list.splice(i + 1, 0, copy);
  markDirty();
  markSuggestionsStale();
  rerenderCurrentView();
}

// §7.7 — icon + title + optional CTAs empty state.
function emptyStateHTML(iconName, title, sub, ctas) {
  return `<div class="empty-state icon-empty">${icon(iconName, 'empty-ic')}<h3>${esc(title)}</h3>${sub ? `<p>${esc(sub)}</p>` : ''}${ctas || ''}</div>`;
}

// §5.1 — skill chip editor (Build `data-skill-chips` + Editor `data-edskill-chips`).
function renderChipRow(container, gi, isEditor) {
  const group = CURRENT_CV.skills[gi];
  const skills = (group && group.skills) || [];
  const rmAttr = isEditor ? 'data-edskill-rm' : 'data-skill-rm';
  const addAttr = isEditor ? 'data-edskill-add' : 'data-skill-add';
  container.innerHTML = skills.map((s, idx) =>
    `<span class="skill-chip">${esc(s)}<button type="button" class="skill-chip-x" ${rmAttr}="${gi}:${idx}" aria-label="Remove ${esc(s)}">${icon('x')}</button></span>`
  ).join('') +
    `<input type="text" class="skill-chip-input" ${addAttr}="${gi}" placeholder="Add skill…" aria-label="Add a skill">`;
}

function addSkillChip(gi, value, isEditor, refocus) {
  const group = CURRENT_CV.skills[gi];
  if (!group) return;
  const v = (value || '').replace(/,+$/, '').trim();
  if (v && !(group.skills || []).some((s) => s.toLowerCase() === v.toLowerCase())) {
    group.skills = group.skills || [];
    group.skills.push(v);
    markDirty();
  }
  const host = isEditor ? document.querySelector(`[data-edskill-chips="${gi}"]`) : document.querySelector(`[data-skill-chips="${gi}"]`);
  if (host) {
    renderChipRow(host, gi, isEditor);
    if (refocus !== false) {
      const inp = host.querySelector('input');
      if (inp) inp.focus();
    }
  }
}

function removeSkillChip(gi, idx, isEditor) {
  const group = CURRENT_CV.skills[gi];
  if (!group || !group.skills) return;
  group.skills.splice(idx, 1);
  markDirty();
  const host = isEditor ? document.querySelector(`[data-edskill-chips="${gi}"]`) : document.querySelector(`[data-skill-chips="${gi}"]`);
  if (host) renderChipRow(host, gi, isEditor);
}

function onChipKeydown(e) {
  const t = e.target;
  const ed = t.matches('[data-edskill-add]');
  const bd = t.matches('[data-skill-add]');
  if (!ed && !bd) return;
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const gi = parseInt(ed ? t.dataset.edskillAdd : t.dataset.skillAdd, 10);
    addSkillChip(gi, t.value, !!ed, true);
  }
}

function onChipClick(e) {
  const rm = e.target.closest('[data-skill-rm], [data-edskill-rm]');
  if (!rm) return;
  const isEditor = !!rm.closest('[data-edskill-chips]');
  const ds = isEditor ? rm.dataset.edskillRm : rm.dataset.skillRm;
  if (!ds) return;
  const parts = ds.split(':');
  removeSkillChip(parseInt(parts[0], 10), parseInt(parts[1], 10), isEditor);
}

/* §5.1 — inline field validation (non-blocking, on blur). */
function validateField(input, type) {
  const v = (input.value || '').trim();
  if (!v) return '';
  if (type === 'email') {
    return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v) ? '' : 'Enter a valid email address.';
  }
  if (type === 'url') {
    return /^https?:\/\//i.test(v) ? '' : 'URL should start with http:// or https://.';
  }
  if (type === 'date') {
    const p = v.toLowerCase();
    if (p === 'present') return '';
    if (/^\d{4}$/.test(v)) return '';
    if (/^\d{4}\s*-\s*\d{2}$/.test(v)) return '';
    if (/^\d{4}\s*-\s*\d{2}\s*[-–—]\s*\d{4}\s*-\s*\d{2}$/.test(v)) return '';
    return 'Use YYYY-MM format.';
  }
  return '';
}

function validateAndShow(input, type) {
  const field = input.closest('.field');
  if (!field) return;
  const old = field.querySelector('.field-error');
  if (old) old.remove();
  const msg = validateField(input, type);
  if (msg) {
    input.classList.add('invalid');
    const err = document.createElement('div');
    err.className = 'field-error';
    err.textContent = msg;
    field.appendChild(err);
  } else {
    input.classList.remove('invalid');
  }
}

function onGlobalFocusOut(e) {
  const t = e.target;
  if (!(t instanceof HTMLElement)) return;
  if (t.matches('.personal, .ed-personal')) {
    const k = t.dataset.k || '';
    let type = '';
    if (t.type === 'email') type = 'email';
    else if (['website', 'linkedin', 'github'].includes(k)) type = 'url';
    if (type) validateAndShow(t, type);
  } else if (t.matches('[data-k="link"]')) {
    validateAndShow(t, 'url');
  } else if (t.matches('[data-k*="dates"], [data-edk*="dates"]')) {
    validateAndShow(t, 'date');
  }
  // skill chip input: commit typed text on blur
  if (t.matches('[data-skill-add], [data-edskill-add]')) {
    const ed = !!t.dataset.edskillAdd;
    const gi = parseInt(ed ? t.dataset.edskillAdd : t.dataset.skillAdd, 10);
    if (t.value.trim()) addSkillChip(gi, t.value, ed, false);
  }
}

/* §5.1 — bullets auto-grow; live summary char count; clear errors on input. */
function autogrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight + 2, 160) + 'px';
}

function autogrowAll(root) {
  (root || document).querySelectorAll('textarea.bullet-grow').forEach(autogrow);
}

function onGlobalInput(e) {
  const t = e.target;
  if (t.matches('textarea.bullet-grow')) autogrow(t);
  if (t.matches('.summary-input, .ed-summary')) {
    const el = document.getElementById('summary-count');
    if (el) el.textContent = `${t.value.length.toLocaleString()} / 3,000`;
  }
  if (t.matches('.field input, .field textarea')) {
    const field = t.closest('.field');
    if (field) {
      const err = field.querySelector('.field-error');
      if (err) { err.remove(); t.classList.remove('invalid'); }
    }
  }
}

/* §5.1 — "Present / current" end-date toggles (Build + Editor). */
function setPresent(kind, i, checked, isEditor, checkbox) {
  const item = CURRENT_CV[kind] && CURRENT_CV[kind][i];
  if (!item || !item.dates) return;
  // Scope the end-date input to the same repeater item (experience/education
  // entries share array indices, so a global query could hit the wrong one).
  const hostEl = checkbox ? checkbox.closest('.repeater-item') : null;
  const endInput = hostEl
    ? hostEl.querySelector(isEditor ? '[data-edk="dates.end"]' : '[data-k="dates.end"]')
    : null;
  if (checked) {
    if (endInput) { endInput.disabled = true; endInput.value = 'Present'; }
    item.dates.end = 'Present';
  } else {
    if (endInput) { endInput.disabled = false; if (endInput.value === 'Present') endInput.value = ''; }
    if (item.dates.end === 'Present') item.dates.end = '';
  }
  markDirty();
}

function onGlobalChange(e) {
  const t = e.target;
  if (t.matches('[data-present]')) {
    const [kind, i] = t.dataset.present.split(':');
    setPresent(kind, parseInt(i, 10), t.checked, false, t);
  } else if (t.matches('[data-edpresent]')) {
    const [kind, i] = t.dataset.edpresent.split(':');
    setPresent(kind, parseInt(i, 10), t.checked, true, t);
  }
}

/* §9 — Ctrl+S saves the current CV from the editor (§ a11y keyboard). */
function hasCVContent() {
  const c = CURRENT_CV;
  return !!(c.personal && (c.personal.name || c.personal.email))
    || !!c.summary || !!c.experience.length || !!c.education.length
    || !!c.skills.length || !!c.projects.length || !!c.certifications.length
    || !!c.languages.length || !!(c.custom_sections && c.custom_sections.length);
}

function onGlobalKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
    if (currentRoute() === 'editor' && hasCVContent()) {
      e.preventDefault();
      const name = (CURRENT_CV.personal && CURRENT_CV.personal.name) || '';
      saveLibraryFlow(name, CURRENT_CV, currentMeta());
    }
  }
}

document.addEventListener('DOMContentLoaded', init);

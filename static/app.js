/* SplitMate single-page UI. Vanilla JS, no build step, no external dependencies. */
'use strict';

const state = {
  health: null,
  parsed: null,
  plan: null,
  split: null,
  editor: null,
  event: null,
};

const CURRENCY_SYMBOLS = { USD: '$', EUR: '€', GBP: '£', CAD: 'CA$', AUD: 'A$' };

/* ------------------------------------------------------------- utilities */
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

function money(cents, currency) {
  const value = Math.abs(Math.round(Number(cents) || 0));
  const symbol = CURRENCY_SYMBOLS[currency] || '';
  const whole = Math.floor(value / 100).toLocaleString('en-US');
  const fraction = String(value % 100).padStart(2, '0');
  return `${symbol}${whole}.${fraction}`;
}

function safeUrl(url) {
  if (typeof url !== 'string') return null;
  if (url.startsWith('/mock/checkout')) return url;
  if (url.startsWith('https://') || url.startsWith('http://')) return url;
  return null;
}

async function api(path, options) {
  const opts = options || {};
  const request = {
    method: opts.method || 'GET',
    headers: { Accept: 'application/json' },
  };
  if (opts.body !== undefined && opts.body !== null) {
    request.headers['Content-Type'] = 'application/json';
    request.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, request);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const info = (data && data.error) || {};
    const error = new Error(info.message || `Request failed (${response.status})`);
    error.details = info.details || [];
    error.status = response.status;
    throw error;
  }
  return data;
}

function show(element) { if (element) element.classList.remove('hidden'); }
function hide(element) { if (element) element.classList.add('hidden'); }
function scrollToEl(element) {
  if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

let toastTimer = null;
function toast(message, kind, timeout) {
  const element = $('toast');
  element.textContent = message;
  element.className = 'toast ' + (kind === 'error' ? 'error' : 'ok');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => hide(element), timeout || (kind === 'error' ? 7000 : 4200));
}

function setStatus(element, message, kind) {
  element.textContent = message || '';
  element.className = 'status-line' + (kind === 'error' ? ' error' : kind === 'ok' ? ' ok' : '');
}

function renderWarnings(warnings, targetId) {
  const element = $(targetId || 'warnings');
  if (!warnings || !warnings.length) {
    hide(element);
    element.innerHTML = '';
    return;
  }
  element.innerHTML = '<strong>Heads up</strong><ul>' +
    warnings.map((warning) => `<li>${esc(warning)}</li>`).join('') + '</ul>';
  show(element);
}

function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel || 'Working…';
    button.disabled = true;
  } else {
    if (button.dataset.label) button.textContent = button.dataset.label;
    button.disabled = false;
  }
}

/* ---------------------------------------------------------------- startup */
document.addEventListener('DOMContentLoaded', () => {
  wireEvents();
  boot();
});

async function boot() {
  try {
    state.health = await api('/api/health');
    renderMode();
  } catch (error) {
    $('mode-text').textContent = 'server offline';
    toast('Could not reach the SplitMate server: ' + error.message, 'error', 9000);
  }
  await handleUrlParams();
}

function renderMode() {
  const health = state.health || {};
  const paypalReal = health.paypal_mode === 'sandbox';
  const aiReal = health.ai_mode === 'llm';
  const pill = $('mode-pill');
  pill.className = 'pill ' + (paypalReal && aiReal ? 'pill-live' : (paypalReal || aiReal) ? 'pill-mixed' : 'pill-mock');
  $('mode-text').textContent =
    (paypalReal ? 'PayPal sandbox' : 'Mock PayPal') + ' · ' +
    (aiReal ? (health.llm_model || 'LLM') : 'rule-based AI');
  $('footer-mode').textContent = 'mode: ' +
    (paypalReal ? 'real PayPal sandbox' : 'offline mock') + ', AI: ' +
    (aiReal ? (health.llm_model || 'LLM') : 'deterministic parser');
  if (health.warnings && health.warnings.length) {
    toast(health.warnings[0], 'error', 8000);
  }
  $('model-note-text').textContent = aiReal
    ? 'AI parsing is live via ' + (health.llm_model || 'the configured LLM') + '; mock PayPal orders point at the in-app simulated checkout.'
    : 'Mock mode uses a deterministic parser and offline PayPal stand-in — the same plan always produces the same split.';
}

async function handleUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const eventId = params.get('event');
  const token = params.get('token');
  if (token) {
    await resumePayPalReturn(token);
    return;
  }
  if (eventId && params.get('mock_paid')) {
    toast('Mock payment captured. Dashboard updated.', 'ok');
    await loadEvent(eventId);
    return;
  }
  if (eventId && params.get('mock_cancelled')) {
    toast('Mock checkout cancelled — nothing captured.', 'error');
    await loadEvent(eventId);
    return;
  }
  if (eventId) await loadEvent(eventId);
  else if (params.get('demo')) await loadDemo();
}

async function resumePayPalReturn(token) {
  try {
    const found = await api('/api/orders/' + encodeURIComponent(token));
    const eventId = found.event.id;
    const name = found.share.name;
    try {
      await api(`/api/events/${encodeURIComponent(eventId)}/shares/${encodeURIComponent(name)}/capture`,
        { method: 'POST', body: {} });
      toast('PayPal payment captured — dashboard updated.', 'ok');
    } catch (error) {
      await api(`/api/events/${encodeURIComponent(eventId)}/sync`, { method: 'POST', body: {} });
      toast(error.message, 'error', 8000);
    }
    await loadEvent(eventId);
  } catch (error) {
    toast('Could not resume that PayPal order: ' + error.message, 'error', 9000);
  }
}

/* ----------------------------------------------------------------- events */
function wireEvents() {
  $('parse-btn').addEventListener('click', parsePlan);
  $('demo-btn').addEventListener('click', loadDemo);
  $('how-btn').addEventListener('click', () => scrollToEl($('how')));
  $('recalc-btn').addEventListener('click', previewFromEditor);
  $('create-btn').addEventListener('click', createEvent);
  $('preview-create-btn').addEventListener('click', createEvent);
  $('sync-all-btn').addEventListener('click', syncAll);
  $('capture-all-btn').addEventListener('click', captureApproved);
  $('new-split-btn').addEventListener('click', resetToStart);
  $('add-participant').addEventListener('click', addParticipant);
  $('add-expense').addEventListener('click', addExpense);
  $('modal-close').addEventListener('click', closeModal);
  $('modal-done').addEventListener('click', closeModal);
  $('modal-copy').addEventListener('click', copyModalText);
  $('modal').addEventListener('click', (event) => {
    if (event.target === $('modal')) closeModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal();
  });
  $('plan-input').addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      parsePlan();
    }
  });
  $('participants').addEventListener('input', onParticipantInput);
  $('participants').addEventListener('click', onParticipantClick);
  $('expenses').addEventListener('input', onExpenseInput);
  $('expenses').addEventListener('change', onExpenseInput);
  $('expenses').addEventListener('click', onExpenseClick);
  $('share-rows').addEventListener('click', onShareRowClick);
}

/* ------------------------------------------------------------------ parse */
async function loadDemo() {
  try {
    const demo = await api('/api/demo');
    $('plan-input').value = demo.plan_text;
    setStatus($('parse-status'), 'Demo plan loaded. Parsing…');
    await parsePlan();
  } catch (error) {
    setStatus($('parse-status'), error.message, 'error');
  }
}

async function parsePlan() {
  const text = $('plan-input').value.trim();
  if (!text) {
    setStatus($('parse-status'), 'Paste an event plan first (or click "Load demo plan").', 'error');
    return;
  }
  const button = $('parse-btn');
  setBusy(button, true, 'Parsing…');
  setStatus($('parse-status'), 'Reading the plan…');
  try {
    const result = await api('/api/parse', { method: 'POST', body: { text } });
    state.parsed = result;
    state.plan = result.plan;
    state.split = result.split;
    state.editor = editorFromPlan(result.plan);
    renderMode();
    renderWarnings(result.warnings);
    renderEditor();
    renderPreview();
    show($('editor'));
    hide($('dashboard'));
    if (result.split) show($('preview'));
    else hide($('preview'));
    const parserLabel = result.parser === 'llm'
      ? 'Parsed with ' + (result.model || 'an LLM')
      : 'Parsed with the deterministic parser';
    setStatus(
      $('parse-status'),
      `${parserLabel} · ${result.plan.expenses.length} expense(s), ${result.plan.participants.length} participant(s).`,
      result.warnings.length ? 'error' : 'ok'
    );
    scrollToEl($('editor'));
  } catch (error) {
    const details = (error.details || []).join(' ');
    setStatus($('parse-status'), details ? error.message + ' ' + details : error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

/* ----------------------------------------------------------------- editor */
function editorFromPlan(plan) {
  return {
    participants: (plan.participants || []).slice(),
    expenses: (plan.expenses || []).map((expense) => ({
      description: expense.description || '',
      amount_text: ((Number(expense.amount_cents) || 0) / 100).toFixed(2),
      paid_by: expense.paid_by || '',
      shared_by: (expense.shared_by || plan.participants || []).slice(),
      note: expense.note || '',
    })),
  };
}

function planFromEditor() {
  const editor = state.editor || { participants: [], expenses: [] };
  const participants = editor.participants.map((name) => String(name).trim()).filter(Boolean);
  const expenses = editor.expenses.map((expense) => {
    const shared = participants.filter((name) => expense.shared_by.includes(name));
    return {
      description: String(expense.description || '').trim() || 'Expense',
      amount: String(expense.amount_text || '').trim(),
      paid_by: expense.paid_by || null,
      shared_by: shared,
      note: expense.note || '',
    };
  });
  return {
    title: (state.plan && state.plan.title) || 'Group expenses',
    currency: (state.plan && state.plan.currency) || 'USD',
    participants,
    expenses,
    notes: (state.plan && state.plan.notes) || [],
  };
}

function renderEditor() {
  if (!state.editor) return;
  const editor = state.editor;
  const participantsEl = $('participants');
  const canRemove = editor.participants.length > 2;
  participantsEl.innerHTML = editor.participants.map((name, index) => `
    <span class="chip">
      <label class="sr-only" for="participant-${index}">Participant ${index + 1}</label>
      <input id="participant-${index}" value="${esc(name)}" data-participant-index="${index}" autocomplete="off" spellcheck="false">
      <button type="button" data-remove-participant="${index}" title="${canRemove ? 'Remove participant' : 'A split needs at least two people'}" ${canRemove ? '' : 'disabled'} aria-label="Remove participant">×</button>
    </span>`).join('');

  const expensesEl = $('expenses');
  if (!editor.expenses.length) {
    expensesEl.innerHTML = '<p class="sub">No expenses yet. Add one or re-parse the plan.</p>';
    return;
  }
  const labels = `
    <div class="expense-labels" aria-hidden="true">
      <span>Item</span><span>Amount</span><span>Paid by</span><span>Shared by</span><span></span>
    </div>`;
  const rows = editor.expenses.map((expense, index) => {
    const options = ['<option value="">— who paid? —</option>']
      .concat(editor.participants.map((name) =>
        `<option value="${esc(name)}" ${expense.paid_by === name ? 'selected' : ''}>${esc(name)}</option>`))
      .join('');
    const toggles = editor.participants.map((name) => {
      const on = expense.shared_by.includes(name);
      return `<button type="button" class="share-toggle ${on ? 'on' : ''}" data-expense-index="${index}"
        data-share-name="${esc(name)}" aria-pressed="${on}" title="Include ${esc(name)} in this expense">${esc(name)}</button>`;
    }).join('');
    return `
      <div class="expense-row" data-expense-row="${index}">
        <input value="${esc(expense.description)}" data-expense-index="${index}" data-field="description"
          placeholder="Item, e.g. Cabin" aria-label="Expense ${index + 1} description" autocomplete="off">
        <input class="amount-input" value="${esc(expense.amount_text)}" data-expense-index="${index}" data-field="amount"
          placeholder="0.00" inputmode="decimal" aria-label="Expense ${index + 1} amount">
        <select data-expense-index="${index}" data-field="paid_by" aria-label="Expense ${index + 1} payer">${options}</select>
        <div class="share-toggles" role="group" aria-label="Who shares expense ${index + 1}">${toggles}</div>
        <button type="button" class="row-delete" data-delete-expense="${index}" aria-label="Remove expense" title="Remove expense">×</button>
      </div>`;
  }).join('');
  expensesEl.innerHTML = labels + rows;

  const notes = (state.plan && state.plan.notes) || [];
  if (notes.length) {
    $('ai-notes-list').innerHTML = notes.map((note) => `<li>${esc(note)}</li>`).join('');
    show($('ai-notes'));
  } else {
    hide($('ai-notes'));
  }

  renderEditorReasoning();
}

function renderEditorReasoning() {
  const list = $('reasoning-list');
  if (!state.split) {
    list.innerHTML = '<li>Add participants and payers, then recalculate to see the reasoning.</li>';
    hide($('rounding-note'));
    return;
  }
  const expenseLines = state.split.reasoning.filter((line) => !line.startsWith('Net:') && !/ fronted | pays | settled up/.test(line));
  const netLines = state.split.reasoning.filter((line) => / fronted | pays | settled up/.test(line));
  list.innerHTML = expenseLines.map((line) => `<li>${esc(line)}</li>`).join('') +
    netLines.map((line) => `<li class="money">${esc(line)}</li>`).join('');
  if (state.split.rounding_notes && state.split.rounding_notes.length) {
    $('rounding-note').textContent = 'Rounding: ' + state.split.rounding_notes.join(' ');
    show($('rounding-note'));
  } else {
    hide($('rounding-note'));
  }
}

function onParticipantInput(event) {
  const input = event.target.closest('input[data-participant-index]');
  if (!input || !state.editor) return;
  const index = Number(input.dataset.participantIndex);
  const oldName = state.editor.participants[index];
  const newName = input.value;
  state.editor.participants[index] = newName;
  state.editor.expenses.forEach((expense) => {
    expense.shared_by = expense.shared_by.map((name) => (name === oldName ? newName : name));
    if (expense.paid_by === oldName) expense.paid_by = newName;
  });
}

function onParticipantClick(event) {
  const button = event.target.closest('button[data-remove-participant]');
  if (!button || !state.editor) return;
  if (state.editor.participants.length <= 2) {
    toast('A split needs at least two participants.', 'error');
    return;
  }
  const index = Number(button.dataset.removeParticipant);
  const [removed] = state.editor.participants.splice(index, 1);
  state.editor.expenses.forEach((expense) => {
    expense.shared_by = expense.shared_by.filter((name) => name !== removed);
    if (expense.paid_by === removed) expense.paid_by = '';
  });
  renderEditor();
}

function onExpenseInput(event) {
  const field = event.target.dataset.field;
  const index = Number(event.target.dataset.expenseIndex);
  if (!field || Number.isNaN(index) || !state.editor) return;
  const expense = state.editor.expenses[index];
  if (!expense) return;
  expense[field === 'paid_by' ? 'paid_by' : field] = event.target.value;
  if (field === 'paid_by') renderEditorReasoning();
}

function onExpenseClick(event) {
  const toggle = event.target.closest('button[data-share-name]');
  if (toggle && state.editor) {
    const index = Number(toggle.dataset.expenseIndex);
    const name = toggle.dataset.shareName;
    const expense = state.editor.expenses[index];
    if (!expense) return;
    if (expense.shared_by.includes(name)) {
      if (expense.shared_by.length === 1) {
        toast('At least one participant has to share an expense.', 'error');
        return;
      }
      expense.shared_by = expense.shared_by.filter((item) => item !== name);
    } else {
      expense.shared_by.push(name);
    }
    renderEditor();
    return;
  }
  const remove = event.target.closest('button[data-delete-expense]');
  if (remove && state.editor) {
    const index = Number(remove.dataset.deleteExpense);
    state.editor.expenses.splice(index, 1);
    renderEditor();
  }
}

function addParticipant() {
  if (!state.editor) return;
  let index = state.editor.participants.length + 1;
  let name = 'Person ' + index;
  while (state.editor.participants.includes(name)) {
    index += 1;
    name = 'Person ' + index;
  }
  state.editor.participants.push(name);
  state.editor.expenses.forEach((expense) => {
    if (!expense.shared_by.includes(name)) expense.shared_by.push(name);
  });
  renderEditor();
  const inputs = document.querySelectorAll('input[data-participant-index]');
  const last = inputs[inputs.length - 1];
  if (last) { last.focus(); last.select(); }
}

function addExpense() {
  if (!state.editor) return;
  state.editor.expenses.push({
    description: '',
    amount_text: '',
    paid_by: '',
    shared_by: state.editor.participants.slice(),
    note: '',
  });
  renderEditor();
  const rows = document.querySelectorAll('input[data-field="description"]');
  const last = rows[rows.length - 1];
  if (last) last.focus();
}

async function previewFromEditor() {
  if (!state.editor) return;
  const plan = planFromEditor();
  const button = $('recalc-btn');
  setBusy(button, true, 'Calculating…');
  try {
    const result = await api('/api/preview', { method: 'POST', body: { plan } });
    state.plan = result.plan;
    state.split = result.split;
    state.parsed = result;
    renderWarnings(result.warnings);
    renderEditorReasoning();
    renderPreview();
    if (result.split) {
      show($('preview'));
      toast('Split recalculated.', 'ok');
    } else {
      hide($('preview'));
    }
  } catch (error) {
    renderWarnings([error.message].concat(error.details || []));
    toast(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

/* ---------------------------------------------------------------- preview */
function renderPreview() {
  if (!state.split) {
    hide($('preview'));
    return;
  }
  const split = state.split;
  const currency = split.currency;
  const missingPayers = (state.editor ? state.editor.expenses : [])
    .filter((expense) => !expense.paid_by)
    .map((expense) => expense.description || 'an expense');
  const ready = missingPayers.length === 0;

  $('preview-totals').innerHTML = [
    ['Total expenses', money(split.total_cents, currency), ''],
    ['Collecting with PayPal', money(split.totals.orders_cents, currency), 'mint'],
    ['Credits back to payers', money(split.totals.credits_cents, currency), 'violet'],
    ['Payments created', String(split.orders.length), 'blue'],
  ].map(([label, value, className]) => `
    <div class="stat"><div class="label">${esc(label)}</div>
    <div class="value ${className}">${esc(value)}</div></div>`).join('');

  $('person-cards').innerHTML = split.participants.map((person) => {
    const isCredit = person.net_cents > 0;
    const isOwed = person.net_cents < 0;
    const pill = isOwed ? 'owes' : isCredit ? 'credit' : 'settled';
    const pillText = isOwed ? 'owes' : isCredit ? 'is owed' : 'settled';
    const amountClass = isOwed ? 'owes' : isCredit ? 'credit' : '';
    return `
      <div class="person-card">
        <div class="person-name">${esc(person.name)}<span class="mini-pill ${pill}">${pillText}</span></div>
        <div class="person-amount ${amountClass}">${esc(money(Math.abs(person.net_cents), currency))}</div>
        <div class="person-meta">paid ${esc(money(person.paid_cents, currency))} · share ${esc(money(person.share_cents, currency))}</div>
      </div>`;
  }).join('');

  $('split-table').innerHTML = split.expenses.map((expense) => {
    const breakdown = Object.entries(expense.shares)
      .map(([name, amount]) => `<span class="share-chip">${esc(name)} ${esc(money(amount, currency))}</span>`)
      .join('');
    const note = expense.note ? `<div class="split-note">${esc(expense.note)}</div>` : '';
    return `
      <div class="split-row">
        <div>
          <div class="desc">${esc(expense.description)}</div>
          <div class="meta">paid by ${esc(expense.paid_by || '—')} · shared by ${expense.shared_by.length}</div>
          <div class="share-breakdown">${breakdown}</div>
          ${note}
        </div>
        <div class="amt">${esc(money(expense.amount_cents, currency))}</div>
      </div>`;
  }).join('');

  $('preview-reasoning').innerHTML = split.reasoning
    .map((line) => `<li class="${/ fronted | pays | settled up/.test(line) ? 'money' : ''}">${esc(line)}</li>`)
    .join('');

  $('preview-create-btn').disabled = !ready;
  if (!ready) {
    renderWarnings(
      ['Choose who paid for: ' + missingPayers.join(', ') + '. Then recalculate the split.'],
      'warnings'
    );
  }
}

/* ------------------------------------------------------------------ event */
async function createEvent() {
  if (!state.editor) return;
  const plan = planFromEditor();
  const buttons = [$('create-btn'), $('preview-create-btn')];
  buttons.forEach((button) => setBusy(button, true, 'Creating orders…'));
  try {
    const result = await api('/api/events', { method: 'POST', body: { plan } });
    state.event = result.event;
    renderDashboard();
    hide($('preview'));
    show($('dashboard'));
    scrollToEl($('dashboard'));
    const failed = result.event.orders.filter((order) => order.status === 'failed').length;
    const made = result.event.orders.length - failed;
    toast(
      failed
        ? `Created ${made} PayPal order(s); ${failed} failed. Check the row errors and retry.`
        : `Created ${made} PayPal order(s). Share the checkout links!`,
      failed ? 'error' : 'ok'
    );
  } catch (error) {
    renderWarnings([error.message].concat(error.details || []));
    toast(error.message, 'error', 8000);
  } finally {
    buttons.forEach((button) => setBusy(button, false));
  }
}

async function loadEvent(eventId) {
  try {
    const result = await api('/api/events/' + encodeURIComponent(eventId));
    state.event = result.event;
    state.plan = result.event.plan;
    state.split = result.event.split;
    renderDashboard();
    hide($('editor'));
    hide($('preview'));
    show($('dashboard'));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) {
    toast('Could not load that event: ' + error.message, 'error');
  }
}

function statusLabel(status) {
  return ({
    order_created: 'order created',
    approved: 'approved — ready to capture',
    captured: 'captured',
    credit: 'is owed',
    settled: 'settled',
    failed: 'order failed',
    pending: 'pending',
  })[status] || status;
}

function renderDashboard() {
  const event = state.event;
  if (!event) return;
  const currency = event.currency;
  const totals = event.totals || {};
  const backend = event.paypal_mode === 'sandbox' ? 'PayPal sandbox' : 'mock PayPal';
  $('dashboard-sub').textContent =
    `${event.title} · ${backend} · created ${event.created_at} · one order per person who owes`;

  const capturedPct = totals.collected_cents
    ? Math.round((100 * (totals.captured_cents || 0)) / totals.collected_cents)
    : 0;
  $('dashboard-totals').innerHTML = [
    ['Expenses', money(totals.expenses_cents || 0, currency), ''],
    ['Collecting', money(totals.collected_cents || 0, currency), 'mint'],
    ['Captured', money(totals.captured_cents || 0, currency), 'blue'],
    ['Credits owed', money(totals.credits_cents || 0, currency), 'violet'],
  ].map(([label, value, className]) => `
    <div class="stat"><div class="label">${esc(label)}</div>
    <div class="value ${className}">${esc(value)}</div></div>`).join('');

  const note = $('dashboard-note');
  if (event.paypal_mode === 'sandbox') {
    note.innerHTML = 'Live <strong>PayPal sandbox</strong> orders: "Open checkout" opens PayPal\'s sandbox approval page in a new tab. ' +
      'After paying with a sandbox buyer account, click <strong>Sync</strong> then <strong>Capture</strong> — or return via the checkout return link for automatic capture.';
    show(note);
  } else {
    note.innerHTML = 'Mock mode: "Open checkout" opens SplitMate\'s own simulated PayPal page, which approves and captures instantly. ' +
      'No network calls, no PayPal account, no real money.';
    show(note);
  }

  const progress = `
    <div class="progress-wrap">
      <div class="progress"><span style="width:${capturedPct}%"></span></div>
      <div class="progress-meta"><span>${totals.captured_count || 0}/${totals.order_count || 0} orders captured</span><span>${capturedPct}%</span></div>
    </div>`;

  const rows = event.shares.map((share) => {
    const isCredit = share.status === 'credit';
    const isSettled = share.status === 'settled';
    const amount = isCredit ? share.net_cents : share.amount_cents;
    const orderLine = share.order_id
      ? `<div class="share-order">${esc(share.order_id)}${share.capture_id ? ' · capture ' + esc(share.capture_id) : ''}</div>`
      : (isCredit ? '<div class="share-order">fronted more than their share</div>'
        : isSettled ? '<div class="share-order">all square</div>' : '');

    const actions = [];
    const checkoutUrl = safeUrl(share.approve_url);
    if (checkoutUrl && share.status !== 'captured') {
      actions.push(`<a class="btn btn-primary btn-small" href="${esc(checkoutUrl)}" target="_blank" rel="noopener noreferrer">Open checkout</a>`);
      actions.push(`<button class="btn btn-secondary btn-small" data-action="copy" data-name="${esc(share.name)}">Copy link</button>`);
    }
    if (share.order_id && share.status !== 'captured') {
      actions.push(`<button class="btn btn-secondary btn-small" data-action="sync" data-name="${esc(share.name)}">Sync</button>`);
      actions.push(`<button class="btn btn-secondary btn-small" data-action="capture" data-name="${esc(share.name)}">Capture</button>`);
    }
    if (share.status === 'failed') {
      actions.push(`<button class="btn btn-danger btn-small" data-action="retry" data-name="${esc(share.name)}">Retry order</button>`);
    }
    if (!isSettled) {
      actions.push(`<button class="btn btn-ghost btn-small" data-action="remind" data-name="${esc(share.name)}">AI reminder</button>`);
    }
    return `
      <div class="share-row">
        <div><div class="share-name">${esc(share.name)}</div>${orderLine}</div>
        <div class="share-amount">${esc(money(amount, currency))}</div>
        <div><span class="status-pill status-${esc(share.status)}">${esc(statusLabel(share.status))}</span></div>
        <div class="share-actions">${actions.join('')}</div>
        ${share.last_error ? `<div class="share-error">${esc(share.last_error)}</div>` : ''}
      </div>`;
  }).join('');

  $('share-rows').innerHTML = progress + rows;
}

async function onShareRowClick(event) {
  const button = event.target.closest('button[data-action]');
  if (!button || !state.event) return;
  const action = button.dataset.action;
  const name = button.dataset.name;
  const eventId = state.event.id;
  const encodedName = encodeURIComponent(name);
  const share = state.event.shares.find((item) => item.name === name);

  if (action === 'copy') {
    if (!share || !share.approve_url) return;
    try {
      await navigator.clipboard.writeText(share.approve_url);
      toast('Checkout link copied for ' + name + '.', 'ok');
    } catch (error) {
      window.prompt('Copy this checkout link:', share.approve_url);
    }
    return;
  }
  if (action === 'remind') {
    await draftReminder(name);
    return;
  }
  const paths = {
    sync: `/api/events/${eventId}/shares/${encodedName}/sync`,
    capture: `/api/events/${eventId}/shares/${encodedName}/capture`,
    retry: `/api/events/${eventId}/shares/${encodedName}/retry`,
  };
  const labels = { sync: 'Synced', capture: 'Captured', retry: 'Order retried' };
  setBusy(button, true, '…');
  try {
    const result = await api(paths[action], { method: 'POST', body: {} });
    if (result && result.event) {
      state.event = result.event;
      renderDashboard();
    } else {
      await loadEvent(eventId);
    }
    toast(labels[action] + ' for ' + name + '.', 'ok');
  } catch (error) {
    toast(error.message, 'error', 9000);
    try { await loadEvent(eventId); } catch (ignored) { /* keep original error visible */ }
  }
}

async function syncAll() {
  if (!state.event) return;
  const button = $('sync-all-btn');
  setBusy(button, true, 'Syncing…');
  try {
    const result = await api(`/api/events/${state.event.id}/sync`, { method: 'POST', body: {} });
    state.event = result.event;
    renderDashboard();
    toast(result.errors && result.errors.length
      ? `Synced with ${result.errors.length} error(s).`
      : 'All orders synced.', result.errors && result.errors.length ? 'error' : 'ok');
  } catch (error) {
    toast(error.message, 'error', 8000);
  } finally {
    setBusy(button, false);
  }
}

async function captureApproved() {
  if (!state.event) return;
  const button = $('capture-all-btn');
  setBusy(button, true, 'Capturing…');
  try {
    const result = await api(`/api/events/${state.event.id}/capture-approved`, { method: 'POST', body: {} });
    state.event = result.event;
    renderDashboard();
    if (result.captured.length) {
      toast('Captured ' + result.captured.length + ' order(s): ' + result.captured.join(', ') + '.', 'ok');
    } else {
      toast('Nothing to capture yet — approve a checkout first on PayPal.', 'error', 7000);
    }
    if (result.failed && result.failed.length) {
      toast(result.failed[0].error, 'error', 8000);
    }
  } catch (error) {
    toast(error.message, 'error', 8000);
  } finally {
    setBusy(button, false);
  }
}

/* --------------------------------------------------------------- reminders */
async function draftReminder(name) {
  if (!state.event) return;
  try {
    const result = await api(`/api/events/${state.event.id}/reminders`, {
      method: 'POST',
      body: { name },
    });
    $('modal-title').textContent = 'AI-drafted reminder for ' + result.name;
    $('modal-sub').textContent = (result.mode === 'llm'
      ? 'Drafted by ' + ((state.health && state.health.llm_model) || 'the configured LLM')
      : 'Drafted by the built-in offline template') +
      (result.warning ? ' · ' + result.warning : '') +
      ' · nothing is sent automatically.';
    $('modal-text').value = result.message;
    show($('modal'));
    $('modal-text').focus();
    $('modal-text').select();
  } catch (error) {
    toast('Could not draft a reminder: ' + error.message, 'error', 8000);
  }
}

function closeModal() { hide($('modal')); }

async function copyModalText() {
  const text = $('modal-text').value;
  try {
    await navigator.clipboard.writeText(text);
    toast('Reminder copied — paste it into your chat.', 'ok');
  } catch (error) {
    $('modal-text').select();
    document.execCommand('copy');
    toast('Reminder copied.', 'ok');
  }
}

/* ------------------------------------------------------------------ reset */
function resetToStart() {
  state.parsed = null;
  state.plan = null;
  state.split = null;
  state.editor = null;
  state.event = null;
  hide($('editor'));
  hide($('preview'));
  hide($('dashboard'));
  renderWarnings([]);
  window.scrollTo({ top: 0, behavior: 'smooth' });
  $('plan-input').focus();
}

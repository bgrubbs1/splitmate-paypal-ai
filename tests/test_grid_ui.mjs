// Execute the real grid layer and browser code against a fake AG Grid and a strict
// fake HTTP boundary. No browser, dependencies, sockets, or provider endpoints are used.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const gridsSource = fs.readFileSync(new URL('../static/grids.js', import.meta.url), 'utf8');
const appSource = fs.readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');

const money = (cents) => '$' + (Math.abs(cents) / 100).toFixed(2);

const SPLIT = {
  currency: 'USD',
  total_cents: 10000,
  participants: [
    { name: 'Alex', share_cents: 5000, paid_cents: 10000, net_cents: 5000 },
    { name: 'Sam', share_cents: 5000, paid_cents: 0, net_cents: -5000 },
  ],
  expenses: [{ description: 'Dinner', amount_cents: 10000, paid_by: 'Alex', shared_by: ['Alex', 'Sam'], shares: { Alex: 5000, Sam: 5000 } }],
  orders: [{ name: 'Sam', amount_cents: 5000 }],
  totals: { orders_cents: 5000, credits_cents: 5000, expenses_cents: 10000, balanced: true },
  reasoning: [],
  rounding_notes: [],
};

function harness({ withGrid = true, routes = {} } = {}) {
  const elements = new Map();
  const grids = [];
  const calls = [];
  const timers = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        textContent: '', innerHTML: '', className: '', dataset: {}, disabled: false, children: [],
        classes,
        classList: { add(name) { classes.add(name); }, remove(name) { classes.delete(name); } },
        scrollIntoView() {}, focus() {}, select() {}, addEventListener() {},
        replaceChildren(...nodes) { this.children = nodes; },
      });
    }
    return elements.get(id);
  }
  const fakeApi = (options) => ({
    options,
    calls: [],
    setGridOption(name, value) { this.options[name] = value; this.calls.push(name); },
    refreshCells() { this.calls.push('refreshCells'); },
  });
  const agGrid = {
    themeQuartz: { withParams: (params) => ({ params }) },
    createGrid(host, options) {
      const api = fakeApi(options);
      grids.push({ host, api });
      return api;
    },
  };
  const windowObject = { location: { search: '' }, scrollTo() {}, prompt() {}, agGrid: withGrid ? agGrid : undefined };
  const documentObject = {
    getElementById: element,
    addEventListener() {},
    querySelectorAll: () => [],
    createElement: () => ({ className: '', textContent: '', setAttribute() {}, addEventListener() {} }),
  };
  windowObject.document = documentObject;
  const context = vm.createContext({
    document: documentObject,
    window: windowObject,
    navigator: { clipboard: { async writeText() {} } },
    URLSearchParams,
    setTimeout(fn) { timers.push(fn); return timers.length; },
    clearTimeout() {},
    async fetch(path, request) {
      calls.push({ path, body: request.body ? JSON.parse(request.body) : null });
      const route = routes[path];
      assert.ok(route, 'Unexpected HTTP request: ' + path);
      const body = typeof route === 'function' ? await route(request) : route;
      return { ok: true, status: 200, async json() { return body; } };
    },
  });
  vm.runInContext(gridsSource, context, { filename: 'static/grids.js' });
  vm.runInContext(appSource, context, { filename: 'static/app.js' });
  return {
    context, elements, element, grids, calls, timers,
    layer: windowObject.SplitMateGrids,
    run(source) { return vm.runInContext(source, context); },
    setPlan(plan, split) {
      context.fixturePlan = plan;
      context.fixtureSplit = split;
      vm.runInContext('state.plan = fixturePlan; state.split = fixtureSplit; state.editor = editorFromPlan(fixturePlan); attachAllocations(); renderEditor();', context);
    },
  };
}

const PLAN = {
  title: 'Dinner', currency: 'USD', participants: ['Alex', 'Sam'], notes: [],
  expenses: [{ description: 'Dinner', amount_cents: 10000, paid_by: 'Alex', shared_by: ['Alex', 'Sam'], note: 'Both ate' }],
};

const toPlain = (value) => JSON.parse(JSON.stringify(value));

function columns(harnessInstance) {
  return harnessInstance.grids[0].api.options.columnDefs;
}

function column(harnessInstance, id) {
  return columns(harnessInstance).find((item) => item.colId === id);
}

test('amount input accepts money forms and rejects everything else', () => {
  const { layer } = harness();
  for (const [input, expected] of [['12', '12.00'], ['12.5', '12.50'], ['$1,025.00', '1025.00'], [' 7.05 ', '7.05']]) {
    assert.equal(layer.parseAmountInput(input), expected, input);
  }
  for (const input of ['', '0', '0.00', '-5', '12.345', 'abc', '1e5', 'NaN', 'Infinity', '1.2.3', null, undefined]) {
    assert.equal(layer.parseAmountInput(input), null, String(input));
  }
});

test('CSV export neutralises spreadsheet formulas but leaves ordinary text alone', () => {
  const { layer } = harness();
  for (const hostile of ['=HYPERLINK("x")', '+1+1', '-2', '@cmd', '\tx']) {
    assert.equal(layer.csvSafe(hostile), "'" + hostile);
  }
  assert.equal(layer.csvSafe('Sam'), 'Sam');
  assert.equal(layer.csvSafe(null), '');
});

test('pinned rows report the server split and tolerate untrimmed editor names', () => {
  const { layer } = harness();
  const rows = toPlain(layer.pinnedRows(SPLIT, ['Alex', 'Sam ']));
  assert.deepEqual(rows.map((row) => row.pinnedKind), ['share', 'paid', 'net']);
  assert.deepEqual(rows[2].values, { Alex: 5000, 'Sam ': -5000 });
  assert.equal(rows[2].total_cents, 0);
  assert.equal(rows[1].total_cents, 10000);
  assert.deepEqual(toPlain(layer.pinnedRows(null, ['Alex'])), []);
});

test('reconciliation reports balance, missing payers and any imbalance', () => {
  const { layer } = harness();
  const editor = { expenses: [{ paid_by: 'Alex' }] };
  const good = layer.reconciliation(SPLIT, editor);
  assert.equal(good.ready, true);
  assert.equal(good.netSum, 0);
  assert.equal(good.orders, 1);
  assert.equal(layer.reconciliation(SPLIT, { expenses: [{ paid_by: '' }] }).ready, false);
  assert.equal(layer.reconciliation(SPLIT, { expenses: [{ paid_by: '' }] }).missingPayers, 1);
  const lostCent = JSON.parse(JSON.stringify(SPLIT));
  lostCent.expenses[0].shares.Sam = 4999;
  assert.equal(layer.reconciliation(lostCent, editor).ready, false);
  const unbalanced = JSON.parse(JSON.stringify(SPLIT));
  unbalanced.participants[1].net_cents = -4999;
  assert.equal(layer.reconciliation(unbalanced, editor).ready, false);
});

test('ledger rows never carry checkout URLs and report credits as positive amounts', () => {
  const { layer } = harness();
  const event = {
    shares: [
      { name: 'Alex', status: 'credit', net_cents: 5000, amount_cents: 0 },
      { name: 'Sam', status: 'captured', amount_cents: 5000, net_cents: -5000, order_id: 'O1', capture_id: 'C1', capture_status: 'COMPLETED', approve_url: 'https://example.test/secret-token' },
      { name: 'Priya', status: 'settled', amount_cents: 0, net_cents: 0 },
    ],
  };
  const rows = toPlain(layer.ledgerRows(event, (status) => status, () => false));
  assert.deepEqual(rows.map((row) => row.amount_cents), [5000, 5000, 0]);
  assert.deepEqual(rows.map((row) => row.position), ['is owed', 'owes', 'settled']);
  assert.ok(!JSON.stringify(rows).includes('secret-token'));
  assert.ok(!JSON.stringify(rows).includes('approve_url'));
});

test('the matrix replaces the plain editor only when AG Grid is available', () => {
  const withGrid = harness();
  withGrid.setPlan(PLAN, SPLIT);
  assert.equal(withGrid.grids.length, 1);
  assert.ok(withGrid.element('expenses-block').classes.has('hidden'));
  assert.equal(withGrid.element('expenses').innerHTML, '');

  const withoutGrid = harness({ withGrid: false });
  withoutGrid.setPlan(PLAN, SPLIT);
  assert.equal(withoutGrid.grids.length, 0);
  assert.ok(withoutGrid.element('expenses').innerHTML.includes('expense-row'));
});

test('person cells show exact cents, an exclusion marker, and a pending marker', () => {
  const instance = harness();
  instance.setPlan(PLAN, SPLIT);
  const sam = column(instance, 'person:Sam');
  const row = instance.run('state.editor.expenses[0]');
  assert.equal(sam.valueGetter({ data: row }), 5000);
  assert.equal(sam.valueFormatter({ data: row, value: 5000 }), '$50.00');
  instance.run("state.editor.expenses[0].shares = null");
  assert.equal(sam.valueGetter({ data: row }), undefined);
  assert.equal(sam.valueFormatter({ data: row, value: undefined }), '…');
  instance.run("state.editor.expenses[0].shared_by = ['Alex']");
  assert.equal(sam.valueGetter({ data: instance.run('state.editor.expenses[0]') }), null);
  assert.equal(sam.valueFormatter({ data: instance.run('state.editor.expenses[0]'), value: null }), 'not in');
});

test('an invalid amount edit is rejected without touching the plan', () => {
  const instance = harness();
  instance.setPlan(PLAN, SPLIT);
  const amount = column(instance, 'amount');
  const data = instance.run('state.editor.expenses[0]');
  assert.equal(amount.valueSetter({ data, newValue: '12.345' }), false);
  assert.equal(data.amount_text, '100.00');
  assert.match(instance.element('toast').textContent, /positive amount/);
  assert.equal(amount.valueSetter({ data, newValue: '100.00' }), false, 'no-op edit');
  assert.equal(amount.valueSetter({ data, newValue: '42.5' }), true);
  assert.equal(data.amount_text, '42.50');
});

test('the last sharer of an expense cannot be removed from the matrix', () => {
  const instance = harness({ routes: { '/api/preview': { plan: PLAN, split: SPLIT, warnings: [] } } });
  instance.setPlan(PLAN, SPLIT);
  const context = instance.run('gridContext()');
  const row = instance.run('state.editor.expenses[0]');
  context.toggleShare(row, 'Sam');
  assert.deepEqual(toPlain(row.shared_by), ['Alex']);
  context.toggleShare(row, 'Alex');
  assert.deepEqual(toPlain(row.shared_by), ['Alex']);
  assert.match(instance.element('toast').textContent, /At least one participant/);
});

test('changing who shares an expense retires the AI note from the plan but keeps it as history', () => {
  const instance = harness({ routes: { '/api/preview': { plan: PLAN, split: SPLIT, warnings: [] } } });
  instance.setPlan(PLAN, SPLIT);
  const item = column(instance, 'description');
  const row = instance.run('state.editor.expenses[0]');
  assert.equal(instance.run('planFromEditor().expenses[0].note'), 'Both ate');
  assert.equal(item.tooltipValueGetter({ data: row }), 'AI note: Both ate');
  assert.equal(item.cellClassRules['cell-noted']({ data: row }), true);

  instance.run('gridContext()').toggleShare(row, 'Sam');
  assert.equal(instance.run('planFromEditor().expenses[0].note'), '');
  assert.equal(item.tooltipValueGetter({ data: row }), 'You changed who shares this. The AI noted: Both ate');
  assert.equal(item.cellClassRules['cell-noted']({ data: row }), false);
});

test('a refused toggle leaves the AI note in force', () => {
  const instance = harness({ routes: { '/api/preview': { plan: PLAN, split: SPLIT, warnings: [] } } });
  const solo = { ...PLAN, expenses: [{ ...PLAN.expenses[0], shared_by: ['Alex'] }] };
  instance.setPlan(solo, SPLIT);
  instance.run('gridContext()').toggleShare(instance.run('state.editor.expenses[0]'), 'Alex');
  assert.equal(instance.run('planFromEditor().expenses[0].note'), 'Both ate');
});

test('rows misaligned with the server split show no money rather than wrong money', () => {
  const instance = harness();
  const twoRows = { ...PLAN, expenses: [PLAN.expenses[0], { ...PLAN.expenses[0], description: 'Taxi' }] };
  instance.setPlan(twoRows, SPLIT);
  const shares = instance.run('state.editor.expenses.map((row) => row.shares)');
  assert.deepEqual(toPlain(shares), [null, null]);
});

test('a slow preview response cannot overwrite a newer edit', async () => {
  let releaseFirst;
  const gate = new Promise((resolve) => { releaseFirst = resolve; });
  const newer = { ...SPLIT, total_cents: 20000, expenses: [{ ...SPLIT.expenses[0], amount_cents: 20000 }] };
  let count = 0;
  const instance = harness({
    routes: {
      '/api/preview': async () => {
        count += 1;
        if (count === 1) {
          await gate;
          return { plan: PLAN, split: SPLIT, warnings: [] };
        }
        return { plan: PLAN, split: newer, warnings: [] };
      },
    },
  });
  instance.setPlan(PLAN, SPLIT);
  const first = instance.run('livePreview()');
  const second = instance.run('livePreview()');
  await second;
  releaseFirst();
  await first;
  assert.equal(instance.run('state.split.total_cents'), 20000);
});

test('an incomplete row holds the recalculation and marks the matrix stale', async () => {
  const instance = harness();
  instance.setPlan(PLAN, SPLIT);
  instance.run("state.editor.expenses[0].amount_text = ''");
  await instance.run('livePreview()');
  assert.equal(instance.calls.length, 0, 'no request with a blank amount');
  assert.equal(instance.run('matrixStale'), true);
  const pills = instance.element('grid-recon').children;
  assert.match(pills[0].textContent, /Not recalculated/);
});

test('hostile names reach the reconciliation strip only as text and stay out of class names', () => {
  const instance = harness();
  const evil = '<img src=x onerror=alert(1)>';
  const plan = { ...PLAN, participants: ['Alex', evil], expenses: [{ ...PLAN.expenses[0], shared_by: ['Alex', evil] }] };
  instance.setPlan(plan, SPLIT);
  assert.equal(column(instance, 'person:' + evil).headerName, evil);
  const event = {
    id: 'evt_x', title: 'x', currency: 'USD', created_at: 'now', paypal_mode: 'mock', totals: {},
    shares: [{ name: evil, status: 'order_created" onmouseover="x', amount_cents: 1, net_cents: -1 }],
  };
  instance.context.fixture = event;
  instance.run('state.event = fixture; renderDashboard()');
  const ledger = instance.grids[1];
  const status = ledger.api.options.columnDefs.find((item) => item.colId === 'status');
  const pill = status.cellRenderer({ data: ledger.api.options.rowData[0], value: 'x' });
  assert.ok(!/["\s]/.test(pill.className.replace('status-pill status-', '')), pill.className);
});

test('the ledger only reports: it exposes no money action or checkout link', () => {
  const instance = harness();
  instance.setPlan(PLAN, SPLIT);
  instance.context.fixture = {
    id: 'evt_x', title: 'x', currency: 'USD', created_at: 'now', paypal_mode: 'mock',
    totals: { captured_cents: 5000, captured_count: 1, order_count: 1 },
    shares: [{ name: 'Sam', status: 'captured', amount_cents: 5000, net_cents: -5000, order_id: 'O1', approve_url: 'https://example.test/x' }],
  };
  instance.run('state.event = fixture; renderDashboard()');
  const ledger = instance.grids[1].api.options;
  const ids = ledger.columnDefs.map((item) => item.colId);
  assert.deepEqual(toPlain(ids), ['name', 'position', 'amount', 'status', 'order_id', 'capture_id', 'capture_status', 'last_error']);
  assert.equal(ledger.pinnedBottomRowData[0].status, '1 of 1 orders captured');
  assert.ok(!JSON.stringify(ledger.rowData).includes('example.test'));
  assert.ok(instance.element('share-rows').innerHTML.includes('share-row'), 'money actions stay on the share rows');
});

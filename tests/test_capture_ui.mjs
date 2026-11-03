// Execute the real browser code with a minimal DOM and a strict fake HTTP boundary.
// No browser, dependencies, sockets, or provider endpoints are used by this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const appSource = fs.readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');

function share(overrides = {}) {
  return {
    name: 'Sam', status: 'approved', amount_cents: 5000, net_cents: -5000,
    order_id: 'FAKEORDER1', approve_url: 'https://example.test/checkout',
    capture_id: null, capture_status: null, capture_attempted: false,
    ...overrides,
  };
}

function eventWith(row = share(), totals = {}) {
  return {
    id: 'evt_test', title: 'Synthetic dinner', currency: 'USD',
    created_at: '2026-11-02T00:00:00Z', paypal_mode: 'sandbox',
    plan: {}, split: {}, shares: [row], orders: [row],
    totals: {
      expenses_cents: 20000, collection_target_cents: 10000,
      collected_cents: 10000, captured_cents: 0,
      credits_cents: 10000, captured_count: 0, order_count: 2, ...totals,
    },
  };
}

function harness(routes = {}) {
  const elements = new Map();
  const calls = [];
  const copied = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', innerHTML: '', className: '', dataset: {}, disabled: false,
      classList: { add() {}, remove() {} },
      scrollIntoView() {}, focus() {}, select() {}, addEventListener() {},
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: { getElementById: element, addEventListener() {} },
    window: { location: { search: '' }, scrollTo() {}, prompt() {} },
    navigator: { clipboard: { async writeText(text) { copied.push(text); } } },
    URLSearchParams, setTimeout() { return 1; }, clearTimeout() {},
    async fetch(path, request) {
      calls.push({ path, method: request.method });
      const route = routes[path];
      assert.ok(route, 'Unexpected HTTP request: ' + path);
      const body = typeof route === 'function' ? route(request) : route;
      return { ok: true, status: 200, async json() { return body; } };
    },
  });
  vm.runInContext(appSource, context, { filename: 'static/app.js' });
  return {
    elements, calls, copied, context, element,
    run(source) { return vm.runInContext(source, context); },
    render(event) {
      context.fixture = event;
      vm.runInContext('state.event = fixture; renderDashboard()', context);
      return element('share-rows').innerHTML;
    },
    async click(action, name = 'Sam') {
      const button = { dataset: { action, name }, textContent: action, disabled: false };
      context.input = { target: { closest() { return button; } } };
      await vm.runInContext('onShareRowClick(input)', context);
    },
  };
}

test('dashboard uses collection target, not captured funds, as the progress denominator', () => {
  const h = harness();
  const html = h.render(eventWith(share(), {
    collection_target_cents: 10000, collected_cents: undefined, captured_cents: 5000,
  }));
  assert.match(h.element('dashboard-totals').innerHTML, /Collecting[\s\S]*?\$100\.00/);
  assert.match(html, /width:50%/);
});

test('dashboard accepts the legacy collected_cents collection target', () => {
  const h = harness();
  const html = h.render(eventWith(share(), {
    collection_target_cents: undefined, collected_cents: 10000, captured_cents: 5000,
  }));
  assert.match(h.element('dashboard-totals').innerHTML, /Collecting[\s\S]*?\$100\.00/);
  assert.match(html, /width:50%/);
});

for (const row of [
  share({ status: 'capture_pending', capture_status: 'PENDING', capture_attempted: true }),
  share({ status: 'capture_failed', capture_status: 'DECLINED', capture_id: 'FAKECAP1' }),
  share({ status: 'capture_unknown' }),
  share({ status: 'capture_pending' }),
  share({ status: 'capture_failed' }),
  share({ status: 'approved', capture_attempted: true }),
  share({ status: 'approved', reconciliation_required: true }),
  share({ status: 'failed', capture_id: 'FAKECAP1' }),
]) {
  test(`held outcome ${JSON.stringify(row)} allows reconciliation, not another money action`, () => {
    const h = harness();
    const html = h.render(eventWith(row));
    assert.match(html, /data-action="sync"/);
    assert.doesNotMatch(html, /Open checkout|data-action="(?:capture|copy|retry|remind)"/);
    assert.doesNotMatch(html, /ready to capture/);
  });
}

test('a failed order without capture evidence remains explicitly retryable', () => {
  const h = harness();
  const html = h.render(eventWith(share({ status: 'failed', order_id: null, approve_url: null })));
  assert.match(html, /data-action="retry"/);
});

for (const status of ['capture_pending', 'capture_failed', 'capture_unknown']) {
  test(`single capture returning ${status} never announces successful capture`, async () => {
    const resultShare = share({ status, capture_attempted: true });
    const result = { event: eventWith(resultShare), share: resultShare };
    const h = harness({ '/api/events/evt_test/shares/Sam/capture': result });
    h.render(eventWith());
    await h.click('capture');
    assert.doesNotMatch(h.element('toast').textContent, /^Captured\b|payment captured/i);
    assert.match(h.element('toast').textContent, /sync|reconcil|review/i);
  });
}

test('completed single capture still announces success', async () => {
  const resultShare = share({ status: 'captured', capture_status: 'COMPLETED', capture_id: 'FAKECAP1' });
  const h = harness({ '/api/events/evt_test/shares/Sam/capture': {
    event: eventWith(resultShare), share: resultShare,
  } });
  h.render(eventWith());
  await h.click('capture');
  assert.match(h.element('toast').textContent, /Captured for Sam/);
});

test('a stale money-action button cannot recapture a held share or copy its checkout link', async () => {
  const h = harness();
  h.render(eventWith(share({ capture_attempted: true, status: 'capture_unknown' })));
  for (const action of ['capture', 'retry', 'copy']) await h.click(action);
  assert.equal(h.calls.length, 0);
  assert.equal(h.copied.length, 0);
});

test('repeated PayPal returns reconcile a held outcome without issuing capture again', async () => {
  const row = share({ status: 'capture_pending', capture_status: 'PENDING', capture_attempted: true });
  const event = eventWith(row);
  const h = harness({
    '/api/orders/FAKEORDER1': { event, share: row },
    '/api/events/evt_test/shares/Sam/sync': { event, share: row },
    '/api/events/evt_test': { event },
  });
  await h.run('resumePayPalReturn("FAKEORDER1")');
  await h.run('resumePayPalReturn("FAKEORDER1")');
  assert.equal(h.calls.filter(call => call.path.endsWith('/capture')).length, 0);
  assert.equal(h.calls.filter(call => call.path.endsWith('/sync')).length, 2);
  assert.doesNotMatch(h.element('toast').textContent, /payment captured/i);
});

test('a first PayPal return that yields pending does not claim payment captured', async () => {
  const fresh = eventWith();
  const pending = share({ status: 'capture_pending', capture_status: 'PENDING', capture_attempted: true });
  const result = { event: eventWith(pending), share: pending };
  const h = harness({
    '/api/orders/FAKEORDER1': { event: fresh, share: fresh.shares[0] },
    '/api/events/evt_test/shares/Sam/capture': result,
    '/api/events/evt_test': { event: result.event },
  });
  await h.run('resumePayPalReturn("FAKEORDER1")');
  assert.equal(h.calls.filter(call => call.path.endsWith('/capture')).length, 1);
  assert.doesNotMatch(h.element('toast').textContent, /payment captured/i);
  assert.match(h.element('toast').textContent, /sync|reconcil|review/i);
});

test('bulk unresolved outcomes prompt reconciliation, never renewed approval', async () => {
  const row = share({ status: 'capture_unknown', capture_attempted: true });
  const h = harness({ '/api/events/evt_test/capture-approved': {
    event: eventWith(row), captured: [], failed: [],
    unresolved: [{ name: 'Sam', status: 'capture_unknown', error: 'Outcome is unknown.' }],
  } });
  h.render(eventWith());
  await h.run('captureApproved()');
  assert.match(h.element('toast').textContent, /sync|reconcil|review/i);
  assert.doesNotMatch(h.element('toast').textContent, /approve a checkout|Captured 1/i);
});

test('mixed bulk outcomes keep completed success and unresolved warnings distinct', async () => {
  const row = share({ status: 'capture_unknown', capture_attempted: true });
  const h = harness({ '/api/events/evt_test/capture-approved': {
    event: eventWith(row), captured: ['Alex'],
    failed: [{ name: 'Jordan', error: 'Approve a checkout first.' }],
    unresolved: [{ name: 'Sam', status: 'capture_unknown', error: 'Outcome is unknown.' }],
  } });
  h.render(eventWith());
  await h.run('captureApproved()');
  assert.match(h.element('toast').textContent, /Captured 1 order/);
  assert.match(h.element('toast').textContent, /sync|reconcil|review/i);
  assert.doesNotMatch(h.element('toast').textContent, /approve a checkout/i);
});

test('a fresh completed PayPal return still captures once and reports success', async () => {
  const fresh = eventWith();
  const completed = share({ status: 'captured', capture_status: 'COMPLETED', capture_id: 'FAKECAP1' });
  const result = { event: eventWith(completed), share: completed };
  const h = harness({
    '/api/orders/FAKEORDER1': { event: fresh, share: fresh.shares[0] },
    '/api/events/evt_test/shares/Sam/capture': result,
    '/api/events/evt_test': { event: result.event },
  });
  await h.run('resumePayPalReturn("FAKEORDER1")');
  assert.equal(h.calls.filter(call => call.path.endsWith('/capture')).length, 1);
  assert.match(h.element('toast').textContent, /Captured for Sam/);
});

test('cancel URL with a token only reconciles and never auto-captures', async () => {
  const row = share({ status: 'approved' });
  const event = eventWith(row);
  const h = harness({
    '/api/orders/FAKEORDER1': { event, share: row },
    '/api/events/evt_test/shares/Sam/sync': { event, share: row },
    '/api/events/evt_test': { event },
  });
  h.context.window.location = { pathname: '/cancel', search: '?token=FAKEORDER1' };
  await h.run('handleUrlParams()');
  assert.equal(h.calls.filter(call => call.path.endsWith('/capture')).length, 0);
  assert.equal(h.calls.filter(call => call.path.endsWith('/sync')).length, 1);
  assert.match(h.element('toast').textContent, /cancelled/i);
  assert.doesNotMatch(h.element('toast').textContent, /payment captured|nothing captured/i);
});

test('completed shares retain a thank-you reminder without another payment action', () => {
  const row = share({ status: 'captured', capture_status: 'COMPLETED', capture_id: 'FAKECAP1', capture_attempted: true });
  const h = harness();
  const html = h.render(eventWith(row));
  assert.match(html, /data-action="remind"/);
  assert.doesNotMatch(html, /Open checkout|data-action="(?:capture|copy|retry)"/);
});

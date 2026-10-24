const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const { webcrypto } = require('node:crypto');

function setup(fetchImpl = async () => ({ ok: true, status: 200 }), blocked = false) {
  const storage = () => { const map = new Map(); return {
    getItem(k) { if (blocked) throw Error('blocked'); return map.get(k) ?? null; },
    setItem(k, v) { if (blocked) throw Error('blocked'); map.set(k, v); }
  }; };
  const listeners = new Map();
  const context = { crypto: webcrypto, TextEncoder, AbortSignal, Date, Math, Set, JSON,
    localStorage: storage(), sessionStorage: storage(), location: { pathname: '/demo', search: '?private=true' },
    history: { pushState() {}, replaceState() {} }, document: { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} },
    addEventListener(k, fn) { listeners.set(k, fn); }, removeEventListener(k) { listeners.delete(k); },
    setInterval() { return 1; }, clearInterval() {}, fetch: fetchImpl };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('analytics_app/static/sdk.js', 'utf8'), context);
  return { context, listeners, make: options => new context.GatherAnalytics({ autoPageViews: false, ...options }) };
}

test('retry keeps stable event IDs and only removes acknowledged events', async () => {
  const requests = [];
  const { make } = setup(async (_, request) => { requests.push(JSON.parse(request.body)); return { ok: requests.length > 1, status: requests.length > 1 ? 200 : 503 }; });
  const sdk = make();
  const id = sdk.track('cart_added', { item_id: 'bowl' });
  await sdk.flush();
  assert.equal(sdk.queue.length, 1);
  sdk.nextAttempt = 0;
  await sdk.flush();
  assert.equal(sdk.queue.length, 0);
  assert.equal(requests[0].events[0].event_id, id);
  assert.deepEqual(requests[0], requests[1]);
});

test('persisted identity and queue survive reinitialization; paths exclude query data', async () => {
  const { make } = setup(async () => { throw Error('offline'); });
  const first = make(); first.track('page_view'); await first.flush(); first.destroy();
  const second = make(); await second.inflight;
  assert.equal(first.anonymousId, second.anonymousId);
  assert.equal(second.queue[0].path, '/demo');
  assert.equal(second.queue[0].event_id, first.queue[0].event_id);
});

test('concurrent flushes do not overlap or delete new events', async () => {
  let finish, calls = 0;
  const { make } = setup(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const sdk = make(); sdk.track('first_event');
  const firstFlush = sdk.flush(); sdk.track('second_event'); await sdk.flush();
  assert.equal(calls, 1); finish({ ok: true }); await firstFlush;
  assert.equal(sdk.queue.length, 1); assert.equal(sdk.queue[0].name, 'second_event');
});

test('storage denial and opt-out leave application usable', () => {
  const { make } = setup(undefined, true); const sdk = make();
  assert.ok(sdk.track('page_view')); assert.equal(sdk.storageAvailable, false);
  sdk.setEnabled(false); assert.equal(sdk.queue.length, 0); assert.equal(sdk.track('page_view'), null);
});

test('SDK validates properties and drops permanent failures without endless retries', async () => {
  const { make } = setup(async () => ({ ok: false, status: 422 })); const sdk = make();
  assert.throws(() => sdk.track('bad name'));
  assert.throws(() => sdk.track('custom', { nested: { email: 'private' } }));
  sdk.track('custom'); await sdk.flush(); assert.equal(sdk.dropped, 1); assert.equal(sdk.queue.length, 0);
});

test('auto page tracking covers history changes and destroy restores hooks', async () => {
  const { context, make } = setup(async () => { throw Error('offline'); });
  const original = context.history.pushState; const sdk = make({ autoPageViews: true });
  await sdk.inflight;
  context.history.pushState({}, '', '/demo');
  assert.equal(sdk.queue.length, 2);
  sdk.destroy(); assert.equal(context.history.pushState, original);
});

test('unload requests use keepalive and bounded payloads', async () => {
  let captured;
  const { make } = setup(async (_, options) => { captured = options; return { ok: true }; });
  const sdk = make();
  for (let i = 0; i < 5; i++) sdk.track('custom', { data: 'a'.repeat(7000) });
  await sdk.flush(true);
  assert.equal(captured.keepalive, true);
  assert.ok(Buffer.byteLength(captured.body) <= 48000);
});

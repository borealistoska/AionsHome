const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(`${__dirname}/static/chat.js`, 'utf8');

function page(api = async () => [], storage = new Map()) {
  const context = { systemLogs: [], Date, Math, console, api,
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    $: () => null, document: { getElementById: () => null } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('// ── 系统日志 ──'),
    source.indexOf('// ── 星标消息 ──')), context);
  return context;
}

test('keeps the latest 30 entries and merges the same call from two channels', () => {
  const p = page();
  for (let i = 0; i < 35; i++) p.addSystemLog({ log_id: `call${i}`, model: 'test', started_at: i + 1 });
  assert.equal(p.systemLogs.length, 30);
  assert.equal(p.systemLogs[0].log_id, 'call34');
  p.addSystemLog({ log_id: 'call34', msg_id: 'message1', recall_topic: 'test' });
  assert.equal(p.systemLogs.length, 30);
  assert.equal(p.systemLogs[0].recall_topic, 'test');
});

test('fresh page restores saved logs and keeps a call completed during loading', async () => {
  let respond;
  const p = page(() => new Promise(resolve => { respond = resolve; }));
  const loading = p.loadSystemLogs();
  p.addSystemLog({ log_id: 'wake', started_at: 2, status: 'success' });
  respond([{ log_id: 'wake', started_at: 2, status: 'running' },
    { log_id: 'supervision', started_at: 1, status: 'success' }]);
  await loading;
  assert.equal(p.systemLogs.length, 2);
  assert.equal(p.systemLogs[0].status, 'success');
  assert.equal(p.systemLogs[1].log_id, 'supervision');
});

test('clearing persists on the server and invalidates an older snapshot', async () => {
  let respond;
  const calls = [];
  const p = page((method, url) => {
    calls.push([method, url]);
    if (method === 'GET') return new Promise(resolve => { respond = resolve; });
    return Promise.resolve({ ok: true });
  });
  const loading = p.loadSystemLogs();
  await p.clearSystemLog();
  respond([{ log_id: 'old', started_at: 1 }]);
  await loading;
  assert.equal(p.systemLogs.length, 0);
  assert.deepEqual(calls[1], ['DELETE', '/api/system-logs']);
});

test('network errors survive a page refresh even though they cannot reach the server', async () => {
  const storage = new Map();
  const first = page(async () => [], storage);
  first.addErrorToSystemLog('connection lost', 'test');
  const refreshed = page(async () => [], storage);
  await refreshed.loadSystemLogs();
  assert.equal(refreshed.systemLogs.length, 1);
  assert.equal(refreshed.systemLogs[0].error_text, 'connection lost');
  await refreshed.clearSystemLog();
  const cleared = page(async () => [], storage);
  await cleared.loadSystemLogs();
  assert.equal(cleared.systemLogs.length, 0);
});

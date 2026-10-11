const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'static/theater.html'), 'utf8');
const studio = fs.readFileSync(path.join(__dirname, 'static/theater-studio.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const pending = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

function initHarness(api) {
  const elements = new Map();
  const calls = [];
  const context = vm.createContext({
    api, models: [], personas: [], conversations: [], currentConvId: null,
    ttsEnabled: false, ttsVoice: '', localStorage: { getItem: () => null },
    $: id => { if (!elements.has(id)) elements.set(id, { value: '', innerHTML: '' }); return elements.get(id); },
    loadTTSVoices: () => api('GET', '/api/tts/voices'),
    renderPersonaList() {}, renderConvList() {}, updateTheaterSelectionSummary() {},
    renderMessages() {}, showToast() {}, escHtml: text => text,
    async selectConv(id) { context.currentConvId = id; calls.push(id); },
    connectWS() { calls.push('ws'); },
  });
  vm.runInContext(html.slice(html.indexOf('async function init()'), html.indexOf('/* ── TTS 音色加载')), context);
  return { context, elements, calls };
}

test('stories open while model, voice and persona requests are delayed', async () => {
  for (const slowModels of [true, false]) {
    const never = new Promise(() => {});
    const f = initHarness(async (_, url) => {
      if (url === '/api/theater/conversations') return [{ id: 'story', model: 'saved-model' }];
      if (url === '/api/models' && !slowModels) return [{ key: 'saved-model' }];
      return never;
    });
    f.context.init();
    await tick();
    assert.equal(f.context.currentConvId, 'story', 'reading must not wait for settings or voices');
    assert.ok(f.calls.includes('ws'));
  }
});

test('late models restore the story model and preserve a newer user selection', async () => {
  for (const chosen of ['', 'new-model']) {
    const modelRequest = pending();
    const f = initHarness(async (_, url) => {
      if (url === '/api/models') return modelRequest.promise;
      if (url === '/api/theater/conversations') return [{ id: 'story', model: 'saved-model' }];
      return [];
    });
    f.context.init();
    await tick();
    f.context.$('modelSelect').value = chosen;
    modelRequest.resolve([{ key: 'saved-model' }, { key: 'new-model' }]);
    await tick();
    assert.equal(f.context.$('modelSelect').value, chosen || 'saved-model');
  }
});

test('studio opens stories before library metadata and names finish loading', async () => {
  const library = pending(), names = pending();
  let opened = false, rerenders = 0;
  const context = vm.createContext({
    mount() {}, action: fn => fn(), request: () => library.promise, api: () => names.promise,
    modes: new Map(), discussionNames: {}, currentConvId: null,
    async init() { opened = true; context.currentConvId = 'story'; },
    renderConvList() {}, novel: () => true, renderNovel() { rerenders++; },
    $() { throw Error('must not show the empty library for an existing story'); },
  });
  vm.runInContext(studio.slice(studio.indexOf('  async function boot()'), studio.lastIndexOf('  return {')), context);
  context.boot();
  await tick();
  assert.ok(opened, 'library metadata must not hold up story loading');
  library.resolve([{ id: 'story', mode: 'novel' }]);
  names.resolve({ user_name: 'Reader', ai_name: 'Writer' });
  await tick();
  assert.equal(context.modes.get('story'), 'novel');
  assert.equal(context.discussionNames.user_name, 'Reader');
  assert.equal(rerenders, 1);
});

test('the embedded page becomes visible on DOM readiness without waiting for images', () => {
  const handlers = new Map();
  const frame = {};
  let revealed = false;
  const context = {
    document: { addEventListener: (event, handler) => handlers.set(event, handler) },
    window: { frameElement: frame, parent: { AionSubPageNavigation: { ready(value) { revealed = value === frame; } } } },
  };
  vm.runInNewContext(html.slice(html.indexOf('/* ── 启动 ── */'), html.indexOf('</script>', html.indexOf('/* ── 启动 ── */'))), context);
  handlers.get('DOMContentLoaded')?.();
  assert.ok(revealed);
});

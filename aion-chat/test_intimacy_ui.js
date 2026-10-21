const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const privateSource = fs.readFileSync(`${__dirname}/static/chat.js`, 'utf8');
const privateHtml = fs.readFileSync(`${__dirname}/static/chat.html`, 'utf8');
const roomSource = fs.readFileSync(`${__dirname}/static/chatroom.js`, 'utf8');
const roomHtml = fs.readFileSync(`${__dirname}/static/chatroom.html`, 'utf8');

function elements(ids) {
  return Object.fromEntries(ids.map(id => {
    const classes = new Set();
    return [id, {
      id, checked: false, textContent: '', disabled: false, style: { display: '' },
      classList: {
        add: value => classes.add(value),
        remove: value => classes.delete(value),
        contains: value => classes.has(value),
      },
    }];
  }));
}

function functionSource(source, name) {
  const asyncStart = source.indexOf(`async function ${name}(`);
  const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} must exist`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') depth -= 1;
    if (depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`unterminated function ${name}`);
}

test('preview renderer groups readable fields and omits empty extras', () => {
  const card = {
    mode: 'group', category: '核心玩法', subtypes: ['变化一', '变化二'],
    location: '卧室', position: '姿势一', transition_position: '姿势二',
    exposure: '', restraint: '', props: ['道具一'], clothing_play: [],
    psychology: ['心理一'], group_dynamic: ['结构一'], ejaculation_play: '',
    final_finish: { a: '设定 A', b: '设定 B', relation: '先后' },
  };
  for (const source of [privateSource, roomSource]) {
    const container = { textContent: '' };
    const context = {};
    vm.runInNewContext(functionSource(source, 'renderIntimacyPreviewCard'), context);
    context.renderIntimacyPreviewCard(container, card);
    assert.match(container.textContent, /核心\n主类：核心玩法\n子玩法：变化一、变化二/);
    assert.match(container.textContent, /场景与姿势\n地点：卧室\n主位：姿势一\n换位：姿势二/);
    assert.match(container.textContent, /心理与辅助\n道具：道具一\n心理：心理一\n三人结构：结构一/);
    assert.match(container.textContent, /最终设定\n男性位 A：设定 A\n男性位 B：设定 B\n关系：先后/);
    assert.doesNotMatch(container.textContent, /束缚/);
  }
});

test('private chat intimacy controls show off, armed and read states', () => {
  assert.match(privateHtml, /id="intimacySkillToggle"/);
  assert.match(privateHtml, /id="intimacyStartBtn"/);
  assert.match(privateHtml, /id="intimacyPreviewBtn"/);
  assert.match(privateHtml, /id="intimacyPreviewModal"/);
  const nodes = elements(['intimacySkillToggle', 'intimacySkillStatus', 'intimacyStartBtn', 'intimacyPreviewBtn']);
  const context = { $: id => nodes[id] };
  const start = privateSource.indexOf('function applyPrivateIntimacyState(');
  const end = privateSource.indexOf('async function syncPrivateIntimacy(', start);
  assert.ok(start >= 0 && end > start, 'private intimacy state renderer must exist');
  vm.runInNewContext(privateSource.slice(start, end), context);

  context.applyPrivateIntimacyState({ enabled: false, state: 'off' });
  assert.equal(nodes.intimacySkillStatus.textContent, '关闭');
  assert.equal(nodes.intimacyStartBtn.style.display, 'none');

  context.applyPrivateIntimacyState({ enabled: true, state: 'armed' });
  assert.equal(nodes.intimacySkillStatus.textContent, '待机');
  assert.equal(nodes.intimacyStartBtn.textContent, '开始本轮');
  assert.equal(nodes.intimacyPreviewBtn.disabled, true);

  context.applyPrivateIntimacyState({ enabled: true, state: 'active', seen_actors: ['aion'] });
  assert.equal(nodes.intimacySkillStatus.textContent, '进行中 · 已读取');
  assert.equal(nodes.intimacyStartBtn.textContent, '换一套玩法');
  assert.equal(nodes.intimacyPreviewBtn.disabled, false);
});

test('chatroom intimacy controls report each configured actor reading the shared card', () => {
  assert.match(roomHtml, /id="setIntimacySkillEnabled"/);
  assert.match(roomHtml, /id="crIntimacyStartBtn"/);
  assert.match(roomHtml, /id="crIntimacyPreviewBtn"/);
  assert.match(roomHtml, /id="crIntimacyPreviewModal"/);
  const nodes = elements(['setIntimacySkillEnabled', 'crIntimacySkillStatus', 'crIntimacyStartBtn', 'crIntimacyPreviewBtn']);
  const context = {
    document: { getElementById: id => nodes[id] },
    currentRoom: { id: 'room-1', type: 'group' },
    crAiName: 'Aion',
    crConnorName: 'Connor',
  };
  const start = roomSource.indexOf('function crApplyIntimacyState(');
  const end = roomSource.indexOf('async function crSyncIntimacy(', start);
  assert.ok(start >= 0 && end > start, 'chatroom intimacy state renderer must exist');
  vm.runInNewContext(roomSource.slice(start, end), context);

  context.crApplyIntimacyState({ enabled: true, state: 'active', seen_actors: ['aion'] });
  assert.equal(nodes.crIntimacySkillStatus.textContent, '进行中 · Aion已读取 · Connor等待');
  context.crApplyIntimacyState({ enabled: true, state: 'active', seen_actors: ['aion', 'connor'] });
  assert.equal(nodes.crIntimacySkillStatus.textContent, '进行中 · 双人已读取');
  assert.equal(nodes.crIntimacyStartBtn.textContent, '换一套玩法');
  assert.equal(nodes.crIntimacyPreviewBtn.disabled, false);
});

test('a server-synced private toggle can start a round without local storage', async () => {
  const calls = [];
  const nodes = elements(['intimacySkillToggle', 'intimacyStartBtn']);
  nodes.intimacySkillToggle.checked = true;
  const context = {
    currentConvId: 'conv-1',
    $: id => nodes[id],
    privateIntimacyEnabled: () => false,
    api: async (...args) => { calls.push(args); return { enabled: true, state: 'active' }; },
    applyPrivateIntimacyState() {},
    console,
    encodeURIComponent,
  };
  vm.runInNewContext(functionSource(privateSource, 'startIntimacyRound'), context);
  await context.startIntimacyRound();
  assert.equal(calls.length, 1);
});

test('a server-synced room toggle can start a round without local storage', async () => {
  const calls = [];
  const nodes = elements(['setIntimacySkillEnabled', 'crIntimacyStartBtn']);
  nodes.setIntimacySkillEnabled.checked = true;
  const context = {
    currentRoom: { id: 'room-1', type: 'group' },
    document: { getElementById: id => nodes[id] },
    crIntimacyEnabled: () => false,
    api: async (...args) => { calls.push(args); return { enabled: true, state: 'active' }; },
    crApplyIntimacyState() {},
    console,
    encodeURIComponent,
    JSON,
  };
  vm.runInNewContext(functionSource(roomSource, 'crStartIntimacyRound'), context);
  await context.crStartIntimacyRound();
  assert.equal(calls.length, 1);
});

test('private preview fetches the active card and reroll refreshes it', async () => {
  const calls = [];
  const rendered = [];
  const nodes = elements([
    'intimacyPreviewModal', 'intimacyPreviewContent', 'intimacyPreviewHint',
    'intimacyPreviewRerollBtn',
  ]);
  const context = {
    currentConvId: 'conv-1',
    $: id => nodes[id],
    api: async (...args) => {
      calls.push(args);
      return { state: 'active', card: { category: '测试玩法' }, reroll_queued: false };
    },
    renderIntimacyPreviewCard: (container, card) => rendered.push([container.id, card.category]),
    applyPrivateIntimacyState() {},
    console,
    encodeURIComponent,
  };
  vm.runInNewContext(functionSource(privateSource, 'openIntimacyPreview'), context);
  vm.runInNewContext(functionSource(privateSource, 'rerollIntimacyPreview'), context);

  await context.openIntimacyPreview();
  assert.deepEqual(calls[0], ['GET', '/api/conversations/conv-1/intimacy/preview']);
  assert.equal(nodes.intimacyPreviewModal.classList.contains('show'), true);
  assert.deepEqual(rendered[0], ['intimacyPreviewContent', '测试玩法']);

  await context.rerollIntimacyPreview();
  assert.equal(calls[1][0], 'POST');
  assert.equal(calls[1][1], '/api/conversations/conv-1/intimacy/start');
  assert.equal(calls[2][1], '/api/conversations/conv-1/intimacy/preview');
});

test('room preview fetches the active card and reroll refreshes it', async () => {
  const calls = [];
  const rendered = [];
  const nodes = elements([
    'crIntimacyPreviewModal', 'crIntimacyPreviewContent', 'crIntimacyPreviewHint',
    'crIntimacyPreviewRerollBtn',
  ]);
  const context = {
    currentRoom: { id: 'room-1', type: 'group' },
    document: { getElementById: id => nodes[id] },
    api: async (...args) => {
      calls.push(args);
      return { state: 'active', card: { category: '测试玩法' }, reroll_queued: false };
    },
    renderIntimacyPreviewCard: (container, card) => rendered.push([container.id, card.category]),
    crApplyIntimacyState() {},
    console,
    encodeURIComponent,
    JSON,
  };
  vm.runInNewContext(functionSource(roomSource, 'crOpenIntimacyPreview'), context);
  vm.runInNewContext(functionSource(roomSource, 'crRerollIntimacyPreview'), context);

  await context.crOpenIntimacyPreview();
  assert.deepEqual(calls[0], ['/rooms/room-1/intimacy/preview']);
  assert.equal(nodes.crIntimacyPreviewModal.classList.contains('show'), true);
  assert.deepEqual(rendered[0], ['crIntimacyPreviewContent', '测试玩法']);

  await context.crRerollIntimacyPreview();
  assert.equal(calls[1][0], '/rooms/room-1/intimacy/start');
  assert.equal(calls[1][1].method, 'POST');
  assert.equal(calls[2][0], '/rooms/room-1/intimacy/preview');
});

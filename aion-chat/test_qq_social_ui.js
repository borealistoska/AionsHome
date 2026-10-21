const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, 'static/qq-social.js'), 'utf8');

function page(stopped = false) {
  const nodes = new Map(), timers = new Map(), requests = [];
  let timerId = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {textContent:'', innerHTML:'', disabled:false,
      dataset:{}, classList:{toggle(){}}, querySelectorAll:()=>[], querySelector:()=>({}),
      addEventListener(){}, setAttribute(){}});
    return nodes.get(id);
  };
  const context = vm.createContext({document:{hidden:false, getElementById:node},
    setInterval(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearInterval(id) { timers.delete(id); },
    async fetch(url) {
      requests.push(url);
      if (url.endsWith('/credentials')) return {ok:true, json:async()=>({actors:{}})};
      if (url.endsWith('/connect')) return {ok:true, json:async()=>({ok:true, connection:'connecting'})};
      return {ok:false, json:async()=>({detail:'服务不可用', retry_stopped:stopped})};
    }, console});
  vm.runInContext(source, context);
  return {context, nodes, timers, requests};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('unavailable service stops page requests after three failures until manual connect', async () => {
  const p = page();
  await settle();
  for (let n=0; n<5; n++) {
    for (const tick of [...p.timers.values()]) { tick(); await settle(); }
  }
  assert.equal(p.requests.filter(url=>url.endsWith('/status')).length, 3);
  assert.equal(p.timers.size, 0);
  assert.equal(p.nodes.get('power').textContent.includes('连接中'), false);
  const click = p.nodes.get('connect').onclick();
  await settle();
  await click;
  assert.equal(p.requests.filter(url=>url.endsWith('/connect')).length, 1);
  assert.equal(p.requests.filter(url=>url.endsWith('/status')).length, 4);
  assert.equal(p.timers.size, 1);
});

test('saved stopped status does not start another page retry cycle', async () => {
  const p = page(true);
  await settle();
  assert.equal(p.timers.size, 0);
  assert.equal(p.requests.filter(url=>url.endsWith('/status')).length, 1);
});

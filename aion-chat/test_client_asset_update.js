const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'static/chat.js'), 'utf8');
const handler = source.slice(source.indexOf("window.addEventListener('aion-client-update-ready'"), source.indexOf('// ── 收发消息音效'));

function setup(child) {
  const timers = [];
  let listener, reloads = 0;
  const frame = {tagName:'IFRAME', matches:()=>false, contentWindow:child};
  const context = {document:{activeElement:frame}, window:{addEventListener:(_,fn)=>{listener=fn;}},
    location:{reload:()=>{reloads++;}}, setTimeout:fn=>timers.push(fn),
    sending:false, streamingAiId:null, voiceInCall:false};
  vm.runInNewContext(handler,context);
  listener();
  return {tick:()=>timers.shift()(), reloads:()=>reloads};
}
const element = editing => ({tagName:editing?'TEXTAREA':'BUTTON', matches:()=>editing});

test('reading a retained chatroom frame does not postpone a verified update forever', () => {
  const app = setup({document:{activeElement:element(false)}});
  app.tick();
  assert.equal(app.reloads(),1);
});

test('a draft inside the frame delays update until editing finishes', () => {
  const child = {document:{activeElement:element(true)}};
  const app=setup(child);
  app.tick();assert.equal(app.reloads(),0);
  child.document.activeElement=element(false);
  app.tick();assert.equal(app.reloads(),1);
});

test('an active group reply delays update until its generation finishes', () => {
  let generating=true;
  const app=setup({document:{activeElement:element(false)},isAionClientUpdateBusy:()=>generating});
  app.tick();assert.equal(app.reloads(),0);
  generating=false;app.tick();assert.equal(app.reloads(),1);
});

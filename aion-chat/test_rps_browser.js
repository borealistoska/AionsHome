// Isolated UI acceptance. Fake rounds never touch chat history or model providers.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/32816/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const embedded=!!process.env.RPS_TEST_IFRAME;
    const page = await browser.newPage({viewport:{width:390,height:844},isMobile:embedded,hasTouch:embedded});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const games = new Map();
    let sequence=0, failPlay=false;
    let delayCreate=false, releaseCreate, signalCreated;
    let heldRound='', releaseRoundGet, signalRoundGet;
    const html = '<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/chatroom.css"><link rel="stylesheet" href="/static/rps.css"></head><body data-theme="dark"><main style="max-width:600px;margin:30px auto;padding:20px"><h3>小家 · 群聊</h3><div id="messages"></div><form class="composer" id="composer"><div id="rpsComposer"></div><textarea id="input" rows="1"></textarea><button type="button" class="send-btn">➤</button></form><button id="start" onclick="AionRps.start()">猜拳</button></main><script src="/static/rps.js"></script></body></html>';
    await page.route('http://rps.test/**', async route => {
      const url = new URL(route.request().url());
      if(url.pathname.startsWith('/api/rps')) {
        const body=route.request().postDataJSON() || {};
        const segments=url.pathname.split('/').filter(Boolean);
        if(segments.length===2 && route.request().method()==='GET') return route.fulfill({json:[...games.values()]});
        if(segments.length===2) {
          const game={id:'manual-'+(++sequence),scope:'chatroom',source_id:'group',actor:body.actor,initiator:'user',status:'draft',topic:body.topic,user_hand:body.hand,ai_hand:null,created_at:sequence+100,updated_at:sequence+100};
          games.set(game.id,game);
          const snapshot={...game};
          if(delayCreate) {signalCreated(game);await new Promise(resolve=>{releaseCreate=resolve;});}
          return route.fulfill({json:snapshot});
        }
        const game=games.get(segments[2]);
        if(route.request().method()==='GET' && segments[2]===heldRound) {
          await new Promise(resolve=>{releaseRoundGet=resolve;signalRoundGet();});
        }
        if(segments[3]==='play') {
          if(failPlay) return route.fulfill({status:503,json:{detail:'暂时离线，请重试'}});
          Object.assign(game,{status:'done',result_pending:true,user_hand:body.hand,ai_hand:'石头',winner:body.hand==='布'?'user':body.hand==='石头'?'draw':game.actor});
        }
        if(segments[3]==='cancel') game.status='cancelled';
        return route.fulfill({json:game});
      }
      if(url.pathname==='/' && embedded) return route.fulfill({contentType:'text/html',body:'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><iframe src="/chatroom" sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-modals" style="position:absolute;inset:0;width:100%;height:100%;border:0"></iframe>'});
      if(url.pathname==='/' || url.pathname==='/chatroom') return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname.startsWith('/static/')) {
        const file=path.join(__dirname,'static',path.basename(url.pathname));
        return route.fulfill({contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});
      }
      // Serve real local avatars/backgrounds for the screenshot, without external requests.
      const file=path.join(__dirname,'..',url.pathname.slice(1));
      if(url.pathname.startsWith('/public/') && fs.existsSync(file)) return route.fulfill({body:fs.readFileSync(file),contentType:url.pathname.endsWith('.png')?'image/png':'image/jpeg'});
      return route.fulfill({status:404,body:''});
    });
    await page.goto('http://rps.test/');
    const ui=embedded ? page.frames().find(frame=>frame.parentFrame()) : page;
    await ui.evaluate(() => {
      window.rpsPageVisible=true; window.rpsSource='group';
      window.AionRps.bind({container:document.getElementById('messages'),composer:document.getElementById('rpsComposer'),getContext:()=>({scope:'chatroom',source_id:window.rpsSource,visible:window.rpsPageVisible,names:{user:'Ithil',aion:'Aion',connor:'Connor'},actors:['aion','connor']})});
      window.addInvite = game => {
        const message={room_id:'group',attachments:[{type:'rps_invite',round_id:game.id,actor:game.actor,initiator:game.initiator,topic:game.topic}]};
        document.getElementById('messages').insertAdjacentHTML('beforeend','<div class="system-event-msg"><div class="system-event-line">'+AionRps.inviteHTML(message)+'</div></div>');
        return AionRps.onInvite(message);
      };
    });
    const first={id:'ai-first',scope:'chatroom',source_id:'group',actor:'connor',initiator:'connor',status:'waiting_user',topic:'谁输谁是小狗',result_pending:false,ai_hand:null,user_hand:null,created_at:1,updated_at:1};
    const second={...first,id:'ai-second',actor:'aion',initiator:'aion',created_at:2};
    games.set(first.id,first);
    await ui.evaluate(()=>AionRps.refresh());
    await ui.evaluate(()=>{window.rpsPageVisible=false;});
    await ui.evaluate(game=>AionRps.onInvite({room_id:'group',attachments:[{type:'rps_invite',round_id:game.id,actor:game.actor,initiator:game.initiator,topic:game.topic}]}),first);
    assert.equal(await ui.locator('.rps-dialog').count(),0,'后台聊天不能在当前页面上弹出猜拳');
    await ui.evaluate(()=>{window.rpsPageVisible=true;});
    await ui.evaluate(game=>addInvite(game),first);
    games.set(second.id,second);
    heldRound=second.id;
    const roundGetStarted=new Promise(resolve=>{signalRoundGet=resolve;});
    const queuedInvitation=ui.evaluate(game=>addInvite(game),second);
    await roundGetStarted;
    await ui.locator('.rps-dialog').waitFor();
    assert.equal(await ui.locator('.rps-ai-hand > span').textContent(),'?');
    assert.equal(await ui.locator('.rps-eyebrow').textContent(),'谁输谁是小狗');
    await ui.getByRole('button',{name:'关闭，保留本局'}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    heldRound='';releaseRoundGet();await queuedInvitation;
    assert.equal(await ui.locator('.rps-dialog').count(),0,'已收到的排队邀请查询晚到时不能重新弹窗');
    await ui.evaluate(game=>AionRps.onUpdate(game),second);
    assert.equal(await ui.locator('.rps-dialog').count(),0,'收起本批邀请后不能立刻弹出排队的旧邀请');
    await ui.locator('[data-rps-round="ai-first"]').click();
    await ui.locator('.rps-dialog').waitFor();
    if(process.env.RPS_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.RPS_SCREENSHOT_DIR,'rps-implemented-dark.png'),animations:'disabled'});
    failPlay=true;
    await ui.getByRole('button',{name:'布',exact:true}).click();
    await ui.locator('.rps-error').waitFor();
    assert.match(await ui.locator('.rps-error').textContent(),/暂时离线/);
    failPlay=false;
    await ui.getByRole('button',{name:'布',exact:true}).click();
    await ui.getByText('你赢啦！',{exact:true}).waitFor();
    assert.equal(await ui.locator('.rps-ai-hand > span').textContent(),'✊');
    assert.equal(await ui.locator('#rpsComposer').textContent(),'','结果结束后不再有待同步提示');
    await ui.getByRole('button',{name:'下一场 · Aion'}).click();
    await ui.getByText('Aion邀请你猜拳',{exact:true}).last().waitFor();
    await ui.getByRole('button',{name:'放弃本局',exact:true}).click();
    await ui.getByText('本局已放弃',{exact:true}).last().waitFor();
    await ui.getByRole('button',{name:'完成',exact:true}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    await ui.locator('#start').click();
    await ui.locator('[data-rps-target]').selectOption('connor');
    await ui.getByRole('button',{name:'布',exact:true}).click();
    await ui.getByText('请先填写本局约定',{exact:true}).waitFor();
    await ui.getByLabel('本局约定').fill('我赢了就吃三颗山竹');
    await ui.getByRole('button',{name:'布',exact:true}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    assert.match(await ui.locator('#rpsComposer').textContent(),/待发送.*我赢了就吃三颗山竹/);
    assert.equal(await ui.evaluate(()=>AionRps.pendingRoundId()), 'manual-1');
    const draftLayout=await ui.evaluate(()=>({theme:document.querySelector('#rpsComposer').getBoundingClientRect().toJSON(), input:document.querySelector('#input').getBoundingClientRect().toJSON(), width:innerWidth}));
    assert(draftLayout.theme.bottom<=draftLayout.input.top && draftLayout.theme.right<=draftLayout.width,'待发送猜拳应在输入框上方且不溢出');
    if(process.env.RPS_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.RPS_SCREENSHOT_DIR,'rps-pending.png'),animations:'disabled'});
    await ui.evaluate(()=>{window.rpsSource='elsewhere';return AionRps.refresh();});
    assert.equal(await ui.evaluate(()=>AionRps.pendingRoundId()), '');
    assert.equal(await ui.locator('#rpsComposer').textContent(), '');
    await ui.evaluate(()=>{window.rpsSource='group';return AionRps.refresh();});
    assert.equal(await ui.evaluate(()=>AionRps.pendingRoundId()), 'manual-1');
    await ui.locator('[data-rps-review]').click();
    await ui.getByText('等待发送',{exact:true}).waitFor();
    assert.equal(await ui.locator('.rps-user-hand > span').textContent(),'🖐️');
    const manual=games.get('manual-1');
    assert.equal(manual.topic, '我赢了就吃三颗山竹');
    Object.assign(manual,{status:'waiting_ai',updated_at:manual.updated_at+1});
    await ui.evaluate(game=>AionRps.onUpdate(game),manual);
    await ui.evaluate(game=>addInvite(game),manual);
    await ui.getByRole('button',{name:'关闭，保留本局'}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    Object.assign(manual,{status:'done',ai_hand:'石头',winner:'user'});
    await ui.evaluate(game=>AionRps.onUpdate(game),manual);
    await ui.locator('.rps-dialog').waitFor();
    assert.match(await ui.locator('[data-rps-round="manual-1"]').textContent(),/我发起了猜拳.*我赢了就吃三颗山竹.*查看结果/);
    assert.equal(await ui.locator('[data-rps-round="manual-1"]').evaluate(el=>el.closest('.system-event-msg').classList.contains('rps-user-invite')),true);
    await ui.getByRole('button',{name:'关闭，保留本局'}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    await ui.evaluate(game=>{AionRps.onUpdate(game);return AionRps.refresh();},manual);
    assert.equal(await ui.locator('.rps-dialog').count(),0,'同一结果关闭或刷新不能重复弹出');
    await ui.locator('[data-rps-round="manual-1"]').click();
    await ui.getByText('你赢啦！',{exact:true}).waitFor();
    await page.setViewportSize({width:320,height:740});
    await ui.evaluate(()=>document.body.dataset.theme='light');
    const geometry=await ui.locator('.rps-dialog').evaluate(element=>({width:innerWidth,scroll:document.documentElement.scrollWidth,rect:element.getBoundingClientRect().toJSON()}));
    assert(geometry.scroll<=geometry.width && geometry.rect.left>=0 && geometry.rect.right<=geometry.width,'320px 排版溢出');
    if(process.env.RPS_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.RPS_SCREENSHOT_DIR,'rps-implemented-light.png'),animations:'disabled'});
    // Withdrawing a saved draft removes it without sending an invitation.
    await ui.getByRole('button',{name:'完成',exact:true}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    await ui.locator('#start').click();
    await ui.getByLabel('本局约定').fill('随便玩一局');
    await ui.getByRole('button',{name:'布',exact:true}).click();
    await ui.locator('.rps-dialog').waitFor({state:'detached'});
    await ui.locator('[data-rps-remove]').click();
    await ui.waitForFunction(()=>AionRps.pendingRoundId()==='');
    assert.equal(games.get('manual-2').status,'cancelled');
    const background={...manual,id:'background-result',status:'waiting_ai',created_at:400,updated_at:400};
    games.set(background.id,background);
    await ui.evaluate(game=>AionRps.onUpdate(game),background);
    Object.assign(background,{status:'done',updated_at:401});
    await ui.evaluate(()=>AionRps.refresh({live:true}));
    assert.equal(await ui.locator('.rps-dialog').count(),0,'恢复连接补查到的旧结果不能自动弹窗');
    const future={...first,id:'ai-future',status:'waiting_user',user_hand:null,ai_hand:null,winner:null,created_at:500,updated_at:500};
    games.set(future.id,future);
    await ui.evaluate(game=>addInvite(game),future);
    assert.equal(await ui.locator('.rps-dialog').count(),1,'关闭旧局之后，新邀请仍应自动弹出');
    if(process.env.RPS_SCREENSHOT_DIR) {
      await ui.getByRole('button',{name:'关闭，保留本局'}).click();
      await ui.locator('.rps-dialog').waitFor({state:'detached'});
      await page.screenshot({path:path.join(process.env.RPS_SCREENSHOT_DIR,'rps-invitation-line.png'),animations:'disabled'});
    }
    assert.deepEqual(errors,[]);
    console.log('Passed: concealed hands, close/resume, queue dismissal, new-invitation popup after close, retry, reveal, abandon, user invitation, background result, topic validation, persisted draft, chat isolation, withdrawal, 320px and both themes.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

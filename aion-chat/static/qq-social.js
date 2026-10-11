'use strict';
let state=null, selected=null, settingsDirty=false, busy=false, connectionError=false;
const $=id=>document.getElementById(id);
const settingFields=['interval_seconds','context_messages','image_wait_seconds','hourly_limit','total_hourly_limit'];
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'尚无';
const folded=value=>{const text=String(value||'');return text.length>140?`<details class="long"><summary>${esc(text.slice(0,90))}…</summary><p>${esc(text)}</p><button type="button" data-collapse>收起</button></details>`:`<p>${esc(text)}</p>`;};
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function api(path,method='GET',body){const res=await fetch('/api/qq-social/'+path,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const data=await res.json();if(!res.ok)throw Error(data.detail||'请求失败');return data;}
async function refresh(){
 if(busy)return;
 try{
  state=await api('status');
  if(connectionError){notice('');connectionError=false;}
  $('power').disabled=false;$('power').textContent=state.settings.enabled?'已开启 · 关闭':'已关闭 · 开启';$('power').classList.toggle('off',!state.settings.enabled);
  const actorOpen=[...$('actors').querySelectorAll('details[open]')].map(d=>d.dataset.actor);
  $('actors').innerHTML=Object.entries(state.actors).map(([actor,a])=>`<article class="actor"><h2>${esc(a.name)}<span class="badge">${esc(a.state)}</span></h2><p class="model" title="${esc(a.model)}">${esc(a.model||'未选择模型')}</p><details data-actor="${actor}" ${actorOpen.includes(actor)?'open':''}><summary>连接详情</summary><p class="muted">收到：${date(a.last_event?.at)}<br>发送：${date(a.last_send)}</p>${a.context?.home_messages!==undefined?`<p class="muted">上次参考 ${a.context.home_messages} 条上下文、${a.context.recalled_memories} 条自身记忆</p>`:''}</details>${a.error||a.reply_error?`<p class="error">${esc(a.error||a.reply_error)}</p>`:''}</article>`).join('');
  $('modeLabel').textContent=state.settings.enabled?'点选即生效':'总开关已关';
  for(const b of $('modeControls').querySelectorAll('button')){b.disabled=false;b.setAttribute('aria-pressed',String(b.dataset.mode===state.settings.mode));}
  $('modeHint').textContent=!state.settings.enabled?'所有回复已暂停；可预选下次开启的方式。':state.settings.mode==='natural'?'被 @ 会回应，也会根据话题低频接话。':'只有被 @ 才回复，普通聊天和单独引用不触发。';
  $('rateLabel').textContent=state.settings.interval_seconds+' 秒 · '+(state.settings.context_messages??35)+' 条';
  $('bringHome').checked=state.settings.bring_home??true;
  $('bringHome').disabled=state.settings.bring_home===undefined;
  if($('outsidePanel').open)await refreshOutside();
  if(!settingsDirty){const f=$('settings');for(const k of settingFields)f.elements[k].value=state.settings[k]??({context_messages:35,image_wait_seconds:60}[k]);}
  $('groups').innerHTML=state.groups.length?state.groups.map(g=>`<div class="group"><div class="row"><div><strong>${esc(g.name||'朋友群 · '+g.group_id.slice(-6))}</strong><br><small>${esc(state.actors[g.actor]?.name||g.actor)} · ${g.full_messages?'可见普通群消息':'等待普通群消息'}</small></div><button data-detail="${g.id}">聊天与记忆</button></div><div class="switches"><label><input type="checkbox" data-group="${g.id}" data-field="enabled" ${g.enabled?'checked':''}> 参与</label><label><input type="checkbox" data-group="${g.id}" data-field="memory_enabled" ${g.memory_enabled?'checked':''}> 积累记忆</label></div></div>`).join(''):'<p class="empty">在群里分别 @ 两位 AI，即可完成首次配对。</p>';
  if(selected)await detail(selected);
 }catch(e){connectionError=true;notice(e.message,true);$('power').disabled=true;for(const b of $('modeControls').querySelectorAll('button'))b.disabled=true;}
}
async function detail(id){
 selected=Number(id);const d=await api('groups/'+selected);$('detail').hidden=false;
 const names=Object.fromEntries(d.members.map(m=>[m.member_id,m.alias||m.name||'成员-'+m.member_id.slice(-6)]));
 $('detailTitle').textContent=(d.group.name||'朋友群')+' · '+(state.actors[d.group.actor]?.name||'AI');
 $('memberCount').textContent=d.members.length;$('memoryCount').textContent=d.memories.length;$('messageCount').textContent=Math.min(d.messages.length,30);
 const summaryMarkup=folded(d.group.summary||'聊过一段时间后，空闲时整理。');
 if($('summary').dataset.version!==summaryMarkup){$('summary').innerHTML=summaryMarkup;$('summary').dataset.version=summaryMarkup;}
 $('members').innerHTML=d.members.map(m=>`<div class="entry row"><span>${esc(names[m.member_id])} ${m.is_owner?'· 你':''}</span><button data-member="${esc(m.member_id)}" data-alias="${esc(names[m.member_id])}">备注 / 身份</button></div>`).join('');
 const memoryMarkup=d.memories.length?d.memories.map(m=>`<div class="entry"><div class="row"><small>${esc(names[m.member_id]||'成员-'+m.member_id.slice(-6))} · ${date(m.created_at)}</small><button data-delete="${m.id}">删除</button></div>${folded(m.content)}</div>`).join(''):'<p class="empty">还没有交往记忆。</p>';
 const messageMarkup=d.messages.slice(-30).map(m=>`<div class="entry"><small>${esc(m.direction==='out'?state.actors[d.group.actor]?.name:names[m.member_id]||m.name)} · ${date(m.created_at)}</small>${folded(m.content)}</div>`).join('');
 if($('memories').dataset.version!==memoryMarkup){$('memories').innerHTML=memoryMarkup;$('memories').dataset.version=memoryMarkup;}
 if($('messages').dataset.version!==messageMarkup){$('messages').innerHTML=messageMarkup;$('messages').dataset.version=messageMarkup;}
}
async function action(fn){if(busy)return;busy=true;$('power').disabled=true;for(const b of $('modeControls').querySelectorAll('button'))b.disabled=true;try{await fn();notice('已保存');}catch(e){notice(e.message,true);}finally{busy=false;await refresh();}}
$('power').onclick=()=>action(()=>api('settings','PATCH',{enabled:!state.settings.enabled}));
$('bringHome').onchange=e=>action(()=>api('settings','PATCH',{bring_home:e.target.checked}));
async function refreshOutside(){
 const res=await fetch('/api/external-memories');if(!res.ok)throw Error(res.status===404?'新功能已准备好，重启小家和 QQ 服务后启用。':'外出回忆暂时无法读取');const data=await res.json();
 const markup=Object.entries(data).map(([actor,rows])=>`<div class="entry"><div class="row"><strong>${esc(state?.actors[actor]?.name||'AI')}</strong><a href="/memory?store=${actor==='aion'?'main':'chatroom'}&kind=friend">管理朋友记忆</a></div>${state?.actors[actor]?.context?.memory_status==='failed'?'<p class="error">上次带回失败，未确认保存。</p>':''}${rows.length?rows.map(m=>`<div class="entry"><small>${date(m.created_at)}</small>${folded(m.content)}</div>`).join(''):'<p class="muted">还没有带回的回忆，不必每次聊天都记。</p>'}</div>`).join('');
 if($('outsideMemories').dataset.version!==markup){$('outsideMemories').innerHTML=markup;$('outsideMemories').dataset.version=markup;}
}
$('outsidePanel').ontoggle=()=>{if($('outsidePanel').open)refreshOutside().catch(e=>notice(e.message,true));};
$('outsideMemories').onclick=e=>{const b=e.target.closest('[data-collapse]');if(b)b.closest('details').open=false;};
$('modeControls').onclick=e=>{const b=e.target.closest('[data-mode]');if(b&&state&&b.dataset.mode!==state.settings.mode)action(()=>api('settings','PATCH',{mode:b.dataset.mode}));};
$('settings').oninput=()=>{settingsDirty=true;};
$('presets').onclick=e=>{const b=e.target.closest('[data-interval]');if(!b)return;const f=$('settings');f.elements.interval_seconds.value=b.dataset.interval;f.elements.hourly_limit.value=b.dataset.hourly;settingsDirty=true;notice('已填入快捷值，点击保存参数后生效');};
$('settings').onsubmit=e=>{e.preventDefault();const f=e.currentTarget;action(async()=>{await api('settings','PATCH',Object.fromEntries(settingFields.map(k=>[k,Number(f.elements[k].value)])));settingsDirty=false;});};
$('groups').onchange=e=>{const t=e.target;if(t.dataset.group)action(()=>api('groups/'+t.dataset.group,'PATCH',{[t.dataset.field]:t.checked}));};
$('groups').onclick=e=>{const b=e.target.closest('[data-detail]');if(b)detail(b.dataset.detail).catch(e=>notice(e.message,true));};
$('detail').onclick=e=>{const collapse=e.target.closest('[data-collapse]');if(collapse)collapse.closest('details').open=false;const d=e.target.closest('[data-delete]');if(d)action(()=>api('memories/'+d.dataset.delete,'DELETE'));const m=e.target.closest('[data-member]');if(m){const alias=prompt('给这位群成员的备注名',m.dataset.alias);if(alias===null)return;const owner=confirm('这位是你本人（小家的用户）吗？\n确定：标记为你；取消：普通朋友。');action(()=>api('groups/'+selected+'/members/'+encodeURIComponent(m.dataset.member),'PATCH',{alias,is_owner:owner}));}};
refresh();setInterval(()=>{if(!document.hidden)refresh();},8000);

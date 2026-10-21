/* One shared, resumable duel dialog for all three chat types. */
(() => {
  const games = new Map();
  const seen = new Set();
  const revealed = new Set();
  const resultQueue = [];
  const receivedInvites = new Map();
  const symbols = { '石头': '✊', '剪刀': '✌️', '布': '🖐️' };
  let binding, dialog, view, busy = false, refreshSequence = 0;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const context = () => binding?.getContext();
  const sameSource = (game, ctx = context()) => !!ctx?.source_id && game.scope === ctx.scope && game.source_id === ctx.source_id;
  const pending = () => [...games.values()].filter(game => sameSource(game) && ['waiting_user','waiting_ai'].includes(game.status)).sort((a,b) => a.created_at-b.created_at);
  const draftRound = () => [...games.values()].find(game => sameSource(game) && game.status === 'draft');
  const actorName = actor => context()?.names?.[actor] || 'AI';
  const userName = () => context()?.names?.user || '你';
  const invitationLabel = game => (game.initiator === 'user' ? '我发起了猜拳' : actorName(game.actor)+'发起了猜拳')
    + ' · ' + (game.topic || '随便玩一局')
    + (game.status === 'done' ? ' · 查看结果' : game.status === 'cancelled' ? ' · 已放弃' : '');
  const visibleHere = () => {
    const frame = window.frameElement;
    return context()?.visible !== false && document.visibilityState !== 'hidden'
      && (!frame || (frame.getClientRects().length && window.parent.getComputedStyle(frame).visibility !== 'hidden'));
  };

  function showNextResult() {
    if(view || !visibleHere()) return;
    while(resultQueue.length) {
      const game = games.get(resultQueue.shift());
      if(!game || !sameSource(game)) continue;
      view={...game}; ensureDialog(); render(); break;
    }
  }
  const avatar = actor => actor === 'user' ? '/public/UserIcon.png' : actor === 'connor' ? '/public/codexicon.png' : context()?.scope === 'private' ? '/public/AIIcon.png' : '/public/gropicon1.png';

  async function request(path, data) {
    const response = await fetch('/api/rps'+path, data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {cache:'no-store'});
    const result = await response.json();
    if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '猜拳操作失败，请稍后重试');
    return result;
  }

  function renderComposer() {
    const host = binding?.composer;
    if(!host) return;
    const draft = draftRound();
    host.innerHTML = draft
      ? `<div class="rps-pending"><button type="button" data-rps-review="${escape(draft.id)}">待发送的猜拳 · ${escape(actorName(draft.actor))} · ${escape(draft.topic)}</button><button type="button" data-rps-remove="${escape(draft.id)}" aria-label="撤掉待发送的猜拳">撤掉</button></div>`
      : '';
    binding?.onPendingChange?.();
  }

  function updateButtons() {
    binding?.container.querySelectorAll('[data-rps-round]').forEach(button => {
      const game = games.get(button.dataset.rpsRound);
      if(!game) return;
      const label = invitationLabel(game);
      button.querySelector('.rps-invite-label').textContent = label;
      button.title = label;
      button.closest('.system-event-msg, .msg-row.system')?.classList.toggle('rps-user-invite', game.initiator === 'user');
      button.disabled = game.status === 'cancelled';
    });
  }

  function close() {
    for(const [id, source] of receivedInvites) {
      if(view && source.scope===view.scope && source.source_id===view.source_id) seen.add(id);
    }
    for(const game of games.values()) {
      if(view && game.scope===view.scope && game.source_id===view.source_id
        && ['waiting_user','waiting_ai'].includes(game.status)) seen.add(game.id);
    }
    dialog?.close();
  }

  function ensureDialog() {
    if(dialog) return;
    const before = document.activeElement;
    dialog = document.createElement('dialog');
    dialog.className = 'rps-dialog';
    dialog.setAttribute('aria-labelledby','rpsTitle');
    dialog.addEventListener('cancel', event => {event.preventDefault();close();});
    dialog.addEventListener('close', () => {dialog.remove();dialog=null;view=null;busy=false;if(before?.isConnected) before.focus({preventScroll:true});showNextResult();});
    dialog.addEventListener('click', handleClick);
    dialog.addEventListener('input', event => {if(event.target.matches('[data-rps-topic]') && view?.draft) view.topic=event.target.value;});
    dialog.addEventListener('change', event => {if(event.target.matches('[data-rps-target]') && view?.draft){view.actor=event.target.value;render();}});
    document.body.appendChild(dialog);
    dialog.showModal();
  }

  function render() {
    if(!dialog || !view) return;
    const game = view.draft ? view : games.get(view.id);
    if(!game || !sameSource(game)){close();return;}
    const done = game.status === 'done', cancelled = game.status === 'cancelled';
    const unsent = game.status === 'draft';
    const waiting = game.status === 'waiting_ai' || unsent;
    const name = actorName(game.actor);
    const rest = pending().filter(g => g.id !== game.id);
    const result = game.winner === 'draw' ? '平局，再比心一次' : game.winner === 'user' ? '你赢啦！' : name+'赢了';
    const title = done ? result : cancelled ? '本局已放弃' : unsent ? '等待发送' : waiting ? '等待'+name+'出拳' : '轮到你啦';
    const caption = done ? '本局结束，结果已记入聊天。' : cancelled ? '不计输赢，下次再来。' : unsent ? '已保存，回到聊天后点发送，可一起说句话。' : waiting ? '你的选择已保密，可以先收起界面。' : view.draft ? '选好后放入聊天，点发送才会邀请对方。' : '选好就出拳，双方一起揭晓。';
    const targets = context()?.actors || ['aion'];
    const choices = ['石头','剪刀','布'].map(hand => `<button type="button" class="rps-choice" data-rps-hand="${hand}" ${busy?'disabled':''}><span aria-hidden="true">${symbols[hand]}</span>${hand}</button>`).join('');
    dialog.innerHTML = `<div class="rps-dialog-top">${view.draft ? `<input class="rps-topic-input" data-rps-topic aria-label="本局约定" maxlength="200" value="${escape(view.topic || '')}" placeholder="本局约定，例如谁输了谁是小狗" autocomplete="off">` : `<span class="rps-eyebrow">${escape(game.topic || '随便玩一局')}</span>`}<button type="button" class="rps-close" data-rps-action="close" aria-label="关闭，保留本局">×</button></div>
      <h2 id="rpsTitle">石头 · 剪刀 · 布</h2><p class="rps-caption">${escape(game.initiator==='user' ? '你向'+name+'发起猜拳' : name+'邀请你猜拳')}</p>
      ${view.draft && targets.length>1 ? `<label class="rps-target">邀请谁？<select data-rps-target aria-label="猜拳对象">${targets.map(actor=>`<option value="${actor}" ${actor===game.actor?'selected':''}>${escape(actorName(actor))}</option>`).join('')}</select></label>` : ''}
      <div class="rps-arena${done?' rps-revealed':''}">
        <div class="rps-player"><div class="rps-player-name"><img src="${avatar('user')}" alt="">${escape(userName())}</div><div class="rps-hand rps-user-hand"><span>${game.user_hand ? symbols[game.user_hand] : '?'}</span></div><span class="rps-hand-state">${game.user_hand ? done ? game.user_hand : '已出拳 · 已锁定' : '等你出拳'}</span></div>
        <span class="rps-vs">VS</span>
        <div class="rps-player"><div class="rps-player-name"><img src="${avatar(game.actor)}" alt="">${escape(name)}</div><div class="rps-hand rps-ai-hand ${!done?'rps-sealed':''}"><span>${done ? symbols[game.ai_hand] : '?'}</span>${!done && !view.draft ? '<small aria-label="选择保密">🔒</small>' : ''}</div><span class="rps-hand-state">${done ? game.ai_hand : waiting || view.draft ? '等待出拳' : cancelled ? '已放弃' : '已出拳 · 未揭晓'}</span></div>
      </div>
      <div class="rps-status" aria-live="polite"><strong>${escape(title)}</strong><span>${escape(caption)}</span></div>
      ${done || cancelled ? `<button type="button" class="rps-complete" data-rps-action="next">${rest.length ? '下一场 · '+escape(actorName(rest[0].actor)) : '完成'}</button>` : waiting ? '' : `<div class="rps-choices">${choices}</div>`}
      <p class="rps-error" role="alert" ${view.error?'':'hidden'}>${escape(view.error||'')}</p>
      <footer class="rps-footer"><button type="button" data-rps-action="close">${done||cancelled?'收起结果':'稍后再玩'}</button><span>${rest.length ? '还有 '+rest.length+' 场邀请' : '这一局，慢慢来'}</span>${!view.draft && !done && !cancelled ? `<button type="button" class="rps-abandon" data-rps-action="cancel" ${busy?'disabled':''}>放弃本局</button>` : ''}</footer>`;
  }

  function apply(game, live = false, revealResult = false) {
    if(!game?.id) return;
    const current = games.get(game.id);
    if(current && (current.updated_at > game.updated_at ||
      (['done','cancelled'].includes(current.status) && !['done','cancelled'].includes(game.status)))) return current;
    games.set(game.id,game);
    updateButtons();
    renderComposer();
    if(view && !view.draft && sameSource(game)) render();
    const visible = visibleHere();
    if(game.status === 'done') {
      const justFinished = revealResult && current?.status === 'waiting_ai' && game.initiator === 'user' && !revealed.has(game.id);
      revealed.add(game.id);
      if(justFinished && sameSource(game) && visible && view?.id !== game.id) {
        resultQueue.push(game.id); showNextResult();
      }
    }
    if(live && !seen.has(game.id) && sameSource(game) && game.status==='waiting_user' && !view && visible) {
      seen.add(game.id); view={...game};ensureDialog();render();
    }
    return game;
  }

  async function openRound(id) {
    try {
      const game = apply(await request('/'+encodeURIComponent(id)));
      if(!sameSource(game)) return;
      seen.add(id);view={...game};ensureDialog();render();
    } catch(error){ binding?.onError?.(error.message); }
  }

  async function handleClick(event) {
    const button = event.target.closest('button');
    if(!button || !view) return;
    const action = button.dataset.rpsAction;
    if(action==='close'){close();return;}
    if(action==='next'){
      const next = pending().find(g=>g.id!==view.id);
      if(next) {view={...next};render();} else close();
      return;
    }
    if(busy) return;
    const active = view;
    if(button.dataset.rpsHand && active.draft && !active.topic?.trim()) {
      active.error='请先填写本局约定'; render(); dialog.querySelector('[data-rps-topic]')?.focus(); return;
    }
    delete active.error;
    busy = true;render();
    try {
      let result;
      if(button.dataset.rpsHand) {
        const options = binding?.getOptions?.() || {};
        result = await request(active.draft ? '' : '/'+encodeURIComponent(active.id)+'/play', active.draft ? {scope:active.scope,source_id:active.source_id,actor:active.actor,hand:button.dataset.rpsHand,topic:active.topic.trim(),reply_options:options} : {hand:button.dataset.rpsHand,reply_options:options});
      } else if(action==='cancel') result = await request('/'+encodeURIComponent(active.id)+'/cancel',{});
      if(result) {
        result = apply(result);
        if(active.draft && result.status==='draft' && view===active) {close(); return;}
        if(view===active && dialog){view={...result};render();}
      }
    } catch(error) {
      if(dialog && view===active){active.error=error.message;render();}
      else binding?.onError?.(error.message);
    } finally {busy=false;if(dialog && view?.id===active.id) render();}
  }

  async function refresh({live=false} = {}) {
    const ctx=context();
    const sequence=++refreshSequence;
    if(view && !sameSource(view,ctx)) close();
    renderComposer();
    if(!ctx?.source_id) return;
    try {
      const rounds=await request('?scope='+encodeURIComponent(ctx.scope)+'&source_id='+encodeURIComponent(ctx.source_id));
      if(sequence!==refreshSequence || !sameSource({scope:ctx.scope,source_id:ctx.source_id})) return;
      rounds.forEach(game=>apply(game,live));updateButtons();
    } catch(error){console.warn('[rps] refresh failed',error);}
  }

  window.AionRps = {
    bind(options) {
      binding=options;
      const form = options.composer?.closest('form');
      if(form) new ResizeObserver(() => {
        form.parentElement.style.setProperty('--rps-composer-height', form.getBoundingClientRect().height+'px');
      }).observe(form);
      options.composer?.addEventListener('click', async event => {
        const review=event.target.closest('[data-rps-review]');
        if(review) {openRound(review.dataset.rpsReview);return;}
        const remove=event.target.closest('[data-rps-remove]');
        if(!remove || remove.disabled) return;
        remove.disabled=true;
        try {apply(await request('/'+encodeURIComponent(remove.dataset.rpsRemove)+'/cancel',{}));}
        catch(error) {binding?.onError?.(error.message);remove.disabled=false;}
      });
      options.container.addEventListener('click',event=>{const button=event.target.closest('[data-rps-round]');if(button){event.stopPropagation();openRound(button.dataset.rpsRound);}});
      window.addEventListener('focus',refresh);
      document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') refresh();});
    },
    start() {
      const ctx=context();
      if(!ctx?.source_id){binding?.onError?.('请先选择一个聊天');return;}
      const existing=draftRound() || pending().find(game=>game.status==='waiting_ai');
      if(existing){openRound(existing.id);return;}
      view={draft:true,scope:ctx.scope,source_id:ctx.source_id,actor:(ctx.actors||['aion'])[0],initiator:'user',status:'choose',user_hand:null};
      ensureDialog();render();
    },
    refresh,
    pendingRoundId: () => draftRound()?.id || '',
    async onInvite(message) {
      const invite=Array.isArray(message?.attachments) && message.attachments.find(a=>a?.type==='rps_invite');
      const source={scope:message?.conv_id?'private':'chatroom',source_id:message?.conv_id||message?.room_id};
      if(!invite || !sameSource(source)) return;
      receivedInvites.set(invite.round_id,source);
      try {apply(await request('/'+encodeURIComponent(invite.round_id)),true);}
      catch(error){console.warn('[rps] invitation refresh failed',error);}
    },
    onUpdate: game => apply(game,true,true),
    stripCommands: text => String(text||'').replace(/\[\s*猜拳[^\]]*(?:\]|$)/g,''),
    isUserInvite(message) {
      const invite=message?.attachments?.find(a=>a?.type==='rps_invite');
      return !!invite && (games.get(invite.round_id)?.initiator || invite.initiator) === 'user';
    },
    inviteHTML(message) {
      const attachments=Array.isArray(message?.attachments) ? message.attachments : [];
      const invite=attachments.find(a=>a?.type==='rps_invite');
      if(!invite) return '';
      const game=games.get(invite.round_id);
      const label=invitationLabel(game || invite);
      return `<button type="button" class="rps-invite" title="${escape(label)}" data-rps-round="${escape(invite.round_id)}" ${game?.status==='cancelled'?'disabled':''}><span class="rps-invite-label">${escape(label)}</span></button>`;
    },
  };
})();

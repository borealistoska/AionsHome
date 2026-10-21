/* Table chat mirrors the billiards room: separate dialogue, hold-to-talk and queued TTS. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  function bind({kind, request, onChatState}) {
    let game = null, names = {}, seen = new Set(), sending = false, recording = false;
    let chatConfig = {}, lastRequest = null, pollTimer = null, pollBusy = false, lastStamp = 0;
    let queue = Promise.resolve(), activeAudio = null, generation = 0, fetchingSpeech = null;
    let key = '', recorder;
    const error = text => { $('chatState').textContent = text || ''; $('tableFeedback').textContent = text || ''; };
    function open(show) {
      $('roomChat').hidden = !show; $('chatDismiss').hidden = !show;
      $('tableChatToggle').setAttribute('aria-expanded', String(show));
      document.body.classList.toggle('table-chat-open', show);
      if (show) { $('tableChatToggle').classList.remove('unread'); $('tableMessages').scrollTop = $('tableMessages').scrollHeight; }
      else { recorder?.cancel(); $('tableChatToggle').focus({preventScroll:true}); }
    }
    function stopSpeech() {
      generation++; fetchingSpeech?.abort(); fetchingSpeech = null; activeAudio?.(); activeAudio = null;
      window.TableAtmosphere?.sound.quiet(false);
    }
    function speak(message, manual = false) {
      if (manual) stopSpeech();
      const epoch = generation, gameId = game?.id;
      const valid = () => epoch === generation && gameId === game?.id && !recording && !document.hidden;
      queue = queue.then(async () => {
        if (!valid()) return;
        const voice = chatConfig[message.sender === 'connor' ? 'tts_connor_voice' : 'tts_aion_voice'];
        if (!voice) { error('请在群聊语音设置中选好这位伴侣的音色，之后可点消息旁的播放。'); return; }
        const controller = new AbortController(); fetchingSpeech = controller;
        let url;
        try {
          const response = await fetch('/api/tts', {method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
            body:JSON.stringify({text:message.text, voice, msg_id:message.id})});
          if (!response.ok) throw Error('这句语音没能播放，可以点播放重试。');
          const blob = await response.blob();
          if (!valid()) return;
          url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          window.TableAtmosphere?.sound.quiet(true);
          await new Promise(resolve => {
            activeAudio = () => { audio.pause(); resolve(); };
            audio.onended = resolve;
            audio.onerror = () => { error('语音播放失败，可以点播放重试。'); resolve(); };
            audio.play().catch(() => { error('点消息旁的播放，就能听这句。'); resolve(); });
          });
        } finally {
          if (fetchingSpeech === controller) fetchingSpeech = null;
          activeAudio = null;
          if (url) URL.revokeObjectURL(url);
          window.TableAtmosphere?.sound.quiet(false);
        }
      }).catch(e => { if (e.name !== 'AbortError') error(e.message); });
    }
    function update(next, labels) {
      if (!next) return;
      const changing = game?.id !== next.id;
      if (!changing && (next.chat_updated_at || 0) < lastStamp) return;
      if (changing) {
        stopSpeech(); recorder?.cancel(); seen.clear(); key = ''; lastRequest = null;
        $('retryChat').hidden = true; error('');
      }
      game = next; names = labels || names; lastStamp = next.chat_updated_at || 0;
      const target = $('chatTarget').value;
      $('chatTarget').replaceChildren(new Option('在场的大家', ''));
      for (const p of game.players.filter(p => p !== 'user')) $('chatTarget').add(new Option(names[p] || p, p));
      if ([...$('chatTarget').options].some(o => o.value === target)) $('chatTarget').value = target;
      const messages = game.messages || [];
      const nextKey = JSON.stringify([game.id, messages, names]);
      if (key !== nextKey) {
        key = nextKey;
        const list = $('tableMessages'), bottom = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
        list.replaceChildren();
        for (const message of messages) {
          const row = document.createElement('article'); row.className = 'table-message ' + (message.sender === 'user' ? 'from-user' : 'from-ai');
          const author = document.createElement('strong'); author.textContent = names[message.sender] || message.sender;
          const meta = document.createElement('small'); meta.textContent = `第 ${message.round} 轮${message.event ? ' · ' + message.event : ''}`;
          const text = document.createElement('p'); text.textContent = message.text;
          const heading = document.createElement('div'); heading.className = 'message-heading';
          const avatar = document.createElement('img'); avatar.className = 'message-avatar'; avatar.alt = '';
          avatar.src = {user:'/public/UserIcon.png', aion:'/public/gropicon1.png', connor:'/public/codexicon.png'}[message.sender];
          heading.append(avatar, author, meta);
          row.append(heading, text);
          if (message.sender !== 'user') {
            const play = document.createElement('button'); play.type = 'button'; play.className = 'message-play';
            play.textContent = '▷ 播放'; play.setAttribute('aria-label', `播放 ${names[message.sender] || message.sender} 的这条消息`);
            play.onclick = () => speak(message, true); heading.append(play);
            if (!changing && !seen.has(message.id)) {
              if ($('roomChat').hidden) $('tableChatToggle').classList.add('unread');
              if ($('tableVoice').checked) speak(message);
            }
          }
          seen.add(message.id); list.append(row);
        }
        if (!messages.length) list.textContent = '随时说话，不用等轮到你。';
        if (changing || bottom) list.scrollTop = list.scrollHeight;
      }
      if (game.chat_error) error(game.chat_error);
      else if (sending || game.chat_busy) error('正在接话……');
      else if ($('chatState').textContent === '正在接话……') error('');
      $('sendChat').disabled = sending || !!game.chat_busy;
    }
    function ingest(data) {
      if (data.state?.id !== game?.id) return;
      update(data.state, data.names);
      onChatState(data.state);
    }
    async function poll() {
      if (!game || document.hidden || pollBusy) return;
      pollBusy = true;
      try { ingest(await request('/state')); } catch { /* The main table handles connection recovery. */ }
      finally { pollBusy = false; }
    }
    async function send(text, destination, retry = false) {
      if (sending || !destination?.id || destination.id !== game?.id) throw Error('牌桌已经切换，请在当前牌桌重新发送。');
      const message = retry ? lastRequest : {game_id:destination.id, message_id:'chat_' + (globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + '_' + Math.random().toString(36).slice(2)), text, target:destination.target || ''};
      if (!message) return;
      sending = true; lastRequest = message; $('retryChat').hidden = true; $('sendChat').disabled = true; error('正在接话……');
      try {
        const data = await request('/chat', message); ingest(data);
        if (data.state?.id === game?.id && data.state?.chat_error) $('retryChat').hidden = false;
        else { lastRequest = null; error(''); }
      } catch (e) { error(e.message); $('retryChat').hidden = false; throw e; }
      finally { sending = false; $('sendChat').disabled = !!game?.chat_busy; }
    }
    $('tableChatToggle').onclick = () => open($('roomChat').hidden);
    $('chatClose').onclick = $('chatDismiss').onclick = () => open(false);
    function tab(log) {
      $('roundLog').hidden = !log; $('tableMessages').hidden = log;
      $('showRoundLog').setAttribute('aria-pressed',String(log)); $('showChatLog').setAttribute('aria-pressed',String(!log));
    }
    $('showChatLog').onclick = () => tab(false);
    $('showRoundLog').onclick = () => tab(true);
    $('stopSpeech').onclick = stopSpeech;
    $('tableVoice').onchange = () => { localStorage.setItem('tablegames_tts', String($('tableVoice').checked)); if (!$('tableVoice').checked) stopSpeech(); };
    $('tableVoice').checked = localStorage.getItem('tablegames_tts') !== 'false';
    $('chatForm').onsubmit = async event => {
      event.preventDefault(); const text = $('tableChatInput').value.trim();
      if (!text || sending) return;
      if (!game) { error('先入座开一局，就能在这桌聊天了。'); return; }
      $('tableChatInput').value = '';
      try { await send(text, {id:game.id, target:$('chatTarget').value}); }
      catch { if (!lastRequest) $('tableChatInput').value = text; }
    };
    $('tableChatInput').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('chatForm').requestSubmit(); } };
    $('retryChat').onclick = () => { if (lastRequest) send(lastRequest.text, {id:lastRequest.game_id}, true).catch(() => {}); };
    recorder = window.TableGamesVoice.bind({button:$('tableVoiceHold'), hint:$('tableVoiceHint'),
      destination:() => game && !sending && !game.chat_busy && !document.hidden ? {id:game.id, target:$('chatTarget').value} : null,
      send:async (text, destination) => { try { await send(text, destination); } catch (e) { if (destination.id !== game?.id) $('tableChatInput').value = text; throw e; } },
      onRecording:active => { recording = active; if (active) stopSpeech(); window.TableAtmosphere?.sound.suspend(active); },
      onError:error});
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('roomChat').hidden) open(false); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { recorder.cancel(); stopSpeech(); } else poll(); });
    window.addEventListener('pagehide', () => { recorder.cancel(); stopSpeech(); clearInterval(pollTimer); });
    fetch('/api/chatroom/config').then(r => r.json()).then(config => { chatConfig = config; }).catch(() => error('音色设置暂时没能加载，文字聊天仍可用。'));
    pollTimer = setInterval(poll, 1500);
    return {update, stopSpeech, open};
  }
  window.TableCompanion = {bind};
})();

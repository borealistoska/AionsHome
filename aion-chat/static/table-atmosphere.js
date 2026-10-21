/* Presentation only: saved dice/cards are never rerolled or changed by animation. */
(() => {
  'use strict';
  const files = {
    dice:'/public/掷骰子.mp3', shuffle:'/public/card/新开局的洗牌声.mp3',
    deal:'/public/card/出牌.mp3', reveal:'/public/card/出牌.mp3',
    turn:'/public/card/轮到你了.mp3', win:'/public/card/结局赢了.mp3', lose:'/public/card/结局输了.mp3'
  };
  let muted = localStorage.getItem('tablegames_sound') === 'false', ducked = false, suspended = false;
  const playing = new Set();
  const sound = {
    play(type) {
      if (muted || suspended || document.hidden || !files[type]) return;
      const audio = new Audio(files[type]); audio.volume = ducked ? .12 : .5;
      playing.add(audio); const done = () => playing.delete(audio);
      audio.onended = audio.onerror = done; audio.play().catch(done);
    },
    stop() { for (const audio of playing) { audio.pause(); audio.currentTime = 0; } playing.clear(); },
    quiet(value) { ducked = value; for (const audio of playing) audio.volume = value ? .12 : .5; },
    suspend(value) { suspended = value; if (value) sound.stop(); }
  };
  const animations = new Set(), layers = new Set(), delays = new Set();
  let cancelled = false;
  const wait = ms => new Promise(resolve => {
    const finish = () => { clearTimeout(timer); delays.delete(finish); resolve(); };
    const timer = setTimeout(finish, ms); delays.add(finish);
  });
  async function motion(el, frames, options) {
    if (!el || cancelled) return;
    const animation = el.animate(frames, {fill:'both', easing:'cubic-bezier(.22,.7,.2,1)', ...options});
    animations.add(animation);
    try { await animation.finished; } catch { /* Page hidden or navigated away. */ }
    finally { animations.delete(animation); animation.cancel(); }
  }
  function layer(html) {
    const el = document.createElement('div'); el.className = 'ritual-layer'; el.setAttribute('aria-hidden','true');
    el.innerHTML = html; document.querySelector('.felt').append(el); layers.add(el); return el;
  }
  function clean() {
    for (const el of layers) el.remove(); layers.clear();
    document.querySelector('.game-stage')?.classList.remove('ritual-active','dice-rolling','cards-dealing');
    document.querySelectorAll('.game-stage [data-presenting]').forEach(el => { el.style.visibility = ''; delete el.dataset.presenting; });
  }
  function cancel() {
    cancelled = true; for (const animation of animations) animation.cancel();
    for (const finish of [...delays]) finish(); sound.stop(); clean();
  }
  function conceal(el) { el.dataset.presenting = 'yes'; el.style.visibility = 'hidden'; }
  function reveal(el) { el.style.visibility = 'visible'; }
  async function rollDice(state, die) {
    const stage = document.querySelector('.game-stage'); stage.classList.add('ritual-active','dice-rolling');
    const screen = layer('<div class="dice-cup"><span>小家的好运</span></div><div class="tumbling-dice"></div><p class="ritual-label">摇一摇，把心思藏进杯里。</p>');
    const cup = screen.querySelector('.dice-cup'), tray = screen.querySelector('.tumbling-dice');
    sound.play('dice');
    await motion(cup, [
      {transform:'translateY(15px) rotate(0deg)'}, {transform:'translate(-19px,-10px) rotate(-19deg)',offset:.18},
      {transform:'translate(21px,-19px) rotate(22deg)',offset:.36}, {transform:'translate(-20px,-4px) rotate(-23deg)',offset:.56},
      {transform:'translate(16px,-15px) rotate(18deg)',offset:.76}, {transform:'translateY(15px) rotate(0deg)'}
    ], {duration:1000});
    if (cancelled) return;
    tray.innerHTML = Array.from({length:state.seats.user.count || 5}, () => die(1 + Math.floor(Math.random()*6))).join('');
    const ticker = setInterval(() => { tray.innerHTML = Array.from({length:state.seats.user.count || 5}, () => die(1 + Math.floor(Math.random()*6))).join(''); }, 85);
    try {
      await Promise.all([
        motion(cup,[{transform:'translateY(15px)',opacity:1},{transform:'translateY(-80px) rotate(-12deg)',opacity:0}],{duration:350}),
        motion(tray,[{transform:'translateY(-20px) rotate(-12deg) scale(.65)',opacity:0},{transform:'translateY(14px) rotate(5deg) scale(1.12)',opacity:1,offset:.6},{transform:'translateY(4px) rotate(0deg) scale(1)',opacity:1}],{duration:650})
      ]);
    } finally { clearInterval(ticker); }
    if (cancelled) return;
    screen.remove(); layers.delete(screen); stage.classList.remove('ritual-active','dice-rolling');
    await Promise.all([...document.querySelectorAll('#selfSeat .die')].map((el,i) => motion(el,
      [{transform:'translateY(-25px) rotateX(150deg) rotateZ(-25deg)',opacity:0},{transform:'translateY(4px) rotateX(0deg) rotateZ(4deg)',opacity:1,offset:.8},{transform:'none',opacity:1}], {duration:380,delay:i*65})));
  }
  async function shuffle() {
    const screen = layer('<div class="shuffle-pack"><i></i><i></i><i></i><i></i></div><p class="ritual-label">洗一副牌，换一手好运。</p>');
    sound.play('shuffle');
    await Promise.all([...screen.querySelectorAll('i')].map((el,i) => motion(el,[
      {transform:`translate(${i*2}px,${-i*2}px) rotate(0deg)`},
      {transform:`translate(${i%2 ? 45 : -45}px,${i%2 ? -20 : 8}px) rotate(${i%2 ? 18 : -18}deg)`,offset:.4},
      {transform:`translate(${i%2 ? -35 : 35}px,${-i*3}px) rotate(${i%2 ? -12 : 12}deg)`,offset:.75},
      {transform:`translate(${i*2}px,${-i*2}px) rotate(0deg)`}
    ], {duration:850,delay:i*35})));
    screen.remove(); layers.delete(screen);
  }
  async function flyCard(target, delay = 0, flip = false) {
    await wait(delay); if (cancelled || !target) return;
    const rect = target.getBoundingClientRect(), source = document.querySelector('.felt').getBoundingClientRect();
    const ghost = document.createElement('span'); ghost.className = 'card back-card flying-card'; ghost.setAttribute('aria-hidden','true');
    Object.assign(ghost.style,{position:'fixed',left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});
    document.body.append(ghost); layers.add(ghost); sound.play('deal');
    await motion(ghost,[{transform:`translate(${source.left+source.width*.72-rect.left}px,${source.top+25-rect.top}px) rotate(-35deg) scale(.72)`,opacity:0},
      {opacity:1,offset:.15},{transform:'translate(0,0) rotate(0deg) scale(1)',opacity:1}],{duration:330});
    ghost.remove(); layers.delete(ghost); if (cancelled) return;
    reveal(target);
    if (flip) await motion(target,[{transform:'perspective(450px) rotateY(90deg)'},{transform:'perspective(450px) rotateY(0deg)'}],{duration:260});
  }
  async function deal(state) {
    const stage = document.querySelector('.game-stage'); stage.classList.add('ritual-active','cards-dealing');
    const targets = [];
    for (let i=0;i<2;i++) for (const p of state.players) {
      const card = document.querySelector(`[data-player="${p}"] .card:nth-child(${i+1})`);
      if (card) { conceal(card); targets.push(card); }
    }
    await shuffle(); stage.classList.remove('ritual-active');
    if (!cancelled) await Promise.all(targets.map((target,i) => flyCard(target,i*145,!target.classList.contains('back-card'))));
    stage.classList.remove('cards-dealing');
  }
  async function revealDice(previous, state) {
    const targets = [...document.querySelectorAll('#opponents .die')]; targets.forEach(conceal);
    const screen = layer('<div class="opening-cup dice-cup"><span>开！</span></div><p class="ritual-label">这一杯，到底有没有？</p>');
    sound.play('reveal');
    await motion(screen.querySelector('.dice-cup'),[{transform:'translateY(8px)'},{transform:'translateY(-90px) rotate(-18deg)',opacity:0}],{duration:650});
    screen.remove(); layers.delete(screen);
    await Promise.all(targets.map(async (target,i) => {
      await wait(i*65); if (cancelled) return; reveal(target);
      await motion(target,[{transform:'rotateX(100deg) scale(.75)',opacity:0},{transform:'none',opacity:1}],{duration:300});
    }));
  }
  async function boardCards(previous, next) {
    const cards = [...document.querySelectorAll('#centerpiece .card')];
    const newCards = cards.slice(previous.board.length, next.board.length); newCards.forEach(conceal);
    const showdownCards = next.phase !== 'playing' ? [...document.querySelectorAll('#opponents .card:not(.back-card)')] : [];
    showdownCards.forEach(conceal);
    for (let i=0;i<newCards.length;i++) {
      if (cancelled) return;
      await flyCard(newCards[i], i===0 ? 120 : 100, true);
    }
    await Promise.all(showdownCards.map(async (target,i) => {
      await wait(i*100); if (cancelled) return; reveal(target); sound.play('reveal');
      await motion(target,[{transform:'perspective(450px) rotateY(90deg)'},{transform:'perspective(450px) rotateY(0deg)'}],{duration:350});
    }));
  }
  async function present(previous, next, {die}) {
    if (!next || document.hidden) return;
    const newRound = !previous || previous.id !== next.id || previous.round !== next.round;
    const ended = previous?.phase === 'playing' && next.phase !== 'playing';
    const moreCards = next.kind === 'texas-holdem' && !newRound && (next.board.length > previous.board.length || ended);
    cancelled = false;
    try {
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
        if (newRound) sound.play(next.kind === 'liars-dice' ? 'dice' : 'shuffle');
      } else if (newRound) {
        document.getElementById('status').textContent = next.kind === 'liars-dice' ? '摇骰中……先别偷看。' : '洗牌、发牌……好运正在路上。';
        if (next.kind === 'liars-dice') await rollDice(next,die); else await deal(next);
      } else if (ended && next.kind === 'liars-dice') {
        document.getElementById('status').textContent = '开骰了，让我们看看。';
        await revealDice(previous,next);
      } else if (moreCards) {
        document.getElementById('status').textContent = next.phase !== 'playing' ? '亮牌，比一比这一手。' : '翻开新的公共牌……';
        await boardCards(previous,next);
      }
      if (!cancelled && ended) {
        const won = next.kind === 'liars-dice' ? next.result.loser !== 'user' : next.result.net.user > 0;
        sound.play(won ? 'win' : 'lose');
      } else if (!cancelled && !newRound && previous?.turn !== 'user' && next.turn === 'user') sound.play('turn');
    } finally { clean(); }
  }
  const toggle = document.getElementById('soundToggle');
  function updateToggle() { toggle.textContent = muted ? '静音' : '音效'; toggle.setAttribute('aria-pressed',String(!muted)); toggle.setAttribute('aria-label',muted ? '开启游戏音效' : '关闭游戏音效'); }
  toggle.onclick = () => { muted = !muted; localStorage.setItem('tablegames_sound',String(!muted)); if (muted) sound.stop(); updateToggle(); };
  updateToggle();
  document.addEventListener('visibilitychange',() => { if (document.hidden) cancel(); });
  window.addEventListener('pagehide',cancel);
  window.TableAtmosphere = {present,cancel,sound};
})();

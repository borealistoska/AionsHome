(function(root,factory){const api=factory(root.Billiards?.Physics||(typeof require==='function'?require('./physics.js'):null),root.Billiards?.Rules||(typeof require==='function'?require('./rules.js'):null),root.Billiards?.AI||(typeof require==='function'?require('./ai.js'):null));if(typeof module==='object')module.exports=api;else{(root.Billiards??={}).Game=api;if(!root.Billiards.deferMount)api.mount();}})(globalThis,function(P,R,AI){
  'use strict';
  function opponentDelay(random=Math.random){return 2000+random()*2000;}
  function shouldAnnounceTurn(previous,s){return previous!==undefined&&previous!==s.turn&&!!s.lastShot&&!['over','error'].includes(s.phase);}
  function cuePose(from,to,power,elapsed,total){
    const aiming=total-700,k=Math.max(0,Math.min(1,elapsed/aiming)),delta=Math.atan2(Math.sin(to-from),Math.cos(to-from));
    const angle=elapsed>=aiming?to:from+delta*k*k*(3-2*k)+Math.sin(5*Math.PI*k)*.025*(1-k)**2;
    const phase=elapsed>=total?'ready':elapsed<aiming?'aiming':elapsed<aiming+200?'settling':elapsed<total-150?'pullback':'stroke';
    const amplitude=10+power*.16,pullback=phase==='pullback'?amplitude*(elapsed-aiming-200)/350:phase==='stroke'?amplitude*(total-elapsed)/150:0;
    return{angle,phase,pullback};
  }
  function createController({scheduler=(fn,ms)=>setTimeout(fn,ms),delay=opponentDelay,onChange=()=>{},onEvent=()=>{},presentationDelay=1100,planner=AI.planTurn,initialState=R.newGame(17,0),autoPlay=true}={}){
    let state=structuredClone(initialState),world=null,start=null,activeShot=null,epoch=0,visible=true,thinking=false,acc=0,aiCue=null,pendingAction=null,aiLastAngle=0,submittedShotCount=0,presentation=null,pocketDrops=[],eventSerial=0;
    let config={difficulty:'medium',accuracy:80};
    const getState=()=>({...structuredClone(state),planning:thinking,aiCue:aiCue?structuredClone(aiCue):null,aiLastAngle,submittedShotCount,presentation:presentation?{events:structuredClone(presentation.events),foul:presentation.foul,shooter:presentation.shooter,elapsedMs:presentation.elapsedMs,durationMs:presentation.durationMs}:null,pocketDrops:structuredClone(pocketDrops)});
    function notifyEvent(type,data={}){try{onEvent({type,id:epoch+':'+(++eventSerial),...data});}catch{/* Optional presentation must never stop gameplay. */}}
    function emit(){onChange(getState());}
    function fail(reason){state.error=reason;emit();return{ok:false,reason};}
    function cancelPlan(){thinking=false;pendingAction=null;aiCue=null;}
    function preview(plan){
      if(!plan||plan.action?.type!=='shot'||plan.actor!==state.turn||state.phase!=='aim'){if(aiCue?.remote){aiLastAngle=aiCue.angle;cancelPlan();emit();}return;}
      if(aiCue?.remote&&aiCue.id===plan.id){aiCue.elapsedMs=Math.max(aiCue.elapsedMs,plan.elapsed_ms||0);return;}
      const from=aiCue?.angle??aiLastAngle;cancelPlan();thinking=true;
      aiCue={id:plan.id,remote:true,fromAngle:from,targetAngle:plan.action.shot.angle,targetPower:plan.action.shot.power,durationMs:plan.duration_ms,elapsedMs:plan.elapsed_ms||0};
      Object.assign(aiCue,cuePose(from,aiCue.targetAngle,aiCue.targetPower,aiCue.elapsedMs,aiCue.durationMs));emit();
    }
    function scheduleAI(){
      if(!autoPlay||!visible||thinking||state.turn!==1||['motion','presenting','over','error'].includes(state.phase))return;
      thinking=true;emit();const token=epoch;
      Promise.resolve().then(()=>{if(token!==epoch||!visible)return null;return planner(getState(),{...config,seed:state.seed+state.balls.filter(b=>b.pocketed).length*91+state.turn*13,cancelled:()=>token!==epoch||!visible});}).then(action=>{
        if(token!==epoch||!visible)return;
        if(!action)throw Error('算法未返回有效动作');
        const frozen=structuredClone(action),durationMs=Math.max(2000,Math.min(4000,delay()));
        if(frozen.type==='shot'){
          pendingAction=frozen;aiCue={fromAngle:aiLastAngle,targetAngle:frozen.shot.angle,targetPower:frozen.shot.power,durationMs,elapsedMs:0,...cuePose(aiLastAngle,frozen.shot.angle,frozen.shot.power,0,durationMs)};emit();
        }else scheduler(()=>{if(token!==epoch||!visible)return;thinking=false;const done=submit(frozen,1);if(!done.ok){state.phase='error';state.error='对手动作无效：'+done.reason;emit();}},durationMs);
      }).catch(e=>{if(token!==epoch||!visible)return;cancelPlan();state.phase='error';state.error='规划器异常，请重新开局：'+e.message;emit();});
    }
    function commit(n,quiet=false){const oldTurn=state.turn,oldPhase=state.phase;state=n;state.error=null;emit();if(!quiet){if(n.phase==='over'&&oldPhase!=='over')notifyEvent(n.result.winner===0?'victory':'defeat');else if(shouldAnnounceTurn(oldTurn,n))notifyEvent('turn-change');}scheduleAI();}
    function reset(seed=17,breaker=0,mode=state.mode||'casual'){epoch++;cancelPlan();presentation=null;pocketDrops=[];notifyEvent('reset');aiLastAngle=0;submittedShotCount=0;world=null;start=null;activeShot=null;acc=0;commit(R.newGame(seed,breaker,mode));}
    function submit(action,actor=0){
      if(!action||actor!==state.turn)return fail('现在不是你的回合。');if(['over','error'].includes(state.phase))return fail('本局已结束，请重新开局。');if(['motion','presenting'].includes(state.phase))return{ok:false,reason:'请等待这一杆的结果提示结束。'};
      const n=structuredClone(state);n.error=null;
      if(action.type==='decision'){if(n.phase!=='decision')return fail('现在没有开球选择。');const next=R.chooseDecision(n,action.id);if(next.error)return fail(next.error);commit(next);return{ok:true};}
      if(action.type==='spot'){if(n.phase!=='placement')return fail('只能在放置白球时请求移球。');const next=R.requestSpot(n,action.ballId);if(next.error)return fail(next.error);commit(next);return{ok:true};}
      if(action.type==='claim'){if(n.phase!=='aim')return fail('现在不能临时认领。');const next=R.claimGroup(n,action.group);if(next.error)return fail(next.error);commit(next);return{ok:true};}
      if(action.type==='place'){if(n.phase!=='placement')return fail('白球现在不能移动。');const check=R.validatePlacement(n,action.x,action.y);if(!check.ok)return fail(check.reason);Object.assign(n.balls.find(b=>b.id===0),{x:action.x,y:action.y});if(actor===1)n.phase='aim';commit(n);return{ok:true};}
      if(action.type==='confirmPlacement'){if(n.phase!=='placement')return fail('现在无需确认白球位置。');const cue=n.balls.find(b=>b.id===0),check=R.validatePlacement(n,cue.x,cue.y);if(!check.ok)return fail(check.reason);n.phase='aim';n.message='白球位置已确认。'+(n.mode==='casual'?'拖动瞄准，按住蓄力，松手击球。':'先报球报袋，再按住蓄力击球。');commit(n);return{ok:true};}
      if(action.type==='call'){if(n.phase!=='aim'||n.isBreak)return fail('这时无需报球报袋。');const call=action.call;
        if(call!==null){if(!call||typeof call!=='object'||Array.isArray(call)||(call.safety!==undefined&&typeof call.safety!=='boolean'))return fail('报球信息格式无效。');
          if(call.ballId!=null&&(!Number.isInteger(call.ballId)||!R.legalTargets(n).includes(call.ballId)))return fail('这颗球不是当前合法目标。');
          if(call.pocketId!=null&&(!Number.isInteger(call.pocketId)||call.pocketId<0||call.pocketId>5))return fail('指定球袋无效。');}
        n.call=call?.safety?{safety:true}:call===null?null:{ballId:call.ballId??null,pocketId:call.pocketId??null};commit(n);return{ok:true};}
      if(action.type==='shot'){
        if(pendingAction)return fail('对手正在准备出杆。');
        if(n.phase!=='aim')return fail('请先确认白球位置。');const shot={...action.shot,call:n.isBreak?null:(action.shot.call??n.call)},check=R.validateCall(n,shot.call);if(!check.ok)return fail(check.reason);
        try{world=P.createWorld(n.balls);P.strike(world,shot);}catch(e){world=null;return fail(e.message);}
        aiLastAngle=shot.angle;submittedShotCount++;n.lastSubmittedShot={angle:shot.angle,power:shot.power,actor};start=structuredClone(n);activeShot=shot;n.phase='motion';n.call=shot.call;n.message=actor===0?'正在击球，等所有球停下后判断结果。':'对手正在击球，与你使用完全相同的物理。';state=n;acc=0;pocketDrops=[];notifyEvent('cue-hit',{strength:.25+.75*shot.power/100,actor});emit();return{ok:true};
      }
      return fail('无法识别这个操作。');
    }
    function tick(elapsedMs){
      if(!visible)return;const dt=Math.max(0,Math.min(elapsedMs,50));
      for(const d of pocketDrops)d.elapsedMs+=dt;pocketDrops=pocketDrops.filter(d=>d.elapsedMs<d.durationMs);
      if(presentation){presentation.elapsedMs+=dt;if(presentation.elapsedMs>=presentation.durationMs){const next=presentation.next;presentation=null;commit(next);}return;}
      if(aiCue){aiCue.elapsedMs+=dt;Object.assign(aiCue,cuePose(aiCue.fromAngle,aiCue.targetAngle,aiCue.targetPower,aiCue.elapsedMs,aiCue.durationMs));if(aiCue.remote)return;if(pendingAction&&aiCue.phase==='ready'){const action=pendingAction;aiLastAngle=aiCue.targetAngle;cancelPlan();const done=submit(action,1);if(!done.ok){state.phase='error';state.error=done.reason;emit();}}else return;}
      if(!world)return;acc+=dt/1000;
      try{while(acc>=P.DT&&world){acc-=P.DT;const cursor=world.events.length,before=new Map(world.balls.map(b=>[b.id,{x:b.x,y:b.y,vx:b.vx,vy:b.vy}])),settled=P.step(world);state.balls=world.balls;
        for(const e of world.events.slice(cursor)){
          if(e.type==='contact'){const speed=e.ids.reduce((n,id)=>{const b=before.get(id);return n+Math.hypot(b.vx,b.vy);},0);notifyEvent('ball-hit',{strength:Math.max(.1,Math.min(1,speed/5)),pair:e.ids.join('-'),ballIds:e.ids,physicsTime:e.time});}
          if(e.type==='pocket'){const ballId=e.ids[0],origin=before.get(ballId);pocketDrops.push({ballId,pocketId:e.pocketId,x:origin.x,y:origin.y,elapsedMs:0,durationMs:620});notifyEvent('pocket-drop',{ballId,pocketId:e.pocketId,physicsTime:e.time});}
        }
        if(world.steps>18000)throw Error('这一杆未在合理时间内停止');
        if(settled){const sim={balls:world.balls,events:world.events,settled:true};world=null;const n=R.applyShot(start,activeShot,sim);if(n.error)throw Error(n.error);
          const events=n.lastShot?.pocketEvents||[];
          if(events.length&&presentationDelay>0){presentation={next:n,events:structuredClone(events),foul:n.lastShot.foul,shooter:n.lastShot.shooter,elapsedMs:0,durationMs:presentationDelay};state.phase='presenting';state.lastShot=n.lastShot;state.shotCount=n.shotCount;state.message='这一杆入袋结果';state.error=null;emit();}
          else commit(n);
        }
      }}catch(e){world=null;cancelPlan();presentation=null;pocketDrops=[];state.phase='error';state.error='模拟暂停，请重新开局：'+e.message;emit();}
    }
    function setVisible(v){if(!v&&visible){epoch++;if(aiCue)aiLastAngle=aiCue.angle;cancelPlan();pocketDrops=[];if(presentation){state=presentation.next;presentation=null;}notifyEvent('pause');}visible=!!v;acc=0;if(visible)scheduleAI();else emit();}
    function configure(c){config={...config,...c};}
    function requestStalemate(){epoch++;cancelPlan();presentation=null;pocketDrops=[];notifyEvent('reset');world=null;acc=0;commit(R.stalemate(state));}
    function restore(snapshot){epoch++;if(aiCue)aiLastAngle=aiCue.angle;cancelPlan();world=null;start=null;activeShot=null;presentation=null;pocketDrops=[];acc=0;state=structuredClone(snapshot);emit();}
    return{getState,reset,submit,tick,setVisible,configure,requestStalemate,restore,preview,hasAnimation:()=>!!(world||presentation||aiCue&&aiCue.phase!=='ready'||pocketDrops.length)};
  }
  function mount(host=null){
    const $=id=>document.getElementById(id),V=Billiards.Renderer,C=Billiards.Controls,canvas=$('table'),ctx=canvas.getContext('2d'),charge=C.createCharge();
    const audio=Billiards.Audio.createAudio();
    const seatName=seat=>host?.playerName?.(seat)||(seat===0?'你':'对手');
    const portrait=()=>!!host&&matchMedia('(max-width:760px) and (orientation:portrait)').matches;
    function sizeTable(){
      if(portrait()){
        const stage=canvas.parentElement,box=stage.getBoundingClientRect(),crop=V.PORTRAIT_CROP;
        const scale=Math.max(0,Math.min(box.height/crop.w,box.width/crop.h));
        canvas.style.width=V.VIEW.w*scale+'px';
        canvas.style.top=crop.w*scale/2+'px';
        stage.style.setProperty('--portrait-table-height',crop.w*scale+'px');
      }else{canvas.style.width='';canvas.style.top='';canvas.parentElement.style.removeProperty('--portrait-table-height');}
    }
    new ResizeObserver(sizeTable).observe(canvas.parentElement);
    const dialogs=['helpDialog','limitsDialog','settingsDialog','endDialog','potsDialog'];let ctrl,current,angle=0,power=0,last=0,seed=17,guide=$('guide').value,aimGesture=null,fineTimer=null,lockedAngle=0,feedback='',spaceCaptured=false,potHistory=[],feedKey='',endKey='',previousTurn,turnTimer=null;
    const pocketNames=['左上','上中','右上','右下','下中','左下'];
    function modalOpen(){return dialogs.some(id=>$(id).open);}
    function canAim(){return (!host||host.canControl())&&current?.turn===0&&current.phase==='aim'&&!current.planning&&!modalOpen();}
    function canShoot(){return canAim()&&R.validateCall(current,current.call).ok;}
    function stopFine(){clearTimeout(fineTimer);fineTimer=null;}
    let drawnPower=-1,chargeKey='',renderDirty=true,drawnAngle,drawnGuide,drawnPortrait;
    function setPower(n){power=n;if(drawnPower===n)return;drawnPower=n;renderDirty=true;$('powerValue').innerHTML=n+'<span>%</span>';$('powerFill').style.width=n+'%';$('powerMarker').style.left=n+'%';$('powerGauge').setAttribute('aria-valuenow',n);}
    function refreshCharge(){const active=charge.active();setPower(active?charge.power(performance.now()):0);const key=[active,current?.phase,current?.turn,feedback,canAim()].join(':');if(key===chargeKey)return;chargeKey=key;renderDirty=true;document.body.classList.toggle('charging',active);$('shootLabel').textContent=current?.phase==='presenting'?'入袋结果':active?'松手出杆':current?.turn===1?'对手回合':current?.phase==='motion'?'球正在运动':'按住蓄力';$('shootBtn').querySelector('small').textContent=current?.phase==='presenting'?'提示后继续':current?.turn===1?'请等待对手出杆':current?.phase==='motion'?'停稳后继续':'松手击球';$('chargeHint').textContent=current?.phase==='presenting'?'等待本杆提示结束':active?'升满回落 · 持续往返':current?.turn===1?'对手瞄准中，请稍候':current?.phase==='motion'?'等待球停稳':feedback||'按住蓄力 · 松手击球';$('aimLeft').disabled=$('aimRight').disabled=!canAim()||active;}
    function cancelInput(clear=false,reason=''){const active=charge.active();charge.cancel(clear);aimGesture=null;stopFine();if(active&&reason)feedback=reason;refreshCharge();}
    function renderPots(s){
      if(!s.lastShot){potHistory=[];feedKey='';endKey='';}else{const key=s.seed+':'+s.shotCount;if(feedKey!==key){feedKey=key;const shot=s.lastShot;
        for(const e of shot.pocketEvents||[]){const id=e.ballId,group=R.group(id),assigned=s.groups[shot.shooter],incidental=id!==0&&id!==8&&((assigned&&group!==assigned)||(s.mode==='strict'&&(shot.called?.safety||shot.called?.ballId!==id||shot.called?.pocketId!==e.pocketId)));
          const type=id===0?'白球':id===8?'黑八':group==='solids'?'全色':'花色',tag=id===0?' · 洗袋':shot.foul?' · 犯规入袋':incidental?' · 附带入袋':'';
          potHistory.push({ballId:id,text:`${seatName(shot.shooter)} · ${type}${id||''} → ${e.pocketId+1}号袋（${pocketNames[e.pocketId]}）${tag}`});}
        potHistory=potHistory.slice(-12);}}
      const draw=(node,entries)=>{node.replaceChildren();for(const e of entries){const row=document.createElement('li'),ball=document.createElement('span'),label=document.createElement('span');ball.className='pot-ball';ball.style.setProperty('--pot-color',V.color(e.ballId));ball.textContent=e.ballId||'白';label.textContent=e.text;row.append(ball,label);node.append(row);}};
      $('potFeed').hidden=!potHistory.length;draw($('recentPots'),potHistory.slice(-3).reverse());draw($('allPots'),[...potHistory].reverse());$('potHistoryBtn').textContent=`本局入袋记录（${potHistory.length}）`;
    }
    function presentEnd(s){if(s.phase!=='over')return;const key=s.seed+':'+s.shotCount;if(endKey===key)return;endKey=key;const won=s.result.winner===0;$('endDialog').dataset.outcome=won?'win':'loss';$('endTitle').textContent=won?'漂亮，你赢了！':'这局输了，再来一次。';$('endReason').textContent=s.result.reason;$('endStats').textContent=`本局 ${s.shotCount} 杆 · ${won?'好球值得庆祝':'下一杆，重新出发'}`;openDialog('endDialog');}
    function update(s){
      renderDirty=true;
      if(s.presentation||document.hidden||modalOpen()||!s.lastShot||s.phase==='over'){clearTimeout(turnTimer);$('turnBanner').hidden=true;}
      else if(shouldAnnounceTurn(previousTurn,s)){clearTimeout(turnTimer);const banner=$('turnBanner');banner.textContent=seatName(s.turn)+'的回合';banner.hidden=false;banner.style.animation='none';void banner.offsetWidth;banner.style.animation='';turnTimer=setTimeout(()=>banner.hidden=true,1300);}
      previousTurn=s.turn;current=s;const summary=$('pocketSummary');summary.hidden=!s.presentation;
      if(s.presentation){summary.replaceChildren();const title=document.createElement('strong');title.textContent=seatName(s.presentation.shooter)+'这一杆';summary.append(title);const entries=s.presentation.events.map(e=>e.ballId===0?'白球 → '+(e.pocketId+1)+'号袋（洗袋）':(e.ballId===8?'黑八':R.groupName(R.group(e.ballId)))+' '+e.ballId+' → '+(e.pocketId+1)+'号袋（'+pocketNames[e.pocketId]+'）');const line=document.createElement('span');line.textContent=entries.join(' · ');summary.append(line);if(s.presentation.foul){const note=document.createElement('small');note.textContent='本杆犯规：'+s.presentation.foul;summary.append(note);}}
      document.body.dataset.phase=s.phase;document.body.dataset.mode=s.mode;document.body.classList.toggle('is-break',s.isBreak);document.body.classList.toggle('game-over',s.phase==='over');
      const human=s.turn===0,aim=s.phase==='aim',place=s.phase==='placement',over=s.phase==='over',busy=!human||['motion','presenting'].includes(s.phase)||s.planning,strict=s.mode==='strict',targets=R.legalTargets(s),g=s.groups[s.turn]||s.tempGroup;
      if(!human||!['aim','placement'].includes(s.phase)||over)cancelInput(false);else if(!aim&&charge.active())cancelInput(false);$('humanGroup').textContent=R.groupName(s.groups[0]);$('aiGroup').textContent=R.groupName(s.groups[1]);$('humanCard').classList.toggle('active',human);$('aiCard').classList.toggle('active',!human);
      $('modeBadge').textContent=strict?'严格 · 报球报袋':'休闲八球';$('phaseBadge').textContent=over?'本局结束':s.phase==='presenting'?'入袋结果':s.phase==='motion'?'球在运动':s.aiCue?'对手瞄准中':s.planning?'对手准备中':place?'放置白球':s.phase==='decision'?'开球选择':s.isBreak?'准备开球':'瞄准击球';
      $('turnTitle').textContent=over?(s.result.winner===0?'你赢了！':'对手赢了'):s.phase==='error'?'需要重开':human?'轮到你了':'对手回合';
      let objective=over?(host?seatName(s.result.winner)+'赢了这局。':s.result.winner===0?'好球！再来一局。':'本局结束，再试一次吧。'):place?(s.placement==='head'?'在左侧开球区放白球。':'自由球：在桌内放白球。'):s.phase==='decision'?'请选择开球处理方式。':s.isBreak?'瞄准球阵，按住蓄力开球。':targets.includes(8)?'本组已清完，现在打黑八。':g?`打进${R.groupName(g)}，合法进球可继续。`:strict?'开放球桌：先报球报袋。':'开放球桌：首个合法进球分组。';
      $('objective').textContent=objective;$('statusText').textContent=s.error||(s.turn===1&&s.planning?'对手正在瞄准与准备出杆，请稍候。':s.message);$('statusIcon').textContent=s.error?'!':over?'★':place?'8':'◇';
      document.querySelector('.call-section').hidden=!strict||!aim||s.isBreak;$('ballSelect').replaceChildren(new Option(s.call?.safety?'安全球':'选择目标球',''),...targets.filter(id=>!s.isBreak).map(id=>new Option(id+' 号'+(id===8?' · 黑八':''),id)));$('ballSelect').value=s.call?.ballId??'';$('pocketSelect').value=s.call?.pocketId??'';$('safetyCheck').checked=!!s.call?.safety;
      $('ballSelect').disabled=$('pocketSelect').disabled=busy||!aim||!!s.call?.safety;$('safetyCheck').disabled=busy||!aim;$('callConfirm').textContent=s.call?.safety?'安全球：打完交换回合':s.call?.ballId&&Number.isInteger(s.call.pocketId)?`${s.call.ballId} 号 → ${pocketNames[s.call.pocketId]}`:'选球和球袋后可击球';
      $('placeBtn').hidden=!place;$('placeBtn').disabled=!human;$('shootBtn').hidden=place||s.phase==='decision';$('shootBtn').disabled=!canShoot();document.querySelector('.power-section').hidden=place||s.phase==='decision';$('cancelHint').hidden=place||s.phase==='decision';document.querySelector('.fine-controls').hidden=place||s.phase==='decision';
      $('keyboardHint').textContent=place?'拖动白球 · 确认位置':host?'拖动球杆一侧瞄准 · ←/→ 或 A/D 微调':'拖动瞄准 · ←/→ 或 A/D 微调 · 空格按住/松开';
      const d=$('decisionPanel');d.hidden=s.phase!=='decision'||!human||!!(host&&!host.canControl());d.replaceChildren();if(!d.hidden&&s.decision){const p=document.createElement('p');p.textContent=s.message;d.append(p);for(const option of s.decision.options){const b=document.createElement('button');b.className='quiet';b.textContent=option.label;b.onclick=()=>ctrl.submit({type:'decision',id:option.id});d.append(b);}}
      const special=$('specialActions');special.replaceChildren();if(human){for(const id of R.spotCandidates(s)){const b=document.createElement('button');b.className='quiet special-button';b.textContent=`申请移出 ${id} 号球`;b.onclick=()=>ctrl.submit({type:'spot',ballId:id});special.append(b);}if(strict&&aim)for(const claim of R.claimable(s)){if(s.tempGroup===claim)continue;const b=document.createElement('button');b.className='quiet special-button';b.textContent='认领空组，打黑八';b.onclick=()=>ctrl.submit({type:'claim',group:claim});special.append(b);}}
      const hg=s.groups[0];$('trayLabel').textContent=R.groupName(hg);const ids=hg?Array.from({length:7},(_,i)=>hg==='solids'?i+1:i+9):Array.from({length:15},(_,i)=>i+1).filter(id=>id!==8),tray=$('remainingBalls');tray.replaceChildren();for(const id of ids){const ball=s.balls.find(o=>o.id===id),badge=document.createElement('span');badge.className='mini-ball'+(id>8?' stripe':'')+(ball.pocketed?' dim':'');badge.style.setProperty('--ball-color',V.color(id));badge.title=id+' 号';const number=document.createElement('span');number.textContent=id;badge.append(number);tray.append(badge);}
      $('trayNote').textContent=hg?`还剩 ${R.remaining(s,hg).length} 颗`:'先合法进球分组';$('stalemateBtn').disabled=['motion','presenting'].includes(s.phase)||over||s.phase==='error';$('difficulty').disabled=$('accuracy').disabled=$('accuracyReset').disabled=busy||over;refreshCharge();renderPots(s);presentEnd(s);
    }
    ctrl=createController({autoPlay:!host,initialState:host?.state??R.newGame(17,0),onChange:s=>{update(s);host?.onState?.(s);},onEvent:e=>{if(e.type==='reset')audio.reset();else if(e.type==='pause')audio.stop();else audio.play(e);}});
    const localSubmit=ctrl.submit;
    if(host)ctrl.submit=(action,actor=0)=>{
      if(!host.canControl())return{ok:false,reason:'球局已暂停或正在等待保存'};
      if(['place','call'].includes(action.type))return localSubmit(action,actor);
      host.action(action,ctrl.getState());return{ok:true};
    };
    Billiards.session={controller:ctrl,audio,get aim(){return angle;},get power(){return power;},get charging(){return charge.active();},cancelCharge:()=>cancelInput(true,'已取消 · 重新按住蓄力'),
      load:s=>{cancelInput(true);ctrl.restore(s);},
      preview:plan=>ctrl.preview(plan),
      play:(before,action)=>{cancelInput(true);ctrl.restore(before);if(action.type==='shot'){angle=action.shot.angle;localSubmit(action,before.turn);}},
      refresh:()=>update(ctrl.getState())};
    const point=e=>C.tablePoint(e.clientX,e.clientY,canvas.getBoundingClientRect(),V.VIEW,portrait());
    const inside=(e,element)=>{const r=element.getBoundingClientRect();return e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom;};
    function fire(p){feedback='';ctrl.submit({type:'shot',shot:{angle:lockedAngle,power:p,call:current.mode==='strict'?current.call:null}});}
    document.addEventListener('pointerdown',e=>audio.unlock(e),true);document.addEventListener('keydown',e=>{if(['Space','Enter'].includes(e.code))audio.unlock(e);},true);
    document.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse'&&e.button!==0)return;const button=e.target.closest('#shootBtn'),began=charge.down(e.pointerId,performance.now(),!!button&&canShoot());if(began){feedback='';lockedAngle=angle;$('shootBtn').setPointerCapture(e.pointerId);e.preventDefault();}else if(!charge.allowsAim(e.pointerId)){aimGesture=null;stopFine();feedback='多指触控已取消 · 抬手后重试';}refreshCharge();},true);
    document.addEventListener('pointermove',e=>{if(charge.owner()===e.pointerId){charge.move(e.pointerId,inside(e,$('shootBtn')));if(!charge.active())feedback='已取消 · 重新按住蓄力';refreshCharge();}},true);
    document.addEventListener('pointerup',e=>{const confirm=current?.phase==='placement'&&current.turn===0&&!modalOpen()&&e.target.closest('#placeBtn')&&charge.allowsAim(e.pointerId)&&inside(e,$('placeBtn'));const p=charge.up(e.pointerId,performance.now(),inside(e,$('shootBtn')),canShoot()&&$('shootBtn').hasPointerCapture(e.pointerId));if(aimGesture?.id===e.pointerId)aimGesture=null;stopFine();if(confirm)ctrl.submit({type:'confirmPlacement'});else if(p!==null)fire(p);refreshCharge();},true);
    document.addEventListener('pointercancel',e=>{cancelInput(false,'已取消 · 重新按住蓄力');charge.up(e.pointerId,performance.now(),false,false);},true);
    document.addEventListener('lostpointercapture',e=>{if(charge.owner()===e.pointerId||aimGesture?.id===e.pointerId)cancelInput(false,'已取消 · 重新按住蓄力');},true);
    canvas.addEventListener('contextmenu',e=>e.preventDefault());$('shootBtn').addEventListener('contextmenu',e=>e.preventDefault());$('shootBtn').onclick=e=>e.preventDefault();
    canvas.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse'&&e.button!==0)return;if(!charge.allowsAim(e.pointerId)||current.turn!==0||modalOpen()||!['aim','placement'].includes(current.phase))return;canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);aimGesture={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false};if(current.phase==='placement')ctrl.submit({type:'place',...point(e)});else if(canAim()){const cue=current.balls.find(b=>b.id===0);angle=C.aimAngle(cue,point(e),angle,!!host);}});
    canvas.addEventListener('pointermove',e=>{if(charge.active()||current.turn!==0||modalOpen())return;const cue=current.balls.find(b=>b.id===0),p=point(e);if(aimGesture?.id===e.pointerId&&charge.allowsAim(e.pointerId)){if(Math.hypot(e.clientX-aimGesture.x,e.clientY-aimGesture.y)>=6)aimGesture.moved=true;if(current.phase==='placement')ctrl.submit({type:'place',...p});else if(canAim()&&aimGesture.moved)angle=C.aimAngle(cue,p,angle,!!host);}else if(e.pointerType==='mouse'&&e.buttons===0&&canAim())angle=C.aimAngle(cue,p,angle,!!host);});
    function fine(delta){if(canAim()&&!charge.active())angle+=delta*.25*Math.PI/180;}
    for(const [id,delta]of[['aimLeft',-1],['aimRight',1]]){$(id).addEventListener('pointerdown',e=>{if(!charge.allowsAim(e.pointerId)||!canAim())return;e.preventDefault();fine(delta);stopFine();const repeat=()=>{if(canAim()&&!charge.active()){fine(delta);fineTimer=setTimeout(repeat,70);}};fineTimer=setTimeout(repeat,250);});$(id).onclick=e=>{if(e.detail===0)fine(delta);};}
    document.addEventListener('keydown',e=>{if(e.key==='Escape')cancelInput(true,'已取消 · 重新按住蓄力');const editing=e.target.closest('input,select,textarea,[contenteditable="true"],#tableVoiceHold');if(e.code==='Space'&&!editing&&!modalOpen()&&['aim','placement','motion','decision'].includes(current?.phase)){e.preventDefault();spaceCaptured=true;if(!e.repeat&&charge.down('keyboard',performance.now(),canShoot())){lockedAngle=angle;feedback='';refreshCharge();}}},true);
    document.addEventListener('keyup',e=>{if(e.code==='Space'&&spaceCaptured){e.preventDefault();spaceCaptured=false;const p=charge.up('keyboard',performance.now(),true,canShoot());if(p!==null)fire(p);refreshCharge();}},true);
    canvas.addEventListener('keydown',e=>{if(current.turn!==0||modalOpen()||charge.active())return;const key=e.code==='KeyA'?'ArrowLeft':e.code==='KeyD'?'ArrowRight':e.key;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(key)){e.preventDefault();if(current.phase==='placement'){const cue=current.balls.find(b=>b.id===0),d=e.shiftKey?.002:.012;ctrl.submit({type:'place',x:cue.x+(key==='ArrowLeft'?-d:key==='ArrowRight'?d:0),y:cue.y+(key==='ArrowUp'?-d:key==='ArrowDown'?d:0)});}else if(canAim()&&(key==='ArrowLeft'||key==='ArrowRight'))angle+=(key==='ArrowLeft'?-1:1)*(e.shiftKey?.1:.5)*Math.PI/180;}else if(key==='Enter'&&current.phase==='placement'){e.preventDefault();ctrl.submit({type:'confirmPlacement'});}});
    $('placeBtn').onclick=e=>{if(e.detail===0&&current.phase==='placement')ctrl.submit({type:'confirmPlacement'});canvas.focus({preventScroll:true});};
    function callChange(){ctrl.submit({type:'call',call:{ballId:$('ballSelect').value===''?null:+$('ballSelect').value,pocketId:$('pocketSelect').value===''?null:+$('pocketSelect').value}});}
    $('ballSelect').onchange=callChange;$('pocketSelect').onchange=callChange;$('safetyCheck').onchange=e=>ctrl.submit({type:'call',call:e.target.checked?{safety:true}:null});$('guide').onchange=e=>guide=e.target.value;
    function config(){ctrl.configure({difficulty:$('difficulty').value,accuracy:+$('accuracy').value});$('accuracyValue').textContent=$('accuracy').value+'%';}
    $('soundVolume').oninput=e=>{audio.configure({volume:+e.target.value/100});$('soundVolumeValue').textContent=e.target.value+'%';};$('soundMute').onchange=e=>audio.configure({muted:e.target.checked});
    $('accuracy').oninput=config;$('accuracyReset').onclick=()=>{$('accuracy').value=AI.defaults[$('difficulty').value];config();};$('difficulty').onchange=()=>{$('accuracy').value=AI.defaults[$('difficulty').value];config();};
    function restartGame(){clearTimeout(turnTimer);$('turnBanner').hidden=true;previousTurn=undefined;cancelInput(true);feedback='';angle=0;endKey='';for(const id of dialogs)if($(id).open)$(id).close();refreshVisibility();if(host){host.restart();return;}seed++;ctrl.reset(seed,+$('breaker').value,$('rulesMode').value);}
    $('restartBtn').onclick=restartGame;$('playAgainBtn').onclick=restartGame;$('stalemateBtn').onclick=()=>{cancelInput(true);if(host)host.restart();else ctrl.requestStalemate();};
    function refreshVisibility(){audio.setForeground(!document.hidden);const visible=!document.hidden&&!modalOpen();if(!visible)cancelInput(true,'已取消 · 重新按住蓄力');ctrl.setVisible(visible);}
    function openDialog(id){cancelInput(true);$(id).showModal();refreshVisibility();}
    $('helpBtn').onclick=()=>openDialog('helpDialog');$('settingsBtn').onclick=()=>openDialog('settingsDialog');$('limitsBtn').onclick=()=>openDialog('limitsDialog');$('potHistoryBtn').onclick=()=>openDialog('potsDialog');document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());for(const id of dialogs)$(id).addEventListener('close',()=>{refreshVisibility();if(!modalOpen())canvas.focus({preventScroll:true});});
    document.addEventListener('visibilitychange',()=>{last=0;refreshVisibility();});window.addEventListener('blur',()=>cancelInput(true,'已取消 · 重新按住蓄力'));window.addEventListener('resize',()=>cancelInput(true,'尺寸改变 · 请重新蓄力'));window.addEventListener('orientationchange',()=>cancelInput(true,'已取消 · 重新按住蓄力'));
    function updateAvatar(s){const avatar=$('opponentAvatar');if(!s.aiCue){avatar.hidden=true;return;}const rect=canvas.getBoundingClientRect(),vertical=portrait(),pose=V.avatarPose(s,s.aiCue?.angle??0,(vertical?rect.height:rect.width)/V.VIEW.w);avatar.hidden=!pose;if(!pose)return;
      if(pose.docked){if(avatar.parentElement!==$('aiCard'))$('aiCard').prepend(avatar);avatar.classList.add('docked');avatar.style.left=avatar.style.top='';}
      else{const stage=canvas.parentElement,box=stage.getBoundingClientRect();if(avatar.parentElement!==stage)stage.append(avatar);avatar.classList.remove('docked');avatar.style.left=(rect.left-box.left+(vertical?1-pose.y/V.VIEW.h:pose.x/V.VIEW.w)*rect.width)+'px';avatar.style.top=(rect.top-box.top+(vertical?pose.x/V.VIEW.w:pose.y/V.VIEW.h)*rect.height)+'px';}}
    function frame(t){
      requestAnimationFrame(frame);
      if(document.hidden||host?.canPresent?.()===false){last=0;return;}
      const moving=ctrl.hasAnimation();
      ctrl.tick(last?t-last:0);last=t;
      if(charge.active())refreshCharge();
      const vertical=portrait();
      if(!moving&&!renderDirty&&drawnAngle===angle&&drawnGuide===guide&&drawnPortrait===vertical)return;
      const s=ctrl.getState();s.targets=['aim','placement'].includes(s.phase)?R.legalTargets(s):[];
      const automated=!!s.aiCue||s.turn===1||host?.isAI?.(s.turn);
      V.draw(ctx,s,{aim:automated?(s.aiCue?.angle??s.aiLastAngle):angle,power:automated?(s.aiCue?.targetPower??0):power,cuePullback:automated?(s.aiCue?.pullback??0):charge.active()?power*.36:0,charging:!automated&&charge.active(),guide,portrait:vertical});updateAvatar(s);
      renderDirty=false;drawnAngle=angle;drawnGuide=guide;drawnPortrait=vertical;
    }
    if(host)ctrl.restore(host.state);else ctrl.reset(seed,0,'casual');config();requestAnimationFrame(frame);if(!host)openDialog('helpDialog');
  }
  return{createController,mount,opponentDelay,cuePose,shouldAnnounceTurn};
});

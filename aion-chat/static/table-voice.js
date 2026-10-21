/* Table chat uses the same native microphone and configured ASR endpoint as chatroom. */
(() => {
  'use strict';
  function nativeBridge(){for(const surface of [window,window.parent,window.top])try{if(surface.AionAudio)return surface.AionAudio;}catch{}return null;}
  function wav(chunks){
    const pcm=chunks.map(chunk=>Uint8Array.from(atob(chunk),c=>c.charCodeAt(0))),size=pcm.reduce((n,b)=>n+b.length,0);
    const header=new ArrayBuffer(44),v=new DataView(header);
    const label=(offset,text)=>[...text].forEach((c,i)=>v.setUint8(offset+i,c.charCodeAt(0)));
    label(0,'RIFF');v.setUint32(4,36+size,true);label(8,'WAVE');label(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
    v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);label(36,'data');v.setUint32(40,size,true);
    return new Blob([header,...pcm],{type:'audio/wav'});
  }
  function bind({button,hint,destination,send,onRecording=()=>{},onError=()=>{}}){
    let session=null,pending=null,processing=false,transcription=null;
    function show(text,recording=false){const label=button.querySelector('.voice-label');if(label)label.textContent=text;else button.textContent=text;button.title=text;button.setAttribute('aria-label',text);button.classList.toggle('recording',recording);button.setAttribute('aria-pressed',String(recording));}
    function restoreCallbacks(s){for(const [surface,previous]of s.callbacks||[])try{if(surface._voiceNativeOnChunk===s.callback)surface._voiceNativeOnChunk=previous;}catch{}}
    function release(s){
      clearTimeout(s.limit);s.stream?.getTracks().forEach(track=>track.stop());
      if(s.nativeOwned){s.nativeOwned=false;try{s.bridge.stop();}catch{}}
      restoreCallbacks(s);
    }
    function cancel(){
      const s=session;session=null;
      if(pending){pending.cancelled=true;release(pending);onRecording(false);}
      if(s){if(s.recorder?.state==='recording')s.recorder.stop();release(s);onRecording(false);}
      transcription?.abort();show('按住说话');hint.textContent='松手发送 · 滑开取消';
    }
    async function start(pointer,x,y){
      if(session||processing)return;
      const target=destination();if(!target){onError('正在接话或当前无法发言，请稍候再试');return;}
      const s={pointer,x,y,target,chunks:[],started:0,cancelled:false};session=s;
      show('正在开启麦克风…',true);hint.textContent='滑开取消';onRecording(true);
      try{
        s.bridge=nativeBridge();
        if(s.bridge){
          if(s.bridge.isRecording?.())throw Error('麦克风正在使用中，请稍候再试');
          s.callback=chunk=>{if(session===s)s.chunks.push(chunk);};s.callbacks=[];
          for(const surface of new Set([window,window.parent,window.top]))try{s.callbacks.push([surface,surface._voiceNativeOnChunk]);surface._voiceNativeOnChunk=s.callback;}catch{}
          if(!s.bridge.start())throw Error('麦克风未能开启，请检查手机的录音权限');
          s.nativeOwned=true;
        }else{
          if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('请在小家手机应用或支持录音的 HTTPS 页面中使用');
          s.stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
          // Releasing while the permission prompt is open must not leave a microphone running.
          if(session!==s){release(s);return;}
          const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
          s.recorder=mime?new MediaRecorder(s.stream,{mimeType:mime}):new MediaRecorder(s.stream);
          s.recorder.ondataavailable=e=>{if(e.data.size)s.chunks.push(e.data);};s.recorder.start();
        }
        s.started=performance.now();show('松开发送',true);
        s.limit=setTimeout(()=>finish(s.pointer),60000);
      }catch(e){if(session===s){cancel();onError(e.name==='NotAllowedError'?'请允许麦克风权限后再按住说话':e.message);}else release(s);}
    }
    async function finish(pointer){
      const s=session;if(!s||s.pointer!==pointer)return;
      if(s.cancelled||!s.started||performance.now()-s.started<500){cancel();return;}
      session=null;pending=s;processing=true;button.disabled=true;clearTimeout(s.limit);show('正在转文字…');hint.textContent='识别后自动发送';
      try{
        let blob;
        if(s.nativeOwned)blob=wav(s.chunks);
        else{await new Promise(resolve=>{s.recorder.onstop=resolve;s.recorder.stop();});blob=new Blob(s.chunks,{type:s.recorder.mimeType||'audio/webm'});}
        release(s);onRecording(false);
        if(s.cancelled)return;
        if(blob.size<=44)throw Error('没有录到声音，再按住试一次');
        const form=new FormData(),ext=blob.type.includes('wav')?'wav':blob.type.includes('mp4')?'m4a':'webm';form.append('file',blob,'voice.'+ext);
        const aborter=new AbortController();transcription=aborter;
        const response=await fetch('/api/voice/transcribe',{method:'POST',body:form,signal:aborter.signal});
        const result=await response.json();if(aborter.signal.aborted||s.cancelled)return;
        if(!response.ok||result.error)throw Error(result.error||'这次语音没能识别，请再试一次');
        const text=(result.text||'').trim();if(!text)throw Error('没听清这句话，再按住试一次');
        show('正在发送…');await send(text,s.target);hint.textContent='已发送';
      }catch(e){if(e.name!=='AbortError')onError(e.message);hint.textContent='松手发送 · 滑开取消';}
      finally{release(s);onRecording(false);pending=null;processing=false;transcription=null;button.disabled=false;show('按住说话');}
    }
    button.addEventListener('pointerdown',e=>{if(session||processing||(e.pointerType==='mouse'&&e.button!==0))return;e.preventDefault();button.setPointerCapture(e.pointerId);start(e.pointerId,e.clientX,e.clientY);});
    button.addEventListener('pointermove',e=>{if(session?.pointer!==e.pointerId)return;session.cancelled=Math.hypot(session.x-e.clientX,session.y-e.clientY)>60;show(session.cancelled?'松开取消':session.started?'松开发送':'正在开启麦克风…',true);hint.textContent=session.cancelled?'已取消 · 移回按钮可继续':'滑开取消';});
    button.addEventListener('pointerup',e=>{e.preventDefault();finish(e.pointerId);});
    button.addEventListener('pointercancel',cancel);button.addEventListener('lostpointercapture',e=>{if(session?.pointer===e.pointerId)cancel();});
    button.addEventListener('contextmenu',e=>e.preventDefault());
    button.addEventListener('keydown',e=>{if(['Space','Enter'].includes(e.code)){e.preventDefault();if(!e.repeat)start('keyboard',0,0);}});
    button.addEventListener('keyup',e=>{if(['Space','Enter'].includes(e.code)){e.preventDefault();finish('keyboard');}});
    button.addEventListener('blur',()=>{if(session?.pointer==='keyboard')cancel();});
    window.addEventListener('blur',()=>{if(session)cancel();});
    return{cancel};
  }
  window.TableGamesVoice={bind};
})();

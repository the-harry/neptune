"use strict";
/* ============================================================================
   VIDEO — WebRTC player fed by go2rtc (RTSP->WebRTC, zero transcode). Signaling
   is go2rtc's WebSocket API (nginx-proxied at CONFIG.camera.webrtcWs). Aggressive
   auto-reconnect so a camera mode-change (which blanks RTSP for ~1.1s, §7.4)
   self-heals without a refresh. Never a broken frame — shows NO FEED /
   "reconfiguring" overlays instead.

   The video plane is INDEPENDENT (§3): it has its own connection, its own retry
   schedule and its own status. Losing it must not disturb the ROV control link,
   and losing the ROV link must not tear this down.

   Every attempt carries a GENERATION number. Callbacks from a superseded attempt
   are ignored outright — otherwise a stale onclose could re-arm the retry timer a
   fresh attempt had just cleared, and the two would churn peer connections
   forever (each one holding a decoder and a UDP socket).
   ============================================================================ */
const videoEl = $('video-feed');
const noFeedEl = $('no-feed');

function setVideoStatus(s){ if(state.video!==s) LOG.net('video status ->', s); state.video=s; }

function showNoFeed(reason){
  setVideoStatus('nofeed');
  videoEl.style.visibility='hidden';
  noFeedEl.classList.remove('hidden');
  noFeedEl.classList.remove('reconfiguring');
  if(reason) $('no-feed-sub').textContent=reason;
  $('no-feed-badge').textContent='NO FEED';
}
function showReconfiguring(){
  // §7.4: a mode change interrupts video — say so, don't freeze a stale frame.
  setVideoStatus('reconfiguring');
  noFeedEl.classList.remove('hidden');
  noFeedEl.classList.add('reconfiguring');
  $('no-feed-badge').textContent='RECONFIGURING';
  $('no-feed-sub').textContent='camera busy — video will resume';
}
function showLive(){
  setVideoStatus('live');
  videoEl.style.visibility='visible';
  noFeedEl.classList.add('hidden');
  noFeedEl.classList.remove('reconfiguring');
  // A live feed cancels any pending retry: without this, a retry armed during a
  // transient ICE blip fired seconds later and tore down a working stream.
  if(state.videoRetryTimer){ clearTimeout(state.videoRetryTimer); state.videoRetryTimer=null; }
  _videoBackoff = 0;
}

function _wsBaseForVideo(){
  // same host as everything else; wsBase already ws://host (or derive from page)
  if(state.wsBase) return state.wsBase;
  const proto = location.protocol==='https:' ? 'wss:' : 'ws:';
  return location.host ? proto+'//'+location.host : '';
}

let _videoBackoff = 0;
let _videoGen = 0;              // bumped on every teardown; stale callbacks check it

function connectVideo(){
  const wsb = _wsBaseForVideo();
  if(!wsb){ showNoFeed('no backend (open with ?host=…)'); return; }  // file:// with no host
  teardownVideo();
  const gen = ++_videoGen;
  const current = ()=> gen===_videoGen;

  setVideoStatus('connecting');
  $('no-feed-sub').textContent='connecting…';

  let pc, ws, opened=false;
  try{
    pc = new RTCPeerConnection({ iceServers: [] });   // LAN/tether — no STUN needed
  }catch(e){ LOG.warn('RTCPeerConnection unavailable', e && e.message); showNoFeed('WebRTC unsupported'); return; }
  state.pc = pc;
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.ontrack = (ev)=>{
    if(!current()) return;
    videoEl.srcObject = ev.streams[0];
    videoEl.play().catch(()=>{});
    showLive();
  };
  pc.onicecandidate = (ev)=>{
    if(!current()) return;
    if(ev.candidate && ws && ws.readyState===1) ws.send(JSON.stringify({type:'webrtc/candidate', value: ev.candidate.candidate}));
  };
  pc.onconnectionstatechange = ()=>{
    if(!current()) return;
    const s = pc.connectionState;
    if(s==='connected'){ showLive(); }
    else if(s==='failed' || s==='disconnected' || s==='closed'){ scheduleVideoReconnect(); }
  };

  const url = wsb + CONFIG.camera.webrtcWs + '?src=' + encodeURIComponent(CONFIG.camera.stream);
  try{ ws = new WebSocket(url); }
  catch(e){ LOG.warn('video signaling ctor failed', e && e.message); scheduleVideoReconnect(); return; }
  state.sigWs = ws;

  ws.onopen = async ()=>{
    if(!current()) return;
    opened=true;
    try{
      const offer = await pc.createOffer();
      if(!current()) return;
      await pc.setLocalDescription(offer);
      ws.send(JSON.stringify({ type:'webrtc/offer', value: pc.localDescription.sdp }));
    }catch(e){ LOG.warn('offer failed', e && e.message); scheduleVideoReconnect(); }
  };
  ws.onmessage = async (ev)=>{
    if(!current()) return;
    let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='webrtc/answer'){
      try{ await pc.setRemoteDescription({ type:'answer', sdp: m.value }); }catch(e){ LOG.warn('answer failed', e && e.message); }
    } else if(m.type==='webrtc/candidate'){
      try{ await pc.addIceCandidate({ candidate: m.value, sdpMid:'0' }); }catch(e){}
    } else if(m.type==='error'){
      // Surface what go2rtc actually said — "no such stream" and "camera
      // unreachable" are different problems and used to look identical.
      LOG.warn('go2rtc:', m.value);
      state.videoError = String(m.value||'').slice(0,80);
      scheduleVideoReconnect();
    }
  };
  ws.onclose = ()=>{ if(current() && state.video!=='live') scheduleVideoReconnect(); };
  ws.onerror = ()=>{ if(current() && !opened) scheduleVideoReconnect(); };
}

function teardownVideo(){
  _videoGen++;                                   // orphan every in-flight callback
  if(state.videoRetryTimer){ clearTimeout(state.videoRetryTimer); state.videoRetryTimer=null; }
  try{
    if(state.sigWs){
      // Strip ALL handlers, not just onclose — a live onerror/onmessage on a
      // discarded socket could still re-arm the retry we just cancelled.
      state.sigWs.onopen = state.sigWs.onmessage = state.sigWs.onclose = state.sigWs.onerror = null;
      state.sigWs.close();
    }
  }catch(e){}
  try{
    if(state.pc){
      state.pc.ontrack = state.pc.onconnectionstatechange = state.pc.onicecandidate = null;
      state.pc.close();
    }
  }catch(e){}
  // Release the dead MediaStream: leaving it attached kept a decode pipeline (and
  // its memory) alive across every single reconnect.
  try{
    if(videoEl && videoEl.srcObject){
      const s = videoEl.srcObject;
      if(s.getTracks) s.getTracks().forEach(t=>{ try{ t.stop(); }catch(e){} });
      videoEl.srcObject = null;
    }
  }catch(e){}
  state.sigWs=null; state.pc=null;
}

function scheduleVideoReconnect(){
  if(state.videoRetryTimer) return;
  // Only claim "reconfiguring" on a FRESH camera reading. A stale is_streaming
  // value used to pin the overlay to RECONFIGURING while the real problem was a
  // dead Pi or a dead go2rtc.
  const camFresh = state.camOkAt && (Date.now()-state.camOkAt) < 20000;
  if(camFresh && state.cam && state.cam.isStreaming==='NO') showReconfiguring();
  else if(state.video!=='reconfiguring'){
    showNoFeed(state.videoError ? state.videoError
             : (state.host ? 'reconnecting…' : 'awaiting camera'));
  }
  _videoBackoff = Math.min(_videoBackoff ? _videoBackoff*1.6 : CONFIG.camera.videoRetryMs, 8000);
  state.videoRetryTimer = setTimeout(()=>{ state.videoRetryTimer=null; connectVideo(); }, _videoBackoff);
}

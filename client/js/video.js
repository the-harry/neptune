"use strict";
/* ============================================================================
   VIDEO — WebRTC player fed by go2rtc (RTSP->WebRTC, zero transcode). Signaling
   is go2rtc's WebSocket API (nginx-proxied at CONFIG.camera.webrtcWs). Aggressive
   auto-reconnect so a camera mode-change (which blanks RTSP for ~1.1s, §7.4)
   self-heals without a refresh. Never a broken frame — shows NO FEED /
   "reconfiguring" overlays instead.
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
  $('no-feed-badge').textContent='NO FEED';
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
}

function _wsBaseForVideo(){
  // same host as everything else; wsBase already ws://host (or derive from page)
  if(state.wsBase) return state.wsBase;
  const proto = location.protocol==='https:' ? 'wss:' : 'ws:';
  return location.host ? proto+'//'+location.host : '';
}

let _videoBackoff = 0;
function connectVideo(){
  const wsb = _wsBaseForVideo();
  if(!wsb){ showNoFeed('no backend (open with ?host=…)'); return; }  // file:// with no host
  teardownVideo();
  setVideoStatus('connecting');
  $('no-feed-sub').textContent='connecting…';

  let pc, ws, opened=false;
  try{
    pc = new RTCPeerConnection({ iceServers: [] });   // LAN/tether — no STUN needed
  }catch(e){ LOG.warn('RTCPeerConnection unavailable', e && e.message); showNoFeed('WebRTC unsupported'); return; }
  state.pc = pc;
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.ontrack = (ev)=>{ videoEl.srcObject = ev.streams[0]; videoEl.play().catch(()=>{}); showLive(); _videoBackoff=0; };
  pc.onicecandidate = (ev)=>{ if(ev.candidate && ws && ws.readyState===1) ws.send(JSON.stringify({type:'webrtc/candidate', value: ev.candidate.candidate})); };
  pc.onconnectionstatechange = ()=>{
    const s = pc.connectionState;
    if(s==='connected'){ showLive(); }
    else if(s==='failed' || s==='disconnected' || s==='closed'){ scheduleVideoReconnect(); }
  };

  const url = wsb + CONFIG.camera.webrtcWs + '?src=' + encodeURIComponent(CONFIG.camera.stream);
  try{ ws = new WebSocket(url); }
  catch(e){ LOG.warn('video signaling ctor failed', e && e.message); scheduleVideoReconnect(); return; }
  state.sigWs = ws;

  ws.onopen = async ()=>{
    opened=true;
    try{
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      ws.send(JSON.stringify({ type:'webrtc/offer', value: pc.localDescription.sdp }));
    }catch(e){ LOG.warn('offer failed', e && e.message); scheduleVideoReconnect(); }
  };
  ws.onmessage = async (ev)=>{
    let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='webrtc/answer'){
      try{ await pc.setRemoteDescription({ type:'answer', sdp: m.value }); }catch(e){ LOG.warn('answer failed', e && e.message); }
    } else if(m.type==='webrtc/candidate'){
      try{ await pc.addIceCandidate({ candidate: m.value, sdpMid:'0' }); }catch(e){}
    } else if(m.type==='error'){
      LOG.warn('go2rtc:', m.value); scheduleVideoReconnect();
    }
  };
  ws.onclose = ()=>{ if(state.video!=='live') scheduleVideoReconnect(); };
  ws.onerror = ()=>{ if(!opened) scheduleVideoReconnect(); };
}

function teardownVideo(){
  if(state.videoRetryTimer){ clearTimeout(state.videoRetryTimer); state.videoRetryTimer=null; }
  try{ if(state.sigWs){ state.sigWs.onclose=null; state.sigWs.close(); } }catch(e){}
  try{ if(state.pc){ state.pc.ontrack=null; state.pc.onconnectionstatechange=null; state.pc.close(); } }catch(e){}
  state.sigWs=null; state.pc=null;
}

function scheduleVideoReconnect(){
  if(state.videoRetryTimer) return;
  // if the camera is mid-reconfigure, say so; else NO FEED
  if(state.cam && state.cam.isStreaming==='NO') showReconfiguring();
  else if(state.video!=='reconfiguring') showNoFeed(state.host ? 'reconnecting…' : 'awaiting camera');
  _videoBackoff = Math.min(_videoBackoff ? _videoBackoff*1.6 : CONFIG.camera.videoRetryMs, 8000);
  state.videoRetryTimer = setTimeout(()=>{ state.videoRetryTimer=null; connectVideo(); }, _videoBackoff);
}

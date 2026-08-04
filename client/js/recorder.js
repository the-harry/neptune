"use strict";
/* ============================================================================
   RECORDER — the topside half of the two-sided blackbox (logging addendum).

   Logs the same events the Pi logs, from the OPERATOR'S side, so the two can be
   differenced afterwards (rovlog). Every record carries `t` in the client's own
   MONOTONIC time (performance.now()) — NEVER a clock-corrected value; correction
   happens in analysis using the logged offsets (§2).

   Durability (§5): events are written to an IndexedDB ring buffer immediately and
   uploaded separately/asynchronously in batches; deleted locally only after the Pi
   confirms. On reconnect the backlog flushes first. It must survive the very link
   failure it is recording — so upload never blocks recording, is bandwidth-capped,
   and backs off. A final flush is attempted via sendBeacon on unload.

   Everything here is wrapped so a recorder fault can never break piloting.
   ============================================================================ */
const REC = {
  enabled:false, ready:false,
  db:null, mem:[],                 // IndexedDB handle; in-memory fallback if IDB is unavailable
  session_id:null, pi_boot_id:null,
  count:0,                          // approx events resident in the ring
  // clock sync
  rtts:[], lastOffset:0, syncSamples:0,
  // telemetry seq tracking
  seqLo:null, seqHi:null, seqSeen:0, seqGaps:[], lastTelMono:0, maxAge:0, lastSeq:null,
  // command confirm watchers  { c_id: {pred, name, until} }
  pending:{},
  // upload state
  uploading:false, backoffMs:0, bytesWindow:[], lastUploadAt:0,
};

function _mono(){ return performance.now(); }
REC.mono = _mono;

/* ---- IndexedDB ring buffer (§5) ---- */
function _openDB(){
  return new Promise((resolve)=>{
    let req; try{ req=indexedDB.open('neptune-blackbox', 1); }catch(e){ resolve(null); return; }
    req.onupgradeneeded = ()=>{ const db=req.result; if(!db.objectStoreNames.contains('events')) db.createObjectStore('events', {keyPath:'k', autoIncrement:true}); };
    req.onsuccess = ()=>resolve(req.result);
    req.onerror   = ()=>resolve(null);
  });
}
function _idbAdd(rec){
  return new Promise((resolve)=>{
    try{ const tx=REC.db.transaction('events','readwrite'); tx.objectStore('events').add(rec);
      tx.oncomplete=()=>resolve(true); tx.onerror=()=>resolve(false);
    }catch(e){ resolve(false); }
  });
}
function _idbOldest(n){
  return new Promise((resolve)=>{
    const out=[];
    try{ const tx=REC.db.transaction('events','readonly'); const cur=tx.objectStore('events').openCursor();
      cur.onsuccess=()=>{ const c=cur.result; if(c && out.length<n){ out.push({k:c.value.k, v:c.value}); c.continue(); } else resolve(out); };
      cur.onerror=()=>resolve(out);
    }catch(e){ resolve(out); }
  });
}
function _idbDelete(keys){
  return new Promise((resolve)=>{
    try{ const tx=REC.db.transaction('events','readwrite'); const os=tx.objectStore('events');
      keys.forEach(k=>os.delete(k)); tx.oncomplete=()=>resolve(true); tx.onerror=()=>resolve(false);
    }catch(e){ resolve(false); }
  });
}
function _idbTrim(){                                   // enforce the ring cap, oldest-out
  if(REC.count<=CONFIG.recorder.maxEvents) return;
  const over=REC.count-CONFIG.recorder.maxEvents;
  _idbOldest(over).then(rows=>{ if(rows.length){ _idbDelete(rows.map(r=>r.k)); REC.count-=rows.length; } });
}
function _idbAll(){
  return new Promise((resolve)=>{
    const out=[];
    try{ const tx=REC.db.transaction('events','readonly'); const cur=tx.objectStore('events').openCursor();
      cur.onsuccess=()=>{ const c=cur.result; if(c){ out.push(c.value); c.continue(); } else resolve(out); };
      cur.onerror=()=>resolve(out);
    }catch(e){ resolve(out); }
  });
}

/* ---- the log call — used everywhere ---- */
REC.log = function(e, d, c_id){
  if(!REC.enabled) return;
  try{
    const rec={ t:+_mono().toFixed(1), e:e };
    if(c_id) rec.c_id=c_id;
    if(d!==undefined) rec.d=d;
    if(REC.db){ _idbAdd(rec).then(ok=>{ if(ok){ REC.count++; if(REC.count%256===0) _idbTrim(); } }); }
    else { REC.mem.push(rec); if(REC.mem.length>CONFIG.recorder.maxEvents) REC.mem.shift(); }
  }catch(err){/* recording must never throw into the app */}
};

/* ---- session adoption (§1) ---- */
REC.adoptSession = async function(){
  try{
    const r=await fetch((state.httpBase||'')+CONFIG.recorder.session);
    const s=await r.json();
    const rebooted = REC.pi_boot_id && s.pi_boot_id && REC.pi_boot_id!==s.pi_boot_id;
    REC.session_id=s.session_id; REC.pi_boot_id=s.pi_boot_id;
    REC.log('session_adopt', { session_id:s.session_id, pi_boot_id:s.pi_boot_id, pi_t_mono:s.pi_t_mono, rebooted:!!rebooted });
    LOG.state('blackbox session adopted:', s.session_id, rebooted?'(pi rebooted → new client file)':'');
  }catch(e){ LOG.warn('session adopt failed:', e && e.message); }
};

/* ---- clock sync (§2): called from net.js on each pong ---- */
REC.onPong = function(m){
  const t4=_mono(), t1=m.t1;
  if(typeof t1!=='number' || typeof m.t2!=='number' || typeof m.t3!=='number'){
    if(typeof t1==='number') state.linkMs=Math.round(t4-t1);   // fallback RTT
    return;
  }
  const rtt=(t4-t1)-(m.t3-m.t2);
  const offset=((m.t2-t1)+(m.t3-t4))/2;                        // ms to ADD to a client ts → Pi timebase
  REC.lastOffset=offset; REC.syncSamples++;
  REC.rtts.push(rtt); if(REC.rtts.length>16) REC.rtts.shift();
  const mean=REC.rtts.reduce((a,b)=>a+b,0)/REC.rtts.length;
  const jitter=Math.sqrt(REC.rtts.reduce((a,b)=>a+(b-mean)*(b-mean),0)/REC.rtts.length);
  state.linkMs=Math.max(0,Math.round(rtt));                    // §2 — rtt doubles as the LINK latency
  REC.log('clock_sync', { rtt_ms:+rtt.toFixed(2), offset_ms:+offset.toFixed(2), samples:REC.syncSamples, jitter_ms:+jitter.toFixed(2) });
};

/* ---- command correlation (§3) ---- */
function _uuid(){ try{ return crypto.randomUUID(); }catch(e){ return 'c'+Date.now().toString(36)+Math.floor(_mono()).toString(36); } }
REC.cmdIntent = function(name, value){                          // operator intent registered
  const c_id=_uuid();
  REC.log('cmd_intent', {name, value:value===undefined?null:value}, c_id);
  // register a confirm watcher for commands whose effect shows in telemetry
  const pred=_confirmPredicate(name, value);
  if(pred) REC.pending[c_id]={pred, name, until:_mono()+4000};
  return c_id;
};
REC.cmdSend = function(c_id){ REC.log('cmd_send', undefined, c_id); };   // handed to the socket
REC.cmdAck  = function(c_id, ok){ if(c_id) REC.log('cmd_ack_recv', {ok:!!ok}, c_id); };  // ack received
function _confirmPredicate(name, value){
  const B=(k,want)=>((tel)=> typeof tel[k]==='boolean' && tel[k]===want);
  if(name==='arm')          return B('armed', true);
  if(name==='disarm'||name==='stop') return B('armed', false);
  if(name==='magnet')       return B('magnet', !!value);
  if(name==='light_green')  return B('light_green', !!value);
  if(name==='light_white')  return B('light_white', !!value);
  return null;
}

/* ---- telemetry receive tracking (§4.2): seq ranges, gaps, staleness ---- */
REC.onTelemetry = function(tel){
  const now=_mono();
  REC.lastTelMono=now;
  if(typeof tel.seq==='number'){
    if(REC.seqLo===null) REC.seqLo=tel.seq;
    if(REC.lastSeq!==null && tel.seq>REC.lastSeq+1) REC.seqGaps.push([REC.lastSeq+1, tel.seq-1]);
    REC.lastSeq=tel.seq; REC.seqHi=tel.seq; REC.seqSeen++;
    if(REC.seqSeen>=CONFIG.recorder.tlmWindow){
      REC.log('tlm_rx', { seq_from:REC.seqLo, seq_to:REC.seqHi, n:REC.seqSeen, gaps:REC.seqGaps.slice(0,20), max_age_ms:Math.round(REC.maxAge) });
      REC.seqLo=null; REC.seqSeen=0; REC.seqGaps=[]; REC.maxAge=0;
    }
  }
  // resolve command confirms (effect observed)
  for(const c_id in REC.pending){ const p=REC.pending[c_id];
    if(p.pred(tel)){ REC.log('cmd_confirm', {name:p.name}, c_id); delete REC.pending[c_id]; }
    else if(now>p.until){ delete REC.pending[c_id]; }
  }
};
/* called from the render loop: age of the newest telemetry the operator is seeing */
REC.tickStaleness = function(){
  if(!REC.enabled || !REC.lastTelMono) return 0;
  const age=_mono()-REC.lastTelMono;
  if(age>REC.maxAge) REC.maxAge=age;
  return age;
};

/* ---- upload loop (§5): batched, capped, backing off ---- */
function _bwBudgetOk(bytes){
  const now=_mono(), winMs=1000;
  REC.bytesWindow=REC.bytesWindow.filter(x=>now-x.t<winMs);
  const used=REC.bytesWindow.reduce((a,b)=>a+b.n,0);
  return used+bytes <= CONFIG.recorder.uploadCapBps/8;         // bytes/sec = bps/8
}
async function _uploadOnce(){
  if(REC.uploading || !REC.enabled) return;
  if(state.wsStatus!=='online' && !navigator.onLine) return;    // no point; keep buffering (backlog flushes on reconnect)
  REC.uploading=true;
  try{
    const rows = REC.db ? await _idbOldest(CONFIG.recorder.uploadMaxBatch)
                        : REC.mem.slice(0,CONFIG.recorder.uploadMaxBatch).map((v,i)=>({k:i,v}));
    if(!rows.length){ REC.backoffMs=0; return; }
    const now=_mono();
    const records=rows.map(r=>({ ...r.v, up_lag_ms:Math.round(now-(r.v.t||now)) }));   // §5 — how long it waited
    const body=JSON.stringify({ session_id:REC.session_id, records });
    const bytes=body.length;
    if(!_bwBudgetOk(bytes)){ REC.log('log_backpressure', {reason:'bandwidth_cap', pending:REC.count}); return; }  // §5 cap hit
    const r=await fetch((state.httpBase||'')+CONFIG.recorder.clientlog, {method:'POST', headers:{'Content-Type':'application/json'}, body});
    if(!r.ok) throw new Error('http '+r.status);
    REC.bytesWindow.push({t:now, n:bytes});
    if(REC.db){ await _idbDelete(rows.map(r=>r.k)); REC.count=Math.max(0,REC.count-rows.length); }
    else { REC.mem.splice(0, rows.length); }
    REC.backoffMs=0; REC.lastUploadAt=now;
  }catch(e){
    REC.backoffMs = REC.backoffMs ? Math.min(CONFIG.recorder.backoffMaxMs, REC.backoffMs*2) : 1000;  // §5 exp backoff
    LOG.net('clientlog upload deferred ('+(e&&e.message)+'), backoff '+REC.backoffMs+'ms');
  }finally{ REC.uploading=false; }
}
function _scheduleUpload(){
  const base=CONFIG.recorder.uploadEveryMs;
  const wait=REC.backoffMs? Math.max(base, REC.backoffMs) : base;
  setTimeout(async()=>{ await _uploadOnce(); _scheduleUpload(); }, wait);
}

/* ---- samplers (§4.1): WebRTC receiver stats + raw gamepad ---- */
async function _sampleWebRTC(){
  if(!REC.enabled || !state.pc || !state.pc.getStats) return;
  try{
    const stats=await state.pc.getStats();
    stats.forEach(s=>{
      if(s.type==='inbound-rtp' && (s.kind==='video'||s.mediaType==='video')){
        REC.log('webrtc_stats', {
          framesDecoded:s.framesDecoded, framesDropped:s.framesDropped, freezeCount:s.freezeCount,
          totalFreezesDuration:s.totalFreezesDuration, jitterBufferDelay:s.jitterBufferDelay,
          packetsLost:s.packetsLost, nackCount:s.nackCount, pliCount:s.pliCount, bytesReceived:s.bytesReceived,
          framesPerSecond:s.framesPerSecond });
      }
    });
  }catch(e){/* ignore */}
}
function _sampleGamepad(){
  if(!REC.enabled) return;
  const gp = (typeof currentPad==='function') ? currentPad() : null;
  if(!gp) return;
  REC.log('gamepad', { id:gp.id, axes:gp.axes.map(a=>+a.toFixed(3)),
    buttons:gp.buttons.map(b=>b.pressed?1:0).join('') });
}

/* ---- manual export (§5): download the whole ring as JSONL, even with the link down ---- */
REC.exportLog = async function(){
  try{
    const rows = REC.db ? (await _idbAll()) : REC.mem.slice();
    const lines = rows.map(v=>JSON.stringify(REC.db? {t:v.t,e:v.e,...(v.c_id?{c_id:v.c_id}:{}),...(v.d!==undefined?{d:v.d}:{})} : v));
    const blob=new Blob([lines.join('\n')+'\n'], {type:'application/x-ndjson'});
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
    a.download='client_'+(REC.session_id||'session')+'.jsonl'; a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href), 4000);
    LOG.state('blackbox exported', rows.length, 'events');
  }catch(e){ LOG.warn('export failed:', e && e.message); }
};
REC.mark = function(note){ REC.log('mark', {note:note||null}); vibrate(20); LOG.state('MARK logged'); };

/* ---- init ---- */
REC.init = async function(){
  if(!CONFIG.recorder.enabled){ return; }
  REC.enabled=true;
  REC.db=await _openDB();
  // environment snapshot (§4.1)
  REC.log('env', {
    ua:navigator.userAgent, screen:[screen.width, screen.height], dpr:window.devicePixelRatio,
    lang:navigator.language, secure:window.isSecureContext, href:location.href, idb:!!REC.db });
  // browser events (§4.1)
  document.addEventListener('visibilitychange', ()=>REC.log('visibility', {state:document.visibilityState}));
  window.addEventListener('online',  ()=>REC.log('browser_online', {}));
  window.addEventListener('offline', ()=>REC.log('browser_offline', {}));
  window.addEventListener('blur',    ()=>REC.log('focus', {focused:false}));
  window.addEventListener('focus',   ()=>REC.log('focus', {focused:true}));
  window.addEventListener('error', (e)=>REC.log('window_error', {msg:e.message, src:e.filename, line:e.lineno, stack:(e.error&&e.error.stack||'').slice(0,600)}));
  window.addEventListener('unhandledrejection', (e)=>REC.log('unhandled_rejection', {reason:String(e.reason).slice(0,400)}));
  window.addEventListener('beforeunload', ()=>{ REC.log('beforeunload', {}); _beaconFlush(); });
  // samplers
  setInterval(_sampleWebRTC, Math.round(1000/CONFIG.recorder.webrtcHz));
  setInterval(_sampleGamepad, Math.round(1000/CONFIG.recorder.gamepadHz));
  _scheduleUpload();
  REC.ready=true;
  LOG.state('blackbox recorder ready ('+(REC.db?'IndexedDB':'in-memory fallback')+')');
};

/* final best-effort flush on unload (§5) */
function _beaconFlush(){
  try{
    const rows = REC.db ? null : REC.mem.slice(0,200);      // IDB read is async → can't block unload; ship the mem tail if any
    if(rows && rows.length && navigator.sendBeacon){
      const body=JSON.stringify({session_id:REC.session_id, records:rows.map(v=>({...v, up_lag_ms:0}))});
      navigator.sendBeacon((state.httpBase||'')+CONFIG.recorder.clientlog, new Blob([body],{type:'application/json'}));
    }
  }catch(e){/* ignore */}
}

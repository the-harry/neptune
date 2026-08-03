"use strict";
/* ============================================================================
   CORE — DOM helpers, math helpers, the LOG bus, the live state object, and
   host/URL resolution. Loaded right after config.js; everything else builds
   on the globals defined here.
   ============================================================================ */

/* ---- small helpers ---- */
const $ = id => document.getElementById(id);
const clamp = (v,a,b) => v<a?a:(v>b?b:v);
const HEADINGS = ['N','NE','E','SE','S','SW','W','NW'];
function headingCardinal(deg){ return HEADINGS[Math.round(((deg%360)/45))%8]; }
function vibrate(ms){ try{ navigator.vibrate && navigator.vibrate(ms); }catch(e){} }

/* ============================================================================
   LOG — console debug bus. Everything of interest is logged here. High-rate
   streams (control/camera TX, telemetry RX) are throttled to 1/s by default.
   Runtime toggles:  NEPTUNE.log(false) · NEPTUNE.logRate(true) · NEPTUNE.state
   ============================================================================ */
const LOG = (function(){
  let enabled=true, highRate=false;
  const t0=Date.now();
  const ts=()=>((Date.now()-t0)/1000).toFixed(2)+'s';
  const last={};
  function base(tag,color,args){
    if(!enabled) return;
    try{ console.log('%c'+tag+'%c '+ts(), 'color:'+color+';font-weight:bold', 'color:#7a8a8f', ...args); }catch(e){}
  }
  function throttled(key,ms,tag,color,args){
    if(!enabled) return;
    if(highRate){ base(tag,color,args); return; }
    const n=Date.now();
    if(!last[key] || n-last[key]>=ms){ last[key]=n; base(tag,color,args); }
  }
  return {
    net:  (...a)=>base('[NET] ','#b46bff',a),
    tx:   (...a)=>base('[TX]  ','#c99bff',a),
    rx:   (...a)=>base('[RX]  ','#4dffa6',a),
    txRate:(k,...a)=>throttled('tx:'+k,1000,'[TX~] ','#9a4dff',a),
    rxRate:(k,...a)=>throttled('rx:'+k,1000,'[RX~] ','#4dffa6',a),
    cmd:  (...a)=>base('[CMD] ','#ff5bd0',a),
    input:(...a)=>base('[IN]  ','#ff9be0',a),
    map:  (...a)=>base('[MAP] ','#ff5bd0',a),
    state:(...a)=>base('[STATE]','#b9a9d6',a),
    warn: (...a)=>base('[WARN]','#ff5c7a',a),
    setEnabled:(v)=>{ enabled=!!v; try{console.log('%c[NEPTUNE] logging '+(enabled?'ON':'OFF'),'color:#b46bff');}catch(e){} },
    setHighRate:(v)=>{ highRate=!!v; try{console.log('%c[NEPTUNE] high-rate logging '+(highRate?'ON':'OFF'),'color:#b46bff');}catch(e){} }
  };
})();

/* ============================================================================
   STATE — the single live state object the whole app reads/writes.
   ============================================================================ */
const state = {
  host:'', httpBase:'', wsBase:'',
  ws:null, wsStatus:'offline', /* offline | connecting | online */
  linkMs:null, lastPingAt:0,
  reconnectDelay:CONFIG.reconnect.baseMs, reconnectTimer:null,
  video:'idle', /* idle | connecting | live | nofeed | reconfiguring */
  pc:null, sigWs:null, videoRetryTimer:null,   // WebRTC peer + go2rtc signaling socket
  // ---- WOLFANG camera control plane ----
  cam:{ battery:null, recording:false, recordRaw:'', mode:'', sd:'', warning:'',
        remaining:null, isStreaming:'', degraded:false, menu:[],
        videoRes:'', awb:'', imageRes:'', ev:'' },   // live camera settings
  camWs:null, surfaced:false,   // surfaced = config/file ops unlocked (§7.4 gate)
  source:'keyboard', /* keyboard | gamepad */
  gamepadIndex:null,
  keys:new Set(), padPrev:{},
  bindings:{},                 // action -> [ {type:'pad',index} | {type:'key',code} ]
  actionPrev:{},               // per-action held state (edge detection)
  learn:{ active:false, action:null, padBaseline:{} },
  mapperOpen:false,
  input:{ throttle:0, steer:0, pan:0, tilt:0, ballast:'hold' },
  ballastTargetCmd:0,         // starts at SURFACE (empty). This is also the shutdown state.
  lastLight:'green',
  lights:{ green:{on:true, level:0.8}, white:{on:false, level:0.2} },
  levelDirty:{ green:false, white:false },
  magnet:false, armed:true,
  ballastLevel:0, ballastTarget:0,
  depth:1.28, pressure:14.7, heading:284, batteryV:24.8,
  cpuC:null, ramPct:null, diskGb:null,   // Pi system metrics (from telemetry)
  left:0, right:0,
  leak:false, simLeak:false, alarmLeak:false,
  realTel:null, realTelAt:0,
  mode:'sim', /* sim | real | stale */
  surfaceUntil:0,
  lastFrame:0
};

/* ============================================================================
   HOST RESOLUTION — default same origin; override via ?host=IP:PORT (persisted
   in localStorage). WS base is derived from the same host (ws/wss to match).
   ============================================================================ */
function resolveHost(){
  let host=null;
  try{
    const params=new URLSearchParams(location.search);
    host=params.get('host');
    if(host){ localStorage.setItem('rov_host', host); }
    else { host=localStorage.getItem('rov_host'); }
  }catch(e){}
  const sameOrigin = location.protocol==='http:'||location.protocol==='https:';
  if(!host && sameOrigin) host=location.host;
  host = host || '';
  const secure = location.protocol==='https:';
  state.host = host;
  state.httpBase = host ? (secure?'https':'http')+'://'+host : '';
  state.wsBase   = host ? (secure?'wss':'ws')+'://'+host : '';
}

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

/* Flat-earth local (east,north metres) <-> lat/lon — mirrors api/nav/geo.py.
   Exact enough at pond/canal scale; used to place the sub/track over imagery. */
const EARTH_R = 6378137.0;
function toLatLon(x, y, lat0, lon0){
  const lat = lat0 + (y / EARTH_R) * 180 / Math.PI;
  const lon = lon0 + (x / (EARTH_R * Math.cos(lat0*Math.PI/180))) * 180 / Math.PI;
  return { lat, lon };
}
function toLocal(lat, lon, lat0, lon0){
  const y = (lat - lat0)*Math.PI/180 * EARTH_R;
  const x = (lon - lon0)*Math.PI/180 * EARTH_R * Math.cos(lat0*Math.PI/180);
  return { x, y };
}

/* ============================================================================
   LOG — console debug bus. Everything of interest is logged here. High-rate
   streams (control/camera TX, telemetry RX) are throttled to 1/s by default.
   Runtime toggles:  NEPTUNE.log(false) · NEPTUNE.logRate(true) · NEPTUNE.state
   ============================================================================ */
/* The log is a BUS, not a console call.

   A submarine fault is diagnosed after the fact, from whatever was recorded while
   it was happening — and on this vehicle the operator cannot leave the console
   mid-dive to go and read a file. So every line goes three places at once:

     console   for a dev machine with devtools open
     ring      an in-memory scrollback the LOGS overlay tails live
     sinks     the on-disk session log, so it survives the machine dying

   Levels exist so the overlay can filter: ok / info / warn / err. Anything that is
   sent, received, attempted or refused should end up here — the point is that a
   question about what the vehicle did five minutes ago is answerable without
   having prepared for it. */
const LOG = (function(){
  let enabled=true, highRate=false;
  const t0=Date.now();
  const ts=()=>((Date.now()-t0)/1000).toFixed(2)+'s';
  const last={};
  const ring=[];
  const sinks=[];
  let seq=0;
  const MAX = 4000;                         // ~ a long dive; bounded so memory cannot run away

  function fmt(a){
    if(a instanceof Error) return a.message || String(a);
    if(typeof a === 'string') return a;
    try{ return JSON.stringify(a); }catch(e){ return String(a); }
  }
  function emit(tag, level, color, args){
    const line = { i:++seq, t:Date.now(), rel:(Date.now()-t0)/1000, tag:tag.trim(), level:level,
                   msg:args.map(fmt).join(' ') };
    ring.push(line);
    if(ring.length > MAX) ring.splice(0, ring.length - MAX);
    for(let i=0;i<sinks.length;i++){ try{ sinks[i](line); }catch(e){} }
    if(!enabled) return;
    try{ console.log('%c'+tag+'%c '+ts(), 'color:'+color+';font-weight:bold', 'color:#7a8a8f', ...args); }catch(e){}
  }
  function base(tag,color,args,level){ emit(tag, level||'info', color, args); }
  /* Rate-limited categories are the high-frequency ones: control frames at 20 Hz
     would be 200 s of scrollback in a 4000-line ring, evicting everything that
     explains how the dive got there. So they are coalesced rather than dropped
     silently, and the suppressed count is carried on the next line that does get
     through - dmesg's "message repeated N times", for the same reason. */
  function throttled(key,ms,tag,color,args){
    if(highRate){ emit(tag,'info',color,args); return; }
    const n=Date.now();
    const st = last[key] || (last[key] = {t:0, n:0, ms:ms, tag:tag, color:color});
    st.ms=ms; st.tag=tag; st.color=color;
    if(n - st.t >= ms){
      const extra = st.n;
      st.t = n; st.n = 0;
      emit(tag,'info',color, extra ? args.concat(['(+'+extra+' more)']) : args);
    } else {
      st.n++;
    }
  }
  /* A burst that STOPS would otherwise take its suppressed count with it: the
     count is only reported by the next line to get through, and if the stream
     goes quiet there is no next line. That loses exactly the interesting case -
     "telemetry was flowing, then it wasn't". Sweep the tail out on a timer. */
  setInterval(function(){
    const n = Date.now();
    for(const k in last){
      const st = last[k];
      if(st.n > 0 && (n - st.t) >= (st.ms || 1000)){
        const extra = st.n;
        st.t = n; st.n = 0;
        emit(st.tag, 'info', st.color, ['(+'+extra+' more, then quiet)']);
      }
    }
  }, 1000);
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
    ok:   (...a)=>base('[OK]  ','#4dffa6',a,'ok'),
    warn: (...a)=>base('[WARN]','#ff8c1a',a,'warn'),
    err:  (...a)=>base('[ERR] ','#ff5c7a',a,'err'),
    // ---- consumers ----
    ring: ()=>ring,
    subscribe:(fn)=>{ sinks.push(fn); return ()=>{ const i=sinks.indexOf(fn); if(i>=0) sinks.splice(i,1); }; },
    setEnabled:(v)=>{ enabled=!!v; try{console.log('%c[NEPTUNE] console logging '+(enabled?'ON':'OFF'),'color:#b46bff');}catch(e){} },
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
  screenRec:{ active:false, file:'' },   // handheld screen recording (launcher + ffmpeg)
  // Per-subsystem liveness stamps (§3). Each subsystem proves itself independently,
  // so one going quiet greys only its own controls. See status.js.
  camOkAt:0,                    // last successful camera control-plane response
  navOkAt:0,                    // last nav telemetry frame
  sys:null, sysAt:0,            // last /api/system snapshot (real Pi health)
  camAp:null,                   // launcher /__wifi: can THIS handheld see the camera's AP
  piProbe:null,                 // /api/healthz: does the sub ANSWER, control link aside
  net:null,                     // launcher /__net: this handheld's own radios and cables
  // WHEN each measured quantity last actually arrived. Not when telemetry arrived -
  // a frame with no `depth` field leaves state.depth holding its last value, which on
  // a sub with no depth sensor is a number from the simulator. These stamps are what
  // let the readouts colour themselves from a sensor or not at all.
  depthAt:0, pressureAt:0,
  source:'keyboard', /* keyboard | gamepad */
  gamepadIndex:null,
  keys:new Set(), padPrev:{},
  bindings:{},                 // action -> [ {type:'pad',index} | {type:'key',code} ]
  actionPrev:{},               // per-action held state (edge detection)
  learn:{ active:false, action:null, padBaseline:{} },
  mapperOpen:false,
  input:{ throttle:0, steer:0, pan:0, tilt:0, ballast:'hold' },
  ballastTargetRaw:0,         // what the operator set (arrows/drag) — may jump
  ballastTargetCmd:0,         // slews toward Raw at CONFIG.ballastSlewPerS → smooth API commands.
                              //   starts at SURFACE (empty); also the shutdown state.
  lastLight:'green',
  lights:{ green:{on:false, level:0}, white:{on:false, level:0} },   // start OFF; toggling on jumps to lightOnDefault
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
  surfaceComboStart:0, surfaceComboFired:false,   // gamepad SURFACE combo hold (both paddles)
  zoomArm:{},                 // paddle pressed alone → zooms the map on release (see input.js)
  lastFrame:0
};

/* TOOLTIPS THAT SURVIVE.

   Every glyph and number carries a written explanation, because for most people
   this dashboard is the first submarine control they have ever seen and there is
   nobody beside them to translate. But the renderers also want the title for LIVE
   state ("Video: live feed"), and whoever wrote last used to win — which quietly
   erased the explanation a few seconds after boot.

   So the explanation is captured once into data-help and the live text is appended
   to it. Both survive, and the HTML stays the single place the wording lives. */
function captureHelp(){
  document.querySelectorAll('[title]').forEach(el=>{
    if(!el.dataset.help) el.dataset.help = el.getAttribute('title') || '';
  });
}
function liveTitle(el, live){
  if(typeof el === 'string') el = $(el);
  if(!el) return;
  const help = el.dataset.help || '';
  el.title = help ? (help + (live ? '   —   ' + live : '')) : (live || '');
  if(help) el.setAttribute('aria-label', el.title);
}

/* Is a real vehicle on the other end of the link, right now?

   THE RULE: while one is, nothing on this console may be synthesised. A simulated
   position drawn over a real dive hides the failure it is most important to see —
   a dead thruster, a snagged tether, a sub pinned against a wall all look exactly
   like normal progress if the map keeps advancing on commanded throttle. The map
   moves on the SUB's output or it does not move at all. */
function vehicleLinked(){
  return !!(state.realTel && (Date.now()-state.realTelAt) < CONFIG.staleTimeoutMs);
}

/* Does the vehicle carry real navigation sensors, or is it a mocked hull?
   Telemetry carries `mock` (api/hardware.py `is_mock`): on a Pi with nothing wired
   yet RealHardware refuses to start and `auto` falls back to the bench simulator.
   Used to SAY SO on the radar — never to fill the gap in with a guess. */
function vehicleHasSensors(){ return !!(state.realTel && state.realTel.mock !== true); }

/* ============================================================================
   HOST RESOLUTION — default same origin; override via ?host=IP:PORT (persisted
   in localStorage). WS base is derived from the same host (ws/wss to match).
   ============================================================================ */
function resolveHost(){
  // §1: the backend is SAME ORIGIN by default (served by nginx on the Pi). ?host=IP:PORT is an
  // explicit override only — it no longer acts as the primary discovery path, and a stale stored
  // host never shadows same-origin when served.
  const served = location.protocol==='http:'||location.protocol==='https:';
  let host=null, secureParam=null;
  try{
    const params=new URLSearchParams(location.search);
    // ?sim=1 — DEMO MODE. There is deliberately no vehicle to look for, so the
    // simulator takes over immediately instead of spending three seconds failing to
    // reach a Pi that was never there. This is what the public demo is served with;
    // everything on screen is honest about being simulated (red robot, SIM badge).
    if(params.get('sim')==='1'){
      state.demo = true;
      LOG.state('DEMO MODE (?sim=1) — no vehicle, everything is the simulator');
      return;                                             // no host, no ws: pure sim
    }
    const p=params.get('host');
    if(p){ host=p; localStorage.setItem('rov_host', p); } // explicit override wins (and is remembered)
    secureParam=params.get('secure');                     // ?secure=0 forces plain http/ws (dev against a mock)
  }catch(e){}
  if(!host && served) host=location.host;                  // default: same origin
  if(!host){                                               // disk fallback only: last override / configured
    try{ host=localStorage.getItem('rov_host')||''; }catch(e){}
    if(!host && CONFIG.defaultHost) host=CONFIG.defaultHost;
  }
  host = host || '';
  // Scheme belongs to the PI, not the page. Same-origin → match the page. A cross-origin override
  // (the kiosk launcher on http://localhost → the Pi) → plain http/ws, because the Pi is plain HTTP
  // (sealed tether, no TLS). Pass ?secure=1 if you ever front the Pi with HTTPS.
  let secure;
  if(served && host===location.host) secure = (location.protocol==='https:');
  else secure = false;
  if(secureParam!==null) secure = (secureParam==='1' || secureParam==='true');
  state.host = host;
  state.httpBase = host ? (secure?'https':'http')+'://'+host : '';
  state.wsBase   = host ? (secure?'wss':'ws')+'://'+host : '';
}

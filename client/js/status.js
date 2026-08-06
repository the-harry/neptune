"use strict";
/* ============================================================================
   STATUS — the degradation model (architectural rule §3).

   Every subsystem fails INDEPENDENTLY and is tracked separately, because on this
   vehicle they really do fail one at a time:

     INTERNET  online | offline        search, live tiles, new downloads
     LINK      online | connecting     the ROV control WebSocket — vehicle commands
               | offline
     VIDEO     live | connecting       go2rtc WebRTC feed
               | down
     CAMERA    ok | degraded | down    the WOLFANG control plane (REC/PIC/settings)
     NAV       ok | down               the nav WebSocket (dead reckoning feed)
     VEHICLE   armed | idle | fault

   A subsystem being down greys ONLY the controls that subsystem owns. Losing the
   ROV link must not take the camera buttons, the map, the radar, saved areas,
   dive logs, settings or the config panel with it — those are client-owned and
   keep working with the Pi switched off.

   Controls declare what they need in markup:  <button data-needs="link">
   and this module marks exactly those elements. Reconnection is automatic and
   silent (net.js), so there are no retry buttons anywhere.
   ============================================================================ */
const STATUS = {
  internet: true,
  link: 'offline',      // online | connecting | offline
  video: 'down',        // live | connecting | down
  cam: 'down',          // ok | degraded | down
  nav: 'down',          // ok | down
  vehicle: 'idle',
  _last: '', _lastGate: ''
};

/* How long a subsystem may go quiet before we call it down. The camera control
   plane pushes every ~15 s and is polled every 5 s, so it needs a wider window
   than the 30 Hz control link. */
const CAM_SILENT_MS = 20000;
const NAV_SILENT_MS = 5000;

/* VEHICLE COMMANDS ONLY. Blocked when we INTEND to talk to a real Pi (a host is
   configured) but the control link is not up. In pure disk/SIM mode there is no
   vehicle to endanger, so the simulator's controls stay live.
   Anything that is not a vehicle command must NOT consult this. */
function commandsBlocked(){ return !!state.wsBase && state.wsStatus !== 'online'; }

/* Per-subsystem predicates — used by the UI gating and by each panel. */
function linkUp(){   return state.wsStatus === 'online'; }
function videoUp(){  return state.video === 'live'; }
function camUp(){    return STATUS.cam === 'ok' || STATUS.cam === 'degraded'; }
function navUp(){    return STATUS.nav === 'ok'; }

STATUS.tick = function(){
  const now = Date.now();

  STATUS.internet = navigator.onLine !== false;

  // ---- ROV control link -------------------------------------------------
  if(!state.wsBase)                     STATUS.link = 'sim';
  else if(state.wsStatus === 'online')  STATUS.link = 'online';
  else if(state.wsStatus === 'connecting') STATUS.link = 'connecting';
  else                                  STATUS.link = 'offline';

  // ---- video plane (independent of the control link) ---------------------
  if(state.video === 'live')            STATUS.video = 'live';
  else if(state.video === 'connecting' || state.video === 'reconfiguring') STATUS.video = 'connecting';
  else                                  STATUS.video = 'down';

  // ---- camera control plane (independent of both) ------------------------
  if(!state.camOkAt || (now - state.camOkAt) > CAM_SILENT_MS) STATUS.cam = 'down';
  else if(state.cam && state.cam.degraded)                    STATUS.cam = 'degraded';
  else                                                        STATUS.cam = 'ok';

  // ---- navigation feed ---------------------------------------------------
  STATUS.nav = (state.navOkAt && (now - state.navOkAt) <= NAV_SILENT_MS) ? 'ok' : 'down';

  // ---- vehicle -----------------------------------------------------------
  // There are only two states worth distinguishing at a glance: is there a real
  // vehicle on the other end, or not. "No host configured" and "host configured but
  // unreachable" both mean SIM to the operator, and the old code split them into
  // 'sim' and an em-dash that rendered as muted grey - so the sub icon said nothing
  // in the exact situation where it most needed to shout.
  if(STATUS.link !== 'online')  STATUS.vehicle = 'sim';    // no live vehicle -> RED
  else if(state.alarmLeak)      STATUS.vehicle = 'fault';  // leak -> pulsing red
  else                          STATUS.vehicle = state.armed ? 'armed' : 'idle';

  STATUS.applyGates();
  // BLIND NAV: with no feed, hand the screen to the map so the sub can still be
  // driven. Lives here because this is where video state is already resolved; the
  // mode itself is debounced in map.js so a brief hiccup does not flip the view.
  try{ if(typeof updateBlindNav==='function') updateBlindNav(); }
  catch(e){ LOG.warn('blind-nav update failed:', e && e.message); }

  const sig = [STATUS.internet, STATUS.link, STATUS.video, STATUS.cam, STATUS.nav, STATUS.vehicle].join('|');
  if(sig !== STATUS._last){
    STATUS._last = sig;
    STATUS.render();
    if(window.REC && REC.enabled) REC.log('status', {
      internet:STATUS.internet, link:STATUS.link, video:STATUS.video,
      cam:STATUS.cam, nav:STATUS.nav, vehicle:STATUS.vehicle });
  }
};

/* Mark ONLY the controls whose own subsystem is down. Everything without a
   data-needs attribute is client-owned and is never gated.

   Two DIFFERENT kinds of "down", because they need different treatment:

     SIMULATED  the vehicle link is absent, but every vehicle control still works
                against the local simulator. Nothing is transmitted. The control
                stays fully operable and is tinted, not disabled — the console has
                to be flyable on the bench with the Pi unplugged.

     GATED      the subsystem is genuinely unavailable and there is nothing to
                simulate. Camera REC/PIC with no camera is a real dead end, so
                those are disabled rather than pretending. */
STATUS.applyGates = function(){
  const down = {
    link:  false,                 // never hard-disables: link loss => simulate
    cam:   !camUp(),
    video: !videoUp(),
    nav:   !navUp()
  };
  const simulated = commandsBlocked();
  const gateSig = simulated+'|'+down.cam+'|'+down.video+'|'+down.nav+'|'+STATUS.link;
  if(gateSig === STATUS._lastGate) return;      // DOM churn only on an actual change
  STATUS._lastGate = gateSig;

  const b = document.body.classList;
  b.toggle('link-sim',        simulated);
  b.toggle('link-connecting', STATUS.link === 'connecting');
  b.toggle('cam-down',        down.cam);
  b.toggle('video-down',      down.video);
  b.toggle('nav-down',        down.nav);

  document.querySelectorAll('[data-needs]').forEach(el=>{
    const needs   = (el.getAttribute('data-needs') || '').split(/\s+/).filter(Boolean);
    const blocked = needs.some(n => down[n]);
    const sim     = simulated && needs.indexOf('link') !== -1;
    el.classList.toggle('gated', blocked);
    el.classList.toggle('simulated', sim && !blocked);
    el.setAttribute('aria-disabled', blocked ? 'true' : 'false');
  });

  // No banner at all any more. SIM was spelled across the screen when the vehicle icon
  // already shows it, and CAMERA OFFLINE duplicated the eye. Both cost real estate on a
  // 7-inch handheld to repeat something a glyph in the status row states continuously.
  const banner = $('controls-disabled');
  if(banner){ banner.textContent=''; banner.classList.remove('show'); }
};

/* ROV — ONE icon for what used to be two. "Is the link up" and "is there a vehicle"
   were never separate questions to the operator: they are the same question about the
   same cable. The SHAPE says which state we are in, so it survives being read at a
   glance, in sunlight, by someone who is also driving:

     red robot   nothing on the end of the tether — the simulator is flying this
     amber plug  connecting; the cable is there, the handshake is not finished
     green sub   a real vehicle is answering                                        */
const ROV_ROBOT = '<svg viewBox="0 0 24 24"><rect x="4.5" y="8.5" width="15" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="9.5" cy="13.5" r="1.4" fill="currentColor"/><circle cx="14.5" cy="13.5" r="1.4" fill="currentColor"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 8.5V5.5"/><circle cx="12" cy="4" r="1.5" fill="currentColor"/></svg>';
const ROV_PLUG  = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M7.5 10.5h9v5.5a2.5 2.5 0 0 1-2.5 2.5h-4a2.5 2.5 0 0 1-2.5-2.5z"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 10.5V4"/><path fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" d="M10 12.5v2M12 12.5v2M14 12.5v2"/></svg>';
const ROV_SUB   = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M4 12c0-2.2 3.6-4 8-4s8 1.8 8 4-3.6 4-8 4-8-1.8-8-4z"/><circle cx="9" cy="12" r="1.1" fill="#0c0118"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 8V5M9 5h6"/></svg>';

/* The video status icon. Open eye = we can see; struck-through = we cannot. */
const EYE_LIVE_SVG  = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.2" fill="currentColor"/></svg>';
const EYE_BLIND_SVG = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.2" fill="currentColor"/><path stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M3.5 20.5 20.5 3.5"/></svg>';

STATUS.render = function(){
  const set = (id, cls, title)=>{ const el = $(id); if(!el) return;
    el.className = 'st-ic ' + cls; liveTitle(el, title); };
  set('st-net', STATUS.internet ? 'ok' : 'warn', 'Internet: ' + (STATUS.internet ? 'online' : 'offline'));


  // VIDEO — an EYE, because that is what it is: are we seeing the water or not.
  // Open + green = live feed. Struck through + red = blind, the map is the driving
  // view. This replaced a full-width BLIND NAV banner that ate the top of the screen
  // to say the same thing; one glyph in a row of status icons says it as well and
  // costs nothing. `set` is called after so the class still drives the colour.
  const vidCls = STATUS.video === 'live' ? 'ok' : (STATUS.video === 'connecting' ? 'warn' : 'down');
  const vEl = $('st-video');
  if(vEl) vEl.innerHTML = (STATUS.video === 'live') ? EYE_LIVE_SVG : EYE_BLIND_SVG;
  set('st-video', vidCls, STATUS.video === 'live'
        ? 'Video: live feed'
        : 'Video: ' + STATUS.video + ' — BLIND, driving on the map');

  // ONE ROV icon: shape for the state, colour to match. A leak keeps the sub shape
  // (there IS a vehicle) but goes pulsing red, so a fault never looks like a dropout.
  const rov=$('st-rov');
  let rGlyph, rCls, rTitle;
  if(STATUS.link === 'connecting'){
    rGlyph=ROV_PLUG;  rCls='warn'; rTitle='ROV link: connecting…';
  } else if(STATUS.link === 'online'){
    rGlyph=ROV_SUB;
    if(STATUS.vehicle === 'fault'){ rCls='bad'; rTitle='Vehicle: FAULT — leak detected'; }
    else { rCls='ok'; rTitle='Vehicle: connected (' + STATUS.vehicle + ')'; }
  } else {
    rGlyph=ROV_ROBOT; rCls='sim';
    rTitle='No vehicle — simulating (link ' + STATUS.link + ')';
  }
  if(rov && rov.dataset.glyph !== rCls){ rov.dataset.glyph = rCls; rov.innerHTML = rGlyph; }
  set('st-rov', rCls, rTitle);
};

/* ---- REAL Pi health (/api/system) -----------------------------------------
   Its own poll on its own schedule, deliberately separate from the control
   WebSocket: the Pi can be perfectly healthy while the ROV link is down, and the
   operator needs to see that difference. One request in flight at a time, hard
   abort deadline, capped backoff — a black-holed Pi must not accumulate requests. */
let _sysPolling = false, _sysBackoff = 0, _sysTimer = null;
function startSystemPoll(){
  const tick = async ()=>{
    if(_sysPolling) return;
    _sysPolling = true;
    const ctl = (typeof AbortController!=='undefined') ? new AbortController() : null;
    const killer = ctl ? setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, 4000) : null;
    try{
      const r = await fetch((state.httpBase||'') + '/api/system', ctl ? {signal:ctl.signal} : undefined);
      if(!r.ok) throw new Error('http '+r.status);
      state.sys = await r.json();
      state.sysAt = Date.now();
      _sysBackoff = 0;
      if(typeof renderSystem==='function') renderSystem(state.sys);
    }catch(e){
      _sysBackoff = Math.min(30000, _sysBackoff ? _sysBackoff*2 : 2000);
      if(Date.now()-state.sysAt > 15000){ state.sys=null; if(typeof renderSystem==='function') renderSystem(null); }
    }finally{
      if(killer) clearTimeout(killer);
      _sysPolling = false;
      clearTimeout(_sysTimer);
      _sysTimer = setTimeout(tick, Math.max(3000, _sysBackoff));
    }
  };
  tick();
}

function initStatus(){
  setInterval(STATUS.tick, 500);
  STATUS.tick();
  startSystemPoll();
}

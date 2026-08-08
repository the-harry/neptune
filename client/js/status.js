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
  camLink: 'gone',      // connected | radio | gone  (the eye)
  camBy: 'none',        // who can see the camera's radio: ally | pi | none
  piSeen: false,        // the Pi ANSWERS on HTTP, even if the control link is not up
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
   Anything that is not a vehicle command must NOT consult this.

   `state.wsBase` AND NOTHING ELSE. The handheld now runs a MAP backend of its own —
   areas, chart layers, depth, downloads — and it answers on the same /api/ prefix the
   Pi does. It is not a vehicle and must never widen this test: a console with a local
   map API and nothing on the tether is still a console with no sub, and the moment
   this starts reading `httpBase` or "an API answered", the simulator's controls stop
   being simulated in the operator's eyes without a hull ever appearing. */
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
  // 'sim' here means NO VEHICLE IS ADDRESSED, and it is decided by the vehicle base
  // alone. The map's backend is deliberately not consulted: a handheld serving its own
  // charts has no sub on the end of the tether, and this line is where that would first
  // be blurred if anyone ever taught it to look at `dataBase`.
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

  // ---- the camera, as ONE state -------------------------------------------
  // Ordered by how much is actually working, because the operator's next move
  // differs at each step. "The radio is there" is the Pi's own wlan0 association
  // or a readable signal: a browser cannot scan Wi-Fi, and the Pi's link is the
  // one that matters anyway, since the Pi is what talks to the camera.
  // TWO observers, because one is not enough. The Pi's own wlan0 says whether IT is
  // associated — but if the Pi's antenna dies while the camera is happily
  // broadcasting, the Pi sees nothing and would report the camera dead. The handheld
  // is standing right there with a radio of its own, so the launcher scans for the
  // camera's SSID (/__wifi) and that second opinion settles it: AP visible, Pi silent
  // means the fault is on the Pi's side and the camera is fine. Amber, not red.
  // What counts as "the Pi can see the camera's radio" is ASSOCIATION, not an
  // interface being enabled. `camera.up` only means wlan0 exists and is not down,
  // which is true on every Pi that has ever been booted — so using it pinned the eye
  // to amber forever, including with the camera switched off in another building.
  // iwgetid returns nothing unless genuinely joined to an AP, and TCP 554 answering
  // is stronger still.
  const deep = (state.sys && state.sys.deep) || {};
  const piSeesRadio = !!((deep.ssid && String(deep.ssid).trim()) || deep.camera_reachable);
  // Only a POSITIVE and FRESH sighting counts. An unavailable scan (no launcher, radio
  // off, no SSID configured) means "cannot tell", which is not evidence of absence —
  // but neither is a sighting from a minute ago evidence of presence. A stale result
  // is dropped rather than believed, so the eye goes back to red when the camera is
  // carried out of range rather than sitting on amber until the next poll lands.
  const apFresh = !!(state.camAp && state.camAp.at &&
                     (now - state.camAp.at) < (CONFIG.camera.apScanMaxAgeMs || 20000));
  const allySeesAp = !!(apFresh && state.camAp.visible === true);
  STATUS.camBy = allySeesAp ? 'ally' : (piSeesRadio ? 'pi' : 'none');
  if(STATUS.cam === 'ok' || STATUS.cam === 'degraded') STATUS.camLink = 'connected';
  else if(piSeesRadio || allySeesAp)                   STATUS.camLink = 'radio';
  else                                                 STATUS.camLink = 'gone';

  // ---- navigation feed ---------------------------------------------------
  STATUS.nav = (state.navOkAt && (now - state.navOkAt) <= NAV_SILENT_MS) ? 'ok' : 'down';

  // ---- is the Pi THERE, independently of the control link? -----------------
  // A WebSocket sitting in `connecting` is not evidence of anything: it says that
  // way for as long as the handshake has not failed, which against an address that
  // will never answer is indefinitely. That is what pinned the sub icon to amber for
  // an entire session with no Pi in the building. Amber has to mean something
  // POSITIVE — the Pi answered — so it comes from an HTTP probe, and a stale answer
  // is dropped rather than believed, exactly as for the camera.
  const probeFresh = !!(state.piProbe && state.piProbe.at &&
                        (now - state.piProbe.at) < (CONFIG.piProbeMaxAgeMs || 15000));
  STATUS.piSeen = !!(probeFresh && state.piProbe.ok);

  // ---- vehicle -----------------------------------------------------------
  // There are only two states worth distinguishing at a glance: is there a real
  // vehicle on the other end, or not. "No host configured" and "host configured but
  // unreachable" both mean SIM to the operator, and the old code split them into
  // 'sim' and an em-dash that rendered as muted grey - so the sub icon said nothing
  // in the exact situation where it most needed to shout.
  // FLOOD, and only FLOOD, takes the vehicle to `fault`. The leak is two stages now:
  // WARN means water is collecting and the answer is to finish up, which is an
  // advisory and must not paint the tether icon as a failure — if every stage pulsed
  // red, the pulse would stop meaning "surface now" within one damp afternoon. WARN
  // is carried by the leak drop (which changes SHAPE) and by its advisory chip.
  if(STATUS.link !== 'online')     STATUS.vehicle = 'sim';    // no live vehicle -> RED
  else if(leakStage()==='FLOOD')   STATUS.vehicle = 'fault';  // flooding -> pulsing red sub
  else                             STATUS.vehicle = state.armed ? 'armed' : 'idle';

  STATUS.applyGates();
  // BLIND NAV: with no feed, hand the screen to the map so the sub can still be
  // driven. Lives here because this is where video state is already resolved; the
  // mode itself is debounced in map.js so a brief hiccup does not flip the view.
  try{ if(typeof updateBlindNav==='function') updateBlindNav(); }
  catch(e){ LOG.warn('blind-nav update failed:', e && e.message); }

  const nwv = (state.net && state.net.wifi) || {}, nev = (state.net && state.net.eth) || {};
  const sig = [STATUS.internet, STATUS.link, STATUS.video, STATUS.cam, STATUS.nav,
               STATUS.vehicle, STATUS.camLink, STATUS.camBy, STATUS.piSeen,
               // The leak STAGE is in the signature even though only FLOOD reaches
               // STATUS.vehicle: a WARN qualifies the tether tooltip, and without it
               // here that sentence would only appear if something else happened to
               // change on the same tick.
               leakStage(),
               // The map backend is settled ASYNCHRONOUSLY (core.js asks the launcher on
               // /__api after boot), and the tether tooltip quotes it. Without it in the
               // signature that sentence would only appear whenever something else
               // happened to change on the same tick.
               state.dataFrom,
               nwv.nic, nwv.up, nwv.internet, nwv.ssid, nev.nic, nev.up].join('|');
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

/* WI-FI, in one glyph. Four states, because "no card", "not joined", "joined but no
   internet" and "working" need four different reactions and only the last is fine.
   The middle two are both amber; the BLINK separates them, so colour alone never has
   to carry it. All of it comes from the launcher (/__net): a browser cannot enumerate
   adapters, and navigator.onLine cannot tell a network from the internet. */
const WIFI_SVG  = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8 15.5a5 5 0 0 1 8 0"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/></svg>';
const WIFI_NONE = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8 15.5a5 5 0 0 1 8 0"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/><path stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M3.5 20.5 20.5 3.5"/></svg>';

/* THE TETHER — the cable from this handheld to the sub. Its red state is deliberately
   about the CABLE, not the vehicle: with no ethernet-style adapter on the handheld
   there is nothing for a sub to be on the end of. */
const TETH_NONE = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M3 12h5M16 12h5"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M8 9.5h2.5v5H8zM13.5 9.5H16v5h-2.5z"/><path stroke="currentColor" stroke-width="2.2" stroke-linecap="round" d="M4 20 20 4"/></svg>';

/* THE CAMERA, in one glyph.
   
   Three states, because there are three genuinely different situations and the
   operator's next action differs in each:

     green, open      the Pi is talking to the camera. Nothing to do.
     amber, open,     the camera's radio is there but the Pi is not getting anything
     blinking         from it — it is booting, dropped its association, or the control
                      plane has gone quiet. Wait, or power-cycle the camera. Blinking
                      because this one is transient by nature and worth noticing.
     red, crossed     no radio and no camera. The map is the driving view now.

   What "the radio is there" means in practice: wlan0 is ASSOCIATED with an AP, or
   still reports a signal, per /proc/net/wireless on the Pi. A browser cannot scan
   Wi-Fi itself, so the Pi's own view of its camera link is the honest source — and
   it is the link that actually matters, since the Pi is what talks to the camera. */
const EYE_LIVE_SVG  = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.2" fill="currentColor"/></svg>';
const EYE_BLIND_SVG = '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.2" fill="currentColor"/><path stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M3.5 20.5 20.5 3.5"/></svg>';

/* THE CLAUSE THAT STOPS "NO SUB" FROM BEING READ AS "NOTHING ON THIS SCREEN IS REAL".

   The tether glyph is the loudest thing on the status row, and when it is the red robot
   it says the simulator is flying this. That sentence is about the VEHICLE and always
   was, but with the map now served from the handheld an operator can be looking at a
   red robot and a fully drawn chart at the same time — and the natural reading of the
   red robot is "so none of this is real". It is worth one clause to say which half is
   which: the sub is a model, the water is not.

   TWO THINGS HAVE TO BE TRUE BEFORE IT IS SAID, and both are about not swapping one
   empty claim for another:

     somebody NAMED the backend — the launcher on /__api, a ?data= override, config, or
       the Pi in disk mode. `dataFrom:'origin'` is not that: it is the provisional guess
       "whoever served this page probably has the charts", which is right on a Pi and
       wrong on GitHub Pages, and a guess must not be quoted to the operator as a fact
       about what this handheld is holding.
     it is somewhere OTHER than the sub — served from the Pi the chart data is on the
       Pi too, so a Pi that is not answering is not answering for maps either.  */
const DATA_NAMED = ['launcher','override','config','vehicle'];
function tetherMapNote(){
  if(typeof hasDataBackend!=='function' || !hasDataBackend()) return '';
  if(DATA_NAMED.indexOf(state.dataFrom) < 0) return '';
  if(state.dataHost && state.host && state.dataHost === state.host) return '';
  return '. The MAP is not simulated: its imagery, hazard layers and chart data are real, '
       + 'they are served from ' + (state.dataHost || 'this handheld')
       + ', and they do not need a sub';
}

STATUS.render = function(){
  const set = (id, cls, title)=>{ const el = $(id); if(!el) return;
    el.className = 'st-ic ' + cls; liveTitle(el, title); };
  // WI-FI. With no launcher to ask, all we have is navigator.onLine, which knows there
  // is a connection but not whether it reaches anything - so the no-card and
  // no-internet states cannot be told apart there, and the tooltip says so.
  const nw = (state.net && state.net.wifi) || null;
  const nEl = $('st-net');
  let nCls, nGlyph, nTitle;
  if(nw){
    if(!nw.nic){
      nCls='down'; nGlyph=WIFI_NONE; nTitle='no wireless adapter on this handheld';
    } else if(!nw.up){
      nCls='warn'; nGlyph=WIFI_SVG;  nTitle='wireless adapter present, not joined to a network';
    } else if(!nw.internet){
      nCls='warn blink'; nGlyph=WIFI_SVG;
      nTitle='joined to "' + (nw.ssid||'a network') + '", but it has no internet';
    } else {
      nCls='ok'; nGlyph=WIFI_SVG;
      nTitle='connected to "' + (nw.ssid||'a network') + '" with internet';
    }
  } else {
    nCls = STATUS.internet ? 'ok' : 'warn'; nGlyph = WIFI_SVG;
    nTitle = (STATUS.internet ? 'online' : 'offline') +
             ' (no launcher here, so the adapter itself cannot be checked)';
  }
  if(nEl && nEl.dataset.glyph !== nCls){ nEl.dataset.glyph = nCls; nEl.innerHTML = nGlyph; }
  set('st-net', nCls, nTitle);


  // THE CAMERA — one eye, three states. This is the ONLY camera indicator: the
  // CAM WIFI readout and the CAMERA LINK DEGRADED banner both said a piece of the
  // same thing somewhere else on the screen, which is the repetition this interface
  // is meant to avoid.
  const vEl = $('st-video');
  let camCls, camGlyph, camTitle;
  if(STATUS.camLink === 'connected'){
    camCls='ok'; camGlyph=EYE_LIVE_SVG;
    camTitle = (STATUS.video === 'live') ? 'connected, live picture'
                                         : 'connected to the Pi (no picture yet)';
  } else if(STATUS.camLink === 'radio'){
    camCls='warn blink'; camGlyph=EYE_LIVE_SVG;
    camTitle = (STATUS.camBy === 'ally')
      ? 'this handheld can see the camera’s Wi-Fi, but the sub is getting nothing '
        + 'from it — the camera is alive, the sub’s side of the link is not'
      : 'the camera’s Wi-Fi is there, but the sub is getting nothing from it';
  } else {
    camCls='down'; camGlyph=EYE_BLIND_SVG;
    camTitle = 'no camera and no Wi-Fi — BLIND, the map is the driving view';
  }
  if(vEl && vEl.dataset.eye !== camCls){ vEl.dataset.eye = camCls; vEl.innerHTML = camGlyph; }
  set('st-video', camCls, camTitle);

  // ONE ROV icon: shape for the state, colour to match. A leak keeps the sub shape
  // (there IS a vehicle) but goes pulsing red, so a fault never looks like a dropout.
  // THE TETHER, from this handheld's side. Ordered by how much of the path works:
  //   green   the sub's API is answering - the whole chain is good
  //   amber   a cable-style adapter is there, but nothing is answering on it
  //   red     no such adapter at all: no cable for a sub to be on the end of
  // A socket sitting in `connecting` is NOT evidence and never reaches amber: it says
  // that for as long as the handshake has not failed, which against an address that
  // will never answer is indefinitely. That is what pinned this to amber all session.
  const ne = (state.net && state.net.eth) || null;
  const rov=$('st-rov');
  let rGlyph, rCls, rTitle;
  if(STATUS.link === 'online'){
    // Green is the whole chain: cable, API, and the control link carrying commands.
    rGlyph=ROV_SUB;
    if(STATUS.vehicle === 'fault'){
      rCls='bad';
      rTitle='FLOODING - water is above the upper probe. SURFACE NOW. The sub is still '
           + 'answering: this is a hull fault, not a lost link';
    }
    else {
      rCls='ok';
      rTitle='connected to the sub (' + STATUS.vehicle + ')'
           + (leakStage()==='WARN' ? ' - water is collecting inside; finish up' : '');
    }
  } else if(STATUS.piSeen){
    // Answers on HTTP but the control socket is not up: booting, API restarting.
    // Same PLUG as the cable-only case, because it is the same story - something is
    // there and it is not talking to us yet - only further along.
    rGlyph=ROV_PLUG; rCls='warn blink';
    rTitle='the sub answers, but the control link is not up yet'
         + (state.piProbe && state.piProbe.ms!=null ? ' (' + state.piProbe.ms + ' ms)' : '');
  } else if(ne && ne.nic){
    rGlyph=ROV_PLUG; rCls='warn';
    rTitle='cable adapter "' + (ne.name||'?') + '" is there, but nothing is answering on it';
  } else if(ne){
    rGlyph=TETH_NONE; rCls='down';
    rTitle='no cable: this handheld has no ethernet-style adapter - the simulator is flying this'
         + tetherMapNote();
  } else {
    // No launcher, so the adapters cannot be checked at all. Say that, rather than
    // claiming a cable is missing on evidence we do not have.
    rGlyph=ROV_ROBOT; rCls='sim';
    rTitle = (!state.wsBase
      ? 'no sub configured - the simulator is flying this'
      : 'nothing answering at ' + state.host + ' - the simulator is flying this')
      + tetherMapNote();
  }
  if(rov && rov.dataset.glyph !== rCls){ rov.dataset.glyph = rCls; rov.innerHTML = rGlyph; }
  // The old pulse keyed on STATUS.link==='connecting', which had the same problem.
  document.body.classList.toggle('link-connecting', false);
  set('st-rov', rCls, rTitle);
};

/* ---- Is the Pi THERE? (HTTP, independently of the control WebSocket) ---------

   Same shape as the camera probe and for the same reason: amber must mean the sub
   answered, not that a socket has not given up yet. Runs ONLY while the control link
   is down — once it is online the answer cannot change anything — and its result
   expires, so a Pi that is unplugged goes red rather than sitting amber on the
   strength of a probe from a minute ago.

   AN EXPLICIT VEHICLE ADDRESS, OR NO PROBE AT ALL. This used to fetch
   `(state.httpBase||'') + '/api/healthz'`, and that `||''` is a RELATIVE url — it asks
   whoever served this page whether the sub is there. That was inert while the launcher
   served nothing but files and the request 404'd. It is not inert now: this handheld
   runs a map backend of its own, /api/healthz is the same FastAPI health endpoint, and
   it answers {"status":"ok","hardware":"mock"} in a few milliseconds. The console would
   have read its own machine's reply as the sub answering and lit the amber plug — "the
   sub answers, but the control link is not up yet" — with nothing whatsoever on the
   tether. So the probe requires a host that was configured as a VEHICLE, and a local
   map API is never allowed to stand in for one. */
function startPiProbe(){
  const tick = async ()=>{
    const wanted = !!state.wsBase && !!state.httpBase && state.wsStatus !== 'online';
    if(wanted){
      const t0 = performance.now();
      const ctl = (typeof AbortController!=='undefined') ? new AbortController() : null;
      const killer = ctl ? setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, 2500) : null;
      try{
        const r = await fetch(state.httpBase + '/api/healthz',
                              Object.assign({cache:'no-store'}, ctl ? {signal:ctl.signal} : {}));
        state.piProbe = { ok: r.ok, at: Date.now(), ms: Math.round(performance.now()-t0) };
      }catch(e){
        state.piProbe = { ok:false, at: Date.now(), ms:null };
      }finally{ if(killer) clearTimeout(killer); }
    }
    setTimeout(tick, wanted ? (CONFIG.piProbeMs || 4000) : (CONFIG.piProbeIdleMs || 10000));
  };
  tick();
}

/* ---- The HANDHELD's own view of the camera's Wi-Fi (launcher /__wifi) --------

   Served only by the local launcher, so it simply does not exist when the dashboard
   is opened from the Pi, from GitHub Pages, or from disk. That is fine and expected:
   `available:false` means "cannot tell", the eye falls back to the Pi's own view, and
   nothing anywhere treats a missing second opinion as bad news. */
function startCamApPoll(){
  let noLauncher = 0;
  const tick = async ()=>{
    // Ask ONLY while the camera is not connected. When it is green the answer cannot
    // change anything, and a scan is a second of CPU and a radio sweep on a handheld
    // that is flying a submarine. The moment it drops to red this starts again by
    // itself, because that is when the second opinion is worth having.
    // Keep asking even when the camera is green: this call now also carries the Wi-Fi
    // and cable state, which change on their own and are never "settled". It is one
    // cached HTTP call to loopback. Only the RATE backs off once the camera is up.
    const wanted = noLauncher < 3;
    const hot = STATUS.camLink !== 'connected';
    if(wanted){
      try{
        const r = await fetch('/__net', {cache:'no-store'});
        if(!r.ok) throw new Error('HTTP ' + r.status);
        const j = await r.json();
        const cam = j.camera || j;                     // new shape, or the old flat one
        state.net = j.ok ? { wifi: j.wifi || null, eth: j.eth || null, at: Date.now() } : null;
        state.camAp = { available: !!j.ok && !!cam.want,
                        visible: (j.ok && cam.want) ? !!cam.visible : null,
                        want: cam.want || '', ssids: cam.ssids || [], error: cam.error || null,
                        ageS: j.age_s, at: Date.now() };
        if(j.ok && !j.want && !state._camApWarned){
          state._camApWarned = true;
          LOG.map('camera Wi-Fi check idle: put the camera AP’s SSID in '
                + 'client/launch/neptune-camera-ssid.txt and the eye can tell a dead Pi '
                + 'antenna from a dead camera');
        }
        if(j.error && !state._camApErr){ state._camApErr = true; LOG.warn('camera Wi-Fi scan: ' + j.error); }
        noLauncher = 0;
      }catch(e){
        // No launcher on this origin (the Pi, GitHub Pages, disk). Give up after a
        // few tries and record that the second opinion is simply unavailable — which
        // is NOT the same as the camera being absent.
        noLauncher++;
        state.net = null;
        state.camAp = { available:false, visible:null, at:Date.now(),
                        error:'no launcher on this origin' };
      }
    }
    setTimeout(tick, hot ? (CONFIG.camera.apScanMs || 5000)
                         : (CONFIG.camera.apScanIdleMs || 15000));
  };
  tick();
}

/* ---- REAL Pi health (/api/system) -----------------------------------------
   Its own poll on its own schedule, deliberately separate from the control
   WebSocket: the Pi can be perfectly healthy while the ROV link is down, and the
   operator needs to see that difference. One request in flight at a time, hard
   abort deadline, capped backoff — a black-holed Pi must not accumulate requests.

   IT ASKS THE VEHICLE OR IT ASKS NOBODY, and this one mattered more than the health
   probe because it was not gated at all. With no vehicle configured it fetched
   `/api/system` RELATIVE, which now lands on the map backend running on this handheld.
   That answer is a truthful description of THE ROG — its CPU, its RAM, its free disk,
   its adapters — and every one of those numbers would have been painted into the PI
   HEALTH tiles as the sub's. Worse, `state.sys.deep.ssid` is read by STATUS.tick as
   "the Pi can see the camera's radio", so the handheld's own Wi-Fi association would
   have turned the camera eye amber for a camera nobody was talking to. A local map API
   knows nothing about the sub and is never asked about it. */
let _sysPolling = false, _sysBackoff = 0, _sysTimer = null, _sysBlanked = false;
function startSystemPoll(){
  const tick = async ()=>{
    if(_sysPolling) return;
    if(!state.httpBase){
      // No vehicle addressed: the Pi's health is CANNOT-TELL, which renderSystem draws
      // as dashes. Not zeroes, and emphatically not this handheld's own numbers.
      if(!_sysBlanked){
        _sysBlanked = true;
        state.sys = null; state.sysAt = 0;
        if(typeof renderSystem==='function') renderSystem(null);
      }
      clearTimeout(_sysTimer);
      _sysTimer = setTimeout(tick, 5000);
      return;
    }
    _sysBlanked = false;
    _sysPolling = true;
    const ctl = (typeof AbortController!=='undefined') ? new AbortController() : null;
    const killer = ctl ? setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, 4000) : null;
    try{
      const r = await fetch(state.httpBase + '/api/system', ctl ? {signal:ctl.signal} : undefined);
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
  startCamApPoll();
  startPiProbe();
  setInterval(STATUS.tick, 500);
  STATUS.tick();
  startSystemPoll();
}

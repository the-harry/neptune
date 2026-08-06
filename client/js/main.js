"use strict";
/* ============================================================================
   MAIN — the RAF loop (poll input, choose data source, simulate, render) and
   bootstrap. Loaded LAST, after every module it calls is defined.
   ============================================================================ */
function frame(ts){
  const now=Date.now();
  let dt=(state.lastFrame? (ts-state.lastFrame):16)/1000;
  if(dt>0.1) dt=0.1; state.lastFrame=ts;

  computeInput(dt);
  pollLearn();

  // THREE data sources, in order of preference:
  //   real   fresh telemetry from the vehicle
  //   stale  a brief gap on a still-open socket — say so, don't invent numbers
  //   sim    no vehicle (or the link is gone) — run the model so the console stays
  //          flyable. Resumes from the last real values, which net.js mirrors in.
  //
  // The stale window is deliberately bounded. Falling back to sim only when realTel
  // was NEVER set meant that once the Pi had connected even once, losing the link
  // parked the dashboard in `stale` permanently: the simulator stopped, the ballast
  // and depth froze, and every control appeared dead while still accepting input.
  const age = now - state.realTelAt;
  const realFresh = state.realTel && age < CONFIG.staleTimeoutMs;
  const linkAlive = state.wsStatus === 'online';
  const stillWorthWaiting = state.realTel && linkAlive && age < (CONFIG.simFallbackMs || 3000);
  let view;
  if(realFresh){
    state.mode='real';
    view=viewFromState(false);
    if(state.realTel.mock) view.sim=true; // server-side mock still flags SIM badge
  } else if(stillWorthWaiting){
    state.mode='stale';
    view=viewFromState(false); view.stale=true;
  } else {
    if(state.mode!=='sim' && state.realTel) LOG.state('link gone — handing back to the simulator');
    state.mode='sim';
    simulate(dt);
    view=viewFromState(true);
  }
  // In SIM the badge shows; in REAL-but-mock also show it.
  if(view.sim && state.mode!=='stale') state.mode='sim';
  if(state.mode!==state._loggedMode){ state._loggedMode=state.mode; LOG.state('telemetry mode ->', state.mode); }

  renderUI(view);
  updateBadges();
  // §4.2 staleness: surface the age of the newest telemetry the operator is seeing (real link only)
  if(window.REC && REC.enabled){
    const age=REC.tickStaleness();
    const b=$('stale-badge');
    if(b){ const stale = state.mode!=='sim' && age>CONFIG.recorder.stalenessMs;
      b.classList.toggle('show', stale);
      if(stale){ const a=$('stale-age'); if(a) a.textContent=Math.round(age)+' ms'; } }
  }
  requestAnimationFrame(frame);
}

/* Go fullscreen on the FIRST user gesture (browsers block auto-fullscreen on
   load). After that first tap/key/touch the URL bar is hidden and it feels like
   an app. For a truly URL-less boot, launch Chrome in kiosk/app mode (see README). */
function enableAppFullscreen(){
  let done=false;
  const go=()=>{
    if(done) return; done=true;
    const el=document.documentElement;
    const req=el.requestFullscreen||el.webkitRequestFullscreen;
    if(req){ try{ req.call(el,{navigationUI:'hide'}); }catch(e){ try{ req.call(el); }catch(_){} } }
    ['pointerdown','keydown','touchstart'].forEach(ev=>window.removeEventListener(ev,go));
  };
  ['pointerdown','keydown','touchstart'].forEach(ev=>window.addEventListener(ev,go,{passive:true}));
}

/* Register the service worker so the dashboard is an installable PWA that launches
   and runs with NO network of any kind (§2). Skipped over file:// (no SW there —
   which is exactly why installing the PWA is the fix). */
function registerServiceWorker(){
  try{
    if('serviceWorker' in navigator && location.protocol!=='file:'){
      navigator.serviceWorker.register('sw.js').then(
        ()=>LOG.state('service worker registered (offline-capable PWA)'),
        (e)=>LOG.warn('service worker registration failed:', e && e.message));
    }
  }catch(e){/* ignore */}
}

/* ---- file:// guard (§1) — the SPA MUST be served over HTTPS from the Pi. From a file://
   origin the browser blocks geolocation (no origin fix) and cross-origin fetches to the Pi
   (no video/telemetry/maps), which degrades into confusing partial failures. Show a blocking
   explanation instead. A clearly-labelled SIM-only escape hatch preserves quick offline UI checks. */
function fileProtocolGuard(){
  if(location.protocol!=='file:') return false;
  const o=document.createElement('div'); o.id='file-guard';
  o.innerHTML =
    '<div class="fg-card">'+
      '<div class="fg-title">SERVE NEPTUNE OVER HTTPS</div>'+
      '<p>This dashboard is open from a <code>file://</code> path. In that mode the browser blocks '+
      '<b>geolocation</b> (the origin fix) and <b>network access to the Pi</b> (video, telemetry, maps) — '+
      'you would get confusing partial failures, not a working console.</p>'+
      '<p>Open it from the Pi instead:</p>'+
      '<div class="fg-url">https://&lt;pi-address&gt;/</div>'+
      '<p class="fg-small">Trust the Pi&rsquo;s self-signed certificate once on this handheld. '+
      'nginx on the Pi serves these same files over TLS. <code>localhost</code> is the only '+
      'insecure-origin exemption and does not apply to a remote Pi.</p>'+
      '<button id="fg-sim">Continue in SIM only — no map · no location · no video</button>'+
    '</div>';
  document.body.appendChild(o);
  const btn=o.querySelector('#fg-sim');
  if(btn) btn.addEventListener('click', ()=>{ o.remove(); state._fileSim=true; boot(true); });
  LOG.warn('file:// origin — serve over HTTPS from the Pi (blocking overlay shown)');
  return true;
}

/* EXIT — the ROG Ally has no physical keyboard, so "Alt+F4 to quit" is not an exit
   at all. Without an on-screen way out, a fullscreen dashboard could only be
   escaped by power-cycling the handheld. This asks the local launcher to stop its
   server (/__quit), then closes the window. */
function bindExit(){
  const b=$('btn-exit'); if(!b) return;
  b.addEventListener('click', async ()=>{
    b.disabled = true;
    try{ await fetch('/__quit', {method:'GET', cache:'no-store'}); }catch(e){/* server may already be gone */}
    try{ if(document.exitFullscreen && document.fullscreenElement) await document.exitFullscreen(); }catch(e){}
    try{ window.close(); }catch(e){}
    // If the browser refused window.close() (not a script-opened window), say so
    // rather than leaving the operator staring at an unchanged screen.
    setTimeout(()=>{
      b.disabled = false;
      if(!document.hidden) camToast && camToast('Server stopped — close the window to finish', 'warn');
    }, 1200);
  });
}

/* ---- BOOTSTRAP ---- */
async function boot(forced){
  if(!forced && fileProtocolGuard()) return;
  // Before anything renders: the display driver on this handheld takes the whole
  // machine down under sustained compositing load, so the expensive always-on
  // effects are off by default. See CONFIG.ui.reduceGpu and the note in styles.css.
  if(!CONFIG.ui || CONFIG.ui.reduceGpu !== false){
    document.body.classList.add('reduce-gpu');
    LOG.state('reduced-GPU rendering ON (backdrop blur + scan line disabled) - CONFIG.ui.reduceGpu');
  }
  resolveHost();
  enableAppFullscreen();
  LOG.state('boot — host="'+ (state.host||'(none, disk mode)') +'"  http="'+(state.httpBase||'(relative)')+'"  ws="'+(state.wsBase||'(none)')+'"');
  loadBindings();
  registerServiceWorker();                                  // PWA — offline app shell + tile cache (§2)
  try{ initStatus(); }catch(e){ LOG.warn('status init failed:', e && e.message); }   // degradation indicator (§3)
  // AWAIT the store before anything reads it. This used to be fire-and-forget, so
  // autoRequestOrigin() ran while IndexedDB was still opening, saw no saved origin,
  // and asked the browser for a position on EVERY boot - re-prompting for location
  // each launch even though an origin was already stored. initMap/initNavUI read the
  // same store, so they were racing it too.
  try{ await STORE.init(); }catch(e){ LOG.warn('store init failed:', e && e.message); }  // client owns its state (§1/§2)
  try{ REC.init(); }catch(e){ LOG.warn('recorder init failed:', e && e.message); }   // blackbox recorder (§4/§5)
  // Initial light lamp render (icons + glow)
  renderLightButton('green', state.lights.green.on, state.lights.green.level);
  renderLightButton('white', state.lights.white.on, state.lights.white.level);
  renderArmed(state.armed);
  renderMagnet(state.magnet);
  renderLeak(false);
  bindOnScreen();
  bindExit();
  buildMapper();
  connectVideo();     // WebRTC feed from go2rtc
  initCamera();       // WOLFANG camera control plane (telemetry, record/capture, config)
  try{ initMap(); }catch(e){ LOG.warn('map init failed (HUD unaffected):', e && e.message); }  // §7.4 error boundary
  try{ initNavUI(); }catch(e){ LOG.warn('nav UI init failed:', e && e.message); }             // origin + area manager
  try{ autoRequestOrigin(); }catch(e){ LOG.warn('auto-origin failed:', e && e.message); }      // §2 — device fix on load
  connect();
  startSendLoop();
  startPingLoop();
  startLevelLoop();
  requestAnimationFrame(frame);

  // Debug console API — try these in DevTools:
  //   NEPTUNE.log(false)      silence logging
  //   NEPTUNE.logRate(true)   log EVERY control tick + telemetry (verbose)
  //   NEPTUNE.state           inspect live state
  //   NEPTUNE.openMapper()    open the input remapper
  //   NEPTUNE.resetBindings() restore default input mapping
  window.NEPTUNE = {
    state, LOG, CONFIG, STORE, MAP,
    log:LOG.setEnabled, logRate:LOG.setHighRate,
    logs:openLogView, closeLogs:closeLogView, ring:()=>LOG.ring(), LOGVIEW,
    openMapper, closeMapper, resetBindings,
    connectVideo, camRecordToggle, camCapture,
    // Topside stills taken by PIC. `stills()` lists metadata; `openStill(id)`
    // pops one out of IndexedDB so a capture can be checked without a camera.
    stills:()=>STORE.stills(),
    openStill:async(id)=>{ const b=await STORE.stillBlob(id); if(!b) return null;
                           window.open(URL.createObjectURL(b), '_blank'); return b; },
    camUp, commandsBlocked,
    openOrigin:openOriginModal, openAreas:openAreaManager, requestLocation:requestDeviceLocation,
    // The sub cannot report where it is; the operator can see it. Arm a tap, or place
    // it straight from coordinates.
    setRov:armRovTap, setRovAt:(lat,lon)=>setRovLatLon(lat,lon), zoomMap,
    // Stand somewhere else to plan. mockMe() arms a tap; mockMeAt() places it directly;
    // clearMock() goes back to the live fix. The dot turns red either way.
    mockMe:armMockMeTap, mockMeAt:(lat,lon)=>setMockMe(lat,lon), clearMock:clearMockMe,
    meSource, showTracks:toggleTrack, breakTrack, geoCheck, axes:padAxes,
    // The DIVE LOGS button is gone from CONFIG (recorded dives live in
    // navigation_logs/), but the browser is still there rather than deleted -
    // removing a control should not silently remove the capability.
    openDives:openDiveLog,
    REC, mark:(n)=>REC.mark(n),
    // The session log writes itself; this only says where it is going.
    sessionLog:()=>({file:REC.diskFile, path:REC.diskPath||'', written:REC.diskWritten,
                     queued:REC.diskQueue.length, dropped:REC.diskDropped}),
    screenRecord,
    get bindings(){ return state.bindings; }
  };
  LOG.state('ready. Console API available as window.NEPTUNE (try NEPTUNE.logRate(true))');
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

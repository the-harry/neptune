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

  const realFresh = state.realTel && (now-state.realTelAt)<CONFIG.staleTimeoutMs;
  let view;
  if(realFresh){
    state.mode='real';
    view=viewFromState(false);
    if(state.realTel.mock) view.sim=true; // server-side mock still flags SIM badge
  } else if(state.realTel){
    state.mode='stale';
    view=viewFromState(false); view.stale=true;
  } else {
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

/* ---- BOOTSTRAP ---- */
function boot(forced){
  if(!forced && fileProtocolGuard()) return;
  resolveHost();
  enableAppFullscreen();
  LOG.state('boot — host="'+ (state.host||'(none, disk mode)') +'"  http="'+(state.httpBase||'(relative)')+'"  ws="'+(state.wsBase||'(none)')+'"');
  loadBindings();
  registerServiceWorker();                                  // PWA — offline app shell + tile cache (§2)
  try{ initStatus(); }catch(e){ LOG.warn('status init failed:', e && e.message); }   // degradation indicator (§3)
  try{ STORE.init(); }catch(e){ LOG.warn('store init failed:', e && e.message); }    // client owns its state (§1/§2)
  try{ REC.init(); }catch(e){ LOG.warn('recorder init failed:', e && e.message); }   // blackbox recorder (§4/§5)
  // Initial light lamp render (icons + glow)
  renderLightButton('green', state.lights.green.on, state.lights.green.level);
  renderLightButton('white', state.lights.white.on, state.lights.white.level);
  renderArmed(state.armed);
  renderMagnet(state.magnet);
  renderLeak(false);
  bindOnScreen();
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
    state, LOG, CONFIG,
    log:LOG.setEnabled, logRate:LOG.setHighRate,
    openMapper, closeMapper, resetBindings,
    connectVideo, camRecordToggle, camCapture,
    openOrigin:openOriginModal, openAreas:openAreaManager, requestLocation:requestDeviceLocation,
    REC, mark:(n)=>REC.mark(n), exportLog:()=>REC.exportLog(),
    get bindings(){ return state.bindings; }
  };
  LOG.state('ready. Console API available as window.NEPTUNE (try NEPTUNE.logRate(true))');
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

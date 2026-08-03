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

/* ---- BOOTSTRAP ---- */
function boot(){
  resolveHost();
  enableAppFullscreen();
  LOG.state('boot — host="'+ (state.host||'(none, disk mode)') +'"  http="'+(state.httpBase||'(relative)')+'"  ws="'+(state.wsBase||'(none)')+'"');
  loadBindings();
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
    get bindings(){ return state.bindings; }
  };
  LOG.state('ready. Console API available as window.NEPTUNE (try NEPTUNE.logRate(true))');
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

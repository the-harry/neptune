"use strict";
/* ============================================================================
   STATUS — the degradation model (architectural rule §3). Three states tracked
   and shown SEPARATELY because they fail independently:

     INTERNET  online | offline   → search, live tiles, new downloads
     BACKEND   up | down          → telemetry, video, ALL vehicle commands
     VEHICLE   armed | idle | fault

   Backend down disables controls + live data ONLY — never the map, search, saved
   areas, dive logs, or settings. One compact indicator; reconnection is automatic
   and silent (net.js), so there are no retry buttons anywhere.
   ============================================================================ */
const STATUS = { internet:true, backend:false, vehicle:'idle', _last:'' };

/* Commands are blocked when we INTEND to talk to a real Pi (a host is configured)
   but the link is down. In pure disk/SIM mode there is no vehicle to endanger, so
   the simulator's controls stay live. */
function commandsBlocked(){ return !!state.wsBase && state.wsStatus!=='online'; }

STATUS.tick = function(){
  STATUS.internet = navigator.onLine !== false;
  STATUS.backend  = state.wsStatus==='online';
  if(!state.wsBase)             STATUS.vehicle='sim';
  else if(!STATUS.backend)      STATUS.vehicle='—';
  else if(state.alarmLeak)      STATUS.vehicle='fault';
  else                          STATUS.vehicle=state.armed?'armed':'idle';

  // grey controls + live data ONLY when a real backend is expected but down
  document.body.classList.toggle('backend-down', commandsBlocked());

  const sig = STATUS.internet+'|'+STATUS.backend+'|'+STATUS.vehicle;
  if(sig!==STATUS._last){ STATUS._last=sig; STATUS.render();
    if(window.REC&&REC.enabled) REC.log('status', {internet:STATUS.internet, backend:STATUS.backend, vehicle:STATUS.vehicle}); }
};

STATUS.render = function(){
  const set=(id, on, txt, cls)=>{ const el=$(id); if(!el) return;
    el.className='st-pip '+cls; const v=el.querySelector('.st-v'); if(v) v.textContent=txt; };
  set('st-net',  STATUS.internet, STATUS.internet?'ONLINE':'OFFLINE', STATUS.internet?'ok':'warn');
  set('st-pi',   STATUS.backend,  STATUS.backend?'UP':'DOWN', STATUS.backend?'ok':'down');
  const vcls = STATUS.vehicle==='fault'?'bad' : (STATUS.vehicle==='armed'?'ok' : (STATUS.vehicle==='sim'?'sim':'idle'));
  set('st-veh', true, STATUS.vehicle.toUpperCase(), vcls);
};

function initStatus(){ setInterval(STATUS.tick, 500); STATUS.tick(); }

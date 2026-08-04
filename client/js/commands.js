"use strict";
/* ============================================================================
   COMMANDS — discrete actions. Each sends to the server AND updates the local
   state mirror so the UI reacts identically online or offline.
   ============================================================================ */
/* §4 — vehicle commands FAIL FAST and NEVER queue. If a real backend is expected
   but the link is down, the press is rejected immediately, logged, and NOT sent
   (no buffer, no replay — a late throttle is a hazard). Returns false if rejected.
   In pure disk/SIM mode there is no vehicle, so the simulator's controls stay live. */
function rejectCommand(name){
  LOG.warn('command rejected (backend down):', name);
  if(window.REC && REC.enabled) REC.log('cmd_rejected', {name, reason:'backend_down'});
  vibrate([40,40,40]);
  const el=$('controls-disabled'); if(el){ el.classList.add('flash'); setTimeout(()=>el.classList.remove('flash'),400); }
}
function cmd(name, value){
  if(typeof commandsBlocked==='function' && commandsBlocked()){ rejectCommand(name); return false; }
  LOG.cmd(name, (value!==undefined?value:''));
  // §3 correlation: a UUID at the moment of operator intent, carried through the socket and
  // echoed in the Pi's ack, so the whole 8-stage lifecycle ties together across both logs.
  const c_id = (window.REC && REC.enabled) ? REC.cmdIntent(name, value) : null;
  send({type:'command', name:name, value:value, c_id:c_id});   // send() is itself a no-op if the socket is gone — never a queue
  if(c_id) REC.cmdSend(c_id);
  return true;
}

// Each action sends the command FIRST and only mutates the local mirror when the
// command was accepted — so nothing fakes a vehicle response while the link is down.
function toggleArm(){
  const want=!state.armed;
  if(!cmd(want?'arm':'disarm')) return;
  state.armed=want; vibrate(15);
}
function eStop(){
  if(!cmd('stop')) return;
  state.armed=false;
  state.input.throttle=0; state.input.steer=0;
  fireScreenFlash();
  vibrate(40);
}
function surface(){
  if(!cmd('surface')) return;
  state.ballastTargetRaw=0; state.ballastTargetCmd=0;   // command tanks empty (and keep them empty after)
  state.surfaceUntil=Date.now()+CONFIG.sim.surfaceDrainMs;  // sim: force-drain regardless of chase deadband
  vibrate(20);
}
function toggleMagnet(){
  const want=!state.magnet;
  if(!cmd('magnet', want)) return;
  state.magnet=want; vibrate(10);
}
function toggleLight(which){
  const L=state.lights[which];
  const want=!L.on;
  if(!cmd(which==='green'?'light_green':'light_white', want)) return;
  L.on=want;
  if(L.on && L.level<CONFIG.lightOnThreshold){ L.level=CONFIG.lightOnDefault; state.levelDirty[which]=true; } // give it a visible level
  state.lastLight=which;
  vibrate(10);
}
function adjustLight(which, delta){
  if(typeof commandsBlocked==='function' && commandsBlocked()){ rejectCommand('light_'+which+'_level'); return; }
  const L=state.lights[which];
  const nv=clamp(L.level+delta, 0, 1);
  if(nv!==L.level){
    L.level=nv; state.levelDirty[which]=true; state.lastLight=which;
    const nowOn=nv>CONFIG.lightOnThreshold;        // dimming to 0 turns it off; raising turns it on
    if(nowOn!==L.on){ L.on=nowOn; cmd(which==='green'?'light_green':'light_white', L.on); }
  }
}
// Set a light's brightness directly from a pointer position on its gauge track.
function setLightLevel(which, level){
  if(typeof commandsBlocked==='function' && commandsBlocked()){ rejectCommand('light_'+which+'_level'); return; }
  const L=state.lights[which];
  level=clamp(level,0,1);
  L.level=level; state.levelDirty[which]=true; state.lastLight=which;
  const nowOn=level>CONFIG.lightOnThreshold;
  if(nowOn!==L.on){ L.on=nowOn; cmd(which==='green'?'light_green':'light_white', L.on); }
}

function fireScreenFlash(){
  const el=$('screen-flash');
  el.classList.remove('fire'); void el.offsetWidth; el.classList.add('fire');
}

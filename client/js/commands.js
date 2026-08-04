"use strict";
/* ============================================================================
   COMMANDS — discrete actions. Each sends to the server AND updates the local
   state mirror so the UI reacts identically online or offline.
   ============================================================================ */
/* §4 — vehicle commands NEVER queue. Nothing is ever buffered or replayed: a late
   `throttle 100%` is a hazard, and send() is itself a no-op on a closed socket.
   That rule is about TRANSMISSION, not about the console being usable.

   With no live link the dashboard is a SIMULATOR, and every control stays fully
   operable so it can be flown, rehearsed and demonstrated on the bench with the Pi
   unplugged. The command is applied to the local mirror ONLY — never transmitted,
   never stored to send later — and the HUD says SIM so the operator can always tell
   the difference. Disabling the rail instead just made the console look broken. */
function simulatedCommand(name, value){
  LOG.cmd('SIM', name, (value!==undefined?value:''));
  if(window.REC && REC.enabled) REC.log('cmd_sim', {name, value:value===undefined?null:value});
  vibrate(8);
  return true;                       // let the caller update the local mirror
}
function cmd(name, value){
  // No live vehicle -> simulate locally. Explicitly NOT sent and NOT queued.
  if(typeof commandsBlocked==='function' && commandsBlocked()) return simulatedCommand(name, value);
  LOG.cmd(name, (value!==undefined?value:''));
  // §3 correlation: a UUID at the moment of operator intent, carried through the socket and
  // echoed in the Pi's ack, so the whole 8-stage lifecycle ties together across both logs.
  const c_id = (window.REC && REC.enabled) ? REC.cmdIntent(name, value) : null;
  send({type:'command', name:name, value:value, c_id:c_id});   // send() is itself a no-op if the socket is gone — never a queue
  if(c_id) REC.cmdSend(c_id);
  return true;
}

// Each action goes through cmd() first. With a live link that transmits and the
// local mirror follows; with no link cmd() returns true having transmitted nothing,
// so the same code drives the simulator. One path, both modes.
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
  // No link check: level changes are local state, and the dirty-level pump in net.js
  // only transmits when the socket is open. Bailing out here was what made the
  // brightness sliders feel dead on the bench.
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

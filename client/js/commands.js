"use strict";
/* ============================================================================
   COMMANDS — discrete actions. Each sends to the server AND updates the local
   state mirror so the UI reacts identically online or offline.
   ============================================================================ */
function cmd(name, value){ LOG.cmd(name, (value!==undefined?value:'')); send({type:'command', name:name, value:value}); }

function toggleArm(){
  state.armed=!state.armed;
  cmd(state.armed?'arm':'disarm');
  vibrate(15);
}
function eStop(){
  cmd('stop');
  state.armed=false;
  state.input.throttle=0; state.input.steer=0;
  fireScreenFlash();
  vibrate(40);
}
function surface(){
  cmd('surface');
  state.ballastTargetCmd=0;                       // command tanks empty (and keep them empty after)
  state.surfaceUntil=Date.now()+CONFIG.sim.surfaceDrainMs;  // sim: force-drain regardless of chase deadband
  vibrate(20);
}
function toggleMagnet(){
  state.magnet=!state.magnet;
  cmd('magnet', state.magnet);
  vibrate(10);
}
function toggleLight(which){
  const L=state.lights[which];
  L.on=!L.on;
  if(L.on && L.level<CONFIG.lightOnThreshold){ L.level=CONFIG.lightOnDefault; state.levelDirty[which]=true; } // give it a visible level
  state.lastLight=which;
  cmd(which==='green'?'light_green':'light_white', L.on);
  vibrate(10);
}
function adjustLight(which, delta){
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

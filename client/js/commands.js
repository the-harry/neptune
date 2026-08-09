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
  // A LATCH, NOT A WRITE TO state.input — because the write did not survive the tick.
  // eStop runs from computeInput()'s edge-action pass, which has ALREADY read the
  // sticks into locals and then rebuilds `state.input` from those locals a few lines
  // later. So zeroing state.input here was undone within the same frame, and the
  // console went on transmitting full throttle at the control rate immediately after an
  // E-STOP — precisely when the stick is still deflected, which is the only way an
  // E-STOP is ever really pressed, because the hand that hits it is the hand that was
  // on the throttle.
  //
  // The latch is honoured where the map's all-stop is honoured (input.js, on `ni`), and
  // it clears only when the controls come back inside the deadzone: after an emergency
  // stop the operator RE-CENTRES before thrust returns, rather than the boat resuming
  // whatever the stick still happened to be asking for.
  state.estopHold = true;
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

/* RE-ARM THE LEAK DETECTOR — the way back from a latch that used to need a restart.

   A leak latch is one-way on the vehicle for a good reason: a probe drying out is
   not evidence the hull is sound. But one-way with no way back meant the only cure
   was restarting the service, i.e. SSH-ing into a submarine, so a bench test left
   the console stuck on an alarm for the rest of the session.

   THE CONSOLE DOES NOT DECIDE WHETHER THIS IS ALLOWED. The vehicle refuses it
   outright while either probe is wet, and refuses with a sentence saying so. That
   check has to live beside the pins, not here, or it is a check an operator can
   get around by using a different client. All this does is ask, and then show the
   answer — including "no".

   Local mirror deliberately NOT updated on send. Everywhere else in this file the
   simulator follows the command optimistically, which is right for a light. Here it
   would paint the hull green on the strength of having ASKED, which is the one
   claim on this console that has to come from the vehicle. */
function resetLeak(){
  if(typeof commandsBlocked==='function' && commandsBlocked()){
    // No vehicle to ask: clear the SIMULATED stage, so a sim leak drill can be re-run
    // without a reload. Nothing is claimed about a hull that does not exist.
    //
    // AND THE LATCH IS NOT TOUCHED, which is the whole of this branch. This line used to
    // clear `state.alarmLeakStage` as well, in the name of resetting the drill — but the
    // drill never raises it. The bench ladder (input.js sim_leak_test) writes
    // simLeakStage and nothing else, and latchLeakAlarm() is only ever called from
    // net.js, off a real alarm frame or a real hull's telemetry. So the only thing that
    // line could ever throw away was A REAL VEHICLE'S FLOOD.
    //
    // "Blocked" does not mean there is no sub; it means there is a sub we cannot reach,
    // and the moment those coincide is the moment this matters. The water shorts the
    // tether, the link drops, the drop is still red because the LATCH is holding it —
    // that is the one job the latch has — and it is still a button. One tap on it and,
    // three seconds later when telemetry goes stale and leakStage() falls back to the
    // model, the console paints the green struck-through drop captioned "both probes
    // dry" over a hull that was filling when it stopped answering. That is this file's
    // own rule broken by this file: the console may ask, and only the vehicle may answer.
    state.simLeakStage='NORMAL'; state.simLeak=false;
    LOG.cmd('leak_reset','(simulated — the bench ladder only; a vehicle\'s latch is not '
                       + 'the console\'s to retire)');
    return true;
  }
  state.leakResetPending=true;
  state.leakResetSaid='';                       // the last answer, cleared on a new ask
  return cmd('leak_reset');
}

/* The vehicle's answer to a leak_reset, good or bad. Called from net.js's ack
   handler because an ack is the ONLY place a refusal exists — there is no telemetry
   field for "you asked and I said no", and a button that silently does nothing
   teaches an operator that the button is broken. */
function noteLeakResetAck(ok, reason){
  state.leakResetPending=false;
  state.leakResetSaid = ok ? 'RE-ARMED' : (reason || 'refused by the vehicle');
  state.leakResetOk = !!ok;
  if(ok){
    // The console's own latch has to go too, or the worse-of-the-two rule in
    // leakStage() keeps showing the stage the vehicle has just cleared.
    state.alarmLeakStage='NORMAL';
    // LOG.ok, and not LOG.info: THERE IS NO `info` ON THIS BUS (core.js), so this line
    // threw a TypeError out of handleMessage on the one outcome nobody watches for —
    // the vehicle AGREEING. A refusal has always been recorded, because LOG.warn on the
    // branch below exists; the success was the only outcome that never reached the log
    // bus, the LOGS overlay or the session file on disk, which is precisely backwards.
    // The dive record has to contain the moment the flood watch was stood down at least
    // as clearly as it contains the moment somebody was told they could not stand it down.
    LOG.ok('leak detector re-armed by the vehicle');
  } else {
    LOG.warn('leak re-arm REFUSED: ' + state.leakResetSaid);
  }
}

function fireScreenFlash(){
  const el=$('screen-flash');
  el.classList.remove('fire'); void el.offsetWidth; el.classList.add('fire');
}

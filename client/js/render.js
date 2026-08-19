"use strict";
/* ============================================================================
   RENDER — local simulation (used only with no real telemetry), the normalized
   view builder, and all telemetry -> DOM rendering + status badges.
   ============================================================================ */

/* ---- SIMULATION: synthesize telemetry from live input so every gauge, lamp
   and readout animates with no server. Advances only in SIM mode. ---- */
function simulate(dt){
  const s=state, sim=CONFIG.sim;
  // THE MODEL DOES NOT INHERIT THE DEAD HULL'S NULLS. A vehicle whose depth sensor
  // stopped leaves state.depth null, and the simulator resumes from these very fields
  // (net.js mirrors them in for exactly that reason). Left alone, `null + dt` quietly
  // becomes a number again anyway — through 0, which is a claim that the sub is at the
  // surface. The model owns these while it is flying, so it takes them back explicitly,
  // the same way viewFromState() refuses to let the bench inherit the last dive's snag.
  if(s.depth==null)    s.depth=0;
  if(s.pressure==null) s.pressure=sim.basePressurePsi;
  if(s.heading==null)  s.heading=0;
  // AND THE PACK, which joined them this round and would have failed LOUDER than any
  // of the three. The drain line below is `Math.max(floorV, s.batteryV - drain*dt)`,
  // and `null - anything` is a negative number, so a hull whose INA219 died would hand
  // the bench a pack pinned at the 6.0 V floor — a red, pulsing, SURFACE-NOW battery
  // on a simulator with no battery in it. The model owns this while it is flying.
  if(s.batteryV==null)
    s.batteryV = (CONFIG.battery && CONFIG.battery.fullV) || 12.6;
  let b=s.input.ballast;
  if(Date.now()<s.surfaceUntil) b='empty'; // SURFACE command drains ballast
  if(b==='fill')  s.ballastLevel += sim.ballastRatePerS*dt;
  else if(b==='empty') s.ballastLevel -= sim.ballastRatePerS*dt;
  s.ballastLevel=clamp(s.ballastLevel,0,1);
  s.ballastTarget=s.ballastLevel;
  const depthTarget = s.ballastLevel*sim.maxDepthM;
  s.depth += (depthTarget - s.depth)*Math.min(1, dt*sim.depthLerp);
  s.pressure = sim.basePressurePsi + s.depth*sim.psiPerMeter;
  const c=s.input;
  s.left  = clamp(c.throttle + c.steer, -1, 1);
  s.right = clamp(c.throttle - c.steer, -1, 1);
  s.heading = (s.heading + c.steer*sim.headingRatePerS*dt + 360)%360;
  // Sags toward the documented 3S floor, not toward a number belonging to a
  // pack this sub has never had. CONFIG.battery is the single place those bands live.
  s.batteryV = Math.max((CONFIG.battery&&CONFIG.battery.floorV)||9.0,
                        s.batteryV - sim.batteryDrainVPerS*dt);
  // SPEED IN SIM IS AN ESTIMATE AND SAYS SO. There is no paddlewheel on the bench,
  // so this is the throttle curve — exactly the source the HUD styles as an estimate.
  // Labelling it 'lut' rather than inventing a measurement is the whole rule: the one
  // reading that would hide a snag is a model dressed up as a sensor.
  s.speedMs = c.throttle * ((CONFIG.map&&CONFIG.map.subMaxSpeedMs)||1.0);
  s.speedSrc = 'lut';
  // THE SECONDARY INSTRUMENTS, AND THE MODEL TAKING THEM BACK. Each entry in
  // FLIGHT_METRICS (core.js) either has a `sim` — a value the bench model can honestly
  // produce out of what it is already modelling — or it does not, and the ones that do
  // not are written to NULL rather than left alone.
  //
  // Writing the nulls is the whole point of this line, and it is the same failure the
  // four fields above were fixed for: `state` is where the LAST HULL's readings are, the
  // simulator resumes from it, and a bench inheriting a dead sub's 9 degrees of roll
  // would show a listing hull that is not there — for hours, with nothing on screen able
  // to explain it. The model owns these while it is flying: it has a heading, a depth
  // and a throttle, so it can say what it is turning at and what it is drawing; it has
  // no hull, so it has no pitch and no roll, and it says so with the same question mark
  // a dead sensor gets rather than with a comfortable 0.0 that means LEVEL.
  for(let i=0;i<FLIGHT_METRICS.length;i++){
    const m=FLIGHT_METRICS[i];
    s[m.key] = m.sim ? m.sim(s, dt) : null;
  }
}

/* Build a normalized "view" object the renderer consumes. */
function viewFromState(sim){
  const s=state;
  // Which of these readings came from a HULL? Everything a vehicle reports about
  // itself — the snag, the compass, the probe fault, whether the syringe has been
  // homed — is meaningless once the vehicle stops answering, and worse than
  // meaningless afterwards: without this gate the simulator inherits the last dive's
  // snag warning and its unhomed tank, and the bench looks broken for hours.
  const live = vehicleRecent();
  const stage = leakStage();
  // THE ONE PLACE A MEASURED READING IS ALLOWED TO BECOME CANNOT-TELL, so there is one
  // rule and not three.
  //
  // The value comes FIRST: null is the vehicle saying the chip behind this reading has
  // stopped answering, and no amount of freshness rescues it. That ordering is the
  // whole bug. The console used to ask `fresh(state.depthAt)` — a stamp written by
  // every arriving FRAME — so an MS5837 that died at 4.33 m and cached its last reading
  // forever produced 15 frames a second that all passed the gate, and the console
  // painted the frozen number in a full depth-band colour while the sub went to 8 m.
  //
  // The stamp is still asked, second, and only about a hull: it catches a vehicle that
  // drops the field instead of nulling it, which would otherwise leave the previous
  // value in `state` looking live. With no hull the values are the model's own and are
  // known by construction.
  //
  // AND THIRD, THE VEHICLE'S OWN VERDICT ON THE CHIP. The null and sensor_faults are
  // one decision read twice, and they can only ever disagree in one direction — a hull
  // naming a chip while still shipping a value measured by it. api/rov.py does exactly
  // that today on the pack (`battery_v=0.0 if volts is None`, "ina219" in the fault
  // list, same frame), and any driver serving a cached last-good reading has the same
  // shape. When they disagree the ADMISSION wins: the list can veto a number, never
  // invent one, so a vehicle too old to report faults is completely unaffected.
  // AND FOURTH, WHOSE FAULT THE SILENCE IS. A per-field stamp can never be fresher than
  // the frame that carried it, so when nothing is arriving every field ages out at the
  // same instant — and that is the LINK, not ten chips failing simultaneously. Read as a
  // sensor failure it painted nine of ten readouts with the cannot-tell "?" and raised
  // three crit SENSOR STOPPED chips accusing hardware that never faltered, on every
  // socket hiccup between staleTimeoutMs and simFallbackMs. "Dropped frame, will come
  // back" already has its own presentation ("--"), and it is a different instruction to
  // the operator than "go and look at that cable", so it has to win here.
  const frameFresh = sensorFresh(s.realTelAt);
  const sensed = (val, at, kind) => {
    if(val == null) return null;                      // the vehicle sent no reading
    if(!live) return val;                             // model values, known by construction
    if(faultedNow(kind, s.sensorFaults)) return null; // the hull named the chip: the admission wins
    if(!frameFresh) return val;                       // nothing is arriving at all — the link's fault
    return sensorFresh(at) ? val : null;              // frames ARE arriving and this field stopped
  };
  // THE SECONDARY INSTRUMENTS THROUGH THE SAME GATE, AND THAT IS THE POINT OF PUTTING
  // THEM THROUGH IT. They are advisory readings, which is exactly the argument that
  // would let them drift onto a weaker rule — and the weaker rule is what makes them
  // dangerous: a turn rate reading 0.0 deg/s beside a blanked bearing says the sub is
  // holding a straight course, drawn from a chip that is answering nothing. Every one of
  // these has a calm, plausible zero, so every one of them is worth more dead than the
  // primary readings are. `imu` is their kind, so a BNO085 named in sensor_faults vetoes
  // a cached number here the same way it vetoes a cached bearing.
  const flight = {};
  // Whether the hull SPEAKS each of these at all, carried onto the view beside the value.
  // Absent and null both arrive here as null, and only one of them is a fault worth an
  // errand: a vehicle too old to send the field has no instrument to have lost.
  let currentSeen = false;
  for(let i=0;i<FLIGHT_METRICS.length;i++){
    const m = FLIGHT_METRICS[i];
    if(m.key==='currentA') currentSeen = !!s[m.key+'Seen'];
    flight[m.key] = sensed(s[m.key], s[m.key+'At'], m.kind);
  }
  return {
    stale:false, sim:!!sim,
    // IS THERE A HULL BEHIND ANY OF THIS. Not the same question as `sim`: that one says
    // which source main.js chose, this one says whether a real vehicle spoke recently
    // enough for its silence to MEAN anything. A blank on a hull is a chip to go and
    // check; the same blank on the bench is the model admitting it has no such
    // instrument, and telling an operator to go and check a cable that is not there is
    // how a console teaches people to ignore it.
    hull: live,
    flight: flight,
    currentSeen: currentSeen,   // does this hull carry current_a at all? absent != null

    armed:s.armed, left:s.left, right:s.right,
    ballastLevel:s.ballastLevel, ballastTarget:s.ballastTarget,
    ballastKnown: live ? s.ballastKnown!==false : true,   // the model's tank is known by construction
    ballastRehome: live ? !!s.ballastRehome : false,
    depth:    sensed(s.depth,    s.depthAt,    'depth'),
    pressure: sensed(s.pressure, s.pressureAt, 'pressure'),
    heading:  sensed(s.heading,  s.headingAt,  'heading'),
    // Names the chip, never a substitute for the null above: a vehicle too old to send
    // faults still blanks the number, it just cannot say which cable to go and check.
    sensorFaults: live ? (s.sensorFaults||[]) : [],
    // AND WHICH OF THOSE NAMES ARE PARTS THIS HULL HAS NEVER HAD. Read off the same
    // frame as the list above (core.js absentSensors) so the two cannot drift apart by
    // a frame. It never blanks anything on its own — everything in it is already in
    // sensorFaults and already null — it only decides whether the blank is an ERRAND or
    // an instrument that is not fitted yet. Empty off the bench: the model's absences
    // are the model's own business and `hull` already says there is nobody out there.
    sensorsAbsent: live ? absentSensors() : [],
    // THE PACK GOES THROUGH THE SAME GATE AS DEPTH, and until this round it was the
    // one measured number that did not. A dead INA219 reached the bar as a confident
    // red 0.0 V with SURFACE beside it — the loudest thing on the console, invented
    // entirely by the absence of the sensor that was supposed to measure it.
    batteryV: sensed(s.batteryV, s.batteryAt, 'battery'),
    cpuC:s.cpuC, ramPct:s.ramPct, diskGb:s.diskGb,
    magnet:s.magnet,
    green:s.lights.green.on, white:s.lights.white.on,
    greenLevel:s.lights.green.level, whiteLevel:s.lights.white.level,
    leakStage:stage,
    // THE ONE-BIT QUESTION, AND IT IS "NOT CERTIFIED DRY" — NOT "WARN OR FLOOD". Written
    // against NORMAL deliberately, the same way api/rov.py writes it
    // (`leaking = leak_state != "NORMAL"`, with its own do-not-tidy-this note): UNKNOWN
    // means the probes are not being sampled, and false here would hand a caller that
    // only asks the bit the one claim the whole stage exists to withhold. Every stage
    // this line has never heard of lands on the alarming side by construction.
    leak: stage!=='NORMAL',
    leakProbeFault: live ? s.leakProbeFault : null,
    speedMs:s.speedMs, speedSrc:s.speedSrc,
    // THE SNAG, IN THE THREE STATES NAVIGATION ACTUALLY HAS. `!!s.snagged` collapsed
    // the null into the reassuring answer, which is how a subsystem's death came out
    // as good news. Split here rather than in the renderers so the alert rail and the
    // speed tile cannot end up disagreeing about which claim is on the wire:
    //   snagged      nav looked and the sub is PINNED — the alarm, and only this one
    //   snagUnknown  nav cannot tell — nothing is watching, which is not an alarm
    //   snagStood    ...and the last thing it did tell us was a snag, so the alarm
    //                standing when it went quiet is unconfirmed, not cleared
    snagged:     live ? s.snagged===true : false,
    snagUnknown: live ? (s.snagged==null && !!s.navAnswered) : false,
    snagStood:   live ? (s.snagged==null && !!s.snagStood)   : false,
    headingFlag: headingFlag(),             // '' | mag | gyro | gyro-mag | nomag | dead | nofilter
    // Free from the same INA219 as the voltage, so it dies with it. Taken straight off
    // the gated list above now that it has a readout of its own — it used to have a
    // hand-written gate here because it only ever rode in the pack tooltip, and a
    // tooltip is not somewhere an operator in sunlight with wet hands ever looks. One
    // value, one gate, so the tile and the tooltip cannot end up disagreeing about
    // whether anything is measuring the draw.
    currentA: flight.currentA,
    linkMs:s.linkMs
  };
}

/* ---- TELEMETRY -> UI ---- */
// Icon SVGs for the light lamps (filled = on, outline = off)
const BULB_FILL='<svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 21c0 .5.4 1 1 1h4c.6 0 1-.5 1-1v-1H9v1zm3-19a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.1 1.7h5.2l.1-1.7c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 2z"/></svg>';
const BULB_LINE='<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.2 1.2h5l.2-1.2c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 3z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M9.7 20h4.6M10.3 22h3.4"/></svg>';

function setText(el, txt, stale){
  if(stale){ el.textContent='--'; el.classList.add('is-stale'); }
  else { el.textContent=txt; el.classList.remove('is-stale'); }
}
function setThrust(el, v){
  const pct=Math.min(1,Math.abs(v))*50;
  el.style.width=pct+'%';
  if(v>=0){ el.style.left='50%'; el.style.right='auto'; }
  else    { el.style.right='50%'; el.style.left='auto'; }
}
function renderLightButton(which, on, level){
  const isGreen = which==='green';
  const btn=$('btn-light-'+which), icon=$('icon-light-'+which), gauge=$('gauge-'+which);
  const color = isGreen ? 'var(--tertiary)' : '#e9f9ff';
  const glow  = isGreen ? '#4dffa6' : 'rgba(190,240,255,.85)';
  if(on){
    btn.style.backgroundColor = 'color-mix(in srgb,'+color+' 30%,transparent)';
    btn.style.borderColor = color;
    btn.style.boxShadow = '0 0 12px '+glow;
    icon.style.color = color; icon.innerHTML=BULB_FILL;
    gauge.style.backgroundColor = color; gauge.style.boxShadow='0 0 8px '+glow;
  } else {
    btn.style.backgroundColor = 'var(--surface-variant)';
    btn.style.borderColor = 'var(--outline-variant)';
    btn.style.boxShadow = 'none';
    icon.style.color = 'var(--on-surface-variant)'; icon.innerHTML=BULB_LINE;
    gauge.style.backgroundColor = 'color-mix(in srgb,var(--on-surface-variant) 40%,transparent)';
    gauge.style.boxShadow='none';
  }
  gauge.style.height = Math.round(level*100)+'%';   // vertical fill (bottom→top)
}
// Armed/magnet no longer have HUD elements (kept as no-ops so callers stay simple;
// arm/disarm + magnet still work as gamepad/key actions and gate the thrusters).
function renderArmed(armed){ /* no armed indicator by request */ }
function renderMagnet(on){ /* no magnet indicator by request */ }

/* LEAK: icon-only, and FOUR SHAPES for four states — the drop changes what it IS,
   not just what colour it is, because a colour-blind operator in sunlight reads the
   outline first and everything else second.

     NORMAL  green SOLID drop struck through  — both probes dry
     WARN    amber HOLLOW drop, filled to a waterline across its middle — the lower
             probe is wet and water is collecting. Deliberately a different drawing
             from both neighbours: half a drop is the shape of "some water".
     FLOOD   red SOLID drop, glowing, plus the full-screen edge pulse — the upper
             probe (2 cm higher) is wet too.
     UNKNOWN amber DASHED-outline drop with a question mark in it — nobody is
             sampling the probes, so the hull's state is not known in either
             direction (api/hardware.py LEAK_UNKNOWN).

   THE FOURTH SHAPE IS THE POINT OF THIS ROUND. UNKNOWN used to be folded into NORMAL,
   so a hull whose probes had stopped being read painted the green struck-through drop
   captioned "both probes dry" — a positive claim about hull integrity, made off
   evidence nobody was collecting. It is deliberately NOT the green drop and
   deliberately not the WARN drop either: it is neither a clean hull nor a wet one, and
   the dashed outline plus the question mark is this console's existing word for
   genuinely-not-known (the unhomed syringe and every dead sensor say '?' too).

   Only FLOOD pulses the screen and only FLOOD turns the tether icon into the red
   pulsing sub (status.js), which is what keeps a flood impossible to confuse with a
   link dropout — and equally keeps a WARN, or a cannot-tell, from being mistaken for
   one. */
const DROP_OK   = '<svg viewBox="0 0 24 24"><path fill="var(--tertiary)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/><path stroke="var(--tertiary)" stroke-width="2.4" stroke-linecap="round" d="M4 4l16 16"/><path stroke="#0c0118" stroke-width="1.1" stroke-linecap="round" d="M4 4l16 16"/></svg>';
const DROP_WARN = '<svg viewBox="0 0 24 24">'
  + '<path fill="none" stroke="var(--hazard)" stroke-width="2" d="M12 3.2s5.6 6.4 5.6 9.8a5.6 5.6 0 0 1-11.2 0c0-3.4 5.6-9.8 5.6-9.8z"/>'
  + '<path fill="var(--hazard)" d="M6.7 14.2a5.6 5.6 0 0 0 10.6 0z"/>'
  + '<path stroke="var(--hazard)" stroke-width="1.6" stroke-linecap="round" d="M6.6 14.2h10.8"/></svg>';
const DROP_FLOOD= '<svg viewBox="0 0 24 24"><path fill="var(--error)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/></svg>';
/* The cannot-tell drop: broken outline (nothing is closing the loop on this hull) with
   the question mark the rest of the console already uses for "genuinely not known".
   No strike-through — the strike is the NORMAL drop's way of saying "no water", and
   that is the exact claim this shape exists to withhold. */
const DROP_NOSAMPLE = '<svg viewBox="0 0 24 24">'
  + '<path fill="none" stroke="var(--hazard)" stroke-width="2" stroke-linecap="round" '
  + 'stroke-dasharray="3.2 2.8" d="M12 3.2s5.6 6.4 5.6 9.8a5.6 5.6 0 0 1-11.2 0c0-3.4 5.6-9.8 5.6-9.8z"/>'
  + '<path fill="none" stroke="var(--hazard)" stroke-width="1.9" stroke-linecap="round" '
  + 'd="M10.2 11.1a1.85 1.85 0 1 1 1.85 1.85v1.35"/>'
  + '<circle cx="12.05" cy="17.1" r="1.05" fill="var(--hazard)"/></svg>';
function renderLeak(stage){
  // Tolerates the old boolean call (main.js boot passes `false`): true was always
  // "water in the hull", which is the flood end of the ladder.
  const st = (stage===true) ? 'FLOOD' : (stage===false || !stage) ? 'NORMAL' : stage;
  const icon=$('leak-icon'), pulse=$('leak-pulse');
  if(icon){
    icon.innerHTML = st==='FLOOD' ? DROP_FLOOD : st==='WARN' ? DROP_WARN
                   : st==='UNKNOWN' ? DROP_NOSAMPLE : DROP_OK;
    icon.style.filter = st==='FLOOD' ? 'drop-shadow(0 0 6px var(--error))'
                      : st==='WARN'  ? 'drop-shadow(0 0 5px var(--hazard))'
                      : st==='UNKNOWN' ? 'drop-shadow(0 0 4px var(--hazard))' : '';
    icon.className = 'leak-'+st.toLowerCase();      // hook for the suites and for CSS
    liveTitle(icon, st==='FLOOD' ? 'FLOODING - water above the upper probe. SURFACE NOW.'
                  : st==='WARN'  ? 'water is collecting on the hull floor - finish up and come home'
                  // Says what IS the case and never spells the reassuring sentence, not
                  // even to deny it: a phrase read at a glance is read without its NOT,
                  // and this is the one phrase on the console that must not be misread.
                  : st==='UNKNOWN' ? 'NOBODY IS SAMPLING THE LEAK PROBES - the vehicle has stopped '
                                   + 'reading them, so nothing on this sub is checking whether '
                                   + 'water is getting in. The green drop would mean both probes '
                                   + 'were read and neither was wet; neither was read'
                  : 'both probes dry');
  }
  // The edge pulse is the FLOOD siren and nothing else. Firing it on a WARN would
  // make the advisory unignorable, and an unignorable advisory gets ignored.
  if(pulse) pulse.classList.toggle('on', st==='FLOOD');
  renderLeakRearm(icon, st);
}

/* THE DROP BECOMES A BUTTON ONLY WHEN THERE IS SOMETHING TO CLEAR.

   On NORMAL it is not focusable, not clickable and carries no hint: an affordance
   that is always there is one an operator can hit by accident on a healthy hull,
   and this is the control that clears the console's loudest claim.

   It is safe to offer on FLOOD as well as WARN, and deliberately is, because the
   VEHICLE decides — it refuses while a probe is wet and says why. Hiding the
   button during a flood would mean an operator who has pumped out, dried the
   bilge and genuinely fixed it still cannot re-arm without a restart, which is
   the situation this whole change exists to end. Pressing it on a live flood
   costs one refusal and one sentence explaining that water is still present. */
function renderLeakRearm(icon, st){
  if(!icon) return;
  const armable = (st!=='NORMAL');
  // A DATA ATTRIBUTE AND NOT A CLASS, on purpose. renderLeak sets
  // icon.className = 'leak-<stage>' outright, and the sensor-loss suite asserts on
  // that exact string to prove a standing FLOOD is never talked down to
  // cannot-tell. Adding a second class here made that assertion read
  // "leak-flood can-rearm" and fail — a real check, failing for a cosmetic reason.
  // The affordance is orthogonal to the stage, so it is stored orthogonally and the
  // className contract stays exactly what that suite is guarding.
  if(armable) icon.dataset.rearm = '1'; else delete icon.dataset.rearm;
  if(armable){
    icon.setAttribute('role','button');
    icon.setAttribute('tabindex','0');
  } else {
    icon.removeAttribute('role');
    icon.removeAttribute('tabindex');
  }
  if(!icon._rearmBound){
    icon._rearmBound = true;
    const go = (e)=>{
      if(!icon.dataset.rearm) return;
      e.preventDefault(); e.stopPropagation();
      if(typeof resetLeak==='function') resetLeak();
    };
    icon.addEventListener('click', go);
    icon.addEventListener('keydown', (e)=>{ if(e.key==='Enter'||e.key===' ') go(e); });
  }
}

/* One compass point of the input dial. `v` is that direction's share of its axis:
   negative means the stick is going the other way, which reads 0 here — the opposite
   number is the one lighting up. */
const _numPrev={};
function setInputNum(id, v){
  const el=$(id); if(!el) return;
  const n=Math.round(Math.max(0, Math.min(1, v||0))*100);
  if(_numPrev[id]!==n){ _numPrev[id]=n; el.textContent=n; el.classList.toggle('live', n>0); }
}

/* ---- THE COLOUR LINK -------------------------------------------------------
   The map draws the dive track in twelve depth bands. The ballast fill and the
   Depth / Pressure / Ballast numbers now wear the same bands, so a glance at the
   rail and a glance at the track say the same thing in the same colour.

   WHAT MAY BE COLOURED IS NOT THE SAME IN BOTH MODES, and that is the point:

     SIM   one made-up number drives everything, so everything wears one colour,
           taken straight from the ballast input. Drag the slider and the whole
           console moves together - which is exactly what a simulation is.

     REAL  ballast is a commanded quantity and colours itself. Depth and pressure
           are MEASURED, and are coloured by their own sensor or not at all. They
           are never tinted from ballast: a sub descending on a full tank with a
           dead depth sensor would then show a deepening colour it did not earn,
           and the one symptom that gives the failure away would be the symptom we
           had painted over. Cyan-and-unchanging while the ballast fill turns
           purple IS the alarm.

   A reading older than staleTimeoutMs is dropped rather than believed, for the
   same reason the camera drops a stale AP sighting. */
function metricTints(v){
  // The ramp lives in map.js. If that file ever fails to load, the HUD keeps its
  // default colours instead of throwing sixty times a second and taking piloting
  // down with the map - subsystems fail alone (§3).
  if(v.stale || typeof rampColor!=='function') return {ballast:null, depth:null, pressure:null};
  // Ballast is coloured by the depth that much water BUYS, not by the fraction itself.
  // Straight 0..1 would look right and be wrong: the tank reaches 9 m while the ramp
  // saturates at 6, so a half-full tank would show band 6 while the track it is about
  // to draw shows band 9. Converting first is what makes them the same colour.
  const bal = _depthColor((v.ballastLevel||0) * ((CONFIG.sim&&CONFIG.sim.maxDepthM)||9));
  if(v.sim || state.mode==='sim') return {ballast:bal, depth:bal, pressure:bal};
  // COLOURED BY THE READING ITSELF, and the reading has already been through the
  // cannot-tell gate in viewFromState — null there means no sensor is behind it.
  //
  // This line used to read `fresh(state.depthAt)`, a stamp net.js wrote on every
  // arriving FRAME. That made the tint a statement about the link dressed as a
  // statement about the sensor: the MS5837 that died at 4.33 m kept the stamp fresh at
  // 15 Hz, so the frozen number wore a full, confident depth-band colour all the way
  // down to 8 m. The tint is the loudest thing on the tile, and it was the part that
  // was lying hardest.
  return {
    ballast:  bal,
    depth:    v.depth!=null    ? _depthColor(v.depth)      : null,
    pressure: v.pressure!=null ? pressureColor(v.pressure) : null
  };
}
/* null puts the number back to its default look rather than picking a "neutral"
   colour, so an untinted readout is visibly the ABSENCE of a reading, not a
   twelfth-band value that happens to be grey.

   `why` is the live half of the tooltip and the CALLER decides it, rather than this
   function inferring it from the colour. It used to say "colour means fine, no colour
   means explain yourself", which had no room for the case that matters most: a dead
   sensor is BOTH coloured (amber, so the eye lands on it) and owed an explanation.
   The memo is keyed on the sentence as well as the colour for the same reason — the
   cause arrives a frame or two after the null, and a memo on colour alone would have
   swallowed it.

   `glow` is opt-OUT, and there is one caller that opts out: the ABSENT readout. Every
   glowing number on this console is a claim — a measured band, or the amber of a chip
   that has stopped — and an instrument this hull has never had is making no claim at
   all. It takes the grey and leaves the halo, so "no claim" is legible as no claim
   without a second colour being invented for it. */
const _tint={};
function paintMetric(id, color, why, glow){
  const el=$(id); if(!el) return;
  const lit = !!color && glow!==false;
  const sig = String(color) + '|' + String(why) + '|' + (lit?'1':'0');
  if(_tint[id]===sig) return;
  _tint[id]=sig;
  el.style.color = color || '';
  el.style.textShadow = lit ? ('0 0 8px '+color) : '';
  if(why!==undefined) liveTitle(el, why || '');
}

/* ---- A MEASURED NUMBER, AND THE THREE DIFFERENT WAYS IT CAN BE MISSING ----
   Four shapes, because they are four facts and an operator acts on each differently:

     42.7        the sensor is reporting. Tinted by its own band.
     '--', dim   STALE — the link went quiet for a moment. The whole bar dashes
                 together and it comes back on its own; correctly ignored.
     '?', amber  CANNOT-TELL — the chip behind this reading has stopped answering.
                 Nothing on the vehicle is measuring it, and waiting will not help.
     '—', grey   ABSENT — this hull has never had the instrument. Nothing stopped,
                 nothing to go and look at, and no chip on the rail.

   The question mark is this console's existing word for "genuinely not known" (the
   unhomed syringe has always said it), and it is deliberately NOT the stale dash. That
   is the whole point of the shape: a dash reads as a dropped frame, and a dead depth
   sensor dressed as a dropped frame is a sub flown on a number nobody is taking. Three
   carriers — the mark, the amber, and the alert chip naming the chip — so none of them
   has to be the one that gets noticed.

   AND THE FOURTH SHAPE IS WHY THIS ROUND EXISTS. The owner is fitting instruments to a
   real vehicle one at a time, so "this boat does not have one yet" is the normal state
   of most readings for weeks — and every one of them was being drawn as a part that had
   just broken, with a crit chip and an errand attached. An errand nobody can run is how
   a console teaches its operator to stop reading the rail, and the rail is where the
   leak goes. So ABSENT is the quiet state: one unbroken rule instead of a number, the
   grey that means "no claim" (docs/playbook.md §3), no amber, no wavy underline, no
   glow and no chip. It is NOT the stale dash either — that is a PAIR of short dashes
   that arrives on the whole bar at once and leaves by itself, where this is one long
   rule on one reading while everything around it goes on reading normally, and it never
   leaves until somebody fits the part.

   `deadWhy` overrides the CANNOT-TELL sentence for callers whose blank has a different
   cause. There is exactly one such cause and it is the bench: with no vehicle on the
   link, a missing reading is not a chip that stopped, it is an instrument the console's
   own model never had. Same mark, same amber, same '?' — genuinely-not-known is
   genuinely-not-known — but the words have to be true, and "the sensor behind this has
   stopped answering" is not true of a hull that is not there. (The bench is not ABSENT:
   its readings would exist if a vehicle were on the link, and `hull` already says
   plainly that there is nobody out there.) */
function renderSensed(id, val, text, stale, tint, what, kind, v, deadWhy){
  const el=$(id); if(!el) return;
  const missing = !stale && val==null;
  // ABSENT is asked FIRST, and only of a reading that is already missing: absence is a
  // fact about the part, and the null is still the whole evidence that there is no
  // number. A vehicle that named an absent part while shipping a value measured by it
  // would be answering a question nobody asked — the value wins, exactly as it does
  // against sensor_faults.
  const gone = missing && !deadWhy && sensorAbsent(kind, v.sensorFaults, v.sensorsAbsent);
  const dead = missing && !gone;
  setText(el, dead ? '?' : gone ? '—' : text, stale);
  el.classList.toggle('nosensor', dead);
  // A hook of its own, so nothing can style ABSENT by accident through the cannot-tell
  // rule and a check can ask the DOM which of the two it is looking at.
  el.classList.toggle('notfitted', gone);
  // The sentence is built only when there is something to admit — this runs on every
  // frame, and paintMetric's memo means the healthy case must not do work to say
  // nothing.
  paintMetric(id, dead ? 'var(--hazard)' : gone ? 'var(--outline)' : tint,
              dead ? (deadWhy || noSensorWhy(what, kind, v))
                   : gone ? notFittedWhy(what, kind, v) : '',
              !gone);
}
/* THE BENCH ADMITTING WHAT IT IS NOT MODELLING. The simulator has a heading, a depth, a
   throttle and a tank; it has no hull, so it has no attitude, and the honest thing to
   draw is the question mark rather than a level 0.0 that would be a claim. It must not
   borrow the dead-sensor sentence: that one ends by sending the operator to look at a
   cable, and on a console with no vehicle attached there is no cable — an errand that
   cannot be run is how a screen teaches people to stop reading it. */
function noModelWhy(what){
  return 'NO ' + what + ' - and nothing has stopped. There is no vehicle on this link, '
       + 'and the console’s own model does not have this instrument: it flies a heading, '
       + 'a depth and a tank, and it has no hull to tilt. The question mark is the model '
       + 'saying it cannot tell you, rather than showing you a level, still, comfortable '
       + 'number it made up.';
}
/* The sentence, with the chip named when the vehicle said which one. Kept in one place
   so the depth tile and the pressure tile cannot drift into telling different stories
   about the same dead MS5837. */
function noSensorWhy(what, kind, v){
  const cause = faultCause(kind, v.sensorFaults);
  return 'NO ' + what + ' - this readout is NOT tracking a sensor'
       + (cause ? ('; the vehicle reports that ' + cause + ' has stopped answering') : '')
       + '. The last number is not being shown, because a reading that has frozen looks '
       + 'exactly like one that is holding steady.';
}
/* THE INSTRUMENT THAT IS NOT IN THE BOAT — the sentence that must NOT send anybody
   anywhere. It names the part, because naming it is what turns the grey rule into
   information ("no compass fitted" rather than "something is missing"), and then says
   the one thing the operator needs: nothing has failed, so there is nothing to check
   and nothing to wait for. This is the tooltip doing the job the alert rail deliberately
   is not doing — quiet and informative where the reading is, rather than loud where the
   emergencies are (docs/playbook.md §2, the same rule the chart layers follow). */
/* Deliberately NOT spelled "NO <READING>". That phrasing belongs to the cannot-tell
   sentence, and on the bearing it would put the words NO BEARING in front of an operator
   whose badge reads NO COMPASS — and those two are a registered pair of OPPOSITE claims
   (docs/playbook.md §2: one is a compass that stopped, the other a compass that was never
   there). Two vocabularies saying different things about one readout is how a screen
   stops being read. It leads with the state's own word instead. */
function notFittedWhy(what, kind, v){
  const parts = faultChips(kind, v.sensorFaults).map(c=>chipMeans(c).long).join(' and ');
  return 'NOT FITTED - nothing on this vehicle measures ' + String(what).toLowerCase()
       + (parts ? (', and it reports ' + parts + ' as never fitted') : '')
       + '. Nothing has stopped and nothing here is broken - there is no cable to go and '
       + 'check and no point waiting, because this reading has never existed on this '
       + 'hull. Fit the part and it fills in by itself.';
}

/* ---- THE PACK -------------------------------------------------------------
   Colour comes ONLY from CONFIG.battery's bands (one colour, one meaning), and the
   number is always shown beside it — a coloured bar with no figure tells you a
   threshold was crossed but never how far past it you are. The band also carries the
   sentence explaining what to do, because "amber" is not an instruction.
   Current draw rides along in the tooltip rather than taking a tile of its own: it
   is free from the same INA219 chip and it is what turns "the pack is sagging" into
   "the pack is sagging BECAUSE both thrusters are at full".

   AND THE PACK CAN NOW SAY IT DOES NOT KNOW. batteryBand(null) has always returned no
   colour at all rather than a healthy green, but nothing ever handed it a null: the
   vehicle sent 0.0 V for a dead INA219 and 0.0 V bands as CRITICAL, so an absent sensor
   produced the single most alarming reading on the console — red, pulsing, SURFACE —
   about a pack it was not measuring. The cannot-tell shape is the SAME one depth and
   the bearing already use (question mark, amber, wavy underline) because it is the same
   fact, and it is deliberately not the stale dash: dashes are a dropped frame and come
   back on their own, and an operator who waits this one out is waiting on a chip that
   is never going to answer. */
let _battSig='';
/* AND A PACK MONITOR THAT WAS NEVER FITTED IS NOT A DEAD ONE. Same three-way split as
   renderSensed, spelled out again here because this tile owns its own colour (the bands)
   and cannot use the shared path. Absent takes the grey, no wavy underline and no glow —
   an instrument that is not in the boat is making no claim, and a claim is what every
   colour on this readout means. */
function renderBattery(v){
  const el=$('battery-v'); if(!el) return;
  const missing = !v.stale && v.batteryV==null;
  const absent = missing && sensorAbsent('battery', v.sensorFaults, v.sensorsAbsent);
  const dead = missing && !absent;
  const band = batteryBand(v.stale ? null : v.batteryV);
  const amps = (v.currentA!=null) ? ('   —   drawing '+v.currentA.toFixed(1)+' A') : '';
  const why  = dead ? noSensorWhy('PACK VOLTAGE', 'battery', v)
             : absent ? notFittedWhy('PACK VOLTAGE', 'battery', v)
             : (band.text + amps);
  // Signature includes staleness because setText() owns the is-stale class on this
  // element and className is assigned wholesale here: without it, one repaint on a
  // band change would quietly un-grey a dashed-out readout. It includes `why` for the
  // same reason paintMetric's does — the fault list naming the chip arrives a frame or
  // two after the null, and a signature on the band alone would swallow the sentence.
  const sig = band.key + '|' + (v.stale?'s':'') + '|' + (dead?'d':absent?'a':'') + '|' + why;
  if(_battSig===sig) return;
  _battSig=sig;
  const color = dead ? 'var(--hazard)' : absent ? 'var(--outline)' : band.color;
  el.style.color = color || '';
  el.style.textShadow = (color && !absent) ? ('0 0 8px '+color) : '';
  el.className = 'm-val batt-'+band.key + (v.stale?' is-stale':'')
               + (dead?' nosensor':'') + (absent?' notfitted':'');
  liveTitle(el, why);
}

/* ---- THE SECONDARY INSTRUMENTS -------------------------------------------
   Five readings the vehicle has always sent and nothing ever drew: the pack's amps
   (spent inside the pack TOOLTIP, which is not showing it to anybody flying a sub in
   sunlight) and the four inertial channels off the compass module. The markup, the
   ingest, the bench model and the wording all come out of ONE table (core.js
   FLIGHT_METRICS), because the operator has said they will fly with all five and then
   cut back, and a metric spread across three files is a metric nobody dares delete.

   NOTHING NEW IS INVENTED HERE. The tiles are the same .metric / .m-label / .m-val the
   whole console is made of, the cannot-tell is renderSensed's existing question mark,
   and the group head's mark is the same .m-flag the bearing wears. Only the grouping is
   new, and it is new because five more numbers in the top bar is the wall-of-digits this
   brief exists to avoid.

   NOT TINTED. The depth ramp means DEPTH everywhere on this console — the track, the
   syringe, the depth and pressure numbers — and a turn rate wearing band 7 would be
   borrowing a colour it did not earn from a quantity it is not. These stay the default
   readout colour and go amber only to say they are not being measured. */
let _clusterBuilt=false;
function flightTile(m){
  const tile=document.createElement('div');
  tile.className='metric';
  tile.dataset.metric=m.key;                       // the hook for pruning, and for a test
  const lab=document.createElement('span');
  lab.className='m-label'; lab.textContent=m.label;
  const val=document.createElement('span');
  val.className='m-val'; val.id=m.id; val.textContent='--';
  // data-help is what captureHelp() would have written had this lived in index.html, so
  // liveTitle() keeps appending the live sentence to the explanation instead of erasing
  // it — and every check that asks an element what it MEANS gets the same answer here as
  // it does from a hand-written tile.
  val.dataset.help=m.help; val.title=m.help; val.setAttribute('aria-label', m.help);
  tile.appendChild(lab); tile.appendChild(val);
  return tile;
}
function buildFlightCluster(){
  if(_clusterBuilt) return;
  _clusterBuilt=true;                              // one attempt: the hosts ship in the HTML
  HUD_GROUPS.forEach(g=>{
    const mine=FLIGHT_METRICS.filter(m=>m.group===g.id);
    if(!mine.length) return;                       // every metric pruned: the group goes too
    // DIRECT SIBLINGS, not a wrapper. #topbar is a flex row whose even gaps come from
    // space-between dividing the leftover width between its CHILDREN, so a container
    // holding two readings would be one child holding two — and the bar's spacing, which
    // has its own test, would quietly stop being even.
    if(g.after){
      const anchor=$(g.after);
      if(!anchor){ LOG.warn('metric group "'+g.id+'" has no anchor #'+g.after); return; }
      let at=anchor;
      mine.forEach(m=>{ const el=flightTile(m); at.parentNode.insertBefore(el, at.nextSibling); at=el; });
      return;
    }
    const host=$(g.into);
    if(!host){ LOG.warn('metric group "'+g.id+'" has no host #'+g.into); return; }
    const wrap=document.createElement('div');
    wrap.className='fgroup'; wrap.id='fg-'+g.id; wrap.dataset.group=g.id;
    const head=document.createElement('button');
    head.type='button'; head.className='fg-head'; head.id='fg-'+g.id+'-head';
    head.innerHTML='<span class="fg-chev" aria-hidden="true">▾</span>'
                 + '<span class="fg-name"></span>'
                 + '<span class="m-flag fg-flag" id="fg-'+g.id+'-flag"></span>';
    head.querySelector('.fg-name').textContent=g.label;
    head.dataset.help=g.title; head.title=g.title; head.setAttribute('aria-label', g.title);
    // The mark carries its own explanation, because it is a readout like any other and
    // an operator who sees it needs to know what it is claiming without hovering the
    // heading behind it.
    const flag=head.querySelector('.fg-flag');
    const flagHelp='NOT BEING MEASURED - at least one of the readings under this heading '
      + 'has no sensor behind it: the vehicle is sending null for it, so there is no '
      + 'number to show and none is being invented. It is shown on the heading as well as '
      + 'on the reading itself so that folding the group away can never hide the fact that '
      + 'something under it stopped. Open the group and the reading with the question mark '
      + 'is the one; its own tooltip names the chip if the vehicle said which.';
    flag.dataset.help=flagHelp; flag.title=flagHelp; flag.setAttribute('aria-label', flagHelp);
    const tiles=document.createElement('div');
    tiles.className='fg-tiles'; tiles.id='fg-'+g.id+'-tiles';
    mine.forEach(m=>tiles.appendChild(flightTile(m)));
    wrap.appendChild(head); wrap.appendChild(tiles);
    host.appendChild(wrap);
    // FOLDING. Advisory instruments, so the operator is allowed to put them away — and
    // the choice survives a reload, because a group that reopens itself on every launch
    // is a group the operator folds every launch and then stops trusting to stay folded.
    const KEY='neptune_group_'+g.id;
    let closed=false;
    try{ closed = localStorage.getItem(KEY)==='1'; }catch(e){}
    const apply=()=>{ wrap.classList.toggle('collapsed', closed);
                      head.setAttribute('aria-expanded', String(!closed)); };
    apply();
    head.addEventListener('click', ()=>{
      closed=!closed; apply();
      try{ localStorage.setItem(KEY, closed?'1':'0'); }catch(e){}
      // Hand the keyboard back. This handheld is flown on WASD and the paddles, and a
      // button that keeps focus after a tap swallows the next Space as "press me again"
      // instead of passing it to the sub.
      head.blur();
    });
  });
}
/* One frame of the cluster. Every reading goes through the SAME renderSensed the depth
   and the bearing use, so there is one rule about what a missing number looks like and
   not two — the only thing this adds is the sentence for the bench, where a blank is the
   model admitting it has no such instrument rather than a chip to go and check. */
const _flightFlagSig={};
function renderFlight(v){
  const stale=!!v.stale;
  // A view built by anything other than viewFromState has no readings on it, and this
  // subsystem is allowed to fail alone (§3): it must not take the depth gauge down with
  // it. An absent set reads as five cannot-tells, which is the true statement about a
  // view that carries none of them.
  const flight = v.flight || {};
  for(let i=0;i<FLIGHT_METRICS.length;i++){
    const m=FLIGHT_METRICS[i];
    const val=flight[m.key];
    renderSensed(m.id, val, (val!=null ? m.fmt(val) : '--'), stale, null,
                 m.what, m.kind, v, v.hull ? null : noModelWhy(m.what));
  }
  HUD_GROUPS.forEach(g=>{
    if(!g.label) return;                                   // headless group: nothing to mark
    const el=$('fg-'+g.id+'-flag'); if(!el) return;
    // ONLY A HULL CAN RAISE IT. On the bench, pitch and roll are permanently unmeasured
    // by construction, and a mark that is lit from power-on is a mark nobody reads — the
    // same reasoning that keeps the snag-watch chip silent on a vehicle whose estimator
    // never ran. And never while STALE: a link that went quiet for a second has not
    // killed anything, and accusing a chip over a late frame is how an operator learns to
    // wait out warnings that mean it.
    const gone = (v.hull && !stale)
      ? FLIGHT_METRICS.filter(m=>m.group===g.id && flight[m.key]==null) : [];
    const chips = gone.length ? faultChips(gone[0].kind, v.sensorFaults) : [];
    // AND WHETHER ANY OF IT EVER EXISTED. A group whose every blank reading is behind a
    // part this hull has never had must not wear "COMPASS STOPPED" in amber for the whole
    // of every dive until the part arrives — a mark that is lit from power-on is a mark
    // nobody reads, which is the same argument that keeps this flag off the bench. It
    // still SAYS something, because a folded group must not hide four blanks; it says the
    // true thing, without the amber and without the errand.
    const absent = gone.length && gone.every(m=>sensorAbsent(m.kind, v.sensorFaults, v.sensorsAbsent));
    // Names the JOB, not the part number, and says the bus when it is the bus — one dead
    // sensor is one connector and a dead bus is every connector plus its power.
    const label = !gone.length ? ''
                : absent                  ? 'NOT FITTED'
                : chips.indexOf('i2c')>=0 ? 'I2C BUS DOWN'
                : chips.length            ? chipMeans(chips[0]).short + ' STOPPED'
                : 'NOT MEASURED';
    // Signed on the COUNT as well as the label, for the reason renderBattery's memo
    // carries `why`: a second reading going quiet under an unchanged label ("NOT
    // MEASURED" whether one of them is gone or three) would leave the sentence behind
    // saying "1 of 4" while four question marks sat under it.
    const sig = label + '|' + gone.length;
    if(_flightFlagSig[g.id]===sig) return;
    _flightFlagSig[g.id]=sig;
    el.textContent=label;
    // Amber says "look closer"; there is nothing to look closer at on a part that is not
    // in the boat, so the absent label takes the badge's plain form.
    el.className='m-flag fg-flag' + (label ? (absent ? ' on' : ' on suspect') : '');
    liveTitle(el, !gone.length ? ''
      : absent
      ? (gone.length + ' of ' + FLIGHT_METRICS.filter(m=>m.group===g.id).length
         + ' readings here are not fitted on this vehicle'
         + (chips.length ? (' - it reports ' + chips.map(c=>chipMeans(c).long).join(' and ')
                            + ' as never fitted') : '')
         + '. Nothing has stopped and there is nothing to check.')
      : (gone.length + ' of ' + FLIGHT_METRICS.filter(m=>m.group===g.id).length
         + ' readings here have no sensor behind them right now'
         + (chips.length ? (' - the vehicle names ' + chips.map(c=>chipMeans(c).long).join(' and ')) : '')
         + '. They are shown as question marks rather than as the last numbers they gave.'));
  });
}

/* ---- WATER SPEED ----------------------------------------------------------
   AN ESTIMATE NEVER DRESSES AS A MEASUREMENT. A paddlewheel-backed reading is the
   plain number: something physically counted water going past. Anything from the
   throttle lookup gets the tilde AND the EST tag — the same qualifier-tag idiom the
   tether readout already uses to say PLANNED / LAST KNOWN when its range stops
   being measured. That distinction is the entire reason the paddlewheel was bought:
   a snagged sub's LUT speed looks exactly like a healthy cruise.

   AND NO SPEED AT ALL IS NOW A QUESTION MARK, NOT A DASH. This readout has spelled
   cannot-tell '--' since it was written, which is this console's word for a dropped
   frame — the one kind of missing number an operator is RIGHT to wait out, because it
   comes back on its own. Nothing here comes back on its own: `speed_ms` and `speed_src`
   both null means neither the paddlewheel nor the throttle curve produced anything, and
   'no-origin' means navigation has no launch point to measure against. Both need
   somebody to DO something, and both were arriving dressed as "be patient". Same '?',
   same amber, same wavy underline as every other cannot-tell on the bar, with the tag
   beside it saying which of the two it is: NO SPEED, or NO DATUM with the remedy in its
   tooltip. */
let _speedSig='';
function renderSpeed(v){
  const el=$('speed-val'), tag=$('speed-src');
  if(!el) return;
  const measured = speedIsMeasured(v.speedSrc);
  const none = (v.speedMs==null || v.speedSrc==null);
  // 'no-origin' is nav saying it has no DATUM yet, not the wheel saying it has no
  // pulses. They read identically on a blank readout and blaming the paddlewheel for
  // the whole pre-dive phase of every boot taught the operator to distrust a sensor
  // that was working perfectly.
  const kind = v.stale ? 'stale'
             : (v.speedSrc==='no-origin') ? 'no-origin'
             : none ? 'none' : measured ? 'measured' : 'est';
  // The NUMBER changes shape too, not only its tag: a measured reading is given to
  // the centimetre because the wheel resolves that; an estimate gets a tilde and one
  // decimal, because pretending to a second one would be dressing up a guess.
  el.textContent = kind==='stale' ? '--'
                 : (kind==='none' || kind==='no-origin') ? '?'
                 : (measured ? '' : '~') + v.speedMs.toFixed(measured ? 2 : 1) + ' m/s';
  const sig = kind + (v.snagged?'!':'');
  if(_speedSig===sig) return;
  _speedSig=sig;
  // 'no-origin' used to carry its own class here and nothing was ever styled from it, so
  // the only carrier of "there is no speed" was the tag. It wears the shared cannot-tell
  // paint now; the TAG is what still separates a silent paddlewheel from a missing datum.
  el.className = 'm-val ' + (kind==='stale' ? 'is-stale'
                           : (kind==='none' || kind==='no-origin') ? 'nosensor' : kind)
               + (v.snagged ? ' snag' : '');
  if(tag){
    tag.textContent = kind==='no-origin' ? 'NO DATUM'
                    : kind==='none' ? 'NO SPEED' : kind==='est' ? 'EST' : '';
    tag.className = 'est-tag' + (kind==='est' ? ' on'
                  : (kind==='none' || kind==='no-origin') ? ' none' : '');
  }
  liveTitle(el,
    kind==='stale'    ? 'the link has gone quiet, so this is not a current reading'
  : kind==='no-origin'? 'no launch point is set yet, so navigation has nothing to measure speed '
                      + 'AGAINST. Nothing is wrong with the paddlewheel - set the origin and this fills in'
  : kind==='none'     ? 'nothing is reporting a speed at all - no paddlewheel pulses and no estimate'
  : kind==='measured' ? 'MEASURED - the paddlewheel counted this much water going past the hull'
  : 'ESTIMATED from the throttle curve, because the paddlewheel is not turning. '
    + 'A snagged sub reports exactly this, so never read it as progress');
}

/* ---- HEADING TRUST --------------------------------------------------------
   Flagged EVERYWHERE the heading is shown (§5.6): the number wears the mark and so
   does the map (map.js, same vocabulary). The number itself changes too — dotted
   underline for suspect, dashed for gyro-only — so the flag is never the only
   carrier and the bearing cannot be read as trustworthy at a glance. */
/* Every class ANY heading flag can wear, read out of the table rather than listed by
   hand. The hand-written list is what broke: 'nomag' was added to HEADING_FLAGS with
   cls 'suspect', and the `f==='mag' || f==='gyro-mag'` test below was not extended, so
   NO COMPASS badged itself and left the bearing looking like every trustworthy number
   on the bar — the badge as the only carrier, which is precisely what this file's rule
   forbids. Derived from the table, a flag added tomorrow cannot repeat it. */
const HEADING_MARKS = Object.keys(HEADING_FLAGS)
  .reduce((a,k)=>a.concat(String(HEADING_FLAGS[k].cls||'').split(/\s+/)), [])
  .filter((c,i,a)=>c && a.indexOf(c)===i);
let _hdgFlag='?';                 // sentinel: '' is a real state, so it cannot be the starting value
function renderHeadingFlag(v){
  const el=$('hdg-flag'), val=$('heading-val');
  const f = v.stale ? '' : (v.headingFlag || '');
  // A MARK QUALIFIES A NUMBER, so it only goes on while there IS one. Every mark in
  // this vocabulary is a sentence about a bearing on the screen — dotted says "this
  // bearing is suspect", dashed says "this bearing is being coasted" — and there is no
  // bearing behind NO BEARING or NO COMPASS. Left on, the dotted underline meant for an
  // uncalibrated compass ends up drawn under a readout that is not a compass reading at
  // all, which on the ABSENT hull would be a mark about a part that is not in the boat.
  // renderSensed has already given the blank its own shape ('?' wavy for cannot-tell,
  // the grey rule for absent); this is the badge staying out of its way.
  //
  // Derived from the VALUE rather than from a list of flag names, deliberately: the
  // hand-written list is exactly what broke here before (see HEADING_MARKS above), and
  // a flag added tomorrow for a bearing that does not exist gets this right for free.
  const num = !v.stale && v.heading != null;
  const key = f + (num ? '' : '|blank');
  if(_hdgFlag===key) return;
  _hdgFlag=key;
  const d = HEADING_FLAGS[f];
  if(el){
    el.textContent = d ? d.label : '';
    el.className = 'm-flag' + (d ? ' on '+d.cls : '');
    liveTitle(el, d ? d.title : 'the compass is calibrated and the filter is using it');
  }
  if(val){
    // The number wears whatever the flag wears. One vocabulary, one source.
    const cls = (d && num) ? String(d.cls||'').split(/\s+/) : [];
    HEADING_MARKS.forEach(c=>val.classList.toggle(c, cls.indexOf(c)>=0));
  }
}

/* ---- THE ALERT STACK ------------------------------------------------------
   The few things that need saying in words, in severity order, sitting under the top
   bar on the rail side so the eye lands on SURFACE at the end of the sentence that
   asked for it. Every chip is a SHAPE plus TEXT: none of them is a colour on its own,
   and none of them is a generic error — a snag and a flood want opposite reactions
   and must never share a presentation.

   Nothing here blocks anything: no dialogs, no modal, no confirmation. The operator
   is flying a sub and cannot be made to answer a question first. */
const ALERT_ICONS = {
  flood:  '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/></svg>',
  water:  '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3.2s5.6 6.4 5.6 9.8a5.6 5.6 0 0 1-11.2 0c0-3.4 5.6-9.8 5.6-9.8z"/><path fill="currentColor" d="M6.7 14.2a5.6 5.6 0 0 0 10.6 0z"/></svg>',
  batt:   '<svg viewBox="0 0 24 24"><rect x="3" y="8" width="15" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="2"/><rect x="19" y="11" width="2.5" height="3" fill="currentColor"/><rect x="5" y="10" width="3" height="5" fill="currentColor"/></svg>',
  snag:   '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M3 4v6a4 4 0 0 0 4 4h3"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="m8 11 2.5 3L8 17"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M13 14h3a4 4 0 0 1 4 4v3"/></svg>',
  syringe:'<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M8 3h8M9.5 3v13L12 21l2.5-5V3"/><path stroke="currentColor" stroke-width="1.6" d="M9.5 9h5"/></svg>',
  probe:  '<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M3 12h5l2-3M14 12h7"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="m10.5 15 1.5-3"/></svg>',
  // A CHIP WITH ITS LEGS, STRUCK THROUGH. Not a generic warning triangle and not the
  // probe glyph: "the depth sensor has died" and "a leak probe is lying" are different
  // things to go and do, so they must not arrive as the same drawing.
  sensor: '<svg viewBox="0 0 24 24"><rect x="7.5" y="7.5" width="9" height="9" rx="1.4" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M10 7.5V4.5M14 7.5V4.5M10 16.5v3M14 16.5v3M7.5 10h-3M7.5 14h-3M16.5 10h3M16.5 14h3"/>'
        + '<path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4.5 19.5 19.5 4.5"/></svg>',
  // THE HULL NOBODY IS WATCHING. The same drawing as the leak glyph's fourth shape —
  // broken drop, question mark — so the chip and the icon it explains are visibly one
  // fact. Not the `sensor` chip-with-legs: the leak probes are not a chip on the bus,
  // and "a sensor died" and "the hull's state is unknown" are different sentences.
  leakunknown: '<svg viewBox="0 0 24 24">'
    + '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    + 'stroke-dasharray="3.2 2.8" d="M12 3.2s5.6 6.4 5.6 9.8a5.6 5.6 0 0 1-11.2 0c0-3.4 5.6-9.8 5.6-9.8z"/>'
    + '<path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
    + 'd="M10.2 11.1a1.85 1.85 0 1 1 1.85 1.85v1.35"/>'
    + '<circle cx="12.05" cy="17.1" r="1.05" fill="currentColor"/></svg>',
  // EVERY NUMBER ON SCREEN IS INVENTED. A screen with a model behind it, drawn as a
  // screen with a wave in it - not a warning triangle, because this is not a fault on
  // the vehicle, it is the console having quietly changed what it is showing.
  sim: '<svg viewBox="0 0 24 24"><rect x="2.5" y="4.5" width="19" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="2"/>'
     + '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M5.5 12.5c1.6-2.2 3.2-2.2 4.8 0s3.2 2.2 4.8 0 3.2-2.2 3.4-1.1"/>'
     + '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M9 20.5h6"/></svg>'
};
function alertList(v){
  const out=[];
  const push=(id,kind,glyph,text,title)=>out.push({id,kind,glyph,text,title});
  // THE SIMULATOR HAS TAKEN THE GAUGES AND THE LINK IS STILL UP.
  //
  // First, because it qualifies every other chip under it: once this is showing, the
  // flood, the snag and the pack below are all statements about a model. It cannot
  // collide with a real alarm — a hull that is still talking never reaches this branch
  // — so being at the top costs nothing and being anywhere else risks it scrolling off
  // the bottom of a stack it is the precondition for.
  if(v.simTakeover)
    push('simtakeover','crit',ALERT_ICONS.sim,'SIMULATED · NO DATA ON AN OPEN LINK',
         'THE NUMBERS ON THIS SCREEN ARE NO LONGER COMING FROM THE SUB - the control '
       + 'socket is still open and the status row still shows it as connected, but no '
       + 'telemetry has arrived for seconds, so the console has handed every gauge to '
       + 'the local simulator. Depth, heading, ballast and the pack are all being '
       + 'modelled and will keep moving plausibly. Nothing you see here is a '
       + 'measurement until this chip clears.');
  if(v.leakStage==='FLOOD')
    push('flood','crit',ALERT_ICONS.flood,'FLOOD · SURFACE NOW',
         'FLOODING - water has reached the upper probe, about 2 cm above the hull floor. '
       + 'Hold SURFACE to blow the ballast and bring the sub up now.');
  // THE HULL, WITH NOBODY READING THE PROBES.
  //
  // Kept OUTSIDE the `!v.stale` guard the dead-sensor chips sit behind, and the
  // difference is real: those are inferred from a reading having gone absent, which a
  // one-second link gap can fake. This is the vehicle SAYING SO in a field of its own,
  // and a late frame does not make what it said less true.
  //
  // Critical, not a warning. Every other cannot-tell on this console costs the operator
  // a number; this one costs them the answer to "is the hull still sound", and it can
  // never compete with a flood because a standing FLOOD outranks UNKNOWN in leakStage()
  // and this chip is then not built at all.
  const leakChips = (v.leakStage==='UNKNOWN') ? faultChips('leak', v.sensorFaults) : [];
  if(v.leakStage==='UNKNOWN')
    push('leakunknown','crit',ALERT_ICONS.leakunknown,
         'HULL STATE UNKNOWN · ' + (leakChips.length ? chipMeans(leakChips[0]).short + ' STOPPED'
                                                     : 'PROBES NOT READ'),
         'NOTHING IS READING THE LEAK PROBES, so the hull is neither dry nor wet as far '
       + 'as this console knows'
       + (leakChips.length ? (' - the vehicle names ' + leakChips.map(c=>chipMeans(c).long).join(' and ')) : '')
       + '. The drop is deliberately not the green one: green is a positive claim that '
       + 'both probes were read and neither was wet, and neither was read. Water could '
       + 'be coming in right now and nothing on this vehicle would notice. Treat the '
       + 'dive as unmonitored and come home.');
  // ONLY A MEASURED VOLTAGE MAY RAISE THE SURFACE PROMPT. batteryBand(null) already
  // answers 'none' rather than a band, but the `!=null` is written out here anyway
  // because this is the chip the whole finding is about: the console used to print
  // "BATTERY 0.0V · SURFACE" — the loudest sentence it can say, in red, with a number
  // no vehicle has ever been at while transmitting — off nothing but a silent INA219.
  // The old '--' fallback in the text is gone with it: it existed to paper over exactly
  // the case that must not reach this line, and "BATTERY --V · SURFACE" is the same
  // false alarm with the number filed off. An absent pack is a SENSOR chip below.
  const band = batteryBand(v.stale ? null : v.batteryV);
  if(band.key==='crit' && v.batteryV!=null)
    push('batt','crit',ALERT_ICONS.batt,
         'BATTERY ' + v.batteryV.toFixed(1) + 'V · SURFACE',
         'PACK CRITICAL - the 3S battery is below '
       + (((CONFIG.battery||{}).critV)||9.9) + ' V. Surface now: '
       + (((CONFIG.battery||{}).floorV)||9.0) + ' V is the hard floor and the cells are '
       + 'damaged below it, and a browning-out Pi drops the tether link with it.');
  // THE SNAG, AND THE TWO THINGS THAT ARE NOT A SNAG.
  //
  // Only `true` is the alarm. The bug this replaces was that only `false` could ever
  // take the alarm down: a null fell through net.js's boolean guard, state kept the
  // last true, and a snag alarm raised a moment before the estimator died stayed lit
  // for the rest of the session with nothing on earth able to clear it.
  //
  // But a null must not clear it in silence either — the sub was pinned when nav last
  // looked, and nav going quiet is not evidence that it came free. So the alarm stays
  // CRITICAL and changes its words instead: it says the claim can no longer be
  // confirmed, which is the true state of affairs and still reads as "go and deal with
  // this". The one thing it must never do is disappear.
  if(v.snagged)
    push('snag','crit',ALERT_ICONS.snag,'SNAGGED · NO WAY ON',
         'SNAGGED - the thrusters have been running hard for seconds with no water going past '
       + 'the paddlewheel. The sub is pinned on something while the map may still be drawing '
       + 'progress. Stop, back off gently, and check the tether.');
  else if(v.snagStood)
    push('snag','crit',ALERT_ICONS.snag,'SNAGGED · UNCONFIRMED',
         'THE SUB WAS PINNED AND NOTHING IS WATCHING ANY MORE - navigation reported a snag and '
       + 'has now stopped answering, so this alarm cannot be confirmed and cannot be cleared. '
       + 'Nav going quiet is not evidence the sub came free. Treat it as still snagged: stop, '
       + 'back off gently, check the tether.');
  else if(v.snagUnknown)
    // Not an alarm — nothing has claimed a snag. But the WATCH is gone, and losing a
    // safety net quietly is how an operator goes on trusting it. Warn, not crit, so it
    // cannot compete with a flood; and silent altogether on a hull whose estimator was
    // never running, where a permanent chip would just teach the eye to skip the rail.
    push('snagwatch','warn',ALERT_ICONS.snag,'SNAG WATCH LOST · NAV QUIET',
         'NOTHING IS WATCHING FOR A SNAG - navigation was reporting and has stopped, so the '
       + 'check that compares hard thrust against water actually going past the hull is no '
       + 'longer running. A pinned sub will now look exactly like a moving one. The bearing '
       + 'has fallen back to the raw compass for the same reason.');
  // A SENSOR THAT ANSWERED AND THEN STOPPED — the failure two earlier reviews walked
  // straight past, because everyone reasoned about sensors that never answered.
  //
  // The number is already a question mark by the time this runs, and that is not
  // enough on its own. A reading that has simply gone blank with no cause attached
  // reads as a glitch in the dashboard, and a glitch is something an operator waits
  // out — while the sub keeps descending on a depth nobody is measuring. Naming the
  // chip is what turns it into an errand: go and look at that cable.
  //
  // Skipped while STALE, because a link that went quiet for a second is not a dead
  // sensor and must not be accused of being one.
  //
  // AND A PART THAT WAS NEVER FITTED GETS NO CHIP AT ALL, which is the other half of
  // the same rule. `absent` is what this reading has to say about a blank before it can
  // claim anything stopped: a hull being built one instrument at a time has most of its
  // readings blank, permanently and correctly, and a rail carrying four crit chips
  // naming four errands that do not exist is a rail nobody reads by the second dive —
  // including on the day the flood chip is the one underneath them. The readout itself
  // still says so, quietly, in its own tooltip (notFittedWhy), which is where a fact
  // that asks nothing of anybody belongs.
  if(!v.stale){
    const absent = k => sensorAbsent(k, v.sensorFaults, v.sensorsAbsent);
    const gone=[];
    if(v.depth==null && v.pressure==null && !absent('depth')) gone.push(['DEPTH & PRESSURE','depth']);
    else{
      if(v.depth==null && !absent('depth'))       gone.push(['DEPTH','depth']);
      if(v.pressure==null && !absent('pressure')) gone.push(['PRESSURE','pressure']);
    }
    if(v.heading==null && !absent('heading')) gone.push(['BEARING','heading']);
    // AND THE FOUR INERTIAL READINGS ARE DELIBERATELY NOT IN THIS LIST. They come off
    // the same BNO085 as the bearing, so when that chip stops they all blank in the same
    // frame — and pushing them here would put FIVE crit chips on the rail for ONE dead
    // connector, with the flood chip somewhere underneath them. The rail exists to turn a
    // blank into an errand, and there is exactly one errand: go and look at the compass
    // module. That chip is already above. The four go quiet behind it, each with a
    // question mark and a tooltip of its own, and the ATTITUDE heading carries the mark
    // so a folded group cannot hide it either.
    //
    // The blank still arrives with a cause on a hull that names one: `imu` is in
    // SENSOR_BEHIND, so faultChips() finds "bno085" for the group mark and for every
    // tile's sentence — it is only the CHIP on the alert rail that is spent once.
    // THE PACK BELONGS IN THIS LIST, and its absence from it is the whole of finding
    // two. Every other measured reading on the bar had somewhere to say "the chip
    // behind me stopped"; the voltage did not, so the only thing an absent INA219
    // could produce was a critical alarm about a number nobody took.
    //
    // AND IT SAYS THE CURRENT WENT WITH IT, because it did: volts and amps are one
    // INA219, so they null in the same frame, and the operator now has a DRAW readout
    // that has gone to a question mark alongside. Named on the one chip exactly the way
    // DEPTH & PRESSURE are named on the one MS5837 — one dead chip is one errand, and
    // two chips for it would be two people-sized jobs for one connector.
    //
    // Deliberately no chip for a current that is missing ON ITS OWN. That is not a
    // failure this vehicle can produce (one chip, both readings), so the only hull that
    // reaches it is one too old to send `current_a` at all — and a console that greets an
    // older Pi with a critical alarm about a reading it never had is a console that gets
    // its alarms ignored. The DRAW tile still shows the question mark and its own tooltip
    // still says nothing is measuring it; what it does not do is manufacture an errand.
    if(v.batteryV==null && !absent('battery'))
      gone.push([(v.currentA==null && v.currentSeen) ? 'PACK VOLTAGE & CURRENT' : 'PACK VOLTAGE', 'battery']);
    // WHICH NAMES THE SCREEN HAS NOW ACCOUNTED FOR. Collected as we go so the sweep
    // below can tell a fault that explains a blank from one that explains nothing on
    // screen at all. Seeded with the leak sampler's names when the hull-unknown chip is
    // up: it accounts for them exactly as a blanked gauge accounts for its chip, and
    // reporting "leak-probes" twice — once as the reason the hull is unknown and once
    // as a fault nothing is drawn from — would contradict itself on one screen.
    //
    // AND SEEDED WITH EVERY PART THE HULL CALLS ABSENT, for a stronger reason than
    // tidiness. The sweep below exists to catch a fault the screen dropped on the floor,
    // and it cannot tell "nothing is drawn from this" from "nothing was ever going to
    // be": without this, a vehicle with three instruments still to fit would raise a
    // standing NOT ANSWERING chip for each of them, which is the accusation this whole
    // change removes, re-entering by the back door. Nothing is lost — every one of these
    // names is already spoken beside its own readout, in the tooltip that says the part
    // was never fitted.
    const explained=leakChips.concat(v.sensorsAbsent||[]);
    gone.forEach(g=>{
      const chips = faultChips(g[1], v.sensorFaults);
      chips.forEach(c=>{ if(explained.indexOf(c)<0) explained.push(c); });
      const cause = chips.map(c=>chipMeans(c).long).join(' and ');
      // THE CAUSE GOES ON THE CHIP, not only in the tooltip behind it. A handheld in
      // sunlight with wet hands does not hover anything, and the two causes ask for
      // completely different errands: one dead sensor is one connector, and a dead bus
      // is every connector plus its power. Naming the JOB and not the part number is
      // the point — "ms5837" sends nobody anywhere.
      const short = chips.indexOf('i2c')>=0 ? 'I2C BUS DOWN'
                  : chips.length            ? chipMeans(chips[0]).short + ' STOPPED'
                  : 'SENSOR STOPPED';
      push('dead-'+g[1],'crit',ALERT_ICONS.sensor,'NO ' + g[0] + ' · ' + short,
           'THE SENSOR BEHIND ' + g[0] + ' HAS STOPPED ANSWERING'
         + (cause ? (' - the vehicle names ' + cause) : '')
         + '. It reported earlier in this dive and does not now, so the reading is shown '
         + 'as a question mark rather than as the last number it gave: a frozen reading '
         + 'and a steady one look identical, and flying on the frozen one is how a sub '
         + 'goes deeper than the console admits.');
    });
    // ANYTHING THE VEHICLE NAMED THAT NOTHING ABOVE ACCOUNTED FOR.
    //
    // sensor_faults is not only chips behind gauges: api/hardware.py unions in its
    // latched subsystem faults, so "ballast-limits" arrives here today and lands
    // nowhere, and any name added to the hull after this handheld was flashed does the
    // same. A fault the vehicle went to the trouble of reporting, dropped silently by
    // the console, is the round-three mistake in miniature — the hull knew and the
    // screen did not say. Warn rather than crit: nothing on screen has gone blank
    // because of it, so this is a thing to go and look at, not a thing to surface for.
    //
    // THE SENTENCE USED TO SAY "Nothing on this screen was being drawn from it", AND
    // THAT WAS FALSE. "leak-probes" landed here, and the hull-integrity glyph IS drawn
    // from it — the chip was quietly certifying that a fault which had just taken the
    // leak readout away had taken nothing away. It is accounted for above now, and the
    // wording no longer makes a claim about what the screen draws from a part this
    // console may never have heard of: all it can honestly say is that nothing has gone
    // blank, which is the observation it actually has.
    const rest = unexplainedFaults(v.sensorFaults, explained);
    if(rest.length)
      push('faults','warn',ALERT_ICONS.sensor,
           'NOT ANSWERING · ' + rest.map(c=>chipMeans(c).short).join(' · '),
           'THE VEHICLE REPORTS THAT ' + rest.map(c=>chipMeans(c).long).join(' and ')
         + ' has stopped answering. No reading on this screen has gone blank because of '
         + 'it - so either what it measures is not shown here, or something else is '
         + 'still standing on the last thing it said - but the hull is naming it as '
         + 'faulted, and a fault nobody is shown is a fault nobody fixes. Check it '
         + 'before the next dive.');
  }
  if(v.leakStage==='WARN')
    push('leakwarn','warn',ALERT_ICONS.water,'WATER COLLECTING · FINISH UP',
         'LEAK WARNING - the lower probe on the hull floor is wet. Not an emergency yet: the '
       + 'upper probe 2 cm above it is still dry. Finish what you are doing and come home.');
  if(v.leakProbeFault)
    push('probe','warn',ALERT_ICONS.probe,'LEAK PROBE FAULT · ' + String(v.leakProbeFault).toUpperCase(),
         'A LEAK PROBE IS OPEN OR SHORTED - a dead probe reads dry forever, which is the one '
       + 'failure this design otherwise hides. The named probe is not to be trusted until its '
       + 'wiring is checked.');
  if(v.ballastRehome)
    push('rehome','warn',ALERT_ICONS.syringe,'BALLAST LOST COUNT · RE-HOME',
         'THE SYRINGE SKIPPED STEPS - it hit its limit switch at the wrong count, so the level '
       + 'shown is no longer step-count truth. Press HOME on the rail to drive it back to the '
       + 'empty stop and re-zero it.');
  else if(!v.ballastKnown)
    push('nothomed','warn',ALERT_ICONS.syringe,'BALLAST NOT HOMED · PRESS HOME',
         'THE SYRINGE HAS NEVER BEEN HOMED - it is an open-loop stepper with no position '
       + 'sensor, so until it has been driven onto its empty stop nobody knows how much water '
       + 'is in it. Press HOME on the rail before diving.');
  return out;
}
let _alertSig='';
function renderAlerts(v){
  const host=$('alerts'); if(!host) return;
  const list=alertList(v);
  // Signed on the rendered TEXT, not just the ids: the battery chip carries a live
  // number and has to follow it down. Keyed on the voltage regardless of whether a
  // chip is showing, this rebuilt an empty stack every 0.1 V for the whole dive.
  const sig=list.map(a=>a.id+':'+a.text).join('|');
  if(sig===_alertSig) return;                 // innerHTML swaps only on a real change
  _alertSig=sig;
  host.innerHTML = list.map(a=>
    '<div class="alert '+a.kind+'" title="'+a.title+'" aria-label="'+a.title+'">'
    + '<span class="alert-ic">'+a.glyph+'</span><span class="alert-tx">'+a.text+'</span></div>').join('');
  host.classList.toggle('on', list.length>0);
  if(list.length) LOG.warn('ALERT: ' + list.map(a=>a.text).join(' | '));
}

let _prev={}, _ballastKnown=true, _ballastRehome=false, _simTakeover=null;
function renderUI(v){
  const stale=!!v.stale;
  // SIMULATED, WITH THE LINK STILL SHOWING GREEN (main.js sets the flag). Two carriers,
  // because the status row is actively saying the opposite: a badge across the top where
  // the STALE one lives — same family, because they are the same kind of statement about
  // where the numbers came from — and a chip in the alert rail that says it in a
  // sentence. Never the badge alone: a badge is a colour and a word, and this needs to
  // say WHY the gauges are still moving.
  const takeover = !!v.simTakeover;
  if(_simTakeover !== takeover){
    _simTakeover = takeover;
    document.body.classList.toggle('sim-takeover', takeover);
    const sb=$('sim-badge'); if(sb) sb.classList.toggle('show', takeover);
  }
  // Numeric readouts (dashed when stale)
  // The pack, and the question mark when nothing is measuring it. `(x!=null?x:'--')+'V'`
  // stood here, which spelled an absent pack "--V" — the STALE shape, the one that means
  // a frame was dropped and will be along shortly. renderBattery owns the colour, the
  // class and the sentence; this owns the glyph, and the two agree on `!stale && null`.
  //
  // AND A PACK MONITOR THIS HULL NEVER HAD GETS THE ABSENT RULE INSTEAD, the same
  // three-way split renderSensed makes for every other reading: '?' is a chip that
  // stopped and an errand, the grey rule is an instrument that is not in the boat. This
  // tile is hand-drawn rather than going through renderSensed (renderBattery owns its
  // colour and its bands), so the split has to be spelled out here — and it has to be,
  // or the one number on this bar that cannot use the shared path would go on accusing
  // a part nobody ever fitted while every other tile had stopped.
  const battAbsent = !stale && v.batteryV==null && sensorAbsent('battery', v.sensorFaults, v.sensorsAbsent);
  const battDead = !stale && v.batteryV==null && !battAbsent;
  setText($('battery-v'), battDead ? '?' : battAbsent ? '—'
                        : (v.batteryV!=null?v.batteryV.toFixed(1)+'V':'--V'), stale);
  // Depth and pressure are drawn further down, by renderSensed, together with the tint
  // they are or are not allowed to wear — the two decisions are one decision.
  // Pi system metrics are rendered by renderSystem() from /api/system, which is
  // independent of the vehicle link — see status.js. Nothing to do here.
  // BALLAST, AND THE CASE WHERE IT DOES NOT KNOW.
  //
  // The syringe is an open-loop stepper: until it has been homed onto its empty stop
  // there is no position to report. 0% would say "empty, you can dive" and 50% would
  // say "half a tank" — both are inventions, and both are the kind of invention that
  // gets believed because a syringe with a number in it looks like a measurement.
  // So the barrel fills with a diagonal HATCH over its whole length (the water could
  // be anywhere in there) and the readout is a question mark. The SHAPE of the
  // syringe, its drag-up-to-fill gesture and the target mark are all untouched: the
  // commanded target is still perfectly known, and being unable to read the tank is
  // no reason to stop being able to command it.
  const known = v.ballastKnown !== false;
  setText($('ballast-pct'), known ? (Math.round((v.ballastLevel||0)*100)+'%') : '?', stale);
  // THE BEARING, OR THE ADMISSION THAT THERE IS NONE. `Math.round(v.heading||0)` stood
  // here, which turned a null bearing into a confident 0° — due north — and that is the
  // single worst number to invent on a heading-up map, because the operator reads it as
  // "pointing north" rather than as "nobody knows". renderHeadingFlag puts NO BEARING
  // beside it and dot-underlines it; map.js's own badge says the same word over the
  // radar, which is still drawn on the last angle the compass ever reported.
  renderSensed('heading-val', v.heading, Math.round(v.heading||0)+'°', stale, null,
               'BEARING', 'heading', v);
  // Gauges / bars keep last position; only dim on stale.
  const dim = stale ? '0.45' : '1';
  const fill=$('ballast-fill');
  fill.style.height = known ? (Math.round((v.ballastLevel||0)*100)+'%') : '100%';
  fill.style.opacity = dim;
  // The liquid is the plunger, and it is the colour of the depth that much water buys.
  const tint = metricTints(v);
  const fillKey = known ? tint.ballast : 'unknown';
  if(_tint.fill !== fillKey){
    _tint.fill = fillKey;
    if(!known){
      // Hatched, not tinted: it must not wear a depth band, because a depth band is a
      // claim about how deep this much water takes you and there is no "this much".
      fill.style.background  = 'repeating-linear-gradient(135deg,'
                             + 'color-mix(in srgb,var(--hazard) 34%,transparent) 0 5px,'
                             + 'rgba(12,1,24,.55) 5px 10px)';
      fill.style.borderColor = 'var(--hazard)';
      fill.style.boxShadow   = 'none';
    } else {
      fill.style.background  = tint.ballast || 'var(--water)';
      fill.style.borderColor = tint.ballast || 'var(--water-edge)';
      // Tight, not a halo, and thrown UPWARD off the meniscus: an 8px bloom spilled a
      // whole band of colour through the empty barrel and the water level stopped
      // having an edge.
      fill.style.boxShadow   = '0 -1px 4px ' + (tint.ballast || 'var(--water)');
    }
  }
  paintMetric('ballast-pct', known ? tint.ballast : 'var(--hazard)');
  // TWO DIFFERENT REASONS THE TANK IS UNKNOWN, and the tooltip has to tell the true
  // one. "The syringe has never been homed" was the whole story when `homed` was the
  // only way to lose the level; then skipped steps became a second way, and this text
  // went on flatly denying it — an operator who HAD homed the syringe, and watched it
  // home, was told it had never happened. Same question mark, same amber, different
  // sentence, because they are different faults with the same remedy.
  const askRehome = !!v.ballastRehome;
  if(_ballastKnown !== known || _ballastRehome !== askRehome){
    _ballastKnown = known;
    liveTitle('ballast-pct', known ? ''
      : askRehome
      ? 'UNKNOWN - the syringe hit its limit switch at the wrong step count, so steps '
      + 'were skipped and the number it was holding no longer maps to a real plunger '
      + 'position. It has been homed; the count has since drifted. Press HOME on the '
      + 'rail to drive it back to the empty stop and re-reference it.'
      : 'UNKNOWN - the syringe has never been homed, so its position is genuinely not '
      + 'known. Press HOME on the rail to drive it to the empty stop and re-zero it.');
    // The HOME affordance appears exactly when it is the next thing to do, and takes
    // no room in the rail the rest of the time.
    document.body.classList.toggle('ballast-home-needed', !known);
  }
  if(_ballastRehome !== askRehome){
    _ballastRehome = askRehome;
    if(_ballastRehome) document.body.classList.add('ballast-home-needed');
    else if(known) document.body.classList.remove('ballast-home-needed');
  }
  // THE RADAR IS HEADING-UP: with no bearing, the whole picture is still rotated by the
  // last angle the compass ever reported, and it will sit there looking like a sub
  // holding course. map.js's own badge says NO BEARING over it (it reads the same
  // HEADING_FLAGS table), and this marks the dial itself so the ring the picture is
  // drawn in admits it too — the badge must never be the only carrier.
  //
  // ASKED OF BOTH SOURCES, because the dial does not turn on the number beside it. The
  // bearing on this bar comes off /ws/control and the angle the map is rotated by comes
  // off /ws/nav, which is a different socket that fails separately — so a nav frame with
  // a null heading holds the picture (map.js setMapHeading) while telemetry is still
  // reporting a perfectly good compass, and without this the held picture would wear no
  // mark at all. Guarded on MAP existing: the map is allowed to fail alone (§3).
  //
  // AND NOT RAISED FOR A COMPASS THIS HULL NEVER HAD. The amber broken ring says "the
  // picture you are looking at was being measured and is not any more" — it is about
  // something having STOPPED, and on a boat with no IMU fitted there is no such moment
  // and no held angle to distrust: the dial has never turned. Painting it amber for the
  // whole of every dive until the part arrives is the standing accusation that teaches
  // an operator to stop seeing amber. The badge still reads NO COMPASS over the dial
  // (map.js, off the shared HEADING_FLAGS table), so the fact is never unsaid.
  const hdgAbsent = sensorAbsent('heading', v.sensorFaults, v.sensorsAbsent);
  const hdgHeld = (typeof MAP!=='undefined') && MAP.hdgLive===false;
  document.body.classList.toggle('heading-dead',
                                !stale && ((v.heading==null && !hdgAbsent) || hdgHeld));
  // DEPTH AND PRESSURE, each with the two ways it can be absent kept apart. The tint
  // and the number are set together on purpose: they are one claim, and the dive that
  // motivated all of this is the one where they came apart — a frozen 4.3 m wearing a
  // confident depth-band colour while the sub was at 8 m.
  renderSensed('depth-val', v.depth, (v.depth!=null?v.depth.toFixed(1):'--')+' m',
               stale, tint.depth, 'DEPTH', 'depth', v);
  renderSensed('pressure-val', v.pressure, (v.pressure!=null?v.pressure.toFixed(1):'--')+' PSI',
               stale, tint.pressure, 'PRESSURE', 'pressure', v);
  // Target marker = where the operator SET it (the goal). The fill (actual level)
  // chases it smoothly via the slewed command. The fill grows UP from the tip.
  const tgt=clamp(state.ballastTargetRaw,0,1);
  const mark=$('ballast-target-mark');
  mark.style.bottom = (tgt*100)+'%';   // measured from the tip, like the liquid
  mark.style.opacity = (Math.abs(tgt-(v.ballastLevel||0))<0.01) ? '0' : '1';
  // SONAR: plot the movement input as a vector (steer = x, throttle = y-up).
  // Scale 62 keeps full diagonal (~88) inside the outer ring (92).
  const R=62, sx=(state.input.steer||0)*R, sy=-(state.input.throttle||0)*R;
  const vec=$('sonar-vec'), dot=$('sonar-dot');
  if(vec){ vec.setAttribute('x2', sx.toFixed(1)); vec.setAttribute('y2', sy.toFixed(1)); }
  if(dot){ dot.setAttribute('cx', sx.toFixed(1)); dot.setAttribute('cy', sy.toFixed(1)); }
  // Four directional numbers, 0-100, one per compass point. Each shows only its OWN
  // half of the axis, so "how hard am I pushing that way" is read straight off the
  // side you are pushing rather than decoded from a signed percentage.
  setInputNum('in-fwd',   state.input.throttle);
  setInputNum('in-rev',  -state.input.throttle);
  setInputNum('in-right', state.input.steer);
  setInputNum('in-left',  -state.input.steer);
  // Lamps / toggles (only re-render on change to keep innerHTML swaps cheap)
  if(_prev.green!==v.green || _prev.greenLevel!==Math.round(v.greenLevel*100)){ renderLightButton('green', v.green, v.greenLevel); }
  if(_prev.white!==v.white || _prev.whiteLevel!==Math.round(v.whiteLevel*100)){ renderLightButton('white', v.white, v.whiteLevel); }
  if(_prev.armed!==v.armed) renderArmed(v.armed);
  if(_prev.magnet!==v.magnet) renderMagnet(v.magnet);
  if(_prev.leakStage!==v.leakStage) renderLeak(v.leakStage);
  // Link ms
  setText($('link-ms'), (v.linkMs!=null? v.linkMs+' ms':'-- ms'), false);
  // §5 readings that carry their own honesty: the pack on its 3S bands, water speed
  // as a measurement or an estimate, and how much the heading is worth.
  renderBattery(v);
  renderSpeed(v);
  renderHeadingFlag(v);
  // The five readings that used to have nowhere to go. Built on first frame from the
  // FLIGHT_METRICS table rather than written into index.html, so that cutting one back
  // later is deleting one entry — see the section above.
  buildFlightCluster();
  renderFlight(v);
  renderAlerts(v);
  _prev={green:v.green,greenLevel:Math.round(v.greenLevel*100),white:v.white,whiteLevel:Math.round(v.whiteLevel*100),armed:v.armed,magnet:v.magnet,leakStage:v.leakStage};
}

/* ---- Status chips (top-bar VIDEO indicator) ---- */
function setChip(id, cls, text){
  const chip=$(id); if(!chip) return;
  const dot=chip.querySelector('.hud-dot'), t=$(id+'-text');
  if(dot) dot.className='hud-dot '+cls;
  if(t) t.textContent=text;
}
// Video status is conveyed by the feed's own NO-FEED / RECONFIGURING overlay
// (the top-bar VIDEO chip was redundant and removed).
function updateBadges(){ /* no-op */ }

/* ============================================================================
   PI SYSTEM HEALTH — real readings from /api/system (api/sysinfo.py).

   Deliberately separate from vehicle telemetry: the Pi's own CPU/RAM/disk and
   the state of its two network interfaces are meaningful even when the ROV
   hardware is simulated or the camera is unplugged.

   A missing probe arrives as null and renders as "--". It is never shown as 0 —
   "CPU 0 deg C" reads as a real measurement and hides the fault, which is exactly
   how the old psutil-less path made every gauge look plausible and be wrong.
   ============================================================================ */
function renderSystem(s){
  const dash = (el)=>{ if(el){ el.textContent='--'; el.classList.add('is-stale'); } };
  const put  = (el, txt)=>{ if(el){ el.textContent=txt; el.classList.remove('is-stale'); } };

  const ids = ['cpu-c','cpu-pct','ram-pct','disk-gb','net-eth'];
  if(!s || s.ok===false){ ids.forEach(id=>dash($(id))); return; }

  const cpu = s.cpu||{}, mem = s.mem||{}, disk = s.disk||{}, net = s.net||{};
  const tether = net.tether||{}, cam = net.camera||{}, wifi = cam.wifi||{};

  cpu.temp_c!=null ? put($('cpu-c'),   Math.round(cpu.temp_c)+'\u00b0C')     : dash($('cpu-c'));
  cpu.pct!=null    ? put($('cpu-pct'), Math.round(cpu.pct)+'%')              : dash($('cpu-pct'));
  mem.pct!=null    ? put($('ram-pct'), Math.round(mem.pct)+'%')              : dash($('ram-pct'));
  disk.free_gb!=null ? put($('disk-gb'), disk.free_gb.toFixed(1)+' GB')      : dash($('disk-gb'));

  // Tether: link state plus negotiated speed — this is the operator's cable.
  const eth = $('net-eth');
  if(eth){
    if(tether.present===false){ dash(eth); }
    else if(tether.up){
      put(eth, tether.speed_mbps ? (tether.speed_mbps+' Mb') : 'UP');
      eth.style.color = 'var(--tertiary)';
    } else {
      put(eth, 'DOWN');
      eth.style.color = 'var(--error)';
    }
  }
  // Camera Wi-Fi has no readout of its own any more: it is one of the three
  // states of the eye in the status row (see STATUS.camLink).

  // Undervoltage / throttling is a leading indicator of the Pi dropping its Ethernet
  // mid-dive, so it goes to the LOG. It used to borrow the camera's warning banner,
  // which is gone \u2014 and it was never a camera fault anyway, so it never belonged
  // there. Logged once per transition rather than on every poll.
  const d = s.deep||{}, th = d.throttled;
  const brownout = !!(th && (th.undervoltage_now || th.throttled_now));
  if(brownout !== renderSystem._brownout){
    renderSystem._brownout = brownout;
    if(brownout) LOG.warn('PI ' + (th.undervoltage_now ? 'UNDER-VOLTAGE' : 'THERMAL THROTTLING') +
                          ' \u2014 this is what drops the tether NIC mid-dive');
    else LOG.state('Pi power/thermal back to normal');
  }
}

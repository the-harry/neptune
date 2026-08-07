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
    s.batteryV = (CONFIG.battery && CONFIG.battery.fullV) || 8.4;
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
  // Sags toward the documented 2S floor, not toward a 20 V number belonging to a
  // pack this sub has never had. CONFIG.battery is the single place those bands live.
  s.batteryV = Math.max((CONFIG.battery&&CONFIG.battery.floorV)||6.0,
                        s.batteryV - sim.batteryDrainVPerS*dt);
  // SPEED IN SIM IS AN ESTIMATE AND SAYS SO. There is no paddlewheel on the bench,
  // so this is the throttle curve — exactly the source the HUD styles as an estimate.
  // Labelling it 'lut' rather than inventing a measurement is the whole rule: the one
  // reading that would hide a snag is a model dressed up as a sensor.
  s.speedMs = c.throttle * ((CONFIG.map&&CONFIG.map.subMaxSpeedMs)||1.0);
  s.speedSrc = 'lut';
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
  const sensed = (val, at, kind) =>
    (val!=null && (!live || (sensorFresh(at) && !faultedNow(kind, s.sensorFaults)))) ? val : null;
  return {
    stale:false, sim:!!sim,
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
    leak: stage!=='NORMAL',                 // kept: the one-bit question, for callers that only ask it
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
    // Free from the same INA219 as the voltage, so it dies with it. No freshness stamp
    // of its own: it rides in the pack tooltip and is already null whenever the chip is
    // silent — the veto is here only for the hull that names the chip and sends a
    // cached amp figure anyway.
    currentA: (live && !faultedNow('current', s.sensorFaults)) ? s.currentA : null,
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

/* LEAK: icon-only, and THREE SHAPES for three states — the drop changes what it IS,
   not just what colour it is, because a colour-blind operator in sunlight reads the
   outline first and everything else second.

     NORMAL  green SOLID drop struck through  — both probes dry
     WARN    amber HOLLOW drop, filled to a waterline across its middle — the lower
             probe is wet and water is collecting. Deliberately a different drawing
             from both neighbours: half a drop is the shape of "some water".
     FLOOD   red SOLID drop, glowing, plus the full-screen edge pulse — the upper
             probe (2 cm higher) is wet too.

   Only FLOOD pulses the screen and only FLOOD turns the tether icon into the red
   pulsing sub (status.js), which is what keeps a flood impossible to confuse with a
   link dropout — and equally keeps a WARN from being mistaken for one. */
const DROP_OK   = '<svg viewBox="0 0 24 24"><path fill="var(--tertiary)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/><path stroke="var(--tertiary)" stroke-width="2.4" stroke-linecap="round" d="M4 4l16 16"/><path stroke="#0c0118" stroke-width="1.1" stroke-linecap="round" d="M4 4l16 16"/></svg>';
const DROP_WARN = '<svg viewBox="0 0 24 24">'
  + '<path fill="none" stroke="var(--hazard)" stroke-width="2" d="M12 3.2s5.6 6.4 5.6 9.8a5.6 5.6 0 0 1-11.2 0c0-3.4 5.6-9.8 5.6-9.8z"/>'
  + '<path fill="var(--hazard)" d="M6.7 14.2a5.6 5.6 0 0 0 10.6 0z"/>'
  + '<path stroke="var(--hazard)" stroke-width="1.6" stroke-linecap="round" d="M6.6 14.2h10.8"/></svg>';
const DROP_FLOOD= '<svg viewBox="0 0 24 24"><path fill="var(--error)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/></svg>';
function renderLeak(stage){
  // Tolerates the old boolean call (main.js boot passes `false`): true was always
  // "water in the hull", which is the flood end of the ladder.
  const st = (stage===true) ? 'FLOOD' : (stage===false || !stage) ? 'NORMAL' : stage;
  const icon=$('leak-icon'), pulse=$('leak-pulse');
  if(icon){
    icon.innerHTML = st==='FLOOD' ? DROP_FLOOD : st==='WARN' ? DROP_WARN : DROP_OK;
    icon.style.filter = st==='FLOOD' ? 'drop-shadow(0 0 6px var(--error))'
                      : st==='WARN'  ? 'drop-shadow(0 0 5px var(--hazard))' : '';
    icon.className = 'leak-'+st.toLowerCase();      // hook for the suites and for CSS
    liveTitle(icon, st==='FLOOD' ? 'FLOODING - water above the upper probe. SURFACE NOW.'
                  : st==='WARN'  ? 'water is collecting on the hull floor - finish up and come home'
                  : 'both probes dry');
  }
  // The edge pulse is the FLOOD siren and nothing else. Firing it on a WARN would
  // make the advisory unignorable, and an unignorable advisory gets ignored.
  if(pulse) pulse.classList.toggle('on', st==='FLOOD');
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
   swallowed it. */
const _tint={};
function paintMetric(id, color, why){
  const el=$(id); if(!el) return;
  const sig = String(color) + '|' + String(why);
  if(_tint[id]===sig) return;
  _tint[id]=sig;
  el.style.color = color || '';
  el.style.textShadow = color ? ('0 0 8px '+color) : '';
  if(why!==undefined) liveTitle(el, why || '');
}

/* ---- A MEASURED NUMBER, AND THE TWO DIFFERENT WAYS IT CAN BE MISSING ------
   Three shapes, because two of them are not the same fact and an operator acts on
   them differently:

     42.7        the sensor is reporting. Tinted by its own band.
     '--', dim   STALE — the link went quiet for a moment. The whole bar dashes
                 together and it comes back on its own; correctly ignored.
     '?', amber  CANNOT-TELL — the chip behind this reading has stopped answering.
                 Nothing on the vehicle is measuring it, and waiting will not help.

   The question mark is this console's existing word for "genuinely not known" (the
   unhomed syringe has always said it), and it is deliberately NOT the stale dash. That
   is the whole point of the shape: a dash reads as a dropped frame, and a dead depth
   sensor dressed as a dropped frame is a sub flown on a number nobody is taking. Three
   carriers — the mark, the amber, and the alert chip naming the chip — so none of them
   has to be the one that gets noticed. */
function renderSensed(id, val, text, stale, tint, what, kind, v){
  const el=$(id); if(!el) return;
  const dead = !stale && val==null;
  setText(el, dead ? '?' : text, stale);
  el.classList.toggle('nosensor', dead);
  // The sentence is built only when there is something to admit — this runs on every
  // frame, and paintMetric's memo means the healthy case must not do work to say
  // nothing.
  paintMetric(id, dead ? 'var(--hazard)' : tint, dead ? noSensorWhy(what, kind, v) : '');
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
function renderBattery(v){
  const el=$('battery-v'); if(!el) return;
  const dead = !v.stale && v.batteryV==null;
  const band = batteryBand(v.stale ? null : v.batteryV);
  const amps = (v.currentA!=null) ? ('   —   drawing '+v.currentA.toFixed(1)+' A') : '';
  const why  = dead ? noSensorWhy('PACK VOLTAGE', 'battery', v) : (band.text + amps);
  // Signature includes staleness because setText() owns the is-stale class on this
  // element and className is assigned wholesale here: without it, one repaint on a
  // band change would quietly un-grey a dashed-out readout. It includes `why` for the
  // same reason paintMetric's does — the fault list naming the chip arrives a frame or
  // two after the null, and a signature on the band alone would swallow the sentence.
  const sig = band.key + '|' + (v.stale?'s':'') + '|' + (dead?'d':'') + '|' + why;
  if(_battSig===sig) return;
  _battSig=sig;
  const color = dead ? 'var(--hazard)' : band.color;
  el.style.color = color || '';
  el.style.textShadow = color ? ('0 0 8px '+color) : '';
  el.className = 'm-val batt-'+band.key + (v.stale?' is-stale':'') + (dead?' nosensor':'');
  liveTitle(el, why);
}

/* ---- WATER SPEED ----------------------------------------------------------
   AN ESTIMATE NEVER DRESSES AS A MEASUREMENT. A paddlewheel-backed reading is the
   plain number: something physically counted water going past. Anything from the
   throttle lookup gets the tilde AND the EST tag — the same qualifier-tag idiom the
   tether readout already uses to say PLANNED / LAST KNOWN when its range stops
   being measured. That distinction is the entire reason the paddlewheel was bought:
   a snagged sub's LUT speed looks exactly like a healthy cruise. */
let _speedSig='';
function renderSpeed(v){
  const el=$('speed-val'), tag=$('speed-src');
  if(!el) return;
  const measured = speedIsMeasured(v.speedSrc);
  const none = (v.speedMs==null || v.speedSrc==null);
  const kind = v.stale ? 'stale' : none ? 'none' : measured ? 'measured' : 'est';
  // The NUMBER changes shape too, not only its tag: a measured reading is given to
  // the centimetre because the wheel resolves that; an estimate gets a tilde and one
  // decimal, because pretending to a second one would be dressing up a guess.
  el.textContent = (kind==='stale'||kind==='none') ? '--'
                 : (measured ? '' : '~') + v.speedMs.toFixed(measured ? 2 : 1) + ' m/s';
  const sig = kind + (v.snagged?'!':'');
  if(_speedSig===sig) return;
  _speedSig=sig;
  el.className = 'm-val ' + (kind==='stale' ? 'is-stale' : kind==='none' ? '' : kind)
               + (v.snagged ? ' snag' : '');
  if(tag){
    tag.textContent = kind==='none' ? 'NO SPEED' : kind==='est' ? 'EST' : '';
    tag.className = 'est-tag' + (kind==='est' ? ' on' : kind==='none' ? ' none' : '');
  }
  liveTitle(el,
    kind==='stale'    ? 'the link has gone quiet, so this is not a current reading'
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
  if(_hdgFlag===f) return;
  _hdgFlag=f;
  const d = HEADING_FLAGS[f];
  if(el){
    el.textContent = d ? d.label : '';
    el.className = 'm-flag' + (d ? ' on '+d.cls : '');
    liveTitle(el, d ? d.title : 'the compass is calibrated and the filter is using it');
  }
  if(val){
    // The number wears whatever the flag wears. One vocabulary, one source.
    const cls = d ? String(d.cls||'').split(/\s+/) : [];
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
        + '<path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4.5 19.5 19.5 4.5"/></svg>'
};
function alertList(v){
  const out=[];
  const push=(id,kind,glyph,text,title)=>out.push({id,kind,glyph,text,title});
  if(v.leakStage==='FLOOD')
    push('flood','crit',ALERT_ICONS.flood,'FLOOD · SURFACE NOW',
         'FLOODING - water has reached the upper probe, about 2 cm above the hull floor. '
       + 'Hold SURFACE to blow the ballast and bring the sub up now.');
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
         'PACK CRITICAL - the 2S battery is below '
       + (((CONFIG.battery||{}).critV)||6.6) + ' V. Surface now: '
       + (((CONFIG.battery||{}).floorV)||6.0) + ' V is the hard floor and the cells are '
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
  if(!v.stale){
    const gone=[];
    if(v.depth==null && v.pressure==null) gone.push(['DEPTH & PRESSURE','depth']);
    else{
      if(v.depth==null)    gone.push(['DEPTH','depth']);
      if(v.pressure==null) gone.push(['PRESSURE','pressure']);
    }
    if(v.heading==null) gone.push(['BEARING','heading']);
    // THE PACK BELONGS IN THIS LIST, and its absence from it is the whole of finding
    // two. Every other measured reading on the bar had somewhere to say "the chip
    // behind me stopped"; the voltage did not, so the only thing an absent INA219
    // could produce was a critical alarm about a number nobody took.
    if(v.batteryV==null) gone.push(['PACK VOLTAGE','battery']);
    // WHICH NAMES THE SCREEN HAS NOW ACCOUNTED FOR. Collected as we go so the sweep
    // below can tell a fault that explains a blank from one that explains nothing on
    // screen at all.
    const explained=[];
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
    // screen did not say. Warn rather than crit: no reading has gone blank, so this is
    // a thing to go and look at, not a thing to surface for.
    const rest = unexplainedFaults(v.sensorFaults, explained);
    if(rest.length)
      push('faults','warn',ALERT_ICONS.sensor,
           'NOT ANSWERING · ' + rest.map(c=>chipMeans(c).short).join(' · '),
           'THE VEHICLE REPORTS THAT ' + rest.map(c=>chipMeans(c).long).join(' and ')
         + ' has stopped answering. Nothing on this screen was being drawn from it, so no '
         + 'reading has gone blank - but the hull is naming it as faulted, and a fault '
         + 'nobody is shown is a fault nobody fixes. Check it before the next dive.');
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

let _prev={}, _ballastKnown=true, _ballastRehome=false;
function renderUI(v){
  const stale=!!v.stale;
  // Numeric readouts (dashed when stale)
  // The pack, and the question mark when nothing is measuring it. `(x!=null?x:'--')+'V'`
  // stood here, which spelled an absent pack "--V" — the STALE shape, the one that means
  // a frame was dropped and will be along shortly. renderBattery owns the colour, the
  // class and the sentence; this owns the glyph, and the two agree on `!stale && null`.
  const battDead = !stale && v.batteryV==null;
  setText($('battery-v'), battDead ? '?' : (v.batteryV!=null?v.batteryV.toFixed(1)+'V':'--V'), stale);
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
  document.body.classList.toggle('heading-dead', !stale && v.heading==null);
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
  // §5 readings that carry their own honesty: the pack on its 2S bands, water speed
  // as a measurement or an estimate, and how much the heading is worth.
  renderBattery(v);
  renderSpeed(v);
  renderHeadingFlag(v);
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

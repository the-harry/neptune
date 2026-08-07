"use strict";
/* ============================================================================
   NET — WebSocket control link (auto-reconnect, capped backoff, safe send),
   telemetry ingest, and the fixed-rate send / ping / level loops.
   ============================================================================ */
function send(obj){
  // Absent/closed socket is a silent no-op — never throws.
  const ws=state.ws;
  if(!ws || ws.readyState!==WebSocket.OPEN) return;
  try{ ws.send(JSON.stringify(obj)); }catch(e){/* swallow */}
}
function setWsStatus(s){
  if(state.wsStatus!==s) LOG.net('link status ->', s);
  state.wsStatus=s; if(s!=='online'){ state.linkMs=null; }
}

function connect(){
  if(!state.wsBase){ LOG.net('no host — staying in SIM (disk mode)'); setWsStatus('offline'); return; }
  const url=state.wsBase+CONFIG.paths.control;
  LOG.net('connecting to', url);
  setWsStatus('connecting');
  let ws;
  try{ ws=new WebSocket(url); }
  catch(e){ LOG.warn('WebSocket ctor threw', e && e.message); setWsStatus('offline'); scheduleReconnect(); return; }
  state.ws=ws;
  ws.onopen = ()=>{
    state.wsOpenAt = Date.now();
    LOG.net('OPEN', url);
    setWsStatus('online');
    state.reconnectDelay=CONFIG.reconnect.baseMs;
    // Vehicle commands NEVER queue (safety rule): anything the operator set while
    // the link was down must not fire the instant it comes back. The ballast target
    // is the one control that survives a dropout as state rather than as a message,
    // so re-anchor it to the vehicle's ACTUAL level and let the operator command a
    // new one deliberately.
    const lvl = (state.realTel && typeof state.realTel.ballast_level==='number')
              ? state.realTel.ballast_level : state.ballastLevel;
    state.ballastTargetRaw = state.ballastTargetCmd = clamp(lvl||0, 0, 1);
    LOG.net('ballast target re-anchored to actual', state.ballastTargetRaw.toFixed(2));
    // AND NAVIGATION'S ACCOUNT OF ITSELF STARTS AGAIN FROM NOTHING. These two remember
    // what nav last DEFINITELY said (see core.js), and they exist to stop a null
    // clearing a standing snag alarm in silence — which means that left alone they
    // would carry the last dive's snag onto the next hull, and a latched alarm about a
    // vehicle that is no longer on the end of the cable is the same lie in the other
    // direction. A new link is a new dive: nav has said nothing yet.
    state.navAnswered = false; state.snagStood = false;
    if(window.REC && REC.enabled){ REC.log('ws_connect', {url}); REC.adoptSession(); }   // §1 adopt session on connect
  };
  ws.onmessage = (ev)=>{ handleMessage(ev.data); };
  ws.onclose = (ev)=>{ LOG.net('CLOSE', 'code='+ev.code, ev.reason||''); if(window.REC&&REC.enabled) REC.log('ws_disconnect', {code:ev.code, reason:ev.reason||''}); setWsStatus('offline'); scheduleReconnect(); };
  ws.onerror = ()=>{ LOG.warn('ws error'); try{ ws.close(); }catch(e){} };
}
function scheduleReconnect(){
  if(!state.wsBase) return;
  if(state.reconnectTimer) return;
  const d=state.reconnectDelay;
  LOG.net('reconnect in', d+'ms');
  state.reconnectTimer=setTimeout(()=>{
    state.reconnectTimer=null;
    connect();
  }, d);
  state.reconnectDelay=Math.min(CONFIG.reconnect.maxMs, d*CONFIG.reconnect.factor);
}
function handleMessage(raw){
  let m; try{ m=JSON.parse(raw); }catch(e){ LOG.warn('bad ws frame', raw); return; }
  if(m.type==='telemetry'){ LOG.rxRate('tel','telemetry', m); if(window.REC&&REC.enabled) REC.onTelemetry(m); onTelemetry(m); }
  else if(m.type==='pong'){ if(window.REC&&REC.enabled) REC.onPong(m); else state.linkMs=Date.now()-state.lastPingAt; LOG.rxRate('pong','pong RTT', state.linkMs+'ms'); }
  else if(m.type==='ack'){ if(window.REC&&REC.enabled) REC.cmdAck(m.c_id, m.ok); LOG.rxRate('ack','cmd ack', m.name, m.ok); }
  // THE ALARM FRAME IS THE ONE MESSAGE THAT CANNOT BE DROPPED. Matching the single
  // name 'leak' silently discarded BOTH of the names the vehicle actually sends now
  // (api/rov.py leak_alarm_edges -> leak_warn / leak_flood), and with them the latch
  // that keeps a flood on screen after the water takes the tether down with it. Names
  // are looked up in a TABLE, so adding a stage on the vehicle cannot go unnoticed
  // here as a silently unmatched string - it lands in the `else` and gets logged.
  else if(m.type==='alarm' && LEAK_ALARM_STAGE[m.name]){
    const stage=LEAK_ALARM_STAGE[m.name];
    LOG.warn('ALARM:', m.name, '- latching', stage);
    latchLeakAlarm(stage);
  }
  else LOG.rx('msg', m);
}
function onTelemetry(t){
  state.realTel=t; state.realTelAt=Date.now();
  // Sync sim mirror so a dropout continues smoothly from real values.
  if(typeof t.ballast_level==='number') state.ballastLevel=t.ballast_level;
  if(typeof t.ballast_target==='number') state.ballastTarget=t.ballast_target;
  // BALLAST: the level is now step-count truth, and a stepper that has never been
  // homed does not know where it is. `null` is that answer and must survive the
  // journey to the glyph — the old `typeof === number` guard alone would silently
  // leave the last good level on screen, which is the one presentation that lets an
  // unhomed tank look like a known one. `homed:false` disqualifies a number too.
  //
  // AND SO DOES needs_rehome, for the same reason and not a weaker one. The FULL limit
  // switch closing at the wrong count (api/hardware.py mark_full_limit) means steps
  // were lost: the axis is against known metal, so the hardware leaves homed TRUE and
  // keeps publishing a level — but the count no longer maps to a real plunger position
  // anywhere except at that one stop. A number that is not a position is exactly
  // cannot-tell, so it renders as unknown until HOME re-references it. Checking only
  // `homed` meant this branch could never fire on the one path that most needs it.
  if(typeof t.ballast_homed==='boolean') state.ballastHomed=t.ballast_homed;
  if(typeof t.ballast_needs_rehome==='boolean') state.ballastRehome=t.ballast_needs_rehome;
  if(t.ballast_level!==undefined)
    state.ballastKnown = (typeof t.ballast_level==='number')
                      && state.ballastHomed!==false && state.ballastRehome!==true;
  // DEPTH, PRESSURE AND HEADING, AND THE SENSOR THAT STOPPED.
  //
  // The dive this exists for: the MS5837 died at 4.33 m. Its driver caches, so
  // read_pressure() went on handing back the last good 20.85 psi for the rest of the
  // dive and the vehicle turned that into depth=4.33 in every frame at 15 Hz. The sub
  // descended to 8 m; the console showed a confident, fully-coloured 4.3 m. Nothing
  // here could have caught it, because the old guard asked `typeof === 'number'` and a
  // FROZEN NUMBER IS STILL A NUMBER. Two review passes reasoned about sensors that
  // never answered; none reasoned about one that answered and then stopped.
  //
  // So null now travels all the way to the glyph. It is not zero, not the last value
  // and not a missing field: it is the vehicle saying "the chip behind this reading is
  // not answering right now", which the project's central rule renders as cannot-tell.
  //
  // The stamps stay, but ONLY A REAL NUMBER WRITES THEM. They used to be written on
  // every arriving frame, which turned state.depthAt into a measure of the link rather
  // than of the sensor - the frozen depth sailed through the freshness gate at 15 Hz,
  // and the gate is what painted it a full depth-band colour. render.js now asks about
  // the VALUE first and these second, where they answer the one question the value
  // cannot: whether an older vehicle has quietly dropped the field entirely.
  if(t.depth!==undefined){
    state.depth = (typeof t.depth==='number') ? t.depth : null;
    if(state.depth!=null) state.depthAt=Date.now();
  }
  if(t.pressure!==undefined){
    state.pressure = (typeof t.pressure==='number') ? t.pressure : null;
    if(state.pressure!=null) state.pressureAt=Date.now();
  }
  // Heading is whatever the SUB reports, always — INCLUDING null. The line that stood
  // here said an unfitted compass simply leaves the bearing where it was, "and that is
  // the truth, not something to paper over". It is not the truth: a bearing that has
  // stopped moving is pixel-for-pixel a sub holding course, and a BNO085 that dies
  // freezes _c_heading and _c_mag_cal together, so the stuck bearing shipped wearing
  // mag_cal 3 — the mark that means "compass calibrated and in use" — and the
  // heading-up radar kept turning the whole map on it.
  if(t.heading!==undefined){
    state.heading = (typeof t.heading==='number') ? t.heading : null;
    if(state.heading!=null) state.headingAt=Date.now();
  }
  // WHICH CHIPS ARE FAULTED. api/hardware.py has always known (sensor_faults()) and
  // nothing ever carried it up the tether, so the best the console could do was blank
  // a number with no reason attached — which reads as a dashboard bug, not as a cable
  // to go and check. The list only ever NAMES the cause: a reading goes cannot-tell on
  // its own null, so a vehicle too old to send this still refuses to show a dead number.
  if(t.sensor_faults!==undefined) state.sensorFaults = normalizeFaults(t.sensor_faults);
  else if(t.faults!==undefined)   state.sensorFaults = normalizeFaults(t.faults);
  // THE PACK IS A SENSED READING, and it was the last one on this bar still being
  // treated as a fact of nature. It is measured by the INA219, the INA219 can stop,
  // and both ways this guard could be wrong were being taken at once: a `null` fell
  // through the typeof test and left the PREVIOUS voltage on screen wearing its band
  // colour, and before that the vehicle covered for the same hole by shipping 0.0 V,
  // which the console dutifully painted red and captioned SURFACE — a critical alarm
  // invented whole by an absent sensor. Same shape as depth and heading now: the null
  // travels to the glyph, and only a real number writes the stamp.
  if(t.battery_v!==undefined){
    state.batteryV = (typeof t.battery_v==='number') ? t.battery_v : null;
    if(state.batteryV!=null) state.batteryAt=Date.now();
  }
  if(typeof t.cpu_c==='number') state.cpuC=t.cpu_c;
  if(typeof t.ram_pct==='number') state.ramPct=t.ram_pct;
  if(typeof t.disk_gb==='number') state.diskGb=t.disk_gb;
  if(typeof t.left==='number') state.left=t.left;
  if(typeof t.right==='number') state.right=t.right;
  if(typeof t.armed==='boolean') state.armed=t.armed;
  if(typeof t.magnet==='boolean') state.magnet=t.magnet;
  if(typeof t.light_green==='boolean') state.lights.green.on=t.light_green;
  if(typeof t.light_white==='boolean') state.lights.white.on=t.light_white;
  // LEAK, in stages. `leak` (bool) is true for WARN *or* FLOOD, so on its own it
  // cannot say which — a vehicle that only sends the bool is read as FLOOD, because
  // over-warning costs a cancelled dive and under-warning costs the sub.
  if(typeof t.leak_state==='string') state.leakState=t.leak_state;
  else if(typeof t.leak==='boolean') state.leakState = t.leak ? 'FLOOD' : 'NORMAL';
  // ONE PIECE OF EVIDENCE RETIRES A LATCHED ALARM, whatever stage it latched: the
  // vehicle itself saying its probes are dry again. A WARN latch is no stickier than a
  // FLOOD one — it is a weaker claim, not a longer-lived one — so both clear here.
  if(t.leak===false || t.leak_state==='NORMAL') state.alarmLeakStage='NORMAL';
  // AND TELEMETRY RAISES IT, not only an alarm frame. The alarm is an EDGE — the
  // vehicle announces the transition — so a console that was not attached when the
  // probe went wet holds no latch at all: a second tab, a client that reconnected, a
  // handheld picked up mid-dive. That console then meets the exact failure the latch
  // was built for. The water takes the tether down, telemetry goes stale ~3 s later,
  // the readout falls back to the simulator and repaints the green "both probes dry"
  // drop over a hull that is filling. A FLOOD standing in telemetry is the same fact as
  // the alarm that announced it, arriving continuously instead of once, so it latches
  // on identical terms — and the clearing rule above is untouched: only the vehicle
  // reporting NORMAL lets go.
  if(state.leakState==='WARN' || state.leakState==='FLOOD') latchLeakAlarm(state.leakState);
  // A DEAD PROBE READS DRY FOREVER, which is the one failure this design otherwise
  // hides, so the vehicle's own open/shorted verdict is carried rather than dropped.
  if(t.leak_probe_fault!==undefined)
    state.leakProbeFault = (typeof t.leak_probe_fault==='string') ? t.leak_probe_fault : null;
  // SPEED. null is the paddlewheel saying it cannot tell (stalled below ~0.1 m/s,
  // stale, or not fitted) and must NOT be turned into a zero: "stopped" and "no
  // idea" are different facts, and the second one beside high throttle is a snag.
  if(t.speed_ms!==undefined) state.speedMs = (typeof t.speed_ms==='number') ? t.speed_ms : null;
  if(t.speed_src!==undefined) state.speedSrc = (typeof t.speed_src==='string') ? t.speed_src : null;
  // SNAG AND GYRO-ONLY, IN THREE STATES. These are NAVIGATION's answers, not the
  // hardware's, and api/main.py fill_nav_fields now nulls them the instant nav cannot
  // speak for this hull — not started, between dives, bus down, loop dead, or the nav
  // call raising inside the telemetry loop.
  //
  // `typeof === 'boolean'` dropped that null on the floor, and dropping it was not
  // neutral, because what stayed behind was the last thing nav ever said. A SNAG
  // ALARM RAISED AND THEN ORPHANED BY THE ESTIMATOR DYING STAYED ON SCREEN FOR THE
  // REST OF THE SESSION AND COULD NOT BE CLEARED BY ANYTHING: the only wire value
  // that ever cleared it was `false`, and a dead nav service never sends false again.
  // The mirror image is just as bad — nav going quiet with no snag standing silently
  // put the console back to its two most reassuring answers, so a subsystem's death
  // read as good news.
  //
  // So all three claims survive: true (pinned), false (nav looked, it is fine), null
  // (nav cannot tell). render.js decides what each one looks like; this only refuses
  // to throw one of them away.
  if(t.snagged!==undefined)   state.snagged  = (typeof t.snagged==='boolean')   ? t.snagged   : null;
  if(t.gyro_only!==undefined) state.gyroOnly = (typeof t.gyro_only==='boolean') ? t.gyro_only : null;
  // WHAT NAV LAST COMMITTED TO, so that a null can be shown as "this alarm can no
  // longer be confirmed" rather than as silence. Only a definite answer writes here;
  // a null deliberately leaves both alone, which is the whole point of keeping them.
  if(state.snagged===true){ state.navAnswered=true; state.snagStood=true; }
  else if(state.snagged===false){ state.navAnswered=true; state.snagStood=false; }
  if(state.gyroOnly===true || state.gyroOnly===false) state.navAnswered=true;
  // mag_cal: 0..3, and null means no IMU answered at all — a different fault from an
  // IMU answering "uncalibrated" (0), so the null is kept rather than folded into 0.
  if(t.mag_cal!==undefined) state.magCal = (typeof t.mag_cal==='number') ? t.mag_cal : null;
  // THE SECONDARY INSTRUMENTS, OFF THE ONE LIST THAT DESCRIBES THEM (core.js
  // FLIGHT_METRICS): the pack's amps and the four inertial readings.
  //
  // Written as a loop rather than five hand-copied lines ON PURPOSE, and the reason is
  // this file's own history. The guard below is the fourth version of a line that has
  // been got wrong twice — `typeof x === 'number'` dropping a null on the floor, and a
  // `||` collapsing a legitimate zero — and hand-copying it five more times is five more
  // chances to write one of them differently. Every one of these fields has a REAL ZERO
  // and every one of those zeroes is the calm answer: 0.0 deg/s is "not turning", 0.00
  // m/s2 is "coasting", 0.0 deg is "level", 0.0 A is "drawing nothing". `x || null` on
  // any of them spells a perfectly good measurement "the chip is dead", and the same
  // mistake written the other way round (`x == null ? 0 : x`) hands the console a dead
  // IMU dressed as a sub sitting still and level — the calm answer again, from a chip
  // that answered nothing. One guard, five metrics, no room for the two to disagree.
  //
  // AND ONLY A REAL NUMBER WRITES THE STAMP, the same rule depth, heading and the pack
  // follow: the stamp says "a measurement arrived", never "a frame arrived", because a
  // frozen driver ships frames at 15 Hz while measuring nothing.
  for(let i=0;i<FLIGHT_METRICS.length;i++){
    const m=FLIGHT_METRICS[i];
    if(t[m.wire]===undefined) continue;      // an older hull that has never sent it
    // ABSENT IS NOT NULL, and the alert rail has to tell them apart: a hull too old to
    // carry this field has no instrument to have lost, while a hull that sends null has
    // one and it stopped. Both leave the value null, so the fact that the field was ever
    // spoken is recorded here - otherwise an older vehicle gets accused of a failure it
    // does not have the hardware to suffer.
    state[m.key+'Seen'] = true;
    state[m.key] = (typeof t[m.wire]==='number') ? t[m.wire] : null;
    if(state[m.key]!=null) state[m.key+'At']=Date.now();
  }
}
// Fixed-rate send loop — ALWAYS transmits, even zeros (feeds server watchdog).
function startSendLoop(){
  setInterval(()=>{
    const i=state.input;
    send({type:'control', throttle:+i.throttle.toFixed(3), steer:+i.steer.toFixed(3)});
    send({type:'camera',  pan:+i.pan.toFixed(3),   tilt:+i.tilt.toFixed(3)});
    send({type:'ballast', cmd:i.ballast});
    LOG.txRate('ctl','control/camera/ballast', 'thr='+i.throttle.toFixed(2), 'str='+i.steer.toFixed(2),
               'pan='+i.pan.toFixed(2), 'tilt='+i.tilt.toFixed(2), 'ballast='+i.ballast, 'ws='+state.wsStatus);
  }, Math.round(1000/CONFIG.sendRateHz));
}
function startPingLoop(){
  // §2 SNTP: carry the client monotonic send time (t1) so the pong can complete the exchange.
  setInterval(()=>{ state.lastPingAt=Date.now(); send({type:'ping', t1:(window.REC?REC.mono():performance.now())}); }, CONFIG.pingIntervalMs);
}
// Push dirty light-brightness levels at a modest rate (avoids flooding).
function startLevelLoop(){
  setInterval(()=>{
    if(state.levelDirty.green){ state.levelDirty.green=false; send({type:'command', name:'light_green_level', value:+state.lights.green.level.toFixed(2)}); }
    if(state.levelDirty.white){ state.levelDirty.white=false; send({type:'command', name:'light_white_level', value:+state.lights.white.level.toFixed(2)}); }
  }, CONFIG.levelSendMs);
}

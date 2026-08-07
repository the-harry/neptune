"use strict";
/* ============================================================================
   CORE — DOM helpers, math helpers, the LOG bus, the live state object, and
   host/URL resolution. Loaded right after config.js; everything else builds
   on the globals defined here.
   ============================================================================ */

/* ---- small helpers ---- */
const $ = id => document.getElementById(id);
const clamp = (v,a,b) => v<a?a:(v>b?b:v);
const HEADINGS = ['N','NE','E','SE','S','SW','W','NW'];
function headingCardinal(deg){ return HEADINGS[Math.round(((deg%360)/45))%8]; }
function vibrate(ms){ try{ navigator.vibrate && navigator.vibrate(ms); }catch(e){} }

/* Flat-earth local (east,north metres) <-> lat/lon — mirrors api/nav/geo.py.
   Exact enough at pond/canal scale; used to place the sub/track over imagery. */
const EARTH_R = 6378137.0;
function toLatLon(x, y, lat0, lon0){
  const lat = lat0 + (y / EARTH_R) * 180 / Math.PI;
  const lon = lon0 + (x / (EARTH_R * Math.cos(lat0*Math.PI/180))) * 180 / Math.PI;
  return { lat, lon };
}
function toLocal(lat, lon, lat0, lon0){
  const y = (lat - lat0)*Math.PI/180 * EARTH_R;
  const x = (lon - lon0)*Math.PI/180 * EARTH_R * Math.cos(lat0*Math.PI/180);
  return { x, y };
}

/* ============================================================================
   LOG — console debug bus. Everything of interest is logged here. High-rate
   streams (control/camera TX, telemetry RX) are throttled to 1/s by default.
   Runtime toggles:  NEPTUNE.log(false) · NEPTUNE.logRate(true) · NEPTUNE.state
   ============================================================================ */
/* The log is a BUS, not a console call.

   A submarine fault is diagnosed after the fact, from whatever was recorded while
   it was happening — and on this vehicle the operator cannot leave the console
   mid-dive to go and read a file. So every line goes three places at once:

     console   for a dev machine with devtools open
     ring      an in-memory scrollback the LOGS overlay tails live
     sinks     the on-disk session log, so it survives the machine dying

   Levels exist so the overlay can filter: ok / info / warn / err. Anything that is
   sent, received, attempted or refused should end up here — the point is that a
   question about what the vehicle did five minutes ago is answerable without
   having prepared for it. */
const LOG = (function(){
  let enabled=true, highRate=false;
  const t0=Date.now();
  const ts=()=>((Date.now()-t0)/1000).toFixed(2)+'s';
  const last={};
  const ring=[];
  const sinks=[];
  let seq=0;
  const MAX = 4000;                         // ~ a long dive; bounded so memory cannot run away

  function fmt(a){
    if(a instanceof Error) return a.message || String(a);
    if(typeof a === 'string') return a;
    try{ return JSON.stringify(a); }catch(e){ return String(a); }
  }
  function emit(tag, level, color, args){
    const line = { i:++seq, t:Date.now(), rel:(Date.now()-t0)/1000, tag:tag.trim(), level:level,
                   msg:args.map(fmt).join(' ') };
    ring.push(line);
    if(ring.length > MAX) ring.splice(0, ring.length - MAX);
    for(let i=0;i<sinks.length;i++){ try{ sinks[i](line); }catch(e){} }
    if(!enabled) return;
    try{ console.log('%c'+tag+'%c '+ts(), 'color:'+color+';font-weight:bold', 'color:#7a8a8f', ...args); }catch(e){}
  }
  function base(tag,color,args,level){ emit(tag, level||'info', color, args); }
  /* Rate-limited categories are the high-frequency ones: control frames at 20 Hz
     would be 200 s of scrollback in a 4000-line ring, evicting everything that
     explains how the dive got there. So they are coalesced rather than dropped
     silently, and the suppressed count is carried on the next line that does get
     through - dmesg's "message repeated N times", for the same reason. */
  function throttled(key,ms,tag,color,args){
    if(highRate){ emit(tag,'info',color,args); return; }
    const n=Date.now();
    const st = last[key] || (last[key] = {t:0, n:0, ms:ms, tag:tag, color:color});
    st.ms=ms; st.tag=tag; st.color=color;
    if(n - st.t >= ms){
      const extra = st.n;
      st.t = n; st.n = 0;
      emit(tag,'info',color, extra ? args.concat(['(+'+extra+' more)']) : args);
    } else {
      st.n++;
    }
  }
  /* A burst that STOPS would otherwise take its suppressed count with it: the
     count is only reported by the next line to get through, and if the stream
     goes quiet there is no next line. That loses exactly the interesting case -
     "telemetry was flowing, then it wasn't". Sweep the tail out on a timer. */
  setInterval(function(){
    const n = Date.now();
    for(const k in last){
      const st = last[k];
      if(st.n > 0 && (n - st.t) >= (st.ms || 1000)){
        const extra = st.n;
        st.t = n; st.n = 0;
        emit(st.tag, 'info', st.color, ['(+'+extra+' more, then quiet)']);
      }
    }
  }, 1000);
  return {
    net:  (...a)=>base('[NET] ','#b46bff',a),
    tx:   (...a)=>base('[TX]  ','#c99bff',a),
    rx:   (...a)=>base('[RX]  ','#4dffa6',a),
    txRate:(k,...a)=>throttled('tx:'+k,1000,'[TX~] ','#9a4dff',a),
    rxRate:(k,...a)=>throttled('rx:'+k,1000,'[RX~] ','#4dffa6',a),
    cmd:  (...a)=>base('[CMD] ','#ff5bd0',a),
    input:(...a)=>base('[IN]  ','#ff9be0',a),
    map:  (...a)=>base('[MAP] ','#ff5bd0',a),
    state:(...a)=>base('[STATE]','#b9a9d6',a),
    ok:   (...a)=>base('[OK]  ','#4dffa6',a,'ok'),
    warn: (...a)=>base('[WARN]','#ff8c1a',a,'warn'),
    err:  (...a)=>base('[ERR] ','#ff5c7a',a,'err'),
    // ---- consumers ----
    ring: ()=>ring,
    subscribe:(fn)=>{ sinks.push(fn); return ()=>{ const i=sinks.indexOf(fn); if(i>=0) sinks.splice(i,1); }; },
    setEnabled:(v)=>{ enabled=!!v; try{console.log('%c[NEPTUNE] console logging '+(enabled?'ON':'OFF'),'color:#b46bff');}catch(e){} },
    setHighRate:(v)=>{ highRate=!!v; try{console.log('%c[NEPTUNE] high-rate logging '+(highRate?'ON':'OFF'),'color:#b46bff');}catch(e){} }
  };
})();

/* ============================================================================
   THE SECONDARY INSTRUMENTS — ONE ENTRY PER READING, AND THE ENTRY *IS* THE READING

   Five numbers the vehicle has been sending every frame with nothing on this console
   drawing them: the turn rate, forward acceleration and the attitude pair off the
   BNO085, and the pack current off the INA219 — which was worse than orphaned, because
   it WAS rendered, inside the pack's tooltip ("drawing 3.1 A"), where an operator on a
   canal bank in sunlight with wet hands is never going to hover it. A reading that only
   exists on hover is a reading that does not exist.

   THEY ARE A TABLE BECAUSE THEY ARE GOING TO BE PRUNED. The operator has said out loud
   that they will fly with all five and then cut back to whichever two earn their room,
   and the way that goes wrong is the way it has gone wrong here four rounds running: a
   metric is added as markup in one file, ingest in a second and a renderer in a third,
   and removing it six weeks later means finding all three. So each entry carries
   EVERYTHING about its metric — the wire name it arrives under, the state field it
   lands in, the element it draws into, the group it sits in, the chip behind it, how it
   formats, what the bench model does about it, and the sentence explaining what it
   MEANS. net.js ingests off this list, render.js builds the markup off it and paints
   off it. Deleting a metric is deleting one entry; adding one is adding one.

   THE SENTENCE LIVES HERE RATHER THAN IN THE HTML, which is a deliberate departure from
   this console's usual rule that index.html owns the wording. The markup is generated,
   so wording left in the HTML would be exactly the loose end the table exists to
   prevent: a tile deleted from the list and a paragraph about it left behind. The
   builder writes it into data-help, which is the same place captureHelp() would have
   put it, so liveTitle() and every test that reads a written explanation are unaffected.

     key    the state field, and `key+'At'` is its "a real number last arrived" stamp
     wire   the protocol.Telemetry field name
     id     the element the number is drawn into (a leaf span, like every other m-val)
     kind   which SENSOR_BEHIND entry names the chip, so a blank can say WHICH cable
     what   the SHOUTED name used in the cannot-tell sentence
     fmt    a live value -> the rendered string. Never called with null.
     sim    what the BENCH MODEL can honestly say, or absent when it can say nothing
   ============================================================================ */
/* A signed reading, with -0.0 refused. `(-0.04).toFixed(1)` is the string "-0.0", which
   reads as "turning left, very slightly" about a number that rounded to nothing at all;
   the sign is meaningful on all four of these, so a sign that is pure rounding artefact
   has to go. Zero is written unsigned because it is not a direction. */
function signedFixed(v, dp){
  const r = +v.toFixed(dp);
  const z = Object.is(r, -0) ? 0 : r;
  return (z > 0 ? '+' : '') + z.toFixed(dp);
}
const FLIGHT_METRICS = [
  { key:'currentA', wire:'current_a', id:'current-a', label:'Draw', group:'pack',
    kind:'current', what:'PACK CURRENT',
    fmt:v=>v.toFixed(1)+' A',
    // The bench has no shunt, but it does have thrusters and lamps, so it can say what
    // its own model is drawing rather than sitting at a permanent question mark beside a
    // pack that is visibly sagging. Same standing as every other simulated number on the
    // screen, and the SIM presentation qualifies all of them at once. The knobs are read
    // through defaults so config.js can adopt them later without this line changing.
    sim:(s)=>{
      const B = (typeof CONFIG!=='undefined' && CONFIG.sim) || {};
      const thrust = (Math.abs(s.left||0) + Math.abs(s.right||0)) / 2;
      const lamps  = (s.lights.green.on ? s.lights.green.level : 0)
                   + (s.lights.white.on ? s.lights.white.level : 0);
      return (B.idleAmps||0.35) + thrust*(B.thrustAmps||2.6) + lamps*(B.lampAmps||0.5);
    },
    help:'PACK CURRENT - how many amps the whole sub is pulling out of the battery right '
       + 'now, measured by the same pack monitor as the voltage beside it. It is what '
       + 'turns "the pack is sagging" into "the pack is sagging BECAUSE both thrusters '
       + 'are at full": read it against the throttle, and a draw that stays high with the '
       + 'thrusters idle is something jammed, fouled or shorted. 0.0 A is the MEASUREMENT '
       + 'that nothing is being drawn, which is what a sub floating with its lamps off '
       + 'actually reads. A QUESTION MARK means the pack monitor has stopped answering, '
       + 'so nothing is measuring the draw - and the voltage beside it will be a question '
       + 'mark too, because it is one chip. That is not the same as the dashes of a '
       + 'dropped frame, which come back on their own.' },
  { key:'turnRate', wire:'gyro_z_dps', id:'turn-val', label:'Turn', group:'attitude',
    kind:'imu', what:'TURN RATE',
    fmt:v=>signedFixed(v,1)+' °/s',
    // The model turns at exactly steer x headingRatePerS - that is the line simulate()
    // applies to the heading - so reporting it is the model quoting itself, not the
    // console inventing an instrument.
    sim:(s)=>(s.input.steer||0) * ((CONFIG.sim && CONFIG.sim.headingRatePerS) || 40),
    help:'TURN RATE - how fast the sub is swinging round, in degrees per second, from the '
       + 'spin sensor inside the compass module. PLUS is clockwise (bow going right), '
       + 'minus is anticlockwise. 0.0 is the MEASUREMENT that it is not turning, which is '
       + 'what holding a straight course looks like - so a question mark here is a very '
       + 'different thing from a zero. Read it when the bearing looks wrong: a steady '
       + 'bearing with a turn rate on it is a compass that has frozen, and a moving '
       + 'bearing with no turn rate under it is a compass being pulled round by the '
       + 'thrusters rather than by the sub actually turning.' },
  { key:'surge', wire:'accel_fwd_ms2', id:'accel-val', label:'Surge', group:'attitude',
    kind:'imu', what:'FORWARD ACCELERATION',
    fmt:v=>signedFixed(v,2)+' m/s²',
    // GIVE THE BENCH SUB SOME MASS, rather than differentiating a speed that has none.
    // The model's speed is `throttle x maxSpeed` with no lag at all, so its derivative is
    // an impulse: a keyboard throttle going 0 to 1 in one 16 ms frame differentiates to
    // 60 m/s2, which is a number no hull in water has ever produced and reads as a broken
    // instrument. So the model tracks a LAGGED speed - a hull that takes about a second
    // to get going - and reports the acceleration that lag implies, which is bounded by
    // the lag itself and settles back to 0.00 the moment the speed is steady. The working
    // value rides on `state` so the whole model of this metric stays inside its entry.
    sim:(s,dt)=>{
      const tau = (CONFIG.sim && CONFIG.sim.surgeTauS) || 0.8;
      const lag = (s._simSpeedLag==null) ? (s.speedMs||0) : s._simSpeedLag;
      const a   = ((s.speedMs||0) - lag) / tau;
      s._simSpeedLag = lag + a*Math.min(dt, tau);
      return a;
    },
    help:'FORWARD ACCELERATION - how hard the sub is gaining or losing speed along its '
       + 'own nose-to-tail line, in metres per second squared, from the accelerometer in '
       + 'the compass module. PLUS is picking up speed ahead, minus is slowing or being '
       + 'pushed backwards. 0.00 is the MEASUREMENT that it is coasting at a steady speed, '
       + 'which is what most of a transit looks like. Read it beside SPEED: thrust on, '
       + 'speed flat and nothing at all here is a sub that never accelerated, which is a '
       + 'sub being held by something.' },
  { key:'pitchDeg', wire:'pitch_deg', id:'pitch-val', label:'Pitch', group:'attitude',
    kind:'imu', what:'PITCH',
    fmt:v=>signedFixed(v,1)+'°',
    help:'PITCH - how far the sub’s nose is above or below level, in degrees, from the '
       + 'attitude sensor in the compass module. PLUS is nose UP. 0.0 is the MEASUREMENT '
       + 'that it is sitting level. Nose-down while the tank is filling is the sub diving '
       + 'and is normal; a pitch that will not come back to level once the ballast has '
       + 'settled is weight that has shifted inside the hull, or a tether pulling the '
       + 'tail. Note what it reads on a trimmed hull at the start of a dive - the number '
       + 'is only worth anything against that.' },
  { key:'rollDeg', wire:'roll_deg', id:'roll-val', label:'Roll', group:'attitude',
    kind:'imu', what:'ROLL',
    fmt:v=>signedFixed(v,1)+'°',
    help:'ROLL - how far the sub is leaning to one side, in degrees, from the attitude '
       + 'sensor in the compass module. PLUS is starboard (right) side down. 0.0 is the '
       + 'MEASUREMENT that it is sitting level. A hull heels into a turn and comes back; '
       + 'a lean that stays put with the thrusters idle is weight shifted inside, a caught '
       + 'tether, or one thruster no longer pushing. It also says how much the camera is '
       + 'tilted, which is why a picture can look wrong while nothing is wrong.' }
];
/* WHERE EACH GROUP MOUNTS, and whether it can be folded away.

     after   the tiles become DIRECT SIBLINGS of this element. The top bar is a flex row
             with space-between doing the spacing, so a tile wrapped in a container would
             be one flex item holding two readings and the bar's even gaps would go with
             it. The pack's amps belong beside the pack's volts and nowhere else: one
             chip, one question, and a voltage that sags without a draw beside it cannot
             be told from a pack that is simply flat.
     into    the tiles are appended INSIDE this element, under a heading that folds.
             ATTITUDE goes with the driving instruments by the minimap rather than into
             the top bar, because that is where Speed already lives and these are read
             the same way Speed is: while flying, with the eye already down there. It
             costs the top bar nothing, which matters - the bar is one nowrap row and
             adding four tiles to it is how it starts overlapping itself again.

   FOLDING NEVER HIDES AN ADMISSION. The head carries the group's own cannot-tell mark,
   so a folded ATTITUDE with a dead IMU under it still says so on the line the operator
   can see. */
const HUD_GROUPS = [
  { id:'pack', after:'pack-tile' },
  { id:'attitude', into:'flight-cluster', label:'ATTITUDE',
    title:'ATTITUDE AND RATES - how the sub is sitting and how it is moving, all four '
        + 'read off the same compass module as the bearing: how fast it is swinging '
        + 'round, how hard it is gaining speed, and how far it is tipped nose-up and '
        + 'side-down. Advisory instruments - nothing here flies the sub - so tap this '
        + 'heading to fold them away when the screen is busy. If any of them stops being '
        + 'measured, this line says so whether the group is folded or not.' }
];

/* ============================================================================
   STATE — the single live state object the whole app reads/writes.
   ============================================================================ */
const state = {
  host:'', httpBase:'', wsBase:'',
  ws:null, wsStatus:'offline', /* offline | connecting | online */
  linkMs:null, lastPingAt:0,
  reconnectDelay:CONFIG.reconnect.baseMs, reconnectTimer:null,
  video:'idle', /* idle | connecting | live | nofeed | reconfiguring */
  pc:null, sigWs:null, videoRetryTimer:null,   // WebRTC peer + go2rtc signaling socket
  // ---- WOLFANG camera control plane ----
  cam:{ battery:null, recording:false, recordRaw:'', mode:'', sd:'', warning:'',
        remaining:null, isStreaming:'', degraded:false, menu:[],
        videoRes:'', awb:'', imageRes:'', ev:'' },   // live camera settings
  camWs:null, surfaced:false,   // surfaced = config/file ops unlocked (§7.4 gate)
  screenRec:{ active:false, file:'' },   // handheld screen recording (launcher + ffmpeg)
  // Per-subsystem liveness stamps (§3). Each subsystem proves itself independently,
  // so one going quiet greys only its own controls. See status.js.
  camOkAt:0,                    // last successful camera control-plane response
  navOkAt:0,                    // last nav telemetry frame
  sys:null, sysAt:0,            // last /api/system snapshot (real Pi health)
  camAp:null,                   // launcher /__wifi: can THIS handheld see the camera's AP
  piProbe:null,                 // /api/healthz: does the sub ANSWER, control link aside
  net:null,                     // launcher /__net: this handheld's own radios and cables
  wsOpenAt:0,                   // when the control socket opened - a socket open and silent
                                // for too long is a different claim from one still connecting
  // WHEN a NUMBER for each measured quantity last actually arrived. Not when telemetry
  // arrived - a frame with no `depth` field leaves state.depth holding its last value,
  // which on a sub with no depth sensor is a number from the simulator. These stamps
  // are what let the readouts colour themselves from a sensor or not at all.
  //
  // READ THE WORD *NUMBER* ABOVE. These used to be stamped on every arriving frame,
  // which made them a measure of the LINK and not of the sensor - so an MS5837 that
  // died mid-dive, whose driver hands back its last cached reading forever, kept these
  // stamps perfectly fresh at 15 Hz while measuring nothing. net.js now stamps them
  // only when a real number arrives, and the readouts gate on the VALUE first (see
  // viewFromState): a null depth is cannot-tell no matter how new the frame is.
  //
  // batteryAt joined them because the PACK IS A SENSED READING TOO and was the last one
  // still being treated as a fact of nature. The INA219 measures it, the INA219 can
  // stop, and until this round a vehicle that dropped `battery_v` left the previous
  // voltage sitting on the bar wearing its full band colour - the frozen-MS5837 failure
  // again, on the one gauge whose colour tells the operator to surface.
  depthAt:0, pressureAt:0, headingAt:0, batteryAt:0,
  source:'keyboard', /* keyboard | gamepad */
  gamepadIndex:null,
  keys:new Set(), padPrev:{},
  bindings:{},                 // action -> [ {type:'pad',index} | {type:'key',code} ]
  actionPrev:{},               // per-action held state (edge detection)
  learn:{ active:false, action:null, padBaseline:{} },
  mapperOpen:false,
  input:{ throttle:0, steer:0, pan:0, tilt:0, ballast:'hold' },
  ballastTargetRaw:0,         // what the operator set (arrows/drag) — may jump
  ballastTargetCmd:0,         // slews toward Raw at CONFIG.ballastSlewPerS → smooth API commands.
                              //   starts at SURFACE (empty); also the shutdown state.
  lastLight:'green',
  lights:{ green:{on:false, level:0}, white:{on:false, level:0} },   // start OFF; toggling on jumps to lightOnDefault
  levelDirty:{ green:false, white:false },
  magnet:false, armed:true,
  ballastLevel:0, ballastTarget:0,
  // BALLAST TRUTH. The syringe is an open-loop stepper with no position sensor, so
  // from power-on until it has been driven onto the EMPTY limit switch its position
  // is not 0 and not 50% - it is genuinely unknown, and any number drawn there is
  // invented. These start TRUE because the SIMULATOR's tank is a modelled quantity
  // that really is known; only a real vehicle reporting ballast_level: null (or
  // ballast_homed: false) can take that away.
  ballastKnown:true, ballastHomed:true, ballastRehome:false,
  // These three are numbers here because the SIMULATOR owns them until a hull does.
  // From a hull any of them may arrive as null, and null means "the chip behind this
  // has stopped answering" - not zero, not the last one. See net.js.
  depth:1.28, pressure:14.7, heading:284,
  // 2S Li-ion: 8.4 V charged, 7.4 V nominal, 6.0 V floor. The old 24.8 V start was
  // a number from a pack this sub does not have, and it silently made every battery
  // threshold on the console wrong while looking entirely plausible.
  batteryV:8.3,
  cpuC:null, ramPct:null, diskGb:null,   // Pi system metrics (from telemetry)
  left:0, right:0,
  leak:false, simLeak:false,
  // THE LATCHED ALARM CARRIES ITS STAGE. It used to be a bool, which could only ever
  // mean FLOOD - so a `leak_warn` frame had nowhere to land and the weaker warning
  // died with the socket it arrived on. 'NORMAL' | 'WARN' | 'FLOOD', raised by
  // latchLeakAlarm() and cleared only by the vehicle itself reporting NORMAL.
  alarmLeakStage:'NORMAL',
  // THE LEAK LADDER. Two probes 2 cm apart: the lower one wet = WARN (water is
  // collecting), the upper one wet = FLOOD. simLeakStage is the bench rehearsal of
  // the same ladder (the "Leak test (sim)" action), because WARN is the stage nobody
  // would ever see before it mattered.
  leakState:'NORMAL', simLeakStage:'NORMAL', leakProbeFault:null,
  // §5 readings that are allowed to say CANNOT-TELL. null is not 0 here: a null
  // speed means the paddlewheel is stalled, stale or not fitted, and a null mag_cal
  // means no IMU answered at all - which is a different fault from an IMU answering
  // "uncalibrated" (0), and the operator has to be able to tell them apart.
  speedMs:null, speedSrc:null,      // speedSrc: lut | paddle | kf-lut | kf-paddle
  // SNAG AND GYRO-ONLY ARE TRI-STATE, and that is a change of kind, not of degree.
  // They are NAVIGATION's answers (api/main.py fill_nav_fields), and navigation can
  // now say three different things: true = it looked and the sub is pinned, false =
  // it looked and it is not, null = IT CANNOT TELL - not started, between dives,
  // sensor bus down, loop dead. The two false answers are the REASSURING ones, so
  // reading a null as false makes a subsystem's death look like good news.
  // They start false because the SIMULATOR is genuinely not snagged and has no gyro
  // to coast on; only a hull can make them null.
  // currentA is NOT here any more, and neither are the four inertial readings: they are
  // seeded from FLIGHT_METRICS below, so that deleting a metric deletes its state field
  // with it instead of leaving an orphan nobody dares remove.
  snagged:false, gyroOnly:false, magCal:null,
  // WHAT NAVIGATION LAST *DEFINITELY* SAID, kept because a null must not be able to
  // clear a standing alarm in silence. navAnswered is false until nav commits to a
  // real true/false, so a hull that never had an estimator stays quiet instead of
  // wearing a permanent "nav is down" chip; snagStood remembers that the last
  // committed answer was a SNAG, so if nav goes quiet while the sub is pinned the
  // alert says the alarm can no longer be confirmed rather than just vanishing.
  // Both are reset on every fresh link (net.js onopen) - they describe THIS dive.
  navAnswered:false, snagStood:false,
  // WHICH CHIPS HAVE STOPPED ANSWERING, by the vehicle's own name for them
  // ("ms5837", "bno085", "ina219", "i2c"). The hull has always known this and nothing
  // carried it topside, so a blanked readout could never say WHY it was blank - and a
  // number that has gone missing with no cause given reads as a console bug, which is
  // the one reading of it that does not send anybody to check the wiring.
  sensorFaults:[],
  realTel:null, realTelAt:0,
  mode:'sim', /* sim | real | stale */
  surfaceUntil:0,
  surfaceComboStart:0, surfaceComboFired:false,   // gamepad SURFACE combo hold (both paddles)
  zoomArm:{},                 // paddle pressed alone → zooms the map on release (see input.js)
  lastFrame:0
};
/* THE SECONDARY INSTRUMENTS' STATE, seeded from the one list that describes them.
   Every one starts NULL, and null here is the same word it is everywhere else on this
   console: nothing has reported this yet. It is emphatically not 0 — 0.0 deg/s is "not
   turning", 0.00 m/s2 is "coasting", 0.0 deg is "level" and 0.0 A is "drawing nothing",
   and every one of those is the CALM answer. A console that started them at zero would
   spend the walk down to the water drawing a sub sitting perfectly still and perfectly
   level, off a vehicle that had not said a word. The `At` stamp is 0 for the same
   reason: it means "a real number has never arrived", which is what sensorFresh() is
   asked and how a hull that drops the field entirely is caught. */
FLIGHT_METRICS.forEach(m=>{ state[m.key]=null; state[m.key+'At']=0; });

/* TOOLTIPS THAT SURVIVE.

   Every glyph and number carries a written explanation, because for most people
   this dashboard is the first submarine control they have ever seen and there is
   nobody beside them to translate. But the renderers also want the title for LIVE
   state ("Video: live feed"), and whoever wrote last used to win — which quietly
   erased the explanation a few seconds after boot.

   So the explanation is captured once into data-help and the live text is appended
   to it. Both survive, and the HTML stays the single place the wording lives. */
function captureHelp(){
  document.querySelectorAll('[title]').forEach(el=>{
    if(!el.dataset.help) el.dataset.help = el.getAttribute('title') || '';
  });
}
function liveTitle(el, live){
  if(typeof el === 'string') el = $(el);
  if(!el) return;
  const help = el.dataset.help || '';
  el.title = help ? (help + (live ? '   —   ' + live : '')) : (live || '');
  if(help) el.setAttribute('aria-label', el.title);
}

/* Is a real vehicle on the other end of the link, right now?

   THE RULE: while one is, nothing on this console may be synthesised. A simulated
   position drawn over a real dive hides the failure it is most important to see —
   a dead thruster, a snagged tether, a sub pinned against a wall all look exactly
   like normal progress if the map keeps advancing on commanded throttle. The map
   moves on the SUB's output or it does not move at all. */
function vehicleLinked(){
  return !!(state.realTel && (Date.now()-state.realTelAt) < CONFIG.staleTimeoutMs);
}

/* Does the vehicle carry real navigation sensors, or is it a mocked hull?
   Telemetry carries `mock` (api/hardware.py `is_mock`): on a Pi with nothing wired
   yet RealHardware refuses to start and `auto` falls back to the bench simulator.
   Used to SAY SO on the radar — never to fill the gap in with a guess. */
function vehicleHasSensors(){ return !!(state.realTel && state.realTel.mock !== true); }

/* Is the vehicle's last word still worth believing?

   Deliberately LOOSER than vehicleLinked(). That one is the 1 s rule that forbids
   synthesising anything over a live dive. This is the question the cannot-tell
   readouts ask: "did a real hull tell us this, recently enough to still show it" —
   and its window is the same one main.js uses before handing back to the simulator,
   so a reading and the STALE badge that qualifies it always agree. Without it a
   vehicle that flooded and then dropped off the link would keep the console red
   forever, and every sim session that followed a real one would inherit the last
   dive's snag, mag fault and leak. */
function vehicleRecent(){
  return !!(state.realTel && (Date.now()-state.realTelAt) < (CONFIG.simFallbackMs || 3000));
}

/* THE LEAK, IN TWO STAGES (§5).

   Two probes: one on the floor of the hull, one 2 cm above it. Wet-low is WARN —
   condensation, a weeping gland, a splash down the tether — and the answer is to
   finish up and come home. Wet-high is FLOOD, and the answer is to surface now.
   Collapsing them into one boolean threw away the only distinction the operator
   can act differently on.

   WHY THERE IS A LATCH AT ALL: the flood that sinks the sub is also what shorts the
   tether, so the last thing this console ever hears about it is the alarm frame. With
   nothing holding that, telemetry goes stale ~3 s later, the readout falls back to the
   simulator and repaints the green "both probes dry" drop - a positive health claim
   about a hull that is filling with water. So an alarm LATCHES, and only the vehicle
   saying NORMAL again can retire it (net.js).

   The latch carries a STAGE, because the vehicle now raises two different alarms
   (api/rov.py leak_alarm_edges -> `leak_warn` / `leak_flood`) and they ask for
   opposite reactions. Only the legacy bare `leak` name carries no stage, and THAT one
   is still read as FLOOD: over-warning costs a cancelled dive, under-warning costs the
   sub.

   AND THERE IS A FOURTH STAGE, WHICH IS NOT ON THE LADDER AT ALL. "UNKNOWN" is the
   vehicle saying NOBODY IS SAMPLING THE PROBES (api/hardware.py LEAK_UNKNOWN: the leak
   GPIO or the sensor thread has stopped, so neither probe was read this tick). It used
   to be folded into NORMAL here, and folding it into NORMAL is the single worst thing
   this console can do with it: NORMAL is not the absence of a leak, it is a POSITIVE
   CLAIM about hull integrity — both probes were read, and both were dry — and it is the
   one reading on this vehicle that must never be a fallback. The whole server was
   rebuilt this round to stop making that claim on evidence nobody was collecting; the
   last layer went on making it anyway, painting the green struck-through drop over a
   hull nothing was watching.

   So UNKNOWN outranks NORMAL and is outranked by both wet stages, which is exactly
   api/hardware.py's own ordering (leak_state_from: wet outranks cannot-tell, because
   water that has already reached a probe is an established fact and the sampler dying
   afterwards does not un-establish it; only the REASSURANCE needs liveness). A latched
   WARN or FLOOD therefore still stands over it, and it can never talk the console down
   off a flood. */
const LEAK_RANK = {NORMAL:0, UNKNOWN:1, WARN:2, FLOOD:3};
/* Wire name -> the stage it latches. `leak` is a pre-two-stage Pi (older vehicle, same
   console): stageless, so it takes the worse reading. */
const LEAK_ALARM_STAGE = {leak_flood:'FLOOD', leak_warn:'WARN', leak:'FLOOD'};
function latchLeakAlarm(stage){
  // Raise only. A WARN arriving after a FLOOD must not talk the console down off the
  // flood - the water receding past one probe is not the emergency ending, and the
  // vehicle is not required to re-announce FLOOD for us to keep showing it.
  if((LEAK_RANK[stage]||0) > (LEAK_RANK[state.alarmLeakStage]||0)) state.alarmLeakStage=stage;
}
function leakStage(){
  const latched = state.alarmLeakStage || 'NORMAL';
  const s = vehicleRecent()
          ? state.leakState
          : (state.simLeakStage || (state.simLeak ? 'FLOOD' : 'NORMAL'));
  // NORMAL HAS TO BE SAID, not arrived at by elimination. Only the three stages this
  // console can positively account for pass as themselves; everything else — "UNKNOWN"
  // from a hull that has stopped sampling, a stage added to the vehicle after this
  // handheld was flashed, a frame with no stage at all — lands on UNKNOWN. That is the
  // same construction api/rov.py uses for the bool (`leaking = leak_state != "NORMAL"`,
  // with its "do not tidy this into `in (WARN, FLOOD)`" note): every stage nobody here
  // has heard of falls on the not-certified-dry side, which is the only direction it is
  // safe to be wrong about water.
  const live = (s==='FLOOD' || s==='WARN' || s==='NORMAL') ? s : 'UNKNOWN';
  // The WORSE of the two, never one instead of the other: a latched WARN has to
  // survive the link dropping, and it must never be able to sit on top of a FLOOD the
  // vehicle is reporting right now. UNKNOWN sits between NORMAL and WARN in that
  // ordering, so it outranks a clean hull nobody is checking and a latch outranks it.
  return (LEAK_RANK[live] >= LEAK_RANK[latched]) ? live : latched;
}

/* THE PACK, IN BANDS (§5). 2S Li-ion, and the ONLY thing allowed to colour the
   voltage: one colour, one meaning. A missing voltage returns no colour at all
   rather than a healthy green - an absent sensor must never read as a good pack. */
function batteryBand(v){
  const B = (typeof CONFIG!=='undefined' && CONFIG.battery) || {};
  if(v==null || !isFinite(v))
    return {key:'none', color:null,
            text:'no pack voltage is arriving, so this is NOT tracking the battery'};
  if(v < (B.critV||6.6))
    return {key:'crit', color:'var(--error)',
            text:'CRITICAL - below '+(B.critV||6.6)+' V. Surface now; '+(B.floorV||6.0)+' V damages the cells'};
  if(v < (B.warnV||7.0))
    return {key:'warn', color:'var(--hazard)',
            text:'low - under '+(B.warnV||7.0)+' V. Plan the way home'};
  return {key:'ok', color:'var(--tertiary)',
          text:'healthy - '+(B.warnV||7.0)+' V or better ('+(B.fullV||8.4)+' V is a full charge)'};
}

/* DID A NUMBER ARRIVE RECENTLY for this reading?

   The second half of the cannot-tell test, and deliberately the SECOND half. The first
   is simply "is the value there", because the vehicle now sends null for a reading
   whose sensor has stopped. This one catches the older shape of the same failure: a
   hull that drops the field altogether rather than nulling it, which leaves the last
   value sitting in `state` looking exactly like a live one.

   It is NOT a test of the link. It asks about a stamp that only a real number writes
   (net.js), which is the distinction the frozen-MS5837 dive turned on: the frames were
   arriving at 15 Hz, and they were carrying the same 4.33 m every time. */
function sensorFresh(at){ return !!at && (Date.now()-at) < (CONFIG.staleTimeoutMs||1000); }

/* WHAT THE VEHICLE CALLS THE CHIP THAT STOPPED, turned into a sentence an operator can
   act on. Telemetry carries the bare keys api/hardware.py raises internally
   ("ms5837", "bno085", "ina219", "i2c"); the console owes the person holding it the
   English, because "ms5837" is a part number and "the depth sensor has stopped
   answering" is an instruction to go and look at a specific cable.

   Each entry carries BOTH forms because both are needed and neither substitutes for the
   other: `short` is what fits on an alert chip the operator reads at a glance while
   driving, `long` is the sentence in the tooltip. Both lead with the JOB and carry the
   part number second - "depth sensor", never "ms5837" on its own. */
const SENSOR_CHIPS = {
  ms5837: { short:'DEPTH SENSOR',   long:'the MS5837 depth/pressure sensor' },
  bno085: { short:'COMPASS',        long:'the BNO085 compass/IMU' },
  ina219: { short:'PACK MONITOR',   long:'the INA219 pack monitor (voltage and current)' },
  i2c:    { short:'I2C BUS',        long:'the whole I2C bus (so every chip on it)' },
  // NOT A CHIP AT ALL, and it reaches this list anyway: api/hardware.py unions its
  // latched subsystem faults into sensor_faults(), and "ballast-limits" (both limit
  // switches reading triggered at once, which is electrically impossible) is one of
  // them. Left out of this table it fell through to a bare uppercase part-number
  // rendering of a name that is not even a part.
  'ballast-limits': { short:'BALLAST SWITCHES',
                      long:'the syringe’s two limit switches, which are both reading '
                         + 'triggered at once - wiring, not water' },
  // THE TWO NAMES BEHIND THE HULL-INTEGRITY GLYPH (api/hardware.py SUBSYSTEMS). Neither
  // is a chip either, and both were falling through to chipMeans()'s "newer than this
  // handheld" sentence — which is wrong twice over: this console knows exactly what they
  // are, and that sentence ends by saying nothing on screen was drawn from them, while
  // the leak drop is drawn from them.
  'leak-probes':  { short:'LEAK PROBES',
                    long:'the two leak probes in the bottom of the hull, which are not '
                       + 'being sampled at all - so nothing is reading whether the hull '
                       + 'is dry' },
  'sensor-thread':{ short:'SENSOR LOOP',
                    long:'the vehicle’s sensor loop itself, so nothing it reads is being '
                       + 'refreshed - the leak probes included' }
};
/* WHAT A NAME THIS CONSOLE HAS NEVER HEARD OF STILL MEANS.

   The vehicle owns this vocabulary and may grow it (a second depth sensor, a tilt
   sensor, a new latched subsystem fault) without the handheld being updated in the
   same breath - the two are flashed separately and routinely disagree by a version.
   An unknown name must therefore still arrive as a sentence: the console cannot say
   what the part DOES, but it can always say what the fault means, which is that the
   vehicle has stopped believing that part and anything it measured is not on screen.
   The old fallback was `'the ' + c.toUpperCase()` - "the TSYS01" - which is the exact
   failure this table exists to prevent, just spelled in capitals. */
function chipMeans(c){
  const d = SENSOR_CHIPS[c];
  if(d) return d;
  return { short: String(c).replace(/[-_]/g,' ').toUpperCase(),
           long: 'a part the vehicle calls “' + c + '”, which this console has no plain '
               + 'name for (it is newer than this handheld - see docs/hardware.md)' };
}
/* Which chips stand behind each reading. A dead I2C bus takes everything on it down at
   once, so it stands behind all of them - naming only the chip would have the console
   report several unrelated sensor failures for one unplugged connector.

   THE PACK IS ON THIS LIST NOW. It was the one measured reading with no entry, so a
   dead INA219 blanked nothing and explained nothing: the console showed a confident
   red "BATTERY 0.0V - SURFACE" invented entirely by the absent sensor, while "ina219"
   sat in sensor_faults with nowhere on screen to be read. A cannot-tell default that
   is itself a measurement is not a cannot-tell, and 0.0 V is a measurement - an
   impossible one, from a vehicle that is plainly powered enough to be transmitting. */
/* AND THE HULL IS ON THIS LIST NOW, for the same reason the pack was added to it last
   round: it was a reading with a cannot-tell state and no way to say WHICH part had
   stopped. The leak probes are GPIO, not I2C — a dead bus does not touch them — so the
   two names behind them are the leak sampler itself and the sensor loop that runs it
   (api/hardware.py: `sampling = not self._stalled & {"leak-probes", "sensor-thread"}`).
   `leak` is not a `sensed()` kind: the stage is a string ladder, not a number, so this
   entry is only ever read to NAME the cause beside the cannot-tell drop. */
/* AND THE FOUR INERTIAL READINGS ARE ON IT UNDER ONE NAME, because they are one chip.
   The BNO085 backs six things — the bearing, its cardinal, the calibration mark, the
   turn rate, forward acceleration and the attitude pair — and api/rov.py takes all six
   off the same hardware handle, so they arrive together and they stop together. `imu`
   is deliberately not a sixth copy of `heading`: the bearing is a primary instrument
   with a chip named on the alert rail when it dies, and these four are advisories that
   go quiet behind that ONE errand rather than raising four more. Same chips, different
   loudness. */
const SENSOR_BEHIND = { depth:['ms5837','i2c'], pressure:['ms5837','i2c'], heading:['bno085','i2c'],
                        imu:['bno085','i2c'],
                        battery:['ina219','i2c'], current:['ina219','i2c'],
                        leak:['leak-probes','sensor-thread'] };
function normalizeFaults(v){
  // Tolerant on the wire, strict in here: a list is the shape, but a single string
  // ("ms5837") or a joined one must not be silently read as no faults at all.
  if(v==null) return [];
  const list = Array.isArray(v) ? v : String(v).split(/[,\s]+/);
  return list.map(x=>String(x==null?'':x).trim().toLowerCase()).filter(Boolean);
}
/* The sentence naming what killed a reading, or '' if the vehicle did not say. Never
   REQUIRED for the reading to go cannot-tell — a null value is the whole evidence for
   that. This only supplies the cause, so a vehicle too old to report faults still
   blanks the number rather than showing a frozen one. */
function faultChips(kind, faults){
  const list = faults || state.sensorFaults || [];
  if(!list.length) return [];
  return (SENSOR_BEHIND[kind]||[]).filter(c=>list.indexOf(c)>=0);
}
function faultCause(kind, faults){
  return faultChips(kind, faults).map(c=>chipMeans(c).long).join(' and ');
}
/* THE VEHICLE HAS ALREADY ADMITTED THIS ONE IS NOT ANSWERING.

   Asked of a reading that arrived as a NUMBER, and the reason it has to be asked is
   that the number and the fault list are two readings of one verdict which can only
   ever disagree in one direction: the hull naming a chip while still shipping a value
   measured by it. That is not hypothetical - it is api/rov.py's battery path today,
   which sends `battery_v=0.0 if volts is None`, puts "ina219" in sensor_faults in the
   same frame, and leaves the console to paint an impossible 0.0 V red and shout
   SURFACE. Any cached-last-good reading behind a dead chip has the same shape.

   So the fault list is allowed to VETO a number, never to invent one. A vehicle too
   old to report faults sends an empty list and nothing here fires; the null on the
   reading itself remains the primary and sufficient evidence. */
function faultedNow(kind, faults){ return faultChips(kind, faults).length > 0; }
/* Faults the screen has NOT already accounted for by blanking something.

   Every name the vehicle sends has to be readable somewhere, or the fix is half a fix:
   a blank the operator cannot explain reads as a dashboard bug, and a fault that blanks
   nothing at all - "ballast-limits", or any name newer than this handheld - is worse
   still, because it is a vehicle shouting into a console that drops it on the floor.
   `explained` is the chips already named beside a blanked reading; whatever is left
   gets a chip of its own rather than being swallowed. */
function unexplainedFaults(faults, explained){
  const list = faults || state.sensorFaults || [];
  return list.filter(c=>explained.indexOf(c) < 0);
}

/* Is this speed a MEASUREMENT or an ESTIMATE? The paddlewheel-backed sources are
   the only ones that watched water go past; the LUT ones are the throttle curve
   talking, which is exactly what a snagged sub also reports. An estimate never
   dresses as a measurement, so everything downstream keys on this. */
function speedIsMeasured(src){ return src==='paddle' || src==='kf-paddle'; }

/* HOW MUCH THE HEADING IS WORTH, in one token, used by the HUD and the map so both
   say the same word.

     ''          nothing to say: the compass is calibrated and in use
     'mag'       mag_cal < 2 - the magnetometer is not calibrated, so the bearing
                 is suspect (§5.6). BROKEN.
     'gyro'      the filter is coasting on the gyro and IGNORING the compass ON
                 PURPOSE (thrusters running, or the mag is untrusted). DELIBERATE.
     'gyro-mag'  both - it is coasting, and the compass it would return to is not
                 trustworthy either.
     'nomag'     mag_cal is null: NO IMU ANSWERED AT ALL. There is no compass here
                 to calibrate, ignore or come back to. ABSENT.
     'dead'      the bearing itself is null: the compass ANSWERED and then STOPPED.
                 There is no heading at all, not even a bad one. GONE.
     'nofilter'  gyro_only is null after navigation HAD been answering: the estimator
                 has gone quiet, so this bearing quietly stopped being the filtered
                 one and became the raw compass, and the marks that used to qualify
                 it are gone with it. UNQUALIFIED.

   The deliberate/broken split is the whole point: an operator who reads "the
   compass is being ignored" as "the compass is dead" turns back from a dive that
   was working perfectly. Returns '' with no vehicle, because the simulator's
   heading is a model and a model has no compass to distrust. */
const HEADING_FLAGS = {
  mag: { label:'MAG?', cls:'suspect',
         title:'HEADING SUSPECT - the sub’s compass is not calibrated (mag_cal below 2), '
             + 'so this bearing and the map’s rotation may be wrong. Swing the sub through '
             + 'a few figure-eights to calibrate it.' },
  gyro:{ label:'GYRO', cls:'gyro',
         title:'GYRO ONLY - the heading filter is ignoring the compass ON PURPOSE, because the '
             + 'thrusters’ magnetic field is currently stronger than the earth’s. It is '
             + 'coasting on the spin sensor, which is accurate for a while and then drifts. '
             + 'This is deliberate, not a fault.' },
  'gyro-mag':{ label:'GYRO · MAG?', cls:'gyro suspect',
         title:'GYRO ONLY, AND THE COMPASS IS UNCALIBRATED - the filter is coasting on the spin '
             + 'sensor on purpose, and the compass it would fall back to is not trustworthy '
             + 'either (mag_cal below 2). Treat the bearing as approximate.' },
  // Reuses .suspect (amber), the same paint as MAG?, because both mean the same thing
  // to the operator's hands - do not steer on this number - and a fourth colour on an
  // 8px badge teaches nothing. The LABEL is what separates them.
  nomag:{ label:'NO COMPASS', cls:'suspect',
         title:'NO COMPASS - no IMU answered at all (mag_cal is null, which the protocol keeps '
             + 'distinct from a fitted compass reporting "uncalibrated"). Nothing is measuring '
             + 'which way the sub is pointing, so this bearing and the map’s rotation are not '
             + 'tracking a sensor. Check the IMU wiring before believing either.' },
  // THE COMPASS THAT WAS HERE AND STOPPED. Amber like its two neighbours, for the same
  // stated reason - it means the same thing to the operator's hands - and the LABEL is
  // what separates it. It does not need a mark of its own on the number, because there
  // is no number: the bearing is rendered as a question mark (render.js renderSensed),
  // which is a louder statement than any underline.
  dead:{ label:'NO BEARING', cls:'suspect',
         title:'NO BEARING - the compass answered earlier in this dive and has now stopped '
             + '(the vehicle is sending heading: null). A BNO085 that dies freezes its last '
             + 'heading AND its calibration score together, so the old console showed that '
             + 'frozen bearing wearing the "calibrated and in use" mark while the sub turned '
             + 'underneath it. Nothing is measuring direction: the number is a question mark '
             + 'and the radar is still drawn on the LAST angle it reported. Do not steer or '
             + 'navigate on either until the IMU is back.' },
  // THE ESTIMATOR STOPPED ANSWERING AND THE BEARING CHANGED SOURCE UNDER THE OPERATOR.
  // With no fresh nav state the vehicle stops stamping the filtered heading and the raw
  // compass in the frame stands (api/main.py fill_nav_fields) — a different number, out
  // by whatever magnetic error the thrusters are inducing, arriving with no announcement
  // at all. Worse, the marks that qualified the old one describe the FILTER, so they go
  // null with it: GYRO goes out, and a blank badge is this console's way of saying "the
  // compass is calibrated and the filter is using it", which is now three separate
  // claims none of which anybody checked. Amber and .suspect like its neighbours,
  // because it means the same thing to the operator's hands: this is not a bearing to
  // steer a canal wall by.
  nofilter:{ label:'RAW COMPASS', cls:'suspect',
         title:'RAW COMPASS - the navigation estimator has stopped answering, so this is no '
             + 'longer its filtered heading: it is the compass reading straight off the IMU, '
             + 'thruster interference and all. Nothing is currently judging how much it is '
             + 'worth, and nothing is watching for a snag either. Usable to hold a rough '
             + 'course; not to navigate on.' }
};
function headingFlag(){
  if(!vehicleRecent()) return '';
  // A NULL BEARING OUTRANKS EVERY OTHER MARK, because the others all qualify a number
  // and here there is no number to qualify. This is the case two review passes missed:
  // the compass that worked and then died. Its driver caches, so mag_cal froze at 3
  // alongside the heading and 'nomag' below could never fire - the frozen bearing
  // shipped carrying the trust mark that means "calibrated and in use".
  //
  // Same two-part test viewFromState() applies to the number itself (value first, stamp
  // second), so the badge and the bearing can never disagree about whether there IS a
  // bearing — a flag that said MAG? over a question mark would be describing a number
  // that is not on screen.
  if(state.heading == null || !sensorFresh(state.headingAt)) return 'dead';
  // NO IMU outranks GYRO, and that ordering is the whole fix. On a hull with no IMU the
  // filter reports gyro_only:true for a trivial reason - it reads mag_cal as 0 and stops
  // trusting it - so the old code showed GYRO: "deliberate, not a fault, coasting on the
  // spin sensor". There is no spin sensor either. That badge promised a bearing that
  // decays gracefully when in fact nothing is measuring heading at all.
  if(state.magCal == null) return 'nomag';
  const suspect = (typeof state.magCal === 'number') && state.magCal < 2;
  // === TRUE, not truthy. gyro_only is tri-state now and `!!null` is false, which is
  // navigation's REASSURING answer put into its mouth while it is saying nothing at
  // all. false means "the filter looked and it is using the compass"; null means the
  // filter is not there to look, which is the 'nofilter' case below.
  const gyro    = state.gyroOnly === true;
  if(gyro && suspect) return 'gyro-mag';
  if(gyro)    return 'gyro';
  if(suspect) return 'mag';
  // LAST, and deliberately: a definite complaint about the compass outranks the
  // estimator's silence, because MAG? is a measured fact with an errand attached
  // (swing the sub) while this is an absence — and the alert rail says navigation has
  // gone quiet in words either way, so nothing is lost by yielding here.
  //
  // Gated on navAnswered so a hull that simply has no estimator stays clean. On those
  // vehicles gyro_only has been null since power-on, the bearing has ALWAYS been the
  // raw compass, and a badge that never goes out is a badge nobody reads. This fires
  // only on the transition that main.py warns about: nav was answering, and stopped.
  if(state.gyroOnly == null && state.navAnswered) return 'nofilter';
  return '';
}

/* ============================================================================
   HOST RESOLUTION — default same origin; override via ?host=IP:PORT (persisted
   in localStorage). WS base is derived from the same host (ws/wss to match).
   ============================================================================ */
function resolveHost(){
  // §1: the backend is SAME ORIGIN by default (served by nginx on the Pi). ?host=IP:PORT is an
  // explicit override only — it no longer acts as the primary discovery path, and a stale stored
  // host never shadows same-origin when served.
  const served = location.protocol==='http:'||location.protocol==='https:';
  let host=null, secureParam=null;
  try{
    const params=new URLSearchParams(location.search);
    // ?sim=1 — DEMO MODE. There is deliberately no vehicle to look for, so the
    // simulator takes over immediately instead of spending three seconds failing to
    // reach a Pi that was never there. This is what the public demo is served with;
    // everything on screen is honest about being simulated (red robot, SIM badge).
    if(params.get('sim')==='1'){
      state.demo = true;
      LOG.state('DEMO MODE (?sim=1) — no vehicle, everything is the simulator');
      return;                                             // no host, no ws: pure sim
    }
    const p=params.get('host');
    if(p){ host=p; localStorage.setItem('rov_host', p); } // explicit override wins (and is remembered)
    secureParam=params.get('secure');                     // ?secure=0 forces plain http/ws (dev against a mock)
  }catch(e){}
  if(!host && served) host=location.host;                  // default: same origin
  if(!host){                                               // disk fallback only: last override / configured
    try{ host=localStorage.getItem('rov_host')||''; }catch(e){}
    if(!host && CONFIG.defaultHost) host=CONFIG.defaultHost;
  }
  host = host || '';
  // Scheme belongs to the PI, not the page. Same-origin → match the page. A cross-origin override
  // (the kiosk launcher on http://localhost → the Pi) → plain http/ws, because the Pi is plain HTTP
  // (sealed tether, no TLS). Pass ?secure=1 if you ever front the Pi with HTTPS.
  let secure;
  if(served && host===location.host) secure = (location.protocol==='https:');
  else secure = false;
  if(secureParam!==null) secure = (secureParam==='1' || secureParam==='true');
  state.host = host;
  state.httpBase = host ? (secure?'https':'http')+'://'+host : '';
  state.wsBase   = host ? (secure?'wss':'ws')+'://'+host : '';
}

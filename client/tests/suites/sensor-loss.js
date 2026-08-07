/* WHAT THIS GUARDS — the last few centimetres, where a null stops being a null and
   becomes a picture. The vehicle now says "I cannot tell" about depth, the bearing, the
   pack, the hull and the snag watch, and every one of those admissions has to survive
   the ingest path in net.js and arrive on the glass as something an operator reads AS an
   admission. Four ways it did not:

     THE HULL. leak_state 'UNKNOWN' means nobody is sampling the leak probes. The console
     folded every stage it did not recognise into NORMAL, so it painted the green
     struck-through drop captioned "both probes dry" — the single strongest reassurance
     this vehicle gives, made from evidence nobody was collecting. The fix is a fourth
     SHAPE and not a fourth colour: the operator is on a canal bank in sunlight and reads
     the outline first.

     THE BEARING. The collapsed radar is heading-up: the whole picture rotates on the
     compass. `-MAP.hdg` is 0 for a null — silently, with no NaN to notice — so the
     instant the BNO085 stopped, the dial went from rotate(-284.0) to rotate(0.0) and
     swung the world round to a bearing nothing was measuring. This suite asserts the
     TRANSFORM STRING, because `-null === 0` is the whole defect and any truthiness check
     ("did it change?", "is it set?") passes straight over it.

     THE NUMBERS. A dead sensor and a dropped frame are different facts with different
     answers — one is never coming back, the other comes back on its own — so they must
     not look the same. Cannot-tell is '?' and amber; stale is '--' and dim. A pack whose
     INA219 has stopped must also raise no alarm at all: 0.0 V used to reach the rail as
     "BATTERY 0.0V · SURFACE", a critical alarm invented entirely by an absent sensor.

     THE SNAG WATCH. snagged and gyro_only are tri-state, and both falses are the
     REASSURING answer. A null must not raise an alarm on a hull that never had an
     estimator, and it must not clear one that a real `true` raised: navigation going
     quiet is not evidence the sub came free.

     THE IMU, WHICH IS ONE CHIP AND MUST FAIL AS ONE. The BNO085 carries six readings —
     the bearing, its cardinal, the calibration mark, the turn rate, forward acceleration
     and the attitude pair — and api/rov.py takes all six off the same hardware handle,
     so they arrive together and they stop together. A cluster where one of them survives
     the chip's death is not a partial failure, it is a LIE: a turn rate still reading
     "0.0 deg/s" beside a blanked bearing says the sub is holding a steady course, which
     is the calm answer, made from a chip that is answering nothing. Every one of them
     goes to cannot-tell in the same frame, and one alert chip names the compass once.

     THE PACK, WHICH IS ALSO ONE CHIP. Voltage and current both come off the INA219, so a
     silent chip blanks BOTH — and the current had nowhere to be blanked, because it was
     spent inside the pack's tooltip ("drawing 3.1 A") where an operator in sunlight with
     wet hands never sees it. A reading that only exists on hover cannot go cannot-tell in
     any way anybody notices.

   DRIVEN THROUGH handleMessage(), the client's own WebSocket message handler, exactly as
   a Pi frame arrives. The coercion bugs live in the INGEST path — `typeof x === 'number'`
   dropping a null on the floor, `-null` becoming 0 — so a suite that wrote into `state`
   directly would skip the code it is here to test and pass on a console that is lying. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote text straight off the page, and the page has glyphs in it. run.py
  // prints to a Windows console whose codepage cannot encode them and dies mid-report
  // with a UnicodeEncodeError, taking every result after it with it. Anything the
  // codepage cannot carry is escaped rather than dropped: a report that cannot be
  // printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E\u00A0-\u00FF\u2013\u2014\u2018\u2019\u201C\u201D\u2022\u2026]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const cls=id=>($(id).className||'');
  const txt=id=>($(id).textContent||'').replace(/ /g,' ').trim();
  const alerts=()=>[...$('alerts').querySelectorAll('.alert')].map(e=>({
    kind:(e.className.match(/\b(crit|warn)\b/)||[])[1]||'',
    text:(e.querySelector('.alert-tx')||{}).textContent||''}));
  const alertText=()=>alerts().map(a=>a.text).join(' | ')||'none';

  // A HULL WITH NOTHING ELSE WRONG WITH IT. Every check below removes exactly one
  // reading, and a second alarm anywhere on the rail is noise that makes the finding
  // harder to read. 8.1 V is a healthy 2S pack (8.4 full, amber under 7.0, red under
  // 6.6); ballast_homed says the syringe has been on its empty stop, so 0.4 is a
  // reading rather than an un-homed stepper's leftover count; mag_cal 3 is a compass
  // that is calibrated and in use, which is what makes the heading marks below mean
  // something when they appear. snagged and gyro_only are deliberately ABSENT from
  // this frame: they are what section 4 is about, and `state.navAnswered` must still
  // be false when it starts.
  //
  // THE FOUR NEW BNO085 READINGS RIDE IN THE HEALTHY FRAME TOO, and they have to: the
  // point of section 5 is that they all go dark WITH the compass, and a check that they
  // are dark is worth nothing unless they were lit first. Distinct digits (12.0 / 0.35 /
  // -6.5 / 9.0, against a 1.2 A pack draw) so a readout wired to the wrong field cannot
  // hide behind a coincidence.
  const BASE = {type:'telemetry', mock:false, armed:false, seq:1,
    heading:284, heading_card:'NW', mag_cal:3,
    gyro_z_dps:12.0, accel_fwd_ms2:0.35, pitch_deg:-6.5, roll_deg:9.0,
    depth:4.2, pressure:20.7, battery_v:8.1, current_a:1.2,
    ballast_level:0.4, ballast_homed:true, ballast_needs_rehome:false, ballast_target:0.4,
    left:0, right:0, magnet:false, light_green:false, light_white:false,
    light_green_level:0, light_white_level:0,
    leak:false, leak_state:'NORMAL', leak_probe_fault:null,
    speed_ms:0.42, speed_src:'paddle', signal:4, sensor_faults:[]};

  let feed=null, frame=null;
  function stopFeed(){ if(feed){ clearInterval(feed); feed=null; } }
  // THE REAL DOOR IN. handleMessage parses the raw frame and dispatches on `type`, so
  // everything under test — the JSON, the typeof guards, the leak latch, the alarm
  // table — runs exactly as it does on a tether.
  function say(extra){
    frame = Object.assign({}, BASE, extra||{});
    stopFeed();
    handleMessage(JSON.stringify(frame));
    feed = setInterval(()=>handleMessage(JSON.stringify(frame)), 100);
    return sleep(260);                     // a RAF render plus a 10 Hz map tick
  }
  async function waitFor(pred, ms){
    const t0=Date.now();
    while(Date.now()-t0 < ms){ if(pred()) return true; await sleep(30); }
    return !!pred();
  }

  // A GLYPH'S SHAPE, WITH ITS COLOURS THROWN AWAY. Only the geometry attributes go into
  // the signature, so two drops that differ solely in fill/stroke produce the SAME
  // signature and fail. That is the claim being tested: the drop has to change what it
  // IS, not what colour it is, or a colour-blind operator in sunlight learns nothing.
  const GEO=['d','cx','cy','r','x','y','width','height','points','stroke-dasharray'];
  function shapeSig(html){
    const box=document.createElement('div'); box.innerHTML=html||'';
    return [...box.querySelectorAll('svg *')].map(n=>
      n.tagName+'['+GEO.map(a=>n.getAttribute(a)).filter(v=>v!=null).join('|')+']'
    ).sort().join(' ') || '(empty)';
  }
  const look=id=>({text:txt(id), cls:cls(id), color:getComputedStyle($(id)).color});
  // THE LIVE HALF OF A TOOLTIP. core.js keeps the HTML's written explanation in
  // data-help and appends the renderer's live sentence to it, so the whole title always
  // contains the words describing every stage — including the ones this reading is not.
  // Only the live half is a claim about the hull right now.
  const liveOf=el=>{ const h=el.dataset.help||'', t=el.getAttribute('title')||'';
    return (t.indexOf(h)===0 ? t.slice(h.length) : t).replace(/^[\s—-]+/,'').trim(); };

  // FINDING A READOUT THIS SUITE DOES NOT OWN THE NAME OF. The file that draws the
  // instrument cluster owns its element ids; this suite is written against the
  // BEHAVIOUR, so each new reading is looked up by the ids the console's naming
  // convention makes likely and, failing that, by the one property every number here is
  // guaranteed to have — a written explanation of what it means. Nothing already spoken
  // for can answer: renderBattery appends "drawing 1.2 A" to the pack tooltip, which
  // would otherwise let the VOLTAGE stand in for the current and make the whole of
  // section 5's pack half pass on the defect it exists to catch. A reading found neither
  // way fails its own named check — never a silent skip.
  const TAKEN=['depth-val','pressure-val','heading-val','battery-v','ballast-pct',
               'link-ms','cpu-c','cpu-pct','ram-pct','disk-gb','net-eth','origin-val',
               'origin-tile','sonar-teth','speed-val','speed-src','hdg-flag','hdg-warning',
               'cam-battery','cam-sd','cam-quality','cam-awb','cam-ev','cam-remaining','cam-mode'];
  function findReadout(ids, re){
    for(let i=0;i<ids.length;i++){ const el=$(ids[i]); if(el) return el; }
    const all=[...document.querySelectorAll('[id]')];
    for(let i=0;i<all.length;i++){
      const el=all[i];
      if(TAKEN.indexOf(el.id)>=0 || el.children.length) continue;
      const t=(el.dataset.help||el.getAttribute('title')||'');
      if(t && re.test(t)) return el;
    }
    return null;
  }
  const CLUSTER = {
    'the turn rate': {re:/turn rate|rate of turn|yaw rate|degrees per second|deg\/s|\u00b0\/s/i,
      ids:['turn-val','turn-rate','turn-rate-val','turnrate-val','yaw-val','yaw-rate',
           'gyro-val','gyro-z','gyro-z-val','rate-val','rot-val']},
    'forward acceleration': {re:/accelerat|m\/s2|m\/s²|surge|speeding up/i,
      ids:['accel-val','accel-fwd','accel-fwd-val','acc-val','accel','surge-val','a-fwd']},
    'pitch': {re:/\bpitch\b/i,
      ids:['pitch-val','pitch','pitch-deg','attitude-val','att-val','tilt-val',
           'pitch-roll-val','pitchroll-val']},
    'roll': {re:/\broll\b/i,
      ids:['roll-val','roll','roll-deg','attitude-val','att-val','tilt-val',
           'pitch-roll-val','pitchroll-val']},
    'the pack current': {re:/\bcurrent\b|\bamps?\b|\bamperes?\b/i,
      ids:['current-a','current-val','current','pack-a','pack-current','battery-a',
           'amps-val','amp-val','draw-val','load-a']}
  };
  const CEL={};
  const elText=el=>((el&&el.textContent)||'').replace(/ /g,' ').trim();
  const clusterText=n=>{ const el=CEL[n]; return el ? elText(el) : '(NO READOUT)'; };

  async function run(){
    await sleep(2600);
    // The collapsed 200 px dial is the one the rotation checks are about, and blind-nav
    // makes the radar fullscreen on a headless box with no video. Same setup the other
    // suites use.
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(400);
    ok('the radar is collapsed, so heading-up rotation is live', !MAP.expanded && MAP.headingUp,
       'expanded='+MAP.expanded+' headingUp='+MAP.headingUp+' blind='+MAP.blind);
    // Resolved ONCE, up front, so sections 2 and 5 are talking about the same elements
    // and a readout that appears halfway through the run cannot make them disagree.
    Object.keys(CLUSTER).forEach(n=>{ CEL[n]=findReadout(CLUSTER[n].ids, CLUSTER[n].re); });

    // ================= 0. A HULL WHOSE ESTIMATOR HAS NEVER RUN =================
    // FIRST, and it has to be first: `state.navAnswered` latches for the life of the
    // link (it is only reset in net.js's ws.onopen, which no suite can fire), so the
    // very first frame this console ever sees is the only chance to test the console's
    // behaviour before navigation has committed to anything. That is not a contrivance —
    // it is every boot: api/main.py sends snagged and gyro_only as null from power-on
    // until an origin exists, which on a real dive is the whole walk down to the water.
    ok('navigation has not committed to anything yet (the premise of the next two checks)',
       state.navAnswered===false, 'state.navAnswered='+state.navAnswered);
    await say({snagged:null, gyro_only:null});
    ok('a null snag on a hull that has never had an estimator raises NO alarm',
       !alerts().some(a=>/SNAG/.test(a.text)),
       'alerts="'+alertText()+'" — a chip that is up from power-on is a chip nobody reads');
    ok('a null gyro_only there raises no heading mark either',
       txt('hdg-flag')==='', 'hdg-flag="'+txt('hdg-flag')+'"');

    // ================= 1. THE HULL: FOUR STAGES, FOUR SHAPES =================
    // The latch has to be cleared between the wet stages: a WARN or FLOOD that has been
    // seen stands until the VEHICLE says NORMAL again (core.js latchLeakAlarm), which is
    // correct and would otherwise make every reading after the first one FLOOD.
    const glyph=async(stage, leakBit)=>{
      await say({leak_state:'NORMAL', leak:false});             // retire any latch
      await say({leak_state:stage, leak:leakBit});
      return {html:$('leak-icon').innerHTML, cls:cls('leak-icon'),
              live:liveOf($('leak-icon')),
              pulse:!!($('leak-pulse')&&$('leak-pulse').classList.contains('on')),
              sig:shapeSig($('leak-icon').innerHTML)};
    };
    const gNormal=await glyph('NORMAL', false);
    const gWarn  =await glyph('WARN',   true);
    const gFlood =await glyph('FLOOD',  true);
    // leak:true beside it, which is what api/rov.py actually sends for UNKNOWN — the old
    // single-bit alarm means "not certified dry", and false is the one value it may not
    // take when nobody has read the probes.
    const gUnknown=await glyph('UNKNOWN', true);

    ok('the three real stages each draw a different shape (the control)',
       new Set([gNormal.sig, gWarn.sig, gFlood.sig]).size===3,
       'NORMAL/WARN/FLOOD signatures all distinct');
    ok('a dry hull really does make the positive claim (the control)',
       gNormal.live.toLowerCase()==='both probes dry' && gNormal.cls==='leak-normal',
       'NORMAL says "'+gNormal.live+'"');
    ok('UNKNOWN is NOT the green "both probes dry" drop',
       gUnknown.sig!==gNormal.sig && gUnknown.cls!==gNormal.cls &&
       gUnknown.live.toLowerCase()!=='both probes dry',
       'class="'+gUnknown.cls+'" says "'+gUnknown.live.slice(0,80)+'"');
    ok('UNKNOWN differs in SHAPE from all three other stages, not merely in colour',
       new Set([gNormal.sig, gWarn.sig, gFlood.sig, gUnknown.sig]).size===4,
       'unknown='+gUnknown.sig.slice(0,110));
    ok('...and the shape is what carries it: colours alone would not distinguish them',
       gUnknown.sig!==gWarn.sig && gUnknown.sig!==gFlood.sig,
       'warn='+gWarn.sig.slice(0,60)+'  flood='+gFlood.sig.slice(0,60));
    ok('the cannot-tell drop is not styled as a clean hull',
       gUnknown.cls!==gNormal.cls, 'unknown class="'+gUnknown.cls+'" vs normal "'+gNormal.cls+'"');
    ok('a cannot-tell does not fire the FLOOD screen pulse',
       gUnknown.pulse===false && gFlood.pulse===true,
       'unknown pulse='+gUnknown.pulse+'  flood pulse='+gFlood.pulse+
       ' — the sampler stopping is a fault, not a flood');
    ok('and it says nobody is checking, rather than making a claim about the hull',
       /nobody|not being|stopped|cannot say/i.test(gUnknown.live),
       '"'+gUnknown.live.slice(0,130)+'"');

    // WET OUTRANKS CANNOT-TELL, and the direction is not symmetric: water that reached a
    // probe is established, and the sampler dying afterwards does not un-establish it.
    await say({leak_state:'NORMAL', leak:false});
    await say({leak_state:'FLOOD', leak:true});
    await say({leak_state:'UNKNOWN', leak:true});
    ok('a standing FLOOD is not talked down to cannot-tell',
       cls('leak-icon')==='leak-flood', 'class="'+cls('leak-icon')+'" after FLOOD then UNKNOWN');
    await say({leak_state:'NORMAL', leak:false});
    ok('and the hull can still be certified dry once the probes answer again',
       cls('leak-icon')==='leak-normal' && liveOf($('leak-icon')).toLowerCase()==='both probes dry',
       'class="'+cls('leak-icon')+'" says "'+liveOf($('leak-icon'))+'" — a blank that never '+
       'clears is its own fault');

    // ================= 2. THE NUMBERS: CANNOT-TELL vs STALE =================
    await say({});
    const liveDepth=look('depth-val'), livePress=look('pressure-val'), liveBatt=look('battery-v');
    ok('a reporting hull shows the numbers (the control)',
       liveDepth.text==='4.2 m' && livePress.text==='20.7 PSI' && liveBatt.text==='8.1V',
       'depth="'+liveDepth.text+'" pressure="'+livePress.text+'" pack="'+liveBatt.text+'"');

    // The MS5837 and the INA219 stop. depth and pressure are one instrument so they go
    // null together; volts and amps come off the other one.
    await say({depth:null, pressure:null, battery_v:null, current_a:null,
               sensor_faults:['ms5837','ina219']});
    const deadDepth=look('depth-val'), deadPress=look('pressure-val'), deadBatt=look('battery-v');
    const isNumber=s=>/\d/.test(s);
    ok('a null depth renders as cannot-tell and NOT as a number',
       deadDepth.text==='?' && !isNumber(deadDepth.text) && /\bnosensor\b/.test(deadDepth.cls),
       'text="'+deadDepth.text+'" class="'+deadDepth.cls+'"');
    ok('a null pressure does the same',
       deadPress.text==='?' && !isNumber(deadPress.text) && /\bnosensor\b/.test(deadPress.cls),
       'text="'+deadPress.text+'" class="'+deadPress.cls+'"');
    ok('a null pack voltage does the same',
       deadBatt.text==='?' && !isNumber(deadBatt.text) && /\bnosensor\b/.test(deadBatt.cls),
       'text="'+deadBatt.text+'" class="'+deadBatt.cls+'"');
    // THE OTHER HALF OF THE SAME CHIP. Volts and amps are one INA219 and go null in one
    // frame, so a console showing a confident current beside a blanked voltage would be
    // reporting a chip that is half dead — which is not a state this hardware has. The
    // known defect this is written against is quieter than that and worse: the current
    // had no readout at all, only the pack's tooltip, so it could not be seen going
    // cannot-tell because it could not be seen.
    ok('the pack current has a readout of its own, not just a line in the pack tooltip',
       !!CEL['the pack current'],
       CEL['the pack current'] ? ('#'+CEL['the pack current'].id)
         : 'no element on the page shows the current draw - "drawing 1.2 A" inside the '
         + 'battery tooltip is invisible to an operator in sunlight who is not hovering '
         + 'anything, and it cannot blank in any way anybody notices');
    ok('a null pack current renders as cannot-tell, exactly as the voltage beside it does',
       !!CEL['the pack current'] && clusterText('the pack current')==='?',
       CEL['the pack current'] ? ('current reads "'+clusterText('the pack current')+
         '" class="'+(CEL['the pack current'].className||'')+'"')
         : 'NOT RUN - there is no pack-current readout to blank');
    ok('the pack and its current go dark TOGETHER - one chip, one admission',
       !!CEL['the pack current'] && deadBatt.text==='?' && clusterText('the pack current')==='?',
       'pack="'+deadBatt.text+'" current="'+clusterText('the pack current')+
       '" — one of them still showing a number claims a chip that is half answering');
    ok('an absent pack raises NO battery alarm at all',
       !/BATTERY/.test(alertText()) && /\bbatt-none\b/.test(deadBatt.cls),
       'class="'+deadBatt.cls+'" alerts="'+alertText()+'" — "BATTERY 0.0V · SURFACE" was '+
       'a critical alarm invented whole by an absent sensor');
    ok('the blanked readings are explained rather than left as a dashboard glitch',
       /NO DEPTH/.test(alertText()) && /NO PACK VOLTAGE/.test(alertText()),
       'alerts="'+alertText()+'"');
    // AND THE CHIP IS NAMED, ON THE RAIL, IN WORDS. A blank with no cause attached reads
    // as a dashboard glitch, and a glitch is a thing an operator waits out. "PACK
    // MONITOR" is the job and not the part number, because "ina219" sends nobody
    // anywhere; the sentence behind it carries the part number for whoever opens the
    // hull. This is the chip that both blanked readings above belong to, so it must be
    // named ONCE and not twice - a screen that reports the same dead chip in two places
    // is a screen inviting the operator to go and check two things.
    const packChips = alerts().filter(a=>/PACK MONITOR/.test(a.text));
    ok('an alert chip names the pack monitor as the chip that stopped',
       packChips.length>=1 && packChips.some(a=>a.kind==='crit'),
       'chips naming the pack monitor: '+(packChips.map(a=>'"'+a.text+'" ['+a.kind+']').join(', ')||'none')+
       '  all alerts="'+alertText()+'"');
    // ONE INSTRUMENT, ONE INDICATOR (design.md §3), and it is not pedantry: the rail is
    // severity-ordered and finite, so a second chip about the SAME dead chip pushes a
    // real one down or off. render.js already merges depth and pressure into "NO DEPTH &
    // PRESSURE" for exactly this reason - one MS5837, one errand - and volts and amps are
    // the same relationship on the INA219.
    ok('...and it names it ONCE - one chip is one errand, and the rail is not a log',
       packChips.length===1,
       packChips.length+' chips for one INA219: '+
       (packChips.map(a=>'"'+a.text+'"').join(', ')||'none'));
    ok('...and it is not double-reported as an unexplained fault as well',
       !/NOT ANSWERING/.test(alertText()),
       'alerts="'+alertText()+'" — the INA219 is accounted for by the blanked readings, '+
       'so it must not also appear as a fault nothing on screen was drawn from');

    // THE STALE SHAPE, for comparison. A dropped frame on a still-open socket: the whole
    // bar dashes together and comes back on its own. setWsStatus is the client's own
    // call — it is what ws.onopen makes — and it is re-asserted through the wait because
    // the reconnect timer is meanwhile failing to reach a Pi that is not there.
    stopFeed();
    const hold=setInterval(()=>setWsStatus('online'), 16);
    setWsStatus('online');
    const gotStale=await waitFor(()=>state.mode==='stale' &&
                                    /\bis-stale\b/.test(cls('depth-val')), 2500);
    const staleDepth=look('depth-val'), staleBatt=look('battery-v');
    clearInterval(hold);
    setWsStatus('offline');
    ok('a dropped frame on a live socket reads as STALE, not as a dead sensor',
       gotStale && staleDepth.text==='--' && /\bis-stale\b/.test(staleDepth.cls),
       'mode='+state.mode+' depth text="'+staleDepth.text+'" class="'+staleDepth.cls+'"');
    ok('cannot-tell is VISIBLY different from stale — different glyph AND different class',
       staleDepth.text!==deadDepth.text && !/\bnosensor\b/.test(staleDepth.cls) &&
       !/\bis-stale\b/.test(deadDepth.cls),
       'dead "'+deadDepth.text+'" ['+deadDepth.cls+']  vs  stale "'+staleDepth.text+'" ['+staleDepth.cls+']');
    ok('...and the pack keeps the two apart too',
       staleBatt.text==='--' && deadBatt.text==='?',
       'dead "'+deadBatt.text+'"  vs  stale "'+staleBatt.text+'" — a dash is a frame that '+
       'will be along shortly; a chip that has stopped will not');
    ok('the two are different colours as well as different marks',
       deadDepth.color!==staleDepth.color,
       'dead '+deadDepth.color+'  vs  stale '+staleDepth.color);

    // And it comes back: a gauge that blanks and stays blank after the connector is
    // pushed home is its own fault, and one nobody notices until a dive.
    await say({});
    ok('the readings return once the chips answer again',
       txt('depth-val')==='4.2 m' && txt('battery-v')==='8.1V' &&
       !/\bnosensor\b/.test(cls('depth-val')),
       'depth="'+txt('depth-val')+'" pack="'+txt('battery-v')+'" alerts="'+alertText()+'"');

    // ================= 3. THE BEARING AND THE RADAR =================
    // The map is driven by /ws/nav on a real dive, so the bearing is fed through the map
    // socket's OWN message handler — the same function the vehicle's frames land in.
    // There is no nav socket to connect to in this harness, so the handler is taken off
    // the object connectNavWs() builds and called directly with a frame.
    connectNavWs();
    const navRx = MAP.navWs && MAP.navWs.onmessage;
    ok('the map exposes the nav-frame handler this section drives', typeof navRx==='function',
       'MAP.navWs.onmessage is '+(typeof navRx));
    const navFrame=(h)=>navRx({data:JSON.stringify({type:'nav', t:1, lat:51.5, lon:-0.1,
      depth_m:4.2, heading_deg:h, x_m:12, y_m:8, raw_lat:51.5, raw_lon:-0.1, snapped:false,
      snap_offset_m:0, range_m:14.4, payout_m:20, confidence:1, mag_cal:3, speed_ms:0.42,
      speed_src:'paddle', snagged:false, gyro_only:false, no_heading:(h==null),
      has_origin:true, simulated:false, reads_vehicle:true})});

    navFrame(284); await sleep(300);
    const rotLive=$('radar-north').getAttribute('transform');
    ok('the radar is heading-up: the ring turns with the measured bearing',
       rotLive==='rotate(-284.0)', 'radar-north transform="'+rotLive+'" at heading 284');

    // THE COMPASS DIES. Both sockets say so at once, which is what the vehicle does —
    // heading, its cardinal, mag_cal AND the four inertial readings all come off the one
    // BNO085 handle, so all six null in the same frame. Written out in full rather than
    // nulling only the bearing, because a frame that keeps a turn rate alive beside a
    // dead compass is a frame this vehicle cannot send, and a suite that feeds one is
    // asking the console questions about a hull that does not exist.
    navFrame(null);
    await say({heading:null, heading_card:null, mag_cal:null,
               gyro_z_dps:null, accel_fwd_ms2:null, pitch_deg:null, roll_deg:null,
               sensor_faults:['bno085']});
    await sleep(300);
    const rotDead=$('radar-north').getAttribute('transform');
    ok('a null bearing does NOT rotate the radar to north',
       rotDead!=='rotate(0.0)' && rotDead!=='rotate(-0.0)',
       'radar-north transform="'+rotDead+'" (was "'+rotLive+'"). `-null` is 0, and '+
       'rotate(0.0) on a heading-up dial is the picture for "the sub is pointing due north"');
    ok('...it holds the last angle a compass actually reported',
       rotDead===rotLive, 'transform "'+rotLive+'" -> "'+rotDead+'"');
    ok('the null never became a number on the way in',
       MAP.hdg!==0 && Math.abs(MAP.hdg-284)<0.01,
       'MAP.hdg='+MAP.hdg+' — held, not zeroed');
    ok('the bearing readout is a question mark, not a confident 0°',
       txt('heading-val')==='?' && /\bnosensor\b/.test(cls('heading-val')),
       'heading-val="'+txt('heading-val')+'" class="'+cls('heading-val')+'"');
    ok('and the dial itself admits it, so the held angle cannot read as a measured one',
       document.body.classList.contains('heading-dead') &&
       /NO BEARING/.test(txt('hdg-warning')) && /NO BEARING/.test(txt('hdg-flag')),
       'body.heading-dead='+document.body.classList.contains('heading-dead')+
       ' dial badge="'+txt('hdg-warning')+'" HUD flag="'+txt('hdg-flag')+'"');

    navFrame(31.5);
    await say({});
    await sleep(300);
    ok('the radar turns again the moment a compass answers',
       $('radar-north').getAttribute('transform')==='rotate(-31.5)' &&
       !document.body.classList.contains('heading-dead'),
       'transform="'+$('radar-north').getAttribute('transform')+'"');
    // Put the two sockets back on the same bearing before moving on. On a real vehicle
    // api/main.py makes them agree by construction (one estimate, one heading); this
    // harness drives them separately, and leaving the ring at 31.5 while the HUD reads
    // 284 would make the suite's own parting screenshot the sort of two-headings-on-one-
    // screen picture the rest of this work exists to prevent.
    navFrame(284); await sleep(150);

    // ================= 4. THE SNAG WATCH AND THE GYRO MARK =================
    // Section 0 covered the hull whose estimator never ran. This is the other half:
    // navigation that WAS answering and stops. The two must not look the same, and
    // neither of them may look like good news.
    //
    // Navigation commits to "looked, and it is fine" — and then goes quiet.
    await say({snagged:false, gyro_only:false});
    ok('navigation answering is recorded', state.navAnswered===true && !/SNAG/.test(alertText()),
       'navAnswered='+state.navAnswered+' alerts="'+alertText()+'"');
    await say({snagged:null, gyro_only:null});
    const lost=alerts().filter(a=>/SNAG/.test(a.text));
    ok('nav going quiet does not LATCH a snag alarm — it reports the watch as lost',
       lost.length===1 && lost[0].kind==='warn' && /WATCH LOST|NAV QUIET/.test(lost[0].text),
       'chip="'+(lost[0]||{}).text+'" kind="'+(lost[0]||{}).kind+'" — losing a safety net '+
       'quietly is how an operator goes on trusting it, but it is not the emergency');
    ok('and a null gyro_only does not clear the heading marks into a clean bill of health',
       txt('hdg-flag')==='RAW COMPASS',
       'hdg-flag="'+txt('hdg-flag')+'" — blank means "calibrated and the filter is using '+
       'it", which is three claims nobody checked');

    // A REAL SNAG, and then nav dies underneath it.
    await say({snagged:true, gyro_only:true});
    const pinned=alerts().filter(a=>/SNAG/.test(a.text));
    ok('a real snag raises the critical alarm (the control)',
       pinned.length===1 && pinned[0].kind==='crit' && /NO WAY ON/.test(pinned[0].text),
       'chip="'+(pinned[0]||{}).text+'" kind="'+(pinned[0]||{}).kind+'"');
    ok('a real gyro_only shows the GYRO mark (the control)',
       txt('hdg-flag')==='GYRO', 'hdg-flag="'+txt('hdg-flag')+'"');

    await say({snagged:null, gyro_only:null});
    const orphan=alerts().filter(a=>/SNAG/.test(a.text));
    ok('a null does NOT clear a snag alarm that was really raised',
       orphan.length===1 && orphan[0].kind==='crit' && /UNCONFIRMED/.test(orphan[0].text),
       'chip="'+(orphan[0]||{}).text+'" kind="'+(orphan[0]||{}).kind+'" — nav going quiet '+
       'is not evidence the sub came free');
    ok('...and it does not silently downgrade it out of critical either',
       orphan.length && orphan[0].kind==='crit',
       'kind="'+((orphan[0]||{}).kind)+'"');
    ok('a null gyro_only does not put the GYRO mark back to blank',
       txt('hdg-flag')!=='' && txt('hdg-flag')==='RAW COMPASS',
       'hdg-flag="'+txt('hdg-flag')+'" — the mark describes the FILTER, and the filter '+
       'has stopped; blank would say it is running and happy');

    // Only the vehicle saying so clears it.
    await say({snagged:false, gyro_only:false});
    ok('a real "no snag" is what clears it, and it does',
       !/SNAG/.test(alertText()), 'alerts="'+alertText()+'"');

    // ================= 5. THE IMU IS ONE CHIP AND DIES AS ONE =================
    // Six readings come off the BNO085 and api/rov.py takes all six off the same handle,
    // so on a real vehicle they are never anything but unanimous. The console's job is
    // to be unanimous with them — and the failure mode is not "a reading is missing", it
    // is that EVERY ONE OF THE FOUR NEW READINGS HAS A CALM DEFAULT. 0.0 deg/s is "not
    // turning". 0.0 m/s2 is "coasting". (0.0, 0.0) is "level". A survivor left showing
    // any of those beside a blanked bearing is not a partial failure, it is a picture of
    // a sub holding a steady, level course, drawn from a chip that is answering nothing —
    // which is the exact shape of the dive that started all of this.
    const IMU = ['the turn rate','forward acceleration','pitch','roll'];
    const present = IMU.filter(n=>CEL[n]);
    ok('the four inertial readings have somewhere to be read at all',
       present.length===IMU.length,
       present.length===IMU.length
         ? IMU.map(n=>n+'=#'+CEL[n].id).join(', ')
         : 'no readout for: ' + IMU.filter(n=>!CEL[n]).join(', ') +
           ' — the vehicle sends them every frame and nothing on this screen shows them, '
         + 'which is a reading that does not exist as far as the operator is concerned');

    // The healthy control FIRST: a check that they are all dark is worth nothing unless
    // they were all lit a moment earlier.
    await say({});
    const lit = present.filter(n=>{ const t=clusterText(n); return t!=='?' && t!=='--' && /\d/.test(t); });
    ok('a healthy IMU lights all four of them (the control)',
       present.length>0 && lit.length===present.length,
       present.map(n=>n+'="'+clusterText(n)+'"').join('  ')||'nothing to light');

    // AND NOW THE CHIP STOPS. Every field the BNO085 backs goes null in one frame, with
    // "bno085" beside it — which is exactly what MockHardware.kill("bno085") puts on the
    // wire, so this frame is the real one and not a suite's idea of one.
    await say({heading:null, heading_card:null, mag_cal:null,
               gyro_z_dps:null, accel_fwd_ms2:null, pitch_deg:null, roll_deg:null,
               sensor_faults:['bno085']});
    const survivors = present.filter(n=>clusterText(n)!=='?');
    const shown = ()=>present.map(n=>n+'="'+clusterText(n)+'"').join('  ')
                  || 'NOT RUN - none of the four has a readout to blank';
    ok('the IMU dying takes ALL FOUR inertial readings to cannot-tell, in the same frame',
       present.length>0 && survivors.length===0,
       shown() +
       (survivors.length ? ('  — STILL CLAIMING A READING: '+survivors.join(', ')+
         '. One survivor is worse than all four missing: it is the calm answer, drawn '
         + 'from a chip that answered nothing') : ''));
    ok('...and none of them fell back to the stale dash, which would say "wait for it"',
       present.length>0 && present.every(n=>clusterText(n)!=='--'), shown());
    ok('the bearing goes with them, off the same chip',
       txt('heading-val')==='?' && /\bnosensor\b/.test(cls('heading-val')),
       'heading-val="'+txt('heading-val')+'" class="'+cls('heading-val')+'"');
    // ONE CHIP, ONE ERRAND. Six blanked readings must not produce six chips telling the
    // operator to go and check six things; the whole rail is about turning a blank into
    // a single errand, and "the compass" is that errand.
    const compassChips = alerts().filter(a=>/COMPASS/.test(a.text));
    ok('an alert chip names the compass as the chip the whole cluster went dark behind',
       compassChips.length>=1,
       'chips naming the compass: '+(compassChips.map(a=>'"'+a.text+'"').join(', ')||'none')+
       '  all alerts="'+alertText()+'"');
    ok('...and it names it ONCE - six blanked readings off one BNO085 are one errand',
       compassChips.length===1,
       compassChips.length+' chips for one BNO085: '+
       (compassChips.map(a=>'"'+a.text+'"').join(', ')||'none')+
       ' — the rail is severity-ordered and finite, so five chips about one dead chip '
       + 'push a flood off the bottom of it');
    ok('nothing the vehicle named is left unaccounted for on the rail',
       !/NOT ANSWERING/.test(alertText()),
       'alerts="'+alertText()+'" — a fault the hull reported and the screen dropped is '+
       'the failure this rail exists to prevent');

    // AND THEY COME BACK, INCLUDING THROUGH ZERO. The chip answers again and reports a
    // sub sitting still and level — 0.0 on all four, which is the reading a moored sub
    // gives and is a MEASUREMENT, not an absence. `value || null` anywhere on the ingest
    // path renders this frame identically to the dead one above, so the recovery check
    // and the real-zero check are deliberately the same frame.
    await say({gyro_z_dps:0.0, accel_fwd_ms2:0.0, pitch_deg:0.0, roll_deg:0.0});
    const stillBlank = present.filter(n=>{ const t=clusterText(n); return t==='?'||t==='--'; });
    ok('a REAL zero on all four reads as a still, level sub — not as a dead chip',
       present.length>0 && stillBlank.length===0 &&
       present.every(n=>/0/.test(clusterText(n))),
       (present.map(n=>n+'="'+clusterText(n)+'"').join('  ')
        || 'NOT RUN - none of the four has a readout to zero') +
       (stillBlank.length ? ('  — blanked by a falsy zero: '+stillBlank.join(', ')) : ''));
    await say({});

    // ================= NOTHING ELSE BROKEN =================
    stopFeed();
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    // The cluster ids are appended from what actually resolved, rather than written out
    // by hand: this suite does not own those names, and a hand-written list would fail
    // for the wrong reason the day the cluster renames something.
    const need=['leak-icon','leak-pulse','depth-val','pressure-val','battery-v','heading-val',
                'hdg-flag','hdg-warning','radar-north','alerts']
               .concat(Object.keys(CEL).filter(n=>CEL[n]).map(n=>CEL[n].id));
    const missing=need.filter(id=>!$(id));
    ok('every element these checks read is still on the page', missing.length===0,
       missing.length? 'MISSING: '+missing.join(', ')
                     : need.length+' elements found (cluster: '+
                       (Object.keys(CEL).filter(n=>CEL[n]).map(n=>n+'=#'+CEL[n].id).join(', ')||'none resolved')+')');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

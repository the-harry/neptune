/* REAL LINK — nothing on this console may be synthesised while a vehicle is connected.
   Guards the rule that cost two regressions: the map marker moves on the SUB's output
   or it does not move. A simulated position over a real dive hides the failure it is
   most important to see (dead thruster, snagged tether, sub against a wall), because
   commanded throttle keeps drawing forward progress either way.
   Also guards the input vector, which is the operator's OWN feedback and must answer
   the stick in every mode, on the keyboard and gamepad paths alike.
   And the pack's colour, for the same reason: a band is a claim made in a colour that
   the operator acts on without reading the number, so it has to come from the VEHICLE's
   3S voltage and from nothing else. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(name,pass,detail)=>R.push({name, pass:!!pass, detail:String(detail)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));

  // A HEALTHY 3S PACK. 12.6 V is a full charge, 10.5 V is the amber line and 9.9 V the
  // red one, so 12.1 V is a vehicle with plenty in it — no check below is incidentally
  // flying an alarm. The 8.1 V this used to send was the 2S pack's healthy figure, and
  // on the 3S bands it reads below the hard floor — the same different-vehicle trap as
  // the 24.5 V before it, pointing the other way.
  const PACK_OK = 12.1;
  function tel(mock, heading, volts){
    return {type:'telemetry', mock:mock, heading:heading, depth:1.2, pressure:14.7,
            // ballast_homed says out loud what a bare 0.4 only implied: the syringe HAS
            // been driven onto its empty stop, so 40% is a reading rather than the count
            // an unhomed stepper happens to hold. Leaving it out left the level's meaning
            // resting on a client-side default, and a default is the one thing a
            // cannot-tell rule must never be tested through.
            ballast_level:0.4, ballast_homed:true, ballast_target:0.4,
            battery_v:(volts==null ? PACK_OK : volts), left:0, right:0,
            armed:false, magnet:false, light_green:false, light_white:false, leak:false};
  }
  let feed=null;
  function startFeed(mock, heading, volts){
    stopFeed();
    onTelemetry(tel(mock, heading, volts));
    feed=setInterval(()=>onTelemetry(tel(mock, heading, volts)), 100);
  }
  function stopFeed(){ if(feed){ clearInterval(feed); feed=null; } }
  const keys=(...k)=>{ state.keys.clear(); k.forEach(c=>state.keys.add(c)); };
  const vec=()=>({x:+$('sonar-vec').getAttribute('x2'), y:+$('sonar-vec').getAttribute('y2')});
  const dot=()=>({x:+$('sonar-dot').getAttribute('cx'), y:+$('sonar-dot').getAttribute('cy')});

  async function run(){
    await sleep(2500);

    // ================= THE PURPLE INPUT VECTOR =================
    // It is the operator's own feedback, so it must answer the stick in EVERY mode.
    startFeed(true, 284);                              // connected, sensorless vehicle
    await sleep(300);
    ok('linked to a vehicle', vehicleLinked()===true, 'vehicleLinked()=true');
    ok('vehicle reports no sensors', vehicleHasSensors()===false, 'mock:true');

    keys('KeyD');                                      // steer RIGHT
    await sleep(300);
    let v=vec(), d=dot();
    ok('input vector points RIGHT on a real link', v.x>40 && Math.abs(v.y)<5,
       'x2='+v.x+' y2='+v.y+'  dot('+d.x+','+d.y+')  STEER readout='+('R'+$('in-right').textContent+' L'+$('in-left').textContent));

    keys('KeyA');                                      // steer LEFT
    await sleep(300);
    v=vec();
    ok('input vector points LEFT on a real link', v.x<-40 && Math.abs(v.y)<5,
       'x2='+v.x+' y2='+v.y+'  STEER readout='+('R'+$('in-right').textContent+' L'+$('in-left').textContent));

    keys('KeyW');                                      // throttle FORWARD (y is up-negative)
    await sleep(300);
    v=vec();
    ok('input vector points FORWARD', v.y<-40 && Math.abs(v.x)<5,
       'x2='+v.x+' y2='+v.y+'  THROTTLE readout='+('F'+$('in-fwd').textContent+' B'+$('in-rev').textContent));

    keys('KeyW','KeyD');                               // diagonal — both axes at once
    await sleep(300);
    v=vec();
    ok('input vector goes diagonal (both axes live)', v.x>40 && v.y<-40,
       'x2='+v.x+' y2='+v.y);

    // ================= THE SUB MUST NOT MOVE =================
    // No sensors -> no navigation -> the marker holds. Commanded throttle must never
    // draw progress a real hull may not be making.
    keys('KeyW','KeyD');
    const p0={x:MAP.x, y:MAP.y}, h0=MAP.hdg;
    await sleep(2000);
    const moved=Math.hypot(MAP.x-p0.x, MAP.y-p0.y);
    ok('sub does NOT move without sensors', moved<0.001,
       'moved '+moved.toFixed(4)+' m under 2 s of full throttle+steer');
    ok('map heading does NOT drift without sensors', Math.abs(MAP.hdg-h0)<0.001,
       'MAP.hdg '+h0.toFixed(1)+' -> '+MAP.hdg.toFixed(1));
    ok('heading readout stays the vehicle\'s own', Math.abs(state.heading-284)<0.001,
       'state.heading='+state.heading+' (telemetry said 284)');
    ok('no track points invented', MAP.track.length===0, MAP.track.length+' track points');

    // ================= AND IT SAYS WHY =================
    const nw=$('nav-warning');
    const txt=()=>nw.textContent.replace(/\u00a0/g,' ').trim();
    ok('NO NAV badge is shown', nw && nw.classList.contains('on'),
       nw? 'text="'+txt()+'" visible='+nw.classList.contains('on') : 'element missing');

    // Wording depends on room. Headless has no video, so BLIND NAV makes the radar
    // fullscreen - turn it off to get the real 200 px glance circle.
    keys();                                            // all-stop-on-expand would collapse it again
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(500);
    const rr=$('radar').getBoundingClientRect(), br=nw.getBoundingClientRect();
    const dyb=Math.abs((br.y+br.height/2)-(rr.y+rr.height/2)), rad2=rr.width/2;
    const chord=dyb<rad2? Math.sqrt(rad2*rad2-dyb*dyb) : 0;
    ok('collapsed badge uses the short form', txt()==='NO NAV',
       '"'+txt()+'" in a '+Math.round(rr.width)+'px circle');
    ok('collapsed badge fits inside the circle', br.width/2 <= chord,
       'half-width '+(br.width/2).toFixed(1)+'px vs '+chord.toFixed(1)+'px of chord available');

    if(typeof expandMap==='function') expandMap();
    await sleep(500);
    ok('expanded badge names the cause', /NO SENSORS/.test(txt()), '"'+txt()+'"');
    if(typeof collapseMap==='function') collapseMap();
    await sleep(300);

    // ================= REAL NAV STILL DRIVES THE MAP =================
    startFeed(false, 123.4);                           // sensors fitted
    await sleep(300);
    ok('measured heading is adopted', Math.abs(state.heading-123.4)<0.01, 'state.heading='+state.heading);
    // Emulate nav frames arriving (no nav WS in this harness).
    let nx=10;
    const navFeed=setInterval(()=>{ MAP.lastNavAt=performance.now(); MAP.x=nx; MAP.y=nx*2; MAP.hdg=77; nx+=1; }, 100);
    await sleep(800);
    ok('NO NAV badge clears once nav arrives', nw && !nw.classList.contains('on'),
       'visible='+(nw?nw.classList.contains('on'):'?'));
    const bx=MAP.x;
    keys('KeyW');                                      // full throttle must add NOTHING
    await sleep(800);
    clearInterval(navFeed);
    ok('nav output alone moves the sub', MAP.x>bx, 'MAP.x '+bx+' -> '+MAP.x+' (from nav frames only)');
    ok('commanded throttle adds no distance', Math.abs(MAP.y-MAP.x*2)<0.001,
       'MAP=('+MAP.x.toFixed(2)+','+MAP.y.toFixed(2)+') still exactly on the nav-fed line y=2x');

    // ================= SIM (no vehicle at all) IS UNCHANGED =================
    keys(); stopFeed();
    state.realTel=null; state.realTelAt=0;
    await sleep(400);
    ok('not linked once telemetry stops', vehicleLinked()===false, 'vehicleLinked()=false');
    const s0={x:MAP.x,y:MAP.y}, sh=state.heading;
    keys('KeyW','KeyD');
    await sleep(1200);
    ok('SIM still flies the model', Math.hypot(MAP.x-s0.x,MAP.y-s0.y)>0.3,
       'moved '+Math.hypot(MAP.x-s0.x,MAP.y-s0.y).toFixed(2)+' m');
    ok('SIM still steers', Math.abs(((state.heading-sh)+540)%360-180)>15,
       'heading '+sh.toFixed(1)+' -> '+state.heading.toFixed(1));
    keys();

    // ================= THE ACTUAL ANALOG STICK =================
    // Keyboard and gamepad take different branches in computeInput, and the handheld
    // is flown on the stick — so exercise axis 0 (left stick X) directly.
    const pad={index:0, id:'FakePad (test)', connected:true,
               buttons:Array.from({length:17},()=>({pressed:false,value:0})), axes:[0,0,0,0]};
    navigator.getGamepads=()=>[pad];
    state.gamepadIndex=0;
    startFeed(true, 284);                              // back on a real, sensorless link
    await sleep(300);

    pad.axes=[0.8,0,0,0];                              // stick hard-ish RIGHT
    await sleep(300);
    v=vec();
    ok('analog stick RIGHT drives the vector', v.x>45 && Math.abs(v.y)<5,
       'axes[0]=0.8 -> x2='+v.x+' y2='+v.y+'  STEER='+('R'+$('in-right').textContent+' L'+$('in-left').textContent));

    pad.axes=[-0.6,0,0,0];                             // stick LEFT
    await sleep(300);
    v=vec();
    ok('analog stick LEFT drives the vector', v.x<-30 && Math.abs(v.y)<5,
       'axes[0]=-0.6 -> x2='+v.x+'  STEER='+('R'+$('in-right').textContent+' L'+$('in-left').textContent));

    pad.axes=[0.25,-0.9,0,0];                          // partial steer + near-full throttle
    await sleep(300);
    v=vec();
    ok('small stick deflections survive the deadzone', v.x>10 && v.y<-50,
       'axes=[0.25,-0.9] -> x2='+v.x+' y2='+v.y+'  STEER='+('R'+$('in-right').textContent+' L'+$('in-left').textContent)+
       ' THROTTLE='+('F'+$('in-fwd').textContent+' B'+$('in-rev').textContent));

    pad.axes=[0.05,0,0,0];                             // inside the 0.08 deadzone
    await sleep(300);
    v=vec();
    ok('deadzone still rejects stick noise', Math.abs(v.x)<1, 'axes[0]=0.05 -> x2='+v.x);

    pad.axes=[0,0,0,0]; state.gamepadIndex=null; navigator.getGamepads=()=>[];
    await sleep(200);

    // ================= THE PACK, ON ITS OWN 2S BANDS =================
    // WHAT THIS GUARDS: the colour of the pack voltage, which until §5 nothing tested at
    // all. It matters here because this suite's whole subject is what a REAL link is
    // allowed to claim — and a battery band is a claim, made in a colour, that the
    // operator flies on without reading the number. Three ways it goes wrong: the
    // thresholds sit on the 24 V scale of a pack that was never built and read FULL for
    // the entire dive; the boundary lands in the pessimistic band and cries wolf; or the
    // red chip latches and stays on after the pack recovers, which teaches the operator
    // to ignore the one alert that means "come up now".
    const battClass=()=>$('battery-v').className;
    const battText =()=>$('battery-v').textContent;
    const alerts   =()=>$('alerts').textContent.replace(/\u00a0/g,' ').trim();
    const battChip =()=>[...$('alerts').querySelectorAll('.alert')].find(e=>/BATTERY/.test(e.textContent))||null;
    const atVolts  =async v=>{ startFeed(true, 284, v); await sleep(300); };
    const B=CONFIG.battery;

    await atVolts(PACK_OK);
    ok('a healthy 3S pack is green, and shows the number beside the colour',
       /\bbatt-ok\b/.test(battClass()) && battText()===PACK_OK.toFixed(1)+'V' && !/BATTERY/.test(alerts()),
       PACK_OK+' V -> class="'+battClass()+'" text="'+battText()+'" alerts="'+(alerts()||'none')+'"');

    await atVolts(B.warnV);
    ok('the amber line itself still counts as healthy', /\bbatt-ok\b/.test(battClass()),
       B.warnV.toFixed(1)+' V (CONFIG.battery.warnV) -> class="'+battClass()+
       '" — "below 10.5" means 10.5 itself has not crossed');

    await atVolts(10.4);
    ok('under 10.5 V the pack goes amber, and only amber',
       /\bbatt-warn\b/.test(battClass()) && !/BATTERY/.test(alerts()),
       '10.4 V -> class="'+battClass()+'" alerts="'+(alerts()||'none')+'" — plan the way home, do not surface yet');

    await atVolts(B.critV);
    ok('the red line itself is still only amber', /\bbatt-warn\b/.test(battClass()),
       B.critV.toFixed(1)+' V (CONFIG.battery.critV) -> class="'+battClass()+
       '" — the red band starts BELOW it, so this is still a "plan the way home"');

    await atVolts(9.8);
    ok('under 9.9 V the pack goes red', /\bbatt-crit\b/.test(battClass()),
       '9.8 V -> class="'+battClass()+'"');
    ok('...and red SAYS what to do, carrying the voltage with it',
       /BATTERY 9\.8V · SURFACE/.test(alerts()) && /surface now/i.test((battChip()||{}).title||''),
       'chip="'+alerts()+'"  title="'+(((battChip()||{}).title)||'no chip').slice(0,90)+'..."');

    await atVolts(PACK_OK);
    ok('and the alert follows the pack back up instead of latching',
       /\bbatt-ok\b/.test(battClass()) && !/BATTERY/.test(alerts()),
       PACK_OK+' V -> class="'+battClass()+'" alerts="'+(alerts()||'none')+'" — a chip that stays lit is a chip nobody reads');

    ok('a pack voltage that is not arriving earns NO colour, not a green one',
       batteryBand(null).key==='none' && batteryBand(null).color===null,
       'batteryBand(null) -> '+JSON.stringify(batteryBand(null)));
    ok('a leftover 24 V reading would band as a FULL pack, not as an error',
       batteryBand(24.8).key==='ok',
       'batteryBand(24.8).key='+batteryBand(24.8).key+' — nothing rescales it, so a fixture '+
       'left on that scale silently tests a battery that cannot go flat');

    // ================= NOTHING ELSE BROKEN =================
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    const need=['heading-val','depth-val','cam-capture','cam-rec','radar','radar-north',
                'sonar-vec','sonar-dot','ballast-fill','btn-light-green','btn-surface','btn-config'];
    const missing=need.filter(id=>!$(id));
    ok('core UI still present', missing.length===0,
       missing.length? 'MISSING: '+missing.join(', ') : need.length+' elements found');
    ok('LOG bus still running', typeof LOG.ring==='function' && LOG.ring().length>0,
       (LOG.ring()||[]).length+' log rows');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

/* TETHER — the cable is 100 m and the console plans against it.
   SIM is CLAMPED (a dive the cable cannot reach must not look reachable on the bench)
   and a real link is WARNED ONLY (the launch point moves: more cable is paid out, the
   operator walks the bank, the boat drifts — an enforced limit would be both wrong and
   dangerous). Range is 3D, so depth eats into horizontal reach. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const keys=(...k)=>{ state.keys.clear(); k.forEach(c=>state.keys.add(c)); };
  function tel(mock, heading){
    return {type:'telemetry', mock:mock, heading:heading, depth:0, pressure:14.7,
            ballast_level:0, ballast_target:0, battery_v:24.5, left:0, right:0, armed:false,
            magnet:false, light_green:false, light_white:false, leak:false};
  }
  let feed=null;
  const startFeed=(m,h)=>{ stopFeed(); onTelemetry(tel(m,h)); feed=setInterval(()=>onTelemetry(tel(m,h)),100); };
  const stopFeed=()=>{ if(feed){clearInterval(feed);feed=null;} };
  const teth=()=>$('sonar-teth'), warn=()=>$('tether-warn');

  async function run(){
    await sleep(2500);
    const L=CONFIG.tether.lengthM;
    ok('tether length configured', L===100, L+' m, warn from '+CONFIG.tether.warnFromM+' m');

    // ---------- SIM: the cable is a HARD limit ----------
    state.realTel=null; state.realTelAt=0;               // unlinked -> SIM integrator
    CONFIG.map.subMaxSpeedMs=40;                          // cover 100 m in ~2.5 s instead of 100
    MAP.x=0; MAP.y=0; MAP.track.length=0;
    await sleep(300);
    keys('KeyW');
    await sleep(5000);                                    // drive well past the limit
    keys();
    const r=Math.hypot(MAP.x,MAP.y,MAP.depth||0);
    ok('SIM clamps at the cable length', r<=L+0.05 && r>L-2,
       'range '+r.toFixed(2)+' m after 5 s at 40 m/s (would be ~200 m unclamped)');
    ok('SIM readout shows the limit', /100 m/.test(teth().textContent), teth().textContent);
    ok('SIM readout flagged over', teth().classList.contains('over'), 'class="'+teth().className+'"');
    ok('SIM warning says END (clamped, not exceeded)',
       warn().classList.contains('on') && /TETHER END/.test(warn().textContent), warn().textContent);

    // the sub must still be able to drive HOME (clamp pulls in, never pushes out)
    state.heading=(state.heading+180)%360;
    keys('KeyW');
    await sleep(800);
    keys();
    const rHome=Math.hypot(MAP.x,MAP.y);
    ok('clamped sub can still drive home', rHome < r-5, 'range '+r.toFixed(1)+' -> '+rHome.toFixed(1)+' m');

    // ---------- depth eats into horizontal reach ----------
    const d0=MAP.depth; MAP.depth=60;
    const lim=tetherHorizLimitM(); MAP.depth=d0;
    ok('depth costs horizontal range', Math.abs(lim-80)<0.01,
       'at 60 m down a '+L+' m tether reaches '+lim.toFixed(2)+' m out (sqrt(100^2-60^2)=80)');

    // ---------- the amber band ----------
    MAP.x=85; MAP.y=0; MAP.depth=0;
    await sleep(300);
    ok('amber band warns before the limit',
       teth().classList.contains('warn') && !teth().classList.contains('over'), 'class="'+teth().className+'"');
    ok('amber warning shows range over length', /TETHER 85\/100 m/.test(warn().textContent), warn().textContent);
    MAP.x=40;
    await sleep(300);
    ok('warning clears well inside the limit',
       !warn().classList.contains('on') && !teth().classList.contains('warn'),
       'at 40 m: warn on='+warn().classList.contains('on')+' text='+teth().textContent);

    // ---------- REAL: warn only, NEVER clamp ----------
    startFeed(true, 284);                                 // linked vehicle
    await sleep(300);
    ok('linked', vehicleLinked()===true, 'vehicleLinked()=true');
    MAP.x=137; MAP.y=0; MAP.depth=0;                      // nav put us past the limit
    MAP.lastNavAt=performance.now();
    await sleep(400);
    ok('real link is NOT clamped', Math.abs(MAP.x-137)<0.001,
       'MAP.x held at '+MAP.x+' m — the launch point can move, so the console must not enforce');
    ok('real link warns instead', warn().classList.contains('on') && warn().classList.contains('over'),
       'warn on + over');
    ok('real warning says OVER with the real number', /TETHER OVER 137\/100 m/.test(warn().textContent),
       warn().textContent);

    // ---------- BLIND NAV: input + tether must be readable, in the SAME place ----------
    keys('KeyW','KeyD');
    await sleep(700);
    ok('blind nav is active', MAP.blind===true, 'body="'+document.body.className.split(' ').filter(c=>/map-/.test(c)).join(' ')+'"');
    const ro=document.querySelector('.sonar-readout').getBoundingClientRect();
    const dial=$('radar-dial').getBoundingClientRect();
    ok('input numbers are on the dial in blind nav',
       $('in-fwd').textContent==='100' && $('in-right').textContent==='100',
       'fwd='+$('in-fwd').textContent+' right='+$('in-right').textContent);
    ok('dial is bottom-left, not centred', dial.x<260 && dial.y+dial.height>innerHeight-120,
       'dial '+Math.round(dial.x)+','+Math.round(dial.y)+' '+Math.round(dial.width)+'px');
    ok('tether readout sits beside the dial', ro.x>=dial.x+dial.width-8 && ro.y>=0,
       'readout x='+Math.round(ro.x)+' vs dial right edge '+Math.round(dial.x+dial.width));
    ok('readout is on screen', ro.y+ro.height<=innerHeight+1 && ro.x>=0,
       'rect '+Math.round(ro.x)+','+Math.round(ro.y)+' '+Math.round(ro.width)+'x'+Math.round(ro.height));
    ok('tether still reads in blind nav', /m$/.test($('sonar-teth').textContent),
       'TETHER='+$('sonar-teth').textContent);

    // ---------- nothing regressed ----------
    const bx=MAP.x;
    await sleep(900);
    ok('sub still does not move on a real link', Math.abs(MAP.x-bx)<0.001,
       'MAP.x '+bx+' -> '+MAP.x+' under full throttle+steer');
    keys();
    // ---------- THE RING IS CENTRED ON THE OPERATOR ----------
    // The cable is in the operator's hand, so a circle drawn around the LAUNCH POINT
    // is a drawing of somewhere the sub can no longer necessarily reach — wrong from
    // the first step along the bank. MAP._lastRing records where it was actually
    // drawn, so this checks the pixels rather than the intent.
    keys();
    const M = m => m/111320;                       // metres -> degrees of latitude
    MAP.track.length=0; MAP.x=0; MAP.y=0; MAP.me=null; MAP.depth=0;
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    try{ await STORE.set('origin', MAP.origin); }catch(e){}
    await sleep(500);
    ok('with no fix the operator is ASSUMED at the launch point',
       !!operatorLL() && operatorLL().assumed===true,
       'operatorLL()=' + JSON.stringify(operatorLL()||null));
    ok('...and reads as LAST KNOWN, not live', meSource()==='stale',
       'meSource()='+meSource()+' — it is where they were, not where they are');
    const r0 = MAP._lastRing ? {x:MAP._lastRing.x, y:MAP._lastRing.y, r:MAP._lastRing.r} : null;
    ok('the ring is drawn', !!r0 && r0.r>4, r0 ? ('centre '+Math.round(r0.x)+','+Math.round(r0.y)+
       ' radius '+Math.round(r0.r)+'px') : 'nothing drawn');

    // move the operator; the circle must go with them
    setMockMe(51.5+M(45), -0.1);
    await sleep(700);
    const r1 = MAP._lastRing ? {x:MAP._lastRing.x, y:MAP._lastRing.y, r:MAP._lastRing.r} : null;
    ok('moving the operator moves the ring with them',
       !!r1 && Math.hypot(r1.x-r0.x, r1.y-r0.y) > 4,
       'centre moved ' + (r1 ? Math.round(Math.hypot(r1.x-r0.x, r1.y-r0.y)) : 0) + ' px');
    ok('the radius did not change with them', !!r1 && Math.abs(r1.r-r0.r) < 2,
       'radius ' + Math.round(r0.r) + ' -> ' + Math.round(r1.r) + ' px (the cable is the same length)');
    clearMockMe();

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('core UI intact', ['in-fwd','in-rev','in-left','in-right','sonar-teth','tether-warn','nav-warning','radar','cam-capture']
         .every(id=>!!$(id)), 'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

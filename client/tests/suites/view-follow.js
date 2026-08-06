/* VIEW FOLLOW — driving takes the view back. Panning is a halted-operator's luxury; the
   moment the sub is commanded to move the map must be showing the sub again, or the craft
   swims out of frame and the operator ends up flying the view as well as the vehicle.
   Below the deadzone nothing is taken, so a deliberate pan survives a twitchy stick. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const keys=(...k)=>{ state.keys.clear(); k.forEach(c=>state.keys.add(c)); };
  async function putOrigin(lat,lon){
    const o={lat,lon,accuracy:8,source:'test',t:Date.now()};
    try{ await STORE.set('origin', o); }catch(e){}
    MAP.origin=o; MAP.hasOrigin=true;
  }
  const subLL=()=>toLatLon(MAP.x, MAP.y, MAP.origin.lat, MAP.origin.lon);
  const offFromSubM=()=>{ const g=subLL();
    const p=toLocal(MAP.viewLat, MAP.viewLon, g.lat, g.lon);
    return Math.hypot(p.x,p.y); };

  async function run(){
    await sleep(2600);
    await putOrigin(51.5,-0.1);
    MAP.x=0; MAP.y=0; MAP.track.length=0; MAP.me=null;

    // ---------- BLIND NAV: the driving view ----------
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    CONFIG.map.blindNav=true; if(typeof enterBlindNav==='function') enterBlindNav();
    await sleep(500);
    ok('blind nav active', MAP.blind===true, 'MAP.blind='+MAP.blind);

    // halted: panning sticks
    keys();
    await sleep(300);
    panMapPx(300, 200);
    await sleep(400);
    const parked=offFromSubM();
    ok('halted, a pan stays where it was put', MAP.follow===false && parked>5,
       'follow='+MAP.follow+', view parked '+parked.toFixed(1)+' m off the sub');

    // now drive
    keys('KeyW');
    await sleep(500);
    ok('driving re-arms follow', MAP.follow===true, 'follow='+MAP.follow);
    await sleep(400);
    ok('and the view snaps back to the craft', offFromSubM()<1,
       'view now '+offFromSubM().toFixed(2)+' m off the sub (was '+parked.toFixed(1)+')');

    // steering alone counts as movement too
    keys();
    await sleep(300);
    panMapPx(-300, 0);
    await sleep(300);
    const parked2=offFromSubM();
    keys('KeyD');
    await sleep(600);
    ok('steering alone also retakes the view', MAP.follow===true && offFromSubM()<1,
       'parked '+parked2.toFixed(1)+' m -> '+offFromSubM().toFixed(2)+' m under steer');

    // a pan while ALREADY driving must not strand the view
    panMapPx(400, 0);
    await sleep(500);
    ok('panning while driving cannot strand the view', offFromSubM()<1,
       'view held on the sub at '+offFromSubM().toFixed(2)+' m');
    keys();

    // ---------- the collapsed radar ----------
    CONFIG.map.blindNav=false; if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(500);
    panMapPx(250, 0);
    await sleep(300);
    const rParked=offFromSubM();
    keys('KeyW');
    await sleep(600);
    ok('the radar follows again once driving', MAP.follow===true && offFromSubM()<1,
       'parked '+rParked.toFixed(1)+' m -> '+offFromSubM().toFixed(2)+' m');
    keys();

    // ---------- the EXPANDED map still collapses (all-stop view) ----------
    if(typeof expandMap==='function') expandMap();
    await sleep(500);
    ok('expanded map is open', MAP.expanded===true, 'expanded='+MAP.expanded);
    keys('KeyW');
    await sleep(500);
    ok('driving collapses the expanded map', MAP.expanded===false,
       'expanded='+MAP.expanded+' — it engages ALL STOP, so a movement command must close it');
    ok('and it comes back following', MAP.follow===true, 'follow='+MAP.follow);
    keys();

    // ---------- a nudge below the deadzone must NOT steal a deliberate pan ----------
    await sleep(300);
    panMapPx(250, 0);
    await sleep(300);
    const held=offFromSubM();
    // Drive it through a REAL axis: poking state.input is pointless because
    // computeInput rewrites it every frame, so that assertion passed on a zero.
    const pad={index:0,id:'FakePad',connected:true,mapping:'standard',
               buttons:Array.from({length:17},()=>({pressed:false,value:0})),
               axes:[0,-(CONFIG.deadzone||0.08)*0.5,0,0]};      // half a deadzone of throttle
    navigator.getGamepads=()=>[pad]; state.gamepadIndex=0;
    await sleep(500);
    ok('a sub-deadzone twitch does not steal the pan',
       MAP.follow===false && Math.abs(state.input.throttle)<1e-9,
       'axis1='+pad.axes[1].toFixed(3)+' (deadzone '+CONFIG.deadzone+') -> input.throttle='+
       state.input.throttle+', view still '+offFromSubM().toFixed(1)+' m off (parked '+held.toFixed(1)+')');
    // ...and a real deflection on the same axis DOES retake it
    pad.axes[1]=-0.6;
    await sleep(600);
    ok('a real deflection on that same axis does retake it',
       MAP.follow===true && offFromSubM()<1,
       'axis1=-0.600 -> throttle '+state.input.throttle.toFixed(2)+', view '+offFromSubM().toFixed(2)+' m off');
    pad.axes[1]=0; state.gamepadIndex=null; navigator.getGamepads=()=>[];

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

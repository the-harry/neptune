/* TRACK HISTORY — old paths stay, but two disjoint journeys are never joined by a line:
   a straight stroke between them reads as the sub having travelled it. Breaks go in at
   every jump that is not travel (plan start, plan end, ROV placed by hand) and must
   survive BOTH thinning passes — a decimation that drops a break silently re-joins the
   journeys it exists to separate. Also the eye toggle, and the refusal to place the ROV
   further from the operator than the cable is long. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const breaks=()=>MAP.track.filter(p=>p.brk).length;
  // refreshBootstrap re-reads the origin from IndexedDB every 5 s, so setting MAP.origin
  // in memory alone gets reverted mid-test. Persist it, as every real path does.
  async function putOrigin(lat,lon){
    const o={lat,lon,accuracy:8,source:'test',t:Date.now()};
    try{ await STORE.set('origin', o); }catch(e){}
    MAP.origin=o; MAP.hasOrigin=true;
  }

  async function run(){
    await sleep(2600);
    const M=m=>m/111320;
    await putOrigin(51.5,-0.1);
    MAP.track.length=0; MAP.x=0; MAP.y=0; MAP.me=null; MAP.meReal=null; MAP.showTrack=true;
    await sleep(300);

    // lay down a short real path
    for(let i=1;i<=5;i++) pushTrack(i*2, 0, 0);
    ok('a plain path has no breaks in it', breaks()===0, MAP.track.length+' points, '+breaks()+' breaks');

    // ---------- planning must not be joined to what came before ----------
    const nBefore=MAP.track.length;
    setMockMe(51.5+M(40), -0.1);
    await sleep(700);
    pushTrack(MAP.x+3, MAP.y, 0);                       // first point of the new journey
    ok('starting a plan breaks the trace', breaks()>=1,
       breaks()+' break(s) after planning started');
    ok('the old path is still there', MAP.track.length>nBefore,
       nBefore+' points before, '+MAP.track.length+' now — history kept, not cleared');
    const brkIdx=MAP.track.findIndex(p=>p.brk);
    ok('the break marks the FIRST point of the new journey', brkIdx>0 && brkIdx>=nBefore-1,
       'break at index '+brkIdx+' of '+MAP.track.length);

    clearMockMe();
    await sleep(500);
    pushTrack(MAP.x+3, MAP.y, 0);
    ok('ending a plan breaks it again', breaks()>=2, breaks()+' breaks total');

    // ---------- a break must survive decimation ----------
    const kept=decimatedTrack();
    ok('breaks survive display decimation', kept.filter(p=>p.brk).length===breaks(),
       kept.filter(p=>p.brk).length+' of '+breaks()+' breaks kept in the drawn track');
    // and survive the stored-track thinning
    const cap=CONFIG.map.maxTrackPoints; CONFIG.map.maxTrackPoints=12;
    for(let i=0;i<40;i++) pushTrack(100+i*2, 50, 0);
    ok('breaks survive track thinning too', breaks()>=2,
       breaks()+' breaks still present after thinning to '+MAP.track.length+' points');
    CONFIG.map.maxTrackPoints=cap;

    // ---------- the eye ----------
    const eye=$('map-track-toggle');
    ok('there is an eye button', !!eye, eye? 'present' : 'missing');
    ok('it starts showing the tracks', MAP.showTrack===true && /<svg/.test(eye.innerHTML),
       'showTrack='+MAP.showTrack+', icon rendered='+/<svg/.test(eye.innerHTML));
    const openIcon=eye.innerHTML;
    toggleTrack(); await sleep(150);
    ok('clicking hides them', MAP.showTrack===false, 'showTrack='+MAP.showTrack);
    ok('the icon changes to a struck-through eye', eye.innerHTML!==openIcon && /M4 20 20 4/.test(eye.innerHTML),
       'title now "'+eye.title+'"');
    ok('hiding does not discard the history', MAP.track.length>0,
       MAP.track.length+' points still held while hidden');
    toggleTrack(); await sleep(150);
    ok('clicking again shows them', MAP.showTrack===true && eye.innerHTML===openIcon, 'restored');

    // ---------- ROV out of reach is refused ----------
    MAP.track.length=0; MAP.x=0; MAP.y=0; MAP.me=null; MAP.depth=0;
    await putOrigin(51.5,-0.1);
    await sleep(300);
    const far=setRovLatLon(51.5+M(140), -0.1);          // 140 m out on a 100 m tether
    await sleep(200);
    ok('placing the ROV beyond the tether is REFUSED', far===false,
       'setRovLatLon returned '+far+' for a 140 m placement on a '+CONFIG.tether.lengthM+' m cable');
    ok('and the ROV did not move', Math.hypot(MAP.x,MAP.y)<1,
       'ROV still at '+MAP.x.toFixed(1)+','+MAP.y.toFixed(1));
    const near=setRovLatLon(51.5+M(70), -0.1);          // inside the cable
    await sleep(200);
    ok('inside the tether it is accepted', near===true && Math.abs(MAP.y-70)<2,
       'ROV placed at y='+MAP.y.toFixed(1)+' m');
    ok('a hand placement also breaks the trace', MAP.track.some(p=>p.brk),
       breaks()+' break(s) — the sub did not swim there');

    // move the operator, and the previously-refused spot becomes reachable
    setMockMe(51.5+M(60), -0.1);
    await sleep(700);
    const now=setRovLatLon(51.5+M(140), -0.1);
    await sleep(200);
    const an=tetherAnchorLocal();
    ok('after moving the operator, the same spot is allowed', now===true,
       'me='+(MAP.me? JSON.stringify({lat:+MAP.me.lat.toFixed(6),mock:!!MAP.me.mock}):'null')+
       ' origin.lat='+MAP.origin.lat.toFixed(6)+
       ' anchor=('+an.x.toFixed(1)+','+an.y.toFixed(1)+')'+
       ' rov=('+MAP.x.toFixed(1)+','+MAP.y.toFixed(1)+')'+
       ' diveUnderway='+diveUnderway()+' trackLen='+MAP.track.length);
    clearMockMe();

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('core UI intact', ['map-track-toggle','map-mock-me','map-set-rov','sonar-teth','radar-dial']
        .every(id=>!!$(id)), 'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

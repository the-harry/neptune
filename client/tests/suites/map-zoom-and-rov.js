/* MAP VIEW — opens at the provider's sharpest imagery (latitude-dependent, so it cannot
   be a constant), paddle zoom on F10/F9 which must never fire during the two-paddle
   SURFACE combo, and the ROV as a point separate from the operator: the sub cannot
   report where it is, so it is placed by hand and must NOT follow the operator around. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const key=(code,type)=>window.dispatchEvent(new KeyboardEvent(type,{code,bubbles:true}));
  const tap=(code)=>{ key(code,'keydown'); key(code,'keyup'); };

  async function run(){
    await sleep(2600);

    // ---------- 1. no blue circle; operator is green ----------
    ok('operator marker is green when live', C.meLive==='#4dffa6', 'C.meLive='+C.meLive+' (stale '+C.meStale+', mock '+C.meMock+')');
    ok('ROV arrow is still purple', C.sub==='#b46bff', 'C.sub='+C.sub);
    ok('accuracy halo is gone', drawMeMarker.length===4,
       'drawMeMarker takes '+drawMeMarker.length+' args (ctx,dpr,x,y) — no radius/accuracy');
    const src=drawMeMarker.toString();
    ok('no blue fill left in the marker', !/31,157,255/.test(src), 'no rgba(31,157,255,...) in drawMeMarker');

    // ---------- 2. max zoom on start ----------
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    MAP.track.length=0; MAP.me=null; MAP.x=0; MAP.y=0;
    await sleep(500);
    const expect = 156543.03392*Math.cos(51.5*Math.PI/180)/(1<<19);
    ok('map opens at the provider max zoom (z19)', Math.abs(MAP.scale-expect)<1e-6,
       'MAP.scale='+MAP.scale.toFixed(4)+' m/px, z19 at 51.5N = '+expect.toFixed(4));
    ok('that is sharper than the old fixed default', MAP.scale < 0.6,
       MAP.scale.toFixed(3)+' m/px vs the previous 0.600');
    ok('zoom pin is one-shot', MAP._zoomPinned===true, 'MAP._zoomPinned='+MAP._zoomPinned);

    // tiles: round UP so a coarse tile is never upscaled
    ok('tiles prefer the sharper level', CONFIG.map.preferSharpTiles===true, 'preferSharpTiles=true');

    // ---------- 3. F9 / F10 zoom, individually ----------
    if(typeof exitBlindNav==='function'){ CONFIG.map.blindNav=false; exitBlindNav(); }
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(400);
    if(typeof expandMap==='function') expandMap();
    await sleep(400);
    const z0=MAP.scale;
    tap('F10');
    await sleep(150);
    const z1=MAP.scale;
    ok('F10 alone zooms IN', z1 < z0-1e-9, z0.toFixed(4)+' -> '+z1.toFixed(4)+' m/px');
    tap('F9'); tap('F9');
    await sleep(150);
    const z2=MAP.scale;
    ok('F9 alone zooms OUT', z2 > z1+1e-9, z1.toFixed(4)+' -> '+z2.toFixed(4)+' m/px (two presses)');

    // holding BOTH is the SURFACE combo and must not zoom at all
    const z3=MAP.scale;
    key('F9','keydown'); key('F10','keydown');
    await sleep(200);
    key('F9','keyup'); key('F10','keyup');
    await sleep(200);
    ok('both paddles together do NOT zoom', Math.abs(MAP.scale-z3)<1e-12,
       'scale held at '+MAP.scale.toFixed(4)+' — that gesture is SURFACE, not zoom');
    ok('SURFACE combo still wired', (CONFIG.surfaceComboKeys||[]).join('+')==='F9+F10',
       'surfaceComboKeys='+JSON.stringify(CONFIG.surfaceComboKeys));

    // collapsed radar zooms its OWN scale, not the big map's
    if(typeof collapseMap==='function') collapseMap();
    await sleep(400);
    const bigBefore=MAP.scale, radBefore=MAP.radarScale;
    tap('F10');
    await sleep(150);
    ok('collapsed, F10 zooms the radar not the big map',
       MAP.radarScale<radBefore-1e-9 && Math.abs(MAP.scale-bigBefore)<1e-12,
       'radar '+radBefore.toFixed(3)+' -> '+MAP.radarScale.toFixed(3)+', big map unchanged at '+MAP.scale.toFixed(4));

    // ---------- 4. operator and ROV are two separate points ----------
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    MAP.x=0; MAP.y=0; MAP.track.length=0; MAP.me=null; MAP.originTap=false;
    const d30=30/111320;                                    // ~30 m north
    onLiveFix({coords:{latitude:51.5+d30, longitude:-0.1, accuracy:8}});
    await sleep(800);
    ok('operator moved with the fix', !!MAP.me && Math.abs(MAP.me.lat-(51.5+d30))<1e-9,
       'operator at '+(MAP.me?MAP.me.lat.toFixed(6):'?'));
    ok('ROV did NOT follow the operator', Math.abs(Math.hypot(MAP.x,MAP.y)-30)<1.5,
       'ROV is now '+Math.hypot(MAP.x,MAP.y).toFixed(1)+' m from the operator (was 0)');
    ok('tether range grew as the operator walked', Math.abs(tetherRangeM()-30)<1.5,
       'tether reads '+tetherRangeM().toFixed(1)+' m');

    // ---------- 5. pinpointing the ROV ----------
    // The datum already followed the operator, so measure the pinpoint from where the
    // datum is NOW (the operator's current spot), not from where it started.
    const before={x:MAP.x,y:MAP.y}, originBefore=MAP.origin.lat;
    const placed=setRovLatLon(originBefore + d30*2, -0.1);   // 60 m north of the current datum
    await sleep(300);
    ok('ROV can be pinpointed by hand', placed===true && Math.abs(MAP.y-60)<1.5,
       'ROV moved from y='+before.y.toFixed(1)+' to y='+MAP.y.toFixed(1)+' m (placed 60 m north of the datum)');
    ok('pinpointing does NOT move the datum', MAP.origin.lat===originBefore,
       'origin held at '+MAP.origin.lat.toFixed(6));
    // The operator is standing on the datum (it followed them), so the cable is the
    // full 60 m out to where the ROV was just placed.
    ok('tether measures operator -> ROV', Math.abs(tetherRangeM()-60)<1.5,
       'operator on the datum, ROV 60 m north => '+tetherRangeM().toFixed(1)+' m of cable');
    ok('arming the tap is exposed', typeof armRovTap==='function' && typeof NEPTUNE.setRov==='function',
       'armRovTap + NEPTUNE.setRov present');
    const btn=$('map-set-rov');
    ok('there is a button for it', !!btn && btn.title.length>0, btn? 'title="'+btn.title+'"' : 'missing');
    armRovTap(); await sleep(200);
    ok('arming sets the one-shot flag', MAP.rovTap===true, 'MAP.rovTap='+MAP.rovTap);
    MAP.rovTap=false; if(typeof hideOriginPrompt==='function') hideOriginPrompt();

    // ---------- 6. nothing broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('core UI intact',
       ['in-fwd','in-rev','in-left','in-right','sonar-vec','sonar-teth','tether-warn',
        'nav-warning','radar','radar-dial','map-set-rov','cam-capture'].every(id=>!!$(id)), 'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

/* HUD LAYOUT — the things that are only wrong when you look at them: depth ramp coverage,
   the exit button inside the bar and clear of every other control, one icon size across
   the status row, the eye's two states, REC/PIC feedback, and map panning by drag and by
   right stick (with the camera NOT commanded while the map has it). */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const box=e=>e.getBoundingClientRect();

  async function run(){
    await sleep(2600);
    const M=m=>m/111320;

    // ---------- 1. depth ramp ----------
    const seen=new Set(); const maxD=CONFIG.map.maxDepthColorM;
    for(let i=0;i<=40;i++) seen.add(_depthColor(i/40*maxD));
    ok('depth resolves to every band', seen.size===DEPTH_RAMP.length,
       seen.size+' distinct colours across 0-'+maxD+' m');
    ok('surface and bottom are unmistakable',
       DEPTH_RAMP[0]!==DEPTH_RAMP.at(-1) && _depthColor(0)===DEPTH_RAMP[0] && _depthColor(999)===DEPTH_RAMP.at(-1),
       'shallow '+DEPTH_RAMP[0]+' -> deep '+DEPTH_RAMP.at(-1));
    ok('there is a legend function', typeof drawDepthLegend==='function', 'drawDepthLegend present');

    // ---------- 2. exit button ----------
    const exit=$('btn-exit'), bar=$('topbar');
    const eb=box(exit), bb=box(bar);
    ok('exit sits INSIDE the top bar', eb.left>=bb.left-1 && eb.right<=bb.right+1 && eb.top>=bb.top-1,
       'exit '+Math.round(eb.left)+','+Math.round(eb.top)+' in bar '+Math.round(bb.left)+'-'+Math.round(bb.right));
    ok('exit is the leftmost control', (()=>{
        const others=['origin-tile','st-net','st-rov'].map(id=>$(id)).filter(Boolean).map(box);
        return others.every(o=>eb.left < o.left);
      })(), 'exit at x='+Math.round(eb.left));
    ok('exit is clear of the next control', (()=>{
        const n=$('st-net'); return n && box(n).left - eb.right >= 8;
      })(), 'gap to the status icons: '+Math.round(box($('st-net')).left-eb.right)+'px');
    ok('the top bar now reaches the rail', Math.abs(bb.right-(innerWidth-84))<2,
       'bar right edge '+Math.round(bb.right)+', rail starts at '+(innerWidth-84));

    // ---------- 3. top-bar icon sizes ----------
    const ids=['st-net','st-rov','st-video','leak-icon'];   // st-cam removed by design
    const sizes=ids.map(id=>{ const b=box($(id)); return Math.round(b.width)+'x'+Math.round(b.height); });
    ok('every top-bar icon is the same size', new Set(sizes).size===1, ids.join(',')+' = '+sizes.join(' '));

    // ---------- 4. the eye ----------
    STATUS.video='live'; STATUS.render();
    await sleep(100);
    const liveHTML=$('st-video').innerHTML;
    ok('live feed shows an open eye (green)', /circle/.test(liveHTML) && !/M3.5 20.5/.test(liveHTML) &&
       $('st-video').className.indexOf('ok')>=0, 'class="'+$('st-video').className+'"');
    STATUS.video='down'; STATUS.render();
    await sleep(100);
    ok('no feed shows a struck-through eye (red)', /M3.5 20.5/.test($('st-video').innerHTML) &&
       $('st-video').className.indexOf('down')>=0, 'class="'+$('st-video').className+'"');
    ok('the BLIND NAV banner is gone', !$('blind-banner'), 'element absent');
    ok('no status banner shows at all', (()=>{
        STATUS.link='sim'; STATUS.vehicle='sim'; STATUS.cam='down'; STATUS._lastGate='';
        if(typeof STATUS.applyGates==='function') STATUS.applyGates();
        const b=$('controls-disabled');
        return !b || !/SIM/.test(b.textContent);
      })(), 'controls-disabled text = "'+($('controls-disabled')||{}).textContent+'"');

    // ---------- 5. REC / PIC feedback ----------
    const rec=$('cam-rec');
    // transition-all is on this button, so sample only AFTER it settles - reading it
    // mid-transition compares two nearly identical colours and proves nothing.
    rec.dataset.rec='ready'; rec.classList.remove('recording'); await sleep(700);
    const offBg=getComputedStyle(rec).backgroundColor, offFg=getComputedStyle(rec).color;
    rec.dataset.rec='all'; rec.classList.add('recording'); await sleep(900);
    const recBg=getComputedStyle(rec).backgroundColor, recFg=getComputedStyle(rec).color;
    rec.dataset.rec='ready'; rec.classList.remove('recording');
    // Recording must be SOLID (no alpha) and the text inverted - a faint tint is what
    // the operator reported as "the colour doesn't seem to change".
    const solid = /^rgb\(/.test(recBg) && !/\/\s*0?\.\d/.test(recBg);
    ok('REC goes solid when recording', solid && recBg!==offBg && recFg!==offFg,
       'idle bg '+offBg+' fg '+offFg+'  ->  recording bg '+recBg+' fg '+recFg);
    ok('REC animates while recording', (()=>{
        rec.dataset.rec='all'; rec.classList.add('recording');
        const a=getComputedStyle(rec).animationName;
        rec.dataset.rec='ready'; rec.classList.remove('recording');
        return a && a!=='none';
      })(), 'animation-name while recording');
    firePicFlash();
    await sleep(80);
    ok('PIC flashes on capture', $('cam-capture').classList.contains('shot') &&
       getComputedStyle($('cam-capture')).animationName!=='none',
       'class="'+$('cam-capture').className.split(' ').filter(c=>c==='shot')+'" animation='+
       getComputedStyle($('cam-capture')).animationName);

    // ---------- 6. rail buttons are smaller ----------
    const railBtns=['cam-rec','cam-capture','btn-surface','btn-config'].map(id=>box($(id)).height);
    ok('rail buttons are compact', railBtns.every(h=>h<=54),
       'heights: '+railBtns.map(h=>Math.round(h)).join(', ')+' px');
    ok('rail glyphs match the slider scale',
       parseFloat(getComputedStyle($('cam-rec').querySelector('.material-symbols-outlined')).fontSize)<=17,
       'glyph font-size '+getComputedStyle($('cam-rec').querySelector('.material-symbols-outlined')).fontSize);

    // ---------- 7. map panning ----------
    try{ await STORE.set('origin', {lat:51.5,lon:-0.1,accuracy:8,source:'test',t:Date.now()}); }catch(e){}
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    MAP.viewLat=51.5; MAP.viewLon=-0.1; MAP.follow=true;
    CONFIG.map.blindNav=false; if(typeof exitBlindNav==='function') exitBlindNav();
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(500);
    const lat0=MAP.viewLat, lon0=MAP.viewLon;
    panMapPx(120, 0);
    await sleep(100);
    ok('panning moves the view', Math.abs(MAP.viewLon-lon0)>1e-8,
       'lon '+lon0.toFixed(6)+' -> '+MAP.viewLon.toFixed(6));
    ok('panning drops follow-the-sub', MAP.follow===false, 'MAP.follow='+MAP.follow);

    // right stick
    MAP.viewLat=51.5; MAP.viewLon=-0.1;
    const pad={index:0,id:'FakePad',connected:true,
               buttons:Array.from({length:17},()=>({pressed:false,value:0})),axes:[0,0,0.9,0]};
    navigator.getGamepads=()=>[pad]; state.gamepadIndex=0;
    await sleep(600);
    ok('the right stick pans the map', Math.abs(MAP.viewLon-(-0.1))>1e-8,
       'axes[2]=0.9 -> lon -0.100000 -> '+MAP.viewLon.toFixed(6));
    ok('and the camera is NOT commanded while it does', Math.abs(state.input.pan)<0.001,
       'input.pan='+state.input.pan+' (map has the stick), mapPanX='+state.input.mapPanX);
    // ...but with the map closed the stick goes back to the camera
    if(typeof collapseMap==='function') collapseMap();
    await sleep(500);
    ok('closing the map hands the stick back to the camera', Math.abs(state.input.pan-0.9)<0.05,
       'input.pan='+state.input.pan.toFixed(2)+' mapPanX='+state.input.mapPanX);
    pad.axes=[0,0,0,0]; state.gamepadIndex=null; navigator.getGamepads=()=>[];

    // ---------- 8. nothing broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('geoCheck reports a reason', (()=>{ const g=geoCheck(); return typeof g.secureContext==='boolean'; })(),
       JSON.stringify(geoCheck()).slice(0,150));

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

/* INPUT DIAL + LIVE POSITION — the four 0-100 direction numbers, and the split between
   MAP.me (the operator, always live) and MAP.origin (the dead-reckoning datum, frozen
   once a dive starts). Moving the datum mid-dive would shift every plotted coordinate,
   so the sub would appear to jump sideways and the recorded track would be a lie.
   Also pins the blind-nav dial to the SAME place and size as the live-feed view. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const keys=(...k)=>{ state.keys.clear(); k.forEach(c=>state.keys.add(c)); };
  const num=id=>$(id).textContent;
  const lit=id=>$(id).classList.contains('live');
  const rect=el=>{const b=(typeof el==='string'?$(el):el).getBoundingClientRect();
    return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)};};
  const same=(a,b)=>a.x===b.x&&a.y===b.y&&a.w===b.w&&a.h===b.h;

  async function run(){
    await sleep(2500);

    // ---------- 1. four directional numbers ----------
    ok('old throttle/steer text readouts are gone', !$('sonar-thr') && !$('sonar-str'),
       'sonar-thr='+(!!$('sonar-thr'))+' sonar-str='+(!!$('sonar-str')));
    ok('FWD/REV word labels replaced', document.querySelectorAll('.sonar-tick').length===0,
       document.querySelectorAll('.sonar-tick').length+' .sonar-tick elements left');

    keys('KeyW','KeyD');                                  // full forward + full right
    await sleep(300);
    ok('forward+right show 100, opposites show 0',
       num('in-fwd')==='100' && num('in-right')==='100' && num('in-rev')==='0' && num('in-left')==='0',
       'fwd='+num('in-fwd')+' right='+num('in-right')+' rev='+num('in-rev')+' left='+num('in-left'));
    ok('only the driven directions are lit',
       lit('in-fwd') && lit('in-right') && !lit('in-rev') && !lit('in-left'),
       'lit: fwd='+lit('in-fwd')+' right='+lit('in-right')+' rev='+lit('in-rev')+' left='+lit('in-left'));

    keys('KeyS','KeyA');                                  // full reverse + full left
    await sleep(300);
    ok('reverse+left show 100, opposites show 0',
       num('in-rev')==='100' && num('in-left')==='100' && num('in-fwd')==='0' && num('in-right')==='0',
       'fwd='+num('in-fwd')+' right='+num('in-right')+' rev='+num('in-rev')+' left='+num('in-left'));

    // partial deflection on the stick
    const pad={index:0,id:'FakePad',connected:true,
               buttons:Array.from({length:17},()=>({pressed:false,value:0})),axes:[0.42,-0.65,0,0]};
    navigator.getGamepads=()=>[pad]; state.gamepadIndex=0; keys();
    await sleep(300);
    ok('partial stick reads as a partial number',
       num('in-right')==='42' && num('in-fwd')==='65' && num('in-left')==='0' && num('in-rev')==='0',
       'axes[0.42,-0.65] -> right='+num('in-right')+' fwd='+num('in-fwd'));
    pad.axes=[0,0,0,0]; state.gamepadIndex=null; navigator.getGamepads=()=>[];
    await sleep(300);
    ok('all four dim at rest',
       !lit('in-fwd')&&!lit('in-rev')&&!lit('in-left')&&!lit('in-right')&&num('in-fwd')==='0',
       'all zero and dim');

    // ---------- 2. blind nav puts the dial exactly where the live feed does ----------
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(600);
    const dialCollapsed=rect('radar-dial'), readCollapsed=rect(document.querySelector('.sonar-readout'));
    // #radar is the 200 px circle; the dial is inset:0 INSIDE its 1.5 px ring, so 198.
    const radC=rect('radar');
    ok('collapsed dial fills the 200 px circle, bottom-left',
       radC.w===200 && radC.h===200 && dialCollapsed.w===198 && radC.x===24 &&
       Math.abs((innerHeight-(radC.y+radC.h))-44)<2,
       'radar '+JSON.stringify(radC)+'  dial '+JSON.stringify(dialCollapsed)+
       '  bottom gap '+(innerHeight-(radC.y+radC.h))+'px');

    CONFIG.map.blindNav=true;
    if(typeof enterBlindNav==='function') enterBlindNav();
    await sleep(700);
    const dialBlind=rect('radar-dial'), readBlind=rect(document.querySelector('.sonar-readout'));
    ok('blind nav is active', MAP.blind===true, 'MAP.blind='+MAP.blind);
    ok('dial is in the SAME place and size in blind nav', same(dialCollapsed,dialBlind),
       'live-feed '+JSON.stringify(dialCollapsed)+'  blind '+JSON.stringify(dialBlind));
    ok('tether readout is in the same place too', same(readCollapsed,readBlind),
       'live-feed '+JSON.stringify(readCollapsed)+'  blind '+JSON.stringify(readBlind));
    const mp=rect('map-panel');
    ok('but the MAP is fullscreen in blind nav', mp.w>=innerWidth-1 && mp.h>=innerHeight-1,
       'map-panel '+JSON.stringify(mp)+' vs viewport '+innerWidth+'x'+innerHeight);

    // stick still shown on the dial while blind
    keys('KeyW','KeyD');
    await sleep(300);
    ok('stick still drives the dial in blind nav',
       num('in-fwd')==='100' && num('in-right')==='100' && lit('in-fwd'),
       'fwd='+num('in-fwd')+' right='+num('in-right'));

    // ---------- 3. the blind dial must NOT be clickable ----------
    const wasExpanded=MAP.expanded;
    $('radar').dispatchEvent(new MouseEvent('click',{bubbles:true}));
    await sleep(300);
    ok('tapping the dial in blind nav does nothing', MAP.expanded===false && wasExpanded===false,
       'MAP.expanded='+MAP.expanded+' (must not expand — that engages ALL STOP)');
    ok('dial itself never takes pointer events',
       getComputedStyle($('radar-dial')).pointerEvents==='none',
       'pointer-events='+getComputedStyle($('radar-dial')).pointerEvents);
    keys();

    // ---------- 4. the handheld position is LIVE ----------
    MAP.origin={lat:51.5,lon:-0.1,accuracy:10,t:Date.now()}; MAP.hasOrigin=true;
    MAP.track.length=0; MAP.originTap=false; MAP.me=null;
    const d10=10/111320;                                   // ~10 m of latitude
    onLiveFix({coords:{latitude:51.5+d10, longitude:-0.1, accuracy:10}});
    await sleep(700);
    ok('a fix sets the live handheld marker', !!MAP.me && Math.abs(MAP.me.lat-(51.5+d10))<1e-9,
       MAP.me? 'me at '+MAP.me.lat.toFixed(6)+','+MAP.me.lon.toFixed(6)+' ±'+MAP.me.acc+' m' : 'no marker');
    ok('launch point follows the handheld BEFORE a dive', Math.abs(MAP.origin.lat-(51.5+d10))<1e-9,
       'origin lat 51.500000 -> '+MAP.origin.lat.toFixed(6));

    // now a dive is under way — the datum must freeze
    MAP.track.push({x:1,y:1,depth:0});
    const frozenLat=MAP.origin.lat;
    onLiveFix({coords:{latitude:51.5+d10*5, longitude:-0.1, accuracy:10}});
    await sleep(700);
    ok('launch point FREEZES once a track exists', MAP.origin.lat===frozenLat,
       'origin held at '+MAP.origin.lat.toFixed(6)+' while the handheld moved on');
    ok('but the handheld marker keeps moving', Math.abs(MAP.me.lat-(51.5+d10*5))<1e-9,
       'me now at '+MAP.me.lat.toFixed(6));

    // the tether anchor follows the operator, not the frozen datum
    const anch=tetherAnchorLocal();
    ok('tether anchors on the handheld, not the datum', Math.abs(anch.y-40)<1.5,
       'anchor is '+anch.y.toFixed(1)+' m north of the datum (walked ~40 m)');
    MAP.x=anch.x; MAP.y=anch.y; MAP.depth=0;
    await sleep(300);
    ok('range is measured from the anchor', Math.abs(tetherRangeM())<0.01,
       'sub sitting at the anchor reads '+tetherRangeM().toFixed(2)+' m of tether');

    // jitter must not rewrite the origin
    MAP.track.length=0;
    const beforeJit=MAP.origin.lat;
    onLiveFix({coords:{latitude:MAP.origin.lat+1/111320, longitude:-0.1, accuracy:10}});  // 1 m
    await sleep(500);
    ok('1 m of GPS jitter does not move the launch point', MAP.origin.lat===beforeJit,
       'origin unchanged at '+MAP.origin.lat.toFixed(6));

    // ---------- 5. nothing else broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('core UI intact',
       ['in-fwd','in-rev','in-left','in-right','sonar-vec','sonar-dot','sonar-teth','tether-warn',
        'nav-warning','radar','radar-dial','cam-capture','heading-val'].every(id=>!!$(id)), 'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

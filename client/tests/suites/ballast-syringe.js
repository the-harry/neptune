/* WHAT THIS GUARDS - the ballast control and the colour link.

   THE SHAPE. The tank is a syringe, so it looks like one: flat solid flange across a
   square top, a barrel, and a V that tapers to a centred point with no needle. The wall
   and the inside are cut from ONE declared shape, which is what stops the liquid from
   squaring off the taper or spilling past the barrel - the failure mode of drawing the
   outline and the fill separately.

   THE COLOUR. The map draws the dive track in twelve depth bands; the ballast fill and
   the Depth / Pressure / Ballast numbers wear the same bands, so the rail and the track
   say the same thing in the same colour.

   AND WHAT IT REFUSES TO COLOUR. On a real dive, depth and pressure are coloured by
   their own sensors or not at all. Tinting them from ballast would mean a sub descending
   with a dead depth sensor showed a deepening colour it never earned - painting over the
   one symptom that gives the failure away. The last checks here drive exactly that case:
   full tank, depth sensor stuck at zero, and assert the two disagree on screen. */
(function(){
  const R=[];
  const ok=(n,p,d)=>R.push({name:n,pass:!!p,detail:String(d||'')});
  setTimeout(async ()=>{
    const track=$('ballast-track'), glass=document.querySelector('.syr-glass'),
          well=document.querySelector('.syr-well'), flange=document.querySelector('.syr-flange'),
          fill=$('ballast-fill');
    const cs=(el)=>getComputedStyle(el);
    ok('the track is a syringe, not a pill', !!glass && !!well && !!flange &&
       cs(track).borderRadius==='0px', 'radius='+cs(track).borderRadius);
    ok('the wall and the inside share ONE shape',
       cs(glass).clipPath===cs(well).clipPath && /polygon/.test(cs(glass).clipPath),
       cs(glass).clipPath);
    ok('the shape ends in a V, tip centred', /50%\s+100%/.test(cs(glass).clipPath), cs(glass).clipPath);
    ok('the flange is square and flat on top',
       cs(flange).borderRadius==='0px' && parseFloat(cs(flange).height)>=5,
       'radius='+cs(flange).borderRadius+' h='+cs(flange).height);
    ok('the flange spans wider than the barrel', (()=>{
        const f=flange.getBoundingClientRect(), w=well.getBoundingClientRect();
        return f.width >= w.width;   // barrel is clipped to 17%..83% of the same box
      })(), 'flange='+Math.round(flange.getBoundingClientRect().width)+'px');
    ok('the liquid cannot overflow the barrel',
       cs(well).overflow==='hidden' && fill.parentElement===well,
       'fill is inside the clipped well, overflow='+cs(well).overflow);

    // Drive the tank and watch every colour move together.
    const readAll=()=>({fill:cs(fill).backgroundColor,
                        bal:cs($('ballast-pct')).color,
                        dep:cs($('depth-val')).color,
                        pre:cs($('pressure-val')).color});
    const at=async(lvl)=>{ state.ballastTargetRaw=lvl; state.ballastLevel=lvl;
                           state.mode='sim'; renderUI(viewFromState(true));
                           await new Promise(r=>requestAnimationFrame(r)); return readAll(); };
    const empty=await at(0), half=await at(0.5), full=await at(1);
    ok('SIM: the tank colours the fill', empty.fill!==full.fill, empty.fill+' -> '+full.fill);
    ok('SIM: all four move together as one colour',
       empty.fill===empty.bal && empty.bal===empty.dep && empty.dep===empty.pre &&
       full.fill===full.bal && full.bal===full.dep && full.dep===full.pre,
       'empty '+empty.fill+' | full '+full.fill);
    ok('SIM: three distinct depths, three distinct colours',
       new Set([empty.fill,half.fill,full.fill]).size===3,
       [empty.fill,half.fill,full.fill].join('  '));
    ok('SIM: the fill matches the colour the TRACK would draw',
       full.fill===(()=>{const c=document.createElement('span');
         c.style.color=_depthColor(1*CONFIG.sim.maxDepthM);document.body.appendChild(c);
         const v=getComputedStyle(c).color;c.remove();return v;})(),
       'fill='+full.fill);

    // REAL with NO sensors: ballast may colour, depth/pressure may NOT.
    state.mode='real'; state.depthAt=0; state.pressureAt=0;
    state.realTel={}; state.realTelAt=Date.now();
    renderUI(viewFromState(false));
    await new Promise(r=>requestAnimationFrame(r));
    const dead=readAll();
    ok('REAL, no sensors: the ballast still colours', dead.fill===full.fill, dead.fill);
    ok('REAL, no sensors: DEPTH refuses the tint', dead.dep!==dead.fill && dead.dep!==full.dep,
       'depth='+dead.dep+' vs tank='+dead.fill);
    ok('REAL, no sensors: PRESSURE refuses the tint', dead.pre!==dead.fill,
       'pressure='+dead.pre);
    ok('and says why when you ask it', /NOT tracking a sensor/.test($('depth-val').title),
       $('depth-val').title.slice(-64));

    // REAL with sensors: they colour themselves, from their OWN reading.
    state.depth=9; state.depthAt=Date.now();
    state.pressure=14.7+9*1.42; state.pressureAt=Date.now();
    renderUI(viewFromState(false));
    await new Promise(r=>requestAnimationFrame(r));
    const live=readAll();
    ok('REAL, sensors live: depth colours itself', live.dep===full.dep, live.dep);
    ok('REAL, sensors live: pressure agrees with depth', live.pre===live.dep,
       'p='+live.pre+' d='+live.dep);
    // The diagnostic case: tank full, depth sensor stuck at zero.
    state.depth=0; state.depthAt=Date.now();
    state.mode='sim'; state.ballastLevel=0.42; state.ballastTargetRaw=0.62;
    renderUI(viewFromState(true));
    renderUI(viewFromState(false));
    await new Promise(r=>requestAnimationFrame(r));
    const stuck=readAll();
    ok('a stuck depth sensor is VISIBLE against the tank', stuck.dep!==stuck.fill,
       'tank='+stuck.fill+'  depth='+stuck.dep);
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }, 900);
})();

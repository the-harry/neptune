/* STATUS + RAIL — the depth ramp's perceptual evenness (measured in Oklab from real
   pixels), the rail sized to its widest word, REC's four colour states, the single ROV
   icon whose SHAPE carries link+vehicle, and right-stick axis detection. The last one is
   why sideways panning appeared broken: the right stick is only axes 2/3 under the
   Gamepad API's standard mapping; a non-standard pad puts triggers in the axis list and
   moves the stick to 3/4. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const box=e=>e.getBoundingClientRect();
  const cs=e=>getComputedStyle(e);

  async function run(){
    await sleep(2600);

    // ---------- 1. depth ramp is EVEN ----------
    // Read REAL pixels. getComputedStyle('color') can hand back oklch()/color(srgb ...)
    // for modern syntax, and scraping digits out of that yields nonsense - which is how
    // this test reported an Oklab step of 1.979, a value the space cannot even hold.
    const _c=document.createElement('canvas'); _c.width=_c.height=1;
    const _x=_c.getContext('2d',{willReadFrequently:true});
    const toRGB=(h)=>{ _x.clearRect(0,0,1,1); _x.fillStyle='#000'; _x.fillStyle=h;
      _x.fillRect(0,0,1,1); const d=_x.getImageData(0,0,1,1).data;
      return [d[0],d[1],d[2]]; };
    // Measure evenness in OKLAB, not RGB: RGB distance is not what the eye reports, and
    // judging a perceptual ramp by it just reintroduces the bias being corrected.
    const oklab=(rgb)=>{
      const f=v=>{ v/=255; return v<=0.04045? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
      const r=f(rgb[0]), g=f(rgb[1]), b=f(rgb[2]);
      const l=Math.cbrt(0.4122214708*r+0.5363325363*g+0.0514459929*b);
      const m=Math.cbrt(0.2119034982*r+0.6806995451*g+0.1073969566*b);
      const s2=Math.cbrt(0.0883024619*r+0.2817188376*g+0.6299787005*b);
      return [0.2104542553*l+0.7936177850*m-0.0040720468*s2,
              1.9779984951*l-2.4285922050*m+0.4505937099*s2,
              0.0259040371*l+0.7827717662*m-0.8086757660*s2];
    };
    const rgbs=DEPTH_RAMP.map(toRGB);
    const labs=rgbs.map(oklab);
    const steps=[]; for(let i=1;i<labs.length;i++){
      const a=labs[i-1],b=labs[i];
      steps.push(Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]));
    }
    const mn=Math.min(...steps), mx=Math.max(...steps);
    ok('depth bands are evenly spaced (perceptually)', mx/mn < 1.6,
       DEPTH_RAMP.length+' bands, Oklab steps '+mn.toFixed(3)+'-'+mx.toFixed(3)+' (ratio '+(mx/mn).toFixed(2)+')');
    ok('no two bands collapse together', mn > 0.03, 'smallest Oklab step '+mn.toFixed(3));
    ok('band count is a sane dozen', DEPTH_RAMP.length===12, DEPTH_RAMP.length+' bands');
    ok('every band is a distinct colour', new Set(DEPTH_RAMP).size===DEPTH_RAMP.length,
       new Set(DEPTH_RAMP).size+' unique of '+DEPTH_RAMP.length);
    // Named ends: the surface band must actually be orange and the deepest purple.
    const hueOf=(rgb)=>{ const r=rgb[0]/255,g=rgb[1]/255,b=rgb[2]/255;
      const mx=Math.max(r,g,b), mn=Math.min(r,g,b), d=mx-mn;
      if(d===0) return -1;
      let h; if(mx===r) h=((g-b)/d)%6; else if(mx===g) h=(b-r)/d+2; else h=(r-g)/d+4;
      h*=60; return (h+360)%360; };
    const h0=hueOf(rgbs[0]), hN=hueOf(rgbs[rgbs.length-1]);
    ok('the surface band is ORANGE', h0>=15 && h0<=50, 'surface hue '+h0.toFixed(0)+'deg  '+DEPTH_RAMP[0]);
    ok('the deepest band is PURPLE', hN>=260 && hN<=330, 'deep hue '+hN.toFixed(0)+'deg  '+DEPTH_RAMP.at(-1));
    ok('hue sweeps further than before', Math.abs(((hN-h0)+720)%360)>200,
       'sweep '+Math.round(((hN-h0)+720)%360)+'deg of hue (was ~170)');
    ok('the deepest band is a clamp, and says so', _depthColor(999)===DEPTH_RAMP[DEPTH_RAMP.length-1],
       'anything past '+CONFIG.map.maxDepthColorM+' m -> last band, legend labels it "+ m"');

    // ---------- 2. rail is as wide as its widest word ----------
    const rail=document.querySelector('aside'), rb=box(rail);
    const label=[...rail.querySelectorAll('span')].find(s=>/BALLAST/.test(s.textContent));
    const lw=label? box(label).width : 0;
    ok('the rail is no wider than BALLAST needs', rb.width<=90,
       'rail '+Math.round(rb.width)+'px, "BALLAST" text '+Math.round(lw)+'px');
    ok('BALLAST still fits without clipping', label && label.scrollWidth<=Math.ceil(box(label).width)+1,
       label? ('scrollWidth '+label.scrollWidth+' vs box '+Math.round(box(label).width)) : 'label missing');
    const btns=['cam-rec','cam-capture','btn-surface','btn-config'].map(id=>box($(id)));
    ok('buttons span the rail width', btns.every(b=>Math.abs(b.width-(rb.width-12))<3),
       'button widths '+btns.map(b=>Math.round(b.width)).join(',')+' in a '+Math.round(rb.width)+'px rail');
    ok('the top bar meets the narrower rail', Math.abs(box($('topbar')).right-(innerWidth-84))<2,
       'bar right '+Math.round(box($('topbar')).right)+', rail starts '+(innerWidth-84));

    // ---------- 3. REC's four colours ----------
    const rec=$('cam-rec');
    const colourFor=async(st)=>{ rec.dataset.rec=st; rec.classList.toggle('recording', st==='partial'||st==='all');
      await sleep(650); return cs(rec).backgroundColor+' / '+cs(rec).color; };
    const nocam=await colourFor('nocam'), ready=await colourFor('ready');
    const part =await colourFor('partial'), all=await colourFor('all');
    const four=new Set([nocam,ready,part,all]);
    ok('REC has four distinct looks', four.size===4,
       'nocam '+nocam+' | ready '+ready+' | partial '+part+' | all '+all);
    ok('recording states are solid, idle states are not',
       /^rgb\(/.test(part.split(' / ')[0]) && /^rgb\(/.test(all.split(' / ')[0]) &&
       !/^rgb\(/.test(nocam.split(' / ')[0]),
       'partial/all opaque, nocam tinted');
    rec.dataset.rec='ready'; rec.classList.remove('recording');

    // ---------- 4. removals ----------
    ok('the little camera icon is gone', !$('st-cam'), 'st-cam absent');
    ok('no CAMERA OFFLINE banner', (()=>{
        STATUS.cam='down'; STATUS._lastGate=''; STATUS.applyGates();
        const b=$('controls-disabled');
        return !b || (!b.classList.contains('show') && b.textContent==='');
      })(), 'controls-disabled text="'+($('controls-disabled')||{}).textContent+'"');
    ok('the eye still reports the camera feed', !!$('st-video'), 'st-video present');

    // ---------- 4b. ONE ROV icon, three shapes ----------
    ok('link and vehicle are one icon now', !!$('st-rov') && !$('st-pi') && !$('st-veh'),
       'st-rov present; st-pi/st-veh gone');
    const rovState=(link,veh)=>{ STATUS.link=link; STATUS.vehicle=veh; STATUS.render();
      const el=$('st-rov'); return {cls:el.className, html:el.innerHTML}; };
    const off=rovState('offline','sim'), conn=rovState('connecting','sim'),
          on=rovState('online','idle'), leak=rovState('online','fault');
    ok('disconnected shows a RED robot', /rect/.test(off.html) && /sim/.test(off.cls),
       'class="'+off.cls+'" glyph=robot');
    ok('connecting shows an AMBER plug', /M12 10.5V4/.test(conn.html) && /warn/.test(conn.cls),
       'class="'+conn.cls+'" glyph=plug');
    ok('connected shows a GREEN sub', /M4 12c0-2.2/.test(on.html) && /ok/.test(on.cls),
       'class="'+on.cls+'" glyph=sub');
    ok('a leak keeps the sub but goes red', /M4 12c0-2.2/.test(leak.html) && /bad/.test(leak.cls),
       'class="'+leak.cls+'" — a fault must not look like a dropout');
    ok('all three states are visually distinct', new Set([off.html,conn.html,on.html]).size===3,
       'robot / plug / sub are three different shapes');

    // ---------- 5. right-stick axis resolution ----------
    const mk=(mapping,n)=>({index:0,id:'Pad',connected:true,mapping,
      buttons:Array.from({length:17},()=>({pressed:false,value:0})),
      axes:Array.from({length:n},()=>0)});
    ok('standard mapping uses axes 2/3', (()=>{ const r=rightStickAxes(mk('standard',4)); return r.x===2&&r.y===3; })(),
       JSON.stringify(rightStickAxes(mk('standard',4))));
    ok('a 6-axis non-standard pad uses 3/4', (()=>{ const r=rightStickAxes(mk('',6)); return r.x===3&&r.y===4; })(),
       JSON.stringify(rightStickAxes(mk('',6)))+' — triggers-in-axes layout');
    CONFIG.rightStickAxes={x:5,y:6};
    ok('an explicit override always wins', (()=>{ const r=rightStickAxes(mk('standard',4)); return r.x===5&&r.y===6; })(),
       JSON.stringify(rightStickAxes(mk('standard',4))));
    CONFIG.rightStickAxes=null;
    ok('there is a live axis diagnostic', typeof padAxes==='function' && typeof NEPTUNE.axes==='function',
       'NEPTUNE.axes() present');

    // both axes actually pan, on the detected pair
    try{ await STORE.set('origin',{lat:51.5,lon:-0.1,accuracy:8,source:'t',t:Date.now()}); }catch(e){}
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    CONFIG.map.blindNav=false; if(typeof exitBlindNav==='function') exitBlindNav();
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(400);
    const pad=mk('standard',4); navigator.getGamepads=()=>[pad]; state.gamepadIndex=0;
    const runAxis=async(ax)=>{ MAP.viewLat=51.5; MAP.viewLon=-0.1; MAP.follow=false; await sleep(200);
      pad.axes=ax.slice(); await sleep(600); pad.axes=[0,0,0,0]; await sleep(120);
      return { n:+((MAP.viewLat-51.5)*111320).toFixed(1),
               e:+((MAP.viewLon+0.1)*111320*Math.cos(51.5*Math.PI/180)).toFixed(1) }; };
    const rx=await runAxis([0,0,0.9,0]), ry=await runAxis([0,0,0,-0.9]);
    ok('stick X pans sideways', Math.abs(rx.e)>5 && Math.abs(rx.e)>Math.abs(rx.n),
       'axis2=0.9 -> east '+rx.e+' m, north '+rx.n+' m');
    ok('stick Y pans up/down', Math.abs(ry.n)>5 && Math.abs(ry.n)>Math.abs(ry.e),
       'axis3=-0.9 -> north '+ry.n+' m, east '+ry.e+' m');
    state.gamepadIndex=null; navigator.getGamepads=()=>[];

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

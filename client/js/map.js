"use strict";
/* ============================================================================
   MAP — full-screen canvas map behind the HUD (spec §6, §7). Shows the dive
   track (depth-coloured), a heading-rotated sub marker, the origin, and a grid
   basemap. Fed by /ws/nav when the backend is up, else by a client-side
   integrator from the SIM driving so it animates from disk too.

   §7 HUD-preservation, honoured here:
   - NO keyboard/gamepad handlers (§7.2) — map can't capture piloting input.
   - Only pointer events, scoped to the canvas (§7.3). HUD chrome is pointer-none.
   - Wrapped in a try/catch error boundary (§7.4) — a draw failure blanks the map,
     never the screen; the HUD keeps its own data path.
   - Redraw capped at CONFIG.map.redrawHz, track decimated (§7.5).

   NOTE: a vanilla canvas basemap stands in for the MapLibre + PMTiles substrate
   (§6.2) which must be vendored/extracted at bootstrap — TODO(bootstrap).
   ============================================================================ */
const MAP = {
  canvas:null, ctx:null, dpr:1,
  track:[],                 // [{x,y,depth}]
  x:0, y:0, hdg:0, depth:0,
  scale: 0.6,               // metres per pixel (set from CONFIG at init)
  view:'map',               // 'map' | 'video' background
  navWs:null, lastNavAt:0,
  lastTick:0, lastInputAt:0,
};
// theme colours (canvas can't read CSS vars cheaply)
const C = { grid:'rgba(180,107,255,0.10)', axis:'rgba(180,107,255,0.22)',
           origin:'#ff8c1a', sub:'#b46bff', shallow:'#4dffa6', deep:'#1f9dff',
           trackDim:'rgba(180,107,255,0.5)' };

function initMap(){
  MAP.canvas = $('map-canvas'); if(!MAP.canvas) return;
  MAP.ctx = MAP.canvas.getContext('2d');
  MAP.scale = CONFIG.map.metersPerPixel;
  resizeMap();
  window.addEventListener('resize', resizeMap);
  // zoom controls (pointer only — no keyboard, §7.2)
  const zi=$('map-zoom-in'), zo=$('map-zoom-out'), rc=$('map-recenter'), sw=$('view-swap');
  if(zi) zi.addEventListener('click', ()=>{ MAP.scale=Math.max(0.1, MAP.scale/1.3); });
  if(zo) zo.addEventListener('click', ()=>{ MAP.scale=Math.min(20, MAP.scale*1.3); });
  if(rc) rc.addEventListener('click', ()=>{ MAP.scale=CONFIG.map.metersPerPixel; });
  MAP.canvas.addEventListener('wheel', (e)=>{ e.preventDefault();
    MAP.scale = Math.max(0.1, Math.min(20, MAP.scale * (e.deltaY>0?1.1:0.9))); }, {passive:false});
  if(sw) sw.addEventListener('click', toggleMapView);
  const vl=$('video-layer'); if(vl) vl.addEventListener('click', ()=>{ if(MAP.view==='map') setView('video'); });  // tap PiP → camera
  connectNavWs();
  MAP.lastTick = performance.now();
  setInterval(mapTick, Math.round(1000/CONFIG.map.redrawHz));   // decoupled 10 Hz (§7.5)
  setView('map');
  LOG.state('map initialised');
}

function toggleMapView(){ setView(MAP.view==='map' ? 'video' : 'map'); }
function setView(v){
  MAP.view=v;
  document.body.classList.toggle('view-map', v==='map');
  document.body.classList.toggle('view-video', v==='video');
  const b=$('view-swap-label'); if(b) b.textContent = v==='map' ? 'CAM' : 'MAP';
}

function connectNavWs(){
  const base = state.wsBase || (location.host ? (location.protocol==='https:'?'wss':'ws')+'://'+location.host : '');
  if(!base) return;                        // disk with no host → client integrator only
  let ws; try{ ws=new WebSocket(base + CONFIG.map.navWs); }catch(e){ return; }
  MAP.navWs=ws;
  ws.onmessage=(ev)=>{ let m; try{ m=JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='nav'){
      MAP.x=m.x_m; MAP.y=m.y_m; MAP.hdg=m.heading_deg; MAP.depth=m.depth_m;
      MAP.lastNavAt=performance.now(); pushTrack(m.x_m, m.y_m, m.depth_m);
    }
  };
  ws.onclose=()=>{ MAP.navWs=null; setTimeout(connectNavWs, 3000); };
  ws.onerror=()=>{ try{ ws.close(); }catch(e){} };
}

function pushTrack(x,y,depth){
  const t=MAP.track, last=t[t.length-1];
  if(last && Math.hypot(x-last.x, y-last.y) < 0.25) return;   // skip tiny moves
  t.push({x,y,depth});
  if(t.length > CONFIG.map.maxTrackPoints){                    // decimate older half (§7.5)
    const keep=[]; for(let i=0;i<t.length;i++){ if(i > t.length/2 || i%2===0) keep.push(t[i]); }
    MAP.track = keep;
  }
}

function mapTick(){
  const now=performance.now();
  let dt=(now-MAP.lastTick)/1000; if(dt>0.5) dt=0.5; MAP.lastTick=now;
  // client-side integrator when the backend nav isn't feeding us (disk / no host)
  if(now - MAP.lastNavAt > 1500){
    MAP.hdg = state.heading;                                   // ROV sim compass heading
    const spd = (state.input.throttle||0) * CONFIG.map.subMaxSpeedMs;
    const h=MAP.hdg*Math.PI/180;
    MAP.x += spd*Math.sin(h)*dt;                               // east
    MAP.y += spd*Math.cos(h)*dt;                               // north
    MAP.depth = state.depth;
    pushTrack(MAP.x, MAP.y, MAP.depth);
  }
  // momentary direction overlay: visible on input, fades when idle
  if(Math.abs(state.input.throttle)+Math.abs(state.input.steer) > 0.03) MAP.lastInputAt=now;
  const wrap=$('sonar-wrap'); if(wrap) wrap.classList.toggle('idle', (now-MAP.lastInputAt) > CONFIG.map.sonarFadeMs);
  try{ drawMap(); }catch(e){ LOG.warn('map draw failed (HUD unaffected):', e && e.message); }  // §7.4
}

function resizeMap(){
  if(!MAP.canvas) return;
  MAP.dpr = window.devicePixelRatio || 1;
  MAP.canvas.width = Math.floor(innerWidth*MAP.dpr);
  MAP.canvas.height = Math.floor(innerHeight*MAP.dpr);
  MAP.canvas.style.width=innerWidth+'px'; MAP.canvas.style.height=innerHeight+'px';
}

function _depthColor(depth){
  const f=Math.max(0, Math.min(1, depth/CONFIG.map.maxDepthColorM));
  // shallow (green) → deep (blue)
  const a=[77,255,166], b=[31,157,255];
  return `rgb(${a[0]+(b[0]-a[0])*f|0},${a[1]+(b[1]-a[1])*f|0},${a[2]+(b[2]-a[2])*f|0})`;
}

function drawMap(){
  const ctx=MAP.ctx, w=MAP.canvas.width, h=MAP.canvas.height, dpr=MAP.dpr;
  ctx.setTransform(1,0,0,1,0,0);
  ctx.clearRect(0,0,w,h);
  const cx=w/2, cy=h/2, ppm=dpr/MAP.scale;                     // pixels per metre
  const sx = wx => cx + (wx - MAP.x)*ppm;
  const sy = wy => cy - (wy - MAP.y)*ppm;                      // north up

  // grid
  ctx.lineWidth=1*dpr;
  const g=CONFIG.map.gridMeters*ppm;
  const ox=((cx - MAP.x*ppm)%g+g)%g, oy=((cy + MAP.y*ppm)%g+g)%g;
  ctx.strokeStyle=C.grid;
  ctx.beginPath();
  for(let gx=ox; gx<w; gx+=g){ ctx.moveTo(gx,0); ctx.lineTo(gx,h); }
  for(let gy=oy; gy<h; gy+=g){ ctx.moveTo(0,gy); ctx.lineTo(w,gy); }
  ctx.stroke();

  // origin marker (launch point) at world (0,0)
  const oX=sx(0), oY=sy(0);
  ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
  ctx.moveTo(oX-7*dpr,oY); ctx.lineTo(oX+7*dpr,oY); ctx.moveTo(oX,oY-7*dpr); ctx.lineTo(oX,oY+7*dpr); ctx.stroke();
  ctx.beginPath(); ctx.arc(oX,oY,10*dpr,0,7); ctx.stroke();

  // track — depth-coloured segments
  const t=MAP.track;
  if(t.length>1){
    ctx.lineWidth=3*dpr; ctx.lineJoin='round';
    for(let i=1;i<t.length;i++){
      ctx.strokeStyle=_depthColor(t[i].depth);
      ctx.beginPath(); ctx.moveTo(sx(t[i-1].x),sy(t[i-1].y)); ctx.lineTo(sx(t[i].x),sy(t[i].y)); ctx.stroke();
    }
  }

  // sub marker — heading-rotated arrow at the sub position
  const pX=sx(MAP.x), pY=sy(MAP.y);
  ctx.save(); ctx.translate(pX,pY); ctx.rotate(MAP.hdg*Math.PI/180);   // 0=N=up
  ctx.fillStyle=C.sub; ctx.strokeStyle='#0c0118'; ctx.lineWidth=1.5*dpr;
  ctx.beginPath(); ctx.moveTo(0,-11*dpr); ctx.lineTo(7*dpr,9*dpr); ctx.lineTo(0,5*dpr); ctx.lineTo(-7*dpr,9*dpr); ctx.closePath();
  ctx.fill(); ctx.stroke();
  ctx.restore();
  ctx.fillStyle=C.sub; ctx.beginPath(); ctx.arc(pX,pY,3*dpr,0,7); ctx.fill();

  // scale bar
  ctx.setTransform(1,0,0,1,0,0);
  const barM=CONFIG.map.gridMeters, barPx=barM*ppm;
  ctx.strokeStyle='rgba(236,227,255,.5)'; ctx.fillStyle='rgba(236,227,255,.7)';
  ctx.lineWidth=2*dpr; ctx.beginPath(); ctx.moveTo(14*dpr,h-16*dpr); ctx.lineTo(14*dpr+barPx,h-16*dpr); ctx.stroke();
  ctx.font=`${11*dpr}px sans-serif`; ctx.fillText(`${barM} m`, 16*dpr, h-22*dpr);
}

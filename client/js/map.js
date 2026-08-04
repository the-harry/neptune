"use strict";
/* ============================================================================
   RADAR — GTA-style circular minimap (spec §2/§3). CAMERA is primary and fills
   the viewport; the map is a compact CIRCLE (bottom-left) with the live basemap
   clipped inside it and the heading needle / input vector drawn on top. There is
   exactly ONE map instance: collapsed it lives in the circle; clicking it EXPANDS
   the SAME instance to fullscreen (video → corner PiP, never unmounted).

   When maplibre-gl.js + pmtiles.js are vendored into client/vendor/ and an area
   is active, a real MapLibre+PMTiles basemap renders. Until then a canvas grid is
   the fallback, with honest empty states — never a plausible map that isn't
   tracking (§6). Heading-up by default (map rotates under a fixed forward marker);
   rotation uses the map's own bearing / a canvas transform, never a CSS transform
   on a square container (which would leave uncovered corners in the circle, §4).

   §5 HUD-safe: no keyboard/gamepad handlers; keyboard:false on MapLibre; handlers
   scoped to the radar; every draw wrapped in an error boundary; redraw ≤10 Hz. A
   map failure degrades to a blank circle with the needle + readouts intact.
   ============================================================================ */
const MAP = {
  panel:null, canvas:null, ctx:null, radar:null, dpr:1, ml:null,
  track:[], x:0, y:0, hdg:0, depth:0, scale:0.6, headingUp:true,
  expanded:false, hasArea:false, hasOrigin:false, origin:null, activeArea:null,
  navWs:null, lastNavAt:0, lastTick:0,
  viewLat:null, viewLon:null, follow:true,   // geographic view centre (§3); follow = track the sub
  centreline:null,                           // [[lon,lat],…] waterway overlay (§3.5)
  originTap:false,                           // one-shot: next map tap sets the origin (§2)
  drag:null, selReadout:null,                // pan drag state; area-selection readout callback (§4)
  replay:false,                              // viewing a saved dive — freeze live integration (§1)
};
const C = { grid:'rgba(180,107,255,0.13)', origin:'#ff8c1a', sub:'#b46bff',
           shallow:'#4dffa6', deep:'#1f9dff' };

function navFetch(path, opts){ return fetch((state.httpBase||'') + path, opts); }

function initMap(){
  MAP.panel = $('map-panel'); MAP.canvas = $('map-canvas'); MAP.radar = $('radar');
  if(!MAP.panel||!MAP.canvas) return;
  MAP.ctx = MAP.canvas.getContext('2d'); MAP.scale = CONFIG.map.metersPerPixel; MAP.headingUp = CONFIG.map.headingUp;
  document.documentElement.style.setProperty('--radar-px', (CONFIG.map.radarPx||200)+'px');
  resizeMap(); window.addEventListener('resize', resizeMap);

  // collapsed: clicking the circle opens the area manager / origin flow when empty (§2),
  // otherwise expands the map to fullscreen (§3).
  if(MAP.radar) MAP.radar.addEventListener('click', (e)=>{
    if(MAP.expanded || e.target.closest('.map-btn,.map-empty-btn')) return;
    if(!MAP.hasArea){ openAreaManager(); return; }
    if(!MAP.hasOrigin){ openOriginModal(); return; }
    expandMap();
  });
  const cl=$('map-close'); if(cl) cl.addEventListener('click', (e)=>{ e.stopPropagation(); collapseMap(); });
  document.addEventListener('keydown', (e)=>{ if(e.key==='Escape' && MAP.expanded && !typingInField(e)) collapseMap(); });
  $('video-layer').addEventListener('click', ()=>{ if(MAP.expanded) collapseMap(); });   // tap PiP video → back to camera
  // resize AFTER layout settles (§3 — else a grey half-render). transitionend is a backup to the rAF pass.
  MAP.panel.addEventListener('transitionend', (ev)=>{ if(ev.propertyName==='opacity'){ resizeMap(); if(MAP.ml) MAP.ml.resize(); } });
  // zoom (pointer only, §5)
  const zi=$('map-zoom-in'), zo=$('map-zoom-out'), rc=$('map-recenter');
  if(zi) zi.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=Math.max(0.1,MAP.scale/1.3); });
  if(zo) zo.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=Math.min(20,MAP.scale*1.3); });
  if(rc) rc.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=CONFIG.map.metersPerPixel; });
  MAP.canvas.addEventListener('wheel', (e)=>{ if(!MAP.expanded) return; e.preventDefault(); MAP.scale=Math.max(0.05,Math.min(40,MAP.scale*(e.deltaY>0?1.1:0.9))); }, {passive:false});
  // pan + tap (expanded only, canvas-scoped so it never touches piloting input, §5)
  MAP.canvas.addEventListener('pointerdown', (e)=>{ if(!MAP.expanded) return;
    MAP.drag={ x:e.clientX, y:e.clientY, moved:0, geo:screenToLatLon(MAP.canvas,e.clientX,e.clientY) };
    try{ MAP.canvas.setPointerCapture(e.pointerId); }catch(_){}
  });
  MAP.canvas.addEventListener('pointermove', (e)=>{ if(!MAP.expanded||!MAP.drag) return;
    MAP.drag.moved += Math.abs(e.clientX-MAP.drag.x)+Math.abs(e.clientY-MAP.drag.y);
    const g0=MAP.drag.geo, g1=screenToLatLon(MAP.canvas,e.clientX,e.clientY);   // keep grabbed point under the cursor
    if(g0&&g1&&MAP.viewLat!=null){ MAP.follow=false; MAP.viewLat+=(g0.lat-g1.lat); MAP.viewLon+=(g0.lon-g1.lon); }
    MAP.drag.x=e.clientX; MAP.drag.y=e.clientY;
  });
  const endDrag=(e)=>{ if(!MAP.drag) return; const tap=MAP.drag.moved<6; MAP.drag=null;
    if(tap && MAP.expanded) onMapTap(e.clientX,e.clientY);
  };
  MAP.canvas.addEventListener('pointerup', endDrag);
  MAP.canvas.addEventListener('pointercancel', ()=>{ MAP.drag=null; });
  // full empty-state action (expanded) → area manager (§5) or origin flow (§4)
  const eb=$('map-empty-btn'); if(eb) eb.addEventListener('click', (e)=>{ e.stopPropagation(); if(!MAP.hasArea) openAreaManager(); else openOriginModal(); });

  tryInitMapLibre();                 // real basemap when vendored + area active (§3)
  connectNavWs();
  refreshBootstrap();                // areas + origin → empty states + ORIGIN tile
  setInterval(refreshBootstrap, 5000);
  MAP.lastTick = performance.now();
  setInterval(mapTick, Math.round(1000/CONFIG.map.redrawHz));
  document.body.classList.add('map-collapsed');
  LOG.state('radar initialised (camera-primary; map in circle)');
}

/* ---- expand / collapse (§3). Same instance; video reparented by CSS, never torn down. ----
   Expanding issues a safe "all-stop" analogue of GTA's pause: throttle is held at zero
   (enforced every frame in input.js) and ALL STOP — MAP OPEN is shown. Telemetry, video,
   recording and safety indicators keep running at full rate. Any live drive input in
   input.js collapses the map instantly and returns control. */
function expandMap(){
  if(MAP.expanded) return;
  MAP.expanded=true; MAP.follow=false;                          // free pan in the expanded view (§4)
  document.body.classList.remove('map-collapsed'); document.body.classList.add('map-expanded');
  const banner=$('all-stop-banner'); if(banner) banner.classList.toggle('on', !!CONFIG.map.allStopOnExpand);
  if(MAP.ml){ try{ MAP.ml.setBearing(0); }catch(e){} }         // north-up + interactive when expanded
  updateEmptyState(); afterResize();
  LOG.state('map expanded — all-stop '+(CONFIG.map.allStopOnExpand?'engaged':'off'));
}
function collapseMap(){
  if(!MAP.expanded) return;
  MAP.expanded=false; MAP.follow=true;                          // radar re-centres on the sub
  document.body.classList.remove('map-expanded'); document.body.classList.add('map-collapsed');
  const banner=$('all-stop-banner'); if(banner) banner.classList.remove('on');
  MAP.scale=CONFIG.map.metersPerPixel;                          // radar back to its glance zoom
  updateEmptyState(); afterResize();
  LOG.state('map collapsed — control returned');
}
/* a non-drag tap in the expanded map: place the origin when armed (§2) */
function onMapTap(clientX, clientY){
  if(!MAP.originTap) return;
  const g=screenToLatLon(MAP.canvas, clientX, clientY); if(!g) return;
  MAP.originTap=false;
  setOrigin({ lat:g.lat, lon:g.lon, accuracy:8, source:'map_tap', t:Date.now() }).then(ok=>{
    if(ok!==false){ MAP.x=0; MAP.y=0; MAP.follow=true; if(typeof hideOriginPrompt==='function') hideOriginPrompt(); }
  });
}
/* §4: the bbox currently inside the fixed selection rectangle (expanded view). */
function mapSelectionRectPx(){
  const w=MAP.canvas.width, h=MAP.canvas.height, m=Math.min(w,h)*0.18;   // inset from the edges
  return { x0:m, y0:m, x1:w-m, y1:h-m };
}
function mapSelectionBBox(){
  if(!MAP.expanded || !TILES.last) return null;
  const r=MAP.canvas.getBoundingClientRect(), dpr=MAP.dpr;
  const px=mapSelectionRectPx();
  // rect corners in CLIENT px → lat/lon (screenToLatLon expects client coords)
  const toClient=(dx,dy)=>({ x:r.left+dx/dpr, y:r.top+dy/dpr });
  const a=toClient(px.x0,px.y0), b=toClient(px.x1,px.y1), c=toClient(px.x1,px.y0), d=toClient(px.x0,px.y1);
  const pts=[a,b,c,d].map(p=>screenToLatLon(MAP.canvas,p.x,p.y)).filter(Boolean);
  if(pts.length<4) return null;
  const lons=pts.map(p=>p.lon), lats=pts.map(p=>p.lat);
  return [Math.min(...lons),Math.min(...lats),Math.max(...lons),Math.max(...lats)];
}
function afterResize(){   // two rAFs so the panel is at its final size before we resize the canvas / GL (§3)
  requestAnimationFrame(()=>requestAnimationFrame(()=>{ resizeMap(); if(MAP.ml){ try{ MAP.ml.resize(); }catch(e){} } }));
}

/* ---- bootstrap state → honest empty states (§6) + ORIGIN tile (§4) ---- */
async function refreshBootstrap(){
  // CLIENT-FIRST (architectural rule): areas + origin come from local storage and work
  // with the Pi off. The Pi is never consulted here — its data is the mirror, not the source.
  try{
    if(typeof STORE!=='undefined'){
      const areas = await STORE.areas();
      if(!MAP.activeArea && areas.length){                     // default-activate the most recent save
        const newest = areas.slice().sort((a,b)=>(b.savedAt||0)-(a.savedAt||0))[0];
        MAP.activeArea = newest.name; MAP.viewLat=(newest.bbox[1]+newest.bbox[3])/2; MAP.viewLon=(newest.bbox[0]+newest.bbox[2])/2;
      }
      MAP.hasArea = areas.some(a=>a.name===MAP.activeArea) || areas.length>0;
      if(MAP.activeArea && MAP._clArea!==MAP.activeArea){ MAP._clArea=MAP.activeArea; loadCentreline(MAP.activeArea); }   // Pi-side overlay if reachable
    }
  }catch(e){ MAP.hasArea=false; }
  try{
    if(typeof STORE!=='undefined'){
      const o = await STORE.get('origin', null);
      MAP.hasOrigin = !!(o && typeof o.lat==='number'); MAP.origin = MAP.hasOrigin ? o : null;
    }
  }catch(e){}
  renderOriginTile();
  updateEmptyState();
}
async function loadCentreline(name){
  MAP.centreline=null; if(!name) return;
  try{
    const r=await navFetch('/api/areas/'+encodeURIComponent(name)+'/centreline');
    if(!r.ok) return; const gj=await r.json();
    const lines=[];
    const walk=(g)=>{ if(!g) return; const t=g.type;
      if(t==='LineString') lines.push(g.coordinates);
      else if(t==='MultiLineString') g.coordinates.forEach(l=>lines.push(l));
      else if(t==='Feature') walk(g.geometry);
      else if(t==='FeatureCollection') g.features.forEach(walk); };
    walk(gj); MAP.centreline=lines;   // array of [ [lon,lat], … ] polylines
  }catch(e){ MAP.centreline=null; }
}
function renderOriginTile(){
  const el=$('origin-val'); if(!el) return;
  if(MAP.hasOrigin){ el.textContent='SET ±'+Math.round(MAP.origin.accuracy)+'m'; el.style.color='var(--tertiary)'; }
  else { el.textContent='NOT SET'; el.style.color='var(--secondary)'; }
}
function updateEmptyState(){
  const empty = !MAP.hasArea || !MAP.hasOrigin;
  if(MAP.radar) MAP.radar.classList.toggle('empty', empty);              // compact NO MAP / NO ORIGIN in the circle
  const compact=$('radar-empty'); if(compact) compact.innerHTML = !MAP.hasArea ? 'NO&nbsp;MAP' : 'NO&nbsp;ORIGIN';
  const full=$('map-empty'), msg=$('map-empty-msg'), btn=$('map-empty-btn');  // full explanation in the expanded view
  // …but don't dim the imagery while the operator is actively tapping an origin or selecting an area
  const suppress = MAP.expanded && (MAP.originTap || MAP.selectMode);
  if(full){
    full.classList.toggle('on', empty && !suppress);
    if(!MAP.hasArea){ if(msg)msg.textContent='NO MAP AREA LOADED'; if(btn)btn.textContent='LOAD OR DOWNLOAD'; }
    else if(!MAP.hasOrigin){ if(msg)msg.textContent='ORIGIN NOT SET'; if(btn)btn.textContent='SET ORIGIN'; }
  }
}

/* ---- MapLibre + PMTiles (§3) — only when vendored ---- */
function tryInitMapLibre(){
  if(typeof window.maplibregl === 'undefined'){ LOG.state('MapLibre not vendored — canvas fallback'); return; }
  try{
    if(window.pmtiles && window.maplibregl.addProtocol){
      const p = new window.pmtiles.Protocol();
      window.maplibregl.addProtocol('pmtiles', p.tile);   // Range-request PMTiles (§3)
    }
    MAP.canvas.style.display='none';
    MAP.ml = new window.maplibregl.Map({
      container:'maplibre-map', keyboard:false,           // §5 — never captures piloting keys
      center:[0,0], zoom:15, attributionControl:false, dragRotate:false,
      style:{ version:8, glyphs:'vendor/fonts/{fontstack}/{range}.pbf', sources:{}, layers:[
        { id:'bg', type:'background', paint:{ 'background-color':'#0c0118' } } ] },
    });
    MAP.ml.on('load', ()=>{ if(MAP.activeArea) setMapLibreArea(MAP.activeArea); });
  }catch(e){ LOG.warn('MapLibre init failed — canvas fallback:', e && e.message); MAP.ml=null; MAP.canvas.style.display='block'; }
}
function setMapLibreArea(name){
  if(!MAP.ml) return;
  try{
    if(MAP.ml.getSource('base')) return;
    MAP.ml.addSource('base', { type:'vector', url:'pmtiles://areas/'+name+'.pmtiles' });
    // TODO(vendor): add basemap fill/line layers per the PMTiles schema. Collapsed style is
    // simplified (water + major ways + centreline only, no labels, §4); full labels on expand.
    MAP.ml.addSource('centreline', { type:'geojson', data:(state.httpBase||'')+'/areas/'+name+'.geojson' });
    MAP.ml.addLayer({ id:'centreline', type:'line', source:'centreline',
      paint:{ 'line-color':'#1f9dff', 'line-width':2 } });
    MAP.ml.addSource('track', { type:'geojson', data:{type:'FeatureCollection',features:[]} });
    MAP.ml.addLayer({ id:'track', type:'line', source:'track', paint:{ 'line-color':'#b46bff','line-width':3 } });
  }catch(e){ LOG.warn('MapLibre area layers:', e && e.message); }
}

function connectNavWs(){
  const base = state.wsBase || (location.host ? (location.protocol==='https:'?'wss':'ws')+'://'+location.host : '');
  if(!base) return;
  let ws; try{ ws=new WebSocket(base + CONFIG.map.navWs); }catch(e){ return; }
  MAP.navWs=ws;
  ws.onmessage=(ev)=>{ let m; try{ m=JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='nav'){ MAP.x=m.x_m; MAP.y=m.y_m; MAP.hdg=m.heading_deg; MAP.depth=m.depth_m; MAP.lastNavAt=performance.now(); pushTrack(m.x_m,m.y_m,m.depth_m); }
  };
  ws.onclose=()=>{ MAP.navWs=null; setTimeout(connectNavWs,3000); };
  ws.onerror=()=>{ try{ ws.close(); }catch(e){} };
}
function pushTrack(x,y,depth){
  if(MAP.replay || !MAP.hasOrigin) return;                // no track without an origin (§6); frozen during replay
  const t=MAP.track, last=t[t.length-1];
  if(last && Math.hypot(x-last.x,y-last.y)<0.25) return;
  t.push({x,y,depth});
  if(t.length>CONFIG.map.maxTrackPoints){ const keep=[]; for(let i=0;i<t.length;i++){ if(i>t.length/2||i%2===0) keep.push(t[i]); } MAP.track=keep; }
}

function mapTick(){
  const now=performance.now(); let dt=(now-MAP.lastTick)/1000; if(dt>0.5)dt=0.5; MAP.lastTick=now;
  if(!MAP.replay && now-MAP.lastNavAt>1500){             // client integrator (disk/SIM fallback; paused during replay)
    MAP.hdg=state.heading; const spd=(state.input.throttle||0)*CONFIG.map.subMaxSpeedMs; const h=MAP.hdg*Math.PI/180;
    MAP.x+=spd*Math.sin(h)*dt; MAP.y+=spd*Math.cos(h)*dt; MAP.depth=state.depth; pushTrack(MAP.x,MAP.y,MAP.depth);
  }
  // view centre (§3): follow the sub when collapsed/following; free pan otherwise
  if(MAP.hasOrigin && (MAP.follow || MAP.viewLat==null)){
    const g=toLatLon(MAP.x,MAP.y,MAP.origin.lat,MAP.origin.lon); MAP.viewLat=g.lat; MAP.viewLon=g.lon;
  }
  // heading-up: the North indicator on the ring rotates opposite to heading (FWD stays up)
  const north=$('radar-north');
  if(north){ const rot=(!MAP.expanded && MAP.headingUp)? -MAP.hdg : 0; north.setAttribute('transform','rotate('+rot.toFixed(1)+')'); }
  if(MAP.ml && !MAP.expanded && MAP.headingUp){ try{ MAP.ml.setBearing(MAP.hdg); }catch(e){} }
  if(!MAP.ml){ try{ drawCanvas(); }catch(e){ LOG.warn('map draw failed (HUD unaffected):', e && e.message); } }  // §5.4
}

function resizeMap(){
  if(!MAP.canvas||!MAP.panel) return;
  const r=MAP.panel.getBoundingClientRect();
  MAP.dpr=window.devicePixelRatio||1;
  MAP.canvas.width=Math.max(1,Math.floor(r.width*MAP.dpr)); MAP.canvas.height=Math.max(1,Math.floor(r.height*MAP.dpr));
  MAP.canvas.style.width=r.width+'px'; MAP.canvas.style.height=r.height+'px';
}
function _depthColor(d){ const f=Math.max(0,Math.min(1,d/CONFIG.map.maxDepthColorM)); const a=[77,255,166],b=[31,157,255];
  return `rgb(${a[0]+(b[0]-a[0])*f|0},${a[1]+(b[1]-a[1])*f|0},${a[2]+(b[2]-a[2])*f|0})`; }

/* meters that render ≈px pixels at the current zoom, snapped to 1/2/5×10ⁿ (nice scale bar) */
function niceMeters(px, ppmCss){
  const raw=px/ppmCss; if(!(raw>0)||!isFinite(raw)) return 10;
  const pw=Math.pow(10,Math.floor(Math.log10(raw))), n=raw/pw;
  return (n<1.5?1:n<3.5?2:n<7.5?5:10)*pw;
}
function fmtMeters(m){ return m>=1000 ? (Math.round(m/100)/10)+' km' : Math.round(m)+' m'; }

function drawCanvas(){
  const ctx=MAP.ctx,w=MAP.canvas.width,h=MAP.canvas.height,dpr=MAP.dpr;
  ctx.setTransform(1,0,0,1,0,0); ctx.fillStyle='#0c0118'; ctx.fillRect(0,0,w,h);
  const cx=w/2,cy=h/2, ppm=dpr/MAP.scale;                  // device px per metre
  const headingUp = !MAP.expanded && MAP.headingUp;
  const rot = headingUp ? -MAP.hdg*Math.PI/180 : 0;
  const center = (MAP.viewLat!=null)? { lat:MAP.viewLat, lon:MAP.viewLon } : null;
  const prov = _provider(MAP.activeArea);                  // offline tiles when an area is active, else online
  const haveProj = !!center && !!prov.url;                 // satellite projection available this frame

  // 1) satellite imagery (§3)
  let drewTiles=false;
  if(haveProj){ try{ drewTiles=drawTiles(ctx,w,h,center.lat,center.lon,MAP.scale,rot,dpr,MAP.activeArea); }catch(e){ LOG.warn('tiles:',e&&e.message); } }

  // 2) readability tint over imagery (§5) — darker in the radar, lighter expanded
  if(drewTiles){ ctx.setTransform(1,0,0,1,0,0);
    ctx.fillStyle='rgba(6,2,16,'+(MAP.expanded?CONFIG.map.tintExpanded:CONFIG.map.tintCollapsed)+')'; ctx.fillRect(0,0,w,h); }

  // scale label (updates with zoom, §4)
  const gm = niceMeters(52, 1/MAP.scale);
  const sc=$('radar-scale'); if(sc) sc.innerHTML=fmtMeters(gm).replace(' ','&nbsp;');

  // 3) reference grid (subordinate) — meter frame, rotated. Fainter over imagery.
  let vx=0,vy=0;
  if(MAP.hasOrigin && center){ const p=toLocal(center.lat,center.lon,MAP.origin.lat,MAP.origin.lon); vx=p.x; vy=p.y; }
  if(MAP.hasOrigin || !haveProj){
    ctx.save(); ctx.translate(cx,cy); ctx.rotate(rot);
    ctx.strokeStyle = drewTiles ? 'rgba(180,107,255,0.12)' : C.grid; ctx.lineWidth=1*dpr;
    const gpx=gm*ppm, maxR=Math.hypot(w,h)/2+gpx; ctx.beginPath();
    const kx0=Math.floor((vx-maxR/ppm)/gm), kx1=Math.ceil((vx+maxR/ppm)/gm);
    for(let k=kx0;k<=kx1;k++){ const lx=(k*gm-vx)*ppm; ctx.moveTo(lx,-maxR); ctx.lineTo(lx,maxR); }
    const ky0=Math.floor((vy-maxR/ppm)/gm), ky1=Math.ceil((vy+maxR/ppm)/gm);
    for(let k=ky0;k<=ky1;k++){ const ly=-(k*gm-vy)*ppm; ctx.moveTo(-maxR,ly); ctx.lineTo(maxR,ly); }
    ctx.stroke(); ctx.restore();
  }

  // 4) waterway centreline over imagery (§3.5) — the snapping target + channel outline
  if(haveProj && MAP.centreline) drawCentreline(ctx,dpr);

  // 5) origin marker + dive track + sub marker — only with an origin (§6)
  if(MAP.hasOrigin){
    if(haveProj && TILES.last) drawTrackProjected(ctx,dpr,headingUp);
    else drawTrackMeterFrame(ctx,cx,cy,ppm,rot,headingUp);
  }

  // 6) area-selection rectangle + live readout (§4, expanded select mode)
  if(MAP.expanded && MAP.selectMode){ drawSelectionRect(ctx,dpr); if(MAP.selReadout) MAP.selReadout(mapSelectionBBox()); }

  // 7) imagery attribution (expanded)
  if(MAP.expanded && drewTiles) drawAttribution(ctx,w,h,dpr);
}

/* --- overlay helpers (projected = placed via lonLatToScreen so they sit on imagery) --- */
function _subLL(){ return toLatLon(MAP.x,MAP.y,MAP.origin.lat,MAP.origin.lon); }
function drawCentreline(ctx,dpr){
  ctx.setTransform(1,0,0,1,0,0); ctx.lineJoin='round'; ctx.lineCap='round';
  for(const line of MAP.centreline){
    if(!line||line.length<2) continue;
    ctx.beginPath(); let started=false;
    for(const c of line){ const s=lonLatToScreen(c[1],c[0]); if(!s) continue; if(!started){ctx.moveTo(s[0],s[1]);started=true;} else ctx.lineTo(s[0],s[1]); }
    ctx.strokeStyle='rgba(0,0,0,.55)'; ctx.lineWidth=5*dpr; ctx.stroke();   // casing
    ctx.strokeStyle=C.deep;           ctx.lineWidth=2*dpr; ctx.stroke();    // core
  }
}
function drawTrackProjected(ctx,dpr,headingUp){
  ctx.setTransform(1,0,0,1,0,0);
  // origin cross
  const oS=lonLatToScreen(MAP.origin.lat,MAP.origin.lon);
  if(oS){ ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
    ctx.moveTo(oS[0]-7*dpr,oS[1]);ctx.lineTo(oS[0]+7*dpr,oS[1]);ctx.moveTo(oS[0],oS[1]-7*dpr);ctx.lineTo(oS[0],oS[1]+7*dpr);ctx.stroke();
    ctx.beginPath();ctx.arc(oS[0],oS[1],10*dpr,0,7);ctx.stroke(); }
  // track: dark casing under a depth-coloured core (§5 legibility over imagery)
  const t=MAP.track, step=Math.max(1,Math.floor(t.length/600));   // decimate for display (§4/§5)
  if(t.length>1){
    const pts=[]; pts.push(lonLatToScreen(...llOf(t[0])));
    for(let i=step;i<t.length;i+=step) pts.push(lonLatToScreen(...llOf(t[i])));
    ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.strokeStyle='rgba(0,0,0,.6)'; ctx.lineWidth=6*dpr; ctx.beginPath();
    for(let i=0;i<pts.length;i++){ const p=pts[i]; if(!p)continue; i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]); } ctx.stroke();
    ctx.lineWidth=3*dpr;
    for(let i=1;i<pts.length;i++){ const a=pts[i-1],b=pts[i]; if(!a||!b)continue;
      ctx.strokeStyle=_depthColor((t[Math.min(t.length-1,i*step)]||t[t.length-1]).depth);
      ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); }
  }
  // sub marker
  const g=_subLL(), s=lonLatToScreen(g.lat,g.lon);
  if(s){ ctx.save(); ctx.translate(s[0],s[1]); ctx.rotate(headingUp?0:MAP.hdg*Math.PI/180);
    ctx.fillStyle=C.sub; ctx.strokeStyle='#0c0118'; ctx.lineWidth=1.5*dpr;
    ctx.beginPath(); ctx.moveTo(0,-11*dpr); ctx.lineTo(7*dpr,9*dpr); ctx.lineTo(0,5*dpr); ctx.lineTo(-7*dpr,9*dpr); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore(); }
}
function llOf(p){ const g=toLatLon(p.x,p.y,MAP.origin.lat,MAP.origin.lon); return [g.lat,g.lon]; }
/* fallback when no imagery: meter frame centred on the sub (SIM / no-basemap) */
function drawTrackMeterFrame(ctx,cx,cy,ppm,rot,headingUp){
  const dpr=MAP.dpr;
  ctx.save(); ctx.translate(cx,cy); ctx.rotate(rot);
  const L=(wx,wy)=>[ (wx-MAP.x)*ppm, -(wy-MAP.y)*ppm ];
  const o=L(0,0); ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
  ctx.moveTo(o[0]-7*dpr,o[1]);ctx.lineTo(o[0]+7*dpr,o[1]);ctx.moveTo(o[0],o[1]-7*dpr);ctx.lineTo(o[0],o[1]+7*dpr);ctx.stroke();
  ctx.beginPath();ctx.arc(o[0],o[1],10*dpr,0,7);ctx.stroke();
  const t=MAP.track, step=Math.max(1,Math.floor(t.length/600));
  if(t.length>1){ ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.strokeStyle='rgba(0,0,0,.5)'; ctx.lineWidth=6*dpr; ctx.beginPath(); let p0=L(t[0].x,t[0].y); ctx.moveTo(p0[0],p0[1]);
    for(let i=step;i<t.length;i+=step){ const p=L(t[i].x,t[i].y); ctx.lineTo(p[0],p[1]); } ctx.stroke();
    ctx.lineWidth=3*dpr; let prev=L(t[0].x,t[0].y);
    for(let i=step;i<t.length;i+=step){ const p=L(t[i].x,t[i].y); ctx.strokeStyle=_depthColor(t[i].depth);
      ctx.beginPath(); ctx.moveTo(prev[0],prev[1]); ctx.lineTo(p[0],p[1]); ctx.stroke(); prev=p; } }
  ctx.restore();
  ctx.setTransform(1,0,0,1,0,0); ctx.save(); ctx.translate(cx,cy); ctx.rotate(headingUp?0:MAP.hdg*Math.PI/180);
  ctx.fillStyle=C.sub; ctx.strokeStyle='#0c0118'; ctx.lineWidth=1.5*dpr;
  ctx.beginPath(); ctx.moveTo(0,-11*dpr); ctx.lineTo(7*dpr,9*dpr); ctx.lineTo(0,5*dpr); ctx.lineTo(-7*dpr,9*dpr); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
}
function drawSelectionRect(ctx,dpr){
  const px=mapSelectionRectPx(), w=ctx.canvas.width, h=ctx.canvas.height;
  ctx.setTransform(1,0,0,1,0,0);
  ctx.fillStyle='rgba(6,2,16,.4)';                          // dim outside the selection
  ctx.fillRect(0,0,w,px.y0); ctx.fillRect(0,px.y1,w,h-px.y1);
  ctx.fillRect(0,px.y0,px.x0,px.y1-px.y0); ctx.fillRect(px.x1,px.y0,w-px.x1,px.y1-px.y0);
  ctx.strokeStyle=C.shallow; ctx.lineWidth=2*dpr; ctx.setLineDash([9*dpr,6*dpr]);
  ctx.strokeRect(px.x0,px.y0,px.x1-px.x0,px.y1-px.y0); ctx.setLineDash([]);
}
function drawAttribution(ctx,w,h,dpr){
  const s=tileAttribution(); if(!s) return;
  ctx.setTransform(1,0,0,1,0,0); ctx.font=(11*dpr)+'px sans-serif';
  const tw=ctx.measureText(s).width;
  ctx.fillStyle='rgba(6,2,16,.5)'; ctx.fillRect(6*dpr,h-20*dpr,tw+12*dpr,16*dpr);
  ctx.fillStyle='rgba(236,227,255,.8)'; ctx.fillText(s, 12*dpr, h-8*dpr);
}

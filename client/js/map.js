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
  navWs:null, lastNavAt:0, lastTick:0, _navBits:-1,   // _navBits: last NO-NAV label state
  me:null,                                   // {lat,lon,acc,t} — the handheld, live (§2)
  viewLat:null, viewLon:null, follow:true,   // geographic view centre (§3); follow = track the sub
  centreline:null,                           // [[lon,lat],…] waterway overlay (§3.5)
  originTap:false,                           // one-shot: next map tap sets the origin (§2)
  drag:null, selReadout:null,                // pan drag state; area-selection readout callback (§4)
  replay:false,                              // viewing a saved dive — freeze live integration (§1)
  blind:false, blindSince:0, videoOkSince:0, videoWasLive:false,   // BLIND NAV (map as the driving view)
};
const C = { grid:'rgba(180,107,255,0.13)', origin:'#ff8c1a', sub:'#b46bff',
           me:'#1f9dff',                       // the handheld — the operator, live
           shallow:'#4dffa6', deep:'#1f9dff' };

function navFetch(path, opts){ return fetch((state.httpBase||'') + path, opts); }

/* Metres per pixel for whichever view is on screen. The radar never follows the big
   map's zoom - that is the whole point of it being a glance instrument. */
function curScale(){ return (MAP.expanded || MAP.blind) ? MAP.scale : MAP.radarScale; }

function initMap(){
  MAP.panel = $('map-panel'); MAP.canvas = $('map-canvas'); MAP.radar = $('radar');
  if(!MAP.panel||!MAP.canvas) return;
  MAP.ctx = MAP.canvas.getContext('2d');
  // TWO scales, on purpose. The radar circle is a GLANCE instrument: it must mean the
  // same thing every time you look at it, so it keeps its own fixed metres-per-pixel.
  // The full-screen views (expanded, blind nav) are explorable and get the adjustable
  // one. Sharing a single value meant zooming the big map silently rescaled the radar -
  // and after blind nav gained zoom controls, that happened constantly.
  MAP.scale = CONFIG.map.metersPerPixel;        // expanded / blind nav (zoomable)
  MAP.radarScale = CONFIG.map.radarMetersPerPixel || CONFIG.map.metersPerPixel;  // collapsed radar (fixed glance zoom)
  MAP.headingUp = CONFIG.map.headingUp;
  document.documentElement.style.setProperty('--radar-px', (CONFIG.map.radarPx||200)+'px');
  resizeMap(); window.addEventListener('resize', resizeMap);

  // collapsed: clicking the circle opens the area manager / origin flow when empty (§2),
  // otherwise expands the map to fullscreen (§3).
  if(MAP.radar) MAP.radar.addEventListener('click', (e)=>{
    if(MAP.expanded || e.target.closest('.map-btn,.map-empty-btn')) return;
    // In BLIND NAV the ring covers the whole viewport, so a stray tap would otherwise
    // expand the map - which engages ALL-STOP. Never zero the throttle by accident
    // while the operator is driving on this view.
    if(MAP.blind) return;
    if(!MAP.hasArea){ openAreaManager(); return; }
    if(!MAP.hasOrigin){ openOriginModal(); return; }
    expandMap();
  });
  const cl=$('map-close'); if(cl) cl.addEventListener('click', (e)=>{ e.stopPropagation();
    // Only meaningful for the EXPANDED map. In blind nav there is nothing to close:
    // leaving would land on a full-screen NO FEED, which is strictly less useful than
    // the map. The button is hidden there (see styles.css) and this is a belt-and-braces
    // no-op in case it is reached another way.
    if(MAP.blind && !MAP.expanded) return;
    collapseMap(); });
  document.addEventListener('keydown', (e)=>{ if(e.key==='Escape' && MAP.expanded && !typingInField(e)) collapseMap(); });
  $('video-layer').addEventListener('click', ()=>{
    if(MAP.expanded){ collapseMap(); return; }                 // tap PiP video → back to camera
    // In BLIND NAV the tile is a STATUS indicator, not a way out. It used to opt out of
    // blind nav, which returned the operator to a full-screen NO FEED — a dead end with
    // strictly less information than the map it replaced. The feed coming back is what
    // restores the camera view, and that is automatic.
  });
  // resize AFTER layout settles (§3 — else a grey half-render). transitionend is a backup to the rAF pass.
  MAP.panel.addEventListener('transitionend', (ev)=>{ if(ev.propertyName==='opacity'){ resizeMap(); if(MAP.ml) MAP.ml.resize(); } });
  // zoom (pointer only, §5)
  const zi=$('map-zoom-in'), zo=$('map-zoom-out'), rc=$('map-recenter');
  if(zi) zi.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=Math.max(0.1,MAP.scale/1.3); });
  if(zo) zo.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=Math.min(20,MAP.scale*1.3); });
  if(rc) rc.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=CONFIG.map.metersPerPixel; });
  MAP.canvas.addEventListener('wheel', (e)=>{ if(!MAP.expanded && !MAP.blind) return; e.preventDefault(); MAP.scale=Math.max(0.05,Math.min(40,MAP.scale*(e.deltaY>0?1.1:0.9))); }, {passive:false});
  // drag-to-pan + tap (expanded only, canvas-scoped so it never touches piloting input, §5).
  // Pan is computed absolutely from the drag-start centre + total screen delta (no feedback loop),
  // so it's smooth like a real slippy map.
  MAP.canvas.addEventListener('pointerdown', (e)=>{ if(!MAP.expanded) return;
    MAP.drag={ x:e.clientX, y:e.clientY, moved:0, lat0:MAP.viewLat, lon0:MAP.viewLon };
    try{ MAP.canvas.setPointerCapture(e.pointerId); }catch(_){}
  });
  MAP.canvas.addEventListener('pointermove', (e)=>{ if(!MAP.expanded||!MAP.drag) return;
    MAP.drag.moved += Math.abs(e.clientX-MAP.drag.x)+Math.abs(e.clientY-MAP.drag.y);
    const L=TILES.last; if(!L || MAP.drag.lat0==null) return;
    const dxd=(e.clientX-MAP.drag.x)*MAP.dpr, dyd=(e.clientY-MAP.drag.y)*MAP.dpr;   // total device-px delta since grab
    const c=Math.cos(-L.rot), s=Math.sin(-L.rot);                                    // undo heading-up rotation
    const rx=dxd*c - dyd*s, ry=dxd*s + dyd*c;
    const mercX0=lonToMercX(MAP.drag.lon0), mercY0=latToMercY(MAP.drag.lat0);
    MAP.follow=false;
    MAP.viewLon = mercXToLon(mercX0 - rx/L.k/L.worldTP);    // drag right → view moves left
    MAP.viewLat = mercYToLat(mercY0 - ry/L.k/L.worldTP);
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
/* ============================================================================
   BLIND NAV — the feed is gone, so drive on the map.

   Explicitly NOT expandMap(): that engages all-stop and goes north-up because it is
   a planning view. This is a DRIVING view, so MAP.expanded stays false and every
   behaviour keyed on it stays in its piloting form — heading-up, following the sub,
   throttle live, no all-stop. Only the layout changes.
   ============================================================================ */
function enterBlindNav(){
  if(MAP.blind || MAP.expanded) return;
  MAP.blind = true; MAP.blindSince = Date.now(); MAP.follow = true;
  document.body.classList.add('map-blind');
  afterResize();
  // Pick a DRIVING zoom rather than inheriting the big map's. Derived from the real
  // canvas so it spans the same ground distance on any display; the operator can zoom
  // from there and it is re-derived on the next entry.
  requestAnimationFrame(()=>{
    try{
      const r = MAP.panel.getBoundingClientRect();
      const shortEdge = Math.max(1, Math.min(r.width, r.height));
      MAP.scale = Math.max(0.02, (CONFIG.map.blindSpanM || 60) / shortEdge);
      LOG.map('blind nav zoom: '+MAP.scale.toFixed(3)+' m/px ('+(CONFIG.map.blindSpanM||60)+' m across)');
    }catch(e){}
  });
  vibrate(30);
  LOG.map('BLIND NAV engaged — no camera, map is the driving view');
  if(window.REC && REC.enabled) REC.log('blind_nav', {on:true});
}
function exitBlindNav(){
  if(!MAP.blind) return;
  MAP.blind = false;
  document.body.classList.remove('map-blind');
  afterResize();
  LOG.map('BLIND NAV disengaged');
  if(window.REC && REC.enabled) REC.log('blind_nav', {on:false});
}
/* Driven from STATUS.tick (2 Hz). Debounced both ways so a brief WebRTC hiccup does
   not throw the operator between views mid-manoeuvre. */
function updateBlindNav(){
  if(!CONFIG.map.blindNav) return;
  const now = Date.now();
  const videoOk = (typeof STATUS !== 'undefined') && STATUS.video === 'live';

  if(videoOk){
    MAP.blindDownSince = 0;
    MAP.videoWasLive = true;      // a real feed existed; future outages get the full debounce
    if(!MAP.videoOkSince) MAP.videoOkSince = now;
    // feed is genuinely back: drop the opt-out so the next outage engages again
    if(now - MAP.videoOkSince >= (CONFIG.map.blindBackMs||1500)){
      if(MAP.blind) exitBlindNav();
    }
    return;
  }
  MAP.videoOkSince = 0;

  // The expanded map wins: it is a deliberate operator action and it is all-stopped.
  if(MAP.expanded){ if(MAP.blind) exitBlindNav(); return; }

  if(!MAP.blindDownSince) MAP.blindDownSince = now;
  // The debounce exists to stop a transient WebRTC blip throwing the operator between
  // views. On a COLD START there is no established feed to blip, so waiting the full
  // window just parks a useless NO FEED on screen. Engage promptly the first time.
  const everLive = !!MAP.videoWasLive;
  const wait = everLive ? (CONFIG.map.blindAfterMs||4000) : (CONFIG.map.blindColdMs||1200);
  if(now - MAP.blindDownSince >= wait) enterBlindNav();
}

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
  const tile=$('origin-tile');
  if(!MAP.hasOrigin){
    el.textContent='NOT SET'; el.style.color='var(--secondary)';
    if(tile) tile.title='Set the launch origin';
    return;
  }
  // An origin does not expire, but it does STOP BEING TRUE the moment the handheld is
  // carried to another launch site - and nothing re-acquires it on its own (Wi-Fi
  // positioning needs internet, which the tether does not have). A stale one silently
  // plots the sub relative to somewhere else entirely, so say how old it is and turn
  // amber once it is old enough to have plausibly come from a different place.
  const ageMs  = MAP.origin.t ? (Date.now() - MAP.origin.t) : 0;
  const ageH   = ageMs / 3600000;
  const staleH = CONFIG.map.originStaleH || 8;
  // Kept short on purpose: the tile's own label already says ORIGIN, and the colour
  // already says fresh/stale, so "SET " and the spaces were 38px of the top bar
  // spent repeating what is next to them - enough to push a tile onto a second row.
  // The full sentence stays in the tooltip.
  const acc    = '±'+Math.round(MAP.origin.accuracy)+'m';
  if(ageH >= staleH){
    const age = ageH >= 48 ? Math.round(ageH/24)+'d' : Math.round(ageH)+'h';
    el.textContent = acc+'·'+age;
    el.style.color = 'var(--hazard)';
    if(tile) tile.title='Origin was set '+age+' ago, possibly at another site. '+
                        'Tap to re-set it to where you are now.';
  } else {
    el.textContent = acc;
    el.style.color = 'var(--tertiary)';
    if(tile) tile.title='Launch origin (set '+(ageH<1 ? Math.max(1,Math.round(ageMs/60000))+' min' : Math.round(ageH)+'h')+' ago). Tap to adjust.';
  }
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
  // NO NAV — a vehicle IS on the link but no navigation is coming back from it: no
  // sensors fitted, or no origin set on the Pi, so there is nothing to dead-reckon
  // from. The marker holds position rather than advancing on commanded throttle,
  // and this is what stops a held marker reading as a sub that simply is not moving.
  const nav=$('nav-warning');
  if(nav){
    const linked = typeof vehicleLinked==='function' && vehicleLinked();
    const navFresh = (performance.now()-MAP.lastNavAt) < 1500;
    nav.classList.toggle('on', linked && !navFresh);
    // The dial is a 200 px circle in BOTH the collapsed and blind-nav views, and this
    // badge sits near the bottom of it where only ~99 px of chord is left — the full
    // reason does not fit there. Spell it out only in the expanded map.
    const noSensors = typeof vehicleHasSensors==='function' && !vehicleHasSensors();
    const roomy = MAP.expanded;
    nav.innerHTML = (roomy && noSensors) ? 'NO&nbsp;NAV&nbsp;·&nbsp;NO&nbsp;SENSORS' : 'NO&nbsp;NAV';
    nav.title = noSensors
      ? 'No navigation: the vehicle reports no sensors fitted, so the marker holds position'
      : 'No navigation data from the vehicle — the marker holds position';
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
    const mlDiv=$('maplibre-map'); if(mlDiv) mlDiv.style.pointerEvents='auto';   // MapLibre handles its own gestures
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

let _navBackoff = 0, _navTimer = null;
function connectNavWs(){
  const base = state.wsBase || (location.host ? (location.protocol==='https:'?'wss':'ws')+'://'+location.host : '');
  if(!base) return;
  if(MAP.navWs){ try{ MAP.navWs.onclose=null; MAP.navWs.close(); }catch(e){} MAP.navWs=null; }
  let ws; try{ ws=new WebSocket(base + CONFIG.map.navWs); }catch(e){ scheduleNavWs(); return; }
  MAP.navWs=ws;
  ws.onopen=()=>{ _navBackoff=0; };
  ws.onmessage=(ev)=>{ let m; try{ m=JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='nav'){ MAP.x=m.x_m; MAP.y=m.y_m; MAP.hdg=m.heading_deg; MAP.depth=m.depth_m;
      MAP.lastNavAt=performance.now(); state.navOkAt=Date.now(); pushTrack(m.x_m,m.y_m,m.depth_m); }
  };
  ws.onclose=()=>{ MAP.navWs=null; scheduleNavWs(); };
  ws.onerror=()=>{ try{ ws.close(); }catch(e){} };
}
/* Capped backoff behind a single timer. A flat 3 s retry with no re-entry guard
   stacked one reconnect chain per close, and they never collapsed back. */
function scheduleNavWs(){
  if(_navTimer) return;
  _navBackoff = Math.min(20000, _navBackoff ? _navBackoff*1.7 : 2000);
  _navTimer = setTimeout(()=>{ _navTimer=null; connectNavWs(); }, _navBackoff);
}
function pushTrack(x,y,depth){
  if(MAP.replay || !MAP.hasOrigin) return;                // no track without an origin (§6); frozen during replay
  const t=MAP.track, last=t[t.length-1];
  if(last && Math.hypot(x-last.x,y-last.y)<0.25) return;
  t.push({x,y,depth});
  if(t.length>CONFIG.map.maxTrackPoints){ const keep=[]; for(let i=0;i<t.length;i++){ if(i>t.length/2||i%2===0) keep.push(t[i]); } MAP.track=keep; }
}

/* Where the cable is anchored, in local-frame metres.

   Whoever holds the handheld holds the tether, so the anchor is the LIVE handheld
   position when there is one — walk 20 m up the bank and the reachable circle walks
   with you. Before any fix (and in SIM) it is the frame origin, which is the launch
   point by definition. */
function tetherAnchorLocal(){
  if(MAP.me && MAP.hasOrigin && MAP.origin){
    const r = toLocal(MAP.me.lat, MAP.me.lon, MAP.origin.lat, MAP.origin.lon);
    return { x:r.x, y:r.y };
  }
  return { x:0, y:0 };
}

/* Straight-line range from the anchor, in 3D. Depth is included because the cable has
   to reach down as well as out — which is what makes the reachable circle shrink as
   the sub descends. */
function tetherRangeM(){
  const a=tetherAnchorLocal();
  return Math.hypot(MAP.x-a.x, MAP.y-a.y, MAP.depth||0);
}

/* Horizontal reach still available at the current depth. */
function tetherHorizLimitM(){
  const L=(CONFIG.tether&&CONFIG.tether.lengthM)||0, d=Math.abs(MAP.depth||0);
  return L>d ? Math.sqrt(L*L - d*d) : 0;
}

/* THROTTLE/STEER live in renderUI at frame rate; this is map-derived, so it runs at
   the map's 10 Hz. Colour and wording carry the mode distinction the operator asked
   for: SIM is CLAMPED at the limit, REAL is only ever WARNED. */
function renderTether(){
  const T=CONFIG.tether; if(!T) return;
  const el=$('sonar-teth'), warn=$('tether-warn');
  const r=tetherRangeM(), over=r>=T.lengthM-0.05, near=r>=T.warnFromM;
  if(el){
    el.textContent=(r<10? r.toFixed(1) : Math.round(r))+' m';
    el.classList.toggle('warn', near && !over);
    el.classList.toggle('over', over);
  }
  if(warn){
    const linked = typeof vehicleLinked==='function' && vehicleLinked();
    warn.classList.toggle('on', near);
    warn.classList.toggle('over', over);
    warn.textContent = !near ? 'TETHER'
      : over ? (linked ? 'TETHER OVER '+Math.round(r)+'/'+T.lengthM+' m'
                       : 'TETHER END '+T.lengthM+' m')
             : 'TETHER '+Math.round(r)+'/'+T.lengthM+' m';
    warn.title = linked
      ? 'Straight-line range from the launch point. Not enforced on a real link — the launch point can move.'
      : 'SIM is clamped to the cable length, so an unreachable dive cannot look reachable.';
  }
}

function mapTick(){
  const now=performance.now(); let dt=(now-MAP.lastTick)/1000; if(dt>0.5)dt=0.5; MAP.lastTick=now;
  // Client integrator — SIM ONLY, and only with no vehicle on the link at all.
  //
  // It advances the sub from COMMANDED throttle, which is a lie the moment a real
  // hull exists: a dead thruster, a snagged tether or a sub held against a wall
  // would all keep drawing forward progress. While a vehicle is linked the marker
  // moves on the sub's own navigation output or it stays put — a stationary sub is
  // information, and NO NAV says why (see updateEmptyState).
  if(!MAP.replay && !vehicleLinked()){
    MAP.hdg=state.heading; const spd=(state.input.throttle||0)*CONFIG.map.subMaxSpeedMs; const h=MAP.hdg*Math.PI/180;
    MAP.x+=spd*Math.sin(h)*dt; MAP.y+=spd*Math.cos(h)*dt; MAP.depth=state.depth;
    // The cable is a hard limit and SIM must obey it — a mission the tether cannot
    // reach has to be un-reachable on the bench too, or planning against it is
    // theatre. Clamped to the HORIZONTAL budget left at this depth, so descending
    // visibly costs range. Only ever pulled back toward the launch point, never
    // pushed, so the sub can always drive home.
    if(CONFIG.tether && CONFIG.tether.clampInSim){
      const a=tetherAnchorLocal(), dx=MAP.x-a.x, dy=MAP.y-a.y;
      const r=Math.hypot(dx,dy), lim=tetherHorizLimitM();
      if(r>lim && r>0){ const k=lim/r; MAP.x=a.x+dx*k; MAP.y=a.y+dy*k; }
    }
    pushTrack(MAP.x,MAP.y,MAP.depth);
  }
  renderTether();
  // Whether navigation is arriving changes with time, not just on user actions, so
  // the NO NAV label has to be re-evaluated here — but only touched when it flips.
  const navBits = (vehicleLinked()?1:0) | ((now-MAP.lastNavAt<1500)?2:0) | (vehicleHasSensors()?4:0)
                | (MAP.expanded?8:0) | (MAP.blind?16:0);   // wording depends on how much room there is
  if(navBits!==MAP._navBits){ MAP._navBits=navBits; updateEmptyState(); }
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
  // offsetWidth/Height, NOT getBoundingClientRect().
  //
  // The rect includes any CSS transform, and both the expanded and blind-nav layouts
  // run `animation: mapExpandIn`, which starts at scale(.94). Measuring mid-animation
  // sized the canvas to 94% of the panel and left it there - 1280 wide became 1203 -
  // so the map's centre sat ~39 px away from the dial's centre. The sub (drawn at the
  // canvas centre) and the dial's input vector (drawn at the viewport centre) then
  // appeared as two parallel, offset lines. offsetWidth is the untransformed layout
  // size, so it is correct whenever it is read.
  const w = MAP.panel.offsetWidth  || MAP.panel.getBoundingClientRect().width;
  const h = MAP.panel.offsetHeight || MAP.panel.getBoundingClientRect().height;
  MAP.dpr=window.devicePixelRatio||1;
  MAP.canvas.width=Math.max(1,Math.floor(w*MAP.dpr)); MAP.canvas.height=Math.max(1,Math.floor(h*MAP.dpr));
  MAP.canvas.style.width=w+'px'; MAP.canvas.style.height=h+'px';
}
/* Trajectory colour encodes DEPTH as a hue sweep: fully emerged → neon yellow,
   fully submerged → dark neon blue, every depth in between a proportional blend —
   so you read how deep it was, not just that it was deep. */
function _depthColor(d){
  const f=Math.max(0,Math.min(1, d/CONFIG.map.maxDepthColorM));
  const h=52+(230-52)*f;            // 52° neon yellow → 230° blue
  const l=58-(58-30)*f;            // bright at the surface → dark when deep
  return `hsl(${h.toFixed(0)},100%,${l.toFixed(0)}%)`;
}

/* meters that render ≈px pixels at the current zoom, snapped to 1/2/5×10ⁿ (nice scale bar) */
function niceMeters(px, ppmCss){
  const raw=px/ppmCss; if(!(raw>0)||!isFinite(raw)) return 10;
  const pw=Math.pow(10,Math.floor(Math.log10(raw))), n=raw/pw;
  return (n<1.5?1:n<3.5?2:n<7.5?5:10)*pw;
}
function fmtMeters(m){ return m>=1000 ? (Math.round(m/100)/10)+' km' : Math.round(m)+' m'; }

/* `target` renders the same frame into a DIFFERENT context — used by PIC.
   Satellite tiles are deliberately loaded without `crossOrigin` (and cached as
   opaque responses so the offline archive works), which TAINTS this canvas:
   `toBlob` refuses to export it. Making the tiles CORS-clean would break the
   offline map in the field, which matters far more than a screenshot — so a
   capture re-renders with `noTiles` instead and keeps every vector layer, which
   is what carries the navigational information anyway.

   Same pixel size and dpr as the live canvas, so the projection state the overlays
   read (TILES.last) still lines up. Returns whether imagery was drawn. */
function drawCanvas(target){
  const ctx  = target ? target.ctx : MAP.ctx;
  const w    = target ? target.w   : MAP.canvas.width;
  const h    = target ? target.h   : MAP.canvas.height;
  const dpr  = target ? target.dpr : MAP.dpr;
  const live = !target;
  ctx.setTransform(1,0,0,1,0,0); ctx.fillStyle='#0c0118'; ctx.fillRect(0,0,w,h);
  const cx=w/2,cy=h/2, ppm=dpr/curScale();                 // device px per metre
  const headingUp = !MAP.expanded && MAP.headingUp;
  const rot = headingUp ? -MAP.hdg*Math.PI/180 : 0;
  const center = (MAP.viewLat!=null)? { lat:MAP.viewLat, lon:MAP.viewLon } : null;
  const prov = _provider(MAP.activeArea);                  // offline tiles when an area is active, else online
  const haveProj = !!center && !!prov.url;                 // satellite projection available this frame

  // 1) satellite imagery (§3)
  let drewTiles=false;
  if(haveProj && !(target && target.noTiles)){
    try{ drewTiles=drawTiles(ctx,w,h,center.lat,center.lon,curScale(),rot,dpr,MAP.activeArea); }
    catch(e){ LOG.warn('tiles:',e&&e.message); }
  }

  // 2) readability tint over imagery (§5) — darker in the radar, lighter expanded
  if(drewTiles){ ctx.setTransform(1,0,0,1,0,0);
    ctx.fillStyle='rgba(6,2,16,'+(MAP.expanded?CONFIG.map.tintExpanded:CONFIG.map.tintCollapsed)+')'; ctx.fillRect(0,0,w,h); }

  // scale label (updates with zoom, §4)
  const gm = niceMeters(52, 1/curScale());
  if(live){ const sc=$('radar-scale'); if(sc) sc.innerHTML=fmtMeters(gm).replace(' ','&nbsp;'); }

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
  if(MAP.expanded && MAP.selectMode){ drawSelectionRect(ctx,dpr); if(live && MAP.selReadout) MAP.selReadout(mapSelectionBBox()); }

  // 7) imagery attribution (expanded)
  if(MAP.expanded && drewTiles) drawAttribution(ctx,w,h,dpr);
  return drewTiles;
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
/* The reachable circle: everywhere the cable can physically get to from the launch
   point. Drawn around the ORIGIN, not the sub — the question it answers is "is this
   a good place to put in", which is a question about the launch point. Radius is the
   horizontal budget left at the current depth, so it closes in as the sub descends. */
/* "You are here" — the handheld, live. Distinct from the origin cross (which is the
   dead-reckoning datum) because after launch the two separate, and the difference is
   the whole point: the operator walked, the sub did not. The accuracy halo is drawn
   honestly at whatever the fix claims, so a ±60 m Wi-Fi guess LOOKS like a guess. */
function drawMeMarker(ctx,dpr,x,y,ppm,acc){
  ctx.save();
  const rpx=(acc||0)*ppm;
  if(rpx>6 && rpx<4000){
    ctx.fillStyle='rgba(31,157,255,.10)'; ctx.beginPath(); ctx.arc(x,y,rpx,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle='rgba(31,157,255,.35)'; ctx.lineWidth=1*dpr; ctx.stroke();
  }
  ctx.fillStyle=C.me; ctx.strokeStyle='#0c0118'; ctx.lineWidth=2*dpr;
  ctx.beginPath(); ctx.arc(x,y,5*dpr,0,Math.PI*2); ctx.fill(); ctx.stroke();
  ctx.restore();
}
function drawTetherRing(ctx,dpr,ox,oy,ppm){
  const T=CONFIG.tether; if(!T || !T.showRing) return;
  const rpx=tetherHorizLimitM()*ppm;
  if(!(rpx>4) || rpx>20000) return;                 // off-scale: skip rather than draw a wall
  const near=tetherRangeM()>=T.warnFromM;
  ctx.save();
  ctx.setLineDash([6*dpr,5*dpr]);
  ctx.lineWidth=(near?2:1.25)*dpr;
  ctx.strokeStyle= near ? 'rgba(255,140,26,.85)' : 'rgba(255,140,26,.42)';
  ctx.beginPath(); ctx.arc(ox,oy,rpx,0,Math.PI*2); ctx.stroke();
  ctx.restore();
}
function drawTrackProjected(ctx,dpr,headingUp){
  ctx.setTransform(1,0,0,1,0,0);
  // origin cross
  const oS=lonLatToScreen(MAP.origin.lat,MAP.origin.lon);
  // Ring around the cable's ANCHOR (the handheld when we have a fix), not the datum.
  const meS = MAP.me ? lonLatToScreen(MAP.me.lat,MAP.me.lon) : null;
  const aS = meS || oS;
  if(aS) drawTetherRing(ctx,dpr,aS[0],aS[1],dpr/curScale());
  if(meS) drawMeMarker(ctx,dpr,meS[0],meS[1],dpr/curScale(),MAP.me.acc);
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
  const o=L(0,0);
  const an=tetherAnchorLocal(), aP=L(an.x,an.y);
  drawTetherRing(ctx,dpr,aP[0],aP[1],ppm);
  if(MAP.me) drawMeMarker(ctx,dpr,aP[0],aP[1],ppm,MAP.me.acc);
  ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
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

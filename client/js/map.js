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
  // `hdg` IS ALWAYS A NUMBER, and `hdgLive` is what says whether anything is still
  // measuring it. Those are two facts and they used to be crammed into one field: a
  // null bearing landed straight in `hdg`, and `-null` is 0, so the heading-up dial
  // wrote rotate(0.0) — which on this map is the picture for "the sub is pointing due
  // north". Observed going from rotate(-284.0) to rotate(0.0) the instant the compass
  // died: the whole world swinging round to a bearing nobody measured, which is the
  // same visual lie the server spent this round removing.
  //
  // So the angle is HELD at the last one a compass actually reported and this flag goes
  // false, which is exactly what HEADING_FLAGS.dead's tooltip has always promised
  // ("the radar is still drawn on the LAST angle it reported") and what the broken
  // amber ring in styles.css (body.heading-dead) is drawn to say. See setMapHeading.
  track:[], x:0, y:0, hdg:0, hdgLive:true, depth:0, scale:0.6, headingUp:true,
  expanded:false, hasArea:false, hasOrigin:false, origin:null, activeArea:null,
  navWs:null, lastNavAt:0, lastTick:0, _navBits:-1,   // _navBits: last NO-NAV label state
  // What the estimator says about its own output (§5), straight off the nav frame.
  // Held here as well as in `state` so the map can qualify what it draws without
  // asking the control link, which is a different socket that fails separately.
  // null, not false: these start at CANNOT-TELL because no nav frame has arrived yet,
  // and false is navigation's reassuring answer, not its silence.
  confidence:1, snagged:null, gyroOnly:null, magCal:null, rangeM:0, payoutM:0, _hdgFlag:'?',
  navReadsVehicle:null, navSimulated:null,   // what the nav frame says it is ABOUT (see connectNavWs)
  me:null,                                   // {lat,lon,acc,t} — the handheld, live (§2)
  viewLat:null, viewLon:null, follow:true,   // geographic view centre (§3); follow = track the sub
  centreline:null,                           // [[lon,lat],…] waterway overlay (§3.5)
  originTap:false,                           // one-shot: next map tap sets the origin (§2)
  rovTap:false,                              // one-shot: next map tap places the ROV by hand
  mockMeTap:false, meReal:null,              // mocked operator position; last GENUINE fix
  showTrack:true, trackBreak:false,          // eye toggle; "start a new segment here"
  drag:null, selReadout:null,                // pan drag state; area-selection readout callback (§4)
  replay:false,                              // viewing a saved dive — freeze live integration (§1)
  blind:false, blindSince:0, videoOkSince:0, videoWasLive:false,   // BLIND NAV (map as the driving view)
};
const C = { grid:'rgba(180,107,255,0.13)', origin:'#ff8c1a', sub:'#b46bff',
           // THE OPERATOR, colour-coded by where the position actually came from. The
           // tether range is measured from this dot, so "how much do I trust it" has
           // to be readable at a glance, not looked up.
           meLive:'#4dffa6',                   // green  — a fresh fix from the handheld
           meStale:'#ffe14d',                  // yellow — last known fix, going cold
           meMock:'#ff5c7a',                   // red    — placed by hand: planning, not real
           shallow:'#4dffa6', deep:'#1f9dff' };
/* Where the displayed operator position came from. Drives the dot colour and the
   MOCK tag on the tether readout. */
function meSource(){
  const me = operatorLL();
  if(!me) return null;
  if(me.mock) return 'mock';
  if(me.assumed) return 'stale';        // assumed = where they were, not where they are
  return (Date.now()-(me.t||0)) > (CONFIG.map.meStaleMs||30000) ? 'stale' : 'live';
}
function meColor(){
  const s=meSource();
  return s==='mock' ? C.meMock : s==='stale' ? C.meStale : C.meLive;
}

function navFetch(path, opts){ return fetch((state.httpBase||'') + path, opts); }

/* THE ONLY WAY A BEARING GETS ONTO THIS MAP.

   Every rotation in this file is `-MAP.hdg` or `MAP.hdg*PI/180`, and JavaScript turns
   both of those into 0 for a null — silently, with no NaN to notice. A single guarded
   setter is the fix rather than seven guarded readers: a rotation added tomorrow cannot
   forget, because there is no longer a null in the field to forget about.

   A non-number HOLDS the last angle instead of clearing it. Freezing and blanking were
   both on the table; holding is what the console already tells the operator it does, in
   three separate places (core.js HEADING_FLAGS.dead, the #hdg-warning tooltip, and
   render.js's note beside the heading readout), and a dial that behaves differently from
   its own explanation is worse than either choice on its own. The picture is then marked
   rather than trusted — amber broken rings, NO BEARING over the dial, '?' for the
   number — so a held angle can never be read as a measured one. */
function setMapHeading(h){
  if(typeof h === 'number' && isFinite(h)){ MAP.hdg = h; MAP.hdgLive = true; return; }
  // Deliberately does NOT touch MAP.hdg. Before any compass has ever answered it is
  // still 0, which is the orientation the map has never been turned from - not a
  // reading, and flagged as not a reading by everything above.
  MAP.hdgLive = false;
}

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
  // Open at the BEST imagery the provider has, not at a fixed metres-per-pixel. The
  // map is being read for underwater structures and bank detail, so the useful default
  // is maximum resolution; zooming out is one keypress away, recovering detail that was
  // never requested is not. Recomputed once a view centre exists, since the scale that
  // lands on the deepest tile zoom is latitude-dependent (see maxZoomScale).
  MAP.scale = CONFIG.map.startAtMaxZoom===false
            ? CONFIG.map.metersPerPixel : maxZoomScale(0);
  MAP.radarScale = CONFIG.map.radarMetersPerPixel || CONFIG.map.metersPerPixel;  // collapsed radar (fixed glance zoom)
  MAP._zoomPinned = false;                      // becomes true once pinned at a real latitude
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
  if(rc) rc.addEventListener('click', (e)=>{ e.stopPropagation(); MAP.scale=bestScaleNow(); });  // reset = best imagery, not a fixed m/px
  const sr=$('map-set-rov'); if(sr) sr.addEventListener('click', (e)=>{ e.stopPropagation(); armRovTap(); });
  const mm=$('map-mock-me'); if(mm) mm.addEventListener('click', (e)=>{ e.stopPropagation(); armMockMeTap(); });
  const tt=$('map-track-toggle'); if(tt){ tt.addEventListener('click', (e)=>{ e.stopPropagation(); toggleTrack(); }); renderTrackToggle(); }
  MAP.canvas.addEventListener('wheel', (e)=>{ if(!MAP.expanded && !MAP.blind) return; e.preventDefault(); MAP.scale=Math.max(0.05,Math.min(40,MAP.scale*(e.deltaY>0?1.1:0.9))); }, {passive:false});
  // drag-to-pan + tap (expanded only, canvas-scoped so it never touches piloting input, §5).
  // Pan is computed absolutely from the drag-start centre + total screen delta (no feedback loop),
  // so it's smooth like a real slippy map.
  MAP.canvas.addEventListener('pointerdown', (e)=>{ if(!MAP.expanded && !MAP.blind) return;
    MAP.drag={ x:e.clientX, y:e.clientY, moved:0, lat0:MAP.viewLat, lon0:MAP.viewLon, px:e.clientX, py:e.clientY };
    try{ MAP.canvas.setPointerCapture(e.pointerId); }catch(_){}
  });
  MAP.canvas.addEventListener('pointermove', (e)=>{ if((!MAP.expanded && !MAP.blind)||!MAP.drag) return;
    MAP.drag.moved += Math.abs(e.clientX-MAP.drag.x)+Math.abs(e.clientY-MAP.drag.y);
    const L=TILES.last;
    // No imagery projection (no basemap saved, or none drawn yet) — fall back to the
    // metre frame so a finger still moves the map instead of doing nothing at all.
    if(!L || MAP.drag.lat0==null){
      panMapPx((e.clientX-MAP.drag.px)*MAP.dpr, (e.clientY-MAP.drag.py)*MAP.dpr);
      MAP.drag.px=e.clientX; MAP.drag.py=e.clientY; return;
    }
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
  // CHART LAYERS (crt.js): the CRT hazard/operations/extras overlays and the depth
  // picture. Guarded like every other optional module here — if crt.js failed to
  // load, the map must still draw. It is deliberately NOT the other way round: the
  // hazard marks are worth nothing without the map they sit on.
  if(typeof crtInit==='function'){ try{ crtInit(); }catch(e){ LOG.warn('chart layers init failed (map unaffected):', e && e.message); } }
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
  MAP.scale=bestScaleNow();                                     // back to max imagery zoom
  updateEmptyState(); afterResize();
  LOG.state('map collapsed — control returned');
}
/* PINPOINT THE ROV.

   The operator's position is known — the handheld reports it. The sub's is not: there
   is no GNSS underwater and, until the IMU is wired, nothing on board can say where it
   drifted to. So the default assumption is the only honest one available: the ROV is
   where the operator was when they set the launch point. That is a starting guess, not
   a fix, and the operator is the one who can correct it by eye — "it surfaced over
   there" — which is what this arms.

   Sets only the sub's position in the local frame. It does NOT touch the origin: the
   datum stays put, so the track and every previous coordinate remain valid. */
function armRovTap(){
  if(!MAP.hasOrigin){ if(typeof openOriginModal==='function') openOriginModal(); return; }
  MAP.rovTap=true; MAP.originTap=false;
  if(!MAP.expanded && typeof expandMap==='function') expandMap();
  if(typeof showOriginPrompt==='function')
    showOriginPrompt('TAP WHERE THE ROV IS',
      'The sub cannot report its own position yet. Tap its actual place on the map.',
      { label:'CANCEL', run:()=>{ MAP.rovTap=false; if(typeof hideOriginPrompt==='function') hideOriginPrompt(); } });
  LOG.map('armed: next map tap places the ROV');
}
function setRovLatLon(lat, lon){
  if(!MAP.hasOrigin || !MAP.origin) return false;
  const p=toLocal(lat, lon, MAP.origin.lat, MAP.origin.lon);
  // OUT OF REACH — refuse. The cable is a hard physical limit, so a hand-placed ROV
  // further from the operator than the tether is long is not a position that can
  // exist. Silently clamping it would invent a location the operator did not pick;
  // refusing says which of the two points is actually wrong, and it is nearly always
  // the operator's own — the ROV is where they can see it.
  const T=CONFIG.tether;
  if(T && T.lengthM){
    const a=tetherAnchorLocal();
    const r=Math.hypot(p.x-a.x, p.y-a.y, MAP.depth||0);
    if(r > T.lengthM){
      const msg='ROV would be '+Math.round(r)+' m away — the tether is only '+T.lengthM+' m. '+
                'Move your own position first.';
      LOG.warn('ROV placement refused: '+msg);
      if(typeof camToast==='function') camToast(msg, 'warn');
      return false;
    }
  }
  // A hand-placed position is a jump, not travel. Break the trace so the old path
  // stays on the map without a line implying the sub swam there.
  breakTrack('ROV placed by hand');
  MAP.x=p.x; MAP.y=p.y;
  pushTrack(MAP.x, MAP.y, MAP.depth);
  LOG.map('ROV placed by hand at '+p.x.toFixed(1)+','+p.y.toFixed(1)+' m from the datum'+
          ' — tether now '+tetherRangeM().toFixed(1)+' m');
  return true;
}
/* STAND SOMEWHERE ELSE — a mocked operator position, for planning.

   "Could I reach that culvert if I put in from the far bank?" is a question about a
   launch point you are not standing on. This moves the operator dot there so the
   reachable circle and the range readout answer it — and turns the dot RED, because
   from that moment the tether range is a hypothesis rather than a measurement.

   Pre-dive it takes the launch point with it (that is the thing being planned). Once a
   track exists the datum is frozen, exactly as it is for a real fix, so an experiment
   can never rewrite a dive already under way. */
function armMockMeTap(){
  if(MAP.me && MAP.me.mock){ clearMockMe(); return; }        // the button toggles
  MAP.mockMeTap=true; MAP.originTap=false; MAP.rovTap=false;
  if(!MAP.expanded && typeof expandMap==='function') expandMap();
  if(typeof showOriginPrompt==='function')
    showOriginPrompt('TAP A POSITION TO PLAN FROM',
      'Your dot moves there and turns red. Range shown from there is a plan, not a measurement.',
      { label:'CANCEL', run:()=>{ MAP.mockMeTap=false; if(typeof hideOriginPrompt==='function') hideOriginPrompt(); } });
  LOG.map('armed: next map tap places a MOCK operator position');
}
function setMockMe(lat, lon){
  // Entering a planning run is a discontinuity: what follows is a different journey.
  // Keep the old path on screen, but never draw a line into the new one.
  breakTrack('planning started');
  MAP.me = { lat, lon, acc:0, t:Date.now(), mock:true };
  // Pre-dive, the launch point is what is being planned, so take it along.
  if(MAP.hasOrigin && MAP.origin && !diveUnderway() && typeof setOrigin==='function'){
    const rel = toLocal(lat, lon, MAP.origin.lat, MAP.origin.lon);
    setOrigin({ lat, lon, accuracy:8, source:'mock_plan', t:Date.now(), _override:true })
      .then(ok=>{ if(ok!==false) rebaseFrame(rel.x, rel.y); });   // ROV stays put in the world
  }
  LOG.map('MOCK operator position set — dot is RED, range is planning only');
  if(typeof camToast==='function') camToast('Planning from a mocked position', 'warn');
}
function clearMockMe(){
  MAP.mockMeTap=false;
  if(!MAP.me || !MAP.me.mock) return;
  breakTrack('planning ended');           // leaving a plan is a discontinuity too
  // Back to the real world: the last genuine fix if we have one, otherwise nothing.
  MAP.me = MAP.meReal ? { lat:MAP.meReal.lat, lon:MAP.meReal.lon, acc:MAP.meReal.acc, t:MAP.meReal.t } : null;
  if(typeof hideOriginPrompt==='function') hideOriginPrompt();
  LOG.map('mock position cleared — back to '+(MAP.me? 'the live fix' : 'no fix'));
}

/* a non-drag tap in the expanded map: place the origin, the ROV, or a mocked
   operator position, whichever is armed (§2) */
function onMapTap(clientX, clientY){
  if(!MAP.originTap && !MAP.rovTap && !MAP.mockMeTap) return;
  const g=screenToLatLon(MAP.canvas, clientX, clientY); if(!g) return;
  if(MAP.mockMeTap){
    MAP.mockMeTap=false;
    setMockMe(g.lat, g.lon);
    if(typeof hideOriginPrompt==='function') hideOriginPrompt();
    return;
  }
  if(MAP.rovTap){
    MAP.rovTap=false;
    setRovLatLon(g.lat, g.lon);
    if(typeof hideOriginPrompt==='function') hideOriginPrompt();
    return;
  }
  MAP.originTap=false;
  setOrigin({ lat:g.lat, lon:g.lon, accuracy:8, source:'map_tap', t:Date.now() }).then(ok=>{
    if(ok!==false){
      // Setting the launch point by hand puts the sub back at 0,0 — a jump, not travel.
      // Without a break the old trace is stroked straight into the new position.
      breakTrack('launch point set on the map');
      MAP.x=0; MAP.y=0; MAP.follow=true; if(typeof hideOriginPrompt==='function') hideOriginPrompt();
    }
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
      // The chart layers are per-area too, and crtSetArea is a no-op when the name
      // has not changed — including the null case, so an area being CLEARED resets
      // every layer to "no area" rather than leaving the last area's hazards drawn
      // over somewhere else entirely.
      if(typeof crtSetArea==='function') crtSetArea(MAP.activeArea);
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
    if(tile) liveTitle(tile,'Set the launch origin');
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
    if(tile) liveTitle(tile,'set '+age+' ago, possibly at another site — tap to re-set it to where you are now');
  } else {
    el.textContent = acc;
    el.style.color = 'var(--tertiary)';
    if(tile) liveTitle(tile,'set '+(ageH<1 ? Math.max(1,Math.round(ageMs/60000))+' min' : Math.round(ageH)+'h')+' ago');
  }
}
function updateEmptyState(){
  const empty = !MAP.hasArea || !MAP.hasOrigin;
  if(MAP.radar) MAP.radar.classList.toggle('empty', empty);              // compact NO MAP / NO ORIGIN in the circle
  const compact=$('radar-empty'); if(compact) compact.innerHTML = !MAP.hasArea ? 'NO&nbsp;MAP' : 'NO&nbsp;ORIGIN';
  const full=$('map-empty'), msg=$('map-empty-msg'), btn=$('map-empty-btn');  // full explanation in the expanded view
  // …but don't dim the imagery while the operator is actively tapping an origin or selecting an area
  const suppress = MAP.expanded && (MAP.originTap || MAP.rovTap || MAP.mockMeTap || MAP.selectMode);
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
  // CHART LAYERS THAT ARE NOT THERE. Same decision, same place: the badge's wording
  // depends on how much room the current view has, exactly as NO NAV's does, and
  // this function is already called on every transition that changes that.
  if(typeof crtRenderBadge==='function') crtRenderBadge();
}

/* HOW MUCH THE MAP'S HEADING IS WORTH (§5.6).

   The collapsed radar is HEADING-UP: the whole picture rotates on the bearing, so a
   suspect compass does not just mistake one number, it turns the entire map under
   the operator. That has to be visible ON the map — reading it off the top bar
   requires knowing the two are the same number, which is exactly the kind of
   knowledge nobody has while driving.

   Same vocabulary as the HUD (core.js HEADING_FLAGS), on purpose: MAG? in one place
   and "compass?" in the other would read as two different faults. The badge sits
   ABOVE the NO NAV one so the two never collide when both are up, and the North
   indicator — the map's own drawing of the heading — is marked with it. */
/* THE MAP'S OWN VERSION OF "NO BEARING", for the case the HUD's vocabulary cannot cover.

   The dial does not turn on the number in the top bar: that bearing comes off
   /ws/control and this rotation comes off /ws/nav, and the estimator can stop carrying
   a heading (NavState.heading_deg is Optional) while the compass behind the HUD is
   answering perfectly. setMapHeading then holds the last angle — correctly — and
   headingFlag() has nothing to say about it, because nothing is wrong with the bearing
   it describes. Without this the ring would go amber and broken with no words anywhere
   on screen, and a mark nobody can read is the thing this console keeps promising not
   to ship. NO BEARING would be the wrong words: there IS one, it is just not this
   picture's. */
const MAP_HELD_BEARING = { label:'HELD BEARING', cls:'suspect',
  title:'HELD BEARING - the navigation feed has stopped carrying a heading, so this map '
      + 'is no longer being rotated by anything: it is frozen at the last angle it was '
      + 'given, and it will sit there looking exactly like a sub holding course. The '
      + 'bearing in the top bar is coming from the compass on the other socket and may '
      + 'well be fine - it is the PICTURE that has stopped following it. Read direction '
      + 'off the number, not off this dial, until the badge clears.' };
function updateHeadingFlag(){
  const f = (typeof headingFlag==='function') ? headingFlag() : '';
  // Keyed on the map's own liveness too, or a heading that stops arriving on /ws/nav
  // while the flag stays '' would never repaint anything.
  const held = MAP.hdgLive === false;
  const key = f + (held ? '|held' : '');
  if(key === MAP._hdgFlag) return;               // 10 Hz tick: only touch the DOM on a change
  MAP._hdgFlag = key;
  // The shared table first: when the compass itself is gone both the HUD and the map say
  // NO BEARING, which is the whole reason that vocabulary is shared. The map-only word
  // is a fallback for when the HUD has nothing to complain about.
  const d = ((typeof HEADING_FLAGS!=='undefined') ? HEADING_FLAGS[f] : null)
         || (held ? MAP_HELD_BEARING : null);
  const el = $('hdg-warning');
  if(el){
    el.textContent = d ? d.label : '';
    el.classList.toggle('on', !!d);
    el.classList.toggle('gyro', f==='gyro' || f==='gyro-mag');
    if(d) el.title = d.title;
  }
  const north = $('radar-north');
  if(north) north.setAttribute('class', d ? ('flag-'+(f==='gyro' ? 'gyro' : 'mag')) : '');
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
    if(m.type==='nav'){ MAP.x=m.x_m; MAP.y=m.y_m; setMapHeading(m.heading_deg); MAP.depth=m.depth_m;
      // THE ESTIMATOR'S ACCOUNT OF ITSELF TRAVELS WITH THE POSITION.
      //
      // This frame used to carry four numbers, which meant the map could draw a
      // track without ever saying how much that track was worth. The estimator
      // knows: which speed source it used, whether it is coasting on the gyro
      // because the compass is untrustworthy right now, whether the thrust it is
      // integrating is actually moving the hull. Those belong on screen beside the
      // line they produced, not in a log nobody reads mid-dive.
      //
      // THEY LAND IN MAP.* ALWAYS AND IN `state` ALMOST NEVER, and that split is a
      // correction. They used to be stamped straight into the same `state` slots the
      // HUD reads, on the theory that "whichever stream is more recent wins" — and the
      // two streams do not carry the same information, so the more recent one is
      // systematically the more reassuring one:
      //
      //   /ws/control  snagged / gyro_only are TRI-STATE. api/main.py fill_nav_fields
      //                nulls them the moment navigation cannot speak for this hull, and
      //                that null is the whole of last round's work.
      //   /ws/nav      the same fields are plain bools off NavState (api/nav/models.py)
      //                with `False` defaults, and speed_ms is a float defaulting to 0.0.
      //                They have no way to say "cannot tell" at all.
      //
      // So a nav frame writing into `state` could only ever overwrite a cannot-tell with
      // "nav looked, and everything is fine" — the exact hole fill_nav_fields exists to
      // close, reopened on the client at 10 Hz. Robust to BOTH shapes on purpose: if the
      // server later nulls these on /ws/nav too, the non-number/non-boolean lands as
      // null below rather than being dropped, because dropping a null leaves the last
      // reassuring answer standing, which is the same bug wearing a different hat.
      //
      // Two gates, and both have to pass before anything reaches `state`:
      //
      //   reads_vehicle / simulated — IS THIS FRAME ABOUT THIS HULL? With NAV_SENSORS=sim
      //     the estimator is fed a scripted path and never looks at the sub; api/main.py
      //     deliberately keeps those answers OUT of telemetry for that reason, and this
      //     handler was putting them back on the HUD under mock=false.
      //   vehicleRecent() — IS ANYTHING BETTER ALREADY SPEAKING? While the console still
      //     believes a hull's telemetry, the control link is the authority on these and
      //     the nav socket does not get a vote. Once it does not, viewFromState's own
      //     `live` gate neutralises them anyway, so this can never resurrect one
      //     session's estimate into the next.
      const aboutThisHull = (m.reads_vehicle !== false) && (m.simulated !== true);
      const telAuthoritative = (typeof vehicleRecent==='function') && vehicleRecent();
      const toState = aboutThisHull && !telAuthoritative;
      if(m.reads_vehicle!==undefined) MAP.navReadsVehicle = (m.reads_vehicle===true);
      if(m.simulated!==undefined)     MAP.navSimulated    = (m.simulated===true);
      if(m.snagged!==undefined){
        MAP.snagged = (typeof m.snagged==='boolean') ? m.snagged : null;
        if(toState) state.snagged = MAP.snagged;
      }
      if(m.gyro_only!==undefined){
        MAP.gyroOnly = (typeof m.gyro_only==='boolean') ? m.gyro_only : null;
        if(toState) state.gyroOnly = MAP.gyroOnly;
      }
      if(m.mag_cal!==undefined){
        MAP.magCal = (typeof m.mag_cal==='number') ? m.mag_cal : null;
        if(toState) state.magCal = MAP.magCal;
      }
      if(toState && m.speed_ms!==undefined)  state.speedMs  = (typeof m.speed_ms==='number') ? m.speed_ms : null;
      if(toState && m.speed_src!==undefined) state.speedSrc = (typeof m.speed_src==='string') ? m.speed_src : null;
      if(typeof m.confidence==='number') MAP.confidence=m.confidence;
      if(typeof m.range_m==='number') MAP.rangeM=m.range_m;
      if(typeof m.payout_m==='number') MAP.payoutM=m.payout_m;   // §5.5 an UPPER bound on range, never a position
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
/* Has the sub actually gone anywhere?

   NOT `track.length > 0`. pushTrack records a point the moment an origin exists and
   then dedupes anything within 0.25 m, so a stationary sub holds at exactly ONE point
   however long it sits there — that test meant "the map has been running", and it
   silently disabled the launch point following the operator after the first second.
   More than one point means the sub moved, which is the only definition of "a dive is
   under way" that does not depend on somebody remembering to press start. */
function diveUnderway(){ return MAP.track.length > 1; }

/* Move the whole local frame when the datum moves: the sub and every plotted point
   shift by the same delta, so nothing changes position in the WORLD. Without this,
   re-basing the sub alone would leave the track behind in the old frame. */
function rebaseFrame(rx, ry){
  MAP.x-=rx; MAP.y-=ry;
  for(let i=0;i<MAP.track.length;i++){ MAP.track[i].x-=rx; MAP.track[i].y-=ry; }
}

/* Show / hide the plotted traces. After a few planning runs the map fills with old
   paths, which is exactly what makes them worth keeping AND worth hiding. The sub
   marker, origin, operator dot and tether ring are never hidden — this is about the
   history, not the instruments. */
const EYE_OPEN  = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="none" stroke="currentColor" stroke-width="1.8" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.1" fill="currentColor"/></svg>';
const EYE_SHUT  = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="none" stroke="currentColor" stroke-width="1.8" d="M1.8 12S5.8 5.5 12 5.5 22.2 12 22.2 12 18.2 18.5 12 18.5 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3.1" fill="currentColor"/><path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4 20 20 4"/></svg>';
function renderTrackToggle(){
  const b=$('map-track-toggle'); if(!b) return;
  b.innerHTML = MAP.showTrack ? EYE_OPEN : EYE_SHUT;
  liveTitle(b, MAP.showTrack ? 'showing' : 'hidden');
  b.style.opacity = MAP.showTrack ? '1' : '.6';
}
function toggleTrack(){
  MAP.showTrack=!MAP.showTrack;
  renderTrackToggle();
  LOG.map('tracks '+(MAP.showTrack?'shown':'hidden')+' ('+MAP.track.length+' points held either way)');
}

/* Start a NEW track segment. Old paths stay on the map — they are real history and
   worth seeing — but the next point must not be joined to the last one by a line.
   A jump between two disjoint journeys drawn as a straight stroke reads as the sub
   having travelled that line, which it never did. */
function breakTrack(why){
  MAP.trackBreak = true;
  LOG.map('track break ('+why+') — drawn as a separate segment, not joined');
}

/* Track thinned for display, ALWAYS keeping the break markers. Decimation that drops
   a break silently re-joins two journeys, which is the very thing the break exists to
   prevent. */
function decimatedTrack(){
  const t=MAP.track, step=Math.max(1,Math.floor(t.length/600)), out=[];
  for(let i=0;i<t.length;i++){ if(i%step===0 || t[i].brk) out.push(t[i]); }
  return out;
}

function pushTrack(x,y,depth){
  if(MAP.replay || !MAP.hasOrigin) return;                // no track without an origin (§6); frozen during replay
  const t=MAP.track, last=t[t.length-1];
  // The dedupe is skipped on a break: the first point of a new segment must be kept
  // even if it happens to land near the last point of the old one.
  if(!MAP.trackBreak && last && Math.hypot(x-last.x,y-last.y)<0.25) return;
  const p={x,y,depth};
  if(MAP.trackBreak){ p.brk=true; MAP.trackBreak=false; }
  t.push(p);
  // Thin the stored track when it gets long — but never drop a break marker.
  if(t.length>CONFIG.map.maxTrackPoints){ const keep=[]; for(let i=0;i<t.length;i++){ if(i>t.length/2||i%2===0||t[i].brk) keep.push(t[i]); } MAP.track=keep; }
}

/* Where the cable is anchored, in local-frame metres.

   Whoever holds the handheld holds the tether, so the anchor is the LIVE handheld
   position when there is one — walk 20 m up the bank and the reachable circle walks
   with you. Before any fix (and in SIM) it is the frame origin, which is the launch
   point by definition. */
/* Where the operator is, in lat/lon — ALWAYS an answer once there is an origin.

   With no fix (no GNSS on this handheld, no internet to position from) MAP.me stays
   null, and everything anchored to the operator quietly fell back to the launch
   point. That is a lie the moment they take one step along the bank, and it is the
   reachable circle — the thing that says whether the sub can get home — that tells
   it. So with no fix the operator is ASSUMED to be at the launch point, which is
   where they were when they set it, and the dot says so by being yellow: a last
   known position, not a live one. Moving it (a fix, or the plan button) moves the
   circle with them, which is the whole point. */
function operatorLL(){
  if(MAP.me) return MAP.me;
  if(MAP.hasOrigin && MAP.origin)
    return { lat:MAP.origin.lat, lon:MAP.origin.lon,
             acc:MAP.origin.accuracy, t:MAP.origin.t || 0, assumed:true };
  return null;
}

function tetherAnchorLocal(){
  const me = operatorLL();
  if(me && MAP.hasOrigin && MAP.origin){
    const r = toLocal(me.lat, me.lon, MAP.origin.lat, MAP.origin.lon);
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
  const el=$('sonar-teth'), warn=$('tether-warn'), src=meSource();
  const r=tetherRangeM(), over=r>=T.lengthM-0.05, near=r>=T.warnFromM;
  if(el){
    el.textContent=(r<10? r.toFixed(1) : Math.round(r))+' m';
    el.classList.toggle('warn', near && !over);
    el.classList.toggle('over', over);
  }
  // The range is measured FROM the operator dot, so it inherits that dot's honesty.
  // A mocked anchor makes it a plan; a stale one makes it as old as the last fix.
  const tag=$('teth-src');
  if(tag){
    tag.textContent = src==='mock' ? 'PLANNED' : src==='stale' ? 'LAST KNOWN' : '';
    tag.className = 'teth-src' + (src==='mock' ? ' mock' : src==='stale' ? ' stale' : '');
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
    // Through the setter, not straight in: state.heading is null on a hull whose compass
    // died, and the console spends up to simFallbackMs here before it hands back to the
    // simulator. `MAP.hdg=null` then made `h` 0 and every rotation in this file 0 with
    // it — the sub swinging to due north as the compass stopped.
    setMapHeading(state.heading);
    const spd=(state.input.throttle||0)*CONFIG.map.subMaxSpeedMs; const h=MAP.hdg*Math.PI/180;
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
  // Right stick pans the map while the map IS the view (see computeInput).
  const px=state.input.mapPanX||0, py=state.input.mapPanY||0;
  if((px||py) && (MAP.expanded||MAP.blind)){
    const rate=(CONFIG.map.stickPanPxPerS||420)*(MAP.dpr||1)*dt;
    panMapPx(-px*rate, py*rate);          // push right → the view travels right
  }
  // DRIVING RETAKES THE VIEW.
  //
  // Panning is a halted-operator's luxury. The moment the sub is commanded to move, the
  // map has to be showing the sub again — otherwise the craft swims out of frame and the
  // operator ends up flying the VIEW as well as the vehicle, which is exactly the wrong
  // thing to be doing while under way. Any throttle or steer past the deadzone re-arms
  // follow, so a parked view can never outlive the decision to move.
  //
  // The expanded map handles its own case in computeInput: driving collapses it outright
  // (it engages ALL STOP, so it must not survive a movement command either).
  const cmd = Math.abs(state.input.throttle||0) + Math.abs(state.input.steer||0);
  if(cmd > (CONFIG.deadzone||0.08) && !MAP.follow){
    MAP.follow = true;
    LOG.map('driving — view re-centred on the sub');
  }

  // Whether navigation is arriving changes with time, not just on user actions, so
  // the NO NAV label has to be re-evaluated here — but only touched when it flips.
  const navBits = (vehicleLinked()?1:0) | ((now-MAP.lastNavAt<1500)?2:0) | (vehicleHasSensors()?4:0)
                | (MAP.expanded?8:0) | (MAP.blind?16:0);   // wording depends on how much room there is
  if(navBits!==MAP._navBits){ MAP._navBits=navBits; updateEmptyState(); }
  updateHeadingFlag();          // self-guarded: touches the DOM only when the flag changes
  // view centre (§3): follow the sub when collapsed/following; free pan otherwise
  if(MAP.hasOrigin && (MAP.follow || MAP.viewLat==null)){
    const g=toLatLon(MAP.x,MAP.y,MAP.origin.lat,MAP.origin.lon); MAP.viewLat=g.lat; MAP.viewLon=g.lon;
  }
  // Pin to the provider's deepest zoom once we know WHERE we are — Mercator resolution
  // is latitude-dependent, so this cannot be a constant. One-shot: after this the
  // operator owns the zoom and nothing moves it under them.
  if(!MAP._zoomPinned && MAP.viewLat!=null && CONFIG.map.startAtMaxZoom!==false){
    MAP._zoomPinned=true;
    MAP.scale=bestScaleNow();
    LOG.map('map opened at maximum imagery zoom — '+MAP.scale.toFixed(3)+' m/px');
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
/* DEPTH — 20 discrete, deliberately distinguishable bands.

   The old ramp swept one hue into another with matching lightness, so in practice you
   could tell maybe four steps apart and everything between them read as "some sort of
   green". Quantising into 20 fixed bands and moving hue, saturation AND lightness
   together makes neighbours separable: the eye is far better at "which band is this"
   than at "how far along a gradient is this".

   Ordered warm-bright → cool-dark, so shallow still reads as shallow at a glance, and
   the ends are unmistakable: near-white at the surface, near-black at the bottom.
   Read the exact value off the legend the map draws (drawDepthLegend). */
/* Generated, not hand-picked. Hand-picking is what produced the last version's
   problem: four teal-ish entries bunched at the deep end that read as one wide band
   while the middle stepped quickly. Walking hue and lightness at a CONSTANT rate makes
   every band the same visual size — which is what "neat" means here.

   12 bands, not 20. Twenty was an arbitrary number and it is past the point where the
   eye can hold the steps apart anyway; a dozen leaves each one clearly its own colour
   and keeps the key short enough to read at a glance. */
const DEPTH_STEPS = 12;
const DEPTH_RAMP = (function(){
  const out=[];
  // OKLCH, because evenly spaced HSL is not evenly spaced to the EYE: equal hue steps
  // crawl through the yellows and sprint through the blues, which is what left a wide
  // flat teal at the deep end. Oklch's lightness and hue are perceptually uniform, so
  // equal numeric steps really do look like equal steps.
  const oklch = (typeof CSS!=='undefined' && CSS.supports && CSS.supports('color','oklch(0.5 0.1 100)'));
  for(let i=0;i<DEPTH_STEPS;i++){
    const f=i/(DEPTH_STEPS-1);
    if(oklch){
      // ORANGE at the surface → PURPLE at the bottom. A 260° sweep instead of 170°:
      // the extra hue travel is what buys separable neighbours, and both ends are now
      // named colours nobody has to squint at. Lightness still falls the whole way, so
      // the ramp reads as depth and not merely as a rainbow.
      const L = 0.84 - 0.44*f;            // light orange → dark purple
      const C = 0.15 - 0.02*f;            // ease off slightly so the deep end is not garish
      const H = 60 + (320-60)*f;          // orange → yellow → green → cyan → blue → purple
      out.push('oklch('+L.toFixed(3)+' '+C.toFixed(3)+' '+H.toFixed(1)+')');
    } else {
      // Fallback for anything without oklch: same journey in HSL, still generated.
      out.push('hsl('+(30+(290-30)*f).toFixed(0)+','+(90-15*f).toFixed(0)+'%,'+(68-40*f).toFixed(0)+'%)');
    }
  }
  return out;
})();
/* 0..1 -> one of the twelve bands. Exposed on its own because the ramp is no longer
   only the map's: the ballast fill and the top-bar numbers wear the same colours, so
   that "how deep" is one visual language across the whole console rather than a
   convention you have to learn twice. */
function rampColor(f){
  const n=DEPTH_RAMP.length;
  const i=Math.round(Math.max(0,Math.min(1,f||0))*(n-1));
  return DEPTH_RAMP[Math.min(n-1,Math.max(0,i))];
}
function _depthColor(d){
  return rampColor((d||0)/(CONFIG.map.maxDepthColorM||6));
}
/* PSI -> the depth it implies -> the same ramp. Pressure and depth therefore agree in
   colour whenever both sensors agree in fact, and DISAGREE the moment one of them
   starts lying - which is the whole reason to colour them separately. */
function pressureColor(psi){
  const sim=CONFIG.sim||{};
  const base=sim.basePressurePsi||14.7, per=sim.psiPerMeter||1.42;
  return _depthColor(((psi==null?base:psi)-base)/per);
}

/* Twenty colours mean nothing without a key. A compact vertical scale, drawn only in
   the views with room for it, with the surface at the top and the deepest band at the
   bottom — the same way down feels. */
function drawDepthLegend(ctx,w,h,dpr){
  if(!(MAP.expanded || MAP.blind) || !MAP.showTrack) return;
  const n=DEPTH_RAMP.length, bh=Math.max(5, Math.round(7*dpr)), bw=Math.round(11*dpr);
  // CLEAR OF THE RIGHT-HAND FURNITURE, which it was not. The key was placed 58 px in
  // from the canvas edge — but the canvas is the whole viewport in both views that
  // draw this, and the control rail (84 px, fixed) plus the map tool column (34 px at
  // right:94px) sit on top of it. The whole legend was therefore painted UNDERNEATH
  // them: the word DEPTH, half the ramp and both labels were behind the ballast
  // slider. It survived because a key you cannot read looks like a key you have not
  // looked at. 136 px clears the rail, the gap and the tool column together.
  const gutter = 136*dpr;
  const n2 = 58*dpr + gutter;
  const x=Math.round(w-bw-n2), y0=Math.round(h/2-(n*bh)/2);
  // Room for the second half of the key: the colour says HOW DEEP, and the two
  // swatches underneath say how much the number behind the colour is worth
  // (crt.js crtDrawDepthKey). They are drawn only when there is a depth overlay to
  // explain, so the box does not grow on a console that never had one.
  const keyed = (typeof crtDrawDepthKey==='function') && (typeof crtEntry==='function')
             && (typeof crtIsOn==='function') && (crtIsOn('depth_nominal') || crtIsOn('depth_surveyed'));
  ctx.save(); ctx.setTransform(1,0,0,1,0,0);
  ctx.fillStyle='rgba(12,1,24,.62)';
  ctx.fillRect(x-6*dpr, y0-16*dpr, bw+52*dpr, n*bh+30*dpr+(keyed?31*dpr:0));
  for(let i=0;i<n;i++){ ctx.fillStyle=DEPTH_RAMP[i]; ctx.fillRect(x, y0+i*bh, bw, bh); }
  ctx.strokeStyle='rgba(255,255,255,.25)'; ctx.lineWidth=1;
  ctx.strokeRect(x+.5, y0+.5, bw-1, n*bh-1);
  ctx.fillStyle='rgba(236,227,255,.9)';
  ctx.font=(9*dpr)+'px '+(getComputedStyle(document.body).fontFamily||'sans-serif');
  ctx.textBaseline='middle';
  ctx.fillText('DEPTH', x-2*dpr, y0-8*dpr);
  ctx.fillText('0 m', x+bw+5*dpr, y0+bh/2);
  // "+" because the deepest band is a CLAMP: everything past maxDepthColorM lands in it.
  ctx.fillText((CONFIG.map.maxDepthColorM||6)+'+ m', x+bw+5*dpr, y0+n*bh-bh/2);
  if(keyed){ try{ crtDrawDepthKey(ctx, x, y0+n*bh+8*dpr, bw, dpr); }catch(e){/* the key is not worth the map */} }
  ctx.restore();
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
  // THE CONFIGURED PROVIDER, WHICHEVER AREA IS ACTIVE. This said "offline tiles when
  // an area is active, else online", and _provider() does no such thing: it ignores
  // the argument and hands back CONFIG.map.tileProvider every time (tiles.js). That
  // is not a bug, it is how the offline archive actually works — SAVE OFFLINE warms
  // the service worker's tile cache with the SAME Esri URLs this provider builds
  // (store.js tileUrlsForBBox), and sw.js answers them cache-first, so a downloaded
  // area draws with the Pi and the internet both gone without the URL changing. The
  // `offline` entry in CONFIG.map.tileProviders is the other route — tiles served by
  // the Pi itself — and it is reached by setting tileProvider, never by having an
  // area. The area is still passed because _tileUrl fills {area} into that template.
  const prov = _provider(MAP.activeArea);
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

  // 3.5) DEPTH — nominal underneath everything, surveyed cells on top of it. It goes
  // here, below the grid lines and the centreline, because it is a WASH over the
  // water rather than a mark on it: laid over the vectors it would bury the very
  // things it is supposed to give context to. Nominal is hatched and surveyed is
  // solid (crt.js), which is this console's usual measured-versus-published
  // distinction wearing texture instead of a tag.
  if(haveProj && typeof crtDrawDepth==='function'){
    try{ crtDrawDepth(ctx,dpr); }catch(e){ LOG.warn('depth overlay:', e && e.message); }
  }

  // 4) waterway centreline over imagery (§3.5) — the snapping target + channel outline
  if(haveProj && MAP.centreline) drawCentreline(ctx,dpr);

  // 4.5) CHART LAYERS — extras, then operations, then the hazard keep-away marks on
  // top, so a mooring glyph can never be drawn over a lock. Its own error boundary:
  // a bad feature in one layer must not take the sub marker and the tether ring down
  // with it, because those are what the operator is flying on.
  if(haveProj && typeof crtDraw==='function'){
    try{ crtDraw(ctx,dpr); }catch(e){ LOG.warn('chart layers:', e && e.message); }
  }

  // 5) origin marker + dive track + sub marker — only with an origin (§6)
  if(MAP.hasOrigin){
    if(haveProj && TILES.last) drawTrackProjected(ctx,dpr,headingUp);
    else drawTrackMeterFrame(ctx,cx,cy,ppm,rot,headingUp);
  }

  // 6) area-selection rectangle + live readout (§4, expanded select mode)
  if(MAP.expanded && MAP.selectMode){ drawSelectionRect(ctx,dpr); if(live && MAP.selReadout) MAP.selReadout(mapSelectionBBox()); }

  // 7) depth key for the track colours, then imagery attribution (expanded)
  try{ drawDepthLegend(ctx,w,h,dpr); }catch(e){/* never let the key break the map */}
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
/* THE OPERATOR — green dot, live, auto-tracked. The purple arrow is the ROV.
   These are two different things and are drawn as two different things: after launch
   the operator walks and the sub does not, and the gap between them is the tether.

   No accuracy halo. It was a large translucent disc sitting on top of the imagery,
   and the imagery is the point — underwater structures and obstacles have to be
   readable. The orange tether ring is the only circle on this map. */
function drawMeMarker(ctx,dpr,x,y){
  ctx.save();
  ctx.fillStyle=meColor(); ctx.strokeStyle='#0c0118'; ctx.lineWidth=2*dpr;
  ctx.beginPath(); ctx.arc(x,y,5.5*dpr,0,Math.PI*2); ctx.fill(); ctx.stroke();
  // A mocked position is a hypothesis, so it is drawn as one — a broken ring around
  // the dot, readable even to an operator who cannot pick red out of green.
  if(meSource()==='mock'){
    ctx.setLineDash([3*dpr,3*dpr]); ctx.strokeStyle=C.meMock; ctx.lineWidth=1.5*dpr;
    ctx.beginPath(); ctx.arc(x,y,10*dpr,0,Math.PI*2); ctx.stroke();
  }
  ctx.restore();
}

/* Pan the view by a screen-space delta, in device pixels. Shared by the drag handler
   and the right stick so both feel identical. Panning always drops follow-the-sub —
   the operator has taken the view somewhere deliberately. */
function panMapPx(dxDev, dyDev){
  if(MAP.viewLat==null || !MAP.origin) return;
  const mpp=curScale()/ (MAP.dpr||1);                 // metres per DEVICE pixel
  const rot=(!MAP.expanded && MAP.headingUp) ? -MAP.hdg*Math.PI/180 : 0;
  const c=Math.cos(-rot), s=Math.sin(-rot);           // undo the heading-up rotation
  const ex=(-dxDev*c - (-dyDev)*s)*mpp;               // east metres
  const ny=((-dxDev)*s + (-dyDev)*c)*mpp;             // north metres (screen y is down)
  const p=toLocal(MAP.viewLat, MAP.viewLon, MAP.origin.lat, MAP.origin.lon);
  const g=toLatLon(p.x+ex, p.y-ny, MAP.origin.lat, MAP.origin.lon);
  MAP.viewLat=g.lat; MAP.viewLon=g.lon; MAP.follow=false;
}

/* Zoom whichever view is actually on screen. The collapsed radar has its own scale
   (a glance instrument), the expanded/blind map has another; zooming the wrong one
   looks like the control is dead. */
function zoomMap(dir){
  const f = dir>0 ? 1/1.3 : 1.3;
  const cl = v => Math.max(0.03, Math.min(40, v*f));
  if(MAP.expanded || MAP.blind) MAP.scale = cl(MAP.scale);
  else MAP.radarScale = cl(MAP.radarScale);
  LOG.map('map zoom '+(dir>0?'in':'out')+' -> '+curScale().toFixed(3)+' m/px');
}

/* Best imagery this provider has, expressed as metres/pixel at a given latitude.
   Mercator resolution is latitude-dependent, so a fixed m/px would land on a
   different tile zoom in different places — this pins the view to the deepest zoom
   that actually exists, which is what "max zoom" has to mean. */
function maxZoomScale(lat){
  const p=_provider(MAP.activeArea);
  const z=(p && p.maxzoom) || 19;
  return 156543.03392 * Math.cos((lat||0)*Math.PI/180) / (1<<z);
}
function bestScaleNow(){
  const lat = (MAP.viewLat!=null) ? MAP.viewLat : (MAP.origin ? MAP.origin.lat : 0);
  return maxZoomScale(lat);
}
/* THE REACHABLE CIRCLE — centred on the OPERATOR, because the operator is holding
   the cable. Not the launch point: the two are the same only until the first step
   along the bank, and after that a ring around the launch point is a drawing of
   somewhere the sub can no longer necessarily reach.

   `rpx` is passed in already measured in SCREEN pixels by the caller, using the same
   projection that placed the centre. Deriving it here from dpr/curScale() was wrong
   in the imagery view: the tiles are drawn through the tile projection, so the ring's
   centre and its radius came from two different mappings and the circle did not sit
   where its own arithmetic said it did. */
function drawTetherRing(ctx,dpr,ox,oy,rpx){
  const T=CONFIG.tether; if(!T || !T.showRing) return;
  if(!(rpx>4) || rpx>20000) return;                 // off-scale: skip rather than draw a wall
  MAP._lastRing = {x:ox, y:oy, r:rpx};              // exposed so a test can check it
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
  // The ring is centred on the OPERATOR and measured through the SAME projection that
  // placed them, by projecting a point one tether-length away and taking the screen
  // distance. Exact whatever the imagery is doing underneath.
  const me = operatorLL();
  const meS = me ? lonLatToScreen(me.lat, me.lon) : null;
  if(meS){
    const lim = tetherHorizLimitM();
    const edge = toLatLon(0, lim, me.lat, me.lon);            // `lim` metres due north
    const eS = lonLatToScreen(edge.lat, edge.lon);
    const rpx = eS ? Math.hypot(eS[0]-meS[0], eS[1]-meS[1]) : 0;
    drawTetherRing(ctx,dpr,meS[0],meS[1],rpx);
    drawMeMarker(ctx,dpr,meS[0],meS[1]);
  }
  if(oS){ ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
    ctx.moveTo(oS[0]-7*dpr,oS[1]);ctx.lineTo(oS[0]+7*dpr,oS[1]);ctx.moveTo(oS[0],oS[1]-7*dpr);ctx.lineTo(oS[0],oS[1]+7*dpr);ctx.stroke();
    ctx.beginPath();ctx.arc(oS[0],oS[1],10*dpr,0,7);ctx.stroke(); }
  // track: dark casing under a depth-coloured core (§5 legibility over imagery)
  const t=decimatedTrack();
  if(MAP.showTrack && t.length>1){
    const pts=t.map(p=>lonLatToScreen(...llOf(p)));
    ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.strokeStyle='rgba(0,0,0,.6)'; ctx.lineWidth=6*dpr; ctx.beginPath();
    let pen=false;
    for(let i=0;i<pts.length;i++){ const p=pts[i]; if(!p){ pen=false; continue; }
      if(!pen || t[i].brk){ ctx.moveTo(p[0],p[1]); pen=true; } else ctx.lineTo(p[0],p[1]); }
    ctx.stroke();
    ctx.lineWidth=3*dpr;
    for(let i=1;i<pts.length;i++){ const a=pts[i-1],b=pts[i]; if(!a||!b||t[i].brk) continue;  // never bridge a break
      ctx.strokeStyle=_depthColor(t[i].depth);
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
  drawTetherRing(ctx,dpr,aP[0],aP[1],tetherHorizLimitM()*ppm);
  drawMeMarker(ctx,dpr,aP[0],aP[1]);
  ctx.strokeStyle=C.origin; ctx.lineWidth=2*dpr; ctx.beginPath();
  ctx.moveTo(o[0]-7*dpr,o[1]);ctx.lineTo(o[0]+7*dpr,o[1]);ctx.moveTo(o[0],o[1]-7*dpr);ctx.lineTo(o[0],o[1]+7*dpr);ctx.stroke();
  ctx.beginPath();ctx.arc(o[0],o[1],10*dpr,0,7);ctx.stroke();
  const t=decimatedTrack();
  if(MAP.showTrack && t.length>1){ ctx.lineJoin='round'; ctx.lineCap='round';
    const pts=t.map(p=>L(p.x,p.y));
    ctx.strokeStyle='rgba(0,0,0,.5)'; ctx.lineWidth=6*dpr; ctx.beginPath();
    for(let i=0;i<pts.length;i++){ const p=pts[i];
      if(i===0 || t[i].brk) ctx.moveTo(p[0],p[1]); else ctx.lineTo(p[0],p[1]); }
    ctx.stroke();
    ctx.lineWidth=3*dpr;
    for(let i=1;i<pts.length;i++){ if(t[i].brk) continue;                 // never bridge a break
      ctx.strokeStyle=_depthColor(t[i].depth);
      ctx.beginPath(); ctx.moveTo(pts[i-1][0],pts[i-1][1]); ctx.lineTo(pts[i][0],pts[i][1]); ctx.stroke(); } }
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
/* CREDITS. This strip is where credits live on this console, so the Canal & River
   Trust line goes here beside the imagery one rather than on a new surface — the
   Open Government Licence asks for the words wherever the data is shown, and the
   map is where it is shown. Drawn only when CRT marks are actually on screen, which
   is the same rule the imagery credit already follows (it is drawn when tiles drew).
   The same sentence is also in the CHART LAYERS panel, where it can be selected and
   read by a screen reader — a credit painted into a canvas is legible to a human and
   to nothing else. */
function drawAttribution(ctx,w,h,dpr){
  const lines=[];
  const s=tileAttribution(); if(s) lines.push(s);
  if(typeof crtAnyPresent==='function' && typeof crtAttribution==='function' && crtAnyPresent())
    lines.push(crtAttribution());
  if(!lines.length) return;
  ctx.setTransform(1,0,0,1,0,0); ctx.font=(11*dpr)+'px sans-serif';
  const lh=16*dpr;
  let tw=0; for(const t of lines) tw=Math.max(tw, ctx.measureText(t).width);
  ctx.fillStyle='rgba(6,2,16,.5)'; ctx.fillRect(6*dpr, h-4*dpr-lh*lines.length, tw+12*dpr, lh*lines.length);
  ctx.fillStyle='rgba(236,227,255,.8)';
  lines.forEach((t,i)=>ctx.fillText(t, 12*dpr, h-8*dpr-lh*(lines.length-1-i)));
}

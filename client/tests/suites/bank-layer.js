/* WHAT THIS GUARDS — the LIDAR launch-bank layer, and the two ways a picture of the
   land beside the water can lie about it.

   THE FIRST LIE IS BARE IMAGERY. A satellite photograph of a canal shows two identical
   strips of green either side of a dark blue line, and a two-metre brick wall and a
   shelving grass bank photograph exactly the same from above. So a map with no paint on
   the banks is not a map that says "no low banks here" — it is a map that has not
   looked. The console has five different reasons for unpainted ground (NOT HERE,
   PARTIAL, ABSENT, UNREADABLE, CANNOT BUILD) plus NOT DOWNLOADED, and if any two of
   them arrive on the glass looking the same, the operator standing at the car with a
   12 kg vehicle and a tether drum has been told nothing. This suite drives four of
   those states through the console's own ingest and insists they are four different
   pictures — different word, different class on the row, different sentence, and not
   one of the sentences readable as a survey result.

   THE SECOND LIE IS AMBER ITSELF. Amber means "this ground is less than 2 m above the
   water beside it". It is a height difference and NOT permission to launch: it knows
   nothing about fences, gates, private land, live railway, reed beds or mud, and the
   recon run that proved the layer painted a railway cutting amber. Three limits have to
   be on the glass in words — that amber is not permission, that the water carries no
   paint because nothing here has measured depth, and that the terrain is a 2022 survey
   of a bank that may since have been rebuilt. A layer this persuasive with any one of
   those missing is worse than no layer.

   AND THE CHEVRONS, WHICH ARE THE SAME PROBLEM IN A GLYPH. The Trust publish an `angle`
   for their locks and bridges, and 267 features carry no angle at all — 265 bridges and
   2 locks. `angle || 0` turns every one of those into a chevron pointing due north,
   which is a bearing this console invented; and four real locks have a published angle
   of EXACTLY 0, so a check that only asks "did it change?" cannot tell an invented north
   from a measured one. This suite therefore asserts the drawn SHAPE: a lock with an
   angle is a chevron turned to it, a lock without one is the plain octagon with no
   front at all, and the two are told apart by their geometry rather than by a flag.

   A bridge's chevron also has to say what it is NOT. The Trust publish no flow
   measurement anywhere in this dataset, and an arrow across a canal at a bridge is
   exactly the shape an operator reads as "the water goes that way". It is the deck's
   orientation and nothing else, at 6,651 bridges.

   DRIVEN THROUGH THE REAL INGEST. window.fetch is stubbed and the Image src setter is
   redirected; everything after that is the shipping code — STORE.areaPut() registers an
   area exactly as SAVE OFFLINE does, bankLoad() reads the cards, bankStatus() classifies
   them, bankRenderRow() writes the row, and bankDraw()/crtDraw() paint. A suite that
   assigned BANK.areas directly would skip every property-name mismatch between the two
   halves, which is the failure this project has had five times. */
(function(){
  /* The ?sim=1 demo loads the shipping page into an IFRAME and run.py injects this
     suite into every /index.html it serves, including that one. A second copy running
     in the frame would drive a different console and POST its results over the top
     frame's. Only the top frame is the suite. */
  if(window.top !== window) return;

  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  /* Details quote sentences straight off the page and those carry em dashes and
     ellipses. run.py prints to a Windows console whose codepage cannot encode them and
     dies mid-report, taking every result after it: a report that cannot be printed is a
     report that did not run. */
  const safe=s=>String(s).replace(/[^\x20-\x7E -ÿ–—‘’“”•…]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const norm=s=>String(s||'').replace(/[\s ]+/g,' ').trim();
  async function waitFor(pred, ms){
    const t0=Date.now();
    while(Date.now()-t0 < ms){ try{ if(pred()) return true; }catch(e){} await sleep(40); }
    try{ return !!pred(); }catch(e){ return false; }
  }

  // ------------------------------------------------------------------ the world
  const LONO=-1.9055, LATO=52.4805;
  const AREA='bank-cut';                       // built, and the view sits inside it
  const BBOX=[LONO-0.010, LATO-0.008, LONO+0.010, LATO+0.008];
  const FAR ='far-cut';                        // built, and nowhere near the view
  const FARBOX=[LONO+0.30, LATO+0.30, LONO+0.32, LATO+0.32];
  const VINTAGE='2022';

  /* ONE DOCUMENT AT /api/bank, byte-shaped like the map backend's — property names
     included, because the interesting failure is not "the client mishandles a card", it
     is the two halves being written and read under different names and nobody noticing
     until the map is bare. */
  const TILE_TEMPLATE = '/api/bank/tiles/{z}/{x}/{y}.png';
  function areaRec(name, state, box){
    return {area:name, status:state, bbox:box,
            fetched:'2026-08-01T11:02:00Z', built:'2026-08-08T09:12:44Z',
            vintage:VINTAGE, tiles:412, pounds:2,
            why:({
              present:'the LIDAR covers this whole corridor and every pixel of it has been '
                    + 'classified.',
              partial:'the LIDAR reaches 71.4% of this corridor. The rest was not flown or not '
                    + 'delivered, and it is drawn as nothing — which is NOT the same as a bank '
                    + 'that was measured and found high.',
              absent: 'no launch-bank paint has been built for this area.',
            })[state] || ''};
  }
  function index(areas, extra){
    return Object.assign({
      layer:'bank', status:(areas||[]).length ? 'present' : 'absent',
      tiles:TILE_TEMPLATE, pounds:'/api/bank/pounds',
      minzoom:13, maxzoom:19, threshold_m:2.0, vintage:VINTAGE,
      attribution:'© Environment Agency copyright and/or database right '+VINTAGE
                 +'. Open Government Licence v3.0.',
      libraries:{ok:true, why:'numpy, scipy and Pillow are installed.', install:null},
      why:(areas||[]).length ? 'this handheld holds launch-bank paint.'
                             : 'no launch-bank paint has been built on this handheld.',
      areas: areas || [],
    }, extra||{});
  }
  const POUNDS = {
    type:'FeatureCollection', layer:'bank', survey_vintage:VINTAGE,
    features:[0,1].map(i=>({type:'Feature',
      geometry:{type:'Point', coordinates:[LONO+i*0.0009, LATO]},
      properties:{level_m_od: i ? 27.12 : 30.12, sheet_pixels:2000}})),
  };

  /* ---- THE CHART LAYERS, for the chevron half ---------------------------------
     The wire names are the real shape: api/nav/crt.py names a file after the ArcGIS
     SERVICE plus that service's layer number, never after a row in this console's
     table, so crtBind() is exercised rather than side-stepped. */
  const LOCK_ANGLE_A = 166;      // a real published bearing, off locks-0.geojson
  const LOCK_ANGLE_B = 76;       // exactly 90 degrees from it, so the drawing can be
                                 // compared to itself instead of to a convention
  /* THE FOUR LOCKS, and the pair that carries the whole check. `angle: 0` is a real
     published bearing — four locks on the network have it — and `angle: null` is two
     more that have none. `angle || 0` makes those two identical, which is a chevron
     pointing north that nothing measured. */
  const LOCKS = {type:'FeatureCollection', features:[
    {type:'Feature', properties:{OBJECTID:1, angle:LOCK_ANGLE_A, sap_description:'Lock A'},
     geometry:{type:'Point', coordinates:[LONO-0.0016, LATO+0.0006]}},
    {type:'Feature', properties:{OBJECTID:2, angle:LOCK_ANGLE_B, sap_description:'Lock B'},
     geometry:{type:'Point', coordinates:[LONO-0.0008, LATO+0.0006]}},
    {type:'Feature', properties:{OBJECTID:3, angle:0, sap_description:'Lock North'},
     geometry:{type:'Point', coordinates:[LONO+0.0000, LATO+0.0006]}},
    {type:'Feature', properties:{OBJECTID:4, angle:null, sap_description:'Lock Unknown'},
     geometry:{type:'Point', coordinates:[LONO+0.0008, LATO+0.0006]}},
  ]};
  const BRIDGES = {type:'FeatureCollection', features:[
    {type:'Feature', properties:{OBJECTID:9, angle:118, sap_description:'Bridge 1'},
     geometry:{type:'Point', coordinates:[LONO-0.0016, LATO-0.0006]}},
    {type:'Feature', properties:{OBJECTID:10, angle:null, sap_description:'Footbridge'},
     geometry:{type:'Point', coordinates:[LONO+0.0008, LATO-0.0006]}},
  ]};
  const wireOf = id=>String(id).replace(/_/g,'-')+'-0';
  const CRT_ATTRIB = 'Contains Canal & River Trust data © Canal & River Trust, licensed '
                   + 'under the Open Government Licence v3.0';

  // ------------------------------------------------------------------ the doubles
  const CALLS = {bank:0, pounds:0, tiles:0, tileUrls:[], crtIndex:0, crtLayer:{}};
  let BANK_DOC = null;                  // the /api/bank body, or null for a 404
  let BANK_TILES_OK = true;

  const json=(o,s)=>new Response(JSON.stringify(o), {status:s||200,
    headers:{'Content-Type':'application/json'}});
  const realFetch = window.fetch.bind(window);

  function netIndexBody(){
    const rows = crtAll().filter(e=>e.kind!=='depth').map(e=>({
      layer:wireOf(e.id), title:wireOf(e.id), status:'present', present:true,
      count:(e.id==='locks'?LOCKS:e.id==='bridges'?BRIDGES:{features:[]}).features.length,
      url:'/api/crt/'+wireOf(e.id)}));
    return {scope:'national', status:'present', attribution:CRT_ATTRIB,
            total:rows.length, layers:rows};
  }
  function crtBodyFor(sub){
    if(sub===wireOf('locks'))   return LOCKS;
    if(sub===wireOf('bridges')) return BRIDGES;
    return {type:'FeatureCollection', features:[]};
  }

  function stub(url, opts){
    const u = String((url && url.url) || url || '');
    const path = u.split('?')[0];

    /* ---- THE BANK STORE. Matched on a path with NO AREA IN IT, deliberately: if this
       console ever goes back to addressing the bank layer through an area, these
       requests stop arriving here and the section fails rather than passing on a
       fallback nobody noticed. */
    if(/\/api\/bank(\/|$)/.test(path)){
      const sub = decodeURIComponent((path.split('/api/bank')[1]||'').replace(/^\//,''));
      if(sub === 'pounds'){
        CALLS.pounds++;
        return Promise.resolve(BANK_DOC ? json(POUNDS) : json({detail:'no pounds'}, 404));
      }
      if(!sub){
        CALLS.bank++;
        if(BANK_DOC === 'dead') return Promise.reject(new TypeError('Failed to fetch'));
        if(!BANK_DOC) return Promise.resolve(json({detail:'no bank layer'}, 404));
        return Promise.resolve(json(BANK_DOC));
      }
      // A tile asked for through fetch rather than an Image: recorded, then answered,
      // so a change of transport does not silently stop this suite counting them.
      CALLS.tiles++; CALLS.tileUrls.push(u);
      return Promise.resolve(json({detail:'tiles are images'}, 404));
    }
    // ---- the national chart store
    if(/\/api\/crt(\/|$)/.test(path)){
      const sub = decodeURIComponent((path.split('/api/crt')[1]||'').replace(/^\//,''));
      if(sub === 'fetch') return Promise.resolve(json({detail:'no download'}, 404));
      if(!sub){ CALLS.crtIndex++; return Promise.resolve(json(netIndexBody())); }
      CALLS.crtLayer[sub] = (CALLS.crtLayer[sub]||0) + 1;
      return Promise.resolve(json(crtBodyFor(sub)));
    }
    // ---- the per-area chart card: this console has none, and says so
    if(/\/api\/areas\/[^/]*\/(crt|depth)(\/|$)/.test(path))
      return Promise.resolve(json({detail:'no card'}, 404));
    return realFetch(url, opts);
  }

  /* THE TILES ARE IMAGES, NOT FETCHES. crt.js loads them with `new Image()` and a src,
     exactly as tiles.js loads the satellite, so a fetch stub never sees them. The src
     SETTER is redirected instead, which leaves every other line of the loading path —
     the cache, the inflight count, onload, onerror, the budget and the drawImage —
     running as it ships. A 1x1 amber PNG is enough: nothing here reads the pixels back,
     the question is whether the tile is requested and drawn at all. */
  const AMBER_PNG =
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGPk'
  + 'nzn/PwAGVAKANDPQtQAAAABJRU5ErkJggg==';
  const NOT_A_PNG = 'data:text/plain,not-a-tile';
  let imgDesc = null;
  function hookImages(){
    imgDesc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable:true, enumerable:imgDesc.enumerable,
      get(){ return imgDesc.get.call(this); },
      set(v){
        const s = String(v||'');
        if(/\/api\/bank\/tiles\//.test(s)){
          CALLS.tiles++; CALLS.tileUrls.push(s);
          imgDesc.set.call(this, BANK_TILES_OK ? AMBER_PNG : NOT_A_PNG);
          return;
        }
        // Satellite tiles would go to the internet from a headless bench. Left to fail
        // as they always do here; TILES.last is set by the draw, not by the load.
        imgDesc.set.call(this, s);
      },
    });
  }

  // ------------------------------------------------------------------ reading it
  const $$ = id=>document.getElementById(id);
  const row  = ()=>$$('crt-row-bank');
  const pill = ()=>$$('crt-state-bank');
  const btn  = ()=>$$('crt-toggle-bank');
  const pillText = ()=>norm((pill()||{}).textContent);
  const rowClass = ()=>String((row()||{}).className||'');
  /* core.js's liveTitle appends the renderer's live sentence to the written one; only
     the live half is a claim about this handheld right now. */
  const liveOf = el=>{ if(!el) return '';
    const h=el.dataset ? (el.dataset.help||'') : '', t=el.getAttribute('title')||'';
    return norm((t.indexOf(h)===0 ? t.slice(h.length) : t).replace(/^[\s—-]+/,'')); };
  /* THE CONSOLE'S OWN WORDS, AND ONLY THOSE. dataset.help is what this file wrote;
     core.js's liveTitle then appends the live sentence — which quotes the SERVER's
     vintage, threshold and area names — into title and aria-label. Reading those would
     let a console whose own explanation had lost the survey year pass on the strength
     of a document happening to carry one, and crt.js's own rule is that these claims
     must never be conditional on a document turning up. */
  const written = ()=>{
    const r=row(), b=btn(), h=$$('crt-tier-bank');
    return norm([r&&r.dataset.help, b&&b.dataset.help, h&&h.dataset.help]
                .filter(Boolean).join(' '));
  };

  /* ---- A REAL 2D CONTEXT, TAPPED --------------------------------------------
     Every path the renderer submits, in DEVICE pixels, pushed through
     ctx.getTransform() at the moment it is issued — so a mark drawn by rotating the
     canvas and one drawn by rotating its own coordinates arrive here identically,
     which is right, because they are the same picture and the picture is what is
     under test. `ops` is the sequence, and the sequence is a safety property: the
     bank paint must go down BEFORE the hazard marks, never over them. */
  function tapCtx(w, h){
    const cv=document.createElement('canvas');
    cv.width=Math.max(2,w|0); cv.height=Math.max(2,h|0);
    const ctx=cv.getContext('2d');
    const st={subs:[], cur:null, ops:[], images:0};
    const dev=(x,y)=>{ const m=ctx.getTransform(); return [m.a*x+m.c*y+m.e, m.b*x+m.d*y+m.f]; };
    const raw={};
    ['beginPath','moveTo','lineTo','closePath','rect','arc','fill','stroke','fillText','drawImage']
      .forEach(k=>{ raw[k]=ctx[k].bind(ctx); });
    ctx.beginPath=function(){ st.subs=[]; st.cur=null; raw.beginPath(); };
    ctx.moveTo=function(x,y){ st.cur=[dev(x,y)]; st.subs.push(st.cur); raw.moveTo(x,y); };
    ctx.lineTo=function(x,y){ if(!st.cur){ st.cur=[]; st.subs.push(st.cur); }
                              st.cur.push(dev(x,y)); raw.lineTo(x,y); };
    ctx.closePath=function(){ raw.closePath(); };
    ctx.rect=function(x,y,rw,rh){ st.cur=[dev(x,y),dev(x+rw,y),dev(x+rw,y+rh),dev(x,y+rh)];
                                  st.subs.push(st.cur); raw.rect(x,y,rw,rh); };
    ctx.arc=function(x,y,r,a0,a1,cc){
      // Recorded as its bounding square so a dot has a shape in the record at all; the
      // checks below only ever ask "is this a circle" of it.
      st.cur=[dev(x-r,y-r),dev(x+r,y-r),dev(x+r,y+r),dev(x-r,y+r)]; st.cur.circle=true;
      st.subs.push(st.cur); raw.arc(x,y,r,a0,a1,cc); };
    ctx.fill=function(){ st.ops.push({kind:'fill', subs:st.subs.map(s=>s.slice()),
                                      circle:!!(st.subs[0]&&st.subs[0].circle)});
                         raw.fill(); };
    ctx.stroke=function(){ st.ops.push({kind:'stroke'}); raw.stroke(); };
    ctx.fillText=function(t,x,y){ st.ops.push({kind:'text', text:String(t), at:dev(x,y)});
                                  try{ raw.fillText(t,x,y); }catch(e){} };
    ctx.drawImage=function(){ st.images++; st.ops.push({kind:'image'});
                              try{ raw.drawImage.apply(null, arguments); }catch(e){} };
    return {ctx:ctx, st:st, canvas:cv};
  }

  /* THE MARK AT A POSITION. Every filled path whose vertices sit within `tol` device
     pixels of (x, y), turned into anchor-relative polar form. That is all a check
     needs: the radii say WHAT the shape is and the angle of the longest one says which
     way it faces, and neither depends on how the renderer chose to build it. */
  function markAt(st, x, y, tol){
    tol = tol || 26;
    let best=null;
    for(const op of st.ops){
      if(op.kind!=='fill' || !op.subs.length) continue;
      for(const sub of op.subs){
        if(sub.length<3) continue;
        let cx=0, cy=0;
        for(const p of sub){ cx+=p[0]; cy+=p[1]; }
        cx/=sub.length; cy/=sub.length;
        const d=Math.hypot(cx-x, cy-y);
        if(d<=tol && (!best || d<best.d))
          best={d:d, n:sub.length, circle:!!op.circle, cx:cx, cy:cy,
                v:sub.map(p=>({r:Math.hypot(p[0]-x, p[1]-y),
                               a:Math.atan2(p[1]-y, p[0]-x)*180/Math.PI}))};
      }
    }
    return best;
  }
  const rmax = m=>m ? Math.max.apply(null, m.v.map(p=>p.r)) : 0;
  const rmin = m=>m ? Math.min.apply(null, m.v.map(p=>p.r)) : 0;
  /* WHICH WAY THE MARK POINTS — from its CENTROID towards its ANCHOR.

     NOT "the bearing of its farthest vertex", which was the obvious thing and is wrong:
     an arrowhead's two tail corners are farther from the anchor than its tip is, so
     that measurement reads a tail, picks whichever of the two symmetric tails wins on a
     rounding, and reports a direction that jumps 172 degrees between two marks that
     differ by 90.

     The centroid is the robust one and needs to know nothing about the glyph. A shape
     with a front has its mass behind the anchor, so anchor-minus-centroid points the
     way the front does; a symmetric shape has its centroid ON the anchor and the vector
     vanishes, which is the honest answer for a mark with no direction to give. */
  function facing(m, x, y){
    if(!m) return null;
    const dx = x - m.cx, dy = y - m.cy;
    if(Math.hypot(dx,dy) < 0.05*rmax(m)) return null;   // no front to read
    return Math.atan2(dy, dx)*180/Math.PI;
  }
  // Every vertex the same distance out = a regular polygon = no front.
  const isRound = m=>!!m && (m.circle || (rmax(m)-rmin(m)) <= 0.06*rmax(m));
  const deltaDeg = (a,b)=>{ let d=(a-b)%360; if(d>180) d-=360; if(d<=-180) d+=360; return d; };
  const shapeOf = (m,x,y)=>{
    if(!m) return '(no mark)';
    const f = facing(m, x, y);
    return (m.circle?'circle':'poly') + ' n='+m.n+' rmin='+rmin(m).toFixed(2)
         + ' rmax='+rmax(m).toFixed(2)
         + ' facing='+(f===null?'(none)':f.toFixed(2));
  };

  // ------------------------------------------------------------------ the run
  async function run(){
    await sleep(2600);

    // ============ 0. THE CONTRACT, NAMED, SO A RENAME FAILS HERE AND NOT EVERYWHERE
    const names = {
      BANK: typeof BANK, bankLoad: typeof bankLoad, bankDraw: typeof bankDraw,
      bankStatus: typeof bankStatus, bankIsOn: typeof bankIsOn,
      bankSetOn: typeof bankSetOn, bankSentence: typeof bankSentence,
      BANK_API: typeof BANK_API, crtDraw: typeof crtDraw, crtWhat: typeof crtWhat,
      crtEntry: typeof crtEntry, lonLatToScreen: typeof lonLatToScreen,
      'crt-row-bank': !!row(), 'crt-toggle-bank': !!btn(), 'crt-state-bank': !!pill(),
    };
    const gone = Object.keys(names).filter(k=>names[k]==='undefined' || names[k]===false);
    ok('the bank layer is on the shipping page under the names this suite drives it by',
       gone.length===0,
       gone.length ? ('MISSING: '+gone.join(', ')+'  (found: '+JSON.stringify(names)+')')
                   : JSON.stringify(names));
    if(gone.length){
      fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
      return;
    }

    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    // state.keys is a Set, not an object — assigning {} to it breaks computeInput on
    // the very next frame ("k.has is not a function") and takes the whole console down
    // around the suite. Expanding the map engages ALL STOP and any held movement key
    // collapses it again immediately, which is why it is cleared at all.
    if(state.keys && state.keys.clear) state.keys.clear();
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(400);

    window.fetch = stub;
    hookImages();

    // ============ 1. THE STORE IS ASKED, AND THE LAYER FINDS ITSELF ============
    BANK_DOC = index([areaRec(AREA, 'present', BBOX)]);
    await STORE.areaPut({name:AREA, bbox:BBOX, zmin:16, zmax:19, tiles:0, cached:0,
                         detail:'standard', savedAt:Date.now(), mirrored:false});
    MAP.activeArea = AREA;
    // Put the view inside the built box, through the map's own origin path.
    await STORE.set('origin', {lat:LATO, lon:LONO, acc:5, src:'test'});
    MAP.origin = {lat:LATO, lon:LONO, acc:5, src:'test'};
    MAP.hasOrigin = true;
    if(typeof refreshBootstrap==='function') await refreshBootstrap();
    await sleep(300);

    // THE REAL DOOR IN. Nothing below assigns BANK.areas.
    await bankLoad('the bank-layer suite');
    await waitFor(()=>BANK.asked, 8000);
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await crtLoadAll('the bank-layer suite');
    await sleep(250);

    ok('the console asks this handheld what bank paint it holds, at a path with no area in it',
       CALLS.bank > 0 && BANK.asked && BANK.ok,
       'index fetches='+CALLS.bank+' asked='+BANK.asked+' ok='+BANK.ok+
       ' areas='+BANK.areas.length+' — nothing in this suite set BANK.areas');
    ok('...and the document is read under the property names the server actually writes',
       BANK.areas.length===1 && BANK.areas[0].status==='present' && !!BANK.areas[0].bbox &&
       BANK.areas[0].vintage===VINTAGE && BANK.tiles===TILE_TEMPLATE &&
       BANK.thresholdM===2.0 && BANK.vintage===VINTAGE,
       'area status='+(BANK.areas[0]||{}).status+' bbox='+JSON.stringify((BANK.areas[0]||{}).bbox)+
       ' area vintage="'+(BANK.areas[0]||{}).vintage+'" | index tiles="'+BANK.tiles+
       '" threshold='+BANK.thresholdM+' vintage="'+BANK.vintage+'" zoom '+BANK.minzoom+
       '-'+BANK.maxzoom+' libs.ok='+(BANK.libs||{}).ok);

    // ---- it DRAWS -----------------------------------------------------------
    // A real frame first, so TILES.last carries the projection bankDraw reasons from.
    if(typeof drawCanvas==='function') drawCanvas();
    await sleep(500);
    const L0 = (typeof TILES!=='undefined') ? TILES.last : null;
    ok('the map has left a projection behind for the overlay to be drawn against',
       !!(L0 && L0.worldTP && L0.k),
       'TILES.last='+(L0 ? ('worldTP='+L0.worldTP+' k='+L0.k.toFixed(4)+' rot='+(L0.rot||0)) : 'null'));

    // Two passes: the first requests the tiles, the second draws the ones that loaded.
    let t = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    bankDraw(t.ctx, (window.devicePixelRatio||1));
    await waitFor(()=>BANK.inflight===0, 6000);
    t = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    const drew = bankDraw(t.ctx, (window.devicePixelRatio||1));
    ok('the bank layer asks for its own tiles and paints the ones that arrive',
       CALLS.tiles>0 && t.st.images>0 && BANK.drew>0 && drew===true,
       'tile requests='+CALLS.tiles+' drawImage calls='+t.st.images+' BANK.drew='+BANK.drew+
       ' first url="'+(CALLS.tileUrls[0]||'')+'"');
    ok('...and the tiles it asks for are the template the SERVER quoted back, not a guess',
       CALLS.tileUrls.length>0 &&
       CALLS.tileUrls.every(u=>/\/api\/bank\/tiles\/\d+\/\d+\/\d+\.png$/.test(u)),
       'e.g. '+(CALLS.tileUrls[0]||'(none)')+'  ('+CALLS.tileUrls.length+' requests)');
    ok('the row reports HERE while it is painting',
       bankStatus()==='present' && /HERE/i.test(pillText()) && !/NOT HERE/i.test(pillText()),
       'status='+bankStatus()+' pill="'+pillText()+'" rowClass="'+rowClass()+'"');

    // ---- it is TOGGLEABLE ---------------------------------------------------
    const beforeTiles = CALLS.tiles;
    btn().click();
    await sleep(250);
    let tOff = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    const drewOff = bankDraw(tOff.ctx, (window.devicePixelRatio||1));
    ok('switching it off through the panel button stops it painting entirely',
       bankIsOn()===false && drewOff===false && tOff.st.images===0 && BANK.drew===0 &&
       CALLS.tiles===beforeTiles,
       'bankIsOn='+bankIsOn()+' drew='+drewOff+' drawImage='+tOff.st.images+
       ' BANK.drew='+BANK.drew+' new tile requests='+(CALLS.tiles-beforeTiles));
    const offSay = liveOf(row());
    ok('...and OFF says it was YOUR choice, not that the data is missing',
       bankStatus()==='off' && /NOT ASKED|switched this layer off|your choice/i.test(offSay) &&
       !/ABSENT|NOT DOWNLOADED/i.test(pillText()),
       'pill="'+pillText()+'" says: "'+offSay.slice(0,220)+'"');
    btn().click();
    await sleep(250);
    let tOn = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    bankDraw(tOn.ctx, (window.devicePixelRatio||1));
    ok('...and switching it back on paints again',
       bankIsOn()===true && tOn.st.images>0 && bankStatus()==='present',
       'bankIsOn='+bankIsOn()+' drawImage='+tOn.st.images+' status='+bankStatus());

    // ============ 2. THE THREE LIMITS, IN WORDS, ON THE GLASS ==================
    /* Every one of these is a sentence the layer would be dangerous without, and each
       is checked by TWO independent phrases so that rewording the explanation does not
       quietly delete the claim. */
    const words = written();
    const LIMITS = [
      ['AMBER IS A HEIGHT AND NOT PERMISSION TO LAUNCH',
       /(not|never)[^.]{0,80}(permission|allowed|that you can launch|a way in)/i,
       /(geometric fact|height difference|fences|private land|railway)/i],
      ['THE WATER IS NEVER PAINTED, BECAUSE NOTHING HERE KNOWS THE DEPTH',
       /water is never painted|never painted|no paint on the water/i,
       /(depth|shows through|unaltered)/i],
      ['THE TERRAIN IS A 2022 SURVEY AND A BANK CAN HAVE MOVED SINCE',
       /2022/,
       /(banks? (slump|change|move)|rebuilt|piled|overgrown|collapse)/i],
    ];
    LIMITS.forEach(([name, a, b])=>{
      const hit = a.test(words), why = b.test(words);
      ok('the console\'s OWN words state the limit: '+name, hit && why,
         'claim='+hit+' reason='+why+'  in '+words.length+
         ' chars of this console\'s own help text (the server\'s live sentence is '+
         'deliberately excluded)'+
         (hit&&why ? '' : ('  — text was: "'+words.slice(0,600)+'"')));
    });
    /* AND THEY SURVIVE A SERVER THAT SAYS NOTHING AT ALL. crt.js's rule is that these
       three claims must never be CONDITIONAL on a document turning up: a console
       showing bare imagery with no caveat attached is the failure the layer exists to
       prevent, and that is exactly the state a handheld is in before anything has been
       built. So the store is taken away and the three are asked for again. */
    const doc = BANK_DOC;
    BANK_DOC = null;
    await bankLoad('the bank-layer suite: nothing held');
    await waitFor(()=>!BANK._busy, 6000);
    await sleep(120);
    const bare = written();
    const bareMiss = LIMITS.filter(([, a, b])=>!(a.test(bare) && b.test(bare)))
                           .map(([n])=>n);
    ok('...and all three are still there with nothing downloaded and nobody to ask',
       bareMiss.length===0 && bankStatus()!=='present',
       'status with an empty store='+bankStatus()+'; limits missing: '+
       (bareMiss.join(' / ')||'none')+' ('+bare.length+' chars of own text)');
    BANK_DOC = doc;
    await bankLoad('the bank-layer suite: restore after the bare check');
    await waitFor(()=>!BANK._busy, 6000);

    ok('...and the explanation is carried in a title AND an aria-label, in whole sentences',
       !!(row().getAttribute('title') && row().getAttribute('aria-label')) &&
       norm(row().getAttribute('aria-label')).length > 200 &&
       !!(btn().getAttribute('title') && btn().getAttribute('aria-label')),
       'row title='+((row().getAttribute('title')||'').length)+' chars, aria='+
       (norm(row().getAttribute('aria-label')).length)+' chars; toggle title='+
       ((btn().getAttribute('title')||'').length)+' chars');

    // ============ 3. FOUR ABSENCES, FOUR PICTURES ==============================
    /* ABSENT, PARTIAL, NOT HERE and HERE are four different facts and an operator has
       to be able to act on the difference. What is being refused here is the map that
       looks the same in all of them, which is the map that teaches you to read bare
       imagery as "no low banks". */
    const seen = {};
    async function world(label, setup){
      await setup();
      await bankLoad('the bank-layer suite: '+label);
      await waitFor(()=>!BANK._busy, 6000);
      bankRenderRow();
      await sleep(120);
      const st = bankStatus();
      seen[label] = {status:st, pill:pillText(), cls:rowClass(),
                     say:liveOf(row()) || bankSentence(st)};
      return seen[label];
    }

    await world('present', async ()=>{
      BANK_DOC = index([areaRec(AREA,'present',BBOX)]); });
    await world('partial', async ()=>{
      BANK_DOC = index([areaRec(AREA,'partial',BBOX)]); });
    // ABSENT: the store answered, in words, and holds no paint for anywhere.
    await world('absent', async ()=>{
      BANK_DOC = index([areaRec(AREA,'absent',null)]); });
    // NOT HERE: the layer IS held, and it is held somewhere else. That is the state that
    // most looks like ABSENT on the glass and is the opposite claim.
    await world('outside', async ()=>{
      BANK_DOC = index([areaRec(FAR,'present',FARBOX)]); });
    // CANNOT BUILD: nothing painted AND this machine has not got the libraries. A job
    // that cannot be done at all, as against one nobody has done yet.
    await world('no-library', async ()=>{
      BANK_DOC = index([], {libraries:{ok:false,
        why:'The LIDAR launch-bank overlay cannot be built on this machine because scipy '
          + 'is not installed. Install with: pip install numpy scipy Pillow',
        install:'pip install numpy scipy Pillow'}}); });

    const keys = ['present','partial','absent','outside','no-library'];
    const pills = keys.map(k=>seen[k].pill);
    const uniq = new Set(pills);
    ok('HERE, PARTIAL, ABSENT, NOT HERE and CANNOT BUILD are five different words on the row',
       uniq.size===keys.length,
       keys.map(k=>k+'="'+seen[k].pill+'"').join('  '));
    const clss = keys.map(k=>seen[k].cls);
    ok('...and five different states on the row element, not only five strings',
       new Set(clss).size===keys.length,
       keys.map(k=>k+' class="'+seen[k].cls+'"').join('  |  '));
    const says = new Set(keys.map(k=>seen[k].say));
    ok('...and five different sentences underneath them',
       says.size===keys.length,
       keys.map(k=>k+': "'+seen[k].say.slice(0,80)+'..."').join('  |  '));
    ok('CANNOT BUILD names the library that is missing and the command that installs it',
       seen['no-library'].status==='no-library' &&
       /scipy/i.test(seen['no-library'].say) && /pip install/i.test(seen['no-library'].say) &&
       /(CAPABILITY|cannot make|no download)/i.test(seen['no-library'].say),
       'pill="'+seen['no-library'].pill+'" says: "'+seen['no-library'].say.slice(0,320)+'"');

    ok('ABSENT says nothing has been BUILT here, and does not read as an empty survey',
       seen.absent.status==='absent' && /ABSENT/.test(seen.absent.pill) &&
       /(no bank classification has been built|not been built|no.*built)/i.test(seen.absent.say) &&
       /(NO DATA|never "no low bank|has not been looked at|not a survey)/i.test(seen.absent.say),
       'pill="'+seen.absent.pill+'" says: "'+seen.absent.say.slice(0,300)+'"');
    ok('PARTIAL says part of what you can see is unpainted for a reason about the DATA',
       seen.partial.status==='partial' && /PARTIAL/.test(seen.partial.pill) &&
       /part/i.test(seen.partial.say) &&
       /(not been looked at|NOT bank that was measured|has not been surveyed)/i.test(seen.partial.say),
       'pill="'+seen.partial.pill+'" says: "'+seen.partial.say.slice(0,300)+'"');
    ok('NOT HERE says the layer IS held, elsewhere — the opposite claim to ABSENT',
       seen.outside.status==='outside' && /NOT HERE/.test(seen.outside.pill) &&
       /(painted|built|holds|held)[^.]{0,60}(for|on this handheld)/i.test(seen.outside.say) &&
       /not inside/i.test(seen.outside.say) && /far-cut/.test(seen.outside.say),
       'pill="'+seen.outside.pill+'" says: "'+seen.outside.say.slice(0,300)+'"');
    ok('and NOT HERE is not confusable with ABSENT by an eye reading only the word',
       seen.outside.pill !== seen.absent.pill &&
       !(/^ABSENT/.test(seen.outside.pill)),
       'NOT HERE pill="'+seen.outside.pill+'"  vs  ABSENT pill="'+seen.absent.pill+'"');
    /* THE ONE SENTENCE NONE OF THEM MAY BE READ AS. Every unpainted state has to deny
       the survey reading explicitly — "there is nothing here" is the failure this whole
       layer is built to avoid. */
    const denials = ['partial','absent','outside'].map(k=>({
      k:k, ok:/(not|never)[^.]{0,120}(no low bank|measured and found high|surveyed and found high|nobody has looked|has not been looked at|NOT the same as)/i
                .test(seen[k].say)}));
    ok('every unpainted state explicitly denies that unpainted means "no low banks here"',
       denials.every(d=>d.ok),
       denials.map(d=>d.k+'='+d.ok).join(' ') + '  ' +
       denials.filter(d=>!d.ok).map(d=>d.k+': "'+seen[d.k].say.slice(0,200)+'"').join(' | '));

    // back to a drawable world for the rest
    BANK_DOC = index([areaRec(AREA,'present',BBOX)]);
    await bankLoad('the bank-layer suite: restore');
    await waitFor(()=>!BANK._busy, 6000);
    await sleep(150);

    // ============ 4. THE PAINT GOES UNDER THE HAZARDS ==========================
    /* A weir mark buried under a wash of bank colour is a hazard this console holds and
       does not show. The order is decided in map.js — the bank raster at step 2.5, the
       chart layers at 4.5 — and this asserts it on the ONE context, in the sequence the
       ops were actually issued, rather than by reading the source. */
    const tOrder = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    let bankFirstOp=-1, bankLastOp=-1, crtFirstOp=-1;
    bankDraw(tOrder.ctx, (window.devicePixelRatio||1));
    bankFirstOp = tOrder.st.ops.length ? 0 : -1;
    bankLastOp = tOrder.st.ops.length - 1;
    crtDraw(tOrder.ctx, (window.devicePixelRatio||1));
    crtFirstOp = bankLastOp + 1;
    const bankImages = tOrder.st.ops.slice(0, bankLastOp+1).filter(o=>o.kind==='image').length;
    const crtOps = tOrder.st.ops.length - crtFirstOp;
    ok('both layers actually painted onto the one context this frame',
       bankImages>0 && crtOps>0,
       'bank ops='+(bankLastOp+1)+' (of which '+bankImages+' images), chart ops='+crtOps);

    /* AND THE SAME QUESTION ASKED OF THE APP RATHER THAN OF THIS SUITE: which of the
       two does the shipping renderer call first? Wrapping the globals records the real
       frame's order, so a change to map.js that put the paint over the marks fails
       here even though the two calls above are in this file's own order. */
    const order=[];
    const realBank = window.bankDraw, realCrt = window.crtDraw;
    window.bankDraw = function(){ order.push('bank'); return realBank.apply(this, arguments); };
    window.crtDraw  = function(){ order.push('crt');  return realCrt.apply(this, arguments); };
    try{
      if(typeof drawCanvas==='function') drawCanvas();
      await sleep(400);
    } finally { window.bankDraw = realBank; window.crtDraw = realCrt; }
    const iB = order.indexOf('bank'), iC = order.indexOf('crt');
    ok('the shipping frame lays the bank paint down BEFORE the hazard marks',
       iB>=0 && iC>=0 && iB<iC,
       'draw order this frame: '+(order.join(' -> ')||'(neither was called)'));

    // ============ 5. THE CHEVRONS =============================================
    /* Four locks: two with real bearings 90 degrees apart, one with a published 0, and
       one with none at all. The check is the DRAWN SHAPE, because every "did it change?"
       test walks straight past `angle || 0`. */
    const lockPos = LOCKS.features.map(f=>lonLatToScreen(f.geometry.coordinates[1],
                                                         f.geometry.coordinates[0]));
    ok('the four fixture locks are on screen to be drawn at all',
       lockPos.every(p=>!!p),
       'screen positions: '+JSON.stringify(lockPos));

    const tMark = tapCtx(L0 ? L0.w : 900, L0 ? L0.h : 600);
    crtDraw(tMark.ctx, (window.devicePixelRatio||1));
    const mA = lockPos[0] && markAt(tMark.st, lockPos[0][0], lockPos[0][1]);
    const mB = lockPos[1] && markAt(tMark.st, lockPos[1][0], lockPos[1][1]);
    const mZero = lockPos[2] && markAt(tMark.st, lockPos[2][0], lockPos[2][1]);
    const mNone = lockPos[3] && markAt(tMark.st, lockPos[3][0], lockPos[3][1]);
    const sA = shapeOf(mA, lockPos[0]&&lockPos[0][0], lockPos[0]&&lockPos[0][1]);
    const sB = shapeOf(mB, lockPos[1]&&lockPos[1][0], lockPos[1]&&lockPos[1][1]);
    const sZ = shapeOf(mZero, lockPos[2]&&lockPos[2][0], lockPos[2]&&lockPos[2][1]);
    const sN = shapeOf(mNone, lockPos[3]&&lockPos[3][0], lockPos[3]&&lockPos[3][1]);
    ok('all four locks reached the glass as marks',
       !!(mA && mB && mZero && mNone),
       'A='+sA+' | B='+sB+' | zero='+sZ+' | none='+sN);

    ok('a lock WITH a published angle is drawn as a chevron — a shape with a front',
       !!mA && !isRound(mA) && rmax(mA) > 1.25*rmin(mA),
       'angle '+LOCK_ANGLE_A+' drew '+sA+' (a shape whose vertices are all the '
       +'same distance out has no direction to read)');

    const fA = mA && facing(mA, lockPos[0][0], lockPos[0][1]);
    const fB = mB && facing(mB, lockPos[1][0], lockPos[1][1]);
    const turned = (fA!==null && fB!==null && fA!==undefined && fB!==undefined)
                   ? deltaDeg(fA, fB) : null;
    const wanted = deltaDeg(LOCK_ANGLE_A, LOCK_ANGLE_B);
    ok('...and turning the published angle turns the chevron by exactly that much',
       turned!==null && Math.abs(Math.abs(turned) - Math.abs(wanted)) < 3.0,
       'angles '+LOCK_ANGLE_A+' and '+LOCK_ANGLE_B+' are '+wanted+' deg apart; the two '
       +'chevrons face '+(fA==null?'?':fA.toFixed(2))+' and '+(fB==null?'?':fB.toFixed(2))
       +' on screen, a difference of '+(turned===null?'?':turned.toFixed(2))+' deg');
    ok('...and it turns the way a compass bearing turns, clockwise on the glass',
       turned!==null && (turned>0) === (wanted>0),
       'bearing went '+(wanted>0?'up':'down')+' by '+Math.abs(wanted)+' deg and the mark '
       +'turned '+(turned>0?'clockwise':'anticlockwise')+' by '
       +(turned===null?'?':Math.abs(turned).toFixed(2))+' deg — a mark that turns the '
       +'other way is annotating a different canal');

    /* THE PAIR THAT MATTERS. `angle || 0` collapses null onto 0, and four real locks on
       the network publish 0. If those two draw the same glyph, this console is showing
       an invented north at 267 features and there is nothing on the map that says so. */
    ok('a lock with NO published angle is drawn as the plain mark, with no front at all',
       !!mNone && isRound(mNone),
       'the lock with angle:null drew '+sN+' — a chevron here would be a '
       +'bearing this console made up');
    ok('...and a lock whose published angle IS zero still gets its chevron',
       !!mZero && !isRound(mZero),
       'angle:0 drew '+sZ);
    /* THE TWO MARKS COMPARED WITH EACH OTHER, and not each described on its own. A
       first version of this asked whether the angle:0 mark was pointed and got a `true`
       that had nothing to do with the angle:null one beside it — so under `angle || 0`,
       which draws both as the same chevron, it passed. The claim is that the two are
       DIFFERENT pictures, so the comparison has to be between them. */
    const zeroVsNone = (mZero && mNone)
      ? (mZero.n!==mNone.n || isRound(mZero)!==isRound(mNone))
      : false;
    ok('...so a published 0 and no angle at all are two different pictures',
       zeroVsNone,
       'angle:0 -> '+sZ+'   vs   angle:null -> '+sN+
       '   (`angle || 0` makes these identical, and 4 locks on the network publish 0)');
    /* AND THE ZERO IS A ZERO, not merely "not the plain mark". A chevron at 0 has to
       point the way a bearing of 0 points, or the console has kept the shape and lost
       the number. Measured against the chevron whose bearing is known to be right,
       rather than against a convention typed into this file. */
    const fZ = mZero && facing(mZero, lockPos[2][0], lockPos[2][1]);
    const zeroTurn = (fZ!=null && fA!=null) ? deltaDeg(fZ, fA) : null;
    ok('...and the zero-bearing chevron points where a bearing of zero points',
       zeroTurn!==null && Math.abs(Math.abs(zeroTurn) - Math.abs(deltaDeg(0, LOCK_ANGLE_A))) < 3.0,
       'angle:0 faces '+(fZ==null?'?':fZ.toFixed(2))+' and angle:'+LOCK_ANGLE_A+' faces '
       +(fA==null?'?':fA.toFixed(2))+' — '+(zeroTurn===null?'?':zeroTurn.toFixed(2))
       +' deg apart, against the '+deltaDeg(0, LOCK_ANGLE_A)+' the published bearings are');
    ok('and the layer counts what it drew as chevrons and what it could not',
       (CRT.state.locks||{}).chevrons===3 && (CRT.state.locks||{}).noAngle===1,
       'chevrons='+((CRT.state.locks||{}).chevrons)+' noAngle='+
       ((CRT.state.locks||{}).noAngle)+' hasAngle='+((CRT.state.locks||{}).hasAngle)+
       ' — the row has to be able to say how many marks are pointing at nothing');

    // ============ 6. A BRIDGE CHEVRON IS NOT A CURRENT =========================
    /* The Trust publish no flow measurement of any kind. An arrow across a canal is
       exactly the shape an operator reads as "the water goes that way", at 6,651
       bridges, and the sentence beside it is the only thing that can stop them. */
    const bEntry = crtEntry('bridges');
    const bridgeSay = norm([crtWhat(bEntry), bEntry && bEntry.angleWhat,
                            $$('crt-row-bridges') && $$('crt-row-bridges').dataset.help,
                            $$('crt-row-bridges') && $$('crt-row-bridges').getAttribute('aria-label')]
                           .filter(Boolean).join(' '));
    ok('the bridge row explains what its chevron IS — how the deck lies across the cut',
       /chevron/i.test(bridgeSay) &&
       /(how the deck lies|lies across|orientation|across the cut|angle)/i.test(bridgeSay),
       '"'+bridgeSay.slice(0,320)+'"');
    /* THE TEST IS NOT "DOES THE WORD FLOW APPEAR". The honest sentence has to use the
       word in order to deny it — "a chevron here never means water is going that way"
       contains every one of the incriminating words and is exactly the sentence that
       should be there. So the text is split into sentences and each is asked whether it
       is an UNQUALIFIED claim that water moves in some direction: the words, and no
       negation anywhere in the same sentence. A first pass that matched on the words
       alone failed the console for saying the right thing. */
    const sentences = s=>String(s).split(/(?<=[.!?;])\s+/).filter(x=>x.trim());
    const NEG = /\b(no|not|never|nothing|none|cannot|can't|does not|is not|are not|without)\b/i;
    const MOVES = /\b(goes|going|flows|flowing|runs|running|moves|moving|travels|current|flow)\b/i;
    const claiming = sentences(bridgeSay).filter(s=>
      /\b(water|current|flow)\b/i.test(s) && MOVES.test(s) && !NEG.test(s));
    const denies = /(no flow measurement|never means water is going|not a (flow|current|direction)|carries no flow|nothing (moves|flows) through)/i.test(bridgeSay);
    ok('...and never claims a water current from it',
       claiming.length===0 && denies,
       'unqualified claims that water moves: '+(claiming.length||'none')+
       (claiming.length ? (' -> "'+claiming.join('" / "')+'"') : '')+
       '; explicit denial present='+denies+' in: "'+bridgeSay.slice(0,400)+'"');
    const lockSay = norm([crtWhat(crtEntry('locks')), (crtEntry('locks')||{}).angleWhat]
                         .filter(Boolean).join(' '));
    const lockClaims = sentences(lockSay).filter(s=>
      /\b(water|current|flow)\b/i.test(s) && MOVES.test(s) && !NEG.test(s));
    ok('the lock chevron does not claim a current either',
       lockClaims.length===0 && /no flow measurement/i.test(lockSay),
       'unqualified claims: '+(lockClaims.length||'none')+
       (lockClaims.length ? (' -> "'+lockClaims.join('" / "')+'"') : '')+
       ' in "'+lockSay.slice(0,300)+'"');

    // ============ 7. NOTHING BROKE ============================================
    ok('no script errors', errs.length===0, errs.join(' | ') || 'none');

    if(imgDesc) Object.defineProperty(HTMLImageElement.prototype, 'src', imgDesc);
    window.fetch = realFetch;
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }

  run().catch(err=>{
    ok('the suite ran to the end', false, String(err && err.stack || err));
    try{ if(imgDesc) Object.defineProperty(HTMLImageElement.prototype, 'src', imgDesc); }catch(e){}
    window.fetch = realFetch;
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  });
})();

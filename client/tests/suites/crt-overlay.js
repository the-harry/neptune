/* WHAT THIS GUARDS — the chart layers, where an absence is drawn as a reassurance.

   A canal map with no marks on it is the most dangerous picture this console can
   produce, because it is what BOTH of these look like:

     the Pi has the locks layer, and there are no locks in this pound
     the Pi has never had the locks layer at all

   The first is a survey result. The second is the absence of one, and a pilot who
   reads it as the first drives a 5 kg sub and a hundred metres of tether towards an
   open paddle. So the panel has three words for three facts — SHOWN, NONE MAPPED,
   ABSENT — plus CANNOT TELL for a Pi that could not be asked, and this suite exists
   to prove that a missing file produces the third and never the second.

   The rest follows from the same place. The hazard tier has no off switch, because
   there is no operator preference that makes it right to hide a culvert. Tier 2 is
   on unless you say otherwise and tier 3 is off — and OFF has to say NOT ASKED,
   never anything that could be read as "there is nothing there". And the depth
   picture carries two different claims in the same twelve colours: a published
   design figure and a number this sub measured by touching the bottom, which must
   never be able to look like each other.

   DRIVEN THROUGH THE REAL INGEST. window.fetch is stubbed, and everything after
   that is the shipping code: STORE.areaPut() registers an area exactly as SAVE
   OFFLINE does, refreshBootstrap() notices it and calls crtSetArea(), which calls
   crtLoadAll(), which asks the index and then each layer and writes CRT.state. The
   bugs this is looking for live in that path — a 404 becoming an empty layer, an
   index failure becoming per-layer absence, a property name the server writes and
   the client does not read — and a suite that assigned CRT.state directly would
   skip every one of them and pass on a console that is lying.

   THE PAYLOADS ARE THE REAL ONES. The depth bodies below are byte-shaped like what
   api/nav/soundings.py's write_geojson() and api/nav/nominal.py actually emit,
   property names included, because the interesting failure is not "the client
   mishandles depth" — it is the two halves being written and read under different
   names and nobody noticing until the map is grey.

   TWO DEFECTS SEEN ON A REAL CONSOLE ADDED SECTIONS 0, 6, 7 AND 8.

   THE ALARM THAT MEANT NOTHING (sections 0, 6, 7). Opening this console on a bench —
   sim, no Pi, nothing ever downloaded — lit HAZARD LAYERS · CANNOT TELL, which is
   the loudest mark this map has. An area name remembered from an earlier session was
   enough: the index fetch had nowhere to go, every layer was marked unavailable, and
   the badge read that as "the hazard layers could not be asked for". Both of these
   are true sentences and they are NOT the same sentence:

     a Pi that was answering and stopped, or an area whose chart index has succeeded
     before          ->  CANNOT TELL, loudly, and keep re-asking
     a console that has never had chart data for this area and no link to ask down
                     ->  "no chart data downloaded yet", quietly, in the panel

   An alarm that fires on a healthy bench is an alarm that gets ignored, and the one
   it teaches you to ignore is the one that means you are missing hazard data for the
   water you are about to put a sub in. Nothing here relaxes the honesty rule: absent
   is still never invented as present, a layer that genuinely could not be asked for
   still says so, and section 6 asserts both of those in the same breath as the quiet.

   THE CELLS THAT LIED ABOUT THEMSELVES (section 8). The live surveyed-depth cells —
   the 3 m bins this session sounds for itself — were drawn as an AXIS-ALIGNED screen
   rect built from two DIAGONALLY OPPOSITE projected corners, so the extent on screen
   was the projection of a diagonal and not of the cell. Under the collapsed radar's
   heading-up rotation that collapses toward zero at 45° and every 90° after: turning
   or panning thinned every cell to a sliver and back. And they were binned off
   MAP.track with no check on whether the telemetry behind it was real, so a SIMULATED
   dive painted measured-style survey cells over water nothing has ever been in.

   NEITHER IS VISIBLE FROM THE RENDERER'S INPUTS. The cells, their depths and their
   metre coordinates were all correct the whole time. So section 8 taps a real 2D
   context and measures the AREA that reaches the glass, at several headings, and
   compares the TREATMENT each fill was painted with against the one the Pi's own
   saved survey uses. Assertions on inputs would have passed on the shipped defect. */
(function(){
  /* THE ?sim=1 SECTION LOADS THE SHIPPING PAGE INTO AN IFRAME, and run.py injects
     this suite into every /index.html it serves — including that one. A second copy
     running in the frame would drive a different console and POST its own results
     over the top frame's, so the report would describe whichever finished first.
     Only the top frame is the suite; inside the frame this file is inert and the
     frame is nothing but the console under test. */
  if(window.top !== window) return;
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote sentences straight off the page, and those contain em dashes and
  // ellipses. run.py prints to a Windows console whose codepage cannot encode them
  // and dies mid-report, taking every result after it: a report that cannot be
  // printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E -ÿ–—‘’“”•…]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const norm=s=>String(s||'').replace(/[ \s]+/g,' ').trim();

  const AREA='test-cut';
  const BBOX=[-1.92, 52.47, -1.89, 52.49];
  /* A SECOND AREA THAT HAS NEVER HAD CHART DATA, ANYWHERE. Its index never succeeds
     in this suite — INDEX_MODE is 'dead' for every fetch made under it — so however
     the console remembers "this area's index has answered before" (in memory, in
     IndexedDB, in a stamp on the saved area), there is nothing to remember about
     this one. That is the whole point of it: it is the bench console's area, and it
     is the one that used to raise the map's loudest alarm from a cold start. */
  const BENCH='bench-cut';
  const BBOX2=[-2.02, 52.55, -1.99, 52.57];
  /* The sentence a console with no chart data owes the operator. The spec's own
     words are "no chart data downloaded yet" — the load-bearing part is that it
     talks about the DOWNLOAD not having happened, which is a fact about this
     handheld, rather than about the water, which it knows nothing about. */
  const QUIET_RE = /download|no chart data/i;

  /* ---------------------------------------------------------------------------
     THE PI, AS THIS SUITE PRETENDS TO BE ONE.

     The index is the gate: crt.js asks it first and only its answering earns the
     console the right to call a per-layer 404 "absent". So the index is what
     decides most of what is checked below.
     --------------------------------------------------------------------------- */
  const LONO=-1.9055, LATO=52.4805;
  const fc=(feats)=>({type:'FeatureCollection', features:feats});
  const pt=(i,props)=>({type:'Feature',
    properties:Object.assign({OBJECTID:i, probe:'P'+i}, props||{}),
    geometry:{type:'Point', coordinates:[LONO+i*0.0004, LATO]}});

  // The one hazard layer whose file is NOT on this Pi. Deliberately tier 1: a
  // missing OPERATIONS layer is an inconvenience, and a missing hazard layer means
  // the map is not showing hazards at all.
  const ABSENT_ID='weirs';
  // ...and the one that IS on the Pi with nothing of its kind inside the area. The
  // whole suite turns on these two being told apart.
  const EMPTY_ID='culverts';
  // A layer the console's table has never heard of, named after something that
  // always means a hazard. crt.js adopts it as tier 1 and must say, in its own
  // words, that it guessed that from a string.
  const ADOPTED_ID='penstock_gates';

  /* SURVEYED DEPTH, exactly as api/nav/soundings.py write_geojson() emits it —
     lower_bound_m, bound:"lower", the `what` sentence, the three-point LineString
     along the channel, and the per-cell provenance. */
  const SURVEYED = {
    type:'FeatureCollection', quantity:'lower_bound_m', bound:'lower',
    means:'Each cell holds the DEEPEST depth the sub itself reached while the journal showed it '
        + 'resting on something solid. The bed is AT LEAST this deep and may be deeper.',
    unsurveyed:'Cells absent from this file are UNSURVEYED — the sub has never left bottom '
             + 'evidence there. Absent is not shallow, and it is not zero.',
    datum:'the water surface as it was on the day of each dive.',
    cell_m:10.0, surveyed:true, reason:null,
    features:[0,1].map(i=>({
      type:'Feature',
      geometry:{type:'LineString', coordinates:[
        [LONO+i*0.0006, LATO], [LONO+i*0.0006+0.0002, LATO], [LONO+i*0.0006+0.0004, LATO]]},
      properties:{
        lower_bound_m: i ? 3.00 : 2.60,
        bound:'lower',
        what:'LOWER BOUND: the bed here is AT LEAST '+(i?'3.00':'2.60')+' m below the surface — '
           + 'that is the deepest this sub reached while it was resting on something solid, not a '
           + 'measurement of the bed, which may be deeper.',
        from_m:i*10.0, to_m:(i+1)*10.0, line:0, cell:i,
        samples:20, contacts:2, dives:['dive-A','dive-B'],
        confidence_mean:1.0, confidence_min:1.0,
        deepest_from:{dive_id:'dive-B', t:12.5, confidence:1.0},
      }})),
  };

  /* NOMINAL DEPTH, exactly as api/nav/nominal.py emits it — nominal_depth_m, the
     three redundant flags, and the whole-layer caveat as a foreign member. */
  const NOMINAL = {
    type:'FeatureCollection', layer:'depth_nominal', status:'present',
    nominal:true, measured:false,
    features:[0,1,2].map(i=>({
      type:'Feature',
      geometry:{type:'LineString', coordinates:[
        [LONO+i*0.0006, LATO+0.0002], [LONO+i*0.0006+0.0004, LATO+0.0002]]},
      properties:{
        layer:'depth_nominal', section_id:'s000'+i, waterway:'broad canal',
        nominal:true, measured:false, is_survey:false,
        nominal_depth_m: 1.4 + i*0.3,
        depth_source:'class-guideline', depth_source_field:null,
        band_m:[1.2,1.5], shoals_to_banks:true, navigable:true,
        basis:'guideline draught for a broad canal, mid-channel',
        provenance:'Canal & River Trust waterway class',
        title:'NOMINAL DEPTH — the published design depth of this section.',
        aria_label:'Nominal depth, guidance and not a survey.',
      }})),
  };

  // What the fake Pi holds. `null` = the file is not on the disk (a per-layer 404).
  const BODIES = {};
  BODIES[EMPTY_ID]   = fc([]);
  BODIES[ABSENT_ID]  = null;
  BODIES['locks']    = fc([pt(1), pt(2), pt(3)]);
  BODIES['bridges']  = fc([pt(4), pt(5)]);
  BODIES['mileposts']= fc([pt(6)]);
  BODIES['depth_surveyed'] = SURVEYED;
  BODIES['depth_nominal']  = NOMINAL;

  /* ---------------------------------------------------------------------------
     THE NATIONAL STORE — THE ORDINARY CASE AFTER THIS ROUND.

     The whole Canal & River Trust network is fetched ONCE, nationally, on launch,
     and lives on the handheld. There is NO AREA IN ITS PATHS, which is the entire
     point: a console with no launch point, no offline area and no Pi still has a
     map, because the map is how this thing is navigated in every mode — on the
     water, in the simulator, and on a bench at home planning a run.

     The wire names are the real shape: api/nav/crt.py names a file after the ArcGIS
     SERVICE plus that service's layer number (`locks-0`, `tunnel-portals-0`), never
     after a row in this console's table. Building them that way here means crtBind()
     is exercised for every single layer rather than side-stepped by a suite that
     helpfully used the console's own ids.
     --------------------------------------------------------------------------- */
  const wireOf = id=>String(id).replace(/_/g,'-')+'-0';
  // A layer the national store looked for and does not hold. Deliberately one the
  // console has a ROW for: ABSENT has to survive this round intact, and a store that
  // answers about a layer it does not have is the one case a 404 may be read as
  // "that file is not on the disk".
  const NET_MISSING = 'water_points';
  const ring=(lon,lat,r,n)=>{ const p=[]; for(let k=0;k<n;k++){ const a=2*Math.PI*k/n;
      p.push([lon+r*Math.cos(a), lat+r*Math.sin(a)]); } p.push(p[0]); return p; };
  const NET_BODIES = {};
  const NET_COUNT  = {};
  function netBuild(){
    Object.keys(NET_BODIES).forEach(k=>delete NET_BODIES[k]);
    crtAll().forEach(e=>{
      if(e.kind==='depth' || e.id===NET_MISSING) return;      // depth is per-area BY NATURE
      const w = wireOf(e.id);
      // THE TWO HEAVY POLYGON LAYERS AND THE CENTRELINE get their real shapes. The
      // planning buffer is 82,880 B/feature nationally and is the thing most likely
      // to be drawn over the top of a weir; a suite that gave every layer a point
      // could not see that happen.
      if(e.id==='planning_buffer')
        NET_BODIES[w] = fc([{type:'Feature', properties:{OBJECTID:1, probe:'PB'},
          geometry:{type:'Polygon', coordinates:[ring(LONO, LATO, 0.0045, 96)]}}]);
      else if(e.id==='canals')
        NET_BODIES[w] = fc([{type:'Feature', properties:{OBJECTID:1, probe:'CN'},
          geometry:{type:'LineString',
                    coordinates:[[LONO-0.004,LATO-0.001],[LONO,LATO],[LONO+0.004,LATO+0.001]]}}]);
      else NET_BODIES[w] = fc([pt(1), pt(2)]);
      NET_COUNT[w] = (NET_BODIES[w].features||[]).length;
    });
  }
  function netIndexBody(){
    const rows = Object.keys(NET_BODIES).map(w=>({
      layer:w, title:w, status:'present', present:true, count:NET_COUNT[w],
      url:'/api/crt/'+w}));
    // The absent row travels IN the same array, as the backend sends it: the index
    // answering is what earns the console the right to say ABSENT at all, so it has
    // to say what is missing and not only what is there.
    rows.push({layer:wireOf(NET_MISSING), title:wireOf(NET_MISSING), status:'absent',
               present:false, count:null,
               why:'the Canal & River Trust publishes no service of this kind, so the '
                 + 'national fetch had nothing to ask for'});
    return {scope:'national', status:'present', attribution:CRT_ATTRIB_NET,
            total:rows.length, layers:rows};
  }
  const CRT_ATTRIB_NET = 'Contains Canal & River Trust data (c) Canal & River Trust, '
                       + 'licensed under the Open Government Licence v3.0';

  const CALLS = {index:0, layer:{}, urls:[], net:0, netLayer:{}, dl:0};
  let INDEX_MODE = 'ok';          // 'ok' | 'dead' | 'noservice'
  /* The national store, as this suite plays it. 'nothing' — a 404, the backend saying
     it holds nothing — is the DEFAULT for sections 0 to 8, because those sections are
     about the per-area card and that is exactly what the test server answers for
     /api/crt anyway. Section 9 is where the store comes up. */
  let NET_MODE = 'nothing';       // 'ok' | 'nothing' | 'dead'
  let DL = null;                  // the once-only download's progress, or null for a 404

  function bodyFor(id){
    if(Object.prototype.hasOwnProperty.call(BODIES, id)) return BODIES[id];
    return fc([pt(9)]);                       // anything else: present, one feature
  }
  function indexBody(){
    const out = {};
    // Every layer the console knows about, plus one it does not.
    crtAll().forEach(e=>{
      if(e.id===ADOPTED_ID) return;
      const b = bodyFor(e.id);
      out[e.id] = (b===null) ? {present:false}
                             : {present:true, count:(b.features||[]).length};
    });
    out[ADOPTED_ID] = {present:true, count:1};
    return {area:AREA, layers:out};
  }
  const json=(obj,status)=>new Response(JSON.stringify(obj),
    {status:status||200, headers:{'Content-Type':'application/json'}});

  const realFetch = window.fetch.bind(window);
  function stub(url, opts){
    const u = String((url && url.url) || url || '');
    const path = u.split('?')[0];
    /* ---- THE NATIONAL STORE. Matched on a path with NO AREA IN IT, deliberately:
       if this console ever goes back to addressing its chart layers through an area,
       these requests stop arriving here and section 9 fails rather than passing on a
       fallback nobody noticed. */
    if(/\/api\/crt(\/|$)/.test(path)){
      CALLS.urls.push(u);
      const sub = decodeURIComponent((path.split('/api/crt')[1] || '').replace(/^\//, ''));
      if(sub === 'fetch'){
        CALLS.dl++;
        return DL ? Promise.resolve(json(DL))
                  : Promise.resolve(json({detail:'no national download has been started'}, 404));
      }
      if(!sub){
        CALLS.net++;
        if(NET_MODE==='dead') return Promise.reject(new TypeError('Failed to fetch'));
        if(NET_MODE==='nothing')
          return Promise.resolve(json({detail:'the national chart store has not been '
                                              +'downloaded on this handheld'}, 404));
        return Promise.resolve(json(netIndexBody()));
      }
      CALLS.netLayer[sub] = (CALLS.netLayer[sub]||0) + 1;
      if(NET_MODE!=='ok') return Promise.reject(new TypeError('Failed to fetch'));
      const b = NET_BODIES[sub];
      if(b===undefined || b===null)
        return Promise.resolve(json({detail:'the national store does not hold that layer'}, 404));
      return Promise.resolve(json(b));
    }
    const isCrt = /\/api\/areas\/[^/]*\/(crt|depth)(\/|$)/.test(u);
    if(!isCrt) return realFetch(url, opts);
    CALLS.urls.push(u);
    const m = u.match(/\/api\/areas\/[^/]*\/(?:crt|depth)\/([^/?#]+)/);
    if(!m){                                   // the index itself
      CALLS.index++;
      if(INDEX_MODE==='dead') return Promise.reject(new TypeError('Failed to fetch'));
      if(INDEX_MODE==='noservice') return Promise.resolve(json({detail:'not found'}, 404));
      return Promise.resolve(json(indexBody()));
    }
    let id = decodeURIComponent(m[1]);
    if(id==='nominal') id='depth_nominal';
    if(id==='surveyed') id='depth_surveyed';
    CALLS.layer[id] = (CALLS.layer[id]||0) + 1;
    const b = bodyFor(id);
    if(b===null) return Promise.resolve(json({detail:'no such layer'}, 404));
    return Promise.resolve(json(b));
  }

  /* ---- reading the panel ------------------------------------------------- */
  const row  = id=>$('crt-row-'+id);
  const pill = id=>$('crt-state-'+id);
  const btn  = id=>$('crt-toggle-'+id);
  const lock = id=>$('crt-locked-'+id);
  const pillText = id=>norm((pill(id)||{}).textContent);
  const status = id=>((CRT.state[id]||{}).status||'(none)');
  // core.js's liveTitle appends the renderer's live sentence to the written one;
  // only the live half is a claim about this Pi right now.
  const liveOf = el=>{ if(!el) return ''; const h=el.dataset.help||'', t=el.getAttribute('title')||'';
    return norm((t.indexOf(h)===0 ? t.slice(h.length) : t).replace(/^[\s—-]+/,'')); };
  const entry = id=>crtEntry(id);

  async function waitFor(pred, ms){
    const t0=Date.now();
    while(Date.now()-t0 < ms){ if(pred()) return true; await sleep(40); }
    return !!pred();
  }
  // The REAL refresh path — the same call the REFRESH button and the background
  // retry make. Never CRT.state directly.
  async function reingest(why){
    await waitFor(()=>!CRT._busy, 8000);
    await crtLoadAll(why);
    await sleep(120);
  }

  /* A glyph's SHAPE with its colours thrown away, so two marks differing only in
     fill produce the same signature and fail. Same helper, same argument, as
     sensor-loss.js: an operator on a towpath in sunlight reads the outline first. */
  const GEO=['d','cx','cy','r','x','y','width','height','points','rx','stroke-dasharray'];
  function shapeSig(html){
    const box=document.createElement('div'); box.innerHTML=html||'';
    return [...box.querySelectorAll('svg *')].map(n=>
      n.tagName+'['+GEO.map(a=>n.getAttribute(a)).filter(v=>v!=null).join('|')+']'
    ).sort().join(' ') || '(empty)';
  }

  /* Luminance mean and spread of a patch of a canvas this suite owns. Untainted by
     construction — nothing cross-origin is ever drawn on it — so the pixels can
     actually be read, which is the only way to tell a texture from a flat fill. */
  function patch(ctx, x, y, w, h){
    const d = ctx.getImageData(x, y, w, h).data;
    const v = [];
    for(let i=0;i<d.length;i+=4) v.push(0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2]);
    const mean = v.reduce((a,b)=>a+b,0)/v.length;
    const sd = Math.sqrt(v.reduce((a,b)=>a+(b-mean)*(b-mean),0)/v.length);
    return {mean:Math.round(mean*10)/10, sd:Math.round(sd*10)/10, n:v.length};
  }

  /* ---- THE BADGE, AS AN OPERATOR'S EYE ACTUALLY MEETS IT -------------------
     .crt-absent is display:none until it is given the class `on` (css: .crt-absent.on
     {display:block}), and `tier1` is what turns it the error colour. So "is this map
     alarming?" is a question about the ELEMENT — its class, its computed display and
     the box it actually occupies — and never about CRT._badge or a status string. A
     quiet that exists only in a variable is not quiet. */
  function badgeLookIn(doc, win){
    const el = doc.getElementById('crt-absent');
    if(!el) return {missing:true, on:false, tier1:false, shown:false,
                    display:'(element missing)', h:0, w:0, text:'', cls:'', title:''};
    const cs = win.getComputedStyle(el), r = el.getBoundingClientRect();
    return {missing:false,
            on:el.classList.contains('on'), tier1:el.classList.contains('tier1'),
            display:cs.display, visibility:cs.visibility,
            colour:cs.color, border:cs.borderStyle+' '+cs.borderColor,
            h:Math.round(r.height), w:Math.round(r.width),
            shown:(cs.display!=='none' && cs.visibility!=='hidden' && r.height>0.5),
            text:norm(el.textContent), cls:el.className||'',
            title:norm(el.getAttribute('title')||'')};
  }
  const badgeLook = ()=>badgeLookIn(document, window);
  const badgeSay = b=>'badge shown='+b.shown+' class="'+b.cls+'" display='+b.display+
                      ' box='+b.w+'x'+b.h+'px colour='+b.colour+' border='+b.border+
                      ' text="'+b.text+'"';
  /* IS THIS THE ALARM? Not "is there a mark on the map" — an unmarked stretch of
     canal must never be readable as a surveyed empty one, so SOMETHING on the map
     saying "no data here" is right in every one of these states. What must not
     happen is the bench console wearing the mark that means a hazard layer this Pi
     was serving has gone. That mark is the tier-1 class, the error colour, and the
     three words HAZARD / CANNOT TELL / ABSENT. */
  const isAlarm = b=>!!b.tier1 || /HAZARD|CANNOT\s*TELL|ABSENT/i.test(b.text||'');

  /* ---------------------------------------------------------------------------
     WHAT ACTUALLY REACHES THE GLASS.

     The rotating-sliver defect cannot be caught by any check written against the
     renderer's INPUTS: the cells, their depths and their metre coordinates were all
     correct while the quad drawn from them collapsed to a line. So this taps a REAL
     2D context — createPattern, canvas.width and getTransform are the genuine
     article — and records the path every fill actually submits, in DEVICE pixels,
     by pushing each point through ctx.getTransform() at the moment it is issued.

     That last part is what makes the measurement independent of HOW the fix was
     written. Projecting four corners and filling a quad, or drawing a plain square
     inside a rotated local-metre transform, both arrive here as the same four
     device-space points and the same area — which is right, because they are the
     same picture, and the picture is the thing under test.
     --------------------------------------------------------------------------- */
  function polyArea(p){
    let a=0;
    for(let i=0,n=p.length;i<n;i++){ const q=p[(i+1)%n]; a += p[i][0]*q[1] - q[0]*p[i][1]; }
    return Math.abs(a)/2;
  }
  function pathArea(subs){
    let a=0; for(const s of subs) if(s.length>=3) a+=polyArea(s); return a;
  }
  function tapCtx(w, h){
    const cv=document.createElement('canvas');
    cv.width=Math.max(2, w|0); cv.height=Math.max(2, h|0);
    const ctx=cv.getContext('2d');
    /* `ops` is every paint IN THE ORDER IT WAS ISSUED, which `fills` is not: a
       polygon ring is stroked with no fill in front of it, so it lands on whichever
       fill came before — fine for measuring a depth cell, useless for asking which
       layer went down first. Draw ORDER is a safety property (a weir must never end
       up under a planning buffer) and the only way to check it is the sequence. */
    const st={subs:[], cur:null, fills:[], texts:[], ops:[]};
    const dev=(x,y)=>{ const m=ctx.getTransform(); return [m.a*x+m.c*y+m.e, m.b*x+m.d*y+m.f]; };
    const raw={};
    ['beginPath','moveTo','lineTo','closePath','rect','fill','stroke','fillText']
      .forEach(k=>{ raw[k]=ctx[k].bind(ctx); });
    const styleOf=v=>(typeof v==='string') ? v : 'pattern';
    ctx.beginPath=function(){ st.subs=[]; st.cur=null; raw.beginPath(); };
    ctx.moveTo=function(x,y){ st.cur=[dev(x,y)]; st.subs.push(st.cur); raw.moveTo(x,y); };
    ctx.lineTo=function(x,y){ if(!st.cur){ st.cur=[]; st.subs.push(st.cur); }
                              st.cur.push(dev(x,y)); raw.lineTo(x,y); };
    ctx.closePath=function(){ raw.closePath(); };
    ctx.rect=function(x,y,rw,rh){ st.cur=[dev(x,y),dev(x+rw,y),dev(x+rw,y+rh),dev(x,y+rh)];
                                  st.subs.push(st.cur); raw.rect(x,y,rw,rh); };
    ctx.fill=function(){ st.fills.push({area:pathArea(st.subs), alpha:ctx.globalAlpha,
                                        fill:styleOf(ctx.fillStyle), stroke:null});
                         st.ops.push({kind:'fill', style:styleOf(ctx.fillStyle),
                                      alpha:ctx.globalAlpha, area:pathArea(st.subs)});
                         raw.fill(); };
    // The edge belongs to the fill it was drawn around: every depth cell on this
    // console is fill-then-stroke, and the outline is half of what says "measured".
    ctx.stroke=function(){ const f=st.fills[st.fills.length-1];
      if(f && !f.stroke) f.stroke={alpha:ctx.globalAlpha, style:styleOf(ctx.strokeStyle),
                                   width:ctx.lineWidth, dash:(ctx.getLineDash()||[]).join(',')};
      st.ops.push({kind:'stroke', style:styleOf(ctx.strokeStyle), alpha:ctx.globalAlpha,
                   width:ctx.lineWidth, area:pathArea(st.subs)});
      raw.stroke(); };
    ctx.fillText=function(t,x,y){ st.texts.push(String(t)); raw.fillText(t,x,y); };
    return {ctx:ctx, st:st};
  }

  /* THE TREATMENT, WITH THE DEPTH COLOUR THROWN AWAY.

     All twelve depth colours are shared by the nominal wash, the surveyed cells and
     the dive track, so a hue says nothing about whether a cell is claiming to be a
     measurement. What says MEASURED is the treatment — opaque flat fill, solid white
     edge, no hatch and no dash, exactly as crtDrawDepthKey paints the SURVEYED
     swatch. Excluding the fill colour is the same argument the leak drop's shape
     signature makes: a difference that is only a colour is not a difference an
     operator reads on a towpath in sunlight, so a "simulated" cell that differs from
     a measured one by hue alone has not been distinguished from it. */
  function treat(f){
    return JSON.stringify({
      fillAlpha: Math.round((f.alpha||0)*100)/100,
      fillIsPattern: (f.fill==='pattern'),
      edgeAlpha: f.stroke ? Math.round((f.stroke.alpha||0)*100)/100 : null,
      edgeColour: f.stroke ? f.stroke.style : null,
      edgeWidth: f.stroke ? Math.round((f.stroke.width||0)*100)/100 : null,
      edgeDash:  f.stroke ? f.stroke.dash : null});
  }
  const treatSay = f=>'fill '+f.fill+' @'+(Math.round((f.alpha||0)*100)/100)+
    (f.stroke ? (', edge '+f.stroke.style+' @'+(Math.round((f.stroke.alpha||0)*100)/100)+
                 ' w'+(Math.round((f.stroke.width||0)*100)/100)+
                 (f.stroke.dash ? (' dashed['+f.stroke.dash+']') : ' solid'))
              : ', NO EDGE');

  /* Replay the SHIPPING depth renderer into a tapped context, with the projection
     state (TILES.last) exactly as the live frame just left it. Nothing here builds a
     projection of its own: the question is what the console drew, not what this
     suite thinks it should have. */
  function drawDepth(){
    const T=tapCtx((MAP.canvas&&MAP.canvas.width)||600, (MAP.canvas&&MAP.canvas.height)||600);
    let err=null;
    try{ crtDrawDepth(T.ctx, MAP.dpr||1); }catch(e){ err=(e&&e.message)||String(e); }
    const ppm=(typeof _crtPpm==='function') ? _crtPpm(MAP.dpr||1) : 0;
    const px=T.st.fills.reduce((a,f)=>a+f.area, 0);
    return {fills:T.st.fills, err:err, px:px, ppm:ppm,
            m2:(ppm>0 ? px/(ppm*ppm) : null),
            rot:(typeof TILES!=='undefined' && TILES.last) ? TILES.last.rot : null};
  }

  /* ---- A REAL VEHICLE, THROUGH THE CONSOLE'S OWN DOORS ---------------------
     handleMessage() is the WebSocket message handler and MAP.navWs.onmessage is the
     /ws/nav one: between them they are how a Pi arrives. A healthy 2S pack and a dry
     hull, so nothing below is incidentally flying an alarm. mock:false is what makes
     this a hull with real sensors rather than the bench simulator wearing a tether. */
  const TEL = {type:'telemetry', mock:false, armed:false, seq:1,
    heading:0, heading_card:'N', mag_cal:3,
    gyro_z_dps:0.0, accel_fwd_ms2:0.0, pitch_deg:0.0, roll_deg:0.0,
    depth:2.6, pressure:18.4, battery_v:8.1, current_a:1.2,
    ballast_level:0.3, ballast_homed:true, ballast_needs_rehome:false, ballast_target:0.3,
    left:0, right:0, magnet:false, light_green:false, light_white:false,
    light_green_level:0, light_white_level:0,
    leak:false, leak_state:'NORMAL', leak_probe_fault:null,
    speed_ms:0.42, speed_src:'paddle', signal:4, sensor_faults:[]};
  let telFeed=null;
  function startTel(){
    stopTel(); handleMessage(JSON.stringify(TEL));
    telFeed=setInterval(()=>handleMessage(JSON.stringify(TEL)), 100);
  }
  function stopTel(){ if(telFeed){ clearInterval(telFeed); telFeed=null; } }
  function navFrame(rx, hdg, x, y, d){
    rx({data:JSON.stringify({type:'nav', t:Date.now()/1000, lat:LATO, lon:LONO,
      depth_m:d, heading_deg:hdg, x_m:x, y_m:y, raw_lat:LATO, raw_lon:LONO, snapped:false,
      snap_offset_m:0, range_m:Math.hypot(x,y), payout_m:60, confidence:1, mag_cal:3,
      speed_ms:0.42, speed_src:'paddle', snagged:false, gyro_only:false, no_heading:false,
      has_origin:true, simulated:false, reads_vehicle:true})});
  }

  async function run(){
    await sleep(2600);

    // ================= SETUP: give the console an area, the way it really gets one
    ok('the chart-layer module is on the shipping page',
       typeof CRT==='object' && typeof crtLoadAll==='function' && !!$('crt-list'),
       'CRT='+(typeof CRT)+' crtLoadAll='+(typeof crtLoadAll)+' #crt-list='+(!!$('crt-list')));

    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(400);

    // ================= 0. A FRESH CONSOLE, WITH NO AREA AND NO PI =================
    // FIRST, and it has to be first: this is the console before any of the setup
    // below has given it an area, which is the state a handheld boots into out of the
    // box and the state the reported alarm came off. Nothing has been asked of
    // anybody, so nothing may be reported missing — and above all the loudest mark on
    // this map must not be lit by a console that has simply not started yet.
    const b0 = badgeLook();
    /* QUIET, NOT SILENT — and that is a change this round made on purpose. A console
       that holds none of the national store IS missing its map data, so a mark saying
       so is right; what must not happen is that mark being the ALARM, which means a
       hazard layer that was being served has gone. isAlarm() is the operator's eye:
       the tier-1 class, the error colour, and the three words this map shouts with. */
    ok('a fresh console with nothing downloaded raises NO map-level hazard alarm',
       CRT.area===null && !isAlarm(b0),
       'CRT.area='+CRT.area+'  '+badgeSay(b0)+' — a quiet mark here is right; the '+
       'loudest mark this map has is not, because nothing has failed and nobody has '+
       'been asked anything yet');
    const t1boot = crtTierList(1);
    const mute0 = t1boot.filter(e=>liveOf(row(e.id)).length < 20);
    ok('...and the panel is not blank about it either — every hazard row still explains itself',
       t1boot.length>0 && mute0.length===0,
       mute0.length ? ('silent rows: '+mute0.map(e=>e.id).join(', '))
                    : (t1boot.length+' hazard rows, e.g. '+t1boot[0].id+' says "'+
                       liveOf(row(t1boot[0].id)).slice(0,120)+'"'));
    const claimed0 = crtAll().filter(e=>['present','absent','empty'].indexOf(status(e.id))>=0);
    ok('...and with nobody asked, not one layer claims to know anything about the water',
       claimed0.length===0,
       claimed0.length ? ('claiming an answer: '+claimed0.map(e=>e.id+'='+status(e.id)).join(', '))
                       : ('all '+crtAll().length+' layers read "'+pillText(t1boot[0].id)+'"'));

    window.fetch = stub;
    await STORE.areaPut({name:AREA, bbox:BBOX, zmin:16, zmax:18, tiles:0, cached:0,
                         detail:'standard', savedAt:Date.now(), mirrored:false});
    // The console's own bootstrap notices the saved area and hands it to crt.js.
    // Nothing here sets CRT.area: if that wiring is broken, this check says so.
    await refreshBootstrap();
    const arrived = await waitFor(()=>CRT.area===AREA && CALLS.index>0 && !CRT._busy, 10000);
    await sleep(200);
    ok('saving an area is what makes the console ask the Pi for its chart layers',
       arrived && CRT.area===AREA,
       'CRT.area='+CRT.area+' index fetches='+CALLS.index+' layer fetches='+
       Object.keys(CALLS.layer).length+' — nothing in this suite set CRT.area');
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(150);

    const rows = [...document.querySelectorAll('#crt-list [data-layer]')];
    ok('every layer in the console\'s table has a row of its own',
       rows.length >= CRT_LAYERS.length,
       rows.length+' rows for '+CRT_LAYERS.length+' known layers (+'+CRT.extra.length+
       ' the Pi published that the table had never heard of)');

    // ================= 1. THE TIERS ARE A SAFETY DECISION =================
    const t1=crtTierList(1), t2=crtTierList(2), t3=crtTierList(3);
    ok('there is at least one layer in each tier (the premise of the next four checks)',
       t1.length>0 && t2.length>0 && t3.length>0,
       'tier1='+t1.length+' tier2='+t2.length+' tier3='+t3.length);

    const t1off = t1.filter(e=>!crtIsOn(e.id));
    ok('every hazard layer is ON', t1off.length===0,
       t1off.length ? ('switched off: '+t1off.map(e=>e.id).join(', '))
                    : t1.map(e=>e.id).join(', '));
    const t1switch = t1.filter(e=>btn(e.id));
    ok('...and not one of them has a switch to turn it off with', t1switch.length===0,
       t1switch.length ? ('these have a toggle button: '+t1switch.map(e=>e.id).join(', '))
                       : 'no toggle rendered on any hazard row');
    const t1locked = t1.filter(e=>lock(e.id) && /ALWAYS/i.test(norm(lock(e.id).textContent)));
    ok('...and each says ALWAYS where the switch would be, rather than nothing at all',
       t1locked.length===t1.length,
       'marked ALWAYS: '+t1locked.length+' of '+t1.length+' — a row with a blank there '
       +'reads as a switch that has not been drawn yet');

    // The setter itself refuses. A tier-1 layer must not be hideable by any route,
    // including the one a future feature would reach for.
    crtSetOn('locks', false);
    await sleep(80);
    ok('a hazard layer cannot be switched off even by asking the setter directly',
       crtIsOn('locks')===true,
       'crtIsOn("locks")='+crtIsOn('locks')+' after crtSetOn("locks", false)');

    /* NOTHING SHIPS SWITCHED OFF, AND THAT IS THE CHANGE THIS ROUND MADE.

       This block used to assert the opposite of its second half: extras were OFF by
       default. The console therefore held data about the water and drew none of it,
       and a layer that is present and invisible is a layer nobody knows they have —
       which on a canal means a planning buffer, a canal centreline and an aqueduct
       the operator could have been looking at and never saw offered.

       The tiers survive, and they still do real work: they decide what is drawn LAST
       and LOUDEST (section 9) and how the panel is grouped. What they no longer do is
       decide what is drawn AT ALL. The toggles stay, because pruning a busy map is a
       decision to make with the thing in front of you — it is simply not one to be
       made for the operator before they have ever seen it. */
    const allOff = crtAll().filter(e=>!crtIsOn(e.id));
    ok('every layer ships ON — hazards, operations and EXTRAS alike',
       allOff.length===0,
       allOff.length ? ('shipped switched off: '+allOff.map(e=>e.id+' (tier '+e.tier+')').join(', '))
                     : ('all '+crtAll().length+' layers on: tier1='+t1.length+' tier2='+
                        t2.length+' tier3='+t3.length+'. A layer that is held and hidden '+
                        'might as well not be held'));
    ok('...and the default is a rule, not a list somebody has to remember to extend',
       typeof crtDefaultOn==='function' && crtAll().every(e=>crtDefaultOn(e)===true),
       'crtDefaultOn says '+JSON.stringify(crtAll().slice(0,6).map(e=>e.id+'='+crtDefaultOn(e)))+
       ' — a per-tier default is how the next layer added gets shipped invisible');
    const t3asked = t3.filter(e=>e.id!==ABSENT_ID && !(CALLS.layer[e.id]>0));
    ok('...and the extras were genuinely ASKED FOR, not merely marked on in the panel',
       t3asked.length===0,
       t3asked.length ? ('never requested: '+t3asked.map(e=>e.id).join(', '))
                      : (t3.length+' extras, each fetched, e.g. '+t3[0].id+' = "'+
                         pillText(t3[0].id)+'"'));
    ok('...and every one of them still has a switch, so the operator can prune what they do not want',
       t3.every(e=>!!btn(e.id)) && t2.every(e=>!!btn(e.id)),
       'tier 2 and 3 rows with a toggle: '+
       (t2.filter(e=>btn(e.id)).length+t3.filter(e=>btn(e.id)).length)+' of '+
       (t2.length+t3.length)+' — on by default is not the same as forced on');

    // ================= 2. THE OPERATOR'S DECISION IS THE ONLY WAY OFF =========
    /* And it has to OUTLIVE the default. The whole risk of shipping everything on is
       that a console which resets to "on" every launch makes the operator prune the
       same map every single time, which is how a panel of toggles becomes a panel
       nobody touches. Their decision persists; the default does not override it. */
    ok('the handheld has somewhere to remember a choice (the premise of the next five)',
       STORE.ready===true, 'STORE.ready='+STORE.ready+
       ' — without IndexedDB nothing below is testable and nothing is remembered');

    const T3='mileposts';
    ok('the layer starts ON and loaded, so the only thing that can switch it off is the operator',
       crtIsOn(T3)===true && (CALLS.layer[T3]||0)>0,
       T3+': on='+crtIsOn(T3)+' fetched '+(CALLS.layer[T3]||0)+' time(s), status='+
       status(T3)+' pill="'+pillText(T3)+'"');
    btn(T3).click();                            // the real control, the real listener
    await waitFor(()=>!crtIsOn(T3), 3000);
    await sleep(400);                           // crtSavePrefs is fire-and-forget
    const saved = await STORE.get('crt.layers', null);
    ok('switching a layer OFF writes that decision to the handheld\'s own storage',
       crtIsOn(T3)===false && !!saved && saved[T3]===false,
       'crtIsOn="'+crtIsOn(T3)+'" stored='+JSON.stringify(saved));
    /* OFF MUST NOT LOOK LIKE ABSENT, and there are now two honest ways to say it
       depending on whether the body is already in hand. Immediately after the switch
       the console HAS the layer and says so — "HERE, held but switched off" — and
       after the next ingest it stops asking and says NOT ASKED. Both are true, and
       what neither may ever be is a word about the WATER: ABSENT, NONE MAPPED and
       NOT DOWNLOADED are findings, and the operator flicking a switch is not one. */
    const offSay = pillText(T3)+' — '+liveOf(row(T3));
    ok('...and OFF reads as the OPERATOR\'S decision, never as anything about the water',
       /switched off|NOT ASKED/i.test(offSay) &&
       !/\bABSENT\b|NONE MAPPED|NOT DOWNLOADED/i.test(pillText(T3)),
       T3+': status='+status(T3)+' pill="'+pillText(T3)+'" says "'+
       liveOf(row(T3)).slice(0,130)+'"');

    // Read it back through the console's own loader, which is what a fresh boot does.
    const askedBefore = CALLS.layer[T3]||0;
    CRT.prefs = null;
    await crtLoadPrefs();
    ok('...and the console reads the decision back rather than starting from the default again',
       crtIsOn(T3)===false,
       'after crtLoadPrefs(): crtIsOn("'+T3+'")='+crtIsOn(T3)+' prefs='+JSON.stringify(CRT.prefs));

    await reingest('suite: re-ingest with a layer the operator switched off');
    // The sentence has to carry two things: that this was the OPERATOR'S doing (now
    // the only way a layer is ever off) and that it is not a claim about the water.
    ok('...and once the console stops asking for it, the row says NOT ASKED, and whose choice it was',
       status(T3)==='off' && /NOT ASKED/i.test(pillText(T3)) &&
       /you switched|your choice/i.test(liveOf(row(T3))) &&
       /not a claim that the layer is missing/i.test(liveOf(row(T3))),
       T3+': status='+status(T3)+' pill="'+pillText(T3)+'" says "'+
       liveOf(row(T3)).slice(0,130)+'"');
    ok('a refresh does not quietly switch it back on, and does not fetch it behind their back',
       crtIsOn(T3)===false && status(T3)==='off' && (CALLS.layer[T3]||0)===askedBefore,
       'crtIsOn='+crtIsOn(T3)+' status='+status(T3)+' fetched '+(CALLS.layer[T3]||0)+
       ' time(s) (was '+askedBefore+') — a refresh that resets the panel is a refresh '+
       'nobody can trust, and it is worse now that the default is ON');

    btn(T3).click();
    await waitFor(()=>crtIsOn(T3), 3000);
    await sleep(400);
    const saved2 = await STORE.get('crt.layers', null);
    ok('switching it back on is remembered too, rather than only the "off" being saved',
       crtIsOn(T3)===true && !!saved2 && saved2[T3]===true && status(T3)==='present',
       'crtIsOn='+crtIsOn(T3)+' status='+status(T3)+' stored='+JSON.stringify(saved2));
    // Left OFF on purpose: section 7 boots a REAL page in an iframe, off the same
    // IndexedDB, and asks it whether the operator's decision survived the reload.
    btn(T3).click();
    await waitFor(()=>!crtIsOn(T3), 3000);
    await sleep(400);

    // ================= 3. EVERY MARK EXPLAINS ITSELF =================
    const described = el=>{ const t=(el.getAttribute('title')||'').trim();
      const a=(el.getAttribute('aria-label')||'').trim(); return {t:t, a:a}; };
    const bare = rows.filter(r=>described(r).t.length < 40);
    ok('every layer row carries a written sentence saying what that mark MEANS',
       bare.length===0,
       bare.length ? ('barely a label: '+bare.map(r=>r.dataset.layer+' ("'+
                      described(r).t.slice(0,40)+'")').join(', '))
                   : rows.length+' rows described');
    const unread = rows.filter(r=>described(r).a.length < 40);
    ok('...and a screen reader is given the same sentence', unread.length===0,
       unread.length ? ('no aria-label: '+unread.map(r=>r.dataset.layer).join(', '))
                     : rows.length+' rows have aria-label');
    const mismatched = rows.filter(r=>described(r).a !== described(r).t);
    ok('...and the two say the same thing, so nobody is reading a different console',
       mismatched.length===0,
       mismatched.length ? ('title and aria-label differ on: '+
                            mismatched.map(r=>r.dataset.layer).join(', '))
                         : 'title === aria-label on all '+rows.length);

    // THE TWO CLAUSES EVERY HAZARD MARK OWES. CRT publish no flow measurement of any
    // kind, and the keep-away ring is this console's own invention — a mark that let
    // either go unsaid would be handing an operator the one number they most want
    // and never measured.
    const hazardRows = t1.concat(crtAll().filter(e=>e.hazardish));
    const noFlow = hazardRows.filter(e=>!/no flow measurement/i.test((row(e.id)||{}).title||''));
    ok('every hazard mark says CRT never measured any flow, so it is not claiming one',
       noFlow.length===0,
       noFlow.length ? ('silent about flow: '+noFlow.map(e=>e.id).join(', '))
                     : hazardRows.length+' hazard rows carry the no-flow clause');
    // THE STANDOFF IS A SENTENCE NOW, NOT A RING. The dashed circle was deleted — around
    // every tier-1 mark it buried the centreline under overlapping rings — so the phrase
    // moved from "standoff this console draws" to "...states". The CHECK is unchanged and
    // deliberately just as strict: every layer carrying a keep-away distance must still say
    // out loud that the number is ours and not a surveyed danger area. Only the drawing it
    // used to refer to has gone.
    const ringed = hazardRows.filter(e=>e.standoffM);
    const noRing = ringed.filter(e=>!/standoff this console states/i.test((row(e.id)||{}).title||''));
    ok('...and that the keep-away distance is ours, not a surveyed danger area',
       noRing.length===0,
       noRing.length ? ('silent about the standoff: '+noRing.map(e=>e.id).join(', '))
                     : ringed.length+' layers with a standoff say whose number it is');

    // A LAYER THE CONSOLE HAD NEVER HEARD OF. It was tiered off its NAME, which is a
    // guess, and a guess presented as a rule is the same defect as an estimate
    // presented as a measurement.
    const adopted = crtEntry(ADOPTED_ID);
    ok('a layer the Pi published and this console has never heard of is still offered',
       !!adopted && !!row(ADOPTED_ID),
       adopted ? (ADOPTED_ID+': tier '+adopted.tier+', adopted='+!!adopted.adopted)
               : 'the console dropped a layer the Pi published');
    ok('...and it admits it was classified by its NAME rather than by a rule anybody wrote',
       !!adopted && /NO ENTRY FOR THIS LAYER/i.test((row(ADOPTED_ID)||{}).title||''),
       'says: "'+norm(((row(ADOPTED_ID)||{}).title||'')).slice(0,120)+'"');

    // The tier headings, the panel furniture and the credit are glyphs too.
    const furniture=['crt-panel-title','crt-refresh','crt-close','map-crt-toggle','crt-credit',
                     'crt-tier-1','crt-tier-2','crt-tier-3'];
    const mute = furniture.filter(id=>{ const el=$(id);
      return !el || (el.getAttribute('title')||'').trim().length<25 ||
             (el.getAttribute('aria-label')||'').trim().length<25; });
    ok('the panel\'s own controls and headings explain themselves as well',
       mute.length===0,
       mute.length ? ('undescribed: '+mute.join(', ')) : furniture.length+' described');

    // ================= 4. ABSENT IS NOT EMPTY =================
    ok('the layer whose file is missing reports ABSENT',
       status(ABSENT_ID)==='absent' && /ABSENT/i.test(pillText(ABSENT_ID)),
       ABSENT_ID+': status='+status(ABSENT_ID)+' pill="'+pillText(ABSENT_ID)+'"');
    ok('the layer that is present with nothing in this area reports NONE MAPPED',
       status(EMPTY_ID)==='empty' && /NONE MAPPED/i.test(pillText(EMPTY_ID)),
       EMPTY_ID+': status='+status(EMPTY_ID)+' pill="'+pillText(EMPTY_ID)+'"');
    ok('...and the two are DIFFERENT WORDS on screen, not one word for two facts',
       pillText(ABSENT_ID) !== pillText(EMPTY_ID),
       '"'+pillText(ABSENT_ID)+'" vs "'+pillText(EMPTY_ID)+'" — an empty map means '
       +'opposite things under those two');
    ok('...and they are styled differently, so the difference survives a glance',
       (pill(ABSENT_ID).className||'') !== (pill(EMPTY_ID).className||'') &&
       (row(ABSENT_ID).className||'') !== (row(EMPTY_ID).className||''),
       'absent pill="'+pill(ABSENT_ID).className+'" row="'+row(ABSENT_ID).className+'"  vs  '
       +'empty pill="'+pill(EMPTY_ID).className+'" row="'+row(EMPTY_ID).className+'"');
    const absSentence = liveOf(row(ABSENT_ID));
    ok('the absent row says the map is showing NO DATA, not that there is nothing there',
       /NO DATA/i.test(absSentence) && !/^NONE MAPPED/i.test(absSentence),
       '"'+absSentence.slice(0,160)+'"');
    const emptySentence = liveOf(row(EMPTY_ID));
    ok('...and the empty row makes the opposite, positive claim, in its own words',
       /NONE MAPPED HERE/i.test(emptySentence) && /not the same/i.test(emptySentence),
       '"'+emptySentence.slice(0,160)+'"');
    ok('a layer that is absent does not report a feature count of zero beside it',
       !/\b0\b/.test(pillText(ABSENT_ID)),
       'pill="'+pillText(ABSENT_ID)+'" — "ABSENT · 0" would be the same lie with a '
       +'label on it');

    // AND THE MAP ITSELF SAYS SO. A panel the operator has to open first cannot
    // deliver a doctrine about what an empty-looking map means.
    const badge=$('crt-absent');
    ok('the MAP says a hazard layer is missing, not only the panel nobody has opened',
       !!badge && /\bon\b/.test(badge.className) && /HAZARD/i.test(norm(badge.textContent)),
       'badge="'+norm((badge||{}).textContent)+'" class="'+((badge||{}).className||'')+'"');
    ok('...and the badge spells out that a blank stretch now means NO DATA',
       !!badge && /NO DATA/i.test(badge.getAttribute('title')||'') &&
       (badge.getAttribute('aria-label')||'').length>60,
       '"'+norm((badge||{}).getAttribute('title')||'').slice(0,150)+'"');

    // NOBODY ASKED, SO NOTHING MAY BE REPORTED MISSING. With the index unreachable
    // the console has learned nothing at all, and per-layer absence would be a claim
    // it has no standing to make.
    INDEX_MODE='dead';
    await reingest('suite: the Pi cannot be reached');
    const claimed = crtAll().filter(e=>status(e.id)==='absent');
    ok('when the Pi cannot be asked at all, NOTHING is reported absent',
       claimed.length===0,
       claimed.length ? ('claimed absent with no evidence: '+claimed.map(e=>e.id).join(', '))
                      : 'every layer says '+status('locks'));
    ok('...they all say CANNOT TELL instead, which is what the console actually knows',
       status('locks')==='unavailable' && /CANNOT TELL/i.test(pillText('locks')),
       'locks: status='+status('locks')+' pill="'+pillText('locks')+'" says "'+
       liveOf(row('locks')).slice(0,120)+'"');
    INDEX_MODE='ok';
    await reingest('suite: the Pi is back');
    ok('and the layers come back when the Pi does — a blank that never clears is its own fault',
       status('locks')==='present' && status(ABSENT_ID)==='absent',
       'locks='+status('locks')+' '+ABSENT_ID+'='+status(ABSENT_ID));

    // ================= 5. MEASURED DEPTH MUST NOT LOOK PUBLISHED =================
    const nom=entry('depth_nominal'), sur=entry('depth_surveyed');
    ok('both depth layers ingested from the Pi',
       status('depth_nominal')==='present' && status('depth_surveyed')==='present',
       'nominal='+status('depth_nominal')+' ('+(CRT.state.depth_nominal||{}).n+' cells)  '
       +'surveyed='+status('depth_surveyed')+' ('+(CRT.state.depth_surveyed||{}).n+' cells)');
    ok('the surveyed row states the claim precisely: a floor under the water, not the bed',
       /floor under|at least|lower bound|not the depth of the bed/i.test(crtWhat(sur)),
       '"'+norm(crtWhat(sur)).slice(0,170)+'"');
    ok('the nominal row says it is a published figure and NOT a measurement',
       /published|design depth|supposed to be/i.test(crtWhat(nom)) &&
       /never be mistaken for a measurement|not a measurement|claim rather than a reading/i.test(crtWhat(nom)),
       '"'+norm(crtWhat(nom)).slice(0,170)+'"');

    // THE NUMBER ITSELF HAS TO SURVIVE THE CROSSING. api/nav/soundings.py writes the
    // depth as `lower_bound_m` and api/nav/nominal.py writes it as `nominal_depth_m`;
    // whatever the client reads it under, a cell whose depth it cannot find is drawn
    // grey — the same grey for both layers, with the whole twelve-colour depth scale
    // silently switched off and nothing anywhere saying so.
    const sFeat = SURVEYED.features[0], nFeat = NOMINAL.features[0];
    const sDepth = (typeof _crtDepthOf==='function') ? _crtDepthOf(sFeat) : undefined;
    const nDepth = (typeof _crtDepthOf==='function') ? _crtDepthOf(nFeat) : undefined;
    ok('a surveyed cell as the api actually writes it is READ as a depth',
       sDepth===2.60,
       'lower_bound_m=2.60 came through as '+JSON.stringify(sDepth)+
       ' — null means the cell is drawn in the "no depth" grey, so every surveyed '
       +'cell on the map is the same colour whatever the sub measured');
    ok('a nominal cell as the api actually writes it is READ as a depth',
       nDepth===1.4,
       'nominal_depth_m=1.4 came through as '+JSON.stringify(nDepth)+
       ' — the nominal layer would be flat grey too, and the two layers then differ '
       +'only in texture');

    // AND IT HAS TO BE DRAWN THE SIZE IT CLAIMS TO BE. The same crossing, one field
    // along: the renderer sizes a cell from properties.cell_m and falls back to
    // properties.cell — which in this file is the cell INDEX, 0, 1, 2… A survey
    // binned in 10 m cells then draws cell 1 one metre wide and cell 0 five, so the
    // stretch the sub actually surveyed is painted smaller than it is, and unsounded
    // water beside it is left looking like water nobody happened to colour in.
    const ppm = (typeof _crtPpm==='function') ? _crtPpm(1) : 0;
    const rec = {rects:[], beginPath(){}, moveTo(){}, lineTo(){}, closePath(){},
                 rect:function(x,y,w,h){ this.rects.push(w); }};
    const drewCell = (typeof _crtCellPath==='function') &&
                     _crtCellPath(rec, SURVEYED.features[1], 1, ppm);
    const widthM = (rec.rects.length && ppm) ? rec.rects[0]/ppm : null;
    ok('a surveyed cell is drawn the length the survey says it covers',
       widthM!==null && Math.abs(widthM - SURVEYED.cell_m) < 0.5,
       'binned in '+SURVEYED.cell_m+' m cells; this one would be drawn '+
       (widthM===null ? '(not drawn at all — projection '+(TILES.last?'ready':'absent')+')'
                      : widthM.toFixed(2)+' m')+' across. properties.cell on this feature '
       +'is '+SURVEYED.features[1].properties.cell+', which is the cell INDEX and not a '
       +'length; the survey\'s own cell_m sits on the FeatureCollection, where the '
       +'per-feature reader never looks.');

    // THE KEY IS WHERE THE DIFFERENCE IS EXPLAINED, and it is drawn, so it is read
    // as pixels. On a canvas this suite creates: nothing cross-origin is ever put on
    // it, so it cannot be tainted and getImageData actually works.
    const cv=document.createElement('canvas'); cv.width=160; cv.height=48;
    const cx=cv.getContext('2d');
    cx.fillStyle='#0c0118'; cx.fillRect(0,0,160,48);
    let keyErr=null;
    try{ crtDrawDepthKey(cx, 6, 6, 44, 1); }catch(e){ keyErr=e&&e.message; }
    // Interiors only, clear of both outlines: the question is what the FILL says.
    const nomPatch = patch(cx, 12, 9, 32, 3);       // nominal swatch at (6,6,44,8)
    const surPatch = patch(cx, 12, 22, 32, 3);      // surveyed swatch at (6,19,44,8)
    ok('the depth key draws without throwing', keyErr===null, keyErr||'drew');
    ok('the two depth treatments are not the same picture',
       nomPatch.mean!==surPatch.mean || nomPatch.sd!==surPatch.sd,
       'nominal '+JSON.stringify(nomPatch)+'  surveyed '+JSON.stringify(surPatch));
    ok('...and the difference is a TEXTURE, not just a colour: nominal is hatched',
       nomPatch.sd > 8 && nomPatch.sd > surPatch.sd*3,
       'nominal spread '+nomPatch.sd+' vs surveyed '+surPatch.sd+
       ' — the hatch has silently tiled out to an empty fill twice already, and when '
       +'it does the published claim and the measured one become the same swatch');
    ok('...and surveyed reads as the more solid of the two, being the measured one',
       surPatch.mean > nomPatch.mean,
       'surveyed mean '+surPatch.mean+' vs nominal '+nomPatch.mean);

    // ================= 6. "I ASKED AND GOT NOTHING" IS NOT "I NEVER ASKED" ========
    /* Section 4 proved that a console which cannot reach the Pi reports CANNOT TELL
       rather than inventing absence, and that is still right. What it never asked is
       WHO IS ENTITLED TO BE LOUD ABOUT IT. There are two consoles behind that one
       status and they need different volumes:

         the Pi was answering and has stopped, or this area's chart index has
         succeeded before — something that was working is not working, and the map
         says so in the loudest words it has;

         a bench, a demo or a handheld that has never had chart data for this area
         and has no link to ask down — nothing has broken, nothing was ever expected,
         and the honest sentence is about the DOWNLOAD not having happened.

       The second used to fire the first's alarm on every cold start, which is how an
       operator learns to read HAZARD LAYERS · CANNOT TELL as "the console is booting".
       That is the reading that gets somebody hurt on the day it means what it says. */

    // ---- (a) an area that has never had chart data, with no Pi: QUIET ----
    INDEX_MODE='dead';
    const idxBefore = CALLS.index;
    await STORE.areaPut({name:BENCH, bbox:BBOX2, zmin:16, zmax:18, tiles:0, cached:0,
                         detail:'standard', savedAt:Date.now()+1000, mirrored:false});
    // Through the console's own boot path: clearing the active area is what makes
    // refreshBootstrap default-activate the newest save and hand it to crtSetArea.
    MAP.activeArea=null;
    await refreshBootstrap();
    const onBench = await waitFor(()=>CRT.area===BENCH && !CRT._busy && CALLS.index>idxBefore, 10000);
    await sleep(300);
    ok('an area with no chart data and no Pi to ask is active (the premise of the next three)',
       onBench && CRT.area===BENCH && CRT.indexOk===false,
       'CRT.area='+CRT.area+' indexOk='+CRT.indexOk+' index fetches '+idxBefore+' -> '+CALLS.index+
       ' — this area has never had a successful index on this console, and there is no link');
    const bBench = badgeLook();
    ok('a console that has never had chart data here is QUIET on the map',
       !isAlarm(bBench),
       badgeSay(bBench)+' — HAZARD LAYERS · CANNOT TELL is the loudest mark this map has, '+
       'and a console that has never been able to ask anybody anything has not earned it. '+
       'A quiet mark here is right and a loud one is not: the point is which of the two '+
       'true sentences is said, and how loudly');
    const t1bench = crtTierList(1);
    const quietSaid = t1bench.filter(e=>QUIET_RE.test(liveOf(row(e.id))) ||
                                        QUIET_RE.test(pillText(e.id)));
    ok('...and it says the one true thing it knows, quietly, in the panel: nothing downloaded yet',
       t1bench.length>0 && quietSaid.length===t1bench.length,
       t1bench.map(e=>e.id+': pill="'+pillText(e.id)+'" says "'+
                   liveOf(row(e.id)).slice(0,90)+'"').slice(0,3).join('  |  ')+
       ' — the truthful sentence is about the DOWNLOAD not having happened on this '+
       'handheld, which is a fact about this console; anything about the water is not');
    const overclaim = crtAll().filter(e=>['present','absent','empty'].indexOf(status(e.id))>=0);
    ok('...and the honesty rule is not what bought the quiet: nothing is present, absent or none-mapped',
       overclaim.length===0,
       overclaim.length ? ('claimed with no evidence: '+
                           overclaim.map(e=>e.id+'='+status(e.id)).join(', '))
                        : ('all '+crtAll().length+' layers still refuse to answer for a Pi '+
                           'that was never asked'));

    // ---- (b) a reachable Pi that HAS looked: ABSENT is still ABSENT, still loud ----
    INDEX_MODE='ok';
    await STORE.areaPut({name:AREA, bbox:BBOX, zmin:16, zmax:18, tiles:0, cached:0,
                         detail:'standard', savedAt:Date.now()+2000, mirrored:false});
    MAP.activeArea=null;
    await refreshBootstrap();
    const backHome = await waitFor(()=>CRT.area===AREA && !CRT._busy && CRT.indexOk===true, 10000);
    await sleep(250);
    ok('an area whose chart index HAS answered is active again (the premise of the loud case)',
       backHome && CRT.indexOk===true && status('locks')==='present',
       'CRT.area='+CRT.area+' indexOk='+CRT.indexOk+' locks='+status('locks'));
    const bAbsent = badgeLook();
    ok('a reachable Pi with a hazard file genuinely missing STILL reports ABSENT, and loudly',
       status(ABSENT_ID)==='absent' && /ABSENT/i.test(pillText(ABSENT_ID)) &&
       bAbsent.on && bAbsent.tier1 && bAbsent.shown && /ABSENT/i.test(bAbsent.text),
       ABSENT_ID+'='+status(ABSENT_ID)+' pill="'+pillText(ABSENT_ID)+'"  '+badgeSay(bAbsent)+
       ' — the quiet above is about WHICH true sentence is said, never about saying less');

    // ---- (c) an index that succeeded and then failed: LOUD ----
    INDEX_MODE='dead';
    await reingest('suite: the Pi that was answering has stopped');
    const bLost = badgeLook();
    ok('an index that HAS succeeded and then fails is LOUD — CANNOT TELL, on the map',
       bLost.on && bLost.tier1 && bLost.shown && /CANNOT TELL/i.test(bLost.text),
       badgeSay(bLost)+' — this is the console the alarm was written for: something that '+
       'was answering has stopped, and the map is no longer showing hazards it was showing '+
       'a minute ago');
    ok('...and its layers are left in the state the background retry re-asks for',
       crtTierList(1).every(e=>status(e.id)==='unavailable'),
       'tier-1 statuses: '+crtTierList(1).map(e=>e.id+'='+status(e.id)).join(', ')+
       ' — crt.js quietly re-asks every "unavailable" layer on a timer, and a state that '+
       'nothing ever retries is a map that stays blank after the tether is pushed home');
    // THE TWO MARKS SIDE BY SIDE. Both of them are the map admitting it is not showing
    // hazards, and they are two different facts with two different reactions — one is
    // "go and get the charts", the other is "the Pi that was serving them has stopped
    // mid-dive". Same rule as ABSENT versus NONE MAPPED in section 4: different words
    // AND different styling, or the difference does not survive a glance.
    ok('...and the loud mark and the quiet one are not the same mark',
       bLost.text!==bBench.text && bLost.cls!==bBench.cls && bLost.colour!==bBench.colour,
       'quiet:  '+badgeSay(bBench)+'\n            loud:   '+badgeSay(bLost));
    INDEX_MODE='ok';
    await reingest('suite: the Pi is back');

    // ================= 8. THE CELLS THIS SESSION SOUNDS FOR ITSELF =================
    /* Two defects in one block of drawing code, and neither is visible from the
       renderer's inputs — which is why everything below measures the picture.

       THE GEOMETRY. Each 3 m cell was an axis-aligned screen rect built from two
       DIAGONALLY OPPOSITE projected corners, so what was drawn was the extent of a
       diagonal and not the cell. Rotate the picture and that extent collapses toward
       zero at 45° and every 90° after — the sliver the operator saw when they turned.

       THE CLAIM. The same cells were binned off MAP.track with no check on whether
       the telemetry behind that track was real, so a SIMULATED dive painted
       measured-style survey cells over water nothing has ever been in. */
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(700);
    ok('the radar is collapsed, so the heading-up rotation these checks are about is live',
       !MAP.expanded && MAP.headingUp===true,
       'expanded='+MAP.expanded+' headingUp='+MAP.headingUp+' blind='+MAP.blind+
       ' — the expanded map is north-up, where a rotation defect cannot show at all');
    ok('the map has a projection to draw into (the premise of every measurement below)',
       typeof TILES!=='undefined' && !!TILES.last,
       'TILES.last='+((typeof TILES!=='undefined' && TILES.last) ? 'ready' : 'ABSENT — '+
       'lonLatToScreen returns null without it and nothing at all would be drawn'));

    // The nominal wash is silenced first: it is a hatched band down whole waterway
    // sections and would swamp every area figure below. What is left on the glass is
    // the SURVEYED treatment and nothing else.
    BODIES['depth_nominal'] = fc([]);
    await reingest('suite: nominal silenced, so only the measured treatment is on the glass');
    ok('the Pi holds a saved survey and no nominal wash (the premise)',
       status('depth_surveyed')==='present' && status('depth_nominal')==='empty' &&
       crtIsOn('depth_surveyed'),
       'surveyed='+status('depth_surveyed')+' nominal='+status('depth_nominal')+
       ' surveyed switched on='+crtIsOn('depth_surveyed'));
    ok('...and this sub has never been in the water on this console yet',
       !MAP.hasOrigin && crtLiveCells().length===0,
       'hasOrigin='+MAP.hasOrigin+' live cells='+crtLiveCells().length+
       ' track points='+MAP.track.length);
    const recSaved = drawDepth();
    ok('the Pi\'s own saved survey paints — and THIS is what measured looks like (the control)',
       !recSaved.err && recSaved.fills.length>0,
       recSaved.err ? ('crtDrawDepth threw: '+recSaved.err)
                    : (recSaved.fills.length+' cell(s) filled: '+treatSay(recSaved.fills[0])));
    const MEASURED = recSaved.fills.length ? treat(recSaved.fills[0]) : '(never established)';

    // With the Pi's saved survey emptied, ANY fill from here on is a cell this
    // session is claiming to have sounded itself. Nothing else can be in the frame.
    BODIES['depth_surveyed'] = fc([]);
    await reingest('suite: the Pi\'s saved survey emptied, so anything drawn now is THIS session');
    const recBlank = drawDepth();
    ok('with the Pi holding nothing and the sub not yet launched, nothing is painted at all',
       status('depth_surveyed')==='empty' && recBlank.fills.length===0,
       'surveyed='+status('depth_surveyed')+' fills='+recBlank.fills.length);

    // ---- A SIMULATED DIVE ----
    await setOrigin({lat:LATO, lon:LONO, accuracy:3, src:'crt-overlay suite'});
    await sleep(250);
    ok('the launch point is set, so the console will bin a track at all (the premise)',
       MAP.hasOrigin===true && !!MAP.origin,
       'hasOrigin='+MAP.hasOrigin+' origin='+(MAP.origin ?
         (MAP.origin.lat.toFixed(4)+','+MAP.origin.lon.toFixed(4)) : 'null'));
    ok('...and no vehicle has ever spoken to this console',
       !vehicleLinked() && !vehicleRecent() && state.mode==='sim',
       'vehicleLinked='+vehicleLinked()+' vehicleRecent='+vehicleRecent()+
       ' mode='+state.mode+' — everything the next check measures is modelled');
    // DRIVEN, not written in. Throttle and the ballast trigger go through the same
    // keyboard path an operator's thumbs use, and the model integrates the result:
    // this is a dive, not a track array assembled by a test.
    state.keys.clear(); state.keys.add('KeyW'); state.keys.add('KeyQ');
    await waitFor(()=>MAP.track.filter(p=>typeof p.depth==='number' && p.depth>0.05).length>4, 9000);
    state.keys.clear();
    await sleep(300);
    const wet = MAP.track.filter(p=>typeof p.depth==='number' && p.depth>0.05);
    const simBins = crtLiveCells().length;
    ok('the simulated dive really did go somewhere and get wet (the premise of the next check)',
       wet.length>4 && state.mode==='sim',
       wet.length+' of '+MAP.track.length+' track points below the surface, deepest '+
       MAP.track.reduce((m,p)=>Math.max(m, p.depth||0), 0).toFixed(2)+' m, binned into '+
       simBins+' cell(s), mode='+state.mode+
       ' — without this the next check would pass on an empty map');
    const recSim = drawDepth();
    const simMeasured = recSim.fills.filter(f=>treat(f)===MEASURED);
    ok('a SIMULATED dive paints NO cell in the measured treatment',
       simMeasured.length===0,
       recSim.fills.length+' fill(s) over '+simBins+' binned cell(s). '+
       (simMeasured.length
         ? (simMeasured.length+' of them are the surveyed treatment EXACTLY ('+
            treatSay(simMeasured[0])+', the same as the Pi\'s saved survey) — a modelled '+
            'dive painted as measurement, which is the class of lie this console refuses')
         : (recSim.fills.length
            ? ('all visibly different from measured: '+treatSay(recSim.fills[0])+
               ' — a visibly-simulated treatment, which is the other honest answer')
            : 'nothing drawn at all, which is the plainest honest answer')));

    // ---- A REAL DIVE, THROUGH THE CONSOLE'S OWN DOORS ----
    // Telemetry goes through handleMessage() and the position through the /ws/nav
    // handler connectNavWs() builds. setWsStatus('online') is re-asserted underneath
    // because the reconnect timer is meanwhile failing to reach a Pi that is not
    // there — and every "is this real?" test this console owns (vehicleLinked,
    // vehicleRecent, vehicleHasSensors, commandsBlocked, MAP.navReadsVehicle) has to
    // answer YES at once. The fix may reasonably ask any of them, and a suite that
    // satisfied only the one it guessed would fail an honest fix for the wrong reason.
    connectNavWs();
    const navRx = MAP.navWs && MAP.navWs.onmessage;
    ok('the map exposes the nav-frame handler this section drives',
       typeof navRx==='function', 'MAP.navWs.onmessage is '+(typeof navRx));
    const wsHold = setInterval(()=>setWsStatus('online'), 16);
    setWsStatus('online');
    startTel();
    const linked = await waitFor(()=>vehicleLinked() && state.mode==='real', 5000);
    let X=0;
    for(let i=0;i<10;i++){ X=i*1.6; navFrame(navRx, 0, X, 0, 2.2+i*0.08); await sleep(60); }
    await sleep(300);
    ok('a real vehicle is on the link, and its OWN frames are what moved the sub',
       linked && vehicleLinked() && vehicleRecent() && vehicleHasSensors() &&
       !commandsBlocked() && MAP.navReadsVehicle===true && MAP.navSimulated===false,
       'mode='+state.mode+' vehicleLinked='+vehicleLinked()+' vehicleRecent='+vehicleRecent()+
       ' hasSensors='+vehicleHasSensors()+' commandsBlocked='+commandsBlocked()+
       ' navReadsVehicle='+MAP.navReadsVehicle+' navSimulated='+MAP.navSimulated);
    const recReal = drawDepth();
    const realBins = crtLiveCells().length;
    // THE TRACK'S OWN CENSUS, because "0 cells" has two completely different causes —
    // no measured points reached the track at all, or they did and are not being
    // drawn — and a failure that cannot say which sends the next reader to the wrong
    // file. The provenance is stamped on each point by pushTrack.
    const census = MAP.track.reduce((a,p)=>{
      const k = (p.measured===true) ? 'measured' : (p.measured===false ? 'modelled' : 'unstamped');
      a[k]=(a[k]||0)+1; if(p.measured===true && p.depth>0.05) a.wetMeasured++; return a;
    }, {measured:0, modelled:0, unstamped:0, wetMeasured:0});
    ok('a REAL dive still paints this session\'s own cells',
       recReal.fills.length>0 && realBins>0,
       realBins+' cell(s) binned, '+recReal.fills.length+' filled'+
       (recReal.err ? (' — crtDrawDepth threw: '+recReal.err) : '')+
       '. track: '+MAP.track.length+' points ('+census.measured+' measured, '+
       census.modelled+' modelled, '+census.unstamped+' unstamped; '+census.wetMeasured+
       ' measured and below the surface). hasOrigin='+MAP.hasOrigin+
       ' — gating these cells on real telemetry must not amount to deleting the feature');
    ok('...and paints them in the SURVEYED treatment, because they ARE measurements',
       recReal.fills.length>0 && recReal.fills.every(f=>treat(f)===MEASURED),
       recReal.fills.length
         ? (treatSay(recReal.fills[0])+'   vs the Pi\'s saved survey: '+
            (recSaved.fills.length ? treatSay(recSaved.fills[0]) : '(never drawn)'))
         : 'nothing drawn');

    // ---- THE ROTATION SWEEP — the check that would have caught the shipped defect ----
    const HDGS=[0, 20, 45, 70, 90, 135, 200];
    const sweep=[];
    for(const h of HDGS){
      navFrame(navRx, h, X, 0, 2.9);
      const turned = await waitFor(()=>TILES.last &&
        Math.abs(TILES.last.rot + h*Math.PI/180) < 0.01, 2500);
      await sleep(90);
      const r = drawDepth();
      sweep.push({h:h, turned:turned, n:r.fills.length, m2:r.m2});
    }
    const swept = sweep.map(s=>s.h+'°'+(s.turned?'':'(NEVER TURNED)')+' '+
      (typeof s.m2==='number' ? s.m2.toFixed(1) : '?')+' m2/'+s.n+' fill').join('  |  ');
    const m2s = sweep.filter(s=>s.turned && typeof s.m2==='number' && isFinite(s.m2)).map(s=>s.m2);
    const lo = m2s.length ? Math.min.apply(null, m2s) : 0;
    const hi = m2s.length ? Math.max.apply(null, m2s) : 0;
    const mid = m2s.length ? m2s.slice().sort((a,b)=>a-b)[Math.floor(m2s.length/2)] : 0;
    ok('the map really did turn to every heading in the sweep (the premise)',
       m2s.length===HDGS.length, swept);
    ok('THE CELL AREA ON SCREEN DOES NOT CHANGE WITH THE MAP HEADING',
       m2s.length===HDGS.length && mid>0 && (hi-lo)/mid <= 0.10,
       swept+'   ->   spread '+(mid>0 ? (((hi-lo)/mid)*100).toFixed(1) : '?')+'% of the median '+
       (mid ? mid.toFixed(1) : '?')+' m2. A rect built from two DIAGONALLY OPPOSITE '+
       'projected corners has an extent that collapses toward zero at 45 degrees and '+
       'every 90 after — that is the rotating sliver, and it is measurable here and '+
       'nowhere in the renderer\'s inputs');
    const nCells = crtLiveCells().length;
    const want = 9 * nCells;                    // the 3 m bin crtLiveCells() uses
    ok('...and the area drawn is the 3 m bin the console says it is binning into',
       mid>0 && nCells>0 && Math.abs(mid-want)/want <= 0.2,
       'drew a median '+(mid ? mid.toFixed(1) : '?')+' m2 across '+nCells+' cell(s); '+
       nCells+' bins of 3 m x 3 m are '+want+' m2 — a shape that is stable and the '+
       'wrong size is still painting the wrong stretch of water as sounded');

    // ---- AND IT STILL SAYS WHAT THE NUMBER IS ----
    // These cells are the deepest the HULL got without grounding. That is a floor
    // under the water and not the depth of the bed, and it is the whole reason the
    // layer is allowed to be drawn solid at all.
    // Read AFTER a render. crtRenderRows() runs on ingest, on a toggle and when the
    // panel is opened — not every frame — so the row's live half is whatever the last
    // of those wrote, and the last one here was during the sim dive. Opening the panel
    // is the real control that refreshes it, and it is what an operator does before
    // reading a row anyway.
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(200);
    const surRow = row('depth_surveyed');
    const surTitle = norm((surRow && surRow.getAttribute('title'))||'');
    const surAria  = norm((surRow && surRow.getAttribute('aria-label'))||'');
    ok('the surveyed row still says these cells are a FLOOR UNDER the water, not the bed',
       /floor under/i.test(surTitle) && /not the depth of the bed/i.test(surTitle),
       '"'+surTitle.slice(0,220)+'"');
    ok('...and a screen reader is given the same sentence',
       surAria.length>60 && surAria===surTitle,
       'aria '+surAria.length+' chars, title '+surTitle.length+' chars, identical='+
       (surAria===surTitle));
    // AND THE LIVE HALF, which is the one this session's cells are described by. It is
    // the sentence that has to keep saying what the number IS: the deepest the HULL got
    // in that cell — a floor under the water there, and never the depth of the bed.
    const surLive = liveOf(surRow);
    ok('...and this session\'s own cells are still a HULL depth, counted apart, and not the bed',
       /THIS session/i.test(surLive) && /(hull depth|floor under)/i.test(surLive) &&
       /not the depth of the bed/i.test(surLive),
       '"'+surLive.slice(-280)+'"');
    const keyTap = tapCtx(220, 90);
    let keyErr2=null;
    try{ crtDrawDepthKey(keyTap.ctx, 6, 6, 44, 1); }catch(e){ keyErr2=e&&e.message; }
    ok('the drawn depth key still labels the measured treatment SURVEYED',
       !keyErr2 && keyTap.st.texts.indexOf('SURVEYED')>=0 && keyTap.st.texts.indexOf('NOMINAL')>=0,
       keyErr2 ? ('threw: '+keyErr2)
               : ('key labels drawn: '+(keyTap.st.texts.join(' / ')||'(none)')));

    stopTel();
    clearInterval(wsHold);
    setWsStatus('offline');
    state.keys.clear();

    // ================= 7. ?sim=1 — THE CONSOLE A STRANGER OPENS ==================
    /* The public demo is this same shipping page with one query parameter, and it is
       exactly the console the reported alarm came off: resolveHost() deliberately
       looks for no vehicle at all, so there is no Pi, there never was one, and an
       area remembered on the handheld is the whole of what it takes to make the
       index fetch fail and light the map's loudest mark.

       Loaded as a REAL PAGE IN AN IFRAME, because demo mode IS the URL: state.demo is
       decided once at boot from location.search and cannot be reached from a page
       already loaded without one. Its fetches are NOT stubbed — they go to the test
       server, which has no chart service, which is the truth of a bench console. The
       area left in IndexedDB is the bench one, which has never had chart data. */
    try{ await STORE.areaDelete(AREA); }catch(e){}
    const IFR = document.createElement('iframe');
    IFR.title = 'the ?sim=1 demo console, loaded as a real page so demo mode is real';
    IFR.style.cssText = 'position:fixed;left:-20000px;top:0;width:1024px;height:768px;'
                      + 'border:0;opacity:0;pointer-events:none';
    IFR.src = 'index.html?sim=1';
    document.body.appendChild(IFR);
    /* WHAT IS AND IS NOT REACHABLE FROM OUT HERE, and it caught this suite out first
       time round. `const CRT`, `const state` and `const MAP` are top-level `const` in
       classic scripts, so they live in that window's global LEXICAL scope and are NOT
       properties of its window object: `frame.contentWindow.CRT` is undefined on a
       console that is running perfectly. What IS on the window is every top-level
       FUNCTION declaration (crtAll, crtTierList, crtStateSentence…) and window.NEPTUNE,
       the console's own documented API object, which main.js publishes at the end of
       boot with state, MAP and CONFIG hanging off it. So NEPTUNE existing is also the
       cleanest possible "this console has finished booting" signal, and everything else
       below is read off the DOM — which is the operator's view of it anyway. */
    const demoUp = await waitFor(()=>{ try{ const w=IFR.contentWindow;
        return !!(w && w.NEPTUNE && w.NEPTUNE.state && typeof w.crtTierList==='function'); }
      catch(e){ return false; } }, 25000);
    const dw = demoUp ? IFR.contentWindow : null;
    const dPill = id=>dw ? norm((dw.document.getElementById('crt-state-'+id)||{}).textContent) : '';
    const dRows = dw ? dw.crtTierList(1) : [];
    // Settled = the boot fetch has resolved into a word. ASKING… is mid-flight and a
    // blank row has not been rendered yet; neither is an answer to assert against.
    if(dw) await waitFor(()=>dRows.length>0 && dRows.every(e=>{
      const p=dPill(e.id); return p && !/ASKING/i.test(p); }), 12000);
    await sleep(400);
    let frameWhy='';
    if(!dw){ try{ const w=IFR.contentWindow, d=w&&w.document;
      frameWhy = ' [frame: url='+((d&&d.location&&d.location.href)||'?')+
                 ' readyState='+((d&&d.readyState)||'?')+
                 ' scripts='+((d&&d.scripts&&d.scripts.length))+
                 ' NEPTUNE='+(typeof (w&&w.NEPTUNE))+
                 ' crtTierList='+(typeof (w&&w.crtTierList))+']';
    }catch(e){ frameWhy=' [frame unreachable: '+((e&&e.message)||e)+']'; } }
    ok('the demo console booted, and it really is the ?sim=1 page',
       !!dw && dw.NEPTUNE.state.demo===true && !dw.NEPTUNE.state.wsBase && !dw.NEPTUNE.state.host,
       dw ? ('demo='+dw.NEPTUNE.state.demo+' wsBase="'+dw.NEPTUNE.state.wsBase+
             '" host="'+dw.NEPTUNE.state.host+'" mode='+dw.NEPTUNE.state.mode)
          : ('the ?sim=1 page never finished booting in the frame — nothing below was '+
             'tested'+frameWhy));
    ok('...with an area remembered on the handheld, which is what used to raise the alarm',
       !!dw && dw.NEPTUNE.MAP.activeArea===BENCH && dRows.length>0 &&
       !/NO AREA/i.test(dPill(dRows[0].id)),
       dw ? ('activeArea='+dw.NEPTUNE.MAP.activeArea+', hazard rows read "'+
             dPill(dRows[0].id)+'" — an area IS active, which is the whole precondition '+
             'of the alarm that used to fire here') : 'NOT RUN');
    const bDemo = dw ? badgeLookIn(dw.document, dw)
                     : {missing:true, on:true, tier1:true, shown:true, cls:'(not run)',
                        display:'(not run)', colour:'(not run)', border:'(not run)',
                        h:0, w:0, text:'(not run)'};
    ok('?sim=1 demos the layers WITHOUT alarming on the map',
       !!dw && !isAlarm(bDemo),
       badgeSay(bDemo)+' — this is the page linked from the README, opened by somebody '+
       'who has never seen the console before, with no vehicle within a hundred miles of it');
    const dQuiet = dRows.filter(e=>{
      const r = dw.document.getElementById('crt-row-'+e.id);
      return QUIET_RE.test(norm((r && r.getAttribute('title'))||'')) || QUIET_RE.test(dPill(e.id));
    });
    ok('...and the panel still tells the stranger what it does and does not have',
       !!dw && dRows.length>0 && dQuiet.length===dRows.length,
       dw ? (dRows.map(e=>e.id+'="'+dPill(e.id)+'"').slice(0,4).join('  ')) : 'NOT RUN');
    const dLies = dw ? dw.crtAll().filter(e=>/\bABSENT\b/i.test(dPill(e.id))) : [];
    ok('...and nothing is reported ABSENT, which would be a claim about a Pi that was never asked',
       !!dw && dLies.length===0,
       dw ? (dLies.length ? ('claimed absent with no Pi to have looked: '+
                             dLies.map(e=>e.id).join(', '))
                          : ('all '+dw.crtAll().length+' layers read "'+dPill(dRows[0].id)+'"'))
          : 'NOT RUN');

    /* THE OPERATOR'S DECISION, ACROSS A REAL RELOAD. This is not the same check as
       section 2's crtLoadPrefs() call: that reloads the preferences into a console
       that is already running, and this is a WHOLE PAGE booting from nothing, off the
       same IndexedDB, running its own crtInit(). Everything ships on now, so the one
       thing that has to survive a launch is the operator having said no — a console
       that comes back with the layer they pruned switched on again has quietly made
       their decision for them, and it will do it every single launch. */
    let dPref = null;
    if(dw){
      await waitFor(()=>{ try{ return typeof dw.crtIsOn==='function'
                                   && dw.crtIsOn(T3)===false; }catch(e){ return false; } }, 8000);
      try{ dPref = {off:dw.crtIsOn(T3), locks:dw.crtIsOn('locks'),
                    others:dw.crtTierList(3).filter(e=>dw.crtIsOn(e.id)).length,
                    of:dw.crtTierList(3).length}; }catch(e){ dPref = {err:String(e)}; }
    }
    ok('a layer the operator switched off is STILL off on a console that has just booted',
       !!dPref && dPref.off===false,
       dPref ? ('after a real page load: crtIsOn("'+T3+'")='+dPref.off+
                ' while '+dPref.others+' of '+dPref.of+' other extras came back ON and '+
                'locks='+dPref.locks+' — their decision persists, the default does not '+
                'override it') : 'NOT RUN');
    ok('...and it is the ONLY one that came back off, so the default is still on for everything else',
       !!dPref && dPref.others === (dPref.of - 1) && dPref.locks===true,
       dPref ? (dPref.others+' of '+dPref.of+' extras on in the fresh console — one '+
                'pruned layer must not be read as permission to ship the rest hidden')
             : 'NOT RUN');
    if(IFR.parentNode) IFR.parentNode.removeChild(IFR);

    // ================= 9. THE NATIONAL STORE IS THE ORDINARY CASE ============
    /* EVERYTHING ABOVE THIS LINE IS THE PER-AREA CARD, which still exists and is
       still read — a console that downloaded one under the old scheme holds real data
       about real water. But it is no longer how the map is got. The whole Trust
       network is fetched once, nationally, on launch, and lives on the handheld; the
       area decides which stretch of imagery is cached and which dive journals the
       surveyed depth is built from, and NOTHING ELSE.

       So this section takes the areas away — every one of them — and asserts the map
       is still there. That is the reported defect stated as a check: a handheld
       holding the Trust's entire published network drew nothing at all until somebody
       tapped a launch point on it.

       THE THREE SENTENCES ABOUT A LAYER THAT IS NOT ON THE GLASS, in one place,
       because the whole value of this round is that the first of them stops being
       the everyday state:
         HERE / NONE MAPPED   the store answered and this is what it holds
         DOWNLOADING          the once-only launch fetch is running. Nothing is wrong
         NOT DOWNLOADED       nothing holds it and nothing is fetching it. LAST RESORT
         ABSENT               a store that looked and does not have this layer
       (a) and (c) and (d) are asserted here in the same page state, one after the
       other, because the risk in making NOT DOWNLOADED rare is that somebody buys the
       quiet by softening ABSENT, and that trade would be a worse defect than the one
       being fixed. */
    netBuild();
    NET_MODE = 'ok';
    try{ await STORE.areaDelete(AREA); }catch(e){}
    try{ await STORE.areaDelete(BENCH); }catch(e){}
    MAP.activeArea = null;
    if(typeof crtSetArea==='function') crtSetArea(null);
    await waitFor(()=>CRT.area===null && !CRT._busy, 10000);
    await reingest('suite: the national store, on a console with no area at all');
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(200);

    const netUrls = CALLS.urls.filter(u=>/\/api\/crt(\/|\?|$)/.test(u.split('?')[0]+'?'));
    ok('the national store answered a console with NO AREA (the premise of this section)',
       CRT.area===null && CRT.net && CRT.net.ok===true && CALLS.net>0,
       'CRT.area='+CRT.area+' CRT.net.ok='+(CRT.net&&CRT.net.ok)+' after '+CALLS.net+
       ' index request(s), most recently "'+(netUrls[netUrls.length-1]||'(none)')+'"');
    const areaInPath = netUrls.filter(u=>/\/areas\//.test(u));
    ok('...and it was addressed without an area in the path, which is the whole point of it',
       netUrls.length>0 && areaInPath.length===0,
       areaInPath.length ? ('area-scoped chart requests: '+areaInPath.slice(0,3).join('  '))
                         : (netUrls.length+' national request(s), e.g. "'+netUrls[0]+'"'));

    // T3 is excluded by name: the operator switched it off in section 2 and that
    // decision is the one thing allowed to keep a layer off this list.
    const netRows = crtAll().filter(e=>e.kind!=='depth' && e.id!==NET_MISSING && e.id!==T3);
    const HOLDS = ['present','empty','held'];
    const notDrawn = netRows.filter(e=>HOLDS.indexOf(status(e.id))<0);
    ok('with the network held, EVERY layer is loaded on a console with no area, no launch point and no Pi',
       netRows.length>0 && notDrawn.length===0,
       notDrawn.length ? ('not loaded: '+notDrawn.map(e=>e.id+'='+status(e.id)+' ("'+
                          pillText(e.id)+'")').slice(0,5).join(', '))
                       : (netRows.length+' layers loaded with CRT.area=null, e.g. '+
                          netRows.slice(0,3).map(e=>e.id+'="'+pillText(e.id)+'"').join('  ')));
    const netOff = netRows.filter(e=>!crtIsOn(e.id));
    ok('...and not one of them is switched off, so nothing is held and hidden',
       netOff.length===0,
       netOff.length ? ('held but not drawn: '+netOff.map(e=>e.id).join(', '))
                     : (netRows.length+' layers on ('+T3+' is excluded because the operator '+
                        'switched it off in section 2, and that is the only way off there is)'));
    const stillOff = crtIsOn(T3)===false && status(T3)==='off';
    ok('...including that the operator\'s own pruning survived the store coming up',
       stillOff, T3+': on='+crtIsOn(T3)+' status='+status(T3)+' pill="'+pillText(T3)+
       '" — a new source of data is not a reason to undo somebody\'s decision');

    /* NOT DOWNLOADED IS NOT THE STATE OF A CONSOLE THAT HAS THE DATA. This is the
       sentence a fresh console used to show as a matter of course, which is the wrong
       way round: it should be what you see when something has gone wrong. */
    const undownloaded = crtAll().filter(e=>e.kind!=='depth' &&
                                            ['not-downloaded','no-area'].indexOf(status(e.id))>=0);
    ok('NOT DOWNLOADED is not what a console that simply HAS the data says',
       undownloaded.length===0,
       undownloaded.length ? ('still reporting an absence over a store that answered: '+
                              undownloaded.map(e=>e.id+'='+status(e.id)).join(', '))
                           : ('0 of '+crtAll().length+' layers read NOT DOWNLOADED or NO AREA'));
    // ...AND THE HONESTY RULE IS NOT WHAT BOUGHT THE QUIET.
    ok('a layer the national store looked for and does not hold STILL says ABSENT',
       status(NET_MISSING)==='absent' && /ABSENT/i.test(pillText(NET_MISSING)) &&
       /NO DATA/i.test(liveOf(row(NET_MISSING))),
       NET_MISSING+': status='+status(NET_MISSING)+' pill="'+pillText(NET_MISSING)+
       '" says "'+liveOf(row(NET_MISSING)).slice(0,140)+'"');
    // The depth pair is per-area BY NATURE — one of the two is built from this area's
    // own dive journals — so with no area it has nothing to report, and it has to say
    // that rather than being quietly folded into the national good news.
    const depthRows = crtAll().filter(e=>e.kind==='depth');
    const depthSays = depthRows.filter(e=>/NO AREA/i.test(pillText(e.id)) &&
                                          /area/i.test(liveOf(row(e.id))));
    ok('...and the depth pair, which really does need an area, says so instead of being lumped in',
       depthRows.length>0 && depthSays.length===depthRows.length,
       depthRows.map(e=>e.id+'="'+pillText(e.id)+'": '+liveOf(row(e.id)).slice(0,80))
                .join('  |  '));

    // ---- (c) THE FIRST LAUNCH: DOWNLOADING, NOT NOT-DOWNLOADED ----
    /* A brand new handheld on its first launch holds nothing and nothing is wrong.
       Saying NOT DOWNLOADED over a download in flight teaches the operator to read
       the absence marks as noise, and the mark it teaches them to ignore is the one
       that means they are missing hazard data for the water they are in. */
    NET_MODE = 'nothing';
    DL = {state:'running', running:true, done:6, total:27, layer:'culverts-0',
          why:'the once-only national chart download is running'};
    await reingest('suite: a brand new handheld, mid launch-download');
    const dlRows = crtAll().filter(e=>e.kind!=='depth');
    const saidDl = dlRows.filter(e=>status(e.id)==='downloading' &&
                                    /DOWNLOADING/i.test(pillText(e.id)));
    const saidNd = dlRows.filter(e=>status(e.id)==='not-downloaded');
    const bDl = badgeLook();
    ok('while the once-only download is running the layers say DOWNLOADING, not NOT DOWNLOADED',
       saidDl.length===dlRows.length && saidNd.length===0,
       saidDl.length+' of '+dlRows.length+' say DOWNLOADING, '+saidNd.length+
       ' say NOT DOWNLOADED. locks reads "'+pillText('locks')+'" — "'+
       liveOf(row('locks')).slice(0,130)+'"');
    ok('...and a download in flight is never drawn as the map\'s loudest mark',
       !isAlarm(bDl),
       badgeSay(bDl)+' — this is the EXPECTED state of a new handheld exactly once, '+
       'and spending the hazard alarm on the expected state is how the hazard alarm '+
       'stops meaning anything');

    /* ---- (d) AND THE QUIET WAS NOT BOUGHT BY GOING QUIET ----
       The download stops answering and so does the store — on a console where BOTH
       have answered already, in this session. That is not "nothing has been
       downloaded yet": it is data that was on this handheld a minute ago and is not
       there now, which is the one thing the loudest mark on this map exists for. The
       contrast with (c) is the whole point — same empty store, same 404, and the
       console says two different things because two different things happened.

       The never-had-anything case is section 7's: a real ?sim=1 page, booted from
       nothing, which says NOT DOWNLOADED quietly and is asserted there. It cannot be
       reached from here, and a suite that faked it by clearing a variable would be
       testing its own reset rather than the console.

       The wait is crt.js's own two-second progress poll noticing the download has
       settled and re-reading the store, rather than this suite reaching in. */
    DL = null;
    await sleep(CRT_API.dlPollMs + 900);
    await waitFor(()=>!CRT._busy && !crtDownloading(), 8000);
    await reingest('suite: the store that was answering has stopped');
    const lost = crtAll().filter(e=>e.kind!=='depth' && status(e.id)==='unavailable');
    const bLost2 = badgeLook();
    ok('a store that HAS answered here and then holds nothing is LOUD — CANNOT TELL, not a shrug',
       lost.length>0 && /CANNOT TELL/i.test(pillText('locks')) && isAlarm(bLost2),
       lost.length+' of '+crtAll().length+' layers read "'+pillText('locks')+'"; '+
       badgeSay(bLost2)+' — making NOT DOWNLOADED the last resort must never turn a '+
       'real gap into a quiet one, and this console HAS been served this data');
    ok('...and nothing is reported ABSENT off the back of it, because nobody looked at any disk',
       crtAll().filter(e=>status(e.id)==='absent').length===0,
       'absent: '+(crtAll().filter(e=>status(e.id)==='absent').map(e=>e.id).join(', ')||'none')+
       ' — a store that could not be read has not told this console that anything is missing');

    // ---- (e) THE PICTURE, WITH EVERY LAYER ON IT ----
    /* THE RISK THIS ROUND CREATED, AND IT IS A REAL ONE. Drawing every layer at once
       can turn the map into soup, and an unreadable map is its own kind of dishonesty.
       The answer is DRAWING — order, weight, size — and never hiding, so the order is
       what gets checked: a weir must never end up underneath a planning buffer.

       Read off the paint stream in the order it was issued, and classified by the
       console's OWN palette rather than by colours typed into this file, so a restyle
       cannot turn this into a comparison of two literals nobody kept up to date. */
    NET_MODE = 'ok';
    await reingest('suite: the whole network on the glass at once');
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(500);
    const canon = (c)=>{ const g=document.createElement('canvas').getContext('2d');
                         g.fillStyle=c; return String(g.fillStyle); };
    const GROUP = {};
    GROUP[canon(CRT_C.hazard)]=1; GROUP[canon(CRT_C.hazardDim)]=1;
    GROUP[canon(CRT_C.ops)]=2;    GROUP[canon(CRT_C.opsDim)]=2;
    GROUP[canon(CRT_C.extra)]=3;
    const DT = tapCtx((MAP.canvas&&MAP.canvas.width)||800, (MAP.canvas&&MAP.canvas.height)||600);
    let drawErr=null;
    try{ crtDraw(DT.ctx, MAP.dpr||1); }catch(e){ drawErr=(e&&e.message)||String(e); }
    const ops = DT.st.ops;
    const at = {1:[], 2:[], 3:[]};
    ops.forEach((o,i)=>{ const g=GROUP[o.style]; if(g) at[g].push(i); });
    const span = g=>at[g].length ? (at[g][0]+'..'+at[g][at[g].length-1]) : 'none';
    const sawAll = at[1].length>0 && at[2].length>0 && at[3].length>0;
    ok('the map has a projection and every tier reached the glass (the premise of the order checks)',
       !drawErr && sawAll,
       drawErr ? ('crtDraw threw: '+drawErr)
               : ('projection='+((typeof TILES!=='undefined'&&TILES.last)?'ready':'ABSENT')+
                  ', '+ops.length+' paints: hazard ops at '+span(1)+', operations at '+
                  span(2)+', extras at '+span(3)));
    const lastOf = g=>at[g].length ? at[g][at[g].length-1] : -1;
    const firstOf = g=>at[g].length ? at[g][0] : Infinity;
    ok('HAZARDS ARE PAINTED LAST, so a mooring or a planning buffer can never sit on top of a weir',
       sawAll && lastOf(3) < firstOf(2) && lastOf(2) < firstOf(1),
       'extras '+span(3)+'  ->  operations '+span(2)+'  ->  hazards '+span(1)+
       ' out of '+ops.length+' paints. Every layer is drawn now, so the ORDER is the '+
       'whole of what keeps the map readable — and it is the safety order: the thing '+
       'that stops the sub is the thing that must still be visible');
    const hazFills  = ops.filter(o=>o.kind==='fill' && GROUP[o.style]===1);
    const extraFill = ops.filter(o=>o.kind==='fill' && GROUP[o.style]===3);
    ok('...and hazards are the loudest thing on it: their marks are FILLED, extras only outlined',
       hazFills.length>0 && extraFill.length===0,
       hazFills.length+' filled hazard marks, '+extraFill.length+' filled extras — '+
       'drawn most prominently is the other half of "nothing is hidden": everything '+
       'is on the map and the map still says what will stop the vehicle');
    /* AND THE BIG FILLS ARE UNDERNEATH. A planning buffer is an AREA — 1,296 polygons
       at 82 kB each nationally — and an area fill laid down after a weir's mark covers
       it completely whatever colour it is. This is measured off the geometry rather
       than off a layer name: anything filled that is several times the size of a
       hazard mark is an area fill, whoever drew it and whatever it is called. The
       count of them is reported either way, so a build that stopped filling areas
       altogether cannot leave this check quietly proving nothing. */
    const hazArea = hazFills.length ? Math.max.apply(null, hazFills.map(o=>o.area)) : 0;
    const bigAll  = ops.filter(o=>o.kind==='fill' && hazArea>0 && o.area > hazArea*4);
    const buried  = ops.filter((o,i)=>o.kind==='fill' && hazArea>0 && o.area > hazArea*4
                                      && i > firstOf(1));
    ok('...and every large AREA fill is laid down before them, so a weir is never buried under one',
       buried.length===0,
       buried.length ? (buried.length+' of '+bigAll.length+' area fill(s) painted AFTER the '+
                        'first hazard mark: '+buried.slice(0,3).map(o=>o.style+' '+
                        Math.round(o.area)+'px2').join(', '))
                     : (bigAll.length+' area fill(s) bigger than '+Math.round(hazArea*4)+
                        ' px2 on this frame, all of them before paint '+firstOf(1)+
                        ' (the largest hazard mark is '+Math.round(hazArea)+' px2)'));

    // ================= NOTHING ELSE BROKEN =================
    // Put the fake Pi back the way the rest of the file found it before the parting
    // photograph, so the picture run.py records is a healthy console rather than the
    // half-emptied one section 8 needed.
    BODIES['depth_nominal'] = NOMINAL;
    BODIES['depth_surveyed'] = SURVEYED;
    btn(T3).click();                       // give the operator their layer back
    await sleep(300);
    await reingest('suite: the fake Pi restored for the parting picture');
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(400);
    window.fetch = realFetch;
    try{ await STORE.areaDelete(AREA); }catch(e){}
    try{ await STORE.areaDelete(BENCH); }catch(e){}
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    const need=['crt-panel','crt-list','crt-absent','crt-credit','map-crt-toggle',
                'crt-refresh','crt-close','crt-panel-title']
               .concat(crtAll().map(e=>'crt-row-'+e.id));
    const missing=need.filter(id=>!$(id));
    ok('every element these checks read is still on the page', missing.length===0,
       missing.length ? ('MISSING: '+missing.join(', '))
                      : need.length+' elements found ('+crtAll().length+' layers)');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>{
    try{ window.fetch = realFetch; }catch(_){}
    fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)});
  });
})();

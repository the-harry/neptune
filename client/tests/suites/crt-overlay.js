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
   names and nobody noticing until the map is grey. */
(function(){
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

  const CALLS = {index:0, layer:{}, urls:[]};
  let INDEX_MODE = 'ok';          // 'ok' | 'dead' | 'noservice'

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

    const t2off = t2.filter(e=>!crtIsOn(e.id));
    ok('operations layers are ON by default', t2off.length===0,
       t2off.length ? ('off: '+t2off.map(e=>e.id).join(', ')) : t2.map(e=>e.id).join(', '));
    const t3on = t3.filter(e=>crtIsOn(e.id));
    ok('extras are OFF by default', t3on.length===0,
       t3on.length ? ('on: '+t3on.map(e=>e.id).join(', ')) : t3.length+' extras, all off');

    // OFF MUST NOT LOOK LIKE ABSENT. This is the same doctrine one step earlier:
    // the console has not asked, so it may not report anything about what is there.
    const t3sample = t3.find(e=>!e.adopted) || t3[0];
    ok('an extra that is off says NOT ASKED, never anything that reads as "nothing there"',
       status(t3sample.id)==='off' && /NOT ASKED/i.test(pillText(t3sample.id)),
       t3sample.id+': status='+status(t3sample.id)+' pill="'+pillText(t3sample.id)+'"');
    ok('...and it was genuinely not requested, rather than requested and hidden',
       !CALLS.layer[t3sample.id],
       t3sample.id+' was fetched '+(CALLS.layer[t3sample.id]||0)+' time(s)');

    // ================= 2. THE CHOICE IS REMEMBERED =================
    ok('the handheld has somewhere to remember a choice (the premise of the next three)',
       STORE.ready===true, 'STORE.ready='+STORE.ready+
       ' — without IndexedDB nothing below is testable and nothing is remembered');

    const T3='mileposts';
    const before = crtIsOn(T3);
    btn(T3).click();                            // the real control, the real listener
    await waitFor(()=>crtIsOn(T3)!==before, 3000);
    await sleep(400);                           // crtSavePrefs is fire-and-forget
    const saved = await STORE.get('crt.layers', null);
    ok('switching an extra on writes the choice to the handheld\'s own storage',
       crtIsOn(T3)===true && !!saved && saved[T3]===true,
       'crtIsOn="'+crtIsOn(T3)+'" stored='+JSON.stringify(saved));
    ok('...and switching it on is what makes the console finally ask for it',
       (CALLS.layer[T3]||0) > 0 && status(T3)==='present',
       T3+' fetched '+(CALLS.layer[T3]||0)+' time(s), status='+status(T3)+
       ' pill="'+pillText(T3)+'"');

    // Read it back through the console's own loader, which is what a fresh boot does.
    CRT.prefs = null;
    await crtLoadPrefs();
    ok('...and the console reads it back rather than starting from the default again',
       crtIsOn(T3)===true,
       'after crtLoadPrefs(): crtIsOn("'+T3+'")='+crtIsOn(T3)+' prefs='+JSON.stringify(CRT.prefs));

    await reingest('suite: re-ingest with a layer switched on');
    ok('re-asking the Pi does not quietly undo the operator\'s choice',
       crtIsOn(T3)===true && status(T3)==='present',
       'crtIsOn="'+crtIsOn(T3)+'" status='+status(T3)+
       ' — a refresh that resets the panel is a refresh nobody can trust');

    btn(T3).click();
    await waitFor(()=>!crtIsOn(T3), 3000);
    await sleep(400);
    const saved2 = await STORE.get('crt.layers', null);
    ok('switching it back off is remembered too, rather than only the "on" being saved',
       crtIsOn(T3)===false && !!saved2 && saved2[T3]===false,
       'crtIsOn='+crtIsOn(T3)+' stored='+JSON.stringify(saved2));

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
    const ringed = hazardRows.filter(e=>e.standoffM);
    const noRing = ringed.filter(e=>!/standoff this console draws/i.test((row(e.id)||{}).title||''));
    ok('...and that the keep-away ring is ours, not a surveyed danger area',
       noRing.length===0,
       noRing.length ? ('silent about the ring: '+noRing.map(e=>e.id).join(', '))
                     : ringed.length+' ringed layers say whose ring it is');

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

    // ================= NOTHING ELSE BROKEN =================
    window.fetch = realFetch;
    try{ await STORE.areaDelete(AREA); }catch(e){}
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

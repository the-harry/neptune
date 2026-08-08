/* @url ?sim=1
   WHAT THIS GUARDS — THE MAP IS REAL WHEN THE SUB IS NOT, AND SAYS SO BOTH WAYS.

   Reported off a real console: "on the dashboard I still get no chart data downloaded",
   with the map blank on a handheld that was perfectly capable of holding it. The panel
   was not lying — there was genuinely nothing to draw — and that is what made it
   useless. Three facts stood behind it: `?sim=1` resolves no host at all (core.js
   resolveHost returns immediately, "pure sim"), the launcher serves the client's files
   and starts no backend, and every piece of map data — the area list, the chart index,
   the layers, the depth pair — is an API endpoint. No backend, no map.

   THE DISTINCTION THIS FILE EXISTS TO GUARD, because it is subtle and one line of code
   loses it:

     THE VEHICLE AND THE MAP ARE DIFFERENT BACKENDS. Sim mode means THE VEHICLE is
     simulated — there is no sub, the physics is a model, and every reading off it is
     flagged, which is a rule this project enforces everywhere. It does NOT mean the MAP
     is fake. Satellite imagery, the Trust's hazard layers, the canal centreline and the
     offline area are real data about real water, downloaded from real services, and they
     are exactly as true with no sub attached as with one.

   So there are two ways to fail this suite and they are opposite.

     PRETENDING THERE IS A SUB. The lazy way to give the map a backend is to point the
     console's VEHICLE host at the local API — which answers /api/healthz and reports
     `hardware: "mock"` quite happily. Do that and the robot goes green, the Pi probe
     starts succeeding, CANNOT TELL becomes reachable again, and a console with no sub
     within a hundred miles looks connected to one. Section 3 asserts the whole of the
     vehicle's honesty AFTER the map has come alive, in the same page state, because
     that is the only place the two can be seen not to have leaked into each other.

     PRETENDING THE MAP IS FAKE. The mirror image: marking downloaded chart data as
     simulated because the console it is drawn on is. That would be as wrong as marking
     the simulator real, and it would teach an operator to discount the one thing on the
     screen that is measured fact about the water. Section 4 asserts real data arrives
     unlabelled — while section 3 asserts that the sub's OWN sentence, the one that says
     a simulated dive measures nothing and paints no survey cell, is still there.

   AND THE HONEST STATES SURVIVE ALL OF IT (section 5). A backend that answers and has
   no file still says ABSENT. A layer it holds with nothing inside this area still says
   NONE MAPPED. An area this handheld has never downloaded still says NOT DOWNLOADED,
   quietly, and never lights the map's loudest mark. A map that gained a backend and
   lost the difference between "nothing here" and "no data here" would be a worse map
   than the blank one this round started from.

   DRIVEN THROUGH THE SHIPPING PAGE'S OWN INGEST, in the style of crt-overlay.js and
   sensor-loss.js. The area is registered with STORE.areaPut exactly as SAVE OFFLINE
   registers one; refreshBootstrap() notices it and hands it to crtSetArea(); crtLoadAll()
   asks the index and then each layer; bootConsider() is the real decision path behind
   the download panel. Nothing here writes CRT.state or BOOTFETCH.jobs, because a suite
   that did would be testing the renderer and saying nothing about the wiring — and the
   wiring is where this defect lived.

   WHAT THE fetch STUB IS, AND WHAT IT IS NOT. window.fetch is stubbed for map-data paths
   only, and it stands in for THE HANDHELD'S OWN MAP SERVICE — the local API the launcher
   brings up beside the client. It is not a pretend Pi and nothing in this suite connects
   a vehicle. The test harness serves client/ as static files and runs no Python, so the
   service is not there to be asked; SERVING=false is that machine, byte for byte, and
   SERVING=true is the same console once the service answers. Both are asserted, and each
   check's detail says which one it ran under. The bodies are shaped like what
   api/nav/service.py's /api/areas/{name}/crt actually emits — a flat `layers` array
   carrying the absent rows as well as the present ones, plus its own `depth` block —
   because a property name the server writes and the client does not read is exactly the
   kind of break a hand-rolled shape hides.

   ONE LIMIT, STATED PLAINLY. The checks that read the panel's WORDS quote sentences off
   the build that had the defect. Re-wording the same claim would slip past them; that is
   why they sit beside behavioural checks — what the download panel actually does with a
   launch point, what the layer rows actually report from an answer — which do not care
   how anything is phrased. */
(function(){
  // run.py injects this suite into every /index.html it serves. Nothing here opens an
  // iframe, but the guard costs nothing and a second copy posting its own results over
  // the top frame's is a report that describes whichever finished first.
  if(window.top !== window) return;
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote whole sentences off the page and the page is full of em dashes and
  // ellipses. run.py prints to a Windows console whose codepage cannot encode them and
  // dies mid-report, taking every result after it: a report that cannot be printed is a
  // report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E -ÿ–—‘’“”•…]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const norm=s=>String(s||'').replace(/[ \s]+/g,' ').trim();
  const txt=id=>norm((($(id))||{}).textContent);
  async function waitFor(pred, ms){
    const t0=Date.now();
    while(Date.now()-t0 < ms){ try{ if(pred()) return true; }catch(e){} await sleep(50); }
    try{ return !!pred(); }catch(e){ return false; }
  }

  /* ---- the two areas ------------------------------------------------------
     One this handheld HOLDS, and one it has never downloaded anything for. The second
     is not a decoration: NOT DOWNLOADED has to stay reachable and stay quiet after the
     map gains a backend, and the only way to show that is to have an area the backend
     answers about with nothing. */
  const AREA  = 'local-map-cut';
  const BENCH = 'never-downloaded-cut';
  const LON=-1.9055, LAT=52.4805;                       // Gas Street basin, near enough
  const BBOX  = [LON-0.010, LAT-0.008, LON+0.010, LAT+0.008];
  const BBOX2 = [-2.0200, 52.5500, -1.9900, 52.5700];

  /* Which layer plays which honest state. Tier 1 is deliberately left whole: the
     hazard alarm is the loudest mark on this map and section 5 needs to show tier-2
     absences being reported WITHOUT it lighting. */
  const GONE_FROM_INDEX = 'moorings';   // the service answered and says it has no such file
  const GONE_FROM_DISK  = 'bridges';    // the index lists it; the file itself 404s
  const NONE_HERE       = 'slipways';   // the file is there and nothing of that kind is inside the box

  const CRT_ATTRIB = 'Canal & River Trust, Open Government Licence v3.0';
  const fc=(feats)=>({type:'FeatureCollection', attribution:CRT_ATTRIB, features:feats});
  const pt=(i)=>({type:'Feature', properties:{OBJECTID:i, name:'feature '+i},
                  geometry:{type:'Point', coordinates:[LON+i*0.0006, LAT+i*0.0002]}});
  const body=(n)=>fc(Array.from({length:n}, (_,i)=>pt(i+1)));

  // The layer bodies this handheld's own card holds. `null` means the file is not on
  // the card at all, which is a 404 and not an empty file — two different claims.
  const BODIES = {};
  const LAYER_N = {locks:3, weirs:2, sluices:1, culverts:2, tunnel_portals:1, tunnels:1,
                   outfalls:2, access_points:4, wharves:1, winding_holes:2, safety_gates:1,
                   stop_plank_grooves:1, feeders:1};
  Object.keys(LAYER_N).forEach(k=>{ BODIES[k]=body(LAYER_N[k]); });
  BODIES[NONE_HERE]       = fc([]);     // present, empty — NONE MAPPED
  BODIES[GONE_FROM_DISK]  = null;       // listed by the index, missing on disk — ABSENT
  // The published depth guidance is REAL DATA about real water: an authority's design
  // figure for the cut. It is not a sounding and it is not simulated, and section 4
  // checks it is labelled as neither.
  const NOMINAL = {type:'FeatureCollection', attribution:CRT_ATTRIB, nominal:true, measured:false,
    features:[{type:'Feature',
      properties:{depth_m:1.2, nominal:true, measured:false, source:'published waterway class'},
      geometry:{type:'LineString', coordinates:[[LON-0.004,LAT],[LON+0.004,LAT+0.001]]}}]};

  function indexBody(){
    const rows = [];
    Object.keys(BODIES).forEach(key=>{
      if(key===GONE_FROM_INDEX) return;
      rows.push({layer:key, title:key, status:'present', present:true,
                 count:(BODIES[key] && BODIES[key].features) ? BODIES[key].features.length : 0,
                 url:'/api/areas/'+AREA+'/crt/'+key});
    });
    // The absent row travels IN the same array, as api/nav/service.py sends it: the
    // index answering is what earns the console the right to say ABSENT, so it has to
    // say what is missing and not only what is there.
    rows.push({layer:GONE_FROM_INDEX, title:GONE_FROM_INDEX, status:'skipped', present:false,
               count:null, url:'/api/areas/'+AREA+'/crt/'+GONE_FROM_INDEX,
               why:'the fetch skipped this layer: the service timed out part-way through paging'});
    return {area:AREA, status:'present', attribution:CRT_ATTRIB, layers:rows,
            depth:{
              nominal:{status:'present', present:true, nominal:true, measured:false, is_survey:false,
                       count:1, sections:1, url:'/api/areas/'+AREA+'/depth/nominal',
                       title:'published waterway class'},
              surveyed:{status:'absent', present:false, url:'/api/areas/'+AREA+'/depth/surveyed',
                        why:'no dive on this card has recorded a sounding yet',
                        means:'nothing here has been measured by a vehicle'},
            }};
  }
  // A real centreline for the cut. Real data about real water, and it arrives over the
  // same map-data path everything else does.
  const CENTRELINE = {type:'FeatureCollection', features:[{type:'Feature', properties:{},
    geometry:{type:'LineString', coordinates:[[LON-0.008,LAT-0.001],[LON,LAT],[LON+0.008,LAT+0.002]]}}]};

  /* ---- THE NATIONAL STORE ---------------------------------------------------
     The whole Canal & River Trust network, fetched once on launch and held on this
     handheld, addressed at paths with NO AREA IN THEM. That is the thing this
     section of the suite exists to prove: the map is not an accessory to a launch
     point, and a console with no area, no origin and no vehicle still has one.

     Wire names are the real shape — api/nav/crt.py names a file after the ArcGIS
     service plus its layer number (`locks-0`) and never after a row in the
     console's table — so crtBind() is exercised rather than side-stepped. */
  const wireOf = id=>String(id).replace(/_/g,'-')+'-0';
  const NET_MISSING = 'water_points';      // the Trust publishes no such service
  const NET_BODIES = {}, NET_COUNT = {};
  function netBuild(){
    crtAll().forEach(e=>{
      if(e.kind==='depth' || e.id===NET_MISSING) return;   // depth is per-area BY NATURE
      const w = wireOf(e.id);
      NET_BODIES[w] = body(2);
      NET_COUNT[w] = 2;
    });
  }
  function netIndexBody(){
    const rows = Object.keys(NET_BODIES).map(w=>({layer:w, title:w, status:'present',
      present:true, count:NET_COUNT[w], url:'/api/crt/'+w}));
    rows.push({layer:wireOf(NET_MISSING), title:wireOf(NET_MISSING), status:'absent',
               present:false, count:null,
               why:'the Canal & River Trust publishes no service of this kind, so the '
                 + 'national fetch had nothing to ask for'});
    return {scope:'national', status:'present', attribution:CRT_ATTRIB,
            total:rows.length, layers:rows};
  }

  const json=(o,s)=>new Response(JSON.stringify(o), {status:s||200,
                                                     headers:{'Content-Type':'application/json'}});

  /* THE HANDHELD'S OWN MAP SERVICE, as this suite plays it. SERVING=false is the test
     harness exactly as it is — a static file server with no map service beside it — and
     every request falls through to it, so those checks are run against the real 404s a
     console with no backend gets. */
  let SERVING = false;
  // The national store, separately: a handheld can perfectly well hold the whole
  // network and no card for this cut, or the other way round, and the two switches
  // have to be independent or the suite cannot tell which one the console read.
  let NET_SERVING = false;
  const realFetch = window.fetch.bind(window);
  const CALLS = {index:0, layer:{}, depth:{}, centreline:0, urls:[], net:0, netLayer:{}};
  function stub(url, opts){
    const u = String((url && url.url) || url || '');
    const path = u.split('?')[0];
    /* THE NATIONAL STORE FIRST, matched on a path with NO AREA IN IT. If this console
       ever goes back to addressing the Trust's layers through an area, these requests
       stop arriving and the last section fails rather than passing on a fallback. */
    if(/\/api\/crt(\/|$)/.test(path)){
      CALLS.urls.push(u);
      const sub = decodeURIComponent((path.split('/api/crt')[1] || '').replace(/^\//, ''));
      if(!NET_SERVING) return realFetch(url, opts);   // no store on this handheld yet
      if(sub === 'fetch')
        return Promise.resolve(json({detail:'no national download has been started'}, 404));
      if(!sub){ CALLS.net++; return Promise.resolve(json(netIndexBody())); }
      CALLS.netLayer[sub] = (CALLS.netLayer[sub]||0) + 1;
      const b = NET_BODIES[sub];
      if(b===undefined || b===null)
        return Promise.resolve(json({detail:'the national store does not hold that layer'}, 404));
      return Promise.resolve(json(b));
    }
    const m = u.match(/\/api\/areas\/([^/?#]+)\/(crt|depth|centreline)(?:\/([^/?#]+))?/);
    if(!m) return realFetch(url, opts);
    CALLS.urls.push(u);
    const area = decodeURIComponent(m[1]), kind = m[2];
    const sub  = m[3] ? decodeURIComponent(m[3]) : null;
    if(kind==='crt' && !sub) CALLS.index++;
    if(kind==='crt' && sub)  CALLS.layer[sub] = (CALLS.layer[sub]||0)+1;
    if(kind==='depth')       CALLS.depth[sub||'?'] = (CALLS.depth[sub||'?']||0)+1;
    if(kind==='centreline')  CALLS.centreline++;
    if(!SERVING) return realFetch(url, opts);          // no service on this handheld yet
    // The area this console has never downloaded anything for. The service is running
    // and answers about it; it simply holds no card. NOT a fault, and not absence.
    if(area!==AREA) return Promise.resolve(json({detail:'no card for this area'}, 404));
    if(kind==='centreline') return Promise.resolve(json(CENTRELINE));
    if(kind==='depth'){
      if(sub==='nominal')  return Promise.resolve(json(NOMINAL));
      return Promise.resolve(json({status:'absent', present:false,
                                   why:'no dive on this card has recorded a sounding yet'}, 200));
    }
    if(!sub) return Promise.resolve(json(indexBody()));
    const b = BODIES[sub];
    if(b===undefined) return Promise.resolve(json({detail:'no such layer'}, 404));
    if(b===null)      return Promise.resolve(json({detail:'not on this card'}, 404));
    return Promise.resolve(json(b));
  }

  /* ---- reading the panel, the operator's way ------------------------------ */
  const pillText = id=>norm(((($('crt-state-'+id))||{}).textContent));
  const status   = id=>((CRT.state[id]||{}).status||'(none)');
  const rowEl    = id=>$('crt-row-'+id);
  // core.js liveTitle appends the live sentence to the written one; the whole title is
  // what an operator reads, so the whole title is what is scanned.
  const rowTitle = id=>norm(((rowEl(id))||{}).title);
  function badgeLook(){
    const el=$('crt-absent');
    if(!el) return {missing:true, on:false, tier1:false, text:'(element missing)'};
    const cs=getComputedStyle(el), r=el.getBoundingClientRect();
    return {missing:false, on:el.classList.contains('on'), tier1:el.classList.contains('tier1'),
            display:cs.display, h:Math.round(r.height), text:norm(el.textContent),
            cls:el.className};
  }
  const alarmed = b=>!b.missing && b.tier1 && b.on && b.display!=='none' && b.h>0;
  const badgeSay = b=>'badge class="'+(b.cls||'')+'" text="'+(b.text||'')+'" display='+
                      (b.display||'?')+' height='+(b.h||0)+'px';

  /* EVERY SENTENCE THIS CONSOLE IS SAYING ABOUT ITS MAP DATA, in one bag, with each
     piece labelled by where it came from — so a failure names the element the operator
     would have been reading. */
  function mapProse(){
    const out=[];
    const add=(where, s)=>{ s=norm(s); if(s) out.push({where, s}); };
    if(typeof crtAll==='function') crtAll().forEach(e=>{
      add('layer row '+e.id, rowTitle(e.id));
      add('layer pill '+e.id, pillText(e.id));
    });
    add('crt-fetch-why', ($('crt-fetch-why')||{}).title || txt('crt-fetch-why'));
    add('crt-fetch-state', txt('crt-fetch-state'));
    if(typeof BOOTFETCH!=='undefined') BOOTFETCH.order.forEach(id=>{
      add('download row '+id, (($('crt-fetch-row-'+id))||{}).title);
      add('download pill '+id, txt('crt-fetch-pill-'+id));
    });
    add('crt-absent badge', (($('crt-absent'))||{}).title);
    add('crt-fetch-badge', (($('crt-fetch-badge'))||{}).title);
    return out;
  }
  const hits=(list, res)=>{
    const found=[];
    list.forEach(({where,s})=>res.forEach(({re,what})=>{ if(re.test(s)) found.push(what+' — on '+where); }));
    return found;
  };

  /* THE CLAIMS THAT MUST NOT SURVIVE. Every one is quoted from the console that
     produced the report, and every one says the same thing: this handheld cannot have
     map data because it has no vehicle. That is the conflation. A fixed console may
     still say it holds nothing — it may well hold nothing — but not FOR THIS REASON. */
  const NO_SOURCE_CLAIMS = [
    {re:/nothing on this link to download it from/i,
     what:'"nothing on this link to download it from" (map data attributed to the tether)'},
    {re:/looks for no vehicle at all, so there is nothing/i,
     what:'"the demo looks for no vehicle at all, so there is nothing..." (no sub, therefore no map)'},
    {re:/nothing on this page can load them/i,
     what:'"nothing on this page can load them" (the handheld disowning its own map data)'},
    {re:/no Pi to ask/i,
     what:'"REFRESH has no Pi to ask" (refresh pointed at a vehicle rather than at the map service)'},
    {re:/same URL without \?sim=1/i,
     what:'"open the same URL without ?sim=1" (a remedy that trades the simulator for a map)'},
  ];
  const SIM_AS_REASON = [
    {re:/running the simulator, so there is no launch point/i,
     what:'"this console is running the simulator, so there is no launch point to download for"'},
    {re:/simulator[^.]{0,80}nothing is fetched/i,
     what:'"...the simulator... nothing is fetched" (a simulated vehicle given as the reason)'},
    {re:/cannot be fetched or checked until it is on the tether/i,
     what:'"the chart layers cannot be fetched or checked until it is on the tether"'},
  ];
  /* Words that mark a READING as produced by the console's own model. On a chart layer
     they would be a lie about the Trust's survey; on the sub's own soundings they are
     compulsory, which is why depth_surveyed is scanned separately (section 3). */
  const SIM_WORDS = /\bsimulat|\bsimulator\b|\bmock\b|\bfake\b|\bsynthetic\b|\bpretend|\bnot real\b|\bmade[- ]up\b/i;

  async function run(){
    await sleep(2600);

    // ============ 0. THE CONSOLE UNDER TEST IS THE ONE FROM THE REPORT ============
    ok('this is the ?sim=1 console — the page the defect was reported from',
       state.demo===true && !state.host && !state.wsBase,
       'demo='+state.demo+' host="'+state.host+'" wsBase="'+state.wsBase+'" mode='+state.mode);
    ok('the map module and the chart panel are on it',
       typeof CRT==='object' && typeof crtLoadAll==='function' && !!$('crt-list')
       && typeof BOOTFETCH!=='undefined',
       'CRT='+(typeof CRT)+' crtLoadAll='+(typeof crtLoadAll)+' #crt-list='+(!!$('crt-list'))
       +' BOOTFETCH='+(typeof BOOTFETCH));

    // Headless has no camera, so BLIND NAV engages on its own about a second after
    // boot and collapses the radar the panel lives beside.
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    state.keys.clear();                            // a held key collapses the map again
    if(!MAP.expanded && typeof expandMap==='function') expandMap();
    await sleep(400);
    window.fetch = stub;                           // recording; SERVING is still false

    // ============ 1. THE MAP DATA SURFACE, ON A HANDHELD WITH NO VEHICLE ============
    /* An area and a launch point, put there the way the console really gets them:
       STORE.areaPut is what SAVE OFFLINE and the auto-download both write, and the
       origin goes through IndexedDB because refreshBootstrap re-reads it every 5 s and
       an origin set in memory alone is silently reverted mid-test. */
    const tiles = (typeof countTilesBBox==='function') ? countTilesBBox(BBOX, 16, 18) : 0;
    await STORE.areaPut({name:AREA, bbox:BBOX, zmin:16, zmax:18, tiles:tiles, cached:tiles,
                         detail:'standard', savedAt:Date.now(), mirrored:false, state:'present'});
    const O = {lat:LAT, lon:LON, src:'suite: a launch point on this handheld'};
    await STORE.set('origin', O);
    MAP.origin = O; MAP.hasOrigin = true;
    await refreshBootstrap();
    const areaUp = await waitFor(()=>CRT.area===AREA && !CRT._busy, 12000);
    await sleep(200);

    // 1a. AREAS. Client-owned by architectural rule (map.js refreshBootstrap: "areas +
    // origin come from local storage and work with the Pi off"), and the operator's own
    // list is the place that has to prove it, not the store behind it.
    const listed = await STORE.areas();
    if(typeof openAreaManager==='function') openAreaManager();
    await sleep(300);
    const aRows = [...document.querySelectorAll('#a-list .a-row')];
    const aText = norm(($('a-list')||{}).textContent);
    ok('areas can be listed on this handheld with no vehicle of any kind',
       areaUp && listed.some(a=>a.name===AREA) && aRows.length>0 && aText.indexOf(AREA)>=0,
       'STORE holds ['+listed.map(a=>a.name).join(', ')+']; the AREAS list drew '+aRows.length+
       ' row(s): "'+aText.slice(0,110)+'"');
    const am=$('area-modal'); if(am) am.classList.remove('show');
    ok('...and one of them is ACTIVE, so there is something to draw and ask about',
       MAP.activeArea===AREA && CRT.area===AREA && MAP.hasArea===true,
       'MAP.activeArea='+MAP.activeArea+' CRT.area='+CRT.area+' hasArea='+MAP.hasArea+
       ' — nothing in this suite set CRT.area; refreshBootstrap did');

    /* 1b. IS THERE ANYWHERE TO ASK AT ALL? The precondition for everything below, and
       worth stating as its own check because the answer is what the round turned on: a
       console served over http was served BY A SERVER, so it has somewhere to address a
       map request even with no sub in the world. Read off whatever the console
       publishes about its own map backend, with the chart module's view of the same
       question printed beside it — those two are written by different hands and a
       console where they disagree is a console that has a backend and does not know it. */
    const backend = (()=>{
      const s = (typeof state!=='undefined' && state) ? state : {};
      const has = (typeof hasDataBackend==='function') ? hasDataBackend()
                : (typeof s.dataFrom==='string') ? (s.dataFrom!=='none')
                : (location.protocol!=='file:');
      const p = (n,f)=>{ try{ return (typeof f==='function') ? f() : '(not published)'; }
                         catch(e){ return '(threw: '+((e&&e.message)||e)+')'; } };
      return {has,
              from:(s.dataFrom!==undefined)?s.dataFrom:'(not published)',
              base:(s.dataBase!==undefined)?JSON.stringify(s.dataBase):'(not published)',
              why:s.dataWhy||'',
              crtBase:  JSON.stringify(p('mapDataBase', typeof mapDataBase!=='undefined' && mapDataBase)),
              crtLocal: p('mapDataLocal', typeof mapDataLocal!=='undefined' && mapDataLocal),
              crtName:  p('mapDataName', typeof mapDataName!=='undefined' && mapDataName)};
    })();
    ok('this console has somewhere to get map data, and it is not a vehicle',
       backend.has === true && !state.wsBase,
       'map backend: from='+backend.from+' base='+backend.base+
       (backend.why?(' why="'+backend.why+'"'):'')+
       '   |   the chart module reads base='+backend.crtBase+' local='+backend.crtLocal+
       ' and calls the holder "'+backend.crtName+'"   |   vehicle: host="'+state.host+
       '" wsBase="'+state.wsBase+'"');

    // 1c. THE ASK ITSELF. With no vehicle configured the console still has to address
    // its map data somewhere, and the URL it builds is recorded as evidence.
    const idxCalls = CALLS.index;
    await crtLoadAll('suite: the console asks for its chart data with no vehicle');
    await sleep(200);
    const idxUrl = CALLS.urls.filter(u=>/\/crt$/.test(u)).slice(-1)[0] || '(none)';
    ok('chart layers are ASKED FOR even though no vehicle is configured',
       CALLS.index > idxCalls,
       CALLS.index+' index request(s) issued in this suite, most recently to "'+idxUrl+
       '" — httpBase is "'+state.httpBase+'", so this is not addressed to a sub');

    // 1d. WHAT THE PANEL SAYS WHILE NOTHING ANSWERS. This is the machine as it stands:
    // static files, no map service. The honest sentence here is about THIS HANDHELD
    // holding nothing yet — never that a missing vehicle is what makes map data
    // impossible, because the map's data does not come from the vehicle.
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(200);
    const proseA = mapProse();
    const claimsA = hits(proseA, NO_SOURCE_CLAIMS);
    ok('with no map service answering, the panel does not tell the operator the map data is unobtainable',
       claimsA.length===0,
       claimsA.length ? ('SERVING=false; still claimed: '+claimsA.slice(0,3).join('  |  '))
                      : ('SERVING=false, '+proseA.length+' sentences scanned; the panel reports what '+
                         'this handheld holds and not what it is not plugged into. Sample: "'+
                         (norm(rowTitle('locks')).slice(0,120))+'"'));
    ok('...and it still says plainly that it is holding no chart data for this area',
       /NOT DOWNLOADED|no chart data/i.test(rowTitle('locks')+' '+pillText('locks')),
       'locks reads "'+pillText('locks')+'" — an absence has to be stated, and quietly is not silently');

    // 1e. THE DOWNLOAD SURFACE. bootConsider is the real decision path — the same call
    // setOrigin and the 5 s bootstrap tick make. The area seeded above already covers
    // this launch point and is fully cached, so the correct answer is "this handheld
    // already holds it", and no request of any kind is spent finding that out.
    if(typeof bootConsider==='function'){
      try{ await bootConsider(listed, O, 'suite: a launch point on a console with no sub'); }
      catch(e){ errs.push('bootConsider threw: '+((e&&e.message)||e)); }
    }
    await sleep(300);
    const img = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.imagery : {};
    ok('the download panel reports what this handheld holds for the launch point',
       img.state!=='waiting' && norm(img.why).length>20,
       'imagery row: state="'+img.state+'" pill="'+txt('crt-fetch-pill-imagery')+'" why="'+
       norm(img.why).slice(0,120)+'" — the area is '+tiles+' tiles and every one of them is here');
    const go=$('crt-fetch-go');
    const goVis = !!go && getComputedStyle(go).display!=='none' && go.getBoundingClientRect().height>0;
    ok('...and the operator is not locked out of downloading it: DOWNLOAD is on screen and pressable',
       goVis && go.disabled===false,
       'crt-fetch-go reads "'+txt('crt-fetch-go')+'", visible='+goVis+' disabled='+(go&&go.disabled)+
       ' — a console with no sub is still the console that holds the map');
    const proseB = mapProse();
    const simReason = hits(proseB, SIM_AS_REASON);
    ok('...and a simulated VEHICLE is never given as the reason there is no map data',
       simReason.length===0,
       simReason.length ? ('the panel blames the simulator: '+simReason.join('  |  '))
                        : ('top line reads "'+txt('crt-fetch-state')+'": '+
                           norm(($('crt-fetch-why')||{}).title||'').slice(0,140)));

    // ============ 2. THE SERVICE ANSWERS — REAL DATA, NO SUB ============
    SERVING = true;
    await crtLoadAll('suite: the handheld\'s own map service is up');
    await sleep(300);
    if(typeof loadCentreline==='function'){ try{ await loadCentreline(AREA); }catch(e){} }
    await sleep(150);
    const t1 = crtTierList(1);
    const shown = t1.filter(e=>status(e.id)==='present');
    ok('with its own map service answering, the sim console draws the real hazard layers',
       shown.length===t1.length && t1.length>0,
       shown.length+' of '+t1.length+' hazard layers loaded: '+
       t1.map(e=>e.id+'="'+pillText(e.id)+'"').slice(0,4).join('  '));
    ok('...and the published depth guidance comes down the same path',
       status('depth_nominal')==='present',
       'depth_nominal='+status('depth_nominal')+' pill="'+pillText('depth_nominal')+'"; '+
       'depth endpoints asked: '+JSON.stringify(CALLS.depth));
    ok('...and so does the canal centreline, which is a fact about the water and not about a sub',
       CALLS.centreline>0 && !!MAP.centreline && MAP.centreline.length>0,
       'centreline requests='+CALLS.centreline+' MAP.centreline='+
       (MAP.centreline ? (MAP.centreline.length+' line(s)') : 'null'));
    const bQuiet = badgeLook();
    ok('...and a complete hazard card raises no absence alarm on the map',
       !alarmed(bQuiet), badgeSay(bQuiet));

    /* WHERE THE ROWS SAY IT CAME FROM, which is a claim about provenance and this
       project does not let those slide. There is no Pi in this deployment — no host, no
       socket, nothing was ever addressed to a vehicle — and every one of these features
       was read off the map service on the handheld. A row that credits the Pi for them
       is naming a machine that is not part of this at all, and the same sentence is what
       sends an operator to the tether to fix a gap that lives on the console in their
       hands. Scanned only over rows that are REPORTING ON DATA (present, empty, absent),
       because those are the sentences that say where the answer came from. */
    const PI_SOURCE = /\bfrom the Pi\b|\bthe Pi looked\b|\basking the Pi\b|\bthe Pi (has|holds|answered|could not)\b|\bon the Pi\b/i;
    const answered = crtAll().filter(e=>['present','empty','absent'].indexOf(status(e.id))>=0);
    const credited = answered.filter(e=>PI_SOURCE.test(rowTitle(e.id)));
    ok('...and the rows credit the source the data actually came from — there is no Pi here to credit',
       answered.length>0 && credited.length===0,
       credited.length ? (credited.length+' of '+answered.length+' rows attribute this handheld\'s own '+
                          'data to a vehicle that was never addressed: '+
                          credited.slice(0,3).map(e=>e.id+' — "'+
                            (rowTitle(e.id).match(PI_SOURCE)||[''])[0]+'"').join(', ')+
                          '   [the chart module calls the holder "'+backend.crtName+
                          '" while the console resolved its map backend from '+backend.from+']')
                       : (answered.length+' rows reporting on data, e.g. locks: "'+
                          rowTitle('locks').slice(0,130)+'"'));

    // ============ 3. THE VEHICLE IS STILL SIMULATED, AND STILL SAYS SO ============
    /* Every check in this section is made AFTER the map came alive, deliberately. A
       local map backend must not leave the console looking like it has a sub, and the
       only place that can be shown is the page state where the map is working. */
    ok('the map having a backend did not give this console a vehicle',
       state.demo===true && !state.host && !state.wsBase && !state.realTel
       && !(state.piProbe && state.piProbe.ok),
       'demo='+state.demo+' host="'+state.host+'" wsBase="'+state.wsBase+'" realTel='+
       (!!state.realTel)+' piProbe='+JSON.stringify(state.piProbe));
    ok('...the link still reads as the simulator, never as a connected sub',
       (STATUS.link==='sim'||STATUS.link==='offline') && STATUS.link!=='online' && state.mode==='sim',
       'STATUS.link='+STATUS.link+' state.mode='+state.mode+' piSeen='+STATUS.piSeen);
    const rov=$('st-rov');
    const rovCls=norm((rov||{}).className), rovTitle=norm((rov||{}).title);
    ok('...and the sub icon is the red robot that says the simulator is flying this',
       !!rov && /\bsim\b|\bdown\b/.test(rovCls) && !/\bok\b/.test(rovCls)
       && /simulator is flying this/i.test(rovTitle),
       'st-rov class="'+rovCls+'" title="'+rovTitle.slice(0,110)+'"');
    // THE MAP'S OWN SIM FLAGGING, which is the one that could most easily have been
    // dropped in the name of "the map is real now". A simulated dive measures nothing,
    // so it paints no survey cell and the row says why in words.
    const measured = (typeof crtLiveMeasured==='function') ? crtLiveMeasured() : null;
    const liveCells = (typeof crtLiveCells==='function') ? crtLiveCells().length : -1;
    ok('...and a simulated dive still measures nothing: no survey cell is painted from it',
       measured===false && liveCells===0,
       'crtLiveMeasured()='+measured+' live cells='+liveCells+' over a '+MAP.track.length+
       '-point track — solid outlined cells on this map mean MEASURED');
    const surv = norm(rowTitle('depth_surveyed'));
    ok('...and the surveyed-depth row still says the depth is coming from the simulator',
       /simulator/i.test(surv) && /measures nothing|nothing is painted/i.test(surv),
       'depth_surveyed says: "'+surv.slice(-190)+'"');

    // ============ 4. THE DOWNLOADED CHART DATA IS NOT LABELLED SIMULATED ============
    /* The mirror image of section 3, and just as much a lie if it goes wrong. These are
       the Trust's published assets and an authority's published depth: real data about
       real water, as true with no sub attached as with one. depth_surveyed is excluded
       BY NAME because its sentence is about THIS SESSION'S soundings — the vehicle's own
       reading — and section 3 has just asserted that it must say "simulator". */
    const dataRows = crtAll().filter(e=>e.id!=='depth_surveyed'
                                     && ['present','empty'].indexOf(status(e.id))>=0);
    const mislabelled = dataRows.filter(e=>SIM_WORDS.test(rowTitle(e.id)) || SIM_WORDS.test(pillText(e.id)));
    ok('downloaded chart data is not labelled simulated, because it is not',
       dataRows.length>0 && mislabelled.length===0,
       mislabelled.length ? ('marked as simulated: '+mislabelled.map(e=>e.id+' — "'+
                             (rowTitle(e.id).match(SIM_WORDS)||[''])[0]+'"').join(', '))
                          : (dataRows.length+' rows carrying real data, none of them flagged: e.g. locks — "'+
                             rowTitle('locks').slice(0,120)+'"'));
    const cred = txt('crt-credit');
    ok('...and it is credited to the body that surveyed it, not to this console',
       /Canal\s*&\s*River Trust/i.test(cred) && !SIM_WORDS.test(cred),
       'credit line reads "'+cred.slice(0,150)+'"');
    const shownRow = rowEl('locks');
    ok('...and the row wears the ordinary SHOWN treatment, with no sim class on it',
       !!shownRow && /\bshown\b/.test(shownRow.className) && !/\bsim\b|\bsuspect\b/.test(shownRow.className)
       && /\d/.test(pillText('locks')),
       'locks row class="'+norm(shownRow&&shownRow.className)+'" pill="'+pillText('locks')+
       '" (the count is the number of real features loaded)');

    // ============ 5. THE HONEST STATES SURVIVE THE MAP HAVING A BACKEND ============
    ok('a layer the service answered about and does not hold reads ABSENT',
       status(GONE_FROM_INDEX)==='absent' && /ABSENT/i.test(pillText(GONE_FROM_INDEX))
       && /not on the disk|NO DATA/i.test(rowTitle(GONE_FROM_INDEX)),
       GONE_FROM_INDEX+': status='+status(GONE_FROM_INDEX)+' pill="'+pillText(GONE_FROM_INDEX)+
       '" says "'+rowTitle(GONE_FROM_INDEX).slice(0,150)+'"');
    ok('...including one the index lists whose file is gone from the card',
       status(GONE_FROM_DISK)==='absent' && (CALLS.layer[GONE_FROM_DISK]||0)>0,
       GONE_FROM_DISK+': status='+status(GONE_FROM_DISK)+' pill="'+pillText(GONE_FROM_DISK)+
       '" after '+(CALLS.layer[GONE_FROM_DISK]||0)+' request(s) — a 404 from a service that '+
       'answered is the one case this console may call ABSENT');
    ok('...and a layer it HOLDS with nothing inside this area still reads NONE MAPPED',
       status(NONE_HERE)==='empty' && /NONE MAPPED/i.test(pillText(NONE_HERE)),
       NONE_HERE+': status='+status(NONE_HERE)+' pill="'+pillText(NONE_HERE)+
       '" — "no slipways here" and "no slipway data here" are opposite claims');

    // THE AREA THIS HANDHELD HAS NEVER DOWNLOADED. The service is up and answers about
    // it; there is simply no card. That must stay NOT DOWNLOADED, stay quiet, and above
    // all must not become ABSENT — nothing looked at anything.
    await STORE.areaPut({name:BENCH, bbox:BBOX2, zmin:16, zmax:18, tiles:0, cached:0,
                         detail:'standard', savedAt:Date.now()+1, mirrored:false});
    MAP.activeArea = BENCH;
    if(typeof crtSetArea==='function') crtSetArea(BENCH);
    await waitFor(()=>CRT.area===BENCH && !CRT._busy && status('locks')!=='off', 12000);
    await sleep(250);
    const nd = crtAll().filter(e=>status(e.id)==='not-downloaded');
    ok('an area this handheld has never downloaded still reads NOT DOWNLOADED',
       CRT.area===BENCH && nd.length>0 && /NOT DOWNLOADED/i.test(pillText('locks')),
       'CRT.area='+CRT.area+': '+nd.length+' of '+crtAll().length+' layers read "'+
       pillText('locks')+'" from a service that answered 404 for that card');
    const wrongly = crtAll().filter(e=>status(e.id)==='absent');
    ok('...and nothing there is reported ABSENT, because nothing looked at anything',
       wrongly.length===0,
       wrongly.length ? ('claimed absent with no card to have looked in: '+
                         wrongly.map(e=>e.id).join(', '))
                      : 'no layer claims a fact about water this handheld holds nothing for');
    const bBench = badgeLook();
    ok('...and it is said quietly: never the map\'s loudest mark on a console that has simply not downloaded it',
       !alarmed(bBench), badgeSay(bBench));
    const benchClaims = hits(mapProse(), NO_SOURCE_CLAIMS);
    ok('...and even here the reason given is never "there is no sub to get it from"',
       benchClaims.length===0,
       benchClaims.length ? benchClaims.slice(0,3).join('  |  ')
                          : ('locks says: "'+rowTitle('locks').slice(0,170)+'"'));

    // ============ 6. THE ORDINARY CASE: THE WHOLE NETWORK, AND NOTHING ELSE ======
    /* EVERY SECTION ABOVE GAVE THIS CONSOLE AN AREA FIRST, because that is what the
       map used to need. It does not any more. The Canal & River Trust network is
       fetched ONCE, nationally, on launch, and lives on this handheld; the area
       decides which stretch of satellite imagery is cached and which dive journals
       the surveyed depth is built from, and NOTHING ELSE.

       So this section takes everything away — the area, the launch point, and there
       was never a vehicle — and asserts the map is still there. Four absences at
       once, on purpose, because each one of them used to be enough on its own to
       leave the operator with a blank rectangle:

         NO AREA          crtLoadAll used to open `if(!CRT.area) return`
         NO LAUNCH POINT  no origin, so nothing has ever been positioned here
         NO PI            ?sim=1 resolves no host at all
         SIMULATED SUB    which says nothing whatever about whether the MAP is real

       And the last of those is the one worth restating: the Trust's layers are real
       data about real water, surveyed by an authority, and they are exactly as true
       with no sub attached as with one. */
    netBuild();
    NET_SERVING = true;
    SERVING = false;                       // this handheld holds no per-area card at all
    try{ await STORE.areaDelete(AREA); }catch(e){}
    try{ await STORE.areaDelete(BENCH); }catch(e){}
    MAP.activeArea = null;
    if(typeof crtSetArea==='function') crtSetArea(null);
    // The launch point goes too, through the store, because refreshBootstrap re-reads
    // it every five seconds and an origin cleared in memory alone comes straight back.
    try{ await STORE.set('origin', null); }catch(e){}
    MAP.origin = null; MAP.hasOrigin = false; MAP.viewLat = null; MAP.viewLon = null;
    await waitFor(()=>CRT.area===null && !CRT._busy, 12000);
    await crtLoadAll('suite: the national store, with no area and no launch point');
    await sleep(250);
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(200);

    const emptied = {area:CRT.area, active:MAP.activeArea, origin:MAP.hasOrigin,
                     areas:(await STORE.areas()).length, host:state.host,
                     ws:state.wsBase, demo:state.demo};
    ok('this handheld now has NO area, NO launch point and NO vehicle (the premise of this section)',
       CRT.area===null && MAP.activeArea==null && MAP.hasOrigin===false
       && emptied.areas===0 && !state.host && !state.wsBase && state.demo===true,
       JSON.stringify(emptied));
    const netUrls = CALLS.urls.filter(u=>/\/api\/crt(\/|$)/.test(u.split('?')[0]));
    ok('...and the chart layers were asked for anyway, at a path with no area in it',
       CALLS.net>0 && netUrls.length>0 && netUrls.filter(u=>/\/areas\//.test(u)).length===0,
       CALLS.net+' national index request(s); '+Object.keys(CALLS.netLayer).length+
       ' layer(s) asked for, e.g. "'+(netUrls[netUrls.length-1]||'(none)')+'"');

    const netRows = crtAll().filter(e=>e.kind!=='depth' && e.id!==NET_MISSING);
    const HELD = ['present','empty','held'];
    const blank = netRows.filter(e=>HELD.indexOf(status(e.id))<0);
    ok('the map is THERE: every Trust layer is loaded with no area, no launch point, no Pi and a simulated sub',
       netRows.length>0 && blank.length===0,
       blank.length ? ('not loaded: '+blank.map(e=>e.id+'='+status(e.id)+' ("'+pillText(e.id)+
                       '")').slice(0,5).join(', '))
                    : (netRows.length+' layers loaded, e.g. '+netRows.slice(0,3)
                        .map(e=>e.id+'="'+pillText(e.id)+'"').join('  ')));
    const off = netRows.filter(e=>!crtIsOn(e.id));
    ok('...and every one of them is switched ON, because a layer that is held and hidden might as well not be',
       off.length===0,
       off.length ? ('held but not drawn: '+off.map(e=>e.id+' (tier '+e.tier+')').join(', '))
                  : (netRows.length+' layers on, across tiers '+
                     [1,2,3].map(t=>t+':'+netRows.filter(e=>e.tier===t).length).join(' ')));
    const quiet = netRows.filter(e=>['not-downloaded','no-area'].indexOf(status(e.id))>=0);
    ok('...and NOT DOWNLOADED is not what a console that HAS the data says',
       quiet.length===0,
       quiet.length ? ('still reporting an absence over a store that answered: '+
                       quiet.map(e=>e.id+'='+status(e.id)).join(', '))
                    : ('0 of '+netRows.length+' layers read NOT DOWNLOADED or NO AREA — '+
                       'those are what you see when something has gone wrong, not what you '+
                       'see because nothing has gone right yet'));
    ok('...and a layer the store looked for and does not hold STILL says ABSENT',
       status(NET_MISSING)==='absent' && /ABSENT/i.test(pillText(NET_MISSING)),
       NET_MISSING+': status='+status(NET_MISSING)+' pill="'+pillText(NET_MISSING)+
       '" — the honesty rule is not being relaxed to buy the quiet, and that trade '+
       'would be a worse defect than the one being fixed');
    const netData = netRows.filter(e=>['present','empty'].indexOf(status(e.id))>=0);
    const netLies = netData.filter(e=>SIM_WORDS.test(rowTitle(e.id)) || SIM_WORDS.test(pillText(e.id)));
    ok('...and none of it is labelled simulated, on a console whose VEHICLE is',
       netData.length>0 && netLies.length===0 && state.demo===true,
       netLies.length ? ('marked as simulated: '+netLies.map(e=>e.id).join(', '))
                      : (netData.length+' layers of real surveyed data on a simulated '+
                         'console, e.g. locks — "'+rowTitle('locks').slice(0,110)+'"'));

    /* AND IT REACHES THE GLASS. Held is not drawn: with no position of any kind there
       is nowhere on the screen to put a lock, so this gives the console the one thing
       it genuinely needs to draw — somewhere to look — and STILL no area. If a mark
       lands, then the area was never what the map was waiting for. */
    const O2 = {lat:LAT, lon:LON, src:'suite: a launch point, and still no offline area'};
    await STORE.set('origin', O2);
    MAP.origin = O2; MAP.hasOrigin = true;
    await refreshBootstrap();
    await waitFor(()=>typeof TILES!=='undefined' && !!TILES.last, 8000);
    await sleep(400);
    const paints = [];
    const cv = document.createElement('canvas');
    cv.width = (MAP.canvas && MAP.canvas.width) || 800;
    cv.height = (MAP.canvas && MAP.canvas.height) || 600;
    const dctx = cv.getContext('2d');
    ['fill','stroke','fillText'].forEach(k=>{
      const raw = dctx[k].bind(dctx);
      dctx[k] = function(){ paints.push(k); return raw.apply(dctx, arguments); };
    });
    let drawErr = null;
    try{ crtDraw(dctx, MAP.dpr||1); }catch(e){ drawErr = (e&&e.message)||String(e); }
    ok('the Trust\'s marks are PAINTED on a console that still has no offline area at all',
       !drawErr && paints.length>0 && MAP.activeArea==null && CRT.area===null,
       drawErr ? ('crtDraw threw: '+drawErr)
               : (paints.length+' paint call(s) from crtDraw with activeArea='+MAP.activeArea+
                  ' and CRT.area='+CRT.area+', projection='+
                  ((typeof TILES!=='undefined'&&TILES.last)?'ready':'ABSENT')+
                  ' — an area caches imagery and builds the surveyed depth; it is not '+
                  'what the chart layers were ever waiting for'));

    // ============ NOTHING ELSE BROKEN ============
    // The console is left in the state this round makes ordinary — the whole network
    // held, no area — so the photograph run.py takes is the new normal rather than
    // the half-emptied state section 5 needed.
    window.fetch = realFetch;
    const need=['crt-panel','crt-list','crt-absent','crt-credit','crt-fetch','crt-fetch-state',
                'crt-fetch-why','crt-fetch-go','st-rov']
               .concat(crtAll().map(e=>'crt-row-'+e.id));
    const missing=need.filter(id=>!$(id));
    ok('every element these checks read is on the page', missing.length===0,
       missing.length ? ('MISSING: '+missing.join(', ')) : need.length+' elements found');
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>{
    try{ window.fetch = realFetch; }catch(_){}
    fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)});
  });
})();

"use strict";
/* ============================================================================
   NAV UI — origin acquisition (§4) and offline area management (§5).
   Two modals built on demand, wired to the nav API. Opened from the ORIGIN
   status tile, the map's empty-state button, and CONFIG.
   ============================================================================ */

function _navFetch(path, opts){ return fetch((state.httpBase||'') + path, opts); }

/* ---- geocoding is CLIENT-FIRST (§1): browser → Nominatim directly. It needs
   INTERNET, not the Pi. The Pi proxy is only a fallback, never a precondition. ---- */
async function _geocode(q){
  const url='https://nominatim.openstreetmap.org/search?'+new URLSearchParams({q, format:'jsonv2', limit:'5', 'accept-language':'en'});
  try{ const r=await fetch(url, {headers:{Accept:'application/json'}});
    if(r.ok){ const j=await r.json(); if(Array.isArray(j)) return j.map(it=>({name:it.display_name, lat:+it.lat, lon:+it.lon})); } }catch(e){}
  try{ const r=await _navFetch('/api/geocode/search?q='+encodeURIComponent(q)); const j=await r.json(); return j.results||null; }catch(e){}
  return null;   // null → no internet (honest); NOT "backend unreachable"
}
async function _revGeocode(lat, lon){
  const url='https://nominatim.openstreetmap.org/reverse?'+new URLSearchParams({lat, lon, format:'jsonv2', zoom:'16', 'accept-language':'en'});
  try{ const r=await fetch(url, {headers:{Accept:'application/json'}}); if(r.ok){ const d=await r.json();
    const a=d.address||{}; return a.waterway||a.road||a.suburb||a.village||a.town||a.city||(d.display_name||'').split(',')[0]||null; } }catch(e){}
  return null;
}
function _mkModal(id){
  let m=$(id); if(m) return m;
  m=document.createElement('div'); m.id=id; m.className='nav-modal';
  m.addEventListener('click', (e)=>{ if(e.target===m) m.classList.remove('show'); });
  document.body.appendChild(m); return m;
}
function _hostForPhone(){
  const h = state.host || location.host || '<pi-ip>:8000';
  return 'https://' + h + '/origin.html';
}

/* ---- ORIGIN (§2/§4) — friendly: use my location, search an address, or drop it
   on the map. No coordinates to type. --------------------------------------- */
function openOriginModal(){
  const m=_mkModal('origin-modal');
  const o = (typeof MAP!=='undefined' && MAP.origin) ? MAP.origin : null;
  const cur = o ? `Set — ±${Math.round(o.accuracy)} m (${o.source})` : 'Not set yet';
  m.innerHTML =
    '<div class="nav-card">'+
      '<div class="nav-head"><span class="font-headline-sm text-headline-sm text-primary font-bold">WHERE ARE YOU LAUNCHING?</span>'+
        '<button class="mp-btn nav-x">CLOSE</button></div>'+
      '<div class="nav-body">'+
        `<div class="nav-cur ${o?'ok':'warn'}">${cur}</div>`+
        '<button id="o-here" class="mp-btn nav-primary nav-big">📍 &nbsp;USE MY LOCATION</button>'+
        '<div class="nav-hint">Uses the handheld&rsquo;s location. It&rsquo;s a rough WiFi fix — refine it below if needed.</div>'+
        '<div class="nav-sec">Search an address or place</div>'+
        '<input id="o-search" class="nav-in" placeholder="e.g. Gas Street Basin, Birmingham" autocomplete="off">'+
        '<div id="o-search-res" class="o-res"></div>'+
        '<div class="nav-sec">Or point it on the map</div>'+
        '<button id="o-onmap" class="mp-btn nav-big">🗺 &nbsp;DROP IT ON THE MAP</button>'+
        '<div class="nav-hint">Opens the map — pan to your launch spot and tap it.</div>'+
        '<div id="o-msg" class="nav-msg"></div>'+
        '<details class="nav-adv"><summary>Advanced — nudge the recorded track</summary>'+
          '<div class="nav-row"><input id="o-dx" class="nav-in" placeholder="left/right m" value="0">'+
            '<input id="o-dy" class="nav-in" placeholder="fwd/back m" value="0">'+
            '<input id="o-rot" class="nav-in" placeholder="rotate&deg;" value="0"></div>'+
          '<button id="o-adjust" class="mp-btn">APPLY ADJUSTMENT</button></details>'+
      '</div>'+
    '</div>';
  m.querySelector('.nav-x').onclick=()=>m.classList.remove('show');
  // 1) use my location
  $('o-here').onclick=()=>{ $('o-msg').textContent='locating…'; requestDeviceLocation();
    setTimeout(()=>{ if(typeof MAP!=='undefined'&&MAP.hasOrigin) m.classList.remove('show'); }, 1200); };
  // 2) search an address → pick a result → set origin there
  const si=$('o-search'); if(si) si.addEventListener('input', (e)=>originSearch(e.target.value));
  // 3) drop it on the map
  $('o-onmap').onclick=()=>{ m.classList.remove('show'); if(typeof armOriginTap==='function') armOriginTap(); };
  // advanced: track nudge (unchanged)
  $('o-adjust').onclick=async()=>{
    const body={dx_m:parseFloat($('o-dx').value||'0'), dy_m:parseFloat($('o-dy').value||'0'), rotation_deg:parseFloat($('o-rot').value||'0')};
    try{ const r=await _navFetch('/api/nav/dive/current/adjust',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      $('o-msg').textContent = r.ok ? 'adjustment applied' : ('adjust failed: '+r.status); }
    catch(e){ $('o-msg').textContent='no active dive / backend'; }
  };
  m.classList.add('show');
}
/* address search inside the origin modal: click a hit → set the origin there */
let _oSearchTimer=null;
function originSearch(q){
  const res=$('o-search-res'); if(!res) return;
  if(!q || q.length<3){ res.innerHTML=''; return; }
  clearTimeout(_oSearchTimer);
  _oSearchTimer=setTimeout(async()=>{
    res.innerHTML='<div class="msr-empty">searching…</div>';
    {
      const results=await _geocode(q);
      if(results===null){ res.innerHTML='<div class="msr-empty">no internet — search needs a connection</div>'; return; }
      if(!results.length){ res.innerHTML='<div class="msr-empty">no matches</div>'; return; }
      res.innerHTML='';
      results.forEach(it=>{ const d=document.createElement('div'); d.className='msr-row'; d.textContent=it.name;
        d.onclick=async()=>{
          const msg=$('o-msg'); if(msg) msg.textContent='setting origin…';
          if(typeof MAP!=='undefined'){ MAP.viewLat=it.lat; MAP.viewLon=it.lon; MAP.follow=false; }
          const ok=await setOrigin({ lat:it.lat, lon:it.lon, accuracy:40, source:'map_tap', t:Date.now() });
          if(ok!==false){ if(typeof MAP!=='undefined'){ MAP.x=0; MAP.y=0; MAP.follow=true; }
            $('origin-modal').classList.remove('show');
            if(typeof offerRefine==='function') offerRefine(40);   // nudge to tap-refine the exact bank
          }
        };
        res.appendChild(d); });
    }
  }, 350);
}
/* Origin is CLIENT-OWNED (§1): it is stored locally and works with the Pi off.
   Mirroring to the Pi is best-effort and secondary (the Pi needs it for dead
   reckoning) — its absence never blocks setting or using the origin. */
async function setOrigin(o){
  o.heading_deg = Math.round((typeof MAP!=='undefined'?MAP.hdg:state.heading)||0);   // heading0 from the sub's IMU (§2)
  o.t = o.t || Date.now();
  const msg=(t)=>{ const el=$('o-msg'); if(el) el.textContent=t; };
  // 1) client accuracy gate (no backend involved)
  if(o.accuracy>15 && !o._override){
    if(!confirm('Origin accuracy ±'+Math.round(o.accuracy)+' m is coarse. Set it anyway (refine by tapping the map)?')){ msg('cancelled — accuracy too high'); return false; }
  }
  // 2) persist locally — this is the source of truth
  try{ if(typeof STORE!=='undefined') await STORE.set('origin', o); }catch(e){}
  if(typeof MAP!=='undefined'){ MAP.origin=o; MAP.hasOrigin=true; if(typeof renderOriginTile==='function') renderOriginTile(); if(typeof updateEmptyState==='function') updateEmptyState(); }
  msg('origin set');
  // 3) mirror to the Pi if it's up (dead reckoning). Best-effort; never blocks.
  try{ await _navFetch('/api/origin?override=true',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)}); }catch(e){/* Pi off — fine */}
  return true;
}

/* ---- §2: auto-request the handheld's location on load ------------------------
   The origin fix comes from the topside ROG Ally's own browser. On load, if no
   origin is set, prompt for location automatically (don't wait for a dialog),
   centre the map on it, and — because WiFi positioning is coarse — offer
   tap-the-map refinement when the reported accuracy is poor. North still comes
   from the sub's IMU (captured in setOrigin), never the handheld. */
async function autoRequestOrigin(){
  if(!CONFIG.map.autoOrigin || state._fileSim) return;
  // client-owned origin first (works with the Pi off); mirror it up if the Pi is there.
  try{
    if(typeof STORE!=='undefined'){ const o=await STORE.get('origin', null);
      if(o && typeof o.lat==='number'){ if(typeof MAP!=='undefined'){ MAP.origin=o; MAP.hasOrigin=true; renderOriginTile&&renderOriginTile(); }
        _navFetch('/api/origin?override=true',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)}).catch(()=>{});
        return; } }
  }catch(e){}
  requestDeviceLocation();
}
function requestDeviceLocation(){
  if(!('geolocation' in navigator)){ showOriginFallback({message:'no geolocation on this device'}); return; }
  if(!window.isSecureContext){ showOriginFallback({message:'insecure context — open Neptune over HTTPS'}); return; }
  showOriginPrompt('LOCATING…', 'waiting for the handheld&rsquo;s position', null);
  navigator.geolocation.getCurrentPosition(
    p=>{
      const body={ lat:p.coords.latitude, lon:p.coords.longitude, accuracy:p.coords.accuracy, source:'device', t:Date.now() };
      setOrigin(body).then(ok=>{
        if(ok===false){ showOriginFallback({message:'could not store the fix'}); return; }
        if(typeof MAP!=='undefined'){ MAP.x=0; MAP.y=0; }   // origin is the local frame's (0,0) — centre there
        if(p.coords.accuracy>(CONFIG.map.originRefineM||30)) offerRefine(p.coords.accuracy);
        else hideOriginPrompt();
      });
    },
    err=>showOriginFallback(err),
    {enableHighAccuracy:true, timeout:15000, maximumAge:0}
  );
}
function offerRefine(accuracy){
  // non-blocking: the operator can see which bank they're on — a tap beats WiFi positioning (§2)
  showOriginPrompt('ORIGIN ±'+Math.round(accuracy)+' m (WiFi)',
    'Coarse fix. Tap the map on your launch point to refine.',
    { label:'TAP TO REFINE', run:armOriginTap });
}
function showOriginFallback(err){
  showOriginPrompt('LOCATION UNAVAILABLE', (err&&err.message)||'permission denied or timed out',
    { label:'SET MANUALLY', run:()=>{ hideOriginPrompt(); openOriginModal(); } });
}
function armOriginTap(){
  if(typeof MAP==='undefined') return;
  MAP.originTap=true;                          // one-shot: the next map tap sets the origin (map.js)
  if(typeof expandMap==='function') expandMap();
  if(typeof updateEmptyState==='function') updateEmptyState();
  showOriginPrompt('TAP YOUR LAUNCH POINT', 'Tap where you are standing on the map.',
    { label:'CANCEL', run:()=>{ MAP.originTap=false; hideOriginPrompt(); } });
}

/* ---- origin prompt banner (non-blocking) ---- */
function showOriginPrompt(title, sub, action){
  let el=$('origin-prompt');
  if(!el){ el=document.createElement('div'); el.id='origin-prompt'; document.body.appendChild(el); }
  el.innerHTML = '<div class="op-title">'+title+'</div><div class="op-sub">'+sub+'</div>'+
                 (action?'<button class="op-btn">'+action.label+'</button>':'');
  if(action){ const b=el.querySelector('.op-btn'); if(b) b.onclick=action.run; }
  el.classList.add('show');
}
function hideOriginPrompt(){ const el=$('origin-prompt'); if(el) el.classList.remove('show'); }

/* ---- AREA MANAGER (§4) — a simple LIST. New areas are added by the in-map
   navigate-and-select flow (below), never by typing coordinates. ------------- */
function openAreaManager(){
  const m=_mkModal('area-modal');
  m.innerHTML =
    '<div class="nav-card">'+
      '<div class="nav-head"><span class="font-headline-sm text-headline-sm text-primary font-bold">MAP AREAS</span>'+
        '<button class="mp-btn nav-x">CLOSE</button></div>'+
      '<div class="nav-body">'+
        '<div class="nav-sec">Downloaded areas</div>'+
        '<div id="a-list" class="a-list">loading…</div>'+
        '<div class="nav-hint">To add an area: expand the map, pan to the spot, and press '+
          '<b>＋ AREA</b> — then <b>DOWNLOAD THIS AREA</b>. No coordinates to type.</div>'+
        '<button id="a-addmap" class="mp-btn nav-primary">OPEN MAP TO ADD AN AREA</button>'+
      '</div>'+
    '</div>';
  m.querySelector('.nav-x').onclick=()=>m.classList.remove('show');
  $('a-addmap').onclick=()=>{ m.classList.remove('show'); enterSelectMode(); };
  _loadAreas();
  m.classList.add('show');
}
async function _loadAreas(){
  const box=$('a-list'); if(!box) return;
  // areas are CLIENT-OWNED (§2): listed from local storage, no Pi involved.
  const areas = (typeof STORE!=='undefined') ? await STORE.areas() : [];
  if(!areas.length){ box.textContent='none yet — add one from the map'; return; }
  box.innerHTML='';
  areas.sort((a,b)=>(b.savedAt||0)-(a.savedAt||0)).forEach(a=>{
    const active = (typeof MAP!=='undefined' && MAP.activeArea===a.name);
    const row=document.createElement('div'); row.className='a-row';
    const meta=document.createElement('div'); meta.style.flex='1'; meta.style.minWidth='0';
    const mb=(a.cached||0)*20/1024;
    meta.innerHTML=`<div class="a-name">${a.name}${active?' •':''}</div>`+
      `<div class="a-meta">${a.cached||0}/${a.tiles||0} tiles · ~${mb<10?mb.toFixed(1):Math.round(mb)} MB · z${a.zmin}–${a.zmax}${a.mirrored?' · ⤴ Pi':''}</div>`;
    const act=document.createElement('button'); act.className='mp-btn'; act.textContent=active?'ACTIVE':'ACTIVATE';
    act.onclick=()=>{ if(typeof MAP!=='undefined'){ MAP.activeArea=a.name; MAP.hasArea=true; MAP.viewLat=(a.bbox[1]+a.bbox[3])/2; MAP.viewLon=(a.bbox[0]+a.bbox[2])/2; MAP.follow=false; if(typeof updateEmptyState==='function') updateEmptyState(); } _loadAreas(); };
    const del=document.createElement('button'); del.className='mp-btn'; del.textContent='DEL';
    del.onclick=async()=>{ if(confirm('Delete area "'+a.name+'"? This removes the saved imagery from this device.')){
      await STORE.evictArea(a);
      _navFetch('/api/areas/'+encodeURIComponent(a.name),{method:'DELETE'}).catch(()=>{});   // also drop the Pi mirror if present
      if(typeof MAP!=='undefined' && MAP.activeArea===a.name){ MAP.activeArea=null; MAP.hasArea=false; if(typeof updateEmptyState==='function') updateEmptyState(); }
      _loadAreas(); } };
    row.appendChild(meta); row.appendChild(act); row.appendChild(del); box.appendChild(row);
  });
}

/* ---- §4: navigate-and-select download (in the expanded map) --------------- */
let _asDetail='standard';
function enterSelectMode(){
  if(typeof MAP==='undefined') return;
  if(!MAP.expanded && typeof expandMap==='function') expandMap();
  MAP.selectMode=true; MAP.selReadout=updateAreaReadout;
  const el=$('area-select'); if(el) el.classList.add('on');
  const p=$('as-prog'); if(p) p.textContent='';
  if(typeof updateEmptyState==='function') updateEmptyState();
}
function exitSelectMode(){
  if(typeof MAP!=='undefined'){ MAP.selectMode=false; MAP.selReadout=null; }
  const el=$('area-select'); if(el) el.classList.remove('on');
  if(typeof updateEmptyState==='function') updateEmptyState();
}
function _detailZooms(){ const z=(CONFIG.map.detailZooms||{})[_asDetail]||[16,18]; return z; }
function updateAreaReadout(bbox){
  const el=$('as-read'); if(!el) return;
  if(!bbox){ el.textContent='pan the map to select an area'; return; }
  const [zmin,zmax]=_detailZooms();
  let n=0; try{ n=countTilesBBox(bbox,zmin,zmax); }catch(e){ n=0; }
  const mb=n*20/1024;   // ~20 KB/tile JPEG (matches backend estimate)
  el.textContent='~'+n.toLocaleString()+' tiles · ~'+(mb<10?mb.toFixed(1):Math.round(mb))+' MB  (z'+zmin+'–'+zmax+')';
}
async function downloadSelected(){
  if(typeof MAP==='undefined') return;
  const prog=(t)=>{ const p=$('as-prog'); if(p) p.textContent=t; };
  const bbox=mapSelectionBBox(); if(!bbox){ prog('pan/zoom to select an area'); return; }
  // SAVE OFFLINE writes tiles into the Cache API FROM THE BROWSER (§2) — works with the Pi off.
  // Auto-name client-side (reverse geocode, else coordinates); the operator can rename later.
  let name=await _revGeocode((bbox[1]+bbox[3])/2, (bbox[0]+bbox[2])/2);
  name=(name || (((bbox[1]+bbox[3])/2).toFixed(4)+'_'+((bbox[0]+bbox[2])/2).toFixed(4)));
  name=name.replace(/[^\w-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,48) || 'area';
  prog('saving imagery…');
  try{
    const meta=await STORE.saveArea(name, bbox, _asDetail, (done,total)=>prog('saving '+done+'/'+total+' tiles…'));
    prog('saved '+meta.cached+'/'+meta.tiles+' tiles offline'+(window.isSecureContext?'':' (meta only — insecure context)'));
    if(typeof MAP!=='undefined'){ MAP.hasArea=true; MAP.activeArea=name; if(typeof updateEmptyState==='function') updateEmptyState(); }
    _mirrorToPi(name, bbox);                         // optional second copy — never required (§2)
    setTimeout(exitSelectMode, 1600);
  }catch(e){ prog('save failed: '+(e&&e.message||'')); }
}
/* mirror a saved area to the Pi as a SECOND copy (and for a Pi-side map view). If the
   Pi is off or the mirror fails, the area is still saved client-side and still usable. */
function _mirrorToPi(name, bbox){
  if(state.wsStatus!=='online') return;
  _navFetch('/api/areas',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name, bbox, detail:_asDetail})})
    .then(async()=>{ LOG.state('area mirrored to Pi: '+name);
      try{ if(typeof STORE!=='undefined'){ const a=(await STORE.areas()).find(x=>x.name===name); if(a){ a.mirrored=true; await STORE.areaPut(a); } } }catch(e){} })
    .catch(()=>{ LOG.net('Pi mirror skipped (area saved client-side)'); });
}
function _watchAreaProgress(){
  const base = state.wsBase || (location.host ? (location.protocol==='https:'?'wss':'ws')+'://'+location.host : '');
  if(!base) return;
  let ws; try{ ws=new WebSocket(base+'/ws/nav'); }catch(e){ return; }
  ws.onmessage=(ev)=>{ let m; try{ m=JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='area_progress'){ const el=$('as-prog'); if(el){
      if(m.state==='running' && m.total) el.textContent='downloading '+m.done+'/'+m.total+' tiles…';
      else if(m.state==='starting') el.textContent='starting ('+m.total+' tiles, ~'+m.est_mb+' MB)…';
      else if(m.state==='done'){ el.textContent='saved ('+(m.ok||0)+'/'+(m.total||0)+' tiles)'; try{ws.close();}catch(e){} } } } };
  setTimeout(()=>{ try{ws.close();}catch(e){} }, 600000);
}

/* ---- Nominatim search in the expanded map (online only, §4) ---- */
let _searchTimer=null;
function mapSearch(q){
  const res=$('map-search-res'); if(!res) return;
  if(!q || q.length<3){ res.innerHTML=''; return; }
  clearTimeout(_searchTimer);
  _searchTimer=setTimeout(async()=>{
    const results=await _geocode(q);   // browser → Nominatim directly (§1); needs internet, not the Pi
    if(results===null){ res.innerHTML='<div class="msr-empty">no internet — search needs a connection</div>'; return; }
    if(!results.length){ res.innerHTML='<div class="msr-empty">no matches</div>'; return; }
    res.innerHTML='';
    results.forEach(it=>{ const d=document.createElement('div'); d.className='msr-row'; d.textContent=it.name;
      d.onclick=()=>{ if(typeof MAP!=='undefined'){ MAP.follow=false; MAP.viewLat=it.lat; MAP.viewLon=it.lon; } res.innerHTML=''; $('map-search-in').value=''; };
      res.appendChild(d); });
  }, 350);
}

/* ---- dive logs (§1) — CLIENT-OWNED, browsable with the Pi off ---- */
async function saveCurrentDive(){
  if(typeof MAP==='undefined' || !MAP.track || MAP.track.length<2){ alert('No track to save yet — dive first.'); return null; }
  const id='dive-'+new Date().toISOString().replace(/[:.]/g,'').slice(0,15);
  const depths=MAP.track.map(p=>p.depth||0);
  const dive={ id, at:Date.now(), origin:MAP.origin||null, points:MAP.track.length,
    max_depth:Math.max(0,...depths),
    track:MAP.track.map(p=>({x:+(p.x||0).toFixed(2), y:+(p.y||0).toFixed(2), depth:+(p.depth||0).toFixed(2)})) };
  await STORE.divePut(dive); LOG.state('dive saved locally: '+id);
  return dive;
}
function openDiveLog(){
  const m=_mkModal('dive-modal');
  m.innerHTML =
    '<div class="nav-card"><div class="nav-head">'+
      '<span class="font-headline-sm text-headline-sm text-primary font-bold">DIVE LOGS</span>'+
      '<button class="mp-btn nav-x">CLOSE</button></div>'+
    '<div class="nav-body">'+
      '<div class="nav-hint">Stored on this device — browse and replay with the Pi off.</div>'+
      '<button id="d-save" class="mp-btn nav-primary">SAVE CURRENT TRACK AS A DIVE</button>'+
      '<div class="nav-sec">Saved dives</div><div id="d-list" class="a-list">loading…</div>'+
      (MAP&&MAP.replay?'<button id="d-live" class="mp-btn">↩ EXIT REPLAY (RESUME LIVE)</button>':'')+
    '</div></div>';
  m.querySelector('.nav-x').onclick=()=>m.classList.remove('show');
  $('d-save').onclick=async()=>{ const d=await saveCurrentDive(); if(d){ _loadDives(); } };
  const lv=$('d-live'); if(lv) lv.addEventListener('click', ()=>{ if(typeof MAP!=='undefined'){ MAP.replay=false; MAP.track=[]; } m.classList.remove('show'); });
  _loadDives();
  m.classList.add('show');
}
async function _loadDives(){
  const box=$('d-list'); if(!box) return;
  const dives = (typeof STORE!=='undefined') ? await STORE.dives() : [];
  if(!dives.length){ box.textContent='none yet'; return; }
  box.innerHTML='';
  dives.sort((a,b)=>(b.at||0)-(a.at||0)).forEach(d=>{
    const row=document.createElement('div'); row.className='a-row';
    const meta=document.createElement('div'); meta.style.flex='1'; meta.style.minWidth='0';
    const when=new Date(d.at||0).toLocaleString();
    meta.innerHTML=`<div class="a-name">${d.id}</div><div class="a-meta">${when} · ${d.points} pts · max ${Math.round((d.max_depth||0)*10)/10} m</div>`;
    const rep=document.createElement('button'); rep.className='mp-btn'; rep.textContent='REPLAY';
    rep.onclick=()=>{ if(typeof MAP!=='undefined'){ MAP.replay=true; MAP.track=(d.track||[]).slice();
      if(d.origin){ MAP.origin=d.origin; MAP.hasOrigin=true; renderOriginTile&&renderOriginTile(); }
      MAP.follow=false; if(typeof expandMap==='function') expandMap(); if(typeof updateEmptyState==='function') updateEmptyState(); }
      $('dive-modal').classList.remove('show'); };
    const del=document.createElement('button'); del.className='mp-btn'; del.textContent='DEL';
    del.onclick=async()=>{ if(confirm('Delete '+d.id+'?')){ await STORE.diveDelete(d.id); _loadDives(); } };
    row.appendChild(meta); row.appendChild(rep); row.appendChild(del); box.appendChild(row);
  });
}

function initNavUI(){
  const ot=$('origin-tile'); if(ot) ot.addEventListener('click', openOriginModal);
  const add=$('map-add-area'); if(add) add.addEventListener('click', enterSelectMode);
  const areas=$('map-areas-btn'); if(areas) areas.addEventListener('click', openAreaManager);
  const cancel=$('as-cancel'); if(cancel) cancel.addEventListener('click', exitSelectMode);
  const dl=$('as-dl'); if(dl) dl.addEventListener('click', downloadSelected);
  const std=$('as-std'), high=$('as-high');
  if(std) std.addEventListener('click', ()=>{ _asDetail='standard'; std.classList.add('on'); high&&high.classList.remove('on'); });
  if(high) high.addEventListener('click', ()=>{ _asDetail='high'; high.classList.add('on'); std&&std.classList.remove('on'); });
  const si=$('map-search-in'); if(si) si.addEventListener('input', (e)=>mapSearch(e.target.value));
}

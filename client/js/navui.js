"use strict";
/* ============================================================================
   NAV UI — origin acquisition (§4) and offline area management (§5).
   Two modals built on demand, wired to the nav API. Opened from the ORIGIN
   status tile, the map's empty-state button, and CONFIG.
   ============================================================================ */

function _navFetch(path, opts){ return fetch((state.httpBase||'') + path, opts); }
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
    try{
      const r=await _navFetch('/api/geocode/search?q='+encodeURIComponent(q)); const j=await r.json();
      if(!j.results||!j.results.length){ res.innerHTML='<div class="msr-empty">no matches (needs internet)</div>'; return; }
      res.innerHTML='';
      j.results.forEach(it=>{ const d=document.createElement('div'); d.className='msr-row'; d.textContent=it.name;
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
    }catch(e){ res.innerHTML='<div class="msr-empty">search unavailable (offline)</div>'; }
  }, 350);
}
async function setOrigin(o){
  // heading0 comes from the SUB's IMU (the handheld has no magnetometer, §2), captured atomically here.
  o.heading_deg = Math.round((typeof MAP!=='undefined'?MAP.hdg:state.heading)||0);
  const msg=(t)=>{ const el=$('o-msg'); if(el) el.textContent=t; };
  try{
    let r = await _navFetch('/api/origin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});
    if(r.status===422){        // accuracy above the arm threshold — device WiFi fixes are coarse, so allow override
      if(confirm('Origin accuracy ±'+Math.round(o.accuracy)+' m exceeds the threshold. Set it anyway (refine by tapping the map)?')){
        r = await _navFetch('/api/origin?override=true',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});
      } else { msg('refused: accuracy too high'); return false; }
    }
    if(r.ok){ msg('origin set'); if(typeof refreshBootstrap==='function') await refreshBootstrap(); return true; }
    msg('set failed: '+r.status); return false;
  }catch(e){ msg('backend unreachable'); return false; }
}

/* ---- §2: auto-request the handheld's location on load ------------------------
   The origin fix comes from the topside ROG Ally's own browser. On load, if no
   origin is set, prompt for location automatically (don't wait for a dialog),
   centre the map on it, and — because WiFi positioning is coarse — offer
   tap-the-map refinement when the reported accuracy is poor. North still comes
   from the sub's IMU (captured in setOrigin), never the handheld. */
async function autoRequestOrigin(){
  if(!CONFIG.map.autoOrigin || state._fileSim) return;
  try{
    const r=await _navFetch('/api/origin'); const o=await r.json();
    if(!(o&&o.set===false) && o && typeof o.lat==='number'){ return; }   // already set this session — respect it
  }catch(e){ /* backend unreachable — still try the device fix */ }
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
  try{
    const r=await _navFetch('/api/areas'); const j=await r.json();
    if(!j.areas.length){ box.textContent='none yet — add one from the map'; return; }
    box.innerHTML='';
    j.areas.forEach(a=>{
      const row=document.createElement('div'); row.className='a-row';
      const thumb=document.createElement('img'); thumb.className='a-thumb';
      thumb.src=(state.httpBase||'')+'/api/areas/'+encodeURIComponent(a.name)+'/thumb';
      thumb.onerror=()=>{ thumb.style.visibility='hidden'; };
      const meta=document.createElement('div'); meta.style.flex='1'; meta.style.minWidth='0';
      meta.innerHTML=`<div class="a-name">${a.name}${a.active?' •':''}</div>`+
        `<div class="a-meta">${(a.size/1e6).toFixed(1)} MB · z≤${a.maxzoom??'?'} ${a.has_centreline?'· ⌇ waterway':''}</div>`;
      const act=document.createElement('button'); act.className='mp-btn'; act.textContent=a.active?'ACTIVE':'ACTIVATE';
      act.onclick=async()=>{ await _navFetch('/api/areas/'+a.name+'/activate',{method:'POST'}); _loadAreas(); if(typeof refreshBootstrap==='function') refreshBootstrap(); };
      const del=document.createElement('button'); del.className='mp-btn'; del.textContent='DEL';
      del.onclick=async()=>{ if(confirm('Delete area "'+a.name+'"? This removes the downloaded imagery.')){ await _navFetch('/api/areas/'+a.name,{method:'DELETE'}); _loadAreas(); if(typeof refreshBootstrap==='function') refreshBootstrap(); } };
      row.appendChild(thumb); row.appendChild(meta); row.appendChild(act); row.appendChild(del); box.appendChild(row);
    });
  }catch(e){ box.textContent='backend unreachable'; }
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
  const bbox=mapSelectionBBox(); if(!bbox){ const p=$('as-prog'); if(p)p.textContent='no imagery yet — wait for tiles to load'; return; }
  const prog=$('as-prog'); if(prog) prog.textContent='queued…';
  _watchAreaProgress();
  try{
    const r=await _navFetch('/api/areas',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({bbox, detail:_asDetail})});   // name auto-derived server-side (§4)
    const j=await r.json();
    if(r.ok){ if(prog) prog.textContent='done: '+j.name+' ('+((j.size||0)/1e6).toFixed(1)+' MB)';
      await _navFetch('/api/areas/'+encodeURIComponent(j.name)+'/activate',{method:'POST'});
      if(typeof refreshBootstrap==='function') refreshBootstrap(); setTimeout(exitSelectMode,1500); }
    else if(prog) prog.textContent='failed: '+(j.detail||r.status);
  }catch(e){ const p=$('as-prog'); if(p) p.textContent='backend unreachable'; }
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
    try{
      const r=await _navFetch('/api/geocode/search?q='+encodeURIComponent(q)); const j=await r.json();
      if(!j.results || !j.results.length){ res.innerHTML='<div class="msr-empty">no matches (needs internet)</div>'; return; }
      res.innerHTML='';
      j.results.forEach(it=>{ const d=document.createElement('div'); d.className='msr-row'; d.textContent=it.name;
        d.onclick=()=>{ if(typeof MAP!=='undefined'){ MAP.follow=false; MAP.viewLat=it.lat; MAP.viewLon=it.lon; } res.innerHTML=''; $('map-search-in').value=''; };
        res.appendChild(d); });
    }catch(e){ res.innerHTML='<div class="msr-empty">search unavailable (offline)</div>'; }
  }, 350);
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

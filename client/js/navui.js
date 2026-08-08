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
  // heading0 from the sub's IMU (§2) — or NOTHING, if nothing measured one.
  //
  // This used to read `Math.round((MAP.hdg || state.heading) || 0)`, which manufactured
  // a bearing twice over. `|| 0` turned a dead compass into due north; and once map.js
  // learned to HOLD the last angle rather than let `-null` become 0, MAP.hdg went from
  // null to a stale-but-plausible number, so the same line began posting a bearing the
  // sub had stopped measuring minutes earlier — as if it were current. The Pi wrote it
  // verbatim into the dive journal header, permanently, for a dive where nothing
  // pointed anywhere. Only a LIVE reading counts, and there being none is recordable.
  const live = (typeof MAP !== 'undefined' && MAP.hdgLive && typeof MAP.hdg === 'number')
             ? MAP.hdg
             : (typeof state.heading === 'number' ? state.heading : null);
  o.heading_deg = (live === null) ? null : Math.round(live);
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
  // 3) THE ORIGIN IS THE TRIGGER (see BOOTSTRAP FETCH below). This is the first
  // moment anything on this handheld knows WHERE it is going to be, which is the
  // one fact an offline area needs — so it is the moment to go and get one. Fired
  // before the Pi mirror on purpose: the download is client-owned and must not wait
  // on a Pi that may be switched off, and bootConsider() itself touches no network
  // until it has been told, by the launcher, that there is internet to touch.
  try{ if(typeof bootConsider==='function') bootConsider(null, o, 'the launch point was set'); }catch(e){}
  // 4) mirror to the Pi if it's up (dead reckoning). Best-effort; never blocks.
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
  let stored = null;
  try{
    if(typeof STORE!=='undefined'){ const o=await STORE.get('origin', null);
      if(o && typeof o.lat==='number'){ stored=o;
        if(typeof MAP!=='undefined'){ MAP.origin=o; MAP.hasOrigin=true; renderOriginTile&&renderOriginTile(); }
        _navFetch('/api/origin?override=true',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)}).catch(()=>{});
      } }
  }catch(e){}

  maybeStartLocationWatch();                       // keep the handheld's position live from here on

  if(!stored){ requestDeviceLocation(); return; }   // nothing saved - ask, as before

  // A stored origin renders immediately so the map works offline and instantly, but it
  // is only a starting assumption: carried to a new launch site it would plot the sub
  // relative to somewhere miles away. So ALWAYS try for a current fix on open.
  refreshOriginOnOpen(stored);
}

/* Take a fresh fix on every open and adopt it when it is genuinely better or genuinely
   elsewhere. Deliberately NOT gated on the permission already being granted - that gate
   made this dead code, because Chrome reports "prompt" even with an allow stored. The
   browser asks at most once per profile; a denial is remembered and fails instantly
   afterwards, so this cannot become a prompt on every launch.

   Two things it must not do:
     - downgrade precision. A map_tap origin is +/-8 m; a Wi-Fi fix is +/-58 m. Replacing
       the former with the latter at the same site makes the map worse, so it is kept.
     - move the frame silently when the operator has actually travelled. Beyond
       originMoveM the origin is a different place, and adopting it shifts every
       coordinate - that gets a confirm, not a silent swap. */
async function refreshOriginOnOpen(stored){
  if(!('geolocation' in navigator)) return;
  // ONLY when the permission is already granted, so opening the dashboard can never
  // put a prompt in the operator's face. Asking unprompted on every open is exactly
  // what made this intolerable: Chrome does not persist the grant on this handheld,
  // so every launch produced the dialog again.
  //
  // The manual path stays available and is the one that fixes this permanently: tap
  // the ORIGIN tile -> the request happens on a real user gesture -> choosing
  // "Allow while visiting the site" persists it -> from then on this runs silently on
  // every open, which is the automatic behaviour we actually want.
  try{
    if(!navigator.permissions) return;
    const st = await permState('geolocation');
    if(!st || st.state !== 'granted'){
      LOG.map('location not granted yet - tap the ORIGIN tile once to enable automatic refresh');
      markOriginNeedsPermission();
      return;
    }
  }catch(e){ return; }
  navigator.geolocation.getCurrentPosition(
    p=>{
      const lat=p.coords.latitude, lon=p.coords.longitude, acc=p.coords.accuracy;
      const rel = toLocal(lat, lon, stored.lat, stored.lon);
      const d   = Math.hypot(rel.x, rel.y);
      const limit = CONFIG.map.originMoveM || 150;
      const adopt = ()=> setOrigin({ lat, lon, accuracy:acc, source:'device', t:Date.now() })
        .then(ok=>{
          if(ok===false){ LOG.warn('could not store the refreshed origin'); return; }
          if(typeof MAP!=='undefined'){ if(typeof breakTrack==='function') breakTrack('origin re-set from a new fix'); MAP.x=0; MAP.y=0; }
          hideOriginPrompt();
          if(acc>(CONFIG.map.originRefineM||30)) offerRefine(acc);
        });

      if(d > limit){
        // Different site. Never silent - adopting this moves every coordinate.
        const km = d>=1000 ? (d/1000).toFixed(1)+' km' : Math.round(d)+' m';
        LOG.map('handheld is '+Math.round(d)+' m from the stored origin - offering to re-set');
        showOriginPrompt('ORIGIN IS ' + km + ' AWAY',
          'That origin was set somewhere else. Use where you are now, or keep it.',
          { label:'USE MY POSITION', run:adopt },
          { label:'KEEP', run:hideOriginPrompt });
        return;
      }
      // Same site: refresh, unless it would make the origin less accurate than it is.
      const storedAcc = (typeof stored.accuracy==='number') ? stored.accuracy : 1e9;
      if(acc > storedAcc + 5){
        LOG.map('keeping the existing origin: +/-'+Math.round(storedAcc)+' m beats the new +/-'+Math.round(acc)+' m');
        return;
      }
      LOG.map('origin refreshed from the current fix (+/-'+Math.round(acc)+' m, '+Math.round(d)+' m from the old one)');
      adopt();
    },
    err=>{ LOG.map('no fix on open ('+(err&&err.message||'?')+') - keeping the stored origin'); },
    {enableHighAccuracy:false, timeout:12000, maximumAge:30000}
  );
}
/* The origin CAN refresh itself on every open, but only once the browser permission is
   granted - and Chrome will only persist that from a real user gesture. Rather than
   nag with an unsolicited dialog, mark the ORIGIN tile so one tap sets it up. */
function markOriginNeedsPermission(){
  const el=$('origin-val'), tile=$('origin-tile');
  if(!el || !tile) return;
  tile.title = 'Tap to update the origin from your current position. '
             + 'Choose "Allow while visiting the site" and it will refresh automatically from then on.';
  tile.classList.add('needs-perm');
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
        // A new launch point is a jump, not travel — never join the traces across it.
        if(typeof MAP!=='undefined'){ if(typeof breakTrack==='function') breakTrack('launch point set from a device fix'); MAP.x=0; MAP.y=0; }
        // The permission was just granted on a real gesture, which is the one moment
        // Chrome will persist it — so start following from here without a reload.
        maybeStartLocationWatch();
        if(p.coords.accuracy>(CONFIG.map.originRefineM||30)) offerRefine(p.coords.accuracy);
        else hideOriginPrompt();
      });
    },
    err=>showOriginFallback(err),
    {enableHighAccuracy:true, timeout:15000, maximumAge:0}
  );
}
/* navigator.permissions.query can REJECT (and in some engines throw outright) for a
   name the browser will not answer for — headless Chrome does exactly that for
   geolocation, producing an unhandled rejection nobody sees until something is
   watching for them. One helper that always resolves, to null when it cannot tell. */
function permState(name){
  try{
    if(!navigator.permissions || !navigator.permissions.query) return Promise.resolve(null);
    return Promise.resolve(navigator.permissions.query({name})).catch(()=>null);
  }catch(e){ return Promise.resolve(null); }
}

/* ---- The handheld's own position, kept LIVE --------------------------------
   A fix taken once on load is wrong the moment the operator walks the bank looking
   for somewhere to put in — which is exactly when the reachable circle matters most.
   watchPosition keeps it current, like any other map application.

   Two things move, and they are NOT the same thing:

     MAP.me      where the handheld is now. Always live. It is the tether anchor,
                 because the cable is held by whoever is holding this.
     MAP.origin  the datum the sub is dead-reckoned FROM. Fixed during a dive.

   The origin can only follow while no track exists — before launch. Moving the datum
   mid-dive would shift every coordinate already plotted, so the sub would appear to
   jump sideways for no reason and the recorded track would be a lie. After launch the
   datum is frozen and the live marker simply diverges from it, which is the truth: you
   walked, the sub did not.

   Never prompts. watchPosition is only started once the permission is already granted,
   so this can never put a dialog in front of an operator mid-dive. */
let _geoWatch = null, _lastOriginWriteAt = 0;
function maybeStartLocationWatch(){
  if(_geoWatch !== null) return;
  if(!CONFIG.map.followMe || state._fileSim) return;
  if(!('geolocation' in navigator)){ LOG.warn('location: no geolocation API in this browser'); return; }
  if(!window.isSecureContext){
    LOG.warn('location: insecure origin ('+location.origin+') — browsers only expose geolocation '+
             'on https:// or localhost. Open Neptune from the launcher, or tap the map to set the origin.');
    return;
  }
  const start=()=>{
    if(_geoWatch !== null) return;
    _geoWatch = navigator.geolocation.watchPosition(onLiveFix,
      err=>LOG.map('live position watch: '+((err&&err.message)||'?')),
      { enableHighAccuracy:true, timeout:20000, maximumAge:2000 });
    LOG.map('live position watch started — the launch point follows the handheld until a dive begins');
  };
  if(!navigator.permissions){                   // cannot check → do not risk a prompt
    LOG.map('location: no Permissions API — automatic refresh disabled (tap ORIGIN to set it)');
    return;
  }
  permState('geolocation').then(st=>{
    if(!st){ LOG.map('location: permission state unavailable — tap ORIGIN to set it by hand'); return; }
    if(st.state==='granted') start();
    // If it is granted later (the operator taps ORIGIN once), pick it up without a reload.
    try{ st.onchange = ()=>{ if(st.state==='granted') start(); }; }catch(e){}
  });
}
function stopLocationWatch(){
  if(_geoWatch===null) return;
  try{ navigator.geolocation.clearWatch(_geoWatch); }catch(e){}
  _geoWatch=null;
}
function onLiveFix(p){
  if(typeof MAP==='undefined') return;
  const lat=p.coords.latitude, lon=p.coords.longitude, acc=p.coords.accuracy;
  MAP.meReal = { lat, lon, acc, t:Date.now() };    // the genuine fix, recorded either way

  // A mocked position is the operator deliberately standing somewhere else to plan.
  // Real fixes keep arriving underneath it (so clearing the mock lands on something
  // current) but must not overwrite it, and must not drag the launch point back.
  if(MAP.me && MAP.me.mock) return;

  MAP.me = { lat, lon, acc, t:MAP.meReal.t };

  if(!MAP.hasOrigin) return;                       // nothing to compare against yet
  if(typeof diveUnderway==='function' && diveUnderway()) return;   // DIVING: datum frozen, deliberately
  if(MAP.originTap) return;                        // operator is placing it by hand — don't fight them

  const rel = toLocal(lat, lon, MAP.origin.lat, MAP.origin.lon);
  const d   = Math.hypot(rel.x, rel.y);
  if(d < (CONFIG.map.meMinMoveM || 3)) return;                 // GPS jitter, not walking
  if(d > (CONFIG.map.originMoveM || 150)) return;              // a different site gets the explicit prompt
  const storedAcc = (typeof MAP.origin.accuracy==='number') ? MAP.origin.accuracy : 1e9;
  if(acc > storedAcc + 5) return;                              // never downgrade a good fix
  const now = Date.now();
  if(now - _lastOriginWriteAt < (CONFIG.map.meMinGapMs || 5000)) return;   // bound the writes
  _lastOriginWriteAt = now;

  // _override: this path is automatic, so it must never raise the accuracy confirm.
  setOrigin({ lat, lon, accuracy:acc, source:'device-live', t:now, _override:true })
    .then(ok=>{
      if(ok===false) return;
      // RE-BASE the frame instead of zeroing the sub. The operator moved; the ROV did
      // not. Zeroing would drag the sub along with whoever is holding the handheld,
      // which is exactly backwards — it is the growing gap between the two that is the
      // tether, and that gap is what the range readout is measuring.
      rebaseFrame(rel.x, rel.y);
      LOG.map('operator moved '+Math.round(d)+' m — launch point followed, ROV held at '
              +MAP.x.toFixed(1)+','+MAP.y.toFixed(1)+' m');
    });
}

/* Why is there no position? Answers it in one call instead of leaving the operator to
   guess between a browser permission, a Windows setting, and physics. */
function geoCheck(){
  const r={
    secureContext: window.isSecureContext,
    origin: location.origin,
    api: 'geolocation' in navigator,
    permissionsApi: !!navigator.permissions,
    watching: _geoWatch !== null,
    internet: (typeof STATUS!=='undefined') ? STATUS.internet : 'unknown',
    lastRealFix: (typeof MAP!=='undefined' && MAP.meReal) ? MAP.meReal : null,
    note: ''
  };
  // The ROG Ally has no GNSS. Without internet there is no positioning service to ask,
  // so no amount of permission-granting will produce a fix.
  if(r.internet === false)      r.note = 'No GPS receiver on this handheld and no internet on the tether — a fix is not obtainable. Tap the map to set the origin (more accurate anyway).';
  else if(!r.secureContext)     r.note = 'Insecure origin: browsers expose geolocation only on https:// or localhost.';
  else if(!r.api)               r.note = 'This browser exposes no geolocation API.';
  permState('geolocation').then(st=>LOG.map('location permission: '+(st? st.state : 'unavailable')));
  LOG.map('geoCheck', r);
  return r;
}

function offerRefine(accuracy){
  // non-blocking: the operator can see which bank they're on — a tap beats WiFi positioning (§2)
  showOriginPrompt('ORIGIN ±'+Math.round(accuracy)+' m (WiFi)',
    'Coarse fix. Tap the map on your launch point to refine.',
    { label:'TAP TO REFINE', run:armOriginTap });
}
/* A failed fix is nearly always one of three things, and "permission denied" alone
   sends the operator hunting in the wrong place. On the ROG Ally the usual cause is
   Windows itself: Settings > Privacy & security > Location > "Let desktop apps access
   your location". That is OFF by default, and with it off Chrome reports a denial no
   matter what the site permission says - there is no in-page way to fix it. */
function showOriginFallback(err){
  const code = err && err.code;
  let why;
  if(code === 1){                       // PERMISSION_DENIED
    why = 'Blocked. Windows: Settings › Privacy & security › Location — turn ON ' +
          '"Location services" AND "Let desktop apps access your location". ' +
          'Then allow location for this page in Chrome.';
  } else if(code === 2){                // POSITION_UNAVAILABLE
    // The usual cause on this handheld, and it is not fixable in software: the ROG Ally
    // has NO GNSS receiver. Chrome positions a device like this by sending nearby Wi-Fi
    // networks to Google's location service — which needs INTERNET. On a sealed tether
    // there is none, so there is no fix to be had, ever. Say that plainly instead of
    // "no signal", which sends the operator hunting for a satellite that isn't there.
    const offline = (typeof STATUS!=='undefined') && STATUS.internet === false;
    why = offline
      ? 'No fix is possible here. This handheld has no GPS receiver, so the browser ' +
        'locates it by looking up nearby Wi-Fi networks online — and the tether has no ' +
        'internet. Tap the map to set the origin; that is the accurate way anyway (±8 m ' +
        'against ±50 m from Wi-Fi).'
      : 'No fix available. Windows location services may be off, or there is nothing to ' +
        'position from. You can set the origin by tapping the map instead.';
  } else if(code === 3){                // TIMEOUT
    why = 'Timed out waiting for a fix. Indoors this is common — tap the map to set the origin.';
  } else {
    why = (err && err.message) || 'location unavailable';
  }
  LOG.warn('geolocation failed:', code, (err && err.message) || '');
  showOriginPrompt('LOCATION UNAVAILABLE', why,
    { label:'SET ON MAP', run:()=>{ hideOriginPrompt(); armOriginTap(); } });
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
/* Optional SECOND action. Some decisions must be declinable: offering to move the
   origin without a way to say "keep it" would push the operator into changing the
   local frame just to clear the message. */
function showOriginPrompt(title, sub, action, action2){
  let el=$('origin-prompt');
  if(!el){ el=document.createElement('div'); el.id='origin-prompt'; document.body.appendChild(el); }
  el.innerHTML = '<div class="op-title">'+title+'</div><div class="op-sub">'+sub+'</div>'+
                 (action ?'<button class="op-btn">'+action.label+'</button>':'')+
                 (action2?'<button class="op-btn op-btn2">'+action2.label+'</button>':'');
  if(action){  const b=el.querySelector('.op-btn');  if(b) b.onclick=action.run; }
  if(action2){ const b2=el.querySelector('.op-btn2'); if(b2) b2.onclick=action2.run; }
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

/* ============================================================================
   BOOTSTRAP FETCH — SETTING THE LAUNCH POINT IS ENOUGH.

   THE BUG THIS EXISTS FOR. The console opened on a map that said no chart data was
   downloaded, and that was TRUE and nobody's fault: nothing anywhere was automatic.
   No offline area existed, nothing created one, `crt-fetch` refused to run without
   an area name it could not be given, and the imagery had a SAVE OFFLINE flow that
   the operator had to go and find. Every piece worked; none of them started.

   The origin is the natural trigger because it is the first instant this system
   knows WHERE it is going to be, which is the only thing an area needs. So: set a
   launch point, and everything that CAN be downloaded starts downloading.

   THE TWO-PHASE MODEL IS UNTOUCHED. This is a BOOTSTRAP-time act. Nothing below
   runs on the serving path, nothing below is required for the map to draw, and the
   canal-side runtime still reads tiles out of the Cache API with no network, no DNS
   and no Pi. What this changes is only WHO presses the button, not what the button
   does.

   AND IT ONLY RUNS WHEN THERE IS ACTUALLY INTERNET. Not "try and see" — bootNet()
   asks the launcher's /__net, which is the one thing on this handheld that can tell
   a network apart from the internet, and falls back to the browser's own view.
   A console standing on a towpath with no signal spends no requests finding that
   out, because the answer is already in memory.
   ============================================================================ */

/* 1200 m RADIUS — a 2.4 km square around the launch point. A tethered ROV works one
   pound, not a county: 1.2 km reaches the lock at either end of most of them, and it
   is the size of the one hand-drawn area this repo has ever had. At z16-z18 that is
   ~970 tiles, ~19 MB and ~2.7 minutes at the polite rate below — finished before the
   tether is rigged. An area that takes longer to download than the dive takes to run
   is an area that gets abandoned half-finished, which is worse than none: the map
   then looks complete and is not. Matches NAV_AREA_RADIUS_M on the Pi so the two
   copies describe the same water. */
const BOOT_RADIUS_M   = 1200;
/* THE HARD CAP, and it is a cap on TILES rather than on metres because tiles are what
   the request count and the disk are actually spent on. An operator who taps a launch
   point must never silently start a download without a ceiling; if the radius above
   would exceed this the radius SHRINKS and the panel says it did. */
const BOOT_MAX_TILES  = 2000;
/* Above this estimate nothing starts by itself — the panel holds at TOO BIG and the
   size goes on the button, because somebody on a metered phone hotspot at the water's
   edge has to be able to decide before the bytes are spent, not after. */
const BOOT_ASK_MB     = 40;
/* POLITE. Esri's imagery is a free public service. Sequential, one request at a time,
   six a second — the same number api/nav/config.py's sat_rate_per_s uses for the Pi's
   half, because it is the same server being asked by the same system. */
const BOOT_RATE_PER_S = 6;
/* A launch point this far INSIDE an existing area's edge is already covered, and
   re-tapping the same spot or walking 50 m along the towpath must not make a second
   area or re-download a single tile. Closer to the edge than this and the map runs
   out just as the sub gets going, so that case is treated as uncovered. */
const BOOT_REUSE_M    = 250;
const BOOT_DETAIL     = 'standard';        // z16-z18, CONFIG.map.detailZooms
const BOOT_TILE_KB    = 20;                // ~20 KB/JPEG — the same figure the backend estimates with
const BOOT_PI_LOOK_MS = 20000;             // how often the Pi's half is re-read (never a network poll with no Pi)

/* Per-source words. Deliberately the SAME vocabulary the layer rows use — DOWNLOADING
   is a state distinct from absent and from present, CANNOT TELL still means the
   console does not know, and NOT DOWNLOADED is still a quiet fact about this handheld
   rather than a fault. A second vocabulary for the same distinctions would mean the
   operator has to learn which panel they are reading before they can read it. */
const BOOT_WORDS = {
  waiting:        'WAITING',
  running:        'DOWNLOADING',
  done:           'DOWNLOADED',
  held:           'ALREADY HELD',
  failed:         'FAILED',
  stopped:        'STOPPED',
  'no-pi':        'NO PI',
  'not-automatic':'NOT AUTOMATIC',
  'not-downloaded':'NOT DOWNLOADED',
  unknown:        'CANNOT TELL',
};
const BOOT_TOP_WORDS = {
  idle:            'NOTHING TO DO',
  'no-origin':     'NO LAUNCH POINT',
  covered:         'ALREADY DOWNLOADED',
  offline:         'NO CONNECTION',
  unknown:         'CANNOT TELL',
  off:             'AUTO OFF',
  holding:         'TOO BIG — CONFIRM',
  running:         'DOWNLOADING',
  done:            'DOWNLOADED',
  partial:         'PARTLY DOWNLOADED',
  failed:          'FAILED',
  stopped:         'STOPPED',
};

const BOOTFETCH = {
  auto:true, _autoP:null,        // automatic start; persisted, because switching it off is a decision
  state:'idle', why:'',
  area:null, plan:null,          // the area being filled, and its bbox/tiles/MB
  coveredBy:null,                // the already-downloaded area this launch point sits inside, if any
  running:false, abort:false,
  startedAt:0, endedAt:0,
  _piAt:0,                       // when the Pi's half was last read
  /* THREE SOURCES, AND THE PANEL NAMES WHICH ONE IS BEING FETCHED. `drives` is the
     honest half: this console downloads its own imagery and only WATCHES the Pi's
     two, because the Pi's card is filled by the Pi and the Trust layers are fetched
     by a command on it. A row that reported the Pi's work as if this handheld were
     doing it would be a console taking credit for a download it cannot start. */
  jobs: {
    imagery: {id:'imagery', name:'SATELLITE IMAGERY', where:'this handheld', drives:true,
              state:'waiting', done:0, total:0, held:0, got:0, missed:0, bytes:0, why:'', at:0},
    pi:      {id:'pi',      name:'IMAGERY ON THE PI', where:"the Pi's card", drives:false,
              state:'waiting', done:0, total:0, held:0, got:0, missed:0, bytes:0, why:'', at:0},
    charts:  {id:'charts',  name:'CHART LAYERS',      where:"the Pi's card", drives:false,
              state:'waiting', done:0, total:0, held:0, got:0, missed:0, bytes:0, why:'', at:0},
  },
  order: ['imagery','pi','charts'],
};

function bootSleep(ms){ return new Promise(r=>setTimeout(r, ms)); }
function bootMB(n){ return n<10 ? n.toFixed(1) : String(Math.round(n)); }
function bootPct(j){ return (j.total>0) ? Math.min(100, Math.round(j.done*100/j.total)) : 0; }

/* ---- IS THERE INTERNET? Asked of the two things that already know. -----------

   NOT a third notion, and emphatically not a probe. status.js already polls the
   launcher's /__net into state.net, and the launcher is the only party here that can
   tell "joined to a network" from "joined to the internet" — Windows answers that
   with IPv4Connectivity and a browser cannot see it at all. navigator.onLine (which
   STATUS.internet carries) is the fallback and is strictly weaker: false is a real
   answer, true only means a cable or a radio is up.

   THREE ANSWERS, NOT TWO. "Cannot tell" is its own outcome and it does NOT start a
   download: the dashboard opened from the Pi or from disk has no launcher on its
   origin, and guessing there would put "try and see" back on a console whose whole
   design rule is that it never does that. It offers the manual path instead. */
function bootNet(){
  const n = (typeof state!=='undefined' && state.net) ? state.net : null;
  if(n){
    const w = n.wifi || {}, e = n.eth || {};
    if(w.internet === true)
      return {v:'yes', why:'the launcher reports the Wi-Fi network "'+(w.ssid||'(unnamed)')+'" has internet'};
    if(String(e.ipv4||'') === 'Internet')
      return {v:'yes', why:'the launcher reports the cable on '+(e.name||'this handheld')+' has internet'};
    return {v:'no', why: w.up
      ? ('the launcher reports this handheld is joined to "'+(w.ssid||'a network')+'" and that network has no internet')
      : 'the launcher reports no network on this handheld with internet on it'};
  }
  if(typeof STATUS!=='undefined' && STATUS.internet === false)
    return {v:'no', why:'this browser reports itself offline'};
  return {v:'unknown', why:'the launcher is the only thing here that can tell a network apart from the '
                         + 'internet, and it is not serving this page — so nothing on this console knows '
                         + 'whether there is a connection to download over'};
}

/* Can the Pi be asked something RIGHT NOW? Not crtLinkIsReal(), which answers the
   different and equally necessary question "has a vehicle ever been on this link" —
   that one licenses an alarm, this one licenses a request. */
function bootPiOnLink(){
  if(typeof state==='undefined' || !state) return false;
  if(state.demo) return false;
  if(state.wsStatus === 'online') return true;
  return !!(state.piProbe && state.piProbe.ok);
}

/* ---- the plan: a bounded box round the launch point ---------------------- */
function bootBBox(lat, lon, radiusM){
  const dLat = radiusM / 111320;
  const dLon = radiusM / (111320 * Math.max(0.15, Math.cos(lat*Math.PI/180)));
  return [lon-dLon, lat-dLat, lon+dLon, lat+dLat];
}
/* Does this saved area already cover the launch point, with room to work in? */
function bootCovers(a, origin, marginM){
  if(!a || !a.bbox || a.bbox.length!==4 || !origin || typeof origin.lat!=='number') return false;
  const mLat = marginM / 111320;
  const mLon = marginM / (111320 * Math.max(0.15, Math.cos(origin.lat*Math.PI/180)));
  return origin.lon >= a.bbox[0]+mLon && origin.lon <= a.bbox[2]-mLon
      && origin.lat >= a.bbox[1]+mLat && origin.lat <= a.bbox[3]-mLat;
}
/* THE PLAN — the box, the tile count, and what it will cost.

   RESUMING IS PLANNING THE BOX THAT ALREADY EXISTS, not a fresh box with an old name.
   That distinction is load-bearing: bootRunImagery writes the plan's bbox into the area
   record, so planning a 1.2 km square and saving it under the name of a 5 km area the
   operator drew by hand REPLACES that area's record with a smaller one — and the
   record is the only account of what is on this disk. The tiles would still be in the
   cache, unreachable, while the panel reported a complete small area over water the
   handheld actually holds. So a resume takes the saved box, the saved zooms, and its
   own count; only a genuinely new launch point gets a new square. */
function bootPlan(origin, resume){
  if(resume && resume.bbox && resume.bbox.length===4){
    const zmin = (typeof resume.zmin==='number') ? resume.zmin : 16;
    const zmax = (typeof resume.zmax==='number') ? resume.zmax : 18;
    let n = 0;
    try{ n = countTilesBBox(resume.bbox, zmin, zmax); }catch(e){ n = resume.tiles||0; }
    const held = Math.max(0, Math.min(resume.cached||0, n));
    return {bbox:resume.bbox.slice(),
            radiusM:Math.round((resume.bbox[3]-resume.bbox[1])*111320/2),
            zmin, zmax, tiles:n,
            // WHAT IT WILL COST FROM HERE, not what the whole box cost once. A resume
            // that quoted the full size would send somebody on a hotspot looking for a
            // way to avoid re-downloading tiles that are not going to be downloaded.
            mb:Math.max(0, n-held)*BOOT_TILE_KB/1024,
            shrunk:false, resuming:resume.name, held};
  }
  const zr = (CONFIG.map.detailZooms||{})[BOOT_DETAIL] || [16,18];
  let r = BOOT_RADIUS_M, bbox = bootBBox(origin.lat, origin.lon, r), n = 0, shrunk = false;
  const count = ()=>{ try{ return countTilesBBox(bbox, zr[0], zr[1]); }catch(e){ return 0; } };
  n = count();
  // THE CAP BITES BY SHRINKING, not by refusing. A refusal at the water's edge leaves
  // the operator with no map at all; a smaller box leaves them with the water they
  // are standing next to, and the panel says it was made smaller and by how much.
  while(n > BOOT_MAX_TILES && r > 150){
    r = Math.round(r*0.75); bbox = bootBBox(origin.lat, origin.lon, r); n = count(); shrunk = true;
  }
  return {bbox, radiusM:r, zmin:zr[0], zmax:zr[1], tiles:n, mb:n*BOOT_TILE_KB/1024, shrunk,
          resuming:null, held:0};
}
/* The saved area this launch point already sits inside, biggest first. Local storage
   only — no Pi, no network — so it costs nothing to ask before every run. */
async function bootExistingArea(origin, marginM){
  if(!origin || typeof origin.lat!=='number') return null;
  try{
    const list = (typeof STORE!=='undefined' && STORE.areas) ? await STORE.areas() : [];
    return (list||[]).filter(a=>bootCovers(a, origin, marginM||0))
                     .sort((a,b)=>(b.tiles||0)-(a.tiles||0))[0] || null;
  }catch(e){ return null; }
}

/* ---- the automatic switch (remembered — switching it off is a decision) --- */
function bootAutoReady(){
  if(!BOOTFETCH._autoP) BOOTFETCH._autoP = (async()=>{
    try{ if(typeof STORE!=='undefined' && STORE.get){
      const v = await STORE.get('boot.auto', null);
      if(typeof v==='boolean') BOOTFETCH.auto = v;
    } }catch(e){}
    return BOOTFETCH.auto;
  })();
  return BOOTFETCH._autoP;
}
function bootSetAuto(on){
  BOOTFETCH.auto = !!on;
  try{ if(typeof STORE!=='undefined' && STORE.set) STORE.set('boot.auto', BOOTFETCH.auto); }catch(e){}
  LOG.map('offline data: automatic download '+(BOOTFETCH.auto?'ON':'OFF')
        + (BOOTFETCH.auto ? '' : ' — nothing will be downloaded until DOWNLOAD NOW is pressed'));
  if(BOOTFETCH.auto) bootConsider(null, (typeof MAP!=='undefined') ? MAP.origin : null, 'automatic download switched back on');
  else bootRender();
}

/* ---- state plumbing ------------------------------------------------------
   bootTop RETURNS WHETHER IT ACTUALLY CHANGED, and that is not a nicety. The settled
   states are recomputed on the map's 5 s bootstrap tick, so a summary that logged
   every time it was recomputed would write the same sentence into the log twelve
   times a minute and bury the line that says something happened. */
function bootTop(st, why){
  if(BOOTFETCH.state===st && BOOTFETCH.why===why) return false;
  BOOTFETCH.state = st; BOOTFETCH.why = why || '';
  bootRender();
  return true;
}
function bootJob(id, st, patch){
  const j = BOOTFETCH.jobs[id]; if(!j) return;
  if(st) j.state = st;
  if(patch) Object.keys(patch).forEach(k=>{ j[k] = patch[k]; });
  j.at = Date.now();
  bootRender();
}
function bootRender(){
  // The panel is crt.js's — the layer states already live there and this belongs
  // beside them, not on a surface of its own.
  if(typeof crtRenderFetch==='function') crtRenderFetch();
}

/* ---- THE TRIGGER --------------------------------------------------------
   Called from setOrigin (immediately, so a tapped launch point acts at once) and
   from map.js's refreshBootstrap every 5 s (so a connection that arrives LATER is
   also a trigger). Cheap by construction: everything it consults before deciding is
   already in memory or in IndexedDB, so a canal-side console with no signal reaches
   the "no connection" branch without spending a single request. */
async function bootConsider(areas, origin, why){
  try{
    await bootAutoReady();
    if(BOOTFETCH.running) return;
    if(typeof state!=='undefined' && (state.demo || state._fileSim)){
      bootTop('idle', 'this console is running the simulator, so there is no launch point to '
                    + 'download for and nothing is fetched');
      return;
    }
    const o = origin || ((typeof MAP!=='undefined') ? MAP.origin : null);
    if(!o || typeof o.lat!=='number'){
      bootTop('no-origin', 'no launch point is set yet. Setting one is what starts this: it is the '
                         + 'first moment this console knows where it is going to be, which is the only '
                         + 'thing an offline area needs.');
      return;
    }
    let list = areas;
    if(!list){ try{ list = (typeof STORE!=='undefined') ? await STORE.areas() : []; }catch(e){ list = []; } }

    // ALREADY HELD IS THE COMMONEST ANSWER AND IT COSTS NOTHING. Re-tapping the same
    // launch point, or walking the bank inside a box that is already downloaded, must
    // not re-download one tile — of anybody's free public imagery, over anybody's
    // hotspot.
    const covering = (list||[]).filter(a=>bootCovers(a, o, BOOT_REUSE_M))
                               .sort((a,b)=>(b.tiles||0)-(a.tiles||0))[0] || null;
    const done = !!(covering && (covering.tiles||0) > 0 && (covering.cached||0) >= (covering.tiles||0));
    BOOTFETCH.coveredBy = done ? covering.name : null;

    // THE SIZE IS WORKED OUT THE MOMENT THERE IS A LAUNCH POINT, before the network is
    // consulted at all — it is arithmetic over a tile grid and touches nothing. It has
    // to be, because the size belongs ON THE BUTTON in every state where the OPERATOR
    // is the one deciding: no connection now and a hotspot in ten minutes is the normal
    // shape of this, and a plan computed only on the branch that auto-starts left the
    // button reading "DOWNLOAD NOW" with no number on it in exactly those states.
    //
    // Planned against the covering area whenever there is one — including the fully
    // downloaded case — so the number on the button is what pressing it would ACTUALLY
    // cost. bootStart resolves the same record the same way, so the two can never quote
    // different boxes.
    BOOTFETCH.plan = bootPlan(o, covering);

    if(done){
      BOOTFETCH.area = covering.name;
      bootJob('imagery','held',{done:covering.cached||0, total:covering.tiles||0, held:covering.cached||0,
        why:'all '+(covering.tiles||0)+' tiles of "'+covering.name+'" are already on this handheld and this '
          + 'launch point is inside it, so nothing was re-downloaded'});
      // SUMMARISED, NOT ANNOUNCED. "ALREADY DOWNLOADED — nothing needs fetching" is
      // only true of the imagery; it says nothing about the hazard charts, and a top
      // line that called the whole block finished while the Trust layers were missing
      // is the map-looks-complete lie in the one place built to catch it. bootFinish
      // reads every row and picks the sentence that is true of all three.
      await bootLookAtPi(false);
      bootFinish();
      return;
    }

    const net = bootNet();
    if(net.v==='no'){
      // NOT AN ERROR. At the canal this is the normal condition and it is said the way
      // the layer panel says NOT DOWNLOADED: quietly, because nothing has failed.
      bootTop('offline', 'no connection — nothing new can be downloaded here ('+net.why+'). This is '
                       + 'normal at the water and it is not a fault. The map draws whatever was '
                       + 'downloaded before you came; press DOWNLOAD NOW once you are back on a '
                       + 'network, or use ＋ AREA to pick a box by hand.');
      bootLookAtPi(false);
      return;
    }
    if(net.v==='unknown'){
      bootTop('unknown', net.why + '. Nothing is started on a guess. If you know there is a '
                       + 'connection, press DOWNLOAD NOW and it will run.');
      bootLookAtPi(false);
      return;
    }
    if(!BOOTFETCH.auto){
      bootTop('off', 'automatic downloading is switched off on this handheld, so nothing starts by '
                   + 'itself. This launch point needs about '+bootMB(BOOTFETCH.plan.mb)+' MB — press '
                   + 'DOWNLOAD NOW to fetch it, or AUTO to turn the automatic path back on.');
      bootLookAtPi(false);
      return;
    }
    if(BOOTFETCH.plan.mb > BOOT_ASK_MB){
      // SIZE BEFORE COMMITTING. Above the threshold this waits to be told, with the
      // number on the button, because the operator may be on a metered hotspot.
      bootTop('holding', 'this launch point needs about '+bootMB(BOOTFETCH.plan.mb)+' MB ('
                       + BOOTFETCH.plan.tiles.toLocaleString()+' tiles'
                       + (BOOTFETCH.plan.resuming ? (' still missing from "'+BOOTFETCH.plan.resuming+'"') : '')
                       + '), which is more than the '+BOOT_ASK_MB+' MB this console will start on its '
                       + 'own. Press the button to download it.');
      bootLookAtPi(false);
      return;
    }
    bootStart(why || 'a launch point is set and this handheld has internet', covering);
  }catch(e){
    LOG.warn('offline data: the automatic check failed (' + ((e&&e.message)||e) + ')');
  }
}

/* ---- THE RUN ------------------------------------------------------------- */
async function bootStart(why, resume){
  if(BOOTFETCH.running) return;
  const o = (typeof MAP!=='undefined') ? MAP.origin : null;
  if(!o || typeof o.lat!=='number'){
    bootTop('no-origin', 'there is no launch point to download for yet — set one first.');
    return;
  }
  await bootAutoReady();
  // WHICH BOX. Pressing DOWNLOAD NOW by hand must land in the SAME area record a
  // stopped or failed run left behind, or the panel grows a second area over the same
  // water and the operator has to guess which of the two `crt-fetch` was pointed at.
  // The lookup is local storage only, so it costs nothing.
  const held = resume || await bootExistingArea(o, 0);
  const plan = BOOTFETCH.plan = bootPlan(o, held);
  BOOTFETCH.coveredBy = null;
  BOOTFETCH.running = true; BOOTFETCH.abort = false;
  BOOTFETCH.startedAt = Date.now(); BOOTFETCH.endedAt = 0;
  // SAY HOW BIG BEFORE THE FIRST BYTE, in the panel and in the log, whether or not
  // the size crossed the confirm threshold. A number that only appears once the
  // download is finished is not a warning, it is a receipt.
  bootTop('running', 'downloading about '+bootMB(plan.mb)+' MB ('
                   + (plan.resuming
                      ? (Math.max(0, plan.tiles-(plan.held||0)).toLocaleString()+' tiles still missing from '
                         + 'the area "'+plan.resuming+'", which already holds '+(plan.held||0).toLocaleString()
                         + ' of '+plan.tiles.toLocaleString())
                      : (plan.tiles.toLocaleString()+' tiles'))
                   + ', z'+plan.zmin+'-'+plan.zmax+') for a '+Math.round(plan.radiusM*2)+' m square around '
                   + 'the launch point — '+why+'. The console stays flyable throughout and STOP ends it '
                   + 'at any point.');
  LOG.map('offline data: starting — '+plan.tiles+' tiles, ~'+bootMB(plan.mb)+' MB, '
        + Math.round(plan.radiusM*2)+' m square ('+why+')'
        + (plan.resuming ? ' [resuming the saved area "'+plan.resuming+'"; '+(plan.held||0)+' tiles already here]' : '')
        + (plan.shrunk ? ' [box shrunk to stay under the '+BOOT_MAX_TILES+'-tile cap]' : ''));
  try{
    const name = held ? held.name : await bootName(plan.bbox, o);
    BOOTFETCH.area = name;
    await bootRunImagery(name, plan, o, held);
  }catch(e){
    bootJob('imagery','failed',{why:'the download stopped with an error the console could not place ('
                                  + ((e&&e.message)||e) + ')'});
  }finally{
    BOOTFETCH.running = false;
    BOOTFETCH.endedAt = Date.now();
  }
  await bootLookAtPi(true);
  bootFinish();
}
function bootStop(why){
  if(!BOOTFETCH.running){ bootRender(); return; }
  BOOTFETCH.abort = true;
  LOG.map('offline data: STOP — '+(why||'the operator stopped the download')
        + '. Whatever landed is kept and the next run resumes from it.');
}

/* THE NAME. The Pi's own name for this water wins when there is one, because two
   copies of one area under two names is two areas — and `crt-fetch <area>` would
   then be run against whichever one the operator happened to read off the screen.

   AND IT IS NEVER WORTH A STALL. _revGeocode goes straight out to Nominatim, a free
   public service that is sometimes slow and carries no timeout of its own — while the
   panel has ALREADY said DOWNLOADING, because this runs after the state is set. A
   reverse lookup that takes half a minute is half a minute of a progress bar sitting
   at 0% with nothing whatsoever wrong, which is indistinguishable from the stalled
   fetch this surface exists to make visible. The box is the thing; the name is a
   label on it, and coordinates are a perfectly good label. */
const BOOT_NAME_MS = 6000;
async function bootName(bbox, origin){
  const pi = await bootPiArea(origin);
  if(pi && pi.name) return pi.name;
  let n = null;
  try{
    n = await Promise.race([
      _revGeocode((bbox[1]+bbox[3])/2, (bbox[0]+bbox[2])/2),
      new Promise(r=>setTimeout(()=>r(null), BOOT_NAME_MS)),
    ]);
  }catch(e){}
  n = n || (((bbox[1]+bbox[3])/2).toFixed(4)+'_'+((bbox[0]+bbox[2])/2).toFixed(4));
  return n.replace(/[^\w-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,48) || 'launch-point';
}

/* SATELLITE IMAGERY -> THIS HANDHELD. The only source this console downloads itself,
   and the one the map actually draws from: tiles.js always asks for the provider's
   real URL and the service worker serves it cache-first, so the Pi is never in this
   path and neither is a network once these are here.

   SEQUENTIAL, RATE-LIMITED, RESUMABLE, AND IT NEVER RE-FETCHES. A tile already in the
   cache costs one lookup and no request and no delay, which is what makes a second run
   over a mostly-downloaded area finish in seconds instead of re-spending somebody's
   free quota. */
async function bootRunImagery(name, plan, origin, existing){
  const j = BOOTFETCH.jobs.imagery;
  const urls = (typeof STORE!=='undefined' && STORE.tileUrlsForBBox)
             ? STORE.tileUrlsForBBox(plan.bbox, plan.zmin, plan.zmax) : [];
  if(!urls.length){
    bootJob('imagery','failed',{done:0,total:0,why:'no imagery provider is configured on this console '
      + '(CONFIG.map.tileProviders), so there is no URL to download tiles from'});
    return;
  }
  // THE AREA IS REGISTERED BEFORE THE FIRST TILE, on purpose. The map then comes
  // alive as the tiles land, which is what "see it happening" means — and the record
  // carries state:'downloading' so that a run killed by a closed lid is not left
  // looking like a finished area next time. An area that looks complete and is not is
  // the exact failure this whole surface exists to prevent.
  // WHAT THE OLD RECORD KNEW IS KEPT. `mirrored` is the only note anywhere that this
  // box also exists on the Pi's card, and `detail` is the zoom range the operator
  // chose by hand — rewriting either of them to this path's defaults would make the
  // area manager report a hand-picked, already-mirrored area as a plain automatic one.
  const meta = {name, bbox:plan.bbox, zmin:plan.zmin, zmax:plan.zmax, tiles:urls.length,
                cached:Math.min(plan.held||0, urls.length),
                detail:(existing && existing.detail) || BOOT_DETAIL, savedAt:Date.now(),
                mirrored:!!(existing && existing.mirrored),
                auto:true, state:'downloading', originLat:origin.lat, originLon:origin.lon};
  try{ if(typeof STORE!=='undefined') await STORE.areaPut(meta); }catch(e){}
  if(typeof MAP!=='undefined'){
    // Nothing covered this launch point — that is why we are here — so making the new
    // area the active one is not overriding a choice, it is making one where there was
    // none. Without it the map keeps drawing the LAST canal over this one's water.
    MAP.activeArea = name; MAP.hasArea = true;
    if(typeof updateEmptyState==='function') updateEmptyState();
  }
  bootJob('imagery','running',{done:0,total:urls.length,held:0,got:0,missed:0,bytes:0,
    why:'downloading '+urls.length.toLocaleString()+' tiles into this handheld\'s offline archive'});

  if(typeof caches==='undefined' || !window.isSecureContext){
    // Cache API is https-only. The area record is still written (so the box and its
    // size are remembered) but NOTHING has been stored, and saying "downloaded" here
    // would be the map-looks-complete lie in its purest form.
    meta.state='failed'; try{ await STORE.areaPut(meta); }catch(e){}
    bootJob('imagery','failed',{done:0,total:urls.length,why:'this page is not in a secure context ('
      + location.origin+'), and browsers only allow the offline tile store on https:// or localhost. '
      + 'Nothing was saved. Open Neptune from the launcher and it will work.'});
    return;
  }
  let cache=null;
  try{ cache = await caches.open(STORE.TILE_CACHE); }
  catch(e){
    meta.state='failed'; try{ await STORE.areaPut(meta); }catch(e2){}
    bootJob('imagery','failed',{done:0,total:urls.length,
      why:'the browser refused to open the offline tile store ('+((e&&e.message)||e)+'), so nothing '
        + 'could be saved. Check that storage is not full or blocked for this site.'});
    return;
  }
  const gap = Math.round(1000/BOOT_RATE_PER_S);
  let held=0, got=0, missed=0, bytes=0, lastErr='';
  for(let i=0;i<urls.length;i++){
    if(BOOTFETCH.abort){
      meta.cached = held+got; meta.state='stopped';
      try{ await STORE.areaPut(meta); }catch(e){}
      bootJob('imagery','stopped',{done:i, total:urls.length, held, got, missed, bytes,
        why:'stopped by the operator after '+(held+got)+' of '+urls.length+' tiles. What landed is '
          + 'kept and the next run picks up from there — no tile is downloaded twice.'});
      return;
    }
    const u = urls[i];
    let have=false; try{ have = !!(await cache.match(u)); }catch(e){}
    if(have){ held++; }
    else{
      let stored=false;
      try{
        // A CORS read first, because an opaque response cannot be checked: mode
        // 'no-cors' hands back status 0 for a 404 and for a 200 alike, so a cache
        // full of error pages would report as a complete download. Esri answers CORS;
        // the opaque path is the fallback for a provider that does not, and the row
        // says which one was used.
        const r = await fetch(u, {cache:'no-store'});
        if(r && r.ok){
          const b = await r.blob(); bytes += b.size;
          await cache.put(u, new Response(b, {status:200,
            headers:{'Content-Type': (r.headers && r.headers.get('content-type')) || 'image/jpeg'}}));
          stored = true;
        } else if(r){ lastErr = 'the imagery server answered '+r.status; }
      }catch(e){
        try{
          const r2 = await fetch(u, {mode:'no-cors'});
          await cache.put(u, r2); stored = true;
        }catch(e2){ lastErr = (e2 && e2.message) || (e && e.message) || 'the request did not complete'; }
      }
      if(stored) got++; else missed++;
      await bootSleep(gap);          // POLITE — paid only on a real request, never on a cache hit
    }
    if(i % 10 === 0 || i === urls.length-1){
      meta.cached = held+got;
      try{ await STORE.areaPut(meta); }catch(e){}
      bootJob('imagery','running',{done:i+1, total:urls.length, held, got, missed, bytes,
        why:'downloading '+urls.length.toLocaleString()+' tiles into this handheld\'s offline archive'
          + (held ? (' — '+held.toLocaleString()+' of them were already here and were not re-fetched') : '')});
    }
  }
  meta.cached = held+got; meta.state = missed ? 'failed' : 'present';
  try{ await STORE.areaPut(meta); }catch(e){}
  const kept = held+got;
  if(missed && kept === 0){
    bootJob('imagery','failed',{done:urls.length,total:urls.length,held,got,missed,bytes,
      why:'not one of the '+urls.length.toLocaleString()+' tiles could be downloaded — '+(lastErr||'every '
        + 'request failed')+'. The map has no imagery for this launch point.'});
  } else if(missed){
    // PART OF A MAP IS NOT A MAP, and this is exactly the half-finished fetch that
    // makes an incomplete picture look complete. It is a FAILURE with a count, not a
    // success with an asterisk.
    bootJob('imagery','failed',{done:urls.length,total:urls.length,held,got,missed,bytes,
      why:missed.toLocaleString()+' of '+urls.length.toLocaleString()+' tiles did not download ('
        + (lastErr||'the requests failed')+'), so this area has holes in it. Blank squares on the map '
        + 'are MISSING IMAGERY, not open water. Press DOWNLOAD NOW on a better connection and only the '
        + 'missing tiles are re-requested.'});
  } else {
    bootJob('imagery','done',{done:urls.length,total:urls.length,held,got,missed,bytes,
      why:'all '+urls.length.toLocaleString()+' tiles are on this handheld'
        + (held ? (', '+held.toLocaleString()+' of which were already here') : '')
        + (bytes ? (' — '+bootMB(bytes/1048576)+' MB fetched this run') : '')
        + '. The map works from here with no network and no Pi.'});
    LOG.map('offline data: imagery complete for "'+name+'" ('+got+' fetched, '+held+' already held)');
  }
}

/* ---- THE PI'S HALF, WATCHED AND NOT DRIVEN -------------------------------
   The Pi fills its own card and the Trust layers are fetched by a command on it, so
   this console reports what it finds and says plainly what it cannot start. Two
   things it will not do: claim the Pi's download as its own, and poll a link with no
   Pi on the end of it — bootPiOnLink() is an in-memory read and the guard below it
   means a console at the canal never spends a request discovering there is nobody
   there. */
async function bootGet(path, ms){
  let ctl=null, timer=null;
  try{ ctl = (typeof AbortController!=='undefined') ? new AbortController() : null; }catch(e){}
  if(ctl) timer = setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, ms||8000);
  try{
    const r = await _navFetch(path, ctl ? {signal:ctl.signal, cache:'no-store'} : {cache:'no-store'});
    if(timer) clearTimeout(timer);
    let j=null; try{ j = await r.json(); }catch(e){}
    return {ok:r.ok, status:r.status, json:j};
  }catch(e){
    if(timer) clearTimeout(timer);
    return {ok:false, status:0, err:(e && e.name==='AbortError') ? 'the Pi did not answer in time'
                                                                : ((e&&e.message)||'the request never reached the Pi')};
  }
}
async function bootPiArea(origin){
  if(!bootPiOnLink() || !origin) return null;
  const r = await bootGet('/api/areas', 8000);
  if(!r.ok) return null;
  const list = (r.json && (r.json.areas || r.json)) || [];
  if(!Array.isArray(list)) return null;
  return list.find(a=>a && a.name===BOOTFETCH.area)
      || list.find(a=>bootCovers(a, origin, 0))
      || null;
}
async function bootLookAtPi(force){
  const now = Date.now();
  if(!force && now - BOOTFETCH._piAt < BOOT_PI_LOOK_MS) return;
  BOOTFETCH._piAt = now;
  const o = (typeof MAP!=='undefined') ? MAP.origin : null;
  if(!bootPiOnLink()){
    const s = 'there is no Pi answering on this link, so nothing on its card can be read or filled. '
            + 'This handheld\'s own copy above is what the map draws from and it does not need one.';
    bootJob('pi','no-pi',{done:0,total:0,why:s});
    bootJob('charts','no-pi',{done:0,total:0,why:s+' The Canal & River Trust hazard layers live on the '
            + 'Pi, so they cannot be fetched or checked until it is on the tether.'});
    return;
  }
  const a = await bootPiArea(o);
  if(!a){
    bootJob('pi','not-downloaded',{done:0,total:0,
      why:'the Pi is answering and holds no offline area covering this launch point. Its card is a '
        + 'SECOND copy — the map above does not need it — but it is also what gives the hazard fetch '
        + 'an area to clip to, which is why the row below cannot run without it.'});
  } else {
    // The Pi's own vocabulary if this build publishes one; otherwise fall back to
    // what /api/areas has always carried. `present` alone is not enough: an MBTiles
    // file exists from the FIRST tile onwards, so a build with no state field can
    // only be reported as "the Pi has a file", which is what this says.
    const st = String(a.state||'');
    const mb = a.size ? (a.size/1048576) : 0;
    const done = (typeof a.done==='number') ? a.done : null;
    const total = (typeof a.total==='number') ? a.total : (typeof a.tiles==='number' ? a.tiles : null);
    if(st==='downloading'){
      bootJob('pi','running',{done:done||0, total:total||0,
        why:'the Pi is downloading "'+a.name+'" onto its own card right now'
          + (done!=null && total ? (' — '+done.toLocaleString()+' of '+total.toLocaleString()+' tiles') : '')
          + '. This console is watching it, not driving it.'});
    } else if(st==='failed'){
      bootJob('pi','failed',{done:done||0, total:total||0,
        why:'the Pi\'s own download of "'+a.name+'" failed'+(a.why?(': '+a.why):'')
          + '. Its card is incomplete; this handheld\'s copy above is unaffected.'});
    } else if(st==='present' || a.present){
      bootJob('pi','held',{done:total||0, total:total||0,
        why:'the Pi holds "'+a.name+'"'+(mb?(' ('+bootMB(mb)+' MB)'):'')
          + (st ? '' : ' — this Pi build does not report whether that download finished, so this says '
                     + 'only that the file is there')
          + '. Nothing was re-downloaded for it.'});
    } else {
      bootJob('pi','not-downloaded',{done:0,total:total||0,
        why:'the Pi lists "'+a.name+'" but has no imagery on its card for it'+(a.why?(': '+a.why):'')+'.'});
    }
  }
  bootLookAtCharts(a);
}
/* THE PI'S DOWNLOAD, LIVE. api/nav/service.py broadcasts area_progress on /ws/nav
   while it fills its own card; map.js's nav socket hands the frames here. Without
   this the Pi row would only move on its 20 s re-read, which is long enough for a
   fetch that starts and fails to be invisible from beginning to end — the silent
   half-finish this whole surface exists to make impossible. */
function bootPiProgress(m){
  if(!m || typeof BOOTFETCH==='undefined') return;
  const st = String(m.state||'');
  const total = (typeof m.total==='number') ? m.total : (BOOTFETCH.jobs.pi.total||0);
  const done  = (typeof m.done==='number') ? m.done : ((typeof m.ok==='number') ? m.ok : 0);
  BOOTFETCH._piAt = Date.now();          // the socket just answered; no need to go and ask
  if(st==='done'){
    bootJob('pi','held',{done:total, total:total,
      why:'the Pi finished its own copy of "'+(m.name||BOOTFETCH.area||'this area')+'"'
        + (typeof m.ok==='number' && total ? (' — '+m.ok.toLocaleString()+' of '+total.toLocaleString()+' tiles landed') : '')
        + '. That card is now what `crt-fetch` can clip the hazard layers to.'});
    bootLookAtCharts(null);
    return;
  }
  if(st==='error' || st==='failed'){
    bootJob('pi','failed',{done:done, total:total,
      why:'the Pi\'s own download failed'+(m.error?(': '+m.error):(m.line?(': '+m.line):''))
        + '. Its card is incomplete; this handheld\'s copy is unaffected and the map still draws.'});
    return;
  }
  bootJob('pi','running',{done:done, total:total,
    why:'the Pi is downloading "'+(m.name||BOOTFETCH.area||'this area')+'" onto its own card'
      + (st==='starting' && m.est_mb ? (' — about '+m.est_mb+' MB, '+(total||0).toLocaleString()+' tiles') : '')
      + (st==='running' && total ? (' — '+done.toLocaleString()+' of '+total.toLocaleString()+' tiles') : '')
      + '. This console is watching it, not driving it.'});
}

/* CHART LAYERS. Read off the index crt.js has already fetched — asking for it a
   second time would double the traffic to say the same thing — and honest about the
   one case this console cannot fix: the Trust layers are downloaded by a command on
   the Pi and there is no endpoint to start it from here. Saying "failed" for that
   would send an operator hunting for a fault; saying NOT AUTOMATIC and printing the
   command sends them to the thing that actually works. */
function bootLookAtCharts(piArea){
  const area = (typeof CRT!=='undefined' && CRT.area) ? CRT.area : (BOOTFETCH.area||'');
  const raw = (typeof CRT!=='undefined' && CRT.indexRaw) ? CRT.indexRaw : null;
  if(typeof CRT==='undefined'){ bootJob('charts','unknown',{why:'the chart layer module is not loaded'}); return; }
  if(!CRT.area){
    bootJob('charts','waiting',{why:'no map area is active yet, so there is nothing to ask the Pi about.'});
    return;
  }
  if(CRT.indexOk !== true){
    // The SAME decision the layer rows make, through the same evidence, so the two
    // halves of this panel can never disagree about which silence this is.
    const quiet = (typeof crtNoAnswerStatus==='function') ? crtNoAnswerStatus() : 'unknown';
    if(quiet==='not-downloaded')
      bootJob('charts','not-downloaded',{why:'no chart data has ever been downloaded for "'+area+'" on '
        + 'this handheld and there is no Pi on this link to download it from'
        + (CRT.indexWhy?(' ('+CRT.indexWhy+')'):'')+'. Nothing has failed.'});
    else
      bootJob('charts','unknown',{why:'the Pi could not be asked for the chart index'
        + (CRT.indexWhy?(' — '+CRT.indexWhy):'')+', so nothing is known about the hazard layers here.'});
    return;
  }
  const present = (raw && raw.status==='present');
  const n = (typeof crtAll==='function')
          ? crtAll().filter(e=>((CRT.state[e.id]||{}).status)==='present').length : 0;
  if(present || n>0){
    bootJob('charts','held',{done:n, total:n,
      why:'the Pi already holds the Canal & River Trust layers for "'+area+'"'
        + (n?(' — '+n+' of them are loaded and drawn'):'')+'. Nothing was re-downloaded.'});
    return;
  }
  const cmd = (raw && raw.remedy) || ('python -m nav.cli crt-fetch ' + area);
  bootJob('charts','not-automatic',{done:0,total:0,
    why:'THE HAZARD LAYERS ARE NOT ON THIS PI AND THIS CONSOLE CANNOT START THAT FETCH. '
      + (raw && raw.why ? ('The Pi says: '+raw.why+'. ') : '')
      + 'The Trust layers are downloaded by a bootstrap command on the Pi itself — there is no '
      + 'endpoint to trigger it from here, so a button that pretended otherwise would do nothing. '
      + 'Run this on the Pi while it still has internet:  ' + cmd + '  '
      + '(the area above is what gives that command a box to clip to). Until then this map is not '
      + 'showing locks, weirs, sluices, culverts, tunnel portals or outfalls at all, and a stretch '
      + 'with no marks on it means NO DATA rather than "nothing there".'});
}

/* THE ONE SENTENCE AT THE END, and it names each source separately. "Download
   finished" over a run where the imagery landed and the charts did not is the report
   that makes an operator dive on a map with no hazards on it.

   IT IS ALSO WHAT THE SETTLED STATES GO THROUGH, not only the end of a run — the
   already-covered branch summarises here too, so there is exactly one place that
   decides whether this block may say the word DOWNLOADED. It is therefore called on
   the 5 s bootstrap tick and must be idempotent: bootTop reports whether anything
   actually changed and only then is a line written to the log.

   WHAT COUNTS AS STILL MISSING IS THE OPERATOR'S QUESTION, NOT THE CONSOLE'S. NO PI
   and CANNOT TELL sit beside NOT DOWNLOADED and NOT AUTOMATIC because "have I got the
   data" is the thing being asked, and "nothing broke" is not an answer to it: a
   handheld whose imagery landed at home with the Pi unplugged has NOT got the hazard
   charts, and this line used to answer that with "everything that could be downloaded
   for this launch point is downloaded". That is the map-looks-complete lie, in the one
   place written to prevent it. None of these are dressed as faults — each row says in
   its own words that nothing has gone wrong — but none of them are allowed to be
   silently rounded up into DOWNLOADED either. */
function bootFinish(){
  const js = BOOTFETCH.order.map(id=>BOOTFETCH.jobs[id]);
  const drove = js.filter(j=>j.drives);
  const stopped = drove.filter(j=>j.state==='stopped');
  const failed  = js.filter(j=>j.state==='failed');
  // `waiting` is in here for a window of a few seconds that is easy to miss and says
  // precisely the wrong thing while it lasts: the imagery run finishes, but CRT.area
  // is only handed the new area on map.js's next 5 s bootstrap tick, so the charts row
  // is still "no map area is active yet" — and a summary that skipped it announced
  // DOWNLOADED over a console that had not yet asked anybody about the hazard layers.
  const outstanding = js.filter(j=>j.state==='not-automatic' || j.state==='not-downloaded'
                               || j.state==='no-pi' || j.state==='unknown' || j.state==='waiting');
  const good = js.filter(j=>j.state==='done' || j.state==='held');
  const fetched = js.some(j=>j.state==='done');
  const list = a=>a.map(j=>j.name.toLowerCase()).join(', ');
  let st, why;
  if(stopped.length){
    st='stopped'; why = 'stopped by the operator. '+(good.length?(list(good)+' is on this handheld; '):'')
                      + 'the rest was not downloaded and nothing was lost — press DOWNLOAD NOW to resume '
                      + 'from exactly where it stopped.';
  } else if(failed.length){
    st='failed'; why = (good.length ? (list(good)+' downloaded; ') : '')
                     + failed.map(j=>j.name.toLowerCase()+' did NOT — '+j.why).join('  ');
  } else if(outstanding.length){
    st='partial'; why = (good.length ? (list(good)+' is downloaded and on this handheld. ') : '')
                      + outstanding.map(j=>j.name.toLowerCase()+' is NOT: '+j.why).join('  ');
  } else if(!fetched && BOOTFETCH.coveredBy){
    // NOTHING WAS FETCHED AND NOTHING NEEDED TO BE — a different fact from a download
    // that ran and finished, and worth its own word so a re-tapped launch point does
    // not read as having just spent somebody's data allowance again.
    st='covered'; why = 'this launch point is already inside the downloaded area "'+BOOTFETCH.coveredBy
                      + '", with at least '+BOOT_REUSE_M+' m of it on every side, and every other source '
                      + 'is already held too. Nothing needed fetching and nothing was re-requested.';
  } else {
    st='done'; why = 'everything that could be downloaded for this launch point is downloaded: '
                   + list(good)+'. The console needs no network from here.';
  }
  if(bootTop(st, why))
    LOG.map('offline data: '+(BOOT_TOP_WORDS[st]||st)+' — '
          + js.map(j=>j.id+'='+(BOOT_WORDS[j.state]||j.state)).join(', '));
}

/* The buttons. Auto is a convenience and never a trap: somebody on a metered hotspot
   needs a way out, and somebody who knows there is a connection needs a way in. */
function bootBindControls(){
  const go=$('crt-fetch-go');
  if(go) go.addEventListener('click', (e)=>{ e.stopPropagation();
    if(BOOTFETCH.running){ return; }
    bootStart('the operator pressed DOWNLOAD NOW', null);
  });
  const stop=$('crt-fetch-stop');
  if(stop) stop.addEventListener('click', (e)=>{ e.stopPropagation(); bootStop('DOWNLOAD stopped from the panel'); });
  const auto=$('crt-fetch-auto');
  if(auto) auto.addEventListener('click', (e)=>{ e.stopPropagation(); bootSetAuto(!BOOTFETCH.auto); });
  bootAutoReady().then(()=>bootRender());
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
  bootBindControls();
}

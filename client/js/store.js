"use strict";
/* ============================================================================
   STORE — the client owns its own state (architectural rule §2). None of this
   routes through the Pi: origin, settings, the saved-area registry, and dive
   logs live in IndexedDB; satellite tiles live in the Cache API (the offline map
   archive). The Pi is an OPTIONAL second copy, never a precondition.

   Everything degrades to a no-op if IndexedDB / Cache API are unavailable (e.g.
   an insecure context) — it must never throw into the app.
   ============================================================================ */
const STORE = { db:null, ready:false, TILE_CACHE:'neptune-tiles' };

STORE.init = function(){
  return new Promise((resolve)=>{
    let req; try{ req=indexedDB.open('neptune-store', 1); }catch(e){ resolve(false); return; }
    req.onupgradeneeded = ()=>{ const db=req.result;
      if(!db.objectStoreNames.contains('kv'))    db.createObjectStore('kv',    {keyPath:'k'});
      if(!db.objectStoreNames.contains('areas')) db.createObjectStore('areas', {keyPath:'name'});
      if(!db.objectStoreNames.contains('dives')) db.createObjectStore('dives', {keyPath:'id'});
    };
    req.onsuccess = ()=>{ STORE.db=req.result; STORE.ready=true; resolve(true); };
    req.onerror   = ()=>resolve(false);
  });
};
function _tx(store, mode){ return STORE.db.transaction(store, mode).objectStore(store); }
function _p(reqFactory){ return new Promise((res)=>{ try{ const r=reqFactory(); r.onsuccess=()=>res(r.result); r.onerror=()=>res(undefined); }catch(e){ res(undefined); } }); }

/* ---- key/value: origin, settings, layout ---- */
STORE.get = async function(k, dflt){ if(!STORE.db) return dflt; const v=await _p(()=>_tx('kv','readonly').get(k)); return v?v.v:dflt; };
STORE.set = async function(k, v){ if(!STORE.db) return false; await _p(()=>_tx('kv','readwrite').put({k, v})); return true; };

/* ---- saved-area registry (client-owned; imagery lives in the Cache API) ---- */
STORE.areas = async function(){ if(!STORE.db) return []; return (await _p(()=>_tx('areas','readonly').getAll()))||[]; };
STORE.areaPut = async function(meta){ if(!STORE.db) return false; await _p(()=>_tx('areas','readwrite').put(meta)); return true; };
STORE.areaDelete = async function(name){ if(!STORE.db) return false; await _p(()=>_tx('areas','readwrite').delete(name)); return true; };

/* ---- dive logs (client-owned; browsable with the Pi off) ---- */
STORE.dives = async function(){ if(!STORE.db) return []; return (await _p(()=>_tx('dives','readonly').getAll()))||[]; };
STORE.divePut = async function(d){ if(!STORE.db) return false; await _p(()=>_tx('dives','readwrite').put(d)); return true; };
STORE.diveDelete = async function(id){ if(!STORE.db) return false; await _p(()=>_tx('dives','readwrite').delete(id)); return true; };

/* ---- Cache API: the offline satellite archive ---- */
function _providerTmpl(){ return ((CONFIG.map.tileProviders||{}).esri||{}).url || ''; }
STORE.tileUrlsForBBox = function(bbox, zmin, zmax){
  const tmpl=_providerTmpl(); if(!tmpl) return [];
  const [minlon,minlat,maxlon,maxlat]=bbox; const out=[];
  for(let z=zmin; z<=zmax; z++){
    const a=lonLatToTile(minlat,minlon,z), b=lonLatToTile(maxlat,maxlon,z);
    const x0=Math.min(a[0],b[0]), x1=Math.max(a[0],b[0]), y0=Math.min(a[1],b[1]), y1=Math.max(a[1],b[1]);
    for(let x=x0; x<=x1; x++) for(let y=y0; y<=y1; y++)
      out.push(tmpl.replace('{z}',z).replace('{y}',y).replace('{x}',x));
  }
  return out;
};

/* SAVE OFFLINE (§2): fetch tiles into the Cache API FROM THE BROWSER — works with the
   Pi powered off. Returns the saved area meta. Mirroring to the Pi is separate/optional. */
STORE.saveArea = async function(name, bbox, detail, onProgress){
  const zr=(CONFIG.map.detailZooms||{})[detail]||[16,18];
  const urls=STORE.tileUrlsForBBox(bbox, zr[0], zr[1]);
  let ok=0;
  if(typeof caches!=='undefined' && window.isSecureContext){
    try{
      const cache=await caches.open(STORE.TILE_CACHE);
      for(let i=0;i<urls.length;i++){
        try{ if(!(await cache.match(urls[i]))){ const r=await fetch(urls[i], {mode:'no-cors'}); await cache.put(urls[i], r); } ok++; }
        catch(e){ /* skip a tile — overzoom covers it later */ }
        if(i%20===0 && onProgress) onProgress(i+1, urls.length);
      }
    }catch(e){ /* Cache API unavailable — meta still saved; tiles fetched live when online */ }
  }
  const meta={ name, bbox, zmin:zr[0], zmax:zr[1], tiles:urls.length, cached:ok,
               detail, savedAt:Date.now(), mirrored:false };
  await STORE.areaPut(meta);
  if(onProgress) onProgress(urls.length, urls.length);
  return meta;
};
STORE.evictArea = async function(meta){
  try{
    if(typeof caches!=='undefined'){
      const cache=await caches.open(STORE.TILE_CACHE);
      const urls=STORE.tileUrlsForBBox(meta.bbox, meta.zmin, meta.zmax);
      await Promise.all(urls.map(u=>cache.delete(u)));
    }
  }catch(e){}
  await STORE.areaDelete(meta.name);
};

"use strict";
/* ============================================================================
   TILES — a tiny zero-dependency raster XYZ tile layer drawn straight to the
   radar canvas (spec §3). Satellite imagery (Esri World Imagery by default)
   renders in both the collapsed radar and the expanded map from ONE engine.

   Why not MapLibre? MapLibre + PMTiles can't be vendored in the offline build,
   and imagery is raster, not vector — so a ~150-line canvas tile layer is both
   simpler and matches the project's browser-native/zero-dependency rule. It
   handles the Esri {z}/{y}/{x} order, heading-up rotation (canvas transform, not
   a CSS transform on a square → no uncovered corners in the circle), overzoom
   from the nearest available parent on a 404, and a dark readability tint.

   Coordinates: standard Web Mercator. Everything is centred on a geographic
   view centre supplied by map.js (the sub's position when following, or a free
   pan centre when placing an origin / selecting a download area).
   ============================================================================ */
const TILES = {
  cache: new Map(),            // "z/x/y" -> { img, state:'load'|'ok'|'err', at }
  last: null,                  // last draw params (for screenToLatLon)
  inflight: 0,
};

/* ---- Web Mercator (unit square 0..1) ---- */
function lonToMercX(lon){ return (lon + 180) / 360; }
function latToMercY(lat){ const r=lat*Math.PI/180; return (1 - Math.log(Math.tan(r) + 1/Math.cos(r))/Math.PI) / 2; }
function mercXToLon(mx){ return mx*360 - 180; }
function mercYToLat(my){ const n=Math.PI*(1 - 2*my); return Math.atan(Math.sinh(n))*180/Math.PI; }

/* tile index of a lat/lon at integer zoom (matches api/nav/satellite.deg2num) */
function lonLatToTile(lat, lon, z){
  const n=1<<z, latR=lat*Math.PI/180;
  const x=Math.floor((lon+180)/360*n);
  const y=Math.floor((1 - Math.asinh(Math.tan(latR))/Math.PI)/2*n);
  return [Math.max(0,Math.min(n-1,x)), Math.max(0,Math.min(n-1,y))];
}
/* count tiles covering a bbox over [zmin..zmax] — the §4 live download estimate */
function countTilesBBox(bbox, zmin, zmax){
  const [minlon,minlat,maxlon,maxlat]=bbox; let total=0;
  for(let z=zmin; z<=zmax; z++){
    const [xa,ya]=lonLatToTile(minlat,minlon,z), [xb,yb]=lonLatToTile(maxlat,maxlon,z);
    total += (Math.abs(xb-xa)+1)*(Math.abs(yb-ya)+1);
  }
  return total;
}

// Always the online provider (Esri) — the client fetches tiles by their real provider
// URLs. Offline is handled TRANSPARENTLY by the service worker's tile cache (§2): saved
// areas are the same URLs, served cache-first, so no internet is needed once saved. The
// Pi never sits in this path. (The `offline` Pi provider stays configured for a Pi-side view.)
function _provider(area){
  const P=CONFIG.map.tileProviders||{};
  return P[CONFIG.map.tileProvider] || { url:'', maxzoom:19, attribution:'' };
}
function tileAttribution(){ return _provider().attribution || ''; }

function _tileUrl(p,z,x,y,area){
  return p.url.replace('{z}',z).replace('{x}',x).replace('{y}',y).replace('{area}', area||'');
}
function _request(z,x,y,area,p){
  const n=1<<z;
  if(z<0 || x<0 || y<0 || x>=n || y>=n) return null;
  const key=z+'/'+x+'/'+y;
  let e=TILES.cache.get(key);
  if(e) return e;
  e={ img:new Image(), state:'load', at:0 };
  // no crossOrigin: we never read pixels back, so a tainted canvas is fine and we
  // avoid tiles failing to load when a provider omits CORS headers.
  e.img.onload = ()=>{ e.state='ok'; e.at=performance.now(); TILES.inflight--; };
  e.img.onerror= ()=>{ e.state='err'; TILES.inflight--; };     // 404 → overzoom handles it (§3.4)
  TILES.cache.set(key,e);
  TILES.inflight++;
  e.img.src=_tileUrl(p,z,x,y,area);
  return e;
}
function _drawTileOrParent(ctx,z,x,y,dx,dy,size,area,fadeMs,p){
  const key=z+'/'+x+'/'+y;
  const e=_request(z,x,y,area,p);
  if(e && e.state==='ok'){
    const a = fadeMs>0 ? Math.max(0,Math.min(1,(performance.now()-e.at)/fadeMs)) : 1;
    ctx.globalAlpha=a; ctx.drawImage(e.img,dx,dy,size,size); ctx.globalAlpha=1;
    if(a<1) return 'fading';
    return true;
  }
  // overzoom / underzoom: nearest cached ancestor fills the gap rather than a black hole (§3.4)
  for(let up=1; up<=6; up++){
    const pz=z-up; if(pz<0) break;
    const px=x>>up, py=y>>up, pkey=pz+'/'+px+'/'+py;
    const pe=TILES.cache.get(pkey);
    if(pe && pe.state==='ok'){
      const sub=256>>up, sx=(x-(px<<up))*sub, sy=(y-(py<<up))*sub;
      ctx.drawImage(pe.img, sx,sy, sub,sub, dx,dy, size,size);
      return 'parent';
    }
  }
  return false;
}

/* Draw the imagery layer, centred on (centerLat,centerLon), rotated by `rot`
   radians, at `scale` CSS metres/pixel. ctx transform is reset by the caller
   AFTER (we save/restore internally). Returns true if any tile drew. */
function drawTiles(ctx, w, h, centerLat, centerLon, scale, rot, dpr, area){
  const p=_provider(area); if(!p.url) return false;
  const latR=centerLat*Math.PI/180;
  const world0 = 156543.03392 * Math.cos(latR);          // metres/pixel at zoom 0 (256px tiles)
  const zf = Math.log2(world0 / scale);
  // Round UP by default, not to nearest. Rounding to nearest picks a coarser tile
  // whenever the view sits just past a zoom boundary and then UPSCALES it — visibly
  // soft, and this imagery is being read for underwater structures and obstacles.
  // Ceil downsamples a sharper tile instead, which is the best the provider has.
  // Still clamped to maxzoom, so it can never ask for a level that does not exist.
  const want = (CONFIG.map.preferSharpTiles===false) ? Math.round(zf) : Math.ceil(zf-0.001);
  const z = Math.max(0, Math.min(p.maxzoom, want));
  const resZ = world0 / (1<<z);                          // metres per tile-pixel at centre
  const k = resZ / scale * dpr;                          // device px per tile-pixel (>dpr ⇒ overzoom, blurry not blank)
  const worldTP = 256 * (1<<z);
  const cxTP = lonToMercX(centerLon) * worldTP;
  const cyTP = latToMercY(centerLat) * worldTP;
  const half = Math.hypot(w,h)/2;                        // cover the rotated diagonal
  const halfTP = half / k + 256;
  const minTx=Math.floor((cxTP-halfTP)/256), maxTx=Math.floor((cxTP+halfTP)/256);
  const minTy=Math.floor((cyTP-halfTP)/256), maxTy=Math.floor((cyTP+halfTP)/256);
  const fadeMs=CONFIG.map.tileFadeMs||0;
  let drew=false;
  ctx.save(); ctx.translate(w/2,h/2); ctx.rotate(rot); ctx.imageSmoothingEnabled=true;
  for(let tx=minTx; tx<=maxTx; tx++){
    for(let ty=minTy; ty<=maxTy; ty++){
      const dx=(tx*256 - cxTP)*k, dy=(ty*256 - cyTP)*k, size=256*k;
      if(_drawTileOrParent(ctx,z,tx,ty,dx,dy,size,area,fadeMs,p)) drew=true;
    }
  }
  ctx.restore();
  TILES.last={ w,h,dpr,rot,k,cxTP,cyTP,worldTP };
  // prune the cache so it can't grow unbounded on long pans
  if(TILES.cache.size>1200){ const it=TILES.cache.keys(); for(let i=0;i<300;i++){ const key=it.next().value; TILES.cache.delete(key); } }
  return drew;
}

/* Forward: {lat,lon} → device-pixel [sx,sy] on the canvas, using the same
   projection + rotation the imagery was drawn with, so vector overlays (sub,
   track, centreline, origin) land exactly on the imagery. */
function lonLatToScreen(lat, lon){
  const L=TILES.last; if(!L) return null;
  const tpx=lonToMercX(lon)*L.worldTP, tpy=latToMercY(lat)*L.worldTP;
  const rx=(tpx-L.cxTP)*L.k, ry=(tpy-L.cyTP)*L.k;
  const c=Math.cos(L.rot), s=Math.sin(L.rot);
  return [ rx*c - ry*s + L.w/2, rx*s + ry*c + L.h/2 ];
}

/* Inverse: a screen point (client px, relative to the canvas) → {lat,lon}. Uses
   the last draw params. Needed for tap-to-set-origin and area selection (§2/§4). */
function screenToLatLon(canvas, clientX, clientY){
  const L=TILES.last; if(!L) return null;
  const r=canvas.getBoundingClientRect();
  let px=(clientX-r.left)*L.dpr - L.w/2, py=(clientY-r.top)*L.dpr - L.h/2;
  const c=Math.cos(-L.rot), s=Math.sin(-L.rot);            // undo the layer rotation
  const rx=px*c - py*s, ry=px*s + py*c;
  const tpx=L.cxTP + rx/L.k, tpy=L.cyTP + ry/L.k;
  return { lat: mercYToLat(tpy/L.worldTP), lon: mercXToLon(tpx/L.worldTP) };
}

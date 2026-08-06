"use strict";
/* ============================================================================
   CAMERA — WOLFANG control plane (REST + telemetry WS). Drives the camera
   status HUD (REC / battery / SD / WARNING), the record + capture buttons, and
   the mode-gated config panel + file browser inside the CONFIG modal.

   §7.4: in-dive controls are limited to record toggle + telemetry; config and
   file ops are gated behind a "SURFACED" toggle because they interrupt video.
   ============================================================================ */

function camApi(path, opts){
  return fetch((state.httpBase || '') + path, opts);
}
function camToast(msg, kind){
  const t = $('cam-toast'); if(!t) return;
  t.textContent = msg;
  t.className = 'cam-toast show' + (kind ? ' ' + kind : '');
  clearTimeout(t._timer); t._timer = setTimeout(()=>{ t.className = 'cam-toast'; }, 3500);
}

/* ---- telemetry (pushed ~15s) + REST status poll (backup) ---------------- */
let _camWsBackoff = 0, _camWsTimer = null;
function connectCamTelemetry(){
  if(!state.wsBase){ return; }                 // disk mode with no host → REST poll only
  if(state.camWs){ try{ state.camWs.onclose=null; state.camWs.close(); }catch(e){} state.camWs=null; }
  let ws;
  try{ ws = new WebSocket(state.wsBase + CONFIG.camera.telemetryWs); }
  catch(e){ scheduleCamTelemetry(); return; }
  state.camWs = ws;
  ws.onopen = ()=>{ _camWsBackoff = 0; };
  ws.onmessage = (ev)=>{
    try{ applyCamStatus(JSON.parse(ev.data)); state.camOkAt = Date.now(); }catch(e){}
  };
  ws.onclose = ()=>{ state.camWs=null; scheduleCamTelemetry(); };
  ws.onerror = ()=>{ try{ ws.close(); }catch(e){} };
}
/* Capped backoff with a single pending timer — a flat 3 s retry with no guard
   stacked reconnects for as long as the Pi was down. */
function scheduleCamTelemetry(){
  if(_camWsTimer) return;
  _camWsBackoff = Math.min(20000, _camWsBackoff ? _camWsBackoff*1.7 : 2000);
  _camWsTimer = setTimeout(()=>{ _camWsTimer=null; connectCamTelemetry(); }, _camWsBackoff);
}
/* Status poll. Guarded so a black-holed Pi cannot pile up requests: at most one
   in flight, each with a hard abort deadline, and a failure backs off instead of
   hammering. A successful response stamps camOkAt, which is what marks the camera
   subsystem up (and therefore what gates ONLY the camera controls). */
let _camPolling = false, _camBackoff = 0, _camTimer = null;
function startCamStatusPoll(){
  const tick = async ()=>{
    if(_camPolling) return;
    _camPolling = true;
    const ctl = (typeof AbortController!=='undefined') ? new AbortController() : null;
    const killer = ctl ? setTimeout(()=>{ try{ ctl.abort(); }catch(e){} }, 4000) : null;
    try{
      const r = await camApi('/api/status', ctl ? {signal:ctl.signal} : undefined);
      if(r.ok){
        applyCamStatus(await r.json());
        state.camOkAt = Date.now();       // the camera plane is alive
        _camBackoff = 0;
      } else {
        _camBackoff = Math.min(30000, _camBackoff ? _camBackoff*2 : 2000);
      }
    }catch(e){
      _camBackoff = Math.min(30000, _camBackoff ? _camBackoff*2 : 2000);
    }finally{
      if(killer) clearTimeout(killer);
      _camPolling = false;
      clearTimeout(_camTimer);
      _camTimer = setTimeout(tick, Math.max(CONFIG.camera.statusPollMs, _camBackoff));
    }
  };
  tick();
}

function applyCamStatus(s){
  const c = state.cam;
  if('battery' in s) c.battery = s.battery;
  if('recording' in s) c.recording = !!s.recording;
  if('record_raw' in s) c.recordRaw = s.record_raw || '';
  if('mode' in s) c.mode = s.mode || '';
  if('sd' in s) c.sd = s.sd || '';
  if('warning' in s) c.warning = s.warning || '';
  if('remaining' in s) c.remaining = s.remaining;
  if('is_streaming' in s) c.isStreaming = s.is_streaming || '';
  if('video_res' in s) c.videoRes = s.video_res || '';
  if('awb' in s) c.awb = s.awb || '';
  if('image_res' in s) c.imageRes = s.image_res || '';
  if('ev' in s) c.ev = s.ev || '';
  if('degraded' in s) c.degraded = !!s.degraded;
  renderCam();
}

function renderCam(){
  const c = state.cam;
  // Recording state lives in the REC button now (ON/OFF + red pulse), not the top bar.
  // REC drives two recorders now, so the button reflects EITHER being live -
  // showing OFF while the screen was still being captured would be a lie.
  // REC carries FOUR facts in one colour, because it is the only camera indicator left
  // (the separate camera glyph said the same thing in a second place):
  //   red    no camera, nothing recording      — the dead-end case
  //   blue   camera present, nothing recording — ready
  //   amber  recording, but not everything     — screen only, or card only
  //   green  camera present and both recording — fully covered
  const btn = $('cam-rec');
  const camOn = (typeof camUp==='function') ? camUp() : true;
  const recs  = (c.recording?1:0) + (state.screenRec.active?1:0);
  const anyRec = recs>0;
  if(btn){
    const st = !anyRec ? (camOn ? 'ready' : 'nocam')
             : (camOn && recs===2) ? 'all' : 'partial';
    btn.dataset.rec = st;
    btn.classList.toggle('recording', anyRec);
    const t=btn.querySelector('.cam-rec-txt');
    // The word says what the RECORDER is doing, and nothing else. Whether a camera
    // exists is already on screen twice over — the eye in the status row, and this
    // button's own colour (red = no camera) — so spelling it out here was a third
    // copy of the same fact, taking space on a button that has one job.
    if(t) t.textContent = anyRec ? (st==='all' ? 'ON' : 'PART') : 'OFF';
    liveTitle(btn, anyRec
      ? 'Recording: ' + [c.recording ? 'camera card' : null,
                         state.screenRec.active ? 'handheld screen' : null].filter(Boolean).join(' + ')
        + (st==='all' ? '' : ' — the other one is NOT recording')
      : (camOn ? 'Ready — record the camera card and the handheld screen'
               : 'No camera: only the handheld screen can be recorded'));
  }
  // camera battery
  const b = $('cam-battery'); if(b) b.textContent = (c.battery!=null ? c.battery+'%' : '--');
  // SD (color-coded, keep the tile value class)
  const sd = $('cam-sd'); if(sd){ sd.textContent = c.sd || '--'; sd.style.color = c.sd==='READY' ? 'var(--tertiary)' : (c.sd ? 'var(--error)' : ''); }
  // live camera settings (mode-dependent: still res in CAMERA, video res otherwise)
  const q = $('cam-quality'); if(q) q.textContent = (c.mode==='CAMERA' ? c.imageRes : c.videoRes) || '--';
  const wb = $('cam-awb'); if(wb){ wb.textContent = shortAwb(c.awb); liveTitle(wb, c.awb || ''); }
  const ev = $('cam-ev'); if(ev) ev.textContent = c.ev || '--';
  const rem = $('cam-remaining'); if(rem) rem.textContent = (c.remaining!=null ? c.remaining : '--');
  const md = $('cam-mode'); if(md) md.textContent = c.mode || '--';
  // No warning banner. A degraded camera link is one of the eye's three states
  // (amber, blinking) - saying it a second time across the middle of the map was
  // noise over the one view the operator is actually flying on.
}

/* The HUD is a glance instrument on a 7in handheld: the top bar has ~48px per
   metric, and "INCANDESCENT" needs 106px, which pushed its neighbours' text into
   each other. Abbreviate for display only - the full value stays in the tooltip,
   and nothing here changes what is sent to the camera. */
const AWB_SHORT = {
  INCANDESCENT:'INCAND', FLUORESCENT1:'FLUOR1', FLUORESCENT2:'FLUOR2',
  FLUORESCENT3:'FLUOR3', DAYLIGHT:'DAY', CLOUDY:'CLOUD', AUTO:'AUTO',
};
function shortAwb(v){
  if(!v) return '--';
  return AWB_SHORT[String(v).toUpperCase()] || v;
}

/* ---- screen recording (the handheld's half of "record both") ------------
   The camera records what it sees, onto a card inside the vehicle. This records
   what the OPERATOR sees - instruments, map and all - onto the handheld, so a
   dive has a topside account of itself even if the card never comes back.

   ffmpeg does it via gdigrab -> libx264, which is the same trade as re-encoding a
   screen recording afterwards, done once and live instead. No audio (-an): there
   is nothing to hear and it only costs bytes. */
async function screenRecord(action, name){
  if(!CONFIG.camera.recordEndpoint) return {ok:false, why:'no launcher'};
  const q = '?action=' + action
          + (name ? '&name=' + encodeURIComponent(name) : '')
          + '&fps=' + (CONFIG.camera.recordFps || 30)
          + '&crf=' + (CONFIG.camera.recordCrf || 23);
  try{
    const r = await fetch(CONFIG.camera.recordEndpoint + q, {method:'POST'});
    const text = (await r.text()).trim();
    return r.ok ? {ok:true, detail:text} : {ok:false, why:text || ('HTTP '+r.status)};
  }catch(e){ return {ok:false, why:(e.message||String(e))}; }
}

/* ---- commands (in-dive allowed): record toggle + capture ----------------
   ONE button, two recorders, reported separately. Either can be unavailable -
   no camera on the bench, no ffmpeg on a fresh machine - and neither absence
   should stop the other from running. */
async function camRecordToggle(){
  const btn = $('cam-rec'); if(btn) btn.disabled = true;
  const notes = [];
  try{
    const wantStart = !state.screenRec.active && !state.cam.recording;

    // --- the handheld's screen ---
    if(CONFIG.camera.recordEndpoint){
      if(state.screenRec.active){
        const r = await screenRecord('stop');
        state.screenRec.active = false;
        notes.push(r.ok ? 'screen saved ' + (r.detail || '').split(/[\\/]/).pop()
                        : 'screen stop failed: ' + r.why);
      } else if(wantStart){
        const name = stampName(Date.now()) + '.mp4';
        const r = await screenRecord('start', name);
        state.screenRec.active = r.ok;
        state.screenRec.file = r.ok ? r.detail : '';
        notes.push(r.ok ? 'screen recording' : 'no screen recording: ' + r.why);
      }
    }

    // --- the camera's own card ---
    if(typeof camUp === 'function' && !camUp()){
      notes.push('no camera');
    } else {
      try{
        const r = await camApi('/api/record/toggle', {method:'POST'});
        if(!r.ok) throw new Error('HTTP '+r.status);
        const st = await r.json();
        state.cam.recording = !!st.recording; state.cam.recordRaw = st.record_raw || '';
        notes.push(st.changed ? (st.recording ? 'camera recording' : 'camera stopped')
                              : 'camera toggle sent (no state change seen)');
        LOG.cmd('camera record ->', st);
      }catch(e){ notes.push('camera failed: '+(e.message||e)); }
    }

    renderCam();
    const bad = notes.some(n => /failed|no screen recording/.test(n));
    camToast('REC · ' + notes.join(' · '), bad ? 'warn' : 'ok');
  }finally{ if(btn) btn.disabled = false; }
}

/* A recording left running outlives the page that started it, so stop it when the
   console goes away. sendBeacon survives unload where fetch does not. */
function stopScreenRecordingOnExit(){
  if(!state.screenRec.active || !CONFIG.camera.recordEndpoint) return;
  try{
    const url = CONFIG.camera.recordEndpoint + '?action=stop';
    if(navigator.sendBeacon) navigator.sendBeacon(url, new Blob([], {type:'text/plain'}));
    else fetch(url, {method:'POST', keepalive:true});
  }catch(e){}
}
/* ---- stills: two copies, taken independently ----------------------------
   The camera's own JPEG goes to the SD card, which is inside the vehicle, in the
   water, on a card that has to be physically recovered - and if the camera is
   flat, absent or unreachable there is no copy at all. So PIC also grabs what the
   operator is looking at, topside, and keeps it here.

   The two halves are deliberately independent (rule 3): a dead camera must not
   stop the local still, and a full disk must not stop the camera. Each reports
   its own outcome, and the toast never claims a copy that was not made.

   It also means PIC does something useful in SIM, where there is no camera at
   all - the frame source falls back to the map, so a bench run still produces a
   real image to test against. */
/* Every artefact this session produces is named the same way: {mode}_{iso}.{ext}.
   The mode is what the console was actually doing (sim / real / stale), so a folder
   of files sorts by time and still says which of them were real dives.

   Milliseconds are kept because the name is also the IndexedDB key: at second
   resolution two presses inside the same second produced the same key and the
   second silently overwrote the first.

   Colons are stripped - ISO 8601 uses them and Windows will not have them in a
   filename, which is the sort of thing that fails only once there is real data. */
function fsIso(t){ return new Date(t).toISOString().replace(/:/g, '-'); }
function stampName(t){ return (state.mode || 'sim') + '_' + fsIso(t); }

/* Hand a file to the launcher, which puts it under client/navigation_logs/<kind>/.
   Returns the path it wrote, or '' if there is no launcher (page served from the
   Pi, a static server, a test harness) - in which case the caller falls back to a
   browser download. */
async function saveArtifact(kind, name, blob, append){
  if(!CONFIG.camera.saveEndpoint) return '';
  try{
    const q = '?kind=' + encodeURIComponent(kind) + '&name=' + encodeURIComponent(name)
            + (append ? '&append=1' : '');
    const r = await fetch(CONFIG.camera.saveEndpoint + q, {method:'POST', body:blob});
    if(!r.ok) return '';
    return (await r.text()).trim();
  }catch(e){ return ''; }
}

/* What the operator is actually looking at, in priority order. In blind nav the
   map IS the view, so a still of a black video element would be worse than
   useless - it would look like the camera worked. */
function grabSource(){
  const v = $('video-feed');
  if(v && v.videoWidth > 0 && v.videoHeight > 0 && state.video === 'live')
    return { kind:'video', el:v, w:v.videoWidth, h:v.videoHeight, source:'video' };
  const c = (typeof MAP !== 'undefined') && MAP.canvas;
  if(c && c.width > 0 && c.height > 0)
    return { kind:'map', el:c, w:c.width, h:c.height, source:'map' };
  return null;
}

/* Satellite tiles are loaded WITHOUT `crossOrigin` on purpose — the offline
   archive stores them as opaque responses, and requiring CORS would break the
   map in the field. The cost is that the live map canvas is tainted and the
   browser refuses to export it: "Tainted canvases may not be exported".

   The video is a MediaStream and never taints, so the camera view is unaffected.
   Only the map fallback needs this, and it is cheaper to ask than to catch: a
   1px read tells us before we have encoded anything. */
function canvasTainted(cv){
  try{ cv.getContext('2d').getImageData(0, 0, 1, 1); return false; }
  catch(e){ return true; }
}

/* THE REAL SCREENSHOT.

   A page cannot screenshot itself. A canvas composite only ever knows about the
   video and the map — it cannot see the top bar, the control rail, the banners or
   anything else the operator is actually looking at, and the satellite basemap
   taints it on top of that. So the launcher, which already serves this page from
   localhost, takes the capture instead: the same thing PrintScreen does.

   Same-origin, so the PNG does NOT taint the canvas we draw it into — which is
   what lets the basemap survive. Falls back to the canvas composite when the page
   is not being served by the launcher (served from the Pi, from a plain static
   server, or a test harness). */
async function grabScreenshot(id){
  if(!CONFIG.camera.screenshotEndpoint) return null;
  const ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  // PIC must stay responsive: a launcher that is wedged or an endpoint that is not
  // there must cost a moment, not the capture.
  const killer = ctl ? setTimeout(()=>{ try{ ctl.abort(); }catch(e){} },
                                  CONFIG.camera.screenshotTimeoutMs || 4000) : null;
  try{
    // Hand the launcher the filename so IT writes the file. Chrome allows one
    // automatic download per origin and then blocks the rest - it had already
    // recorded a permanent block for this origin, so only the first PIC ever
    // reached the disk. Taking the browser out of the path fixes that for good.
    const url = CONFIG.camera.screenshotEndpoint
              + (id ? (CONFIG.camera.screenshotEndpoint.indexOf('?') === -1 ? '?' : '&')
                      + 'name=' + encodeURIComponent(id) : '');
    const r = await fetch(url, ctl ? {signal:ctl.signal} : undefined);
    if(!r.ok) return null;                       // 404 = not the launcher; 500 = it tried and failed
    const type = r.headers.get('content-type') || '';
    if(type.indexOf('image') !== 0) return null;
    const blob = await r.blob();
    const bmp = await createImageBitmap(blob);
    return { bmp, w: bmp.width, h: bmp.height, savedPath: r.headers.get('X-Saved-Path') || '' };
  }catch(e){ return null; }
  finally{ if(killer) clearTimeout(killer); }
}

/* A downloaded JPEG leaves the app and loses the record around it, so burn the
   essentials into the pixels. The camera does the same thing for the same reason
   (Camera.Preview.MJPEG.TimeStamp is kept ACTIVE precisely so footage can be lined
   up against the blackbox afterwards). One thin strip along the bottom - it must
   not cover the thing being photographed. */
function stampCaption(cx, w, h, t, source, basemap){
  try{
    const d = new Date(t), p = n => String(n).padStart(2,'0');
    const clock = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    const full  = `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${clock}`;
    // Ordered by what must survive if the strip is too narrow to hold everything.
    // SIM ranks above the date: mistaking a simulated frame for a real one is a
    // worse error than not knowing which day it was, and the filename carries the
    // date anyway.
    const bits = [
      full,
      isFinite(state.depth)   ? state.depth.toFixed(1)+'m'   : null,
      isFinite(state.heading) ? Math.round(state.heading)+'°' : null,
      (typeof commandsBlocked === 'function' && commandsBlocked()) ? 'SIM' : null,
      source === 'map' ? 'MAP' : 'CAM',
      basemap === false ? 'NO BASEMAP' : null,
    ].filter(Boolean);

    // The radar canvas is only ~198px wide, where the full caption overflowed and
    // was clipped — losing exactly the SIM / NO BASEMAP markers that say what the
    // image is. Degrade deliberately instead: shrink, then shorten the timestamp,
    // then drop from the least important end.
    const SEP = '  ·  ';
    let fs = Math.max(9, Math.round(h * 0.028));
    let pad = Math.round(fs * 0.5);
    const setFont = ()=>{ cx.font = `600 ${fs}px ui-monospace, Consolas, monospace`; };
    setFont();
    const tooWide = ()=> cx.measureText(bits.join(SEP)).width > w - pad * 2;
    while(tooWide() && fs > 8){ fs--; pad = Math.round(fs * 0.5); setFont(); }
    if(tooWide() && bits[0] === full) bits[0] = clock;
    while(tooWide() && bits.length > 1) bits.pop();

    const bar = fs + pad * 2;
    cx.save();
    cx.setTransform(1,0,0,1,0,0);
    cx.fillStyle = 'rgba(6,2,16,0.62)';
    cx.fillRect(0, h - bar, w, bar);
    cx.fillStyle = '#ece3ff';
    setFont();
    cx.textBaseline = 'middle';
    cx.fillText(bits.join(SEP), pad, h - bar / 2);
    cx.restore();
  }catch(e){ /* a caption is never worth losing the image over */ }
}

async function captureLocalStill(t){
  // A true screen capture first — it is the whole point: the operator wants what
  // is on the screen, metrics and basemap included, not the video layer alone.
  const screenId = stampName(t) + '.png';
  const shot = await grabScreenshot(screenId);
  if(shot){
    try{
      const cv = document.createElement('canvas');
      cv.width = shot.w; cv.height = shot.h;
      const cx = cv.getContext('2d');
      cx.drawImage(shot.bmp, 0, 0);
      try{ shot.bmp.close(); }catch(e){}
      // NO caption here on purpose. This is "the screen as I see it" - the top bar
      // is already in the frame with the time-relevant telemetry, the filename
      // carries the timestamp, and a strip along the bottom would cover the
      // control rail. The composite fallbacks below DO get one, because there the
      // surrounding UI is genuinely absent from the image.
      const blob = await new Promise(res => cv.toBlob(res, 'image/jpeg', CONFIG.camera.stillQuality || 0.92));
      if(blob) return await storeStill(t, blob, {source:'screen', w:shot.w, h:shot.h,
                                                 basemap:null, degraded:'',
                                                 savedPath:shot.savedPath});
    }catch(e){ /* fall through to the canvas composite below */ }
  }

  const src = grabSource();
  if(!src) return { ok:false, why:'nothing to capture (no screen capture, no live feed, no map)' };
  let blob, basemap = null, degraded = '';
  try{
    const cv = document.createElement('canvas');
    cv.width = src.w; cv.height = src.h;
    const cx = cv.getContext('2d');
    if(src.kind === 'map' && canvasTainted(src.el)){
      // Re-render the same frame without the imagery. Same pixel size and dpr, so
      // the track, sub, grid and centreline land exactly where they do on screen —
      // the imagery is the only thing lost, and every navigational layer survives.
      basemap = false;
      degraded = 'no basemap (imagery cannot be exported)';
      drawCanvas({ ctx:cx, w:cv.width, h:cv.height, dpr:MAP.dpr, noTiles:true });
    } else {
      cx.drawImage(src.el, 0, 0, src.w, src.h);
      if(src.kind === 'map') basemap = true;
    }
    stampCaption(cx, cv.width, cv.height, t, src.source, basemap);
    blob = await new Promise(res => cv.toBlob(res, 'image/jpeg', CONFIG.camera.stillQuality || 0.92));
  }catch(e){ return { ok:false, why:'could not read the frame: '+(e.message||e) }; }
  if(!blob) return { ok:false, why:'encoder returned nothing' };

  return await storeStill(t, blob, {source:src.source, w:src.w, h:src.h, basemap, degraded});
}

async function storeStill(t, blob, meta){
  // Telemetry travels WITH the image. A still with no depth or heading is a
  // holiday snap; the point is being able to place it in the dive afterwards.
  const rec = {
    // The id IS the filename, so the in-app list and the folder agree.
    id: stampName(t) + (meta.source === 'screen' ? '.png' : '.jpg'),
    t, source: meta.source, w: meta.w, h: meta.h, blob, basemap: meta.basemap,
    sim: (typeof commandsBlocked === 'function') ? commandsBlocked() : null,
    depth: state.depth, heading: state.heading, pressure: state.pressure,
    ballast: state.ballastLevel, batteryV: state.batteryV,
    x: (typeof MAP !== 'undefined') ? MAP.x : null,
    y: (typeof MAP !== 'undefined') ? MAP.y : null,
    origin: (typeof MAP !== 'undefined' && MAP.hasOrigin) ? MAP.origin : null,
    // Where the launcher put the file. Set BEFORE the record is written - assigning
    // it afterwards left every stored record without it, so the in-app list could
    // not tell you where the disk copy went.
    savedPath: meta.savedPath || '',
  };
  const stored = await STORE.stillPut(rec);

  // A real FILE on disk - the whole point is a second copy, and one that survives
  // the browser profile being cleared.
  //
  // The launcher writes it when it took the capture, which is the ONLY reliable
  // route: Chrome permits one automatic download per origin and then blocks the
  // rest, and it had already recorded a permanent block for http://localhost:8080,
  // so every PIC after the first vanished silently. The <a download> path below is
  // now only for the composite fallbacks, where nothing else can write the file.
  let downloaded = false, savedPath = meta.savedPath || '';
  if(!savedPath){
    // The composite fallbacks were the only artefacts still going out through the
    // browser and landing loose in Downloads. Offer them to the launcher first so
    // everything the session produced ends up in the same folder.
    savedPath = await saveArtifact('images', rec.id, blob, false);
  }
  if(savedPath){
    downloaded = true;
  } else {
    try{
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = rec.id + '.jpg';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>URL.revokeObjectURL(url), 30000);
      downloaded = true;
    }catch(e){ /* stays in IndexedDB only */ }
  }

  return { ok: stored || downloaded, rec, stored, downloaded, savedPath,
           degraded: meta.degraded || '',
           why: (!stored && !downloaded) ? 'could not store or save the image' : '' };
}

/* A shutter has to be FELT. The PIC button flashes the instant the capture starts —
   not when it finishes, because the camera's own capture blocks for ~2 s and feedback
   that late reads as a button that did nothing. */
function firePicFlash(){
  const b=$('cam-capture'); if(!b) return;
  b.classList.remove('shot');
  void b.offsetWidth;                    // restart the animation even on a rapid re-press
  b.classList.add('shot');
  setTimeout(()=>b.classList.remove('shot'), 500);
}
async function camCapture(){
  firePicFlash();
  const btn = $('cam-capture'); if(btn) btn.disabled = true;
  const t = Date.now();
  const notes = [];
  try{
    // LOCAL first: it is the copy that always works, and doing it first means the
    // frame matches the moment PIC was pressed rather than whatever is on screen
    // ~2s later, after the camera's very slow capture has blocked the stream.
    let local;
    try{ local = await captureLocalStill(t); }
    catch(e){ local = { ok:false, why:(e.message||String(e)) }; }
    if(local.ok) notes.push((local.savedPath ? 'saved ' + local.savedPath.split(/[\/]/).pop()
                                             : 'saved locally')
                            + (local.downloaded ? '' : ' (in-app only)')
                            + (local.degraded ? ' — ' + local.degraded : ''));
    else         notes.push('local save failed: ' + local.why);
    if(local.rec) LOG.cmd('still saved ->', {id:local.rec.id, source:local.rec.source,
                                             stored:local.stored, downloaded:local.downloaded,
                                             path:local.savedPath || '(browser download)'});

    // CAMERA second, and only if there is one. In sim this is skipped entirely
    // rather than reported as a failure - there is no camera to fail.
    if(typeof camUp === 'function' && !camUp()){
      notes.push('no camera (SD copy skipped)');
      camToast('PIC · ' + notes.join(' · '), local.ok ? 'warn' : 'bad');
      return;
    }
    try{
      const r = await camApi('/api/capture', {method:'POST'});
      if(!r.ok) throw new Error('HTTP '+r.status);
      const f = await r.json();
      notes.push(f && f.name ? 'SD: '+f.name.split('/').pop() : 'SD copy sent');
      LOG.cmd('camera capture ->', f);
      camToast('PIC · ' + notes.join(' · '), local.ok ? 'ok' : 'warn');
    }catch(e){
      notes.push('SD copy failed: '+(e.message||e));
      camToast('PIC · ' + notes.join(' · '), local.ok ? 'warn' : 'bad');
    }
  }finally{ if(btn) btn.disabled = false; }
}

/* ---- mode-gated config panel (driven by cammenu.xml) -------------------- */
function setSurfaced(on){
  state.surfaced = !!on;
  const panel = $('cfg-camera'), files = $('cfg-files');
  [panel, files].forEach(el=>{ if(el){ el.style.opacity = on ? '1' : '.4'; el.style.pointerEvents = on ? 'auto' : 'none'; } });
  const hint = $('cfg-surfaced-hint'); if(hint) hint.textContent = on ? '' : '(locked in-dive)';
  if(on){ loadCamMenu(); loadCamFiles('video'); }
}

async function loadCamMenu(){
  const panel = $('cfg-camera'); if(!panel) return;
  panel.innerHTML = '<div class="font-label-caps text-[10px] text-on-surface-variant">loading camera menu…</div>';
  let menu;
  try{ const r = await camApi('/api/menu'); menu = await r.json(); }
  catch(e){ panel.innerHTML = '<div class="font-label-caps text-[10px] text-error">camera unreachable</div>'; return; }
  state.cam.menu = menu || [];
  panel.innerHTML = '';
  (menu || []).forEach(item=>{
    const row = document.createElement('div'); row.className = 'cfg-row';
    const name = document.createElement('span'); name.className='cfg-name'; name.textContent = item.property;
    const sel = document.createElement('select'); sel.className='cfg-select';
    (item.options || []).forEach(opt=>{
      const o = document.createElement('option'); o.value = opt; o.textContent = opt;
      if(opt === item.current) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', ()=> applyCamConfig(item.property, sel.value, sel));
    row.appendChild(name); row.appendChild(sel); panel.appendChild(row);
  });
}
async function applyCamConfig(prop, value, sel){
  if(sel) sel.disabled = true;
  try{
    const r = await camApi('/api/config/'+encodeURIComponent(prop)+'?value='+encodeURIComponent(value), {method:'PUT'});
    const res = await r.json();
    camToast(res.took ? (prop+' → '+res.actual) : (prop+' set (actual: '+res.actual+')'), res.took ? 'ok' : 'warn');
    LOG.cmd('camera config', prop, '->', res);
  }catch(e){ camToast('config failed: '+(e.message||e), 'bad'); }
  finally{ if(sel) sel.disabled = false; }
}

/* ---- file browser (thumbnails + resumable download) --------------------- */
async function loadCamFiles(kind){
  const box = $('cfg-files'); if(!box) return;
  box.innerHTML = '<div class="font-label-caps text-[10px] text-on-surface-variant">loading files…</div>';
  let files;
  try{ const r = await camApi('/api/files?type='+kind+'&from=0&count=100'); files = await r.json(); }
  catch(e){ box.innerHTML = '<div class="font-label-caps text-[10px] text-error">could not list files</div>'; return; }
  box.innerHTML = '';
  const tabs = document.createElement('div'); tabs.className='cfg-file-tabs';
  ['video','photo'].forEach(t=>{
    const b = document.createElement('button'); b.className='mp-btn'+(t===kind?' active':''); b.textContent=t.toUpperCase();
    b.addEventListener('click', ()=>loadCamFiles(t)); tabs.appendChild(b);
  });
  box.appendChild(tabs);
  if(!files.length){ const e=document.createElement('div'); e.className='font-label-caps text-[10px] text-on-surface-variant'; e.textContent='no files'; box.appendChild(e); return; }
  files.forEach(f=>{
    const row = document.createElement('div'); row.className='cfg-file';
    const img = document.createElement('img'); img.className='cfg-thumb'; img.loading='lazy';
    img.src = (state.httpBase||'') + '/api/files' + f.name + '/thumb';
    img.onerror = ()=>{ img.style.visibility='hidden'; };
    const meta = document.createElement('div'); meta.className='cfg-file-meta';
    const nm = document.createElement('div'); nm.className='cfg-file-name'; nm.textContent = f.name.split('/').pop();
    const sub = document.createElement('div'); sub.className='cfg-file-sub';
    sub.textContent = [f.resolution, f.duration!=null?f.duration+'s':'', _mb(f.size)].filter(Boolean).join(' · ');
    meta.appendChild(nm); meta.appendChild(sub);
    const dl = document.createElement('button'); dl.className='mp-btn'; dl.textContent='GET';
    dl.addEventListener('click', ()=>camDownload(f.name, dl));
    const del = document.createElement('button'); del.className='mp-btn'; del.textContent='DEL';
    del.addEventListener('click', ()=>camDelete(f.name, kind));
    row.appendChild(img); row.appendChild(meta); row.appendChild(dl); row.appendChild(del);
    box.appendChild(row);
  });
}
async function camDownload(name, btn){
  if(btn) btn.textContent='…';
  try{
    await camApi('/api/files'+name+'/download', {method:'POST'});
    camToast('Offload queued: '+name.split('/').pop(), 'ok');
  }catch(e){ camToast('download failed', 'bad'); }
  finally{ if(btn) btn.textContent='GET'; }
}
async function camDelete(name, kind){
  if(!confirm('Delete '+name.split('/').pop()+' from the card? This is unrecoverable.')) return;
  try{
    const r = await camApi('/api/files'+name+'?confirm=true', {method:'DELETE'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    camToast('Deleted '+name.split('/').pop(), 'ok'); loadCamFiles(kind);
  }catch(e){ camToast('delete failed', 'bad'); }
}
function _mb(b){ return b ? (b/1e6).toFixed(1)+'MB' : ''; }

/* ---- bootstrap ---------------------------------------------------------- */
function initCamera(){
  const rec = $('cam-rec'); if(rec) rec.addEventListener('click', camRecordToggle);
  const cap = $('cam-capture'); if(cap) cap.addEventListener('click', camCapture);
  // A recording outlives the page unless something stops it.
  window.addEventListener('beforeunload', stopScreenRecordingOnExit);
  window.addEventListener('pagehide', stopScreenRecordingOnExit);
  const surf = $('cfg-surfaced'); if(surf) surf.addEventListener('change', ()=>setSurfaced(surf.checked));
  setSurfaced(false);            // locked until surfaced
  renderCam();
  connectCamTelemetry();
  startCamStatusPoll();
  LOG.state('camera plane initialised');
}

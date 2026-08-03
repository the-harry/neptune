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
function connectCamTelemetry(){
  if(!state.wsBase){ return; }                 // disk mode with no host → REST poll only
  let ws;
  try{ ws = new WebSocket(state.wsBase + CONFIG.camera.telemetryWs); }
  catch(e){ return; }
  state.camWs = ws;
  ws.onmessage = (ev)=>{ try{ applyCamStatus(JSON.parse(ev.data)); }catch(e){} };
  ws.onclose = ()=>{ state.camWs=null; setTimeout(connectCamTelemetry, 3000); };
  ws.onerror = ()=>{ try{ ws.close(); }catch(e){} };
}
function startCamStatusPoll(){
  const tick = async ()=>{
    try{ const r = await camApi('/api/status'); if(r.ok) applyCamStatus(await r.json()); }
    catch(e){}
  };
  tick();
  setInterval(tick, CONFIG.camera.statusPollMs);
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
  // REC value (tile) + rail button
  const lbl = $('cam-rec-label'), btn = $('cam-rec');
  if(lbl){ lbl.textContent = c.recording ? 'REC' : 'IDLE'; lbl.className = 'm-val' + (c.recording ? ' rec' : ''); }
  if(btn){ btn.classList.toggle('recording', c.recording); const t=btn.querySelector('.cam-rec-txt'); if(t) t.textContent = c.recording ? 'STOP' : 'REC'; }
  // camera battery
  const b = $('cam-battery'); if(b) b.textContent = (c.battery!=null ? c.battery+'%' : '--');
  // SD (color-coded, keep the tile value class)
  const sd = $('cam-sd'); if(sd){ sd.textContent = c.sd || '--'; sd.style.color = c.sd==='READY' ? 'var(--tertiary)' : (c.sd ? 'var(--error)' : ''); }
  // live camera settings (mode-dependent: still res in CAMERA, video res otherwise)
  const q = $('cam-quality'); if(q) q.textContent = (c.mode==='CAMERA' ? c.imageRes : c.videoRes) || '--';
  const wb = $('cam-awb'); if(wb) wb.textContent = c.awb || '--';
  const ev = $('cam-ev'); if(ev) ev.textContent = c.ev || '--';
  const rem = $('cam-remaining'); if(rem) rem.textContent = (c.remaining!=null ? c.remaining : '--');
  const md = $('cam-mode'); if(md) md.textContent = c.mode || '--';
  // WARNING banner — the primary fault channel (§4.4)
  const warn = $('cam-warning');
  if(warn){
    const msg = c.degraded ? 'CAMERA LINK DEGRADED' : (c.warning || '');
    if(msg){ warn.textContent = '⚠ ' + msg; warn.classList.add('show'); }
    else warn.classList.remove('show');
  }
}

/* ---- commands (in-dive allowed): record toggle + capture ---------------- */
async function camRecordToggle(){
  const btn = $('cam-rec'); if(btn) btn.disabled = true;
  try{
    const r = await camApi('/api/record/toggle', {method:'POST'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const st = await r.json();
    state.cam.recording = !!st.recording; state.cam.recordRaw = st.record_raw || '';
    renderCam();
    camToast(st.changed ? (st.recording ? 'Recording started' : 'Recording stopped')
                        : 'Record toggle sent (no state change seen)', st.changed ? 'ok' : 'warn');
    LOG.cmd('camera record ->', st);
  }catch(e){ camToast('Record failed: '+(e.message||e), 'bad'); }
  finally{ if(btn) btn.disabled = false; }
}
async function camCapture(){
  const btn = $('cam-capture'); if(btn) btn.disabled = true;
  try{
    const r = await camApi('/api/capture', {method:'POST'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const f = await r.json();
    camToast(f && f.name ? 'Captured '+f.name.split('/').pop() : 'Capture sent', 'ok');
    LOG.cmd('camera capture ->', f);
  }catch(e){ camToast('Capture failed: '+(e.message||e), 'bad'); }
  finally{ if(btn) btn.disabled = false; }
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
  const surf = $('cfg-surfaced'); if(surf) surf.addEventListener('change', ()=>setSurfaced(surf.checked));
  setSurfaced(false);            // locked until surfaced
  renderCam();
  connectCamTelemetry();
  startCamStatusPoll();
  LOG.state('camera plane initialised');
}

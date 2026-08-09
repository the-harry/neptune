"use strict";
/* ============================================================================
   LOGVIEW — the live log, readable WITHOUT leaving the dive.

   A fault underwater has to be diagnosed while the vehicle is still in the water.
   Leaving the console to open a file is not an option mid-session, so the log is
   an overlay: centred, deliberately NOT full screen, over a dimmed-but-visible
   background — the point is to read the log while still seeing what the vehicle
   is doing behind it.

   Tails by default. Scrolling up stops the tail (so a line you are reading does
   not slide away) and scrolling back to the bottom resumes it, which is the
   behaviour every log viewer has and nobody wants to think about.

   The scrollback here is the in-memory ring, so it is bounded. The complete
   record is the session log on disk under navigation_logs/logs.
   ============================================================================ */
const LOGVIEW = {
  el:null, body:null, open:false, tail:true,
  filter:'', level:'all',
  unsub:null, pending:[], flushTimer:null,
};

const _LV_LEVEL_ORDER = { info:0, ok:0, warn:1, err:2 };

function _lvMatch(line){
  if(LOGVIEW.level === 'warn' && (_LV_LEVEL_ORDER[line.level]||0) < 1) return false;
  if(LOGVIEW.level === 'err'  && (_LV_LEVEL_ORDER[line.level]||0) < 2) return false;
  if(LOGVIEW.filter){
    const f = LOGVIEW.filter.toLowerCase();
    if((line.tag + ' ' + line.msg).toLowerCase().indexOf(f) === -1) return false;
  }
  return true;
}

function _lvClock(ms){
  const d = new Date(ms);
  const p = n => String(n).padStart(2,'0');
  return p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())
       + '.' + String(d.getMilliseconds()).padStart(3,'0');
}

function _lvRow(line){
  const clock = _lvClock(line.t);
  const row = document.createElement('div');
  row.className = 'lv-row lv-' + (line.level || 'info');
  row.innerHTML = '<span class="lv-t">' + clock + '</span>'
                + '<span class="lv-tag">' + _lvEsc(line.tag) + '</span>'
                + '<span class="lv-m">' + _lvEsc(line.msg) + '</span>';
  return row;
}
function _lvEsc(s){
  return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function buildLogView(){
  if(LOGVIEW.el) return;
  const el = document.createElement('div');
  el.id = 'logview';
  el.innerHTML =
    '<div class="lv-card">'+
      '<div class="lv-head">'+
        '<span class="lv-title">LOGS</span>'+
        '<input id="lv-filter" placeholder="filter…" autocomplete="off">'+
        '<button id="lv-all"  class="lv-btn lv-on">ALL</button>'+
        '<button id="lv-warn" class="lv-btn">WARN+</button>'+
        '<button id="lv-err"  class="lv-btn">ERR</button>'+
        '<button id="lv-tail" class="lv-btn lv-on" title="Follow new lines">TAIL</button>'+
        '<span id="lv-count" class="lv-count"></span>'+
        '<button id="lv-close" class="lv-btn lv-close">&#10005;</button>'+
      '</div>'+
      '<div id="lv-body" class="lv-body"></div>'+
      '<div class="lv-foot"><span id="lv-file"></span></div>'+
    '</div>';
  document.body.appendChild(el);
  LOGVIEW.el = el;
  LOGVIEW.body = el.querySelector('#lv-body');

  el.addEventListener('click', (e)=>{ if(e.target === el) closeLogView(); });   // backdrop
  $('lv-close').addEventListener('click', closeLogView);
  $('lv-tail').addEventListener('click', ()=>{ LOGVIEW.tail = !LOGVIEW.tail; _lvSyncButtons(); if(LOGVIEW.tail) _lvScrollEnd(); });
  ['all','warn','err'].forEach(k=>{
    $('lv-'+k).addEventListener('click', ()=>{ LOGVIEW.level = k; _lvSyncButtons(); renderLogView(); });
  });
  const f = $('lv-filter');
  f.addEventListener('input', ()=>{ LOGVIEW.filter = f.value.trim(); renderLogView(); });
  // The overlay must never eat piloting input (HUD rule): keys typed in the filter
  // stay in the filter, and nothing else here binds a key.
  f.addEventListener('keydown', (e)=>{ e.stopPropagation(); if(e.key==='Escape'){ f.blur(); closeLogView(); } });

  // Scrolling up suspends the tail; returning to the bottom resumes it.
  LOGVIEW.body.addEventListener('scroll', ()=>{
    const b = LOGVIEW.body;
    const atEnd = (b.scrollHeight - b.scrollTop - b.clientHeight) < 24;
    if(LOGVIEW.tail !== atEnd){ LOGVIEW.tail = atEnd; _lvSyncButtons(); }
  });
}

function _lvSyncButtons(){
  ['all','warn','err'].forEach(k=>{
    const b = $('lv-'+k); if(b) b.classList.toggle('lv-on', LOGVIEW.level === k);
  });
  const t = $('lv-tail'); if(t){ t.classList.toggle('lv-on', LOGVIEW.tail); t.textContent = LOGVIEW.tail ? 'TAIL' : 'PAUSED'; }
}
function _lvScrollEnd(){ if(LOGVIEW.body) LOGVIEW.body.scrollTop = LOGVIEW.body.scrollHeight; }

function renderLogView(){
  if(!LOGVIEW.open) return;
  const rows = LOG.ring().filter(_lvMatch);
  const frag = document.createDocumentFragment();
  for(let i=0;i<rows.length;i++) frag.appendChild(_lvRow(rows[i]));
  LOGVIEW.body.innerHTML = '';
  LOGVIEW.body.appendChild(frag);
  _lvCount(rows.length);
  if(LOGVIEW.tail) _lvScrollEnd();
}
function _lvCount(shown){
  const c = $('lv-count');
  if(c) c.textContent = shown + ' / ' + LOG.ring().length;
}

/* New lines are appended, not re-rendered — at 20 Hz a full redraw per line would
   make the overlay itself the thing slowing the console down. Batched on a frame
   so a burst costs one layout. */
function _lvOnLine(line){
  if(!LOGVIEW.open) return;
  LOGVIEW.pending.push(line);
  if(LOGVIEW.flushTimer) return;
  LOGVIEW.flushTimer = setTimeout(()=>{
    LOGVIEW.flushTimer = null;
    const batch = LOGVIEW.pending.splice(0, LOGVIEW.pending.length);
    const frag = document.createDocumentFragment();
    let added = 0;
    for(let i=0;i<batch.length;i++){ if(_lvMatch(batch[i])){ frag.appendChild(_lvRow(batch[i])); added++; } }
    if(added){
      LOGVIEW.body.appendChild(frag);
      // Keep the DOM bounded independently of the ring.
      const max = (CONFIG.log && CONFIG.log.viewMaxRows) || 1200;
      while(LOGVIEW.body.childElementCount > max) LOGVIEW.body.removeChild(LOGVIEW.body.firstChild);
      if(LOGVIEW.tail) _lvScrollEnd();
    }
    _lvCount(LOGVIEW.body.childElementCount);
  }, 120);
}

function openLogView(){
  buildLogView();
  LOGVIEW.open = true;
  LOGVIEW.el.classList.add('show');
  if(!LOGVIEW.unsub) LOGVIEW.unsub = LOG.subscribe(_lvOnLine);
  // WHERE THE COMPLETE RECORD IS. The scrollback above is the bounded in-memory ring;
  // this one line is the only pointer to the copy that is NOT bounded, so it has to be
  // true. It used to be gated on `window.REC`, which is always undefined — recorder.js
  // declares `const REC`, and a top-level const is a global BINDING, not a property of
  // window — so the naming branch was dead code and every console, launcher or no
  // launcher, was told that nothing was being written to disk while the session log was
  // being written perfectly well.
  //
  // Three states, because there are three facts, and the middle one is what a bare "is
  // there a filename" test gets wrong: the recorder names its file at boot whether or
  // not anything is listening, so a NAME proves a queue and nothing else. `diskPath` is
  // what the launcher wrote back after actually accepting a batch, which is the only
  // evidence this console has that a file exists at all.
  const f = $('lv-file');
  if(f){
    const rec  = (typeof REC !== 'undefined') ? REC : null;
    const tail = '  (this view is the last ' + LOG.ring().length + ' lines held in memory)';
    f.textContent =
        (rec && rec.diskPath) ? ('full session log: ' + rec.diskPath + tail)
      : (rec && rec.diskFile) ? ('session log ' + rec.diskFile + ' is queued in this browser — '
                                 + 'nothing has reached the launcher yet' + tail)
      : 'no launcher: this view is in-memory only, nothing is being written to disk';
  }
  _lvSyncButtons();
  renderLogView();
}
function closeLogView(){
  LOGVIEW.open = false;
  if(LOGVIEW.el) LOGVIEW.el.classList.remove('show');
  if(LOGVIEW.unsub){ LOGVIEW.unsub(); LOGVIEW.unsub = null; }   // stop building rows nobody is reading
  LOGVIEW.pending.length = 0;
}
function toggleLogView(){ LOGVIEW.open ? closeLogView() : openLogView(); }

/* ============================================================================
   THE VEHICLE'S OWN LOG, PULLED ONTO THIS BUS

   Everything above shows lines the BROWSER produced, and the browser sits at the
   far end of the chain — where every failure in the boat looks identical: a field
   arrived null. "The MS5837 stopped answering after two consecutive raises",
   "leak probe WARN reads WET, 3 of 5 samples toward a latch", "the ballast group
   did not come up" are all written on the Pi, and until now they stayed on the Pi,
   readable only over ssh. An operator on a towpath with the sub in the water does
   not have ssh, and mid-dive is exactly when those lines are worth reading.

   PULLED, NOT PUSHED, and the choice matters:

     * it answers when the control socket is the thing that is broken, which is
       the case the log is most needed for;
     * the vehicle keeps a ring, so a console that attaches LATE gets the lines
       from before it existed — including the boot, which is where a sensor's life
       story starts;
     * nothing is consumed by being read, so a second console (a laptop beside the
       handheld) sees the same log rather than stealing lines from the first.

   Lines are re-emitted onto LOG so they land in the ring, in this overlay, and in
   the on-disk session log through LOG's sinks — the same three places every other
   line goes. They carry the VEHICLE's clock, printed as the vehicle stated it: the
   Pi has no RTC and runs days behind, and that difference is a finding, not
   something to quietly correct on arrival.
   ============================================================================ */
const LVFEED = {
  since:0, boot:null, fails:0, timer:null, started:false,
  gapMs:2000,      // while the vehicle is answering
  maxGapMs:30000,  // after it stops; the tether comes back, so this never gives up
};

function _lvVehicleLine(line){
  const text = 'VEHICLE ' + (line.tag || '?') + '  ' + _lvClock(line.t) + '  ' + line.msg;
  if(line.level === 'err')       LOG.err(text);
  else if(line.level === 'warn') LOG.warn(text);
  else                           LOG.state(text);
}

async function _lvPollVehicle(){
  let ok = false;
  try{
    const base = (typeof state !== 'undefined' && state.httpBase) ? state.httpBase : '';
    const r = await fetch(base + '/api/logs?since=' + LVFEED.since + '&limit=200', {cache:'no-store'});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    ok = true;
    // A DIFFERENT BOOT IS A DIFFERENT LOG. The Pi restarting resets the sequence
    // to 1, and a cursor left at 4000 would wait out the rest of the dive for a
    // line that will never be numbered that high.
    if(LVFEED.boot && j.boot && j.boot !== LVFEED.boot){
      LVFEED.since = 0;
      LOG.warn('VEHICLE restarted — its log starts again from the beginning');
    }
    LVFEED.boot = j.boot || LVFEED.boot;
    // THE LINES THAT WERE EVICTED BEFORE WE ASKED. The vehicle's ring is bounded,
    // so a console that was away long enough has a hole in its record — and a hole
    // nobody is told about is a record that reads as complete. Same rule as
    // everywhere else here: an absence is stated.
    if(typeof j.oldest === 'number' && j.oldest > LVFEED.since + 1){
      LOG.warn('VEHICLE log: ' + (j.oldest - LVFEED.since - 1) + ' earlier line(s) had already been evicted '
             + 'from the vehicle’s ring' + (LVFEED.since ? ' while this console was away' : ' before this console '
             + 'connected') + ' — they exist only in the Pi’s own journal');
      LVFEED.since = j.oldest - 1;
    }
    const lines = j.lines || [];
    for(let i=0;i<lines.length;i++) _lvVehicleLine(lines[i]);
    if(typeof j.next === 'number') LVFEED.since = Math.max(LVFEED.since, j.next);
  }catch(e){
    // No vehicle on this origin (disk mode, GitHub Pages), or the tether is down.
    // Neither is worth a line per attempt — the link's own status row already says
    // the sub is unreachable, and this must not become the noise it exists to cure.
  }
  LVFEED.fails = ok ? 0 : LVFEED.fails + 1;
  const gap = ok ? LVFEED.gapMs : Math.min(LVFEED.maxGapMs, LVFEED.gapMs * Math.pow(2, Math.min(4, LVFEED.fails)));
  LVFEED.timer = setTimeout(_lvPollVehicle, gap);
}

/* Started from here rather than from main.js's boot sequence because these lines
   have to be collected whether or not anyone has opened the overlay: the point is
   that a question about what the vehicle did five minutes ago is answerable
   without having prepared for it. The first poll waits for the boot to have
   resolved which host the vehicle is on (core.js writes state.httpBase). */
function startVehicleLogTail(){
  if(LVFEED.started) return;
  LVFEED.started = true;
  LVFEED.timer = setTimeout(_lvPollVehicle, 2500);
}
startVehicleLogTail();

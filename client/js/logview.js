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

function _lvRow(line){
  const d = new Date(line.t);
  const p = n => String(n).padStart(2,'0');
  const clock = p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())
              + '.' + String(d.getMilliseconds()).padStart(3,'0');
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
  const f = $('lv-file');
  if(f) f.textContent = (window.REC && REC.diskFile)
    ? ('full session log: navigation_logs/logs/' + REC.diskFile + '  (this view is the last '
       + LOG.ring().length + ' lines held in memory)')
    : 'no launcher: this view is in-memory only, nothing is being written to disk';
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

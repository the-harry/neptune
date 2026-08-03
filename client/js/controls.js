"use strict";
/* ============================================================================
   CONTROLS — on-screen (touch/mouse) bindings: the unified vertical control
   shared by the LEDs and ballast, the SURFACE hold-to-fire button, and the
   CONFIG / input-mapping modal.
   ============================================================================ */

// Unified vertical control shared by the LEDs and the ballast so they behave
// identically: DRAG the track to set the value (bottom = 0, top = 1), and the
// up/down arrows CLICK to step by `step` or HOLD to ramp at `rampPerS`.
// opts = { track, upBtn, downBtn, get, set, step, rampPerS }
function bindVerticalControl(opts){
  const {track, upBtn, downBtn, get, set, step, rampPerS} = opts;
  // Drag on the track → set directly.
  if(track){
    let dragging=false;
    const apply=(y)=>{ const r=track.getBoundingClientRect(); set(1-((y-r.top)/r.height)); };
    const yOf=e=> e.touches&&e.touches[0] ? e.touches[0].clientY : e.clientY;
    const down=(e)=>{ dragging=true; apply(yOf(e)); vibrate(6); e.preventDefault(); };
    const move=(e)=>{ if(dragging){ apply(yOf(e)); e.preventDefault(); } };
    const up=()=>{ dragging=false; };
    track.addEventListener('mousedown', down);
    track.addEventListener('touchstart', down, {passive:false});
    window.addEventListener('mousemove', move);
    window.addEventListener('touchmove', move, {passive:false});
    window.addEventListener('mouseup', up);
    window.addEventListener('touchend', up);
  }
  // Arrow: tap = one step; press-and-hold (> CONFIG.arrowHoldDelayMs) = ramp.
  const bindArrow=(btn,dir)=>{
    if(!btn) return;
    let raf=null, startT=0, ramping=false, lastT=0;
    const HOLD_DELAY=CONFIG.arrowHoldDelayMs;
    const stop=()=>{ if(raf) cancelAnimationFrame(raf); raf=null; ramping=false; };
    const loop=(now)=>{
      if(now-startT>=HOLD_DELAY){
        if(!ramping){ ramping=true; lastT=now; }
        const dt=(now-lastT)/1000; lastT=now;
        set(get()+dir*rampPerS*dt);
      }
      raf=requestAnimationFrame(loop);
    };
    const begin=(e)=>{ e.preventDefault(); if(raf) return; set(get()+dir*step); vibrate(6); startT=performance.now(); raf=requestAnimationFrame(loop); };
    btn.addEventListener('mousedown', begin); btn.addEventListener('touchstart', begin, {passive:false});
    ['mouseup','mouseleave','touchend','touchcancel'].forEach(ev=>btn.addEventListener(ev, stop));
  };
  bindArrow(upBtn, +1); bindArrow(downBtn, -1);
}

// SURFACE is an emergency action: press-and-HOLD to fire (guards against stray
// taps). A progress fill grows over CONFIG.surfaceHoldMs; release early cancels.
function bindSurfaceHold(){
  const btn=$('btn-surface'), fill=$('surface-fill'), hint=$('surface-hint');
  let raf=null, start=0, fired=false;
  const reset=()=>{ if(raf) cancelAnimationFrame(raf); raf=null; fill.style.width='0%'; if(hint) hint.textContent='HOLD'; };
  const tick=(now)=>{
    const p=Math.min(1,(now-start)/CONFIG.surfaceHoldMs);
    fill.style.width=(p*100)+'%';
    if(hint) hint.textContent = p<1 ? 'HOLD' : 'GO';
    if(p>=1 && !fired){ fired=true; surface(); vibrate(40); reset(); return; }
    raf=requestAnimationFrame(tick);
  };
  const begin=(e)=>{ e.preventDefault(); if(raf) return; fired=false; start=performance.now(); vibrate(8); raf=requestAnimationFrame(tick); };
  const end=()=>{ if(!fired) reset(); fired=false; };
  btn.addEventListener('mousedown', begin); btn.addEventListener('touchstart', begin, {passive:false});
  ['mouseup','mouseleave','touchend','touchcancel'].forEach(ev=>btn.addEventListener(ev,end));
}

function bindOnScreen(){
  // LEDs: round button toggles; arrows step/ramp brightness; track drags brightness.
  $('btn-light-green').addEventListener('click', ()=>toggleLight('green'));
  $('btn-light-white').addEventListener('click', ()=>toggleLight('white'));
  bindVerticalControl({ track:$('track-green'), upBtn:$('led-green-up'), downBtn:$('led-green-down'),
    get:()=>state.lights.green.level, set:v=>setLightLevel('green',v), step:CONFIG.ledStep, rampPerS:CONFIG.ledRampPerS });
  bindVerticalControl({ track:$('track-white'), upBtn:$('led-white-up'), downBtn:$('led-white-down'),
    get:()=>state.lights.white.level, set:v=>setLightLevel('white',v), step:CONFIG.ledStep, rampPerS:CONFIG.ledRampPerS });
  // Ballast: identical interaction — arrows step/ramp, track drags — sets the
  // commanded target level; the send-loop chases it with fill/empty/hold.
  bindVerticalControl({ track:$('ballast-track'), upBtn:$('btn-ballast-fill'), downBtn:$('btn-ballast-empty'),
    get:()=>state.ballastTargetCmd, set:v=>{ state.ballastTargetCmd=clamp(v,0,1); }, step:CONFIG.ballastStep, rampPerS:CONFIG.ballastRampPerS });
  bindSurfaceHold();       // top-bar SURFACE emergency (hold to fire)
  $('btn-config').addEventListener('click', openMapper);   // CONFIG (rail) opens the config / input-map menu
}

/* ============================================================================
   CONFIG / INPUT MAPPER modal — pick an action, then press any button/key to
   bind it. Captures gamepad buttons (any index) and keyboard keys; a captured
   input REPLACES the existing binding of the same type. Persisted to localStorage.
   ============================================================================ */
function startLearn(action){
  state.learn.active=true; state.learn.action=action;
  // Snapshot already-held pad buttons so we don't capture one that's down.
  const gp=currentPad(); const base={};
  if(gp){ for(let i=0;i<gp.buttons.length;i++){ if(padHeld(gp,i)) base[i]=true; } }
  state.learn.padBaseline=base;
  LOG.map('listening — press a button/key to bind:', action);
  updateMapperUI();
}
function cancelLearn(){
  if(!state.learn.active) return;
  LOG.map('mapping cancelled for', state.learn.action);
  state.learn.active=false; state.learn.action=null;
  updateMapperUI();
}
function captureBinding(input){
  const action=state.learn.action;
  state.learn.active=false; state.learn.action=null;
  if(!action){ return; }
  const list=state.bindings[action] || [];
  const kept=list.filter(b=>b.type!==input.type);   // replace same-type binding
  kept.push(input);
  state.bindings[action]=kept;
  state.actionPrev[action]=true;                     // avoid instant re-fire from the capture press
  saveBindings();
  LOG.map('bound', action, '->', bindingLabel(input), '| now:', bindingsText(action));
  updateMapperUI();
}
function clearBinding(action){
  state.bindings[action]=[]; saveBindings();
  LOG.map('cleared', action); updateMapperUI();
}
function resetBindings(){
  state.bindings={};
  for(const a in DEFAULT_BINDINGS){ state.bindings[a]=DEFAULT_BINDINGS[a].map(x=>({...x})); }
  saveBindings(); LOG.map('reset all bindings to defaults'); updateMapperUI();
}
// Per-frame: live raw-input monitor + gamepad-button capture while learning.
function pollLearn(){
  const gp=currentPad();
  if(state.mapperOpen){
    const mon=$('mapper-raw');
    if(mon){
      let pressed=[];
      if(gp){ for(let i=0;i<gp.buttons.length;i++){ if(padHeld(gp,i)) pressed.push(i); } }
      const axesTxt = gp && gp.axes ? Array.prototype.map.call(gp.axes,v=>v.toFixed(2)).join(', ') : '(no pad)';
      mon.textContent = 'pad buttons down: ['+pressed.join(', ')+']    axes: ['+axesTxt+']';
    }
  }
  if(state.learn.active && gp){
    for(let i=0;i<gp.buttons.length;i++){
      const down=padHeld(gp,i);
      if(down && !state.learn.padBaseline[i]){ captureBinding({type:'pad',index:i}); return; }
      if(!down) delete state.learn.padBaseline[i]; // a released baseline button becomes capturable
    }
  }
}
function updateMapperUI(){
  const list=$('mapper-list'); if(!list) return;
  list.innerHTML='';
  ACTION_ORDER.forEach(action=>{
    const learning = state.learn.active && state.learn.action===action;
    const row=document.createElement('div');
    row.className='mp-row'+(learning?' learning':'');
    const name=document.createElement('div'); name.className='mp-name'; name.textContent=ACTIONS[action].label;
    const bind=document.createElement('div'); bind.className='mp-bind';
    bind.textContent = learning ? '● press a button or key…  (Esc = cancel)' : bindingsText(action);
    const mapBtn=document.createElement('button'); mapBtn.className='mp-btn'; mapBtn.textContent=learning?'CANCEL':'MAP';
    mapBtn.addEventListener('click', ()=> learning ? cancelLearn() : startLearn(action));
    const clrBtn=document.createElement('button'); clrBtn.className='mp-btn'; clrBtn.textContent='CLEAR';
    clrBtn.addEventListener('click', ()=> clearBinding(action));
    row.appendChild(name); row.appendChild(bind); row.appendChild(mapBtn); row.appendChild(clrBtn);
    list.appendChild(row);
  });
}
function openMapper(){ state.mapperOpen=true; const hi=$('cfg-host'); if(hi) hi.value=state.host||''; $('mapper-modal').classList.add('show'); updateMapperUI(); LOG.map('config opened'); }
function closeMapper(){ if(state.learn.active) cancelLearn(); state.mapperOpen=false; $('mapper-modal').classList.remove('show'); LOG.map('mapper closed'); }
function applyHost(){
  const inp=$('cfg-host'); if(!inp) return;
  const h=inp.value.trim();
  try{
    if(h===''){ localStorage.removeItem('rov_host'); location.search=''; }
    else { location.search='?host='+encodeURIComponent(h); }
  }catch(e){}
}
function buildMapper(){
  // Opened from the CONFIG button in the right rail (no separate chip needed).
  const modal=document.createElement('div');
  modal.id='mapper-modal';
  modal.innerHTML =
    '<div class="mp-card">'+
      '<div class="mp-head">'+
        '<span class="font-headline-sm text-headline-sm text-primary font-bold">CONFIG</span>'+
        '<button id="mapper-close" class="mp-btn">CLOSE</button>'+
      '</div>'+
      '<div style="display:flex;align-items:center;gap:8px;padding:12px 20px 4px">'+
        '<span class="font-label-caps text-label-caps text-on-surface-variant" style="flex:0 0 80px">BACKEND</span>'+
        '<input id="cfg-host" placeholder="same-origin (e.g. 192.168.1.10:8000)" '+
          'style="flex:1;background:rgba(0,0,0,.3);border:1px solid var(--outline-variant);border-radius:.25rem;'+
          'color:var(--on-surface);font-family:var(--font-mono);font-size:12px;padding:6px 8px;outline:none">'+
        '<button id="cfg-host-apply" class="mp-btn">APPLY</button>'+
      '</div>'+
      '<div class="font-label-caps text-[10px] text-primary/50 uppercase tracking-widest" style="padding:10px 20px 0">Camera '+
        '<span id="cfg-surfaced-hint" class="text-error" style="text-transform:none;letter-spacing:0;font-weight:400">(locked in-dive)</span></div>'+
      '<div style="padding:6px 20px 2px;display:flex;align-items:center;gap:8px">'+
        '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-family:var(--font-sans);font-size:11px;color:var(--on-surface-variant)">'+
          '<input type="checkbox" id="cfg-surfaced"> SURFACED — unlock config &amp; files (interrupts video)'+
        '</label>'+
      '</div>'+
      '<div id="cfg-camera" style="padding:2px 20px"></div>'+
      '<div class="font-label-caps text-[10px] text-primary/50 uppercase tracking-widest" style="padding:6px 20px 0">Files</div>'+
      '<div id="cfg-files" style="padding:4px 20px;max-height:200px;overflow-y:auto"></div>'+
      '<div class="font-label-caps text-[10px] text-primary/50 uppercase tracking-widest" style="padding:10px 20px 0">Input mapping</div>'+
      '<div class="mp-raw" id="mapper-raw">move a stick or press buttons to see raw input…</div>'+
      '<div class="mp-list" id="mapper-list"></div>'+
      '<div class="mp-foot">'+
        '<span class="font-label-caps text-[10px] text-on-surface-variant" style="flex:1;line-height:15px">'+
          'ROG Ally back paddles show up as extra pad buttons or as keys — watch the raw monitor, then hit MAP and press one.'+
        '</span>'+
        '<button id="mapper-reset" class="mp-btn">RESET DEFAULTS</button>'+
      '</div>'+
    '</div>';
  document.body.appendChild(modal);
  $('mapper-close').addEventListener('click', closeMapper);
  $('mapper-reset').addEventListener('click', resetBindings);
  $('cfg-host-apply').addEventListener('click', applyHost);
  $('cfg-host').addEventListener('keydown', (e)=>{ if(e.key==='Enter') applyHost(); });
  // Camera config panel + file browser are wired by camera.js (initCamera).
  // Click backdrop (outside the card) to close
  modal.addEventListener('click', (e)=>{ if(e.target===modal) closeMapper(); });
  updateMapperUI();
}

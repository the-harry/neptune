"use strict";
/* ============================================================================
   INPUT — Gamepad API (RAF-polled) with full keyboard fallback. Movement axes
   come from the sticks (or WASD/arrows); discrete/hold actions resolve through
   the remappable ACTION model so any pad button or key can drive them.
   ============================================================================ */
window.addEventListener('gamepadconnected', (e)=>{
  state.gamepadIndex=e.gamepad.index;
  LOG.input('gamepad connected: index='+e.gamepad.index, '"'+e.gamepad.id+'"', e.gamepad.buttons.length+' buttons', e.gamepad.axes.length+' axes');
});
window.addEventListener('gamepaddisconnected', (e)=>{
  if(state.gamepadIndex===e.gamepad.index){ state.gamepadIndex=null; }
  LOG.input('gamepad disconnected: index='+e.gamepad.index);
});

/* ---- Remappable ACTION model ------------------------------------
   Every discrete/hold action resolves through state.bindings, so ANY
   input (gamepad button OR keyboard key) can be assigned via the mapper
   — including the ROG Ally back paddles, whatever index/key they emit.
   Movement axes (throttle/steer/pan/tilt) stay on the sticks + WASD/arrows.
   ---------------------------------------------------------------- */
const ACTIONS = {
  arm_toggle:         { label:'Arm / Disarm',       kind:'edge', run:()=>toggleArm() },
  estop:              { label:'E-STOP',             kind:'edge', run:()=>eStop() },
  // NOTE: SURFACE has no single-button binding on purpose (dangerous) — it fires ONLY from the
  // UI press-and-hold or the two-paddle F9+F10 hold (surfaceComboTick). See CONFIG.surfaceCombo*.
  magnet_toggle:      { label:'Magnet toggle',      kind:'edge', run:()=>toggleMagnet() },
  light_green_toggle: { label:'Green light toggle', kind:'edge', run:()=>toggleLight('green') },
  light_white_toggle: { label:'White light toggle', kind:'edge', run:()=>toggleLight('white') },
  cam_record_toggle:  { label:'Camera REC toggle',  kind:'edge', run:()=>{ if(typeof camRecordToggle==='function') camRecordToggle(); } },
  cam_capture:        { label:'Camera PICTURE',     kind:'edge', run:()=>{ if(typeof camCapture==='function') camCapture(); } },
  sim_leak_test:      { label:'Leak test (sim)',    kind:'edge', run:()=>{ state.simLeak=!state.simLeak; LOG.input('sim leak ->', state.simLeak); } },
  ballast_fill:       { label:'Ballast FILL (hold)',  kind:'hold' },
  ballast_empty:      { label:'Ballast EMPTY (hold)', kind:'hold' },
  // Dedicated, independent brightness — up/down = WHITE, left/right = GREEN (still toggled by LB/RB)
  light_white_up:     { label:'White brighter (hold)', kind:'hold' },
  light_white_down:   { label:'White dimmer (hold)',   kind:'hold' },
  light_green_up:     { label:'Green brighter (hold)', kind:'hold' },
  light_green_down:   { label:'Green dimmer (hold)',   kind:'hold' }
};
const ACTION_ORDER = ['arm_toggle','estop','magnet_toggle','light_green_toggle',
  'light_white_toggle','cam_record_toggle','cam_capture','ballast_fill','ballast_empty',
  'light_white_up','light_white_down','light_green_up','light_green_down','sim_leak_test'];
// Defaults reproduce the original mapping (gamepad + documented key).
const DEFAULT_BINDINGS = {
  arm_toggle:         [{type:'pad',index:3},{type:'key',code:'Space'}],
  estop:              [{type:'pad',index:1},{type:'key',code:'KeyX'},{type:'key',code:'Escape'}],
  magnet_toggle:      [{type:'pad',index:0},{type:'key',code:'KeyM'}],
  light_green_toggle: [{type:'pad',index:4},{type:'key',code:'KeyG'}],   // LB
  light_white_toggle: [{type:'pad',index:5},{type:'key',code:'KeyH'}],   // RB
  cam_record_toggle:  [{type:'pad',index:8},{type:'key',code:'KeyR'}],   // Select / View → REC
  cam_capture:        [{type:'pad',index:9},{type:'key',code:'KeyC'}],   // Pause / Menu  → PIC
  ballast_fill:       [{type:'pad',index:7},{type:'key',code:'KeyQ'}],
  ballast_empty:      [{type:'pad',index:6},{type:'key',code:'KeyE'}],
  // D-pad: up/down → WHITE, left/right → GREEN (independent of which was last toggled)
  light_white_up:     [{type:'pad',index:12},{type:'key',code:'BracketRight'}],   // D-pad ↑
  light_white_down:   [{type:'pad',index:13},{type:'key',code:'BracketLeft'}],    // D-pad ↓
  light_green_up:     [{type:'pad',index:15},{type:'key',code:'Equal'}],          // D-pad →
  light_green_down:   [{type:'pad',index:14},{type:'key',code:'Minus'}],          // D-pad ←
  sim_leak_test:      [{type:'key',code:'KeyL'}]
};
// Bump when the action set / defaults change so stale saved maps (e.g. an old
// single-key SURFACE binding, or a removed action) are dropped and re-seeded clean.
const BINDINGS_VERSION = 2;
function loadBindings(){
  let saved=null;
  try{
    if(localStorage.getItem('rov_bindings_v')===String(BINDINGS_VERSION)){
      const raw=localStorage.getItem('rov_bindings'); if(raw) saved=JSON.parse(raw);
    } else { LOG.map('bindings schema changed → re-seeding defaults'); }
  }catch(e){}
  state.bindings={};
  for(const a in DEFAULT_BINDINGS){ state.bindings[a]=DEFAULT_BINDINGS[a].map(x=>({...x})); }
  if(saved){ for(const a in saved){ if(ACTIONS[a]) state.bindings[a]=saved[a]; } }
  saveBindings();
  LOG.map('bindings loaded', state.bindings);
}
function saveBindings(){
  try{ localStorage.setItem('rov_bindings', JSON.stringify(state.bindings)); localStorage.setItem('rov_bindings_v', String(BINDINGS_VERSION)); }catch(e){}
}
function bindingLabel(b){
  if(!b) return '--';
  if(b.type==='key') return 'Key:'+b.code;
  if(b.type==='pad') return 'Pad#'+b.index;
  return '?';
}
function bindingsText(action){
  const list=state.bindings[action]||[];
  return list.length ? list.map(bindingLabel).join('   +   ') : '— unbound —';
}

/* ---- input evaluation ---- */
function currentPad(){
  const pads=navigator.getGamepads?navigator.getGamepads():[];
  return state.gamepadIndex!=null ? pads[state.gamepadIndex] : null;
}
function padHeld(gp,i){
  const b=gp&&gp.buttons&&gp.buttons[i]; if(!b) return false;
  return (b.value!==undefined) ? b.value>0.5 : b.pressed;  // triggers threshold at 0.5
}
function inputActive(b){
  if(!b) return false;
  if(b.type==='key') return state.keys.has(b.code);
  if(b.type==='pad') return padHeld(currentPad(), b.index);
  return false;
}
function actionHeld(action){
  const list=state.bindings[action]||[];
  for(let i=0;i<list.length;i++){ if(inputActive(list[i])) return true; }
  return false;
}
/* SURFACE combo: BOTH paddles (F9+F10) held for surfaceComboHoldMs. Mirrors the UI
   press-and-hold — including driving the SURFACE button's fill so the operator sees
   the countdown — so the dangerous emergency can never fire on a single tap. */
function surfaceComboTick(){
  const keys=CONFIG.surfaceComboKeys||[];
  const both = keys.length>0 && keys.every(k=>state.keys.has(k));
  const fill=$('surface-fill');
  if(both){
    if(!state.surfaceComboStart) state.surfaceComboStart=performance.now();
    const pct=Math.min(1, (performance.now()-state.surfaceComboStart)/CONFIG.surfaceComboHoldMs);
    if(fill) fill.style.width=(pct*100)+'%';
    if(!state.surfaceComboFired && pct>=1){ state.surfaceComboFired=true; LOG.input('SURFACE combo fired (paddles held)'); surface(); if(fill) fill.style.width='0%'; }
  } else if(state.surfaceComboStart || state.surfaceComboFired){
    state.surfaceComboStart=0; state.surfaceComboFired=false; if(fill) fill.style.width='0%';
  }
}
function computeInput(dt){
  const src = state.gamepadIndex!=null ? 'gamepad' : 'keyboard';
  if(src!==state.source){ state.source=src; LOG.input('input source ->', src); }

  // --- movement axes: sticks when a pad is present (WASD/arrows if sticks idle) ---
  const dz=CONFIG.deadzone, k=state.keys, on=c=>k.has(c)?1:0;
  let throttle=0,steer=0,pan=0,tilt=0;
  const gp=currentPad();
  let stickLive=false;
  if(gp && gp.axes && gp.axes.length>=4){
    const ax=i=>{ const v=gp.axes[i]||0; return Math.abs(v)<dz?0:v; };
    throttle=-ax(1); steer=ax(0); pan=ax(2); tilt=-ax(3);
    stickLive=(Math.abs(throttle)+Math.abs(steer)+Math.abs(pan)+Math.abs(tilt))>0;
  }
  if(!stickLive){ // keyboard movement (fallback, or alongside an idle pad)
    throttle=on('KeyW')-on('KeyS'); steer=on('KeyD')-on('KeyA');
    pan=on('ArrowRight')-on('ArrowLeft'); tilt=on('ArrowUp')-on('ArrowDown');
  }

  // --- discrete + hold actions via bindings (suspended while learning) ---
  if(!state.learn.active){
    for(let n=0;n<ACTION_ORDER.length;n++){
      const action=ACTION_ORDER[n];
      if(ACTIONS[action].kind!=='edge') continue;
      const held=actionHeld(action);
      if(held && !state.actionPrev[action]) ACTIONS[action].run();
      state.actionPrev[action]=held;
    }
    // dedicated brightness holds — white and green move independently
    const r=CONFIG.lightLevelRateS*dt;
    if(actionHeld('light_white_up'))   adjustLight('white', +r);
    if(actionHeld('light_white_down')) adjustLight('white', -r);
    if(actionHeld('light_green_up'))   adjustLight('green', +r);
    if(actionHeld('light_green_down')) adjustLight('green', -r);
    // SURFACE via BOTH paddles (F9+F10) held for surfaceComboHoldMs — same deliberate hold as the UI
    surfaceComboTick();
  }

  // --- ballast ---
  // Slew the COMMANDED target toward what the operator set (raw), capped at
  // CONFIG.ballastSlewPerS. This is the key hardware-safety step: a fast drag or a
  // big jump is applied gradually so the syringe stepper can keep up, and the
  // fill/empty commands the send-loop emits stay smooth.
  {
    const d = clamp(state.ballastTargetRaw - state.ballastTargetCmd,
                    -CONFIG.ballastSlewPerS*dt, CONFIG.ballastSlewPerS*dt);
    state.ballastTargetCmd = clamp(state.ballastTargetCmd + d, 0, 1);
  }

  // Gamepad/key triggers = momentary direct fill/empty (they pin both raw + cmd to
  // the current level so it holds wherever you release). Otherwise the send-loop
  // chases the slewed ballastTargetCmd by emitting fill/empty/hold.
  let ballast='hold';
  const cur=state.ballastLevel;
  if(actionHeld('ballast_fill')){ ballast='fill'; state.ballastTargetRaw=state.ballastTargetCmd=cur; }
  else if(actionHeld('ballast_empty')){ ballast='empty'; state.ballastTargetRaw=state.ballastTargetCmd=cur; }
  else {
    const db=CONFIG.ballastDeadband;
    if(cur < state.ballastTargetCmd-db) ballast='fill';
    else if(cur > state.ballastTargetCmd+db) ballast='empty';
  }

  const ni={ throttle:clamp(throttle,-1,1), steer:clamp(steer,-1,1),
             pan:clamp(pan,-1,1), tilt:clamp(tilt,-1,1), ballast:ballast };

  // --- §3 map-open safety: a submarine can't be "paused". While the map is expanded,
  // hold an all-stop (throttle+steer zeroed); the moment the operator commands thrust or
  // steer past the deadzone, collapse the map and hand control back THIS SAME tick.
  if(typeof MAP!=='undefined' && MAP.expanded){
    if((Math.abs(ni.throttle)+Math.abs(ni.steer)) > CONFIG.deadzone){ collapseMap(); }
    else if(CONFIG.map.allStopOnExpand){ ni.throttle=0; ni.steer=0; }
  }

  if(ni.ballast!==state.input.ballast) LOG.input('ballast ->', ni.ballast);
  state.input=ni;
}

/* ---- keyboard: maintain held-set + feed the mapper. Discretes are
   resolved from bindings in computeInput (no per-key switch here). ---- */
const MOVE_KEYS=new Set(['KeyW','KeyS','KeyA','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','KeyQ','KeyE','BracketLeft','BracketRight','Equal','Minus','Space']);
function typingInField(e){
  const t=e.target;
  return t && (t.tagName==='INPUT' || t.tagName==='TEXTAREA' || t.isContentEditable);
}
window.addEventListener('keydown', (e)=>{
  if(typingInField(e)) return;            // don't hijack typing in the config host field
  if(state.learn.active){                 // capture a key for the mapper
    e.preventDefault();
    if(e.code==='Escape') cancelLearn(); else captureBinding({type:'key',code:e.code});
    return;
  }
  // swallow the default for movement/brightness keys and the surface paddles (F9/F10)
  if(MOVE_KEYS.has(e.code) || (CONFIG.surfaceComboKeys||[]).indexOf(e.code)>=0) e.preventDefault();
  state.keys.add(e.code);
});
window.addEventListener('keyup', (e)=>{ state.keys.delete(e.code); });

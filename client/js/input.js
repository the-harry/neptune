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
  surface:            { label:'Surface',            kind:'edge', run:()=>surface() },
  magnet_toggle:      { label:'Magnet toggle',      kind:'edge', run:()=>toggleMagnet() },
  light_green_toggle: { label:'Green light toggle', kind:'edge', run:()=>toggleLight('green') },
  light_white_toggle: { label:'White light toggle', kind:'edge', run:()=>toggleLight('white') },
  sim_leak_test:      { label:'Leak test (sim)',    kind:'edge', run:()=>{ state.simLeak=!state.simLeak; LOG.input('sim leak ->', state.simLeak); } },
  ballast_fill:       { label:'Ballast FILL (hold)',  kind:'hold' },
  ballast_empty:      { label:'Ballast EMPTY (hold)', kind:'hold' },
  brightness_up:      { label:'Brightness + (hold)',  kind:'hold' },
  brightness_down:    { label:'Brightness − (hold)', kind:'hold' }
};
const ACTION_ORDER = ['arm_toggle','estop','surface','magnet_toggle','light_green_toggle',
  'light_white_toggle','ballast_fill','ballast_empty','brightness_up','brightness_down','sim_leak_test'];
// Defaults reproduce the original mapping (gamepad + documented key).
const DEFAULT_BINDINGS = {
  arm_toggle:         [{type:'pad',index:3},{type:'key',code:'Space'}],
  estop:              [{type:'pad',index:1},{type:'key',code:'KeyX'},{type:'key',code:'Escape'}],
  surface:            [{type:'pad',index:2},{type:'key',code:'KeyP'}],
  magnet_toggle:      [{type:'pad',index:0},{type:'key',code:'KeyM'}],
  light_green_toggle: [{type:'pad',index:4},{type:'key',code:'KeyG'}],
  light_white_toggle: [{type:'pad',index:5},{type:'key',code:'KeyH'}],
  ballast_fill:       [{type:'pad',index:7},{type:'key',code:'KeyQ'}],
  ballast_empty:      [{type:'pad',index:6},{type:'key',code:'KeyE'}],
  brightness_up:      [{type:'pad',index:12},{type:'key',code:'BracketRight'}],
  brightness_down:    [{type:'pad',index:13},{type:'key',code:'BracketLeft'}],
  sim_leak_test:      [{type:'key',code:'KeyL'}]
};
function loadBindings(){
  let saved=null;
  try{ const raw=localStorage.getItem('rov_bindings'); if(raw) saved=JSON.parse(raw); }catch(e){}
  state.bindings={};
  for(const a in DEFAULT_BINDINGS){ state.bindings[a]=DEFAULT_BINDINGS[a].map(x=>({...x})); }
  if(saved){ for(const a in saved){ if(ACTIONS[a]) state.bindings[a]=saved[a]; } }
  LOG.map('bindings loaded', state.bindings);
}
function saveBindings(){
  try{ localStorage.setItem('rov_bindings', JSON.stringify(state.bindings)); }catch(e){}
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
    if(actionHeld('brightness_up'))   adjustLight(state.lastLight, +CONFIG.lightLevelRateS*dt);
    if(actionHeld('brightness_down')) adjustLight(state.lastLight, -CONFIG.lightLevelRateS*dt);
  }

  // --- ballast ---
  // Gamepad/key triggers = momentary direct fill/empty (they also drag the
  // commanded target along so it "holds" wherever you release). Otherwise the
  // send-loop chases state.ballastTargetCmd (set by the arrows/drag) by emitting
  // fill/empty/hold — same API command, just automated toward a level.
  let ballast='hold';
  const cur=state.ballastLevel;
  if(actionHeld('ballast_fill')){ ballast='fill'; state.ballastTargetCmd=cur; }
  else if(actionHeld('ballast_empty')){ ballast='empty'; state.ballastTargetCmd=cur; }
  else {
    const db=CONFIG.ballastDeadband;
    if(cur < state.ballastTargetCmd-db) ballast='fill';
    else if(cur > state.ballastTargetCmd+db) ballast='empty';
  }

  const ni={ throttle:clamp(throttle,-1,1), steer:clamp(steer,-1,1),
             pan:clamp(pan,-1,1), tilt:clamp(tilt,-1,1), ballast:ballast };
  if(ni.ballast!==state.input.ballast) LOG.input('ballast ->', ni.ballast);
  state.input=ni;
}

/* ---- keyboard: maintain held-set + feed the mapper. Discretes are
   resolved from bindings in computeInput (no per-key switch here). ---- */
const MOVE_KEYS=new Set(['KeyW','KeyS','KeyA','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','KeyQ','KeyE','BracketLeft','BracketRight','Space']);
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
  if(MOVE_KEYS.has(e.code)) e.preventDefault();
  state.keys.add(e.code);
});
window.addEventListener('keyup', (e)=>{ state.keys.delete(e.code); });

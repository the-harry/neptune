"use strict";
/* ============================================================================
   RENDER — local simulation (used only with no real telemetry), the normalized
   view builder, and all telemetry -> DOM rendering + status badges.
   ============================================================================ */

/* ---- SIMULATION: synthesize telemetry from live input so every gauge, lamp
   and readout animates with no server. Advances only in SIM mode. ---- */
function simulate(dt){
  const s=state, sim=CONFIG.sim;
  let b=s.input.ballast;
  if(Date.now()<s.surfaceUntil) b='empty'; // SURFACE command drains ballast
  if(b==='fill')  s.ballastLevel += sim.ballastRatePerS*dt;
  else if(b==='empty') s.ballastLevel -= sim.ballastRatePerS*dt;
  s.ballastLevel=clamp(s.ballastLevel,0,1);
  s.ballastTarget=s.ballastLevel;
  const depthTarget = s.ballastLevel*sim.maxDepthM;
  s.depth += (depthTarget - s.depth)*Math.min(1, dt*sim.depthLerp);
  s.pressure = sim.basePressurePsi + s.depth*sim.psiPerMeter;
  const c=s.input;
  s.left  = clamp(c.throttle + c.steer, -1, 1);
  s.right = clamp(c.throttle - c.steer, -1, 1);
  s.heading = (s.heading + c.steer*sim.headingRatePerS*dt + 360)%360;
  s.batteryV = Math.max(20.0, s.batteryV - sim.batteryDrainVPerS*dt);
}

/* Build a normalized "view" object the renderer consumes. */
function viewFromState(sim){
  const s=state;
  return {
    stale:false, sim:!!sim,
    armed:s.armed, left:s.left, right:s.right,
    ballastLevel:s.ballastLevel, ballastTarget:s.ballastTarget,
    depth:s.depth, pressure:s.pressure, heading:s.heading, batteryV:s.batteryV,
    cpuC:s.cpuC, ramPct:s.ramPct, diskGb:s.diskGb,
    magnet:s.magnet,
    green:s.lights.green.on, white:s.lights.white.on,
    greenLevel:s.lights.green.level, whiteLevel:s.lights.white.level,
    leak:(sim ? s.simLeak : (s.realTel&&s.realTel.leak)) || s.alarmLeak,
    linkMs:s.linkMs
  };
}

/* ---- TELEMETRY -> UI ---- */
// Icon SVGs for the light lamps (filled = on, outline = off)
const BULB_FILL='<svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 21c0 .5.4 1 1 1h4c.6 0 1-.5 1-1v-1H9v1zm3-19a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.1 1.7h5.2l.1-1.7c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 2z"/></svg>';
const BULB_LINE='<svg viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.2 1.2h5l.2-1.2c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 3z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M9.7 20h4.6M10.3 22h3.4"/></svg>';

function setText(el, txt, stale){
  if(stale){ el.textContent='--'; el.classList.add('is-stale'); }
  else { el.textContent=txt; el.classList.remove('is-stale'); }
}
function setThrust(el, v){
  const pct=Math.min(1,Math.abs(v))*50;
  el.style.width=pct+'%';
  if(v>=0){ el.style.left='50%'; el.style.right='auto'; }
  else    { el.style.right='50%'; el.style.left='auto'; }
}
function renderLightButton(which, on, level){
  const isGreen = which==='green';
  const btn=$('btn-light-'+which), icon=$('icon-light-'+which), gauge=$('gauge-'+which);
  const color = isGreen ? 'var(--tertiary)' : '#e9f9ff';
  const glow  = isGreen ? '#4dffa6' : 'rgba(190,240,255,.85)';
  if(on){
    btn.style.backgroundColor = 'color-mix(in srgb,'+color+' 30%,transparent)';
    btn.style.borderColor = color;
    btn.style.boxShadow = '0 0 12px '+glow;
    icon.style.color = color; icon.innerHTML=BULB_FILL;
    gauge.style.backgroundColor = color; gauge.style.boxShadow='0 0 8px '+glow;
  } else {
    btn.style.backgroundColor = 'var(--surface-variant)';
    btn.style.borderColor = 'var(--outline-variant)';
    btn.style.boxShadow = 'none';
    icon.style.color = 'var(--on-surface-variant)'; icon.innerHTML=BULB_LINE;
    gauge.style.backgroundColor = 'color-mix(in srgb,var(--on-surface-variant) 40%,transparent)';
    gauge.style.boxShadow='none';
  }
  gauge.style.height = Math.round(level*100)+'%';
}
// Armed/magnet no longer have HUD elements (kept as no-ops so callers stay simple;
// arm/disarm + magnet still work as gamepad/key actions and gate the thrusters).
function renderArmed(armed){ /* no armed indicator by request */ }
function renderMagnet(on){ /* no magnet indicator by request */ }

// LEAK: icon-only — green water-drop (normal) / red crossed drop (leak) + edge pulse.
const DROP_OK   = '<svg viewBox="0 0 24 24"><path fill="var(--tertiary)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/></svg>';
const DROP_LEAK = '<svg viewBox="0 0 24 24"><path fill="var(--error)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/><path stroke="var(--error)" stroke-width="2.4" stroke-linecap="round" d="M4 4l16 16"/><path stroke="#0c0118" stroke-width="1.1" stroke-linecap="round" d="M4 4l16 16"/></svg>';
function renderLeak(leak){
  const icon=$('leak-icon'), pulse=$('leak-pulse');
  if(icon) icon.innerHTML = leak ? DROP_LEAK : DROP_OK;
  if(icon) icon.style.filter = leak ? 'drop-shadow(0 0 6px var(--error))' : '';
  if(pulse) pulse.classList.toggle('on', !!leak);
}

let _prev={};
function renderUI(v){
  const stale=!!v.stale;
  // Numeric readouts (dashed when stale)
  setText($('battery-v'), (v.batteryV!=null?v.batteryV.toFixed(1):'--')+'V', stale);
  setText($('pressure-val'), (v.pressure!=null?v.pressure.toFixed(1):'--')+' PSI', stale);
  // Pi system metrics — only when the server provides them (placeholders in SIM).
  if(v.cpuC!=null)   setText($('cpu-c'),   Math.round(v.cpuC)+'°C', stale);
  if(v.ramPct!=null) setText($('ram-pct'), Math.round(v.ramPct)+'%', stale);
  if(v.diskGb!=null) setText($('disk-gb'), v.diskGb.toFixed(1)+' GB', stale);
  setText($('ballast-pct'), Math.round((v.ballastLevel||0)*100)+'%', stale);
  setText($('heading-val'), Math.round(v.heading||0)+'° '+headingCardinal(v.heading||0), stale);
  // Gauges / bars keep last position; only dim on stale.
  const dim = stale ? '0.45' : '1';
  $('ballast-fill').style.height = Math.round((v.ballastLevel||0)*100)+'%';
  $('ballast-fill').style.opacity = dim;
  // Commanded-target marker (where the arrows/drag set it); hidden when it matches actual.
  const tgt=clamp(state.ballastTargetCmd,0,1);
  const mark=$('ballast-target-mark');
  mark.style.bottom = (tgt*100)+'%';
  mark.style.opacity = (Math.abs(tgt-(v.ballastLevel||0))<0.02) ? '0' : '1';
  setThrust($('thrust-left'),  stale?0:(v.left||0));
  setThrust($('thrust-right'), stale?0:(v.right||0));
  // Lamps / toggles (only re-render on change to keep innerHTML swaps cheap)
  if(_prev.green!==v.green || _prev.greenLevel!==Math.round(v.greenLevel*100)){ renderLightButton('green', v.green, v.greenLevel); }
  if(_prev.white!==v.white || _prev.whiteLevel!==Math.round(v.whiteLevel*100)){ renderLightButton('white', v.white, v.whiteLevel); }
  if(_prev.armed!==v.armed) renderArmed(v.armed);
  if(_prev.magnet!==v.magnet) renderMagnet(v.magnet);
  if(_prev.leak!==!!v.leak) renderLeak(!!v.leak);
  // Link ms
  setText($('link-ms'), (v.linkMs!=null? v.linkMs+' ms':'-- ms'), false);
  _prev={green:v.green,greenLevel:Math.round(v.greenLevel*100),white:v.white,whiteLevel:Math.round(v.whiteLevel*100),armed:v.armed,magnet:v.magnet,leak:!!v.leak};
}

/* ---- Status chips (top-bar VIDEO indicator) ---- */
function setChip(id, cls, text){
  const chip=$(id); if(!chip) return;
  const dot=chip.querySelector('.hud-dot'), t=$(id+'-text');
  if(dot) dot.className='hud-dot '+cls;
  if(t) t.textContent=text;
}
// Video status is conveyed by the feed's own NO-FEED / RECONFIGURING overlay
// (the top-bar VIDEO chip was redundant and removed).
function updateBadges(){ /* no-op */ }

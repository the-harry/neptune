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
  gauge.style.height = Math.round(level*100)+'%';   // vertical fill (bottom→top)
}
// Armed/magnet no longer have HUD elements (kept as no-ops so callers stay simple;
// arm/disarm + magnet still work as gamepad/key actions and gate the thrusters).
function renderArmed(armed){ /* no armed indicator by request */ }
function renderMagnet(on){ /* no magnet indicator by request */ }

// LEAK: icon-only. OK = GREEN drop with a cross through it ("no water"); LEAK =
// RED plain drop ("water present"). Plus the full-screen edge pulse on leak.
const DROP_OK   = '<svg viewBox="0 0 24 24"><path fill="var(--tertiary)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/><path stroke="var(--tertiary)" stroke-width="2.4" stroke-linecap="round" d="M4 4l16 16"/><path stroke="#0c0118" stroke-width="1.1" stroke-linecap="round" d="M4 4l16 16"/></svg>';
const DROP_LEAK = '<svg viewBox="0 0 24 24"><path fill="var(--error)" d="M12 3s6 6.5 6 10a6 6 0 0 1-12 0c0-3.5 6-10 6-10z"/></svg>';
function renderLeak(leak){
  const icon=$('leak-icon'), pulse=$('leak-pulse');
  if(icon) icon.innerHTML = leak ? DROP_LEAK : DROP_OK;
  if(icon) icon.style.filter = leak ? 'drop-shadow(0 0 6px var(--error))' : '';
  if(pulse) pulse.classList.toggle('on', !!leak);
}

/* One compass point of the input dial. `v` is that direction's share of its axis:
   negative means the stick is going the other way, which reads 0 here — the opposite
   number is the one lighting up. */
const _numPrev={};
function setInputNum(id, v){
  const el=$(id); if(!el) return;
  const n=Math.round(Math.max(0, Math.min(1, v||0))*100);
  if(_numPrev[id]!==n){ _numPrev[id]=n; el.textContent=n; el.classList.toggle('live', n>0); }
}

/* ---- THE COLOUR LINK -------------------------------------------------------
   The map draws the dive track in twelve depth bands. The ballast fill and the
   Depth / Pressure / Ballast numbers now wear the same bands, so a glance at the
   rail and a glance at the track say the same thing in the same colour.

   WHAT MAY BE COLOURED IS NOT THE SAME IN BOTH MODES, and that is the point:

     SIM   one made-up number drives everything, so everything wears one colour,
           taken straight from the ballast input. Drag the slider and the whole
           console moves together - which is exactly what a simulation is.

     REAL  ballast is a commanded quantity and colours itself. Depth and pressure
           are MEASURED, and are coloured by their own sensor or not at all. They
           are never tinted from ballast: a sub descending on a full tank with a
           dead depth sensor would then show a deepening colour it did not earn,
           and the one symptom that gives the failure away would be the symptom we
           had painted over. Cyan-and-unchanging while the ballast fill turns
           purple IS the alarm.

   A reading older than staleTimeoutMs is dropped rather than believed, for the
   same reason the camera drops a stale AP sighting. */
function metricTints(v){
  // The ramp lives in map.js. If that file ever fails to load, the HUD keeps its
  // default colours instead of throwing sixty times a second and taking piloting
  // down with the map - subsystems fail alone (§3).
  if(v.stale || typeof rampColor!=='function') return {ballast:null, depth:null, pressure:null};
  // Ballast is coloured by the depth that much water BUYS, not by the fraction itself.
  // Straight 0..1 would look right and be wrong: the tank reaches 9 m while the ramp
  // saturates at 6, so a half-full tank would show band 6 while the track it is about
  // to draw shows band 9. Converting first is what makes them the same colour.
  const bal = _depthColor((v.ballastLevel||0) * ((CONFIG.sim&&CONFIG.sim.maxDepthM)||9));
  if(v.sim || state.mode==='sim') return {ballast:bal, depth:bal, pressure:bal};
  const fresh = (at)=> !!at && (Date.now()-at) < (CONFIG.staleTimeoutMs||1000);
  return {
    ballast:  bal,
    depth:    fresh(state.depthAt)    ? _depthColor(v.depth)       : null,
    pressure: fresh(state.pressureAt) ? pressureColor(v.pressure)  : null
  };
}
/* null puts the number back to its default look rather than picking a "neutral"
   colour, so an untinted readout is visibly the ABSENCE of a reading, not a
   twelfth-band value that happens to be grey. */
const _tint={};
function paintMetric(id, color, why){
  const el=$(id); if(!el || _tint[id]===color) return;
  _tint[id]=color;
  el.style.color = color || '';
  el.style.textShadow = color ? ('0 0 8px '+color) : '';
  if(why!==undefined) liveTitle(el, color ? '' : why);
}

let _prev={};
function renderUI(v){
  const stale=!!v.stale;
  // Numeric readouts (dashed when stale)
  setText($('battery-v'), (v.batteryV!=null?v.batteryV.toFixed(1):'--')+'V', stale);
  setText($('depth-val'), (v.depth!=null?v.depth.toFixed(1):'--')+' m', stale);
  setText($('pressure-val'), (v.pressure!=null?v.pressure.toFixed(1):'--')+' PSI', stale);
  // Pi system metrics are rendered by renderSystem() from /api/system, which is
  // independent of the vehicle link — see status.js. Nothing to do here.
  setText($('ballast-pct'), Math.round((v.ballastLevel||0)*100)+'%', stale);
  setText($('heading-val'), Math.round(v.heading||0)+'°', stale);   // compass bearing (degrees)
  // Gauges / bars keep last position; only dim on stale.
  const dim = stale ? '0.45' : '1';
  const fill=$('ballast-fill');
  fill.style.height = Math.round((v.ballastLevel||0)*100)+'%';
  fill.style.opacity = dim;
  // The liquid is the plunger, and it is the colour of the depth that much water buys.
  const tint = metricTints(v);
  if(_tint.fill !== tint.ballast){
    _tint.fill = tint.ballast;
    fill.style.background  = tint.ballast || 'var(--water)';
    fill.style.borderColor = tint.ballast || 'var(--water-edge)';
    // Tight, not a halo, and thrown UPWARD off the meniscus: an 8px bloom spilled a
    // whole band of colour through the empty barrel and the water level stopped
    // having an edge.
    fill.style.boxShadow   = '0 -1px 4px ' + (tint.ballast || 'var(--water)');
  }
  paintMetric('ballast-pct',  tint.ballast);
  paintMetric('depth-val',    tint.depth,
    'no depth reading is arriving, so this number is NOT tracking a sensor');
  paintMetric('pressure-val', tint.pressure,
    'no pressure reading is arriving, so this number is NOT tracking a sensor');
  // Target marker = where the operator SET it (the goal). The fill (actual level)
  // chases it smoothly via the slewed command. The fill grows UP from the tip.
  const tgt=clamp(state.ballastTargetRaw,0,1);
  const mark=$('ballast-target-mark');
  mark.style.bottom = (tgt*100)+'%';   // measured from the tip, like the liquid
  mark.style.opacity = (Math.abs(tgt-(v.ballastLevel||0))<0.01) ? '0' : '1';
  // SONAR: plot the movement input as a vector (steer = x, throttle = y-up).
  // Scale 62 keeps full diagonal (~88) inside the outer ring (92).
  const R=62, sx=(state.input.steer||0)*R, sy=-(state.input.throttle||0)*R;
  const vec=$('sonar-vec'), dot=$('sonar-dot');
  if(vec){ vec.setAttribute('x2', sx.toFixed(1)); vec.setAttribute('y2', sy.toFixed(1)); }
  if(dot){ dot.setAttribute('cx', sx.toFixed(1)); dot.setAttribute('cy', sy.toFixed(1)); }
  // Four directional numbers, 0-100, one per compass point. Each shows only its OWN
  // half of the axis, so "how hard am I pushing that way" is read straight off the
  // side you are pushing rather than decoded from a signed percentage.
  setInputNum('in-fwd',   state.input.throttle);
  setInputNum('in-rev',  -state.input.throttle);
  setInputNum('in-right', state.input.steer);
  setInputNum('in-left',  -state.input.steer);
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

/* ============================================================================
   PI SYSTEM HEALTH — real readings from /api/system (api/sysinfo.py).

   Deliberately separate from vehicle telemetry: the Pi's own CPU/RAM/disk and
   the state of its two network interfaces are meaningful even when the ROV
   hardware is simulated or the camera is unplugged.

   A missing probe arrives as null and renders as "--". It is never shown as 0 —
   "CPU 0 deg C" reads as a real measurement and hides the fault, which is exactly
   how the old psutil-less path made every gauge look plausible and be wrong.
   ============================================================================ */
function renderSystem(s){
  const dash = (el)=>{ if(el){ el.textContent='--'; el.classList.add('is-stale'); } };
  const put  = (el, txt)=>{ if(el){ el.textContent=txt; el.classList.remove('is-stale'); } };

  const ids = ['cpu-c','cpu-pct','ram-pct','disk-gb','net-eth'];
  if(!s || s.ok===false){ ids.forEach(id=>dash($(id))); return; }

  const cpu = s.cpu||{}, mem = s.mem||{}, disk = s.disk||{}, net = s.net||{};
  const tether = net.tether||{}, cam = net.camera||{}, wifi = cam.wifi||{};

  cpu.temp_c!=null ? put($('cpu-c'),   Math.round(cpu.temp_c)+'\u00b0C')     : dash($('cpu-c'));
  cpu.pct!=null    ? put($('cpu-pct'), Math.round(cpu.pct)+'%')              : dash($('cpu-pct'));
  mem.pct!=null    ? put($('ram-pct'), Math.round(mem.pct)+'%')              : dash($('ram-pct'));
  disk.free_gb!=null ? put($('disk-gb'), disk.free_gb.toFixed(1)+' GB')      : dash($('disk-gb'));

  // Tether: link state plus negotiated speed — this is the operator's cable.
  const eth = $('net-eth');
  if(eth){
    if(tether.present===false){ dash(eth); }
    else if(tether.up){
      put(eth, tether.speed_mbps ? (tether.speed_mbps+' Mb') : 'UP');
      eth.style.color = 'var(--tertiary)';
    } else {
      put(eth, 'DOWN');
      eth.style.color = 'var(--error)';
    }
  }
  // Camera Wi-Fi has no readout of its own any more: it is one of the three
  // states of the eye in the status row (see STATUS.camLink).

  // Undervoltage / throttling is a leading indicator of the Pi dropping its Ethernet
  // mid-dive, so it goes to the LOG. It used to borrow the camera's warning banner,
  // which is gone \u2014 and it was never a camera fault anyway, so it never belonged
  // there. Logged once per transition rather than on every poll.
  const d = s.deep||{}, th = d.throttled;
  const brownout = !!(th && (th.undervoltage_now || th.throttled_now));
  if(brownout !== renderSystem._brownout){
    renderSystem._brownout = brownout;
    if(brownout) LOG.warn('PI ' + (th.undervoltage_now ? 'UNDER-VOLTAGE' : 'THERMAL THROTTLING') +
                          ' \u2014 this is what drops the tether NIC mid-dive');
    else LOG.state('Pi power/thermal back to normal');
  }
}

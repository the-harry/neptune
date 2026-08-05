"use strict";
/* ============================================================================
   NET — WebSocket control link (auto-reconnect, capped backoff, safe send),
   telemetry ingest, and the fixed-rate send / ping / level loops.
   ============================================================================ */
function send(obj){
  // Absent/closed socket is a silent no-op — never throws.
  const ws=state.ws;
  if(!ws || ws.readyState!==WebSocket.OPEN) return;
  try{ ws.send(JSON.stringify(obj)); }catch(e){/* swallow */}
}
function setWsStatus(s){
  if(state.wsStatus!==s) LOG.net('link status ->', s);
  state.wsStatus=s; if(s!=='online'){ state.linkMs=null; }
}

function connect(){
  if(!state.wsBase){ LOG.net('no host — staying in SIM (disk mode)'); setWsStatus('offline'); return; }
  const url=state.wsBase+CONFIG.paths.control;
  LOG.net('connecting to', url);
  setWsStatus('connecting');
  let ws;
  try{ ws=new WebSocket(url); }
  catch(e){ LOG.warn('WebSocket ctor threw', e && e.message); setWsStatus('offline'); scheduleReconnect(); return; }
  state.ws=ws;
  ws.onopen = ()=>{
    LOG.net('OPEN', url);
    setWsStatus('online');
    state.reconnectDelay=CONFIG.reconnect.baseMs;
    // Vehicle commands NEVER queue (safety rule): anything the operator set while
    // the link was down must not fire the instant it comes back. The ballast target
    // is the one control that survives a dropout as state rather than as a message,
    // so re-anchor it to the vehicle's ACTUAL level and let the operator command a
    // new one deliberately.
    const lvl = (state.realTel && typeof state.realTel.ballast_level==='number')
              ? state.realTel.ballast_level : state.ballastLevel;
    state.ballastTargetRaw = state.ballastTargetCmd = clamp(lvl||0, 0, 1);
    LOG.net('ballast target re-anchored to actual', state.ballastTargetRaw.toFixed(2));
    if(window.REC && REC.enabled){ REC.log('ws_connect', {url}); REC.adoptSession(); }   // §1 adopt session on connect
  };
  ws.onmessage = (ev)=>{ handleMessage(ev.data); };
  ws.onclose = (ev)=>{ LOG.net('CLOSE', 'code='+ev.code, ev.reason||''); if(window.REC&&REC.enabled) REC.log('ws_disconnect', {code:ev.code, reason:ev.reason||''}); setWsStatus('offline'); scheduleReconnect(); };
  ws.onerror = ()=>{ LOG.warn('ws error'); try{ ws.close(); }catch(e){} };
}
function scheduleReconnect(){
  if(!state.wsBase) return;
  if(state.reconnectTimer) return;
  const d=state.reconnectDelay;
  LOG.net('reconnect in', d+'ms');
  state.reconnectTimer=setTimeout(()=>{
    state.reconnectTimer=null;
    connect();
  }, d);
  state.reconnectDelay=Math.min(CONFIG.reconnect.maxMs, d*CONFIG.reconnect.factor);
}
function handleMessage(raw){
  let m; try{ m=JSON.parse(raw); }catch(e){ LOG.warn('bad ws frame', raw); return; }
  if(m.type==='telemetry'){ LOG.rxRate('tel','telemetry', m); if(window.REC&&REC.enabled) REC.onTelemetry(m); onTelemetry(m); }
  else if(m.type==='pong'){ if(window.REC&&REC.enabled) REC.onPong(m); else state.linkMs=Date.now()-state.lastPingAt; LOG.rxRate('pong','pong RTT', state.linkMs+'ms'); }
  else if(m.type==='ack'){ if(window.REC&&REC.enabled) REC.cmdAck(m.c_id, m.ok); LOG.rxRate('ack','cmd ack', m.name, m.ok); }
  else if(m.type==='alarm' && m.name==='leak'){ LOG.warn('ALARM: leak'); state.alarmLeak=true; }
  else LOG.rx('msg', m);
}
function onTelemetry(t){
  state.realTel=t; state.realTelAt=Date.now();
  // Sync sim mirror so a dropout continues smoothly from real values.
  if(typeof t.ballast_level==='number') state.ballastLevel=t.ballast_level;
  if(typeof t.ballast_target==='number') state.ballastTarget=t.ballast_target;
  if(typeof t.depth==='number') state.depth=t.depth;
  if(typeof t.pressure==='number') state.pressure=t.pressure;
  // Heading is whatever the SUB reports, always. If its compass is not fitted the
  // bearing does not move — and that is the truth, not something to paper over.
  if(typeof t.heading==='number') state.heading=t.heading;
  if(typeof t.battery_v==='number') state.batteryV=t.battery_v;
  if(typeof t.cpu_c==='number') state.cpuC=t.cpu_c;
  if(typeof t.ram_pct==='number') state.ramPct=t.ram_pct;
  if(typeof t.disk_gb==='number') state.diskGb=t.disk_gb;
  if(typeof t.left==='number') state.left=t.left;
  if(typeof t.right==='number') state.right=t.right;
  if(typeof t.armed==='boolean') state.armed=t.armed;
  if(typeof t.magnet==='boolean') state.magnet=t.magnet;
  if(typeof t.light_green==='boolean') state.lights.green.on=t.light_green;
  if(typeof t.light_white==='boolean') state.lights.white.on=t.light_white;
  if(t.leak===false) state.alarmLeak=false; // telemetry clears a latched alarm
}
// Fixed-rate send loop — ALWAYS transmits, even zeros (feeds server watchdog).
function startSendLoop(){
  setInterval(()=>{
    const i=state.input;
    send({type:'control', throttle:+i.throttle.toFixed(3), steer:+i.steer.toFixed(3)});
    send({type:'camera',  pan:+i.pan.toFixed(3),   tilt:+i.tilt.toFixed(3)});
    send({type:'ballast', cmd:i.ballast});
    LOG.txRate('ctl','control/camera/ballast', 'thr='+i.throttle.toFixed(2), 'str='+i.steer.toFixed(2),
               'pan='+i.pan.toFixed(2), 'tilt='+i.tilt.toFixed(2), 'ballast='+i.ballast, 'ws='+state.wsStatus);
  }, Math.round(1000/CONFIG.sendRateHz));
}
function startPingLoop(){
  // §2 SNTP: carry the client monotonic send time (t1) so the pong can complete the exchange.
  setInterval(()=>{ state.lastPingAt=Date.now(); send({type:'ping', t1:(window.REC?REC.mono():performance.now())}); }, CONFIG.pingIntervalMs);
}
// Push dirty light-brightness levels at a modest rate (avoids flooding).
function startLevelLoop(){
  setInterval(()=>{
    if(state.levelDirty.green){ state.levelDirty.green=false; send({type:'command', name:'light_green_level', value:+state.lights.green.level.toFixed(2)}); }
    if(state.levelDirty.white){ state.levelDirty.white=false; send({type:'command', name:'light_white_level', value:+state.lights.white.level.toFixed(2)}); }
  }, CONFIG.levelSendMs);
}

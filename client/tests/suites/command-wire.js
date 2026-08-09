/* COMMAND WIRE — every discrete action an operator can issue, driven the way the
   operator issues it, with the socket stubbed AT THE WIRE so what was actually
   TRANSMITTED can be read back.

   client/js/commands.js carries arm, disarm, E-STOP, surface, the lamps and the leak
   re-arm, plus the local simulator mirror that runs when there is no vehicle — and
   almost none of it was executed by anything. That is the worst place on this console
   for a blind spot: every other file describes the sub, and this one CHANGES it.

   WHAT MAKES THIS A TEST RATHER THAN AN EXERCISE

     THE SOCKET IS STUBBED, NOTHING ELSE. state.ws is replaced by an object with a
     readyState and a send(), which is precisely where net.js's send() ends up, so
     every layer above it is the shipping one: the keydown listener, the binding
     table, the edge detection in computeInput, cmd(), the correlation id, and
     JSON.stringify. What lands in WIRE is the bytes the Pi would have received.

     SO "NOTHING WAS SENT" MEANS SOMETHING. The first check proves the fixed-rate
     send loop reaches this stub; without that, every later assertion that a blocked
     command transmitted nothing would pass on a stub nothing was ever wired to.

     THE SHAPE IS ASSERTED, NOT THE FACT THAT A FUNCTION RAN. `disarm` has to be its
     own word — a vehicle that received `arm` with a value of false would arm — and a
     bare command must not carry a `value` key at all.

   THE THREE RULES THIS FILE IS REALLY ABOUT

     1. A HAZARD IS HELD, NEVER TAPPED (docs/playbook.md §5). SURFACE blows the tank;
        a stray thumb on a 7-inch handheld must not be enough. Tapped, tapped three
        times impatiently, and held — all three, on the real button, at the real
        coordinates the document says the button occupies.

     2. NOTHING QUEUES (§4). A command issued while the tether was down must never
        arrive when it comes back: a late `throttle 100%` is a hazard, so the console
        applies it to the local mirror and transmits nothing, ever.

     3. THE CONSOLE MAY ASK ABOUT THE HULL; ONLY THE VEHICLE MAY ANSWER. leak_reset is
        the one command the vehicle can decline on its merits, and the refusal only
        exists in the ack. Asking must clear nothing. A refusal must clear nothing.
        And with the tether cut, tapping the drop must not repaint a flooding hull
        green on the strength of having asked. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote text off the page and out of the vehicle's own sentences. run.py
  // prints to a Windows console whose codepage cannot carry every glyph, and a report
  // that cannot be printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const frames=n=>new Promise(r=>{ let i=0;
    const step=()=>{ if(++i>=n) return r(); requestAnimationFrame(step); };
    requestAnimationFrame(step); });
  const cls=id=>(($(id)||{}).className||'');
  const jsn=o=>{ try{ return JSON.stringify(o); }catch(e){ return String(o); } };
  // The live half of a tooltip: index.html's written explanation is captured into
  // data-help at boot and the renderer appends its sentence about the hull RIGHT NOW.
  // Only the appended half is a claim.
  const liveOf=el=>{ const h=(el&&el.dataset&&el.dataset.help)||'', t=(el&&el.getAttribute('title'))||'';
    return (t.indexOf(h)===0 ? t.slice(h.length) : t).replace(/^[\s—-]+/,'').trim(); };
  const logHas=(tag,re)=>LOG.ring().some(l=>l.tag===tag && re.test(l.msg));

  /* ---------------- THE WIRE ---------------------------------------------------
     Everything net.js's send() hands to a socket, parsed back from the JSON it
     actually produced. Parsed rather than recorded as an object on purpose: a key
     whose value is `undefined` does not survive JSON.stringify, and whether `value`
     is on the wire at all is one of the things being asserted. */
  const WIRE=[];
  const SOCK={ readyState:WebSocket.OPEN, bufferedAmount:0,
               send(s){ try{ WIRE.push(JSON.parse(s)); }
                        catch(e){ WIRE.push({UNPARSEABLE:String(s)}); } },
               close(){} };
  const mark=()=>WIRE.length;
  const after=m=>WIRE.slice(m);
  const cmdsAfter=(m,name)=>after(m).filter(x=>x.type==='command' && (name===undefined || x.name===name));
  const DISCRETE=['arm','disarm','stop','surface','magnet','light_green','light_white',
                  'leak_reset','ballast_home'];

  /* THE LINK, HELD WHERE THIS SUITE PUT IT. net.js reconnects on a backoff, and every
     attempt overwrites state.ws and walks wsStatus back to 'connecting' — which would
     turn a live command into a simulated one halfway through a check, at random. The
     reconnect timer is PARKED rather than cancelled (scheduleReconnect() returns early
     while one is outstanding), and a fast pin re-asserts the socket. Both are harness
     plumbing around the code under test; nothing below reaches past send(). */
  let pin=null;
  const unpin=()=>{ if(pin){ clearInterval(pin); pin=null; } };
  function parkReconnect(){
    if(state.reconnectTimer) clearTimeout(state.reconnectTimer);
    state.reconnectTimer=setTimeout(()=>{}, 3600000);
  }
  function dropRealSocket(){
    const ws=state.ws;
    if(ws && ws!==SOCK){
      try{ ws.onopen=null; ws.onmessage=null; ws.onclose=null; ws.onerror=null; }catch(e){}
      try{ if(ws.close) ws.close(); }catch(e){}
    }
  }
  function link(){                       // a vehicle on the tether, and we can read the wire
    unpin(); parkReconnect(); dropRealSocket();
    state.ws=SOCK; state.wsOpenAt=Date.now();
    setWsStatus('online');
    pin=setInterval(()=>{ if(state.ws!==SOCK) state.ws=SOCK;
                          if(state.wsStatus!=='online') setWsStatus('online'); }, 10);
  }
  function unlink(){                     // the tether is cut: no socket at all
    unpin(); parkReconnect(); dropRealSocket();
    state.ws=null; setWsStatus('offline');
    pin=setInterval(()=>{ if(state.ws) { dropRealSocket(); state.ws=null; }
                          if(state.wsStatus==='online') setWsStatus('offline'); }, 10);
  }

  /* ---------------- THE VEHICLE'S VOICE ----------------------------------------
     Frames go in through handleMessage, the client's own WebSocket message handler,
     so the ingest guards, the leak latch and the ack dispatch all run exactly as they
     do on a tether. */
  const TEL=extra=>Object.assign({
    type:'telemetry', mock:false, seq:1,
    heading:284, heading_card:'NW', mag_cal:3,
    depth:1.2, pressure:16.4, battery_v:8.1, current_a:1.1,
    ballast_level:0.4, ballast_homed:true, ballast_needs_rehome:false, ballast_target:0.4,
    left:0, right:0, armed:false, magnet:false,
    light_green:false, light_white:false, light_green_level:0, light_white_level:0,
    leak:false, leak_state:'NORMAL', leak_probe_fault:null, leak_rearms:0,
    speed_ms:0.4, speed_src:'paddle', sensor_faults:[]
  }, extra||{});
  let feed=null, frame=null;
  const stopFeed=()=>{ if(feed){ clearInterval(feed); feed=null; } };
  function say(extra){                   // one frame, then keep saying it at 10 Hz
    frame=TEL(extra); stopFeed();
    handleMessage(JSON.stringify(frame));
    feed=setInterval(()=>handleMessage(JSON.stringify(frame)), 100);
    return sleep(260);
  }
  const once=extra=>{ stopFeed(); handleMessage(JSON.stringify(TEL(extra))); return frames(2); };
  /* AN ACK, DELIVERED THE WAY ws.onmessage DELIVERS IT — and the throw kept rather than
     allowed to kill the run. On a real link handleMessage is called from an event
     handler, so anything it raises is an uncaught error that vanishes into the browser
     console: the socket survives, the operator sees nothing, and whatever came after the
     raise simply did not happen. Here it would abort this file instead, which would
     report the wrong thing entirely — one crashed suite instead of the finding. So it is
     caught, and whether it threw is itself asserted. */
  let ackThrew=null;
  const ack=(o)=>{
    ackThrew=null;
    try{ handleMessage(JSON.stringify(Object.assign({type:'ack', name:'leak_reset'}, o))); }
    catch(e){ ackThrew=(e && (e.stack||e.message)) || String(e); }
    return frames(2);
  };

  /* ---------------- THE OPERATOR'S HANDS ---------------------------------------
     Real events on real elements. The keyboard path goes through input.js's own
     listener, its binding table and the edge detection in computeInput, so a check
     here fails if any of those breaks — which is the point of not calling the action
     functions directly. */
  const key=(code,type)=>window.dispatchEvent(new KeyboardEvent(type,{code,bubbles:true}));
  const tap=async(code)=>{ key(code,'keydown'); await frames(3); key(code,'keyup'); await frames(3); };
  const centre=el=>{ const r=el.getBoundingClientRect();
                     return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; };
  // WHAT IS ACTUALLY UNDER THE FINGER (playbook §5: hit targets are real). A synthetic
  // dispatch on the element by name passes over a control nothing can reach, so the
  // document is asked what is at the control's centre and the event goes THERE.
  function reach(el){
    const p=centre(el), top=document.elementFromPoint(p.x,p.y);
    const hit=!!(top && (top===el || el.contains(top)));
    return {p, top, hit, target:(hit?top:el)};
  }
  const mouse=(el,type,p)=>el.dispatchEvent(new MouseEvent(type,
    {bubbles:true, cancelable:true, clientX:(p?p.x:0), clientY:(p?p.y:0)}));
  const press=r=>mouse(r.target,'mousedown',r.p);
  const release=(el,r)=>mouse(el,'mouseup',r.p);
  // RE-ASKED EVERY TIME, never cached. The renderers rebuild the glyph inside a lamp
  // button and inside the leak drop, so a child element captured a moment ago is
  // detached from the document by the next press — and an event dispatched on a
  // detached node bubbles to nothing at all. Caching it made a real toggle look like a
  // dead button, which is the same false negative as testing a control nobody can reach.
  const clickOn=el=>{ const r=reach(el);
                      mouse(r.target,'mousedown',r.p); mouse(r.target,'mouseup',r.p);
                      mouse(r.target,'click',r.p); return r; };
  // Drag a rail slider to a fraction of its travel (bottom = 0, top = 1), the way a
  // thumb does. The syringe's flange is solid, so its travel starts below it.
  function dragTrack(track, frac){
    const r=track.getBoundingClientRect();
    const inset=parseFloat(getComputedStyle(track).getPropertyValue('--syr-flange'))||0;
    const y=r.top+inset+(r.height-inset)*(1-frac);
    track.dispatchEvent(new MouseEvent('mousedown',{clientX:r.left+r.width/2, clientY:y, bubbles:true}));
    window.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
  }

  async function run(){
    await sleep(2600);
    // The collapsed console, the same setup the other suites use: headless has no
    // video, so blind nav would otherwise make the map the whole screen and bury the
    // right-hand rail these checks press.
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(400);

    /* ================= 0. THE WIRE IS REALLY THE WIRE ======================= */
    link();
    await sleep(250);
    let m=mark();
    await sleep(250);
    const ctl=after(m).filter(x=>x.type==='control');
    ok('the console\'s send loop reaches this stub, so "nothing was sent" is a measurement',
       ctl.length>0,
       ctl.length+' control frames arrived in 250 ms (e.g. '+jsn(ctl[0])+') — every check '+
       'below that asserts silence rests on this one');
    ok('with the link up, commands are transmitted rather than simulated',
       commandsBlocked()===false && linkUp()===true,
       'commandsBlocked()='+commandsBlocked()+' wsStatus='+state.wsStatus);

    /* ================= 1. ARM, AND DISARM ==================================== */
    await once({armed:false});
    ok('the vehicle has the console disarmed (the premise)', state.armed===false,
       'state.armed='+state.armed+' after a frame saying armed:false');

    m=mark();
    await tap('Space');
    const armed=cmdsAfter(m);
    ok('ARM goes out as one command, named arm, carrying no value at all',
       armed.length===1 && armed[0].name==='arm' && !('value' in armed[0]),
       'wire: '+jsn(armed)+' — a `value` key on a bare command is a field the vehicle '+
       'has to decide how to ignore');
    // The §3 correlation FIELD is on the wire and must stay there — it is what ties the
    // operator's intent, the socket frame and the Pi's ack together across both logs.
    // Its VALUE is measured rather than demanded here: see the report on window.REC.
    ok('...and the correlation field rides with it, so the ack has something to answer',
       armed.length===1 && ('c_id' in armed[0]),
       'c_id='+jsn(armed.length?armed[0].c_id:'no such key')+
       '   [measured: typeof REC="'+(typeof REC)+'", typeof window.REC="'+(typeof window.REC)+
       '", REC.enabled='+((typeof REC!=='undefined')?REC.enabled:'n/a')+']');
    ok('...and the console arms itself on the instant, without waiting to be told',
       state.armed===true, 'state.armed='+state.armed+' with no frame since');

    // AND NOW THE OTHER WAY, WHICH IS THE ONE THAT MATTERS. The vehicle insists it is
    // armed; the operator disarms. A console that waited for a frame to agree would
    // keep showing an armed sub for as long as the link took to answer — or forever,
    // if the answer is what has gone missing.
    await once({armed:true});
    ok('the vehicle is reporting ARMED (the premise for the disarm)', state.armed===true,
       'state.armed='+state.armed);
    m=mark();
    await tap('Space');
    const dis=cmdsAfter(m);
    ok('DISARM is its own word on the wire, not arm-with-a-false',
       dis.length===1 && dis[0].name==='disarm' && !('value' in dis[0]),
       'wire: '+jsn(dis)+' — `{name:"arm", value:false}` reaching a vehicle that reads '+
       'the name and not the value ARMS it');
    ok('...and the console disarms LOCALLY, rather than waiting for a frame to confirm it',
       state.armed===false,
       'state.armed='+state.armed+' — the last frame this console saw said armed:true, '+
       'and no frame has arrived since');

    /* ================= 2. E-STOP ============================================= */
    await once({armed:true});
    $('screen-flash').classList.remove('fire');
    // WITH THE STICK STILL DEFLECTED, which is the only way it is ever really used:
    // the hand that hits E-STOP is the hand that was on the throttle.
    key('KeyW','keydown');
    await frames(5);
    const thrAtStop=state.input.throttle;
    m=mark();
    await tap('KeyX');
    await frames(4);
    const stops=cmdsAfter(m,'stop');
    const postCtl=after(m).filter(x=>x.type==='control');
    key('KeyW','keyup');
    await frames(3);
    ok('E-STOP sends the vehicle a single, bare stop',
       stops.length===1 && !('value' in stops[0]),
       'wire: '+jsn(stops));
    ok('...and disarms the console with it, without waiting for the vehicle',
       state.armed===false,
       'state.armed='+state.armed+' — the last frame said armed:true and none has come since');
    ok('...and fires the screen flash, so a stab in the dark is visibly acknowledged',
       $('screen-flash').classList.contains('fire'),
       'screen-flash class="'+cls('screen-flash')+'"');
    ok('...and it fires while the throttle is held, not only from a rested hand',
       stops.length===1 && thrAtStop>0.5,
       'throttle was '+thrAtStop.toFixed(2)+' at the moment of the stop; the control frames '+
       'transmitted immediately after it carried throttle='+
       (postCtl.length? postCtl[postCtl.length-1].throttle : 'none'));

    /* ================= 3. SURFACE: HELD, NEVER TAPPED (playbook §5) ========== */
    // A tank with water in it, drawn up the way a thumb draws it, so that "the tank
    // was not blown" is a claim about something rather than about zero.
    dragTrack($('ballast-track'), 0.62);
    await frames(3);
    ok('there is water in the tank to blow (the premise for every SURFACE check)',
       state.ballastTargetRaw>0.3,
       'ballastTargetRaw='+state.ballastTargetRaw.toFixed(3)+' after dragging the syringe up');

    const btn=$('btn-surface');
    const sr=reach(btn);
    ok('SURFACE is really where it is drawn — the document hands its own centre back',
       sr.hit,
       'elementFromPoint('+sr.p.x+','+sr.p.y+') -> '+
       (sr.top? ('<'+sr.top.tagName.toLowerCase()+(sr.top.id?' #'+sr.top.id:'')+'>') : 'nothing')+
       (sr.hit? '' : ' — a control nothing can reach still answers a synthetic dispatch'));

    m=mark();
    press(sr); await sleep(150); release(btn, sr);
    await frames(3);
    ok('a TAP on SURFACE fires nothing: a stray thumb may not blow ballast',
       cmdsAfter(m,'surface').length===0 && state.ballastTargetRaw>0.3,
       '150 ms press against a '+CONFIG.surfaceHoldMs+' ms hold -> '+
       cmdsAfter(m,'surface').length+' surface commands, tank still at '+
       state.ballastTargetRaw.toFixed(3));

    m=mark();
    for(let i=0;i<3;i++){ press(sr); await sleep(140); release(btn, sr); await sleep(80); }
    await frames(3);
    ok('...and three impatient taps do not add up to one hold',
       cmdsAfter(m,'surface').length===0 && state.ballastTargetRaw>0.3,
       cmdsAfter(m,'surface').length+' surface commands from three taps — the progress fill '+
       'restarts from zero on release, and it has to');
    ok('...and the fill went back to zero each time, so the countdown never accumulates',
       ($('surface-fill').style.width||'0%')==='0%',
       'surface-fill width="'+$('surface-fill').style.width+'"');

    m=mark();
    press(sr);
    await sleep(CONFIG.surfaceHoldMs+350);
    const fired=cmdsAfter(m,'surface');
    ok('holding it for the full '+CONFIG.surfaceHoldMs+' ms DOES fire it, once, and bare',
       fired.length===1 && !('value' in fired[0]),
       'wire: '+jsn(fired));
    ok('...and the tank is commanded empty locally, not merely requested',
       state.ballastTargetRaw===0 && state.ballastTargetCmd===0,
       'raw='+state.ballastTargetRaw+' cmd='+state.ballastTargetCmd);
    ok('...and the bench model is told to drain regardless of the chase deadband',
       state.surfaceUntil>Date.now(),
       'surfaceUntil is '+Math.round(state.surfaceUntil-Date.now())+' ms out');
    await sleep(CONFIG.surfaceHoldMs+250);          // still held down
    ok('...and going on holding it does not fire it again',
       cmdsAfter(m,'surface').length===1,
       cmdsAfter(m,'surface').length+' surface commands after '+
       Math.round(2*CONFIG.surfaceHoldMs+600)+' ms of unbroken hold');
    release(btn, sr);
    await frames(3);

    // THE OTHER HAZARD PATH: both ROG Ally paddles, held. Same deliberate gesture, no
    // single-button binding anywhere, because there must not be one.
    ok('SURFACE has no single-key or single-button binding to fall off',
       !(state.bindings && state.bindings.surface) &&
       Object.keys(ACTIONS).indexOf('surface')<0,
       'ACTIONS has no `surface` entry — the emergency exists only as a held gesture');
    dragTrack($('ballast-track'), 0.5);
    await frames(3);
    m=mark();
    key('F9','keydown'); key('F10','keydown');
    await sleep(900);
    ok('a short squeeze of both paddles is not SURFACE either',
       cmdsAfter(m,'surface').length===0,
       '900 ms of a '+CONFIG.surfaceComboHoldMs+' ms combo -> nothing on the wire');
    await sleep(CONFIG.surfaceComboHoldMs-900+450);
    const combo=cmdsAfter(m,'surface');
    key('F9','keyup'); key('F10','keyup');
    await frames(3);
    ok('...and holding both paddles the whole '+CONFIG.surfaceComboHoldMs+' ms fires it, once',
       combo.length===1 && state.ballastTargetRaw===0,
       'wire: '+jsn(combo)+'  tank now '+state.ballastTargetRaw);

    /* ================= 4. THE LAMPS ========================================== */
    // Toggled from their own round buttons, dimmed from their own tracks, and ramped
    // from the D-pad keys — three different callers into three different functions,
    // all of which have to agree about what a lamp being "on" is.
    const gbtn=reach($('btn-light-green'));
    ok('the GREEN lamp button is reachable at its own centre',
       gbtn.hit, 'elementFromPoint -> '+(gbtn.top? gbtn.top.tagName : 'nothing'));
    m=mark();
    clickOn($('btn-light-green'));
    await frames(3);
    const lg=cmdsAfter(m,'light_green');
    ok('the lamp button sends light_green carrying the state it is going TO',
       lg.length===1 && lg[0].value===true,
       'wire: '+jsn(lg));
    ok('...the mirror follows, and a lamp switched on from nothing is given a level to be seen at',
       state.lights.green.on===true &&
       Math.abs(state.lights.green.level-CONFIG.lightOnDefault)<1e-9,
       'on='+state.lights.green.on+' level='+state.lights.green.level+
       ' (CONFIG.lightOnDefault='+CONFIG.lightOnDefault+') — switching a lamp on and '+
       'seeing nothing happen is how an operator concludes the lamp is broken');
    await sleep(CONFIG.levelSendMs*2+120);
    const glvl=cmdsAfter(m,'light_green_level');
    ok('...and the brightness travels as its own command, at its own rate, carrying the number',
       glvl.length>=1 && Math.abs(glvl[glvl.length-1].value-CONFIG.lightOnDefault)<0.011,
       'wire: '+jsn(glvl[glvl.length-1]||null)+' after '+glvl.length+
       ' level push(es) — levels are rate-limited state, on/off is an event');

    m=mark();
    clickOn($('btn-light-green'));
    await frames(3);
    const lgOff=cmdsAfter(m,'light_green');
    ok('...and pressing it again turns it off, in exactly the same shape',
       lgOff.length===1 && lgOff[0].value===false && state.lights.green.on===false,
       'wire: '+jsn(lgOff)+' mirror on='+state.lights.green.on);

    // THE GAUGE TRACK: a brightness set straight from where the thumb landed.
    m=mark();
    dragTrack($('track-green'), 0.98);
    await frames(3);
    const lgUp=cmdsAfter(m,'light_green');
    ok('dragging the green gauge to the top sets the level AND turns the lamp on',
       state.lights.green.level>0.9 && state.lights.green.on===true &&
       lgUp.length===1 && lgUp[0].value===true,
       'level='+state.lights.green.level.toFixed(2)+' wire: '+jsn(lgUp));
    m=mark();
    dragTrack($('track-green'), 0.0);
    await frames(3);
    const lgDown=cmdsAfter(m,'light_green');
    ok('...and dragging it to the bottom turns it off, because dark IS off',
       state.lights.green.level<=CONFIG.lightOnThreshold && state.lights.green.on===false &&
       lgDown.length===1 && lgDown[0].value===false,
       'level='+state.lights.green.level.toFixed(3)+' wire: '+jsn(lgDown));

    // THE D-PAD RAMPS: brightness held rather than set, and the on/off it implies.
    const wbtn=reach($('btn-light-white'));
    ok('the WHITE lamp button is reachable at its own centre too',
       wbtn.hit, 'elementFromPoint('+wbtn.p.x+','+wbtn.p.y+') -> '+
       (wbtn.top? ('<'+wbtn.top.tagName.toLowerCase()+(wbtn.top.id?' #'+wbtn.top.id:'')+'>') : 'nothing'));
    m=mark();
    clickOn($('btn-light-white'));                   // white on, at lightOnDefault
    await frames(3);
    ok('the WHITE lamp is on its own command name, not the green one',
       cmdsAfter(m,'light_white').length===1 && cmdsAfter(m,'light_green').length===0 &&
       state.lights.white.on===true,
       'wire: '+jsn(cmdsAfter(m,'light_white')));

    m=mark();
    key('BracketLeft','keydown');                    // dim white, held
    await sleep(900);
    key('BracketLeft','keyup');
    await frames(3);
    const wOff=cmdsAfter(m,'light_white');
    ok('ramping a lamp down to nothing switches it off, once, at the threshold',
       state.lights.white.level<=CONFIG.lightOnThreshold && state.lights.white.on===false &&
       wOff.length===1 && wOff[0].value===false,
       'level='+state.lights.white.level.toFixed(3)+' wire: '+jsn(wOff)+
       ' — one command at the crossing, not one per frame of the ramp');

    m=mark();
    key('BracketRight','keydown');                   // brighten white, held
    await sleep(500);
    const wOn=cmdsAfter(m,'light_white');
    ok('...and ramping it back up switches it on again at the same threshold',
       state.lights.white.on===true && wOn.length===1 && wOn[0].value===true,
       'level='+state.lights.white.level.toFixed(3)+' wire: '+jsn(wOn));
    await sleep(1600);                                // ramp it all the way to the top stop
    await sleep(CONFIG.levelSendMs*3);                // and let the pump carry the last change
    const atStop=mark();
    await sleep(600);                                 // ...and keep holding, hard against it
    key('BracketRight','keyup');
    await frames(3);
    // BOTH names, because the on/off is not what a jammed ramp would repeat — the LEVEL
    // is. A lamp at 1.0 that goes on marking itself dirty every frame has the pump
    // pushing an identical brightness down the tether several times a second, on a link
    // that is also carrying the telemetry and sharing the tether with the video.
    const chatter=after(atStop).filter(x=>x.type==='command' && /^light_white/.test(x.name));
    ok('a lamp already at full brightness goes quiet, instead of repeating itself down the tether',
       state.lights.white.level===1 && chatter.length===0,
       'level='+state.lights.white.level+', '+chatter.length+
       ' further light_white* commands over 600 ms held hard against the stop: '+
       jsn(chatter.slice(0,3)));
    dragTrack($('track-white'), 0.0);
    await frames(3);

    /* ================= 5. THE MAGNET ========================================= */
    m=mark();
    await tap('KeyM');
    const mag=cmdsAfter(m,'magnet');
    ok('MAGNET carries the state it is going to, and the mirror follows',
       mag.length===1 && mag[0].value===true && state.magnet===true,
       'wire: '+jsn(mag)+' mirror='+state.magnet);
    await tap('KeyM');
    await frames(2);

    /* ================= 6. LEAK RE-ARM: ASKING IS NOT ANSWERING =============== */
    // The hull floods. The alarm frame is the EDGE the vehicle announces once; the
    // telemetry carries the stage continuously. Both go in through handleMessage.
    handleMessage(JSON.stringify({type:'alarm', name:'leak_flood'}));
    await say({leak:true, leak_state:'FLOOD'});
    ok('the hull is flooding and the console says so (the premise)',
       leakStage()==='FLOOD' && cls('leak-icon')==='leak-flood' && state.alarmLeakStage==='FLOOD',
       'leakStage()='+leakStage()+' icon class="'+cls('leak-icon')+'" latch='+state.alarmLeakStage);
    ok('...and the drop has become a button, because there is now something to clear',
       $('leak-icon').dataset.rearm==='1' && $('leak-icon').getAttribute('role')==='button' &&
       $('leak-icon').getAttribute('tabindex')==='0',
       'rearm="'+$('leak-icon').dataset.rearm+'" role="'+$('leak-icon').getAttribute('role')+'"');

    const drop=reach($('leak-icon'));
    ok('the drop is reachable at its own centre — the re-arm is a real hit target',
       drop.hit,
       'elementFromPoint('+drop.p.x+','+drop.p.y+') -> '+
       (drop.top? ('<'+drop.top.tagName.toLowerCase()+(drop.top.id?' #'+drop.top.id:'')+'>') : 'nothing'));

    stopFeed();                     // the vehicle goes quiet for a moment while it decides
    m=mark();
    clickOn($('leak-icon'));
    await frames(3);
    const asked=cmdsAfter(m,'leak_reset');
    ok('tapping the drop ASKS the vehicle, in one bare command',
       asked.length===1 && asked[0].name==='leak_reset' && !('value' in asked[0]) &&
       ('c_id' in asked[0]),
       'wire: '+jsn(asked));
    ok('...and the console records that it is waiting, with the last answer cleared',
       state.leakResetPending===true && state.leakResetSaid==='',
       'pending='+state.leakResetPending+' said="'+state.leakResetSaid+'"');
    ok('...and ASKING CLEARS NOTHING: the flood stands while the vehicle thinks',
       state.alarmLeakStage==='FLOOD' && leakStage()==='FLOOD' && cls('leak-icon')==='leak-flood',
       'latch='+state.alarmLeakStage+' leakStage()='+leakStage()+' icon="'+cls('leak-icon')+
       '" — a console that painted the hull green on the strength of having asked would '+
       'be inventing the one claim that has to come from the vehicle');

    // THE REFUSAL, WORD FOR WORD. This is the vehicle's own sentence
    // (api/hardware.py reset_leak_latches), and the whole value of asking is getting it.
    const WHY='the flood probe is WET RIGHT NOW. This clears the memory of water, never '+
              'water that is present - dry the hull and find out where it came from first';
    await ack({ok:false, reason:WHY, c_id:asked[0].c_id});
    ok('a refusal is handled without raising anything on the ingest path',
       ackThrew===null, 'handleMessage threw: '+(ackThrew||'nothing'));
    ok('a refusal ends the wait and keeps the vehicle\'s sentence intact',
       state.leakResetPending===false && state.leakResetSaid===WHY && state.leakResetOk===false,
       'said="'+state.leakResetSaid.slice(0,90)+'" ok='+state.leakResetOk+
       ' pending='+state.leakResetPending);
    ok('...and a REFUSED re-arm does not clear the console\'s own latch',
       state.alarmLeakStage==='FLOOD' && cls('leak-icon')==='leak-flood',
       'latch='+state.alarmLeakStage+' icon="'+cls('leak-icon')+'" — the vehicle said NO, '+
       'and a console that let go anyway would be worse than one that never asked');
    ok('...and the refusal is on the log bus as a warning, carrying the reason with it',
       logHas('[WARN]', /leak re-arm REFUSED: .*WET RIGHT NOW/),
       'log: "'+((LOG.ring().filter(l=>/re-arm REFUSED/.test(l.msg)).pop()||{}).msg||'nothing')
       .slice(0,110)+'"');

    // A REFUSAL THAT SAYS NOTHING. The vehicle is entitled to decline without a
    // sentence, and a button that then shows an empty string is a button that did nothing.
    m=mark();
    clickOn($('leak-icon'));
    await frames(3);
    await ack({ok:false, c_id:(cmdsAfter(m,'leak_reset')[0]||{}).c_id});
    ok('a refusal with no reason still says out loud that it was refused',
       state.leakResetSaid==='refused by the vehicle' && state.leakResetOk===false,
       'said="'+state.leakResetSaid+'" — an empty answer reads as a broken button');
    ok('...and it leaves the latch standing too',
       state.alarmLeakStage==='FLOOD', 'latch='+state.alarmLeakStage);

    // THE VEHICLE AGREES.
    m=mark();
    clickOn($('leak-icon'));
    await frames(3);
    await ack({ok:true, c_id:(cmdsAfter(m,'leak_reset')[0]||{}).c_id});
    // THE OUTCOME NOBODY WATCHES FOR. Everything on this console is built to be careful
    // about the vehicle saying NO; the vehicle saying YES is the branch that had never
    // been executed, and it raised a TypeError straight out of handleMessage.
    ok('the vehicle AGREEING does not raise anything on the ingest path either',
       ackThrew===null,
       'handleMessage threw: '+(ackThrew? ackThrew.split('\n')[0] : 'nothing')+
       ' — on a real link this comes out of ws.onmessage, where an uncaught error is '+
       'invisible to the operator and takes the rest of the handler with it');
    ok('an agreed re-arm is recorded as agreed',
       state.leakResetSaid==='RE-ARMED' && state.leakResetOk===true &&
       state.leakResetPending===false,
       'said="'+state.leakResetSaid+'" ok='+state.leakResetOk);
    ok('...and THAT clears the console\'s latch, because the claim came from the vehicle',
       state.alarmLeakStage==='NORMAL', 'latch='+state.alarmLeakStage);
    ok('...but the last thing the hull said was FLOOD, so the drop is still red',
       leakStage()==='FLOOD' && cls('leak-icon')==='leak-flood',
       'leakStage()='+leakStage()+' icon="'+cls('leak-icon')+'" — a retired latch is not '+
       'a dry hull, and only the probes can say which this is');
    // AND IT IS WRITTEN DOWN. The log bus is what reaches the LOGS overlay and the
    // session file on disk, so a re-arm that is not on it did not happen as far as the
    // dive record is concerned — while every refusal is on it, because LOG.warn exists.
    ok('...and the log bus carries the re-arm, so the dive record contains it',
       LOG.ring().some(l=>/re-armed by the vehicle/.test(l.msg)),
       'log: "'+((LOG.ring().filter(l=>/re-armed by the vehicle/.test(l.msg)).pop()||{}).msg||'NOTHING')+
       '" — the refusals above are all in the ring; standing the flood watch DOWN is the '+
       'one that has to be at least as well recorded as being told you may not');

    await say({leak:false, leak_state:'NORMAL'});
    ok('only the probes answering dry again turns the drop green',
       cls('leak-icon')==='leak-normal' && /both probes dry/i.test(liveOf($('leak-icon'))) &&
       !$('leak-icon').dataset.rearm,
       'icon="'+cls('leak-icon')+'" says "'+liveOf($('leak-icon'))+'" rearm='+
       jsn($('leak-icon').dataset.rearm||null));

    /* ================= 7. THE WATER TAKES THE TETHER WITH IT ================= */
    // The failure the latch exists for, with the re-arm button now sitting in the
    // middle of it. The hull floods, the operator asks, the vehicle refuses because a
    // probe is wet — and then the water shorts the tether and the link goes.
    handleMessage(JSON.stringify({type:'alarm', name:'leak_flood'}));
    await say({leak:true, leak_state:'FLOOD'});
    m=mark();
    clickOn($('leak-icon'));
    await frames(3);
    await ack({ok:false, reason:WHY, c_id:(cmdsAfter(m,'leak_reset')[0]||{}).c_id});
    ok('the hull is flooding, the vehicle has refused to re-arm, and the drop is red (the premise)',
       cls('leak-icon')==='leak-flood' && state.alarmLeakStage==='FLOOD' &&
       state.leakResetOk===false,
       'icon="'+cls('leak-icon')+'" latch='+state.alarmLeakStage);

    stopFeed(); unlink();
    await sleep((CONFIG.simFallbackMs||3000)+400);   // telemetry goes stale, the model takes over
    ok('the console has handed the gauges to the simulator, as it must (the premise)',
       vehicleRecent()===false && commandsBlocked()===true,
       'vehicleRecent()='+vehicleRecent()+' commandsBlocked()='+commandsBlocked()+
       ' mode='+state.mode);
    ok('...and the LATCH is the only thing still holding the flood on screen, and it holds',
       leakStage()==='FLOOD' && cls('leak-icon')==='leak-flood',
       'leakStage()='+leakStage()+' icon="'+cls('leak-icon')+'" — with the vehicle gone, '+
       'the live stage is the bench model\'s NORMAL; the latch is what stops it repainting');

    // AND NOW THE TAP THAT MUST NOT BE ABLE TO UNDO IT. The drop is still a button, the
    // operator has just been refused, the sub is not answering, and there is nobody to
    // ask. Whatever this does locally, it may not end with the console certifying a
    // hull it cannot hear as dry.
    m=mark();
    clickOn($('leak-icon'));
    await frames(4);
    ok('tapping the drop with the tether cut transmits nothing',
       after(m).length===0,
       after(m).length+' messages reached the socket (there is no socket)');
    ok('...and it does NOT repaint a flooding hull as certified dry',
       leakStage()!=='NORMAL' && cls('leak-icon')!=='leak-normal' &&
       !/both probes dry/i.test(liveOf($('leak-icon'))),
       'leakStage()='+leakStage()+' icon="'+cls('leak-icon')+'" says "'+
       liveOf($('leak-icon')).slice(0,80)+'"  latch='+state.alarmLeakStage+
       ' simStage='+state.simLeakStage+
       ' — the green struck-through drop is this console\'s strongest reassurance, and '+
       'nothing on the end of this cable has said anything to earn it');
    ok('...and the console has not quietly awarded itself the vehicle\'s answer either',
       state.leakResetOk===false,
       'leakResetOk='+state.leakResetOk+' said="'+state.leakResetSaid.slice(0,60)+'"');

    /* ================= 8. THE HULL COMES BACK ================================ */
    // Recovery is half the contract. A latch that can never be retired is its own
    // fault, and the way back is the vehicle itself saying NORMAL.
    link();
    await say({leak:false, leak_state:'NORMAL'});
    ok('a hull that answers dry again retires the latch and the drop goes green',
       state.alarmLeakStage==='NORMAL' && cls('leak-icon')==='leak-normal',
       'latch='+state.alarmLeakStage+' icon="'+cls('leak-icon')+'"');
    stopFeed();

    /* ================= 9. NO VEHICLE: THE MIRROR STILL FOLLOWS =============== */
    // A host is configured and the link is down. Every control stays live and drives
    // the local model, nothing is transmitted, and the log says SIM so the operator
    // can always tell which of the two they are flying.
    unlink();
    await frames(3);
    ok('with a host configured and the link down, commands are simulated',
       commandsBlocked()===true,
       'wsBase="'+state.wsBase+'" wsStatus='+state.wsStatus);

    const wasArmed=state.armed;
    const ringAt=LOG.ring().length;
    m=mark();
    await tap('Space');
    const said=LOG.ring().slice(ringAt).filter(l=>l.tag==='[CMD]');
    ok('ARM in SIM transmits absolutely nothing',
       after(m).length===0,
       after(m).length+' messages on the wire — not the command, and not a queued copy of it');
    ok('...and the mirror follows anyway, so the console is still flyable on the bench',
       state.armed===!wasArmed,
       'armed '+wasArmed+' -> '+state.armed);
    ok('...and the log bus says SIM, so the two modes are never confused',
       said.some(l=>/^SIM (arm|disarm)/.test(l.msg)),
       'log: '+jsn(said.map(l=>l.msg)));
    // AND IT DOES NOT ALSO TAKE THE TRANSMIT PATH. cmd() writes a plain `[CMD] arm` line
    // on the way to the socket and `[CMD] SIM arm` when it turns back; a console showing
    // both is a console whose own record cannot say which of the two modes flew the sub.
    // Asserted separately from the wire because send() is a no-op on an absent socket
    // anyway — silence on the wire alone would pass with the simulator branch deleted.
    ok('...and nothing pretends it went out: no plain command line beside the SIM one',
       !said.some(l=>/^(arm|disarm)\b/.test(l.msg)),
       'log: '+jsn(said.map(l=>l.msg)));

    m=mark();
    await tap('KeyM');
    ok('the magnet mirrors in SIM, and stays off the wire',
       after(m).length===0 && state.magnet===true,
       'sent '+after(m).length+' messages, mirror='+state.magnet);

    m=mark();
    clickOn($('btn-light-green'));
    await frames(3);
    ok('a lamp mirrors in SIM, and stays off the wire',
       after(m).length===0 && state.lights.green.on===true &&
       state.lights.green.level>=CONFIG.lightOnDefault,
       'sent '+after(m).length+', green on='+state.lights.green.on+
       ' level='+state.lights.green.level.toFixed(2));

    dragTrack($('ballast-track'), 0.6);
    await frames(3);
    m=mark();
    const sr2=reach(btn);
    press(sr2);
    await sleep(CONFIG.surfaceHoldMs+350);
    release(btn, sr2);
    await frames(3);
    ok('even SURFACE stays operable with no vehicle — held, mirrored, and never transmitted',
       after(m).length===0 && state.ballastTargetRaw===0 && state.surfaceUntil>Date.now(),
       'sent '+after(m).length+' messages; tank '+state.ballastTargetRaw+
       '; drain window '+Math.round(state.surfaceUntil-Date.now())+' ms — disabling the '+
       'rail instead just made the console look broken');

    // THE SIM LEAK DRILL, AND ITS WAY BACK. The rehearsal ladder is the only leak this
    // console may raise on its own, so it is the only one the console may retire on its
    // own — and doing so must not need a reload. The last real frame has to be old
    // enough for the model to own the drop first: while a hull's word is still recent
    // the ladder is correctly ignored, which is the rule and not the exception.
    while(vehicleRecent()) await sleep(200);
    await tap('KeyL');                               // NORMAL -> WARN
    await tap('KeyL');                               // WARN   -> FLOOD
    await frames(3);
    ok('the bench leak drill raises a simulated flood (the premise)',
       state.simLeakStage==='FLOOD' && leakStage()==='FLOOD' && cls('leak-icon')==='leak-flood',
       'simLeakStage='+state.simLeakStage+' leakStage()='+leakStage()+
       ' latch='+state.alarmLeakStage+' vehicleRecent()='+vehicleRecent());
    m=mark();
    clickOn($('leak-icon'));
    await frames(4);
    ok('tapping the drop clears the SIMULATED stage, so the drill can be run again',
       state.simLeakStage==='NORMAL' && state.simLeak===false && leakStage()==='NORMAL' &&
       after(m).length===0,
       'simLeakStage='+state.simLeakStage+' leakStage()='+leakStage()+
       ' sent '+after(m).length+' messages — nothing is claimed about a hull that is not there');

    /* ================= 10. AND NOTHING WAS QUEUED (§4) ======================= */
    const before=mark();
    link();
    await sleep(700);
    const late=after(before).filter(x=>x.type==='command' && DISCRETE.indexOf(x.name)>=0);
    ok('nothing issued while the tether was down arrives when it comes back',
       late.length===0,
       (late.length? ('LATE: '+jsn(late)) : 'no discrete command replayed')+
       ' — a late `surface` or a late `arm` is a hazard, so they are applied to the '+
       'mirror and never stored to send');

    /* ================= 11. NO HOST AT ALL ==================================== */
    // resolveHost() reads this off the URL at boot and cannot be re-run from inside a
    // page that is already loaded, so the one field it writes is set here by hand. It
    // is CONFIGURATION — which backend, if any, this console addresses — and not any of
    // the state the commands under test write. This is the other branch of cmd(): not
    // "blocked", simply a console with nowhere to send, which takes the full transmit
    // path into a socket that is not there.
    const savedBase=state.wsBase;
    unlink();
    state.wsBase='';
    await sleep(700);                                // STATUS.tick runs at 2 Hz
    ok('a console with no vehicle address is not blocked — it is a simulator',
       commandsBlocked()===false && STATUS.link==='sim',
       'commandsBlocked()='+commandsBlocked()+' STATUS.link='+STATUS.link);
    const armedBefore=state.armed;
    m=mark();
    await tap('Space');
    ok('a command with nowhere to go is dropped at the socket, never queued, and still mirrors',
       after(m).length===0 && state.armed===!armedBefore,
       'sent '+after(m).length+' messages, armed '+armedBefore+' -> '+state.armed+
       ' — send() is itself a no-op on an absent socket, which is what makes the '+
       'no-queue rule structural rather than remembered');
    ok('...and it is logged as a real command, not as SIM: nothing was simulated about it',
       LOG.ring().slice(-14).some(l=>l.tag==='[CMD]' && /^(arm|disarm)\b/.test(l.msg)),
       'recent [CMD] lines: '+jsn(LOG.ring().filter(l=>l.tag==='[CMD]').slice(-3).map(l=>l.msg)));
    state.wsBase=savedBase;

    /* ================= NOTHING ELSE BROKEN =================================== */
    // Put the console back somewhere calm before the portrait: linked, dry, disarmed,
    // lamps out, tank empty.
    link();
    await say({leak:false, leak_state:'NORMAL', armed:false, magnet:false});
    if(state.lights.green.on) clickOn($('btn-light-green'));
    if(state.lights.white.on) clickOn($('btn-light-white'));
    dragTrack($('ballast-track'), 0.0);
    await sleep(300);
    stopFeed(); unpin();

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    const need=['btn-surface','surface-fill','btn-light-green','btn-light-white',
                'track-green','track-white','ballast-track','leak-icon','leak-pulse',
                'screen-flash','alerts'];
    const missing=need.filter(id=>!$(id));
    ok('every control these checks press is still on the page', missing.length===0,
       missing.length? 'MISSING: '+missing.join(', ') : need.length+' elements found');
    ok('the command surface is all still there, by name',
       ['cmd','simulatedCommand','toggleArm','eStop','surface','toggleMagnet','toggleLight',
        'adjustLight','setLightLevel','resetLeak','noteLeakResetAck','fireScreenFlash']
         .every(f=>typeof window[f]==='function'),
       ['cmd','simulatedCommand','toggleArm','eStop','surface','toggleMagnet','toggleLight',
        'adjustLight','setLightLevel','resetLeak','noteLeakResetAck','fireScreenFlash']
         .filter(f=>typeof window[f]!=='function').join(', ')||'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

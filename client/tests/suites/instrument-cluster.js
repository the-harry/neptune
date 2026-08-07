/* WHAT THIS GUARDS — the instrument cluster, one readout at a time, and the single
   rule every one of them has to obey: a number on this console is either a reading, or
   an admission that there is none. There is no third thing, and there is no version of
   the admission that looks like a reading.

     THE ORPHANS. gyro_z_dps, accel_fwd_ms2, pitch_deg and roll_deg were produced by the
     vehicle every frame and read by nothing at all, and current_a was spent inside the
     pack tooltip — "drawing 3.1 A" — which an operator on a canal bank in sunlight with
     wet hands is never going to hover. A reading nobody can see is a reading that does
     not exist, so each of them gets a readout here and each readout is held to the same
     bar as depth: the value, the cannot-tell, and the sentence saying what it MEANS.

     THE REAL ZERO. This is the failure the whole cluster is most exposed to, because
     every one of the new fields has a legitimate zero and every one of those zeroes is
     the CALM answer. 0.0 deg/s is "not turning". 0.0 m/s2 is "coasting". (0.0, 0.0) is
     "level". 0.0 A is "drawing nothing". A single `x || null` anywhere on the ingest
     path turns all four into cannot-tell, and — far worse — the same coercion written
     the other way (`x == null ? 0 : x`) turns a dead IMU into a vehicle sitting
     perfectly still and perfectly level. Both directions are checked, on every readout
     that has a real zero.

     '?' IS NOT '--'. They are different facts with different reactions: a dash is a
     dropped frame on a live socket and it comes back on its own, so the operator is
     right to ignore it; a question mark is a chip that has stopped answering and waiting
     it out means flying on a number nobody is taking. Speed is the one that has been
     spelling cannot-tell as '--' since it was written, which reads as "the frame is
     late" about a paddlewheel that is not turning.

     AND EVERY GLYPH EXPLAINS ITSELF. Title and aria-label, both, in a full sentence, on
     every reading in the cluster — the same bar demo-mode holds the rest of the console
     to. An estimate says it is an estimate (Speed's EST chip); a reading never wears the
     estimate mark it did not earn.

   DRIVEN THROUGH handleMessage(), the client's own WebSocket message handler, exactly as
   a Pi frame arrives — the same door sensor-loss.js uses and for the same reason. Every
   bug this file is about lives in the INGEST COERCION, so a suite that assigned into
   `state` would skip the code under test and pass on a console that is lying.

   THE IDS ARE NOT THIS SUITE'S PROPERTY. The file that draws the cluster owns its
   element names; this file is written against the BEHAVIOUR. So each reading is looked
   up by the ids this console's naming convention makes likely, and failing that by the
   one property the project guarantees every number has — a written explanation of what
   it means. A reading that can be found NEITHER way fails its own named check and takes
   its five behaviour checks down with it, each one reported as NOT RUN. A missing
   readout must never be able to leave a green run behind it. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote text straight off the page, and the page has glyphs in it (the map
  // tools carry a ⚑, the dial a ＋). run.py prints to a Windows console whose codepage
  // cannot encode those, and it dies mid-report with a UnicodeEncodeError — losing every
  // remaining result to a decoration. Anything the codepage cannot carry is escaped so it
  // is still IDENTIFIABLE rather than dropped: a report that cannot be printed is a
  // report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E\u00A0-\u00FF\u2013\u2014\u2018\u2019\u201C\u201D\u2022\u2026]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const txtOf=el=>((el&&el.textContent)||'').replace(/ /g,' ').trim();
  const alerts=()=>[...$('alerts').querySelectorAll('.alert')].map(e=>
    (e.querySelector('.alert-tx')||{}).textContent||'');
  const alertText=()=>alerts().join(' | ')||'none';

  // A HULL WITH NOTHING ELSE WRONG WITH IT, so that the one reading each check removes
  // is the only thing that changed. Every value is deliberately distinct in its digits —
  // 12.0, 0.35, -6.5, 9.0, 3.1, 4.2, 20.7, 284, 8.1, 0.42 — because a readout wired to
  // the WRONG field is the quietest way this cluster can be wrong, and it is invisible
  // the moment two of the numbers match.
  const BASE = {type:'telemetry', mock:false, armed:false, seq:1,
    heading:284, heading_card:'NW', mag_cal:3,
    depth:4.2, pressure:20.7, battery_v:8.1, current_a:3.1,
    gyro_z_dps:12.0, accel_fwd_ms2:0.35, pitch_deg:-6.5, roll_deg:9.0,
    ballast_level:0.4, ballast_homed:true, ballast_needs_rehome:false, ballast_target:0.4,
    left:0, right:0, magnet:false, light_green:false, light_white:false,
    light_green_level:0, light_white_level:0,
    leak:false, leak_state:'NORMAL', leak_probe_fault:null,
    speed_ms:0.42, speed_src:'paddle', signal:4, sensor_faults:[],
    snagged:false, gyro_only:false};

  let feed=null, frame=null;
  function stopFeed(){ if(feed){ clearInterval(feed); feed=null; } }
  // THE REAL DOOR IN. Repeated at 10 Hz because one frame is not a link: main.js falls
  // back to the simulator ~1 s after the last telemetry, and a readout compared against
  // the model's own invented numbers proves nothing about the wire.
  function say(extra){
    frame = Object.assign({}, BASE, extra||{});
    stopFeed();
    handleMessage(JSON.stringify(frame));
    feed = setInterval(()=>handleMessage(JSON.stringify(frame)), 100);
    return sleep(260);                     // a RAF render plus a 10 Hz map tick
  }

  // Everything already spoken for by the rest of the console. The fallback search below
  // matches on what an element SAYS it means, and several of these say it in words the
  // cluster's own readouts also use — renderBattery appends "drawing 3.1 A" to the pack
  // tooltip, which would otherwise make the pack voltage answer to a search for the
  // current. A readout that has to be found by its meaning must not be found by somebody
  // else's meaning.
  const TAKEN = ['depth-val','pressure-val','heading-val','battery-v','ballast-pct',
                 'link-ms','cpu-c','cpu-pct','ram-pct','disk-gb','net-eth','origin-val',
                 'origin-tile','sonar-teth','speed-val','speed-src','hdg-flag','hdg-warning',
                 'cam-battery','cam-sd','cam-quality','cam-awb','cam-ev','cam-remaining','cam-mode'];
  function findReadout(ids, re){
    for(let i=0;i<ids.length;i++){ const el=$(ids[i]); if(el) return el; }
    const all=[...document.querySelectorAll('[id]')];
    for(let i=0;i<all.length;i++){
      const el=all[i];
      if(TAKEN.indexOf(el.id)>=0) continue;
      if(el.children.length) continue;                 // a readout, not a panel
      // A READOUT, AND NOT A BUTTON THAT HAPPENED TO SAY THE WORD. The first draft
      // matched on the tooltip alone and resolved "turn rate" to the map's PLAN FROM
      // ELSEWHERE tool ("...somewhere you are not standing") and "pack current" to a lamp
      // ("LAMP" contains "amp"), then asserted about a flag glyph for the rest of the
      // section. The console has one vocabulary for a number on screen, so the search is
      // restricted to it.
      if(!/\b(m-val|m-flag|est-tag)\b/.test(el.className||'')) continue;
      // data-help is the HTML's written explanation, captured before any renderer
      // appended its live sentence (core.js captureHelp), so this matches on the
      // permanent meaning rather than on whatever the hull happens to be saying.
      const t=(el.dataset.help||el.getAttribute('title')||'');
      if(t && re.test(t)) return el;
    }
    return null;
  }
  // A tooltip's own words, and its screen-reader twin. Origin's number keeps its
  // explanation on the tile around it, so an ancestor counts — what matters is that
  // something the operator can point at says what the number means.
  const helpOf=el=>{ const h=el.closest('[title]');
    return h ? ((h.dataset.help || h.getAttribute('title') || '')) : ''; };
  const ariaOf=el=>{ const a=el.closest('[aria-label]');
    return a ? (a.getAttribute('aria-label')||'') : ''; };
  const look=el=>({text:txtOf(el), cls:(el.className||''),
                   color:getComputedStyle(el).color, id:el.id});

  /* ---- ONE READING, HELD TO THE WHOLE RULE --------------------------------
     spec = {name, ids, re, live, has, dead, zero, zeroHas}
       live/dead/zero  telemetry overlays: the reading present, absent, and at a
                       genuine zero
       has / zeroHas   what the rendered text must contain. Digits, not a whole
                       string, so the cluster is free to choose its own units and
                       spacing without this file having an opinion about them. */
  const found={};
  async function cover(spec){
    const el = findReadout(spec.ids, spec.re);
    found[spec.name] = el;
    ok(spec.name+' — there is a readout for it on the page at all', !!el,
       el ? ('#'+el.id) : ('nothing matches the ids ' + spec.ids.join(' / ') +
             ', and nothing else on the page explains itself as ' + spec.re +
             ' — a reading the operator cannot see is a reading the vehicle did not send'));
    const miss = 'NOT RUN - no ' + spec.name + ' readout exists to check';
    if(!el){
      ok(spec.name+' — a real reading renders as the number', false, miss);
      if(spec.zero!==undefined)
        ok(spec.name+' — a real ZERO renders as zero, not as cannot-tell', false, miss);
      ok(spec.name+' — a null renders as "?" and never as the stale "--"', false, miss);
      ok(spec.name+' — cannot-tell is visibly a different thing from a reading', false, miss);
      ok(spec.name+' — it says what it MEANS, to the eye and to a screen reader', false, miss);
      return;
    }

    await say(spec.live);
    const live = look(el);
    ok(spec.name+' — a real reading renders as the number',
       spec.has.test(live.text) && live.text!=='?' && live.text!=='--',
       'shows "'+live.text+'", wanted '+spec.has+' — a readout wired to the wrong field '
       + 'looks perfectly healthy');

    // THE ZERO THAT IS A MEASUREMENT. Checked before the null, so a console that has
    // collapsed the two is caught while there is still something on screen to compare.
    if(spec.zero!==undefined){
      await say(spec.zero);
      const z = look(el);
      ok(spec.name+' — a real ZERO renders as zero, not as cannot-tell',
         spec.zeroHas.test(z.text) && z.text!=='?' && !/\bnosensor\b/.test(z.cls),
         'shows "'+z.text+'" class="'+z.cls+'" — this zero is the vehicle MEASURING the '
         + 'calm answer, and `value || null` on the way in spells it "the chip is dead"');
    }

    await say(spec.dead);
    const dead = look(el);
    ok(spec.name+' — a null renders as "?" and never as the stale "--"',
       dead.text==='?' && dead.text!=='--',
       'shows "'+dead.text+'" class="'+dead.cls+'" — "--" is this console\'s word for a '
       + 'dropped frame that will be along shortly, and an operator is right to wait that '
       + 'out; a chip that has stopped will not be along');
    ok(spec.name+' — cannot-tell is visibly a different thing from a reading',
       dead.text!==live.text && (dead.cls!==live.cls || dead.color!==live.color),
       'reading "'+live.text+'" ['+live.cls+'] '+live.color+
       '   vs   cannot-tell "'+dead.text+'" ['+dead.cls+'] '+dead.color);

    await say(spec.live);
    const help=helpOf(el), aria=ariaOf(el);
    // A LABEL IS NOT AN EXPLANATION. "Turn" tells a stranger nothing; the bar is a
    // sentence saying what the number means and what to do about it, which is the same
    // bar demo-mode holds every other glyph to.
    const words = help.trim().split(/\s+/).length;
    ok(spec.name+' — it says what it MEANS, to the eye and to a screen reader',
       help.trim().length>=40 && words>=8 && aria.trim().length>=40,
       'title('+help.trim().length+' chars, '+words+' words)="'+help.slice(0,90)+'..." '
       + 'aria-label '+aria.trim().length+' chars');
  }

  async function run(){
    await sleep(2600);
    // Headless has no video, so BLIND NAV engages on its own and takes the top bar off
    // screen with it. The cluster has to be visible to be read.
    CONFIG.map.blindNav=false;
    if(typeof exitBlindNav==='function') exitBlindNav();
    if(MAP.expanded && typeof collapseMap==='function') collapseMap();
    await sleep(400);

    await say({});
    ok('the console is flying the WIRE, not its own model (the premise of everything below)',
       state.mode==='real' && state.realTel && state.realTel.gyro_z_dps===12.0,
       'mode='+state.mode+' realTel.gyro_z_dps='+(state.realTel||{}).gyro_z_dps+
       ' — in sim mode every number below is invented and every check is vacuous');

    // ================= 1. EVERY READING IN THE CLUSTER =================
    // The four that already existed, then the five that had nothing to be read on.
    await cover({name:'Depth', ids:['depth-val'], re:/depth/i,
      live:{depth:4.2}, has:/4\.2/, dead:{depth:null},
      zero:{depth:0}, zeroHas:/0/});
    await cover({name:'Pressure', ids:['pressure-val'], re:/pressure/i,
      live:{pressure:20.7}, has:/20\.7/, dead:{pressure:null}});
    await cover({name:'Heading', ids:['heading-val'], re:/bearing|compass heading/i,
      live:{heading:284}, has:/284/, dead:{heading:null, heading_card:null},
      // Due north is a real bearing and the single worst number to invent. It has to be
      // shown when it is MEASURED and refused when it is not, and the two must not be
      // the same picture.
      zero:{heading:0}, zeroHas:/0/});
    await cover({name:'Pack voltage', ids:['battery-v'], re:/pack voltage/i,
      live:{battery_v:8.1}, has:/8\.1/, dead:{battery_v:null}});

    await cover({name:'Pack current', re:/\bcurrent\b|\bamps?\b|\bamperes?\b/i,
      ids:['current-a','current-val','current','pack-a','pack-current','battery-a',
           'amps-val','amp-val','draw-val','load-a'],
      live:{current_a:3.1}, has:/3\.1/, dead:{current_a:null},
      // A pack drawing nothing is a pack with the thrusters off, which is a perfectly
      // ordinary thing for a sub on the surface to be doing.
      zero:{current_a:0}, zeroHas:/0/});
    await cover({name:'Turn rate', re:/turn rate|rate of turn|yaw rate|degrees per second|deg\/s|\u00b0\/s/i,
      ids:['turn-val','turn-rate','turn-rate-val','turnrate-val','yaw-val','yaw-rate',
           'gyro-val','gyro-z','gyro-z-val','rate-val','rot-val'],
      live:{gyro_z_dps:12.0}, has:/12/, dead:{gyro_z_dps:null},
      // NOT TURNING is a measurement, and it is the reading a pilot holding a straight
      // course expects to see. Spelled '?' it says the gyro is dead instead.
      zero:{gyro_z_dps:0.0}, zeroHas:/0/});
    await cover({name:'Acceleration', re:/accelerat|m\/s2|m\/s²|surge|speeding up/i,
      ids:['accel-val','accel-fwd','accel-fwd-val','acc-val','accel','surge-val','a-fwd'],
      live:{accel_fwd_ms2:0.35}, has:/0\.3/, dead:{accel_fwd_ms2:null},
      // COASTING. Zero acceleration at speed is the normal state of a sub in transit.
      zero:{accel_fwd_ms2:0.0}, zeroHas:/0/});
    await cover({name:'Pitch', re:/\bpitch\b/i,
      ids:['pitch-val','pitch','pitch-deg','attitude-val','att-val','tilt-val',
           'pitch-roll-val','pitchroll-val'],
      live:{pitch_deg:-6.5}, has:/6\.5/, dead:{pitch_deg:null},
      zero:{pitch_deg:0.0}, zeroHas:/0/});
    await cover({name:'Roll', re:/\broll\b/i,
      ids:['roll-val','roll','roll-deg','attitude-val','att-val','tilt-val',
           'pitch-roll-val','pitchroll-val'],
      live:{roll_deg:9.0}, has:/9/, dead:{roll_deg:null},
      // LEVEL. The calm answer, and the one a dead IMU used to be able to fake.
      zero:{roll_deg:0.0}, zeroHas:/0/});
    await cover({name:'Speed', ids:['speed-val'], re:/water speed/i,
      live:{speed_ms:0.42, speed_src:'paddle'}, has:/0\.42/,
      dead:{speed_ms:null, speed_src:null},
      // A paddlewheel that counted no water going past is not a paddlewheel that has
      // stopped answering, and 0.00 m/s beside full throttle is the snag.
      zero:{speed_ms:0, speed_src:'paddle'}, zeroHas:/0/});

    // ================= 2. NO TWO READINGS ARE THE SAME READING =================
    // A resolver that quietly answered "depth" for two different questions would make
    // half of section 1 assert twice about one element and pass. Pitch and roll are the
    // one legitimate pair: a cluster is free to draw attitude as a single readout, and
    // if it does, both names resolving to it is correct rather than a collision.
    const names = Object.keys(found).filter(n=>found[n]);
    const dupes = [];
    names.forEach((a,i)=>names.slice(i+1).forEach(b=>{
      if(found[a]===found[b] && !(/Pitch|Roll/.test(a) && /Pitch|Roll/.test(b)))
        dupes.push(a+' and '+b+' are both #'+found[a].id);
    }));
    ok('each reading has a readout of its OWN', dupes.length===0,
       dupes.length ? dupes.join(' | ')
                    : names.length+' readouts, '+new Set(names.map(n=>found[n].id)).size+' distinct elements');
    if(found['Pitch'] && found['Roll'] && found['Pitch']===found['Roll'])
      ok('pitch and roll share one attitude readout, and it shows both numbers', (()=>{
          return /6\.5/.test(txtOf(found['Pitch']));   // set by Roll's live frame above
        })(), 'combined readout reads "'+txtOf(found['Pitch'])+'" — a single attitude '
        + 'readout is fine, one that only ever shows half of it is not');

    // ================= 3. AN ESTIMATE IS TAGGED, A READING IS NOT =================
    // The paddlewheel was bought for exactly this: a snagged sub's throttle-curve speed
    // is pixel-for-pixel a healthy cruise, so the estimate has to declare itself or the
    // one reading that would reveal a snag becomes the one that hides it.
    await say({speed_ms:0.42, speed_src:'paddle'});
    const measuredTxt=txtOf($('speed-val')), measuredTag=txtOf($('speed-src'));
    ok('a MEASURED speed carries no estimate tag and no tilde (the control)',
       measuredTag==='' && measuredTxt.indexOf('~')<0 && /0\.42/.test(measuredTxt),
       'speed="'+measuredTxt+'" tag="'+measuredTag+'"');
    await say({speed_ms:0.5, speed_src:'lut'});
    ok('a speed from the throttle curve is TAGGED, and the number changes shape too',
       txtOf($('speed-src'))==='EST' && txtOf($('speed-val')).indexOf('~')===0,
       'speed="'+txtOf($('speed-val'))+'" tag="'+txtOf($('speed-src'))+'" — the tag must '
       + 'not be the only carrier');
    await say({speed_ms:0.5, speed_src:'kf-lut'});
    ok('...and so is the filtered one, which is still the throttle curve underneath',
       txtOf($('speed-src'))==='EST' && txtOf($('speed-val')).indexOf('~')===0,
       'speed="'+txtOf($('speed-val'))+'" tag="'+txtOf($('speed-src'))+'"');
    await say({speed_ms:0.5, speed_src:'kf-paddle'});
    ok('a filtered PADDLEWHEEL reading is still a measurement and is not tagged',
       txtOf($('speed-src'))==='' && txtOf($('speed-val')).indexOf('~')<0,
       'speed="'+txtOf($('speed-val'))+'" tag="'+txtOf($('speed-src'))+'"');

    // AND THE FOUR NEW ONES ARE READINGS. rov.py takes them off the same hardware handle
    // as the heading; nothing about them is modelled, so none of them may wear the mark
    // that says it is. A readout that dresses a reading as an estimate is the same lie
    // as the reverse, and it teaches the operator to discount the tag.
    await say({});
    const marked = ['Turn rate','Acceleration','Pitch','Roll','Pack current']
      .filter(n=>found[n])
      .filter(n=>/~/.test(txtOf(found[n])) || /\best\b/.test(found[n].className||''));
    ok('the readings are not dressed as estimates', marked.length===0,
       marked.length ? ('wearing the estimate mark: '+marked.map(n=>n+' "'+txtOf(found[n])+'"').join(', '))
                     : 'turn rate, acceleration, pitch, roll and pack current are all plain numbers');

    // ================= 4. CANNOT-TELL IS NOT STALE, ACROSS THE WHOLE CLUSTER ======
    // The distinction is the point of the '?' and it is worth proving on the cluster as
    // a whole and not only one readout at a time: a dropped frame dashes the WHOLE bar
    // together and comes back on its own, and that is what makes a single '?' sitting in
    // a bar of live numbers mean something.
    await say({});
    const clusterEls = Object.keys(found).map(n=>found[n]).filter(Boolean);
    stopFeed();
    const hold=setInterval(()=>setWsStatus('online'), 16);
    setWsStatus('online');
    const t0=Date.now();
    while(Date.now()-t0<2500 && !(state.mode==='stale' && /\bis-stale\b/.test($('depth-val').className||'')))
      await sleep(30);
    const staleTexts = clusterEls.map(el=>el.id+'="'+txtOf(el)+'"');
    const notDashed = clusterEls.filter(el=>txtOf(el)!=='--' && !/\bis-stale\b/.test(el.className||''));
    clearInterval(hold);
    setWsStatus('offline');
    ok('a dropped frame dashes the cluster rather than accusing any chip of dying',
       state.mode==='stale' && notDashed.length===0,
       'mode='+state.mode+'  '+staleTexts.join(' ')+
       (notDashed.length ? ('  — still not dashed: '+notDashed.map(e=>e.id).join(', ')) : ''));
    const stillQuestion = clusterEls.filter(el=>txtOf(el)==='?');
    ok('...and nothing in the cluster reads "?" over a merely late frame',
       stillQuestion.length===0,
       stillQuestion.length ? ('claiming a dead chip on a dropped frame: '+
                               stillQuestion.map(e=>e.id).join(', '))
                            : 'no cannot-tell claimed while stale');

    // And it all comes back. A gauge that blanks and stays blank after the link returns
    // is its own fault, and one nobody notices until a dive.
    await say({});
    const notBack = clusterEls.filter(el=>txtOf(el)==='--' || txtOf(el)==='?');
    ok('every reading returns the moment the frames do', notBack.length===0,
       notBack.length ? ('still blank: '+notBack.map(e=>e.id+'="'+txtOf(e)+'"').join(', '))
                      : clusterEls.map(e=>e.id+'="'+txtOf(e)+'"').join(' '));

    // ================= 5. FOLDING NEVER HIDES AN ADMISSION =================
    // The attitude readings are advisory, so the operator is allowed to put them away —
    // and the moment a group can be folded, folding it becomes a way for a dead chip to
    // go quiet. That is this project's oldest failure wearing new clothes: not a wrong
    // number, but a subsystem's death arriving as silence. A folded group with a dead IMU
    // under it has to say so ON THE LINE THAT IS STILL VISIBLE, or the fold is a mute
    // button for the one thing that must not be mutable.
    //
    // Written against the two designs that are both acceptable — no fold at all, or a
    // fold whose head carries the mark — rather than against one file's markup, so a
    // cluster that later drops folding passes honestly instead of failing for a feature
    // it no longer has.
    const vis=el=>!!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
    const turn = found['Turn rate'];
    const grp  = turn && turn.closest ? turn.closest('.fgroup, [data-group]') : null;
    const head = grp ? grp.querySelector('.fg-head, button, [role="button"]') : null;
    if(!grp || !head){
      ok('a folded group cannot hide a dead chip (there is no fold to hide behind)',
         !!turn && vis(turn),
         grp ? 'a group with no heading to fold it by' : 'the readings are not in a foldable group'+
         (turn ? ', and #'+turn.id+' is on screen' : ''));
    } else {
      await say({gyro_z_dps:null, accel_fwd_ms2:null, pitch_deg:null, roll_deg:null,
                 heading:null, heading_card:null, mag_cal:null, sensor_faults:['bno085']});
      // A FINGER MUST BE ABLE TO REACH IT. head.click() dispatches straight at the
      // element and skips hit testing entirely, so it certifies the HANDLER while
      // saying nothing about whether anything on screen can invoke it. That is how
      // this suite passed 75/75 over a fold button sitting under a pointer-events:none
      // wrap, where every real tap landed on the map canvas beneath. Ask the document
      // what is actually at the button's centre before trusting a synthetic click.
      const hb = head.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.round(hb.left+hb.width/2),
                                            Math.round(hb.top+hb.height/2));
      ok('the fold control is reachable by a finger, not just by a synthetic click',
         !!hit && (hit===head || head.contains(hit)),
         hit ? ('the tap at the header’s centre lands on <'+hit.tagName.toLowerCase()+
                (hit.id?' id="'+hit.id+'"':'')+'>')
             : 'nothing is hit-testable at the header’s centre');
      head.click();                                  // fold it, dead chip and all
      await sleep(320);
      const folded = !vis(turn);
      const marks = [...grp.querySelectorAll('.m-flag, .fg-flag')]
        .filter(el=>vis(el) && (el.textContent||'').trim());
      ok('folding the group away actually hides the readings (the premise)', folded,
         'turn-rate readout visible while folded: '+vis(turn)+
         ' — if it does not fold there is nothing to hide and nothing to prove');
      ok('a folded group with a dead chip under it still says so, on the line still showing',
         marks.length>0,
         marks.length ? marks.map(el=>'"'+(el.textContent||'').trim()+'"').join(', ')
                      : 'the fold head carries no mark — four readings went to cannot-tell '
                      + 'and the only thing on screen saying so is behind the fold');
      // The mark is a readout like any other and owes the same sentence: an operator who
      // sees it while flying has to know what it is claiming without hovering anything
      // twice, and a screen reader has to get the same words.
      const bare = marks.filter(el=>((el.dataset.help||el.getAttribute('title')||'').trim().length<40)
                                 || ((el.getAttribute('aria-label')||'').trim().length<40));
      ok('...and that mark explains itself too', marks.length>0 && bare.length===0,
         bare.length ? ('no real explanation on: '+bare.map(el=>el.id||el.className).join(', '))
                     : marks.length+' visible mark(s), each with a sentence and an aria-label');
      head.click();                                  // put it back for the portrait
      await sleep(320);
      await say({});
      ok('unfolding brings the readings back', vis(turn) && /\d/.test(txtOf(turn)),
         '#'+turn.id+' visible='+vis(turn)+' text="'+txtOf(turn)+'"');
    }

    // ================= NOTHING ELSE BROKEN =================
    stopFeed();
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('the alert rail is not accusing anything on a healthy hull', alertText()==='none',
       'alerts="'+alertText()+'"');
    const need=['depth-val','pressure-val','heading-val','battery-v','speed-val','speed-src','alerts'];
    const missing=need.filter(id=>!$(id));
    ok('every element these checks read is still on the page', missing.length===0,
       missing.length ? 'MISSING: '+missing.join(', ')
                      : need.length+' base elements found, cluster resolved to: '+
                        (Object.keys(found).filter(n=>found[n]).map(n=>n+'=#'+found[n].id).join(', ')||'nothing'));

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

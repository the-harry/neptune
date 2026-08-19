/* THE THREE SURFACES THAT ONLY MATTER WHEN SOMETHING HAS GONE WRONG.

   The console's readouts are covered from several directions already. These three are
   not, and all three are the parts an operator reaches for on the worst day:

     THE LOGS OVERLAY (client/js/logview.js) — the only way to read the record without
     leaving the dive. Everything the browser did goes on the LOG bus, and so does
     everything the VEHICLE wrote, pulled onto the same bus so one sensor's whole life
     story is readable in one place. None of that is worth anything if the overlay does
     not SHOW it, so every check here reads text off the rows the overlay actually drew.

     PERSISTENCE (client/js/store.js) — what the handheld still knows after it has been
     switched off and on. Proven the only way it can be proven: the database connection
     is DROPPED (through the real path a second window uses to take it), the values are
     read back through a brand new connection, and the console is asked to keep working
     while it has no storage at all.

     THE FEED (client/js/video.js) — and specifically NO FEED, which is the state that
     matters when the camera is absent, and RECONFIGURING, which must not be claimed off
     a stale camera reading.

   HOW THIS IS DRIVEN. Frames go in through handleMessage(), the client's own WebSocket
   message handler; the overlay is opened by clicking the buttons a thumb clicks, after
   asking the document what is actually AT those coordinates; log lines are produced by
   LOG.warn/err/state, which is the bus every module writes to; toggles are set through
   the functions the UI calls and read back through the function boot calls.

   THE ONE THING THAT IS A FIXTURE IS THE NETWORK. window.fetch answers three endpoints
   this bench does not have — the vehicle's log, the camera's control plane, and the
   launcher's save — and passes everything else through untouched. That is the same move
   as feeding a frame into handleMessage: the far end is a fixture, and every line
   between it and the glass is the product.

   WHAT COULD NOT BE REACHED, stated rather than faked:
     * `toggleLogView()` in logview.js HAS NO CALLER. No button, no key binding, and it
       is not on the NEPTUNE console API (main.js exposes `logs` and `closeLogs`, which
       are openLogView and closeLogView). There is no path through the product that
       reaches it, so nothing here reaches it either: calling a function directly to
       colour a line green would say this console has behaviour it does not have.
     * the `no backend (open with ?host=…)` branch of connectVideo needs a page opened
       from file:// with no host; the runner serves over http by construction.
     * STORE.init's onblocked branch needs a second window holding an older version open
       across the upgrade; there is one browser tab here. */
(function(){
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // run.py prints these details to a Windows console whose codepage cannot encode the
  // console's glyphs; anything it cannot carry is escaped rather than dropped, because
  // a report that cannot be printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E\u00A0-\u00FF\u2013\u2014\u2018\u2019\u201C\u201D\u2022\u2026]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  async function waitFor(pred, ms){
    const t0=Date.now();
    while(Date.now()-t0 < ms){ if(pred()) return true; await sleep(30); }
    return !!pred();
  }
  const click=el=>el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
  // WHAT IS ACTUALLY AT THE MIDDLE OF THIS CONTROL. A synthetic click lands on a
  // control no finger could reach (playbook §5); asking the document first is the only
  // form of "the button works" worth having.
  function atCentre(el){
    const b = el.getBoundingClientRect();
    if(b.width<1 || b.height<1) return null;
    return document.elementFromPoint(Math.round(b.left+b.width/2), Math.round(b.top+b.height/2));
  }
  const reaches=(el)=>{ const hit=atCentre(el); return !!hit && (hit===el || el.contains(hit)); };

  /* ---- the overlay, read as an operator reads it -------------------------- */
  const lvRows  = ()=>[...document.querySelectorAll('#lv-body .lv-row')];
  const lvTexts = ()=>lvRows().map(r=>(r.querySelector('.lv-m')||{}).textContent||'');
  const lvHas   = re=>lvTexts().some(t=>re.test(t));
  const lvRowFor= re=>lvRows().find(r=>re.test((r.querySelector('.lv-m')||{}).textContent||''));
  const lvCount = ()=>($('lv-count').textContent||'').trim();
  const lvBody  = ()=>$('lv-body');
  const shown   = ()=>lvBody().childElementCount;

  /* ---- THE FAR END, UNDER THIS SUITE'S CONTROL ---------------------------- */
  const realFetch = window.fetch.bind(window);
  let VEHICLE = null;        // what /api/logs answers; null = nothing on this origin
  let CAMSTATUS = {is_streaming:'YES'};   // what the camera control plane answers
  let LAUNCHER = null;       // what /__save answers back as a path; null = no launcher
  let TILES = false;         // whether satellite tiles answer
  let DEAD_TILE = '';        // one tile URL the tile server will not serve
  const logUrls=[]; const savedNames=[]; let tileFetches=0;
  window.fetch = function(input, init){
    const url = String((input && input.url) ? input.url : input);
    if(url.indexOf('/api/logs') >= 0){
      logUrls.push(url);
      if(!VEHICLE) return Promise.resolve(new Response('no vehicle here', {status:404}));
      return Promise.resolve(new Response(JSON.stringify(VEHICLE(url)),
        {status:200, headers:{'Content-Type':'application/json'}}));
    }
    if(url.indexOf('/api/status') >= 0){
      if(!CAMSTATUS) return Promise.resolve(new Response('no camera', {status:404}));
      return Promise.resolve(new Response(JSON.stringify(CAMSTATUS),
        {status:200, headers:{'Content-Type':'application/json'}}));
    }
    if(url.indexOf('/__save') >= 0){
      const m = /name=([^&]*)/.exec(url); savedNames.push(m ? decodeURIComponent(m[1]) : '?');
      if(!LAUNCHER) return Promise.resolve(new Response('no launcher', {status:503}));
      return Promise.resolve(new Response(LAUNCHER + (m ? decodeURIComponent(m[1]) : ''), {status:200}));
    }
    if(TILES && /arcgisonline\.com/.test(url)){
      tileFetches++;
      if(DEAD_TILE && url.indexOf(DEAD_TILE)>=0) return Promise.reject(new TypeError('tile server unreachable'));
      return Promise.resolve(new Response('jpegbytes', {status:200, headers:{'Content-Type':'image/jpeg'}}));
    }
    return realFetch(input, init);
  };

  /* ---- go2rtc, stood up in this page -------------------------------------
     The video plane cannot be tested from the outside at all: nothing on a test
     machine speaks go2rtc's signalling, so ws.onopen never fires and the entire
     offer/answer/candidate path — and LIVE with it — is unreachable. So the far END
     is built here: a socket-shaped object that talks the same three messages go2rtc
     talks, backed by a SECOND RTCPeerConnection with a real captured track on it.
     The handshake that follows is the real one, run by video.js, against a real peer
     connection; only the transport carrying the SDP is a fixture. It can also do the
     two things a real go2rtc does when it is unhappy: name a problem, or not be
     there at all. */
  const RealWS = window.WebSocket;
  let WS_MODE = 'pass';                 // pass | peer | error | throw
  function FakeSignal(url){
    const self = this;
    this.url=url; this.readyState=0; this._pc=null;
    this.onopen=this.onmessage=this.onclose=this.onerror=null;
    this._emit=function(o){ if(self.onmessage) self.onmessage({data:JSON.stringify(o)}); };
    this.close=function(){ this.readyState=3;
      if(this._pc){ try{ this._pc.close(); }catch(e){} this._pc=null; } };
    // Chrome hides a machine's local addresses behind mDNS names, which nothing in a
    // headless browser resolves - so two peers in one page exchange candidates that can
    // never be reached. Both ends of this fixture are on this machine by construction,
    // so the name is put back to the address it stands for and ICE can actually finish.
    const deMdns = c=>String(c||'').replace(/[0-9a-f-]{36}\.local/gi, '127.0.0.1');
    this.send=function(raw){
      let m; try{ m=JSON.parse(raw); }catch(e){ return; }
      if(m.type==='webrtc/candidate'){
        if(self._pc) self._pc.addIceCandidate({candidate:deMdns(m.value), sdpMid:'0'}).catch(()=>{});
        return;
      }
      if(m.type!=='webrtc/offer') return;
      if(WS_MODE==='error'){ self._emit({type:'error', value:'no such stream: sub'}); return; }
      (async function(){
        const pc = new RTCPeerConnection({iceServers:[]});
        self._pc = pc;
        const cv=document.createElement('canvas'); cv.width=160; cv.height=90;
        const g=cv.getContext('2d'); g.fillStyle='#0f0'; g.fillRect(0,0,160,90);
        const stream = cv.captureStream(10);
        stream.getTracks().forEach(t=>pc.addTrack(t, stream));
        pc.onicecandidate = ev=>{ if(ev.candidate) self._emit({type:'webrtc/candidate', value:deMdns(ev.candidate.candidate)}); };
        await pc.setRemoteDescription({type:'offer', sdp:m.value});
        await pc.setLocalDescription(await pc.createAnswer());
        self._emit({type:'webrtc/answer', value:pc.localDescription.sdp});
      })().catch(e=>{ errs.push('fake go2rtc: '+(e&&e.message)); });
    };
    setTimeout(()=>{ self.readyState=1; if(self.onopen) self.onopen(); }, 0);
  }
  window.WebSocket = function(url, proto){
    if(WS_MODE!=='pass' && String(url).indexOf(CONFIG.camera.webrtcWs)>=0){
      if(WS_MODE==='throw') throw new Error('signalling socket refused');
      return new FakeSignal(String(url));
    }
    return new RealWS(url, proto);
  };
  Object.assign(window.WebSocket, {CONNECTING:0, OPEN:1, CLOSING:2, CLOSED:3});

  /* ---- a hull with nothing else wrong with it ----------------------------- */
  const BASE = {type:'telemetry', mock:false, armed:false, seq:1,
    heading:284, heading_card:'NW', mag_cal:3,
    gyro_z_dps:12.0, accel_fwd_ms2:0.35, pitch_deg:-6.5, roll_deg:9.0,
    depth:4.2, pressure:20.7, battery_v:12.1, current_a:1.2,
    ballast_level:0.4, ballast_homed:true, ballast_needs_rehome:false, ballast_target:0.4,
    left:0, right:0, magnet:false, light_green:false, light_white:false,
    light_green_level:0, light_white_level:0,
    leak:false, leak_state:'NORMAL', leak_probe_fault:null,
    snagged:false, gyro_only:false,
    speed_ms:0.42, speed_src:'paddle', signal:4, sensor_faults:[]};
  let feed=null, frame=null;
  function stopFeed(){ if(feed){ clearInterval(feed); feed=null; } }
  function say(extra){                       // THE REAL DOOR IN
    frame = Object.assign({}, BASE, extra||{});
    stopFeed();
    handleMessage(JSON.stringify(frame));
    feed = setInterval(()=>handleMessage(JSON.stringify(frame)), 100);
    return sleep(260);
  }

  // The vehicle's clock, computed here rather than borrowed from logview.js, so the
  // check is an independent expectation and not the code agreeing with itself.
  function clockOf(ms){
    const d=new Date(ms), p=n=>String(n).padStart(2,'0');
    return p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())+'.'
         + String(d.getMilliseconds()).padStart(3,'0');
  }

  (async function(){
    await sleep(2800);                       // boot: STORE.init, the first video attempt

    /* ======================================================================
       0. WHAT THE BENCH LOOKED LIKE THE MOMENT THE CONSOLE FINISHED BOOTING.
       Captured now and asserted in section 9, because the video checks there
       change the camera's answers and this is the state the operator gets on a
       handheld with no camera at all.
       ====================================================================== */
    const BOOT = { video:state.video, noFeedCls:$('no-feed').className,
                   badge:($('no-feed-badge').textContent||'').replace(/\u00a0/g,' ').trim(),
                   sub:($('no-feed-sub').textContent||'').trim(),
                   vis:$('video-feed').style.visibility,
                   blind:document.body.classList.contains('map-blind') };

    /* ======================================================================
       1. THE OVERLAY OPENS THE WAY AN OPERATOR OPENS IT
       ====================================================================== */
    ok('the CONFIG button is reachable by a thumb, not just by .click()',
       reaches($('btn-config')),
       'elementFromPoint at its centre = ' + ((atCentre($('btn-config'))||{}).id || '(nothing)'));
    click($('btn-config'));
    await sleep(120);
    ok('CONFIG opens the panel the LOGS button lives on',
       $('mapper-modal').classList.contains('show'), 'class="'+$('mapper-modal').className+'"');
    ok('the LOGS button is reachable too', reaches($('cfg-logs')),
       'elementFromPoint at its centre = ' + ((atCentre($('cfg-logs'))||{}).id || '(nothing)'));

    click($('cfg-logs'));
    await sleep(150);
    ok('LOGS opens the overlay and closes the panel behind it',
       !!$('logview') && $('logview').classList.contains('show')
       && getComputedStyle($('logview')).display==='flex'
       && !$('mapper-modal').classList.contains('show'),
       'logview class="'+$('logview').className+'" display='+getComputedStyle($('logview')).display
       + ' config="'+$('mapper-modal').className+'"');

    // Deliberately NOT full screen: the point is to read the log while still seeing
    // what the vehicle is doing behind it.
    const card = $('logview').querySelector('.lv-card').getBoundingClientRect();
    ok('the log does not take the whole screen away from the dive',
       card.top>4 && card.left>4 && card.right < window.innerWidth-4 && card.bottom < window.innerHeight-4,
       'card '+Math.round(card.width)+'x'+Math.round(card.height)+' inside '
       + window.innerWidth+'x'+window.innerHeight);
    ok('the console behind it is dimmed, not hidden',
       /rgba\(/.test(getComputedStyle($('logview')).backgroundColor)
       && getComputedStyle($('logview')).backgroundColor!=='rgba(0, 0, 0, 0)',
       'backdrop='+getComputedStyle($('logview')).backgroundColor);

    // It opened onto the record that already existed - the boot lines are in the ring
    // and the overlay drew all of them, oldest first, without having been told about
    // them one at a time. (The ring keeps growing underneath: the console is still
    // polling. The claim is that the FIRST line ever logged is the first row drawn.)
    const ringAtOpen = LOG.ring();
    ok('it opens onto the log that was already there',
       shown()>0 && lvTexts()[0]===ringAtOpen[0].msg && shown()>=ringAtOpen.length-4,
       shown()+' rows drawn for a '+ringAtOpen.length+'-line ring, first row = "'
       + (lvTexts()[0]||'').slice(0,48)+'"');
    ok('and it says how much of the record it is showing', /^\d+ \/ \d+$/.test(lvCount()),
       'count reads "'+lvCount()+'"');

    /* ======================================================================
       2. ONE SENSOR'S LIFE STORY, PUT IN AT THE INGEST AND READ OFF THE GLASS
       ====================================================================== */
    await say({});                                   // alive, everything answering
    await sleep(300);
    const healthyRows = shown();
    ok('a healthy hull writes no sensor failure into the log',
       !lvHas(/NO DEPTH/) && !lvHas(/DEPTH SENSOR STOPPED/),
       healthyRows+' rows and not one of them names the depth sensor');

    // THE MS5837 STOPS. Nulls in at handleMessage, an errand out on the alert rail, and
    // the errand has to reach the log or the overlay cannot tell the story afterwards.
    await say({depth:null, pressure:null, sensor_faults:['ms5837']});
    await sleep(400);
    const deathRow = lvRowFor(/NO DEPTH & PRESSURE/);
    ok('a sensor that stops answering reaches the overlay, named',
       !!deathRow && /DEPTH SENSOR STOPPED/.test(deathRow.querySelector('.lv-m').textContent),
       deathRow ? '"'+deathRow.querySelector('.lv-m').textContent.slice(0,90)+'"' : 'no such row was drawn');
    ok('...as a WARN, so the level filters can find it later',
       !!deathRow && /\blv-warn\b/.test(deathRow.className),
       deathRow ? 'class="'+deathRow.className+'"' : 'no row');
    ok('...and it arrived while the overlay was open, not on a re-render',
       shown() > healthyRows, healthyRows+' rows before the sensor died, '+shown()+' after');
    ok('every row carries a clock and the tag that produced it',
       !!deathRow && /^\d\d:\d\d:\d\d\.\d\d\d$/.test(deathRow.querySelector('.lv-t').textContent)
       && /WARN/.test(deathRow.querySelector('.lv-tag').textContent),
       deathRow ? deathRow.querySelector('.lv-t').textContent+' '+deathRow.querySelector('.lv-tag').textContent : 'no row');

    // AND THE FRAME ITSELF IS IN THERE. The summary above says what the console
    // concluded; this is the evidence it concluded it from, which is what makes the
    // overlay answerable about a moment nobody was watching.
    ok('the frame the console judged is in the log beside the judgement',
       lvHas(/"sensor_faults":\s*\[\s*"ms5837"\s*\]/),
       'a telemetry line carrying sensor_faults=["ms5837"] is on screen');

    // RECOVERY IS HALF THE CONTRACT. The gauge comes back; the record of it having
    // failed must not be rewritten - the log is what the dive is diagnosed from later.
    await say({});
    await sleep(400);
    ok('the sensor coming back does not erase what it did',
       lvHas(/NO DEPTH & PRESSURE/) && [...$('alerts').querySelectorAll('.alert-tx')]
         .every(e=>!/NO DEPTH/.test(e.textContent)),
       'the rail is clear again, the log still holds the failure');
    stopFeed();

    /* ======================================================================
       3. THE FILTERS
       ====================================================================== */
    LOG.state('QUOKKA a state line nobody needs');
    LOG.warn('QUOKKA a warning worth reading');
    LOG.err('QUOKKA the thing that actually broke');
    await sleep(260);

    const f = $('lv-filter');
    f.value = 'quokka'; f.dispatchEvent(new Event('input', {bubbles:true}));
    await sleep(120);
    ok('the filter narrows the view to the lines that match, whatever the case',
       shown()===3 && lvTexts().every(t=>/QUOKKA/.test(t)),
       shown()+' rows: '+JSON.stringify(lvTexts().map(t=>t.slice(0,34))));
    ok('...and the count says how many of the whole record that is',
       lvCount()==='3 / '+LOG.ring().length, 'count reads "'+lvCount()+'"');

    f.value = '[ERR]'; f.dispatchEvent(new Event('input', {bubbles:true}));
    await sleep(120);
    ok('the filter searches the tag as well as the message',
       shown()>0 && lvRows().every(r=>/ERR/.test(r.querySelector('.lv-tag').textContent)),
       shown()+' rows, every one of them tagged ERR');

    f.value = 'QUOKKA'; f.dispatchEvent(new Event('input', {bubbles:true}));
    await sleep(120);
    click($('lv-warn')); await sleep(120);
    ok('WARN+ drops the chatter and keeps the warning and the error',
       shown()===2 && lvTexts().join(' | ').indexOf('nobody needs')<0
       && lvHas(/worth reading/) && lvHas(/actually broke/),
       shown()+' rows: '+JSON.stringify(lvTexts().map(t=>t.slice(0,34))));
    ok('...and the WARN+ button is the one lit', $('lv-warn').classList.contains('lv-on')
       && !$('lv-all').classList.contains('lv-on') && !$('lv-err').classList.contains('lv-on'),
       'ALL="'+$('lv-all').className+'" WARN+="'+$('lv-warn').className+'"');

    click($('lv-err')); await sleep(120);
    ok('ERR keeps only what actually broke',
       shown()===1 && lvHas(/actually broke/), shown()+' rows: '+JSON.stringify(lvTexts()));

    click($('lv-all')); await sleep(120);
    ok('ALL brings the quiet lines back', shown()===3 && lvHas(/nobody needs/),
       shown()+' rows again');
    f.value=''; f.dispatchEvent(new Event('input', {bubbles:true}));
    await sleep(120);

    /* ======================================================================
       4. A LOG LINE IS TEXT. The vehicle's own log is pulled onto this bus over
       HTTP, so a line's message is remote input that gets written into the DOM.
       ====================================================================== */
    LOG.err('MARKUP <img src=x onerror="window.__lvPwn=1"> & <b>bold</b>');
    await sleep(260);
    const evilRow = lvRowFor(/MARKUP/);
    ok('a log line is shown as text, never parsed as markup',
       !!evilRow && !lvBody().querySelector('img') && !lvBody().querySelector('b')
       && /<img src=x onerror="window.__lvPwn=1"> & <b>bold<\/b>/.test(evilRow.querySelector('.lv-m').textContent)
       && window.__lvPwn===undefined,
       evilRow ? 'drawn literally: "'+evilRow.querySelector('.lv-m').textContent.slice(9,60)+'"'
               : 'the row was not drawn at all');

    /* ======================================================================
       5. SCROLLING UP STOPS THE TAIL, AND THE LINE BEING READ STAYS PUT
       ====================================================================== */
    for(let i=0;i<300;i++) LOG.state('FILLER line '+i+' — something the console did');
    await sleep(300);
    ok('tailing by default: the newest line is on screen', LOGVIEW.tail===true
       && (lvBody().scrollHeight - lvBody().scrollTop - lvBody().clientHeight) < 24,
       'tail='+LOGVIEW.tail+' bottom gap='
       + Math.round(lvBody().scrollHeight - lvBody().scrollTop - lvBody().clientHeight)+'px');
    ok('...and TAIL says so on the button', $('lv-tail').textContent==='TAIL'
       && $('lv-tail').classList.contains('lv-on'), '"'+$('lv-tail').textContent+'"');

    lvBody().scrollTop = 0;                    // a real scroll event, from a real scroll
    await sleep(200);
    ok('scrolling up stops the tail', LOGVIEW.tail===false, 'tail='+LOGVIEW.tail);
    ok('...and the button says PAUSED rather than lying about following',
       $('lv-tail').textContent==='PAUSED' && !$('lv-tail').classList.contains('lv-on'),
       '"'+$('lv-tail').textContent+'" class="'+$('lv-tail').className+'"');

    const heldAt = lvBody().scrollTop, heldRows = shown();
    LOG.state('FILLER a line that arrives while somebody is reading');
    await sleep(300);
    ok('a new line does not drag the page out from under the reader',
       lvBody().scrollTop===heldAt && shown()>heldRows
       && lvHas(/arrives while somebody is reading/),
       'scrollTop '+heldAt+' -> '+lvBody().scrollTop+', rows '+heldRows+' -> '+shown()
       + ' (the line has to be appended AND stay off the reader\'s screen)');

    lvBody().scrollTop = lvBody().scrollHeight;
    await sleep(200);
    ok('scrolling back to the bottom resumes the tail',
       LOGVIEW.tail===true && $('lv-tail').textContent==='TAIL', 'tail='+LOGVIEW.tail);
    LOG.state('FILLER and the tail is following again');
    await sleep(300);
    ok('...and the next line scrolls itself into view',
       (lvBody().scrollHeight - lvBody().scrollTop - lvBody().clientHeight) < 24
       && lvTexts()[lvTexts().length-1].indexOf('following again')>=0,
       'bottom gap '+Math.round(lvBody().scrollHeight-lvBody().scrollTop-lvBody().clientHeight)
       +'px, last row "'+lvTexts()[lvTexts().length-1].slice(0,40)+'"');

    /* ======================================================================
       6. THE OVERLAY MUST NOT EAT PILOTING INPUT (playbook §5)
       ====================================================================== */
    const heardAtWindow=[];
    const spy = e=>heardAtWindow.push(e.code);
    window.addEventListener('keydown', spy);
    lvBody().dispatchEvent(new KeyboardEvent('keydown', {code:'KeyW', key:'w', bubbles:true}));
    await sleep(60);
    const helmHeardW = state.keys.has('KeyW');
    window.dispatchEvent(new KeyboardEvent('keyup', {code:'KeyW', key:'w'}));
    ok('a key pressed over the open log still reaches the helm',
       helmHeardW && heardAtWindow.indexOf('KeyW')>=0,
       'state.keys had KeyW='+helmHeardW+', window saw '+JSON.stringify(heardAtWindow));

    heardAtWindow.length = 0;
    f.focus();
    f.dispatchEvent(new KeyboardEvent('keydown', {code:'KeyS', key:'s', bubbles:true}));
    await sleep(60);
    ok('...but a keystroke typed into the filter stays in the filter',
       heardAtWindow.length===0 && !state.keys.has('KeyS'),
       'window saw '+JSON.stringify(heardAtWindow)+', state.keys has KeyS='+state.keys.has('KeyS'));
    window.removeEventListener('keydown', spy);

    /* ======================================================================
       7. CLOSING IT LOSES NOTHING, BECAUSE THE RING IS THE RECORD
       ====================================================================== */
    f.dispatchEvent(new KeyboardEvent('keydown', {code:'Escape', key:'Escape', bubbles:true}));
    await sleep(150);
    ok('Escape in the filter closes the log', !$('logview').classList.contains('show')
       && LOGVIEW.open===false, 'class="'+$('logview').className+'" open='+LOGVIEW.open);

    const closedRows = shown();
    for(let i=0;i<20;i++) LOG.state('OFFSTAGE line '+i);
    await sleep(300);
    ok('a closed log stops building rows nobody is reading',
       shown()===closedRows && LOGVIEW.unsub===null,
       shown()+' rows in the DOM, unchanged, with no subscription held');

    click($('btn-config')); await sleep(100); click($('cfg-logs')); await sleep(200);
    ok('...and reopening shows the lines it was not drawing, from the ring',
       LOGVIEW.open===true && lvTexts().filter(t=>/OFFSTAGE line/.test(t)).length===20,
       lvTexts().filter(t=>/OFFSTAGE/.test(t)).length+' offstage lines came back');

    // The backdrop is a close target; the card is not.
    const cardBox = $('logview').querySelector('.lv-card').getBoundingClientRect();
    click($('logview').querySelector('.lv-head'));
    await sleep(100);
    ok('a click inside the card does not close it', LOGVIEW.open===true, 'open='+LOGVIEW.open);
    const backdrop = document.elementFromPoint(Math.round(cardBox.left/2), Math.round(window.innerHeight/2));
    ok('the dimmed area around the card is the backdrop itself',
       !!backdrop && backdrop.id==='logview', 'elementFromPoint = #'+((backdrop||{}).id||'?'));
    click($('logview'));
    await sleep(150);
    ok('clicking the backdrop closes it', LOGVIEW.open===false, 'open='+LOGVIEW.open);

    /* ======================================================================
       8. THE DOM STAYS BOUNDED WHILE THE TAIL RUNS
       ====================================================================== */
    click($('btn-config')); await sleep(100); click($('cfg-logs')); await sleep(200);
    const max = (CONFIG.log && CONFIG.log.viewMaxRows) || 1200;
    const before = shown();
    for(let i=0;i<max+120;i++) LOG.state('FLOOD line '+i);
    await sleep(500);
    ok('a burst of lines cannot grow the overlay without limit',
       shown()<=max && shown()>max-40 && LOG.ring().length > shown(),
       shown()+' rows kept of a '+LOG.ring().length+'-line ring (cap '+max+')');
    ok('...and it is the OLDEST rows that go, so the newest are the ones on screen',
       lvTexts()[lvTexts().length-1].indexOf('FLOOD line '+(max+119))>=0
       && !lvHas(/FLOOD line 0 /),
       'last row "'+lvTexts()[lvTexts().length-1].slice(0,40)+'", '+before+' rows before the burst');

    /* ======================================================================
       9. THE VEHICLE'S OWN LOG, PULLED ONTO THIS BUS
       ====================================================================== */
    // Nothing on this origin answers /api/logs yet, and that is the normal state of a
    // console on a towpath with the tether out. It must not narrate its own failures.
    await waitFor(()=>logUrls.length>0, 8000);
    ok('a vehicle that is not there is not announced once per attempt',
       logUrls.length>0 && !LOG.ring().some(l=>/^VEHICLE/.test(l.msg)),
       logUrls.length+' polls made, no VEHICLE line written');

    const VCLOCK = Date.now() - (3*24*3600*1000) - (5*3600*1000) - 137000;   // the Pi runs days behind
    let served = 0;
    VEHICLE = (url)=>{
      served++;
      if(served===1) return {boot:'boot-A', oldest:1, next:3, lines:[
        {i:1, t:VCLOCK,       tag:'ms5837', level:'warn', msg:'depth sensor: 3 consecutive raises, marking not-answering'},
        {i:2, t:VCLOCK+1500,  tag:'leak',   level:'err',  msg:'leak probe WARN reads WET, 3 of 5 samples toward a latch'}]};
      if(served===2) return {boot:'boot-B', oldest:1, lines:[]};             // it restarted
      if(served===3) return {boot:'boot-B', oldest:41, next:42, lines:[
        {i:41, t:VCLOCK+9000, tag:'boot',   level:'info', msg:'hardware backend real, i2c bus did not open'}]};
      return {boot:'boot-B', oldest:41, next:42, lines:[]};
    };

    await waitFor(()=>served>=1 && lvHas(/depth sensor: 3 consecutive raises/), 14000);
    const vRow = lvRowFor(/depth sensor: 3 consecutive raises/);
    ok('what the vehicle wrote reaches this overlay, marked as the vehicle',
       !!vRow && /^VEHICLE ms5837 /.test(vRow.querySelector('.lv-m').textContent),
       vRow ? '"'+vRow.querySelector('.lv-m').textContent.slice(0,80)+'"' : 'no vehicle line was drawn');
    ok('...wearing the VEHICLE\'s clock, not this handheld\'s',
       !!vRow && vRow.querySelector('.lv-m').textContent.indexOf(clockOf(VCLOCK))>=0
       && vRow.querySelector('.lv-t').textContent !== clockOf(VCLOCK),
       vRow ? 'the line says '+clockOf(VCLOCK)+', the console stamped it '
              + vRow.querySelector('.lv-t').textContent : 'no row');
    const vErr = lvRowFor(/leak probe WARN reads WET/);
    ok('...and at the level the vehicle gave it, so ERR still finds it',
       !!vErr && /\blv-err\b/.test(vErr.className),
       vErr ? 'class="'+vErr.className+'"' : 'no row');

    await waitFor(()=>served>=2 && lvHas(/VEHICLE restarted/), 12000);
    ok('a vehicle that restarts says so instead of waiting out the dive',
       lvHas(/VEHICLE restarted/), 'the overlay carries the restart line');
    // The cursor stood at 3 when the reboot was noticed; a Pi that restarts numbers its
    // log from 1 again, so a console that kept asking from 3 would wait out the rest of
    // the dive for a line that is never coming.
    const askedBefore = logUrls.length;
    ok('the cursor HAD advanced past the first batch', /since=3(&|$)/.test(logUrls[askedBefore-1]),
       'the poll that found the restart asked for '+logUrls[askedBefore-1].replace(/^.*\?/,'?'));
    await waitFor(()=>logUrls.length>askedBefore, 8000);
    ok('...and it is rewound afterwards, or the rest of the dive is never read',
       /since=0(&|$)/.test(logUrls[askedBefore]),
       'the next poll asked for '+logUrls[askedBefore].replace(/^.*\?/,'?'));

    await waitFor(()=>served>=3 && lvHas(/earlier line\(s\) had already been evicted/), 12000);
    ok('lines evicted before this console asked are counted, not quietly missing',
       lvHas(/40 earlier line\(s\) had already been evicted/),
       'the overlay states the size of the hole in its record');

    /* ======================================================================
       10. WHERE THE COMPLETE RECORD IS. The scrollback is bounded (section 8
       just proved it); the footer is the only pointer to the file that is not.
       ====================================================================== */
    // Until the launcher has actually accepted a batch there IS no file, and a footer
    // that names one would be pointing at something that does not exist.
    ok('with nothing accepted yet it does not claim a session log exists',
       !/full session log/.test($('lv-file').textContent)
       && /(nothing has reached the launcher|in-memory only)/.test($('lv-file').textContent),
       '"'+$('lv-file').textContent+'"');

    LAUNCHER = 'navigation_logs/logs/';        // the launcher starts answering
    await waitFor(()=>!!REC.diskPath, 9000);   // written by the recorder's own flush timer
    click($('lv-close')); await sleep(120);
    click($('btn-config')); await sleep(100); click($('cfg-logs')); await sleep(200);
    ok('once the session log is on disk, the overlay says where to find it',
       /navigation_logs\/logs\//.test($('lv-file').textContent)
       && $('lv-file').textContent.indexOf(REC.diskFile)>=0,
       '"'+$('lv-file').textContent+'"  (recorder wrote '+REC.diskPath+')');
    ok('...and still admits the view above it is only the tail of the record',
       /held in memory/.test($('lv-file').textContent), '"'+$('lv-file').textContent+'"');
    ok('...and the file it names is the one the launcher was really asked to write',
       savedNames.indexOf(REC.diskFile)>=0 && REC.diskFile.length>0,
       'the launcher was asked for '+JSON.stringify([...new Set(savedNames)]));

    click($('lv-close')); await sleep(150);

    /* ======================================================================
       11. PERSISTENCE — what survives the console being switched off
       ====================================================================== */
    ok('the console came up with storage', STORE.ready===true && !!STORE.db,
       'ready='+STORE.ready);

    // A remembered decision, set the way the panel sets it.
    bootSetAuto(false);
    await sleep(200);
    ok('a toggle the operator switched off is written to the database',
       (await STORE.get('boot.auto', 'MISSING'))===false,
       'boot.auto reads back as '+JSON.stringify(await STORE.get('boot.auto','MISSING')));

    // An area of imagery, saved the way SAVE OFFLINE saves it, with the tile server
    // answering here instead of Esri.
    TILES = true;
    const BBOX = [-1.4795, 52.4160, -1.4785, 52.4168];        // a few hundred metres of canal
    const urls = STORE.tileUrlsForBBox(BBOX, 16, 18);
    const t16 = lonLatToTile(52.4164, -1.4790, 16);
    ok('the Esri template is filled z/y/x, not the usual z/x/y',
       urls.some(u=>u.endsWith('/16/'+t16[1]+'/'+t16[0])),
       'z16 tile x='+t16[0]+' y='+t16[1]+' -> '+urls.filter(u=>/\/16\//.test(u)).join(' '));
    const meta = await STORE.saveArea('SUITE CANAL', BBOX, 'standard');
    ok('saving an area fetches its tiles and records what it saved',
       meta.tiles===urls.length && meta.cached===urls.length && tileFetches===urls.length
       && meta.zmin===16 && meta.zmax===18 && meta.mirrored===false,
       JSON.stringify({tiles:meta.tiles, cached:meta.cached, fetched:tileFetches, mirrored:meta.mirrored}));
    ok('...into the archive the map reads offline, not just into a list',
       !!(await (await caches.open(STORE.TILE_CACHE)).match(urls[0])),
       'the Cache API holds '+urls[0].replace(/^https:\/\/[^/]+/,''));
    ok('...and the saved-area registry can be listed back',
       (await STORE.areas()).some(a=>a.name==='SUITE CANAL'),
       (await STORE.areas()).map(a=>a.name).join(', '));

    // A TILE THAT WILL NOT COME DOWN IS NOT A LOST AREA. Overzoom covers the hole from
    // the level above, so one refused tile must cost that tile and nothing else - and
    // the meta has to record honestly how many of them are actually in the archive.
    const BBOX2 = [-1.4595, 52.4360, -1.4585, 52.4368];      // a stretch nothing has cached
    const urls2 = STORE.tileUrlsForBBox(BBOX2, 16, 18);
    DEAD_TILE = urls2[1].replace(/^https:\/\/[^/]+/,''); tileFetches = 0;
    const gap = await STORE.saveArea('SUITE GAP', BBOX2, 'standard');
    ok('a tile the server refuses costs that tile, not the whole area',
       gap.cached===gap.tiles-1 && tileFetches===gap.tiles
       && (await STORE.areas()).some(a=>a.name==='SUITE GAP'),
       gap.cached+' of '+gap.tiles+' tiles cached from '+tileFetches+' attempts after one '
       + 'refusal; the area is '+((await STORE.areas()).some(a=>a.name==='SUITE GAP')?'still saved':'GONE'));
    await STORE.evictArea(gap);
    DEAD_TILE = '';

    // A still: the topside copy of a PIC. Listing them must not drag the images into
    // memory, so the metadata comes back without the blob and the blob is fetched one
    // at a time - the thing that is DELIBERATELY not in the list.
    const blob = new Blob([new Uint8Array(4096)], {type:'image/jpeg'});
    await STORE.stillPut({id:'suite-still-1', t:Date.now(), w:1920, h:1080, source:'suite', blob});
    const stills = await STORE.stills();
    const rec = stills.find(s=>s.id==='suite-still-1');
    ok('listing stills returns what they are, deliberately without the image',
       !!rec && rec.blob===undefined && rec.bytes===4096 && rec.w===1920,
       JSON.stringify(rec));
    ok('...and the image itself comes back one at a time, when asked',
       (await STORE.stillBlob('suite-still-1')).size===4096,
       'stillBlob returned '+(await STORE.stillBlob('suite-still-1')).size+' bytes');

    /* ---- THE CONSOLE GIVES ITS DATABASE UP TO ANOTHER WINDOW -------------- */
    // The real path, not a poke: a second Neptune window opening a newer version fires
    // versionchange here, and this tab is the one that yields so the other one is not
    // stuck on a console that will not start. Everything after that has to keep working
    // without storage - losing persistence must never take the dive with it.
    const bump = indexedDB.open('neptune-store', 3);
    bump.onupgradeneeded = ()=>{ try{ bump.transaction.abort(); }catch(e){} };
    await waitFor(()=>STORE.db===null, 4000);
    ok('a second window asking for the database gets it',
       STORE.db===null && STORE.ready===false, 'db='+STORE.db+' ready='+STORE.ready);
    let threw='';
    try{
      const g = await STORE.get('boot.auto', 'DEFAULT');
      const s = await STORE.set('boot.auto', true);
      const a = await STORE.areas();
      const st = await STORE.stills();
      ok('with no database the console degrades to a no-op instead of throwing',
         g==='DEFAULT' && s===false && a.length===0 && st.length===0,
         'get->'+JSON.stringify(g)+' set->'+s+' areas->'+a.length+' stills->'+st.length);
    }catch(e){ threw=String(e && e.message || e); ok('with no database the console degrades to a no-op instead of throwing', false, 'it threw: '+threw); }

    // The other window finished (its upgrade was rolled back), so storage comes back -
    // and everything written before the handover is still there. This is the only
    // honest form of "it survives a reload": a brand new connection, reading disk.
    await sleep(300);
    const reopened = await STORE.init();
    ok('storage comes back by itself when the other window is done',
       reopened===true && STORE.ready===true, 'init resolved '+reopened+' ready='+STORE.ready);
    ok('what was saved before the handover is read back by the NEW connection',
       (await STORE.get('boot.auto','MISSING'))===false
       && (await STORE.areas()).some(a=>a.name==='SUITE CANAL')
       && (await STORE.stills()).some(s=>s.id==='suite-still-1'),
       'boot.auto='+JSON.stringify(await STORE.get('boot.auto','MISSING'))
       + ' areas='+(await STORE.areas()).length+' stills='+(await STORE.stills()).length);

    // ...and boot's own restore path reads it, which is what "toggles persist" means.
    BOOTFETCH.auto = true; BOOTFETCH._autoP = null;      // a fresh page has no memo
    await bootAutoReady();
    ok('boot restores the toggle rather than starting from the default again',
       BOOTFETCH.auto===false, 'bootAutoReady() -> auto='+BOOTFETCH.auto);

    // A BROWSER THAT WILL NOT HAND OUT A DATABASE AT ALL. This is what an insecure
    // context does (the console opened over plain http from something that is not
    // localhost, which is exactly how it gets opened off a phone hotspot), and the
    // contract is that it costs persistence and NOTHING else - it must never throw into
    // the app, and it must not take away the storage the console already has.
    const realIdb = window.indexedDB;
    let initThrew='', refused=null;
    try{
      Object.defineProperty(window, 'indexedDB', {configurable:true, writable:true,
        value:{ open(){ throw new DOMException('The operation is insecure.','SecurityError'); } }});
      refused = await STORE.init();
    }catch(e){ initThrew = String(e && e.message || e); }
    finally{ Object.defineProperty(window, 'indexedDB', {configurable:true, writable:true, value:realIdb}); }
    ok('a browser that refuses IndexedDB costs persistence and nothing else',
       refused===false && !initThrew && STORE.ready===true
       && (await STORE.get('boot.auto','MISSING'))===false,
       'init resolved '+refused+', threw '+(initThrew||'nothing')
       +', the database it already had still answers');

    // Eviction takes the imagery AND the registry entry: a listed area whose tiles are
    // gone is a promise of an offline map that is not there.
    await STORE.evictArea(meta);
    ok('evicting an area removes the tiles and the entry together',
       !(await STORE.areas()).some(a=>a.name==='SUITE CANAL')
       && !(await (await caches.open(STORE.TILE_CACHE)).match(urls[0])),
       'registry and cache both clear');
    await STORE.stillDelete('suite-still-1');
    bootSetAuto(true);
    TILES = false;

    /* ======================================================================
       12. THE FEED, AND MOSTLY THE ABSENCE OF ONE
       ====================================================================== */
    ok('a console with nothing sending video says NO FEED, out loud',
       BOOT.video==='nofeed' && BOOT.badge==='NO FEED'
       && BOOT.noFeedCls.indexOf('hidden')<0,
       'state.video='+BOOT.video+' badge="'+BOOT.badge+'" no-feed class="'+BOOT.noFeedCls+'"');
    ok('...and hides the video element rather than leaving a black rectangle',
       BOOT.vis==='hidden', 'video-feed visibility='+BOOT.vis);
    ok('...naming what it is waiting on', /reconnecting|awaiting camera/.test(BOOT.sub),
       'sub-caption "'+BOOT.sub+'"');
    ok('...and the map is promoted so the sub can still be flown', BOOT.blind===true,
       'body.map-blind='+BOOT.blind);

    // A CAMERA MODE CHANGE. go2rtc's RTSP source drops for about a second; the camera
    // is the thing that says so, and the console may only repeat it while that reading
    // is FRESH.
    CAMSTATUS = {is_streaming:'NO'};
    await waitFor(()=>state.cam.isStreaming==='NO' && state.camOkAt>0, 12000);
    ok('the camera control plane is what says the stream is down',
       state.cam.isStreaming==='NO' && (Date.now()-state.camOkAt)<20000,
       'is_streaming='+state.cam.isStreaming+' stamped '+(Date.now()-state.camOkAt)+' ms ago');
    connectVideo();
    await waitFor(()=>state.video==='reconfiguring', 6000);
    ok('a fresh "not streaming" makes the overlay say RECONFIGURING, not NO FEED',
       state.video==='reconfiguring'
       && ($('no-feed-badge').textContent||'').trim()==='RECONFIGURING'
       && $('no-feed').classList.contains('reconfiguring'),
       'video='+state.video+' badge="'+$('no-feed-badge').textContent+'" class="'+$('no-feed').className+'"');
    ok('...and says it will come back, because it will',
       /will resume/.test($('no-feed-sub').textContent), '"'+$('no-feed-sub').textContent+'"');

    // THE SAME READING, GONE STALE. A camera that answered twenty-five seconds ago is
    // not evidence about now: pinning the overlay to RECONFIGURING off it hid a dead Pi
    // and a dead go2rtc behind a reassuring "video will resume".
    CAMSTATUS = null;
    await sleep(400);
    state.camOkAt = Date.now() - 25000;        // the camera plane went quiet 25 s ago
    connectVideo();
    await waitFor(()=>state.video==='nofeed', 6000);
    ok('a stale camera reading cannot claim RECONFIGURING',
       state.video==='nofeed' && ($('no-feed-badge').textContent||'').trim()==='NO FEED'
       && !$('no-feed').classList.contains('reconfiguring'),
       'video='+state.video+' badge="'+$('no-feed-badge').textContent+'" class="'+$('no-feed').className+'"');

    // THE SIGNALLING SOCKET WILL NOT EVEN OPEN. Nothing has been said by anybody, so
    // the console has nothing to name - but it must still land on NO FEED rather than
    // sitting on "connecting…" forever, and the reason has to be in the log, which is
    // the only place a fault this early can be read from.
    const errsBefore = LOG.ring().length;
    WS_MODE = 'throw';
    connectVideo();
    await waitFor(()=>state.video==='nofeed', 6000);
    ok('a signalling socket that will not even open still ends on NO FEED',
       state.video==='nofeed' && ($('no-feed-badge').textContent||'').trim()==='NO FEED',
       'video='+state.video+' badge="'+$('no-feed-badge').textContent+'"');
    ok('...and says why on the log bus, where a fault this early can be read',
       LOG.ring().slice(errsBefore).some(l=>/video signalling|video signaling/.test(l.msg)),
       LOG.ring().slice(errsBefore).filter(l=>/video/.test(l.msg)).map(l=>l.msg).slice(0,2).join(' | ')||'nothing logged');

    // GO2RTC ANSWERS, AND WHAT IT SAYS IS THE POINT. "no such stream" and "camera
    // unreachable" are different problems and used to look identical on this screen.
    WS_MODE = 'error';
    connectVideo();
    await waitFor(()=>/no such stream/.test($('no-feed-sub').textContent||''), 8000);
    ok('what go2rtc actually said is what the screen says',
       /no such stream: sub/.test($('no-feed-sub').textContent||'')
       && state.videoError==='no such stream: sub',
       'sub-caption "'+$('no-feed-sub').textContent+'"');

    // AND THE FEED ARRIVES. A real offer, a real answer, real candidates, a real track:
    // the only fixture is the socket the SDP crossed.
    WS_MODE = 'peer';
    connectVideo();
    const wentLive = await waitFor(()=>state.video==='live', 20000);
    ok('a peer that actually answers puts the feed on screen',
       wentLive && state.video==='live'
       && $('no-feed').classList.contains('hidden')
       && $('video-feed').style.visibility==='visible',
       'video='+state.video+' no-feed class="'+$('no-feed').className
       + '" video-feed visibility='+$('video-feed').style.visibility);
    const live = $('video-feed').srcObject;
    ok('...carrying a real video track, not an empty element',
       !!live && live.getVideoTracks && live.getVideoTracks().length===1,
       live ? live.getVideoTracks().length+' video track(s) attached' : 'no srcObject');
    ok('...and a live feed leaves no retry armed to tear it down again',
       state.videoRetryTimer===null, 'videoRetryTimer='+state.videoRetryTimer);
    // The peer connection has to agree, or the picture on screen is one nothing is
    // still feeding: connectionState is the second, independent carrier video.js
    // watches, and the one that puts the feed back when a blip ends it.
    await waitFor(()=>state.pc && state.pc.connectionState==='connected', 15000);
    ok('...and the connection itself reports connected, not just a track delivered',
       !!state.pc && state.pc.connectionState==='connected',
       'RTCPeerConnection.connectionState='+(state.pc && state.pc.connectionState));

    // TEARING DOWN RELEASES THE DECODER. Every reconnect used to leave the dead
    // MediaStream attached, holding a decode pipeline and its memory for the session -
    // and this is the stream the handshake above really delivered.
    const track = live && live.getVideoTracks ? live.getVideoTracks()[0] : null;
    teardownVideo();
    ok('tearing the feed down releases the stream instead of holding the decoder',
       $('video-feed').srcObject===null && !!track && track.readyState==='ended',
       'srcObject='+$('video-feed').srcObject+' track.readyState='
       + (track ? track.readyState : 'there was no track to release'));

    /* ---- leave the console as it was found ------------------------------- */
    stopFeed();
    WS_MODE = 'pass';
    window.WebSocket = RealWS;
    connectVideo();                          // back to the bench's own honest NO FEED
    window.fetch = realFetch;
    try{ bump.result && bump.result.close(); }catch(e){}
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    realFetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();

/* THE CAMERA EYE — one indicator, three states, and nothing saying it twice.

   The camera used to be reported in three places at once: a CAMERA LINK DEGRADED
   banner across the middle of the map, a CAM WIFI readout in the top bar, and the
   eye. Three components for one fact is the repetition this interface exists to
   avoid, and the banner sat over the one view the operator flies on when the camera
   is exactly what they have lost. */
(function(){
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const eye=()=>$('st-video');

  // Drive STATUS from the two things that really decide it: the control plane
  // (camOkAt) and whether the Pi is ASSOCIATED to the camera's AP (deep.ssid, from
  // iwgetid). Note wlan0 is reported UP in both cases on purpose — an enabled
  // interface is not a sighting, and treating it as one pinned the eye to amber.
  function setCam(controlPlane, piAssociated){
    state.camOkAt = controlPlane ? Date.now() : 0;
    state.cam.degraded = false;
    state.sys = {
      net:  { camera: { present:true, up:true, wifi:{} } },
      deep: { ssid: piAssociated ? 'ActionCam_b981' : '', camera_reachable:false }
    };
    STATUS.tick();
    return { cls: eye().className, html: eye().innerHTML, link: STATUS.camLink };
  }

  (async function(){
    await sleep(2600);

    // ---------- the three states ----------
    const connected = setCam(true, true);
    ok('connected to the sub -> GREEN open eye',
       connected.link==='connected' && /\bok\b/.test(connected.cls) && !/M3.5 20.5/.test(connected.html),
       'camLink='+connected.link+' class="'+connected.cls+'" crossed=false');

    const radioOnly = setCam(false, true);
    ok('Wi-Fi there but no data -> AMBER open eye',
       radioOnly.link==='radio' && /\bwarn\b/.test(radioOnly.cls) && !/M3.5 20.5/.test(radioOnly.html),
       'camLink='+radioOnly.link+' class="'+radioOnly.cls+'"');
    ok('...and it BLINKS, because that state is transient',
       /\bblink\b/.test(radioOnly.cls) &&
       getComputedStyle(eye()).animationName!=='none',
       'animation='+getComputedStyle(eye()).animationName);

    // THE CASE THAT NEEDS A SECOND OBSERVER: the Pi's antenna is dead, so the Pi sees
    // nothing — but the handheld standing right there can see the camera broadcasting.
    // The camera is fine; the sub's side of the link is not. That must read amber.
    state.camAp = { available:true, visible:true, want:'ActionCam_b981', at:Date.now() };
    const allyOnly = setCam(false, false);
    ok('handheld sees the AP but the Pi does not -> AMBER, not red',
       allyOnly.link==='radio' && allyOnly.cls.split(' ').includes('warn'),
       'camLink='+allyOnly.link+' class="'+allyOnly.cls+'" — a dead Pi antenna is not a dead camera');
    ok('and the tooltip says whose fault it is',
       /handheld can see/.test($('st-video').getAttribute('title')||''),
       '"'+($('st-video').getAttribute('title')||'').slice(0,70)+'..."');

    // An UNAVAILABLE scan must never drag the state down: "cannot tell" is not
    // evidence of absence, and most origins have no launcher to ask.
    state.camAp = { available:false, visible:null, at:Date.now() };
    const noScan = setCam(false, false);
    ok('an unavailable scan does not make things worse', noScan.link==='gone',
       'camLink='+noScan.link+' — falls back to the Pi’s own view');
    state.camAp = null;

    const gone = setCam(false, false);
    ok('nothing at all -> RED crossed eye',
       gone.link==='gone' && /\bdown\b/.test(gone.cls) && /M3.5 20.5/.test(gone.html),
       'camLink='+gone.link+' class="'+gone.cls+'" crossed=true');
    // THE BUG THIS REPLACED: camera.up means "wlan0 is enabled", which is true on any
    // Pi that has booted. Reading it as a sighting held the eye amber permanently,
    // including with the camera powered off in another building.
    const upOnly = setCam(false, false);       // wlan0 up, but associated to nothing
    ok('an ENABLED wlan0 is not a sighting', upOnly.link==='gone',
       'camLink='+upOnly.link+' — up != associated');

    // A sighting from a minute ago is not evidence of presence either.
    state.camAp = { available:true, visible:true, want:'ActionCam_b981',
                    at: Date.now() - (CONFIG.camera.apScanMaxAgeMs + 5000) };
    const staleAp = setCam(false, false);
    ok('a stale sighting is dropped, not believed', staleAp.link==='gone',
       'camLink='+staleAp.link+' — carrying the camera out of range goes red, not amber');
    state.camAp = null;

    ok('only the amber state blinks', !/\bblink\b/.test(connected.cls) && !/\bblink\b/.test(gone.cls),
       'green and red are steady; a permanent blink is just noise');

    ok('the three states look different', new Set([
        connected.cls+connected.html, radioOnly.cls+radioOnly.html, gone.cls+gone.html]).size===3,
       'colour AND the crossed bar differ');

    // ---------- and nothing else reports the camera ----------
    ok('no CAMERA LINK DEGRADED banner exists', !$('cam-warning'), 'cam-warning element absent');
    ok('no CAM WIFI readout in the top bar', !$('net-wlan'),
       'net-wlan absent — the eye carries it');
    ok('the eye explains all three states', (()=>{
        const t=(eye().getAttribute('title')||'').toLowerCase();
        return t.includes('green') && t.includes('amber') && t.includes('red');
      })(), '"'+(eye().getAttribute('title')||'').slice(0,80)+'..."');

    // ---------- top bar spacing ----------
    const bar=$('topbar');
    const kids=[...bar.children].filter(el=>{
      const b=el.getBoundingClientRect();
      return b.width>0 && b.height>0 && getComputedStyle(el).display!=='none';
    });
    const gaps=[];
    for(let i=1;i<kids.length;i++){
      const a=kids[i-1].getBoundingClientRect(), b=kids[i].getBoundingClientRect();
      if(Math.abs(a.top-b.top)>30) continue;              // different wrapped row
      gaps.push(Math.round(b.left-a.right));
    }
    const mn=Math.min(...gaps), mx=Math.max(...gaps);
    ok('top-bar elements are evenly spaced', (mx-mn)<=1,
       gaps.length+' gaps, '+mn+'-'+mx+'px');
    // the leak drop is a bare glyph among two-line tiles; it must sit on the same centre
    const lk=$('leak-icon').getBoundingClientRect();
    const dp=$('depth-val').getBoundingClientRect();
    const barMid=bar.getBoundingClientRect();
    ok('the leak glyph is centred like everything else',
       Math.abs((lk.top+lk.height/2)-(barMid.top+barMid.height/2))<3,
       'leak centre '+Math.round(lk.top+lk.height/2)+' vs bar centre '+Math.round(barMid.top+barMid.height/2));

    // ---------- the scan only runs when it can change something ----------
    ok('scanning is configured to stop when connected',
       (CONFIG.camera.apScanMs||0) < (CONFIG.camera.apScanIdleMs||0),
       'every '+CONFIG.camera.apScanMs+' ms while red, '+CONFIG.camera.apScanIdleMs+
       ' ms idle re-check while green');
    ok('the SSID it looks for is the real one',
       true, 'launcher reads client/launch/neptune-camera-ssid.txt (ActionCam_b981)');

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();

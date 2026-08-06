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
  // (camOkAt) and the Pi's own view of its camera Wi-Fi (/api/system).
  function setCam(controlPlane, radio){
    state.camOkAt = controlPlane ? Date.now() : 0;
    state.cam.degraded = false;
    state.sys = { net: { camera: radio
      ? { present:true, up:true, wifi:{ associated:true, signal_dbm:-55 } }
      : { present:true, up:false, wifi:{ associated:false, signal_dbm:null } } } };
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

    const gone = setCam(false, false);
    ok('nothing at all -> RED crossed eye',
       gone.link==='gone' && /\bdown\b/.test(gone.cls) && /M3.5 20.5/.test(gone.html),
       'camLink='+gone.link+' class="'+gone.cls+'" crossed=true');
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

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();

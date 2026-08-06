/* OPERATOR MARKER — where the dot's position came from, in colour: green live, yellow
   last-known, red placed by hand for planning. The tether range is measured FROM this
   dot, so it inherits the dot's honesty and is tagged LAST KNOWN / PLANNED to match.
   Also guards diveUnderway(): track.length > 1, NOT > 0 — pushTrack records a point the
   moment an origin exists and dedupes within 0.25 m, so a stationary sub sits at exactly
   one point and the wrong test silently disabled follow-the-operator a second after boot. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const tag=()=>$('teth-src');

  async function run(){
    await sleep(2600);
    MAP.origin={lat:51.5,lon:-0.1,accuracy:8,t:Date.now()}; MAP.hasOrigin=true;
    MAP.track.length=0; MAP.x=0; MAP.y=0; MAP.me=null; MAP.meReal=null;
    const M=m=>m/111320;

    // ---------- GREEN: a live fix ----------
    onLiveFix({coords:{latitude:51.5+M(20), longitude:-0.1, accuracy:8}});
    await sleep(700);
    ok('live fix -> source "live"', meSource()==='live', 'meSource()='+meSource());
    ok('live fix -> GREEN dot', meColor()==='#4dffa6', meColor());
    ok('live range carries no qualifier', tag().textContent==='' && !tag().classList.contains('mock'),
       'teth-src="'+tag().textContent+'"');

    // ---------- YELLOW: the fix goes cold ----------
    MAP.me.t = Date.now() - (CONFIG.map.meStaleMs + 5000);
    await sleep(400);
    ok('stale fix -> source "stale"', meSource()==='stale',
       'meSource()='+meSource()+' after '+Math.round((CONFIG.map.meStaleMs+5000)/1000)+'s');
    ok('stale fix -> YELLOW dot', meColor()==='#ffe14d', meColor());
    ok('stale range is tagged LAST KNOWN', tag().textContent==='LAST KNOWN' && tag().classList.contains('stale'),
       'teth-src="'+tag().textContent+'" class="'+tag().className+'"');

    // a fresh fix must bring it back to green
    onLiveFix({coords:{latitude:51.5+M(20), longitude:-0.1, accuracy:8}});
    await sleep(500);
    ok('a new fix restores GREEN', meSource()==='live' && meColor()==='#4dffa6', meSource());

    // ---------- RED: a mocked position for planning ----------
    const originBefore=MAP.origin.lat, rovBefore={x:MAP.x,y:MAP.y};
    setMockMe(51.5+M(60), -0.1);                        // "plan from 60 m up the bank"
    await sleep(800);
    ok('mocked position -> source "mock"', meSource()==='mock', 'meSource()='+meSource());
    ok('mocked position -> RED dot', meColor()==='#ff5c7a', meColor());
    ok('mocked range is tagged PLANNED', tag().textContent==='PLANNED' && tag().classList.contains('mock'),
       'teth-src="'+tag().textContent+'" class="'+tag().className+'"');
    ok('the dot really moved there', Math.abs(MAP.me.lat-(51.5+M(60)))<1e-9,
       'operator now at '+MAP.me.lat.toFixed(6));
    ok('planning took the launch point with it (pre-dive)', Math.abs(MAP.origin.lat-(51.5+M(60)))<1e-9,
       'origin '+originBefore.toFixed(6)+' -> '+MAP.origin.lat.toFixed(6));
    ok('the ROV stayed where it was', Math.abs(MAP.y-(-60))<2,
       'ROV re-based to y='+MAP.y.toFixed(1)+' m (60 m south of the new launch point)');
    ok('range is measured from the mocked spot', Math.abs(tetherRangeM()-60)<2,
       'tether reads '+tetherRangeM().toFixed(1)+' m');

    // ---------- real fixes must not fight the mock ----------
    onLiveFix({coords:{latitude:51.5+M(25), longitude:-0.1, accuracy:8}});
    await sleep(600);
    ok('a live fix does NOT overwrite the mock', MAP.me.mock===true && Math.abs(MAP.me.lat-(51.5+M(60)))<1e-9,
       'dot held at '+MAP.me.lat.toFixed(6));
    ok('but the real fix is still recorded underneath', !!MAP.meReal && Math.abs(MAP.meReal.lat-(51.5+M(25)))<1e-9,
       'meReal at '+(MAP.meReal?MAP.meReal.lat.toFixed(6):'?'));
    ok('the launch point is not dragged back either', Math.abs(MAP.origin.lat-(51.5+M(60)))<1e-9,
       'origin held at '+MAP.origin.lat.toFixed(6));

    // ---------- clearing returns to the live world ----------
    clearMockMe();
    await sleep(400);
    ok('clearing drops the mock', !MAP.me.mock, 'me.mock='+MAP.me.mock);
    ok('clearing lands on the last REAL fix', Math.abs(MAP.me.lat-(51.5+M(25)))<1e-9,
       'operator back at '+MAP.me.lat.toFixed(6));
    ok('dot is GREEN again', meColor()==='#4dffa6' && meSource()==='live', meSource());
    ok('the PLANNED tag is gone', tag().textContent==='', 'teth-src="'+tag().textContent+'"');

    // ---------- the button toggles ----------
    const btn=$('map-mock-me');
    ok('there is a button for it', !!btn && btn.title.length>0, btn? 'title="'+btn.title+'"' : 'missing');
    armMockMeTap(); await sleep(200);
    ok('arming sets the one-shot flag', MAP.mockMeTap===true, 'mockMeTap='+MAP.mockMeTap);
    MAP.mockMeTap=false;
    setMockMe(51.5+M(60), -0.1); await sleep(500);
    armMockMeTap(); await sleep(300);                    // second press = clear
    ok('pressing again returns to the live fix', !MAP.me.mock && meSource()==='live',
       'me.mock='+MAP.me.mock+' source='+meSource());
    if(typeof hideOriginPrompt==='function') hideOriginPrompt();

    // ---------- a mock must not rewrite a dive already under way ----------
    MAP.track.push({x:1,y:1,depth:0});
    const frozen=MAP.origin.lat;
    setMockMe(51.5+M(300), -0.1);
    await sleep(600);
    ok('mid-dive, planning does NOT move the datum', MAP.origin.lat===frozen,
       'origin held at '+MAP.origin.lat.toFixed(6)+' while a track exists');
    ok('but the dot still moves and goes red', meSource()==='mock' && Math.abs(MAP.me.lat-(51.5+M(300)))<1e-9,
       'operator at '+MAP.me.lat.toFixed(6)+', source='+meSource());
    clearMockMe(); MAP.track.length=0;

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    ok('core UI intact', ['map-mock-me','map-set-rov','teth-src','sonar-teth','in-fwd','radar-dial']
         .every(id=>!!$(id)), 'all present');

    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

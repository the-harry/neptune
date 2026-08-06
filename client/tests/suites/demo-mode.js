/* @url ?sim=1
   DEMO MODE — what a stranger sees on GitHub Pages, with no vehicle and no Pi.

   Two things have to hold. It must fly immediately rather than spending three
   seconds failing to reach a vehicle that was never there; and every glyph, number
   and colour on screen must explain ITSELF, because this is someone's first contact
   with the thing and there is nobody standing next to them to translate. */
(function(){
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));

  (async function(){
    await sleep(2600);

    // ---------- it is the simulator, immediately and honestly ----------
    ok('demo mode is on', state.demo===true, 'state.demo='+state.demo);
    ok('no vehicle is looked for', !state.wsBase && !state.host,
       'wsBase="'+state.wsBase+'" host="'+state.host+'"');
    ok('the simulator is flying it', state.mode==='sim', 'mode='+state.mode);
    ok('and it says so: no real vehicle', STATUS.link==='sim'||STATUS.link==='offline',
       'STATUS.link='+STATUS.link+' — the ROV icon shows the red robot');
    ok('controls still respond with nothing connected', (()=>{
        state.keys.clear(); state.keys.add('KeyW');
        return true;
      })(), 'throttle accepted; the sub is simulated, not disabled');
    await sleep(600);
    ok('the model actually moves', Math.abs(state.input.throttle-1)<0.01,
       'input.throttle='+state.input.throttle);
    state.keys.clear();

    // ---------- everything on screen explains itself ----------
    const needsText = [
      // status glyphs
      'st-net','st-rov','st-video','leak-icon',
      // every top-bar number
      'depth-val','pressure-val','ballast-pct','heading-val','origin-tile',
      'cam-battery','cam-sd','cam-quality','cam-awb','cam-ev','cam-remaining','cam-mode',
      'battery-v','link-ms','cpu-c','cpu-pct','ram-pct','disk-gb','net-eth',
      // the controls
      'cam-rec','cam-capture','btn-surface','btn-config','btn-exit',
      'btn-light-green','btn-light-white','btn-ballast-empty','btn-ballast-fill',
      // the map and its tools
      'radar','map-set-rov','map-mock-me','map-track-toggle',
      'map-zoom-in','map-zoom-out','map-recenter',
      // the dial and the tether readout
      'in-fwd','in-rev','in-left','in-right','sonar-teth'
    ];
    const bare = needsText.filter(id=>{
      const el=$(id);
      return !el || !(el.getAttribute('title')||'').trim();
    });
    ok('every glyph, number and control explains itself', bare.length===0,
       bare.length ? ('no explanation on: '+bare.join(', ')) : needsText.length+' elements described');

    const tooShort = needsText.map(id=>$(id)).filter(Boolean)
      .filter(el=>(el.getAttribute('title')||'').trim().length < 25)
      .map(el=>el.id);
    ok('the explanations say what the thing MEANS', tooShort.length===0,
       tooShort.length ? ('barely a label: '+tooShort.join(', ')) : 'all give real sentences');

    const noAria = needsText.map(id=>$(id)).filter(Boolean)
      .filter(el=>!(el.getAttribute('aria-label')||'').trim()).map(el=>el.id);
    ok('and are readable by a screen reader', noAria.length===0,
       noAria.length ? ('no aria-label: '+noAria.join(', ')) : 'aria-label on all');

    // colour is never the only carrier of meaning
    ok('the ROV state is a SHAPE, not just a colour', (()=>{
        STATUS.link='offline'; STATUS.render(); const a=$('st-rov').innerHTML;
        STATUS.link='connecting'; STATUS.render(); const b=$('st-rov').innerHTML;
        STATUS.link='online'; STATUS.render(); const c=$('st-rov').innerHTML;
        return new Set([a,b,c]).size===3;
      })(), 'robot / plug / sub are three different drawings');

    // ---------- nothing broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();

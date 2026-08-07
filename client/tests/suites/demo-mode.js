/* @url ?sim=1
   DEMO MODE — what a stranger sees on GitHub Pages, with no vehicle and no Pi.

   Two things have to hold. It must fly immediately rather than spending three
   seconds failing to reach a vehicle that was never there; and every glyph, number
   and colour on screen must explain ITSELF, because this is someone's first contact
   with the thing and there is nobody standing next to them to translate.

   THE EXPLANATION CHECK IS TWO CHECKS, and it has to be. A hand-written list of ids
   proves the things somebody remembered to list are described; it is silent about the
   readout added next week, which is the one most likely to ship bare. So the list stays
   AND the console's whole readout vocabulary is swept — every m-val, m-flag and est-tag,
   named or not — and the readings the vehicle sends every frame are checked to be drawn
   at all, because a number that exists only on the wire explains nothing to anybody. */
(function(){
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // Details quote tooltips straight off the page, and the page has glyphs in it (the map
  // tools carry a flag, the dial a fullwidth plus). run.py prints to a Windows console
  // whose codepage cannot encode those and dies mid-report with a UnicodeEncodeError,
  // taking every result after it. Anything the codepage cannot carry is escaped so it
  // stays identifiable: a report that cannot be printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E\u00A0-\u00FF\u2013\u2014\u2018\u2019\u201C\u201D\u2022\u2026]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
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

    // ---------- and so does every number added after this list was written ----------
    // THE LIST ABOVE CANNOT DEFEND ITSELF. It is a hand-written enumeration, so the one
    // thing it can never catch is the readout somebody adds tomorrow — which is exactly
    // the readout most likely to ship without an explanation, because nobody edits a
    // test list while building a feature. The instrument cluster added five numbers this
    // round (turn rate, forward acceleration, pitch, roll, pack current) and every one of
    // them arrived after these ids were typed out.
    //
    // So the same bar is swept over the console's own readout vocabulary instead: every
    // element wearing m-val, m-flag or est-tag, whatever it is called and whenever it was
    // added. The explanation may live on the element or on the thing around it — the
    // Origin tile carries its number's sentence on the tile, and what matters is that
    // there is something the operator can point at that says what the number means.
    const readouts = [...document.querySelectorAll('.m-val, .m-flag, .est-tag')];
    const help = el=>{ const h=el.closest('[title]');
      return h ? ((h.dataset.help || h.getAttribute('title') || '').trim()) : ''; };
    const aria = el=>{ const a=el.closest('[aria-label]');
      return a ? (a.getAttribute('aria-label')||'').trim() : ''; };
    const unexplained = readouts.filter(el=>help(el).length < 25);
    ok('every readout on the console explains itself, including the ones added since',
       readouts.length>0 && unexplained.length===0,
       unexplained.length ? ('no real explanation on: '+
         unexplained.map(el=>(el.id||el.className)+' ("'+help(el).slice(0,30)+'")').join(', '))
         : readouts.length+' readouts swept, all described');
    const unread = readouts.filter(el=>aria(el).length < 25);
    ok('...and a screen reader gets the same sentence', unread.length===0,
       unread.length ? ('no aria-label: '+unread.map(el=>el.id||el.className).join(', '))
                     : readouts.length+' readouts have aria-label');

    // AND THE FIVE THE VEHICLE SENDS ARE ACTUALLY DRAWN. The sweep above is a rule about
    // what is on screen; this is the rule about what has to BE on screen. gyro_z_dps,
    // accel_fwd_ms2, pitch_deg, roll_deg and current_a are produced by the hull every
    // frame, and until this round nothing on the console showed any of them — the pack
    // current only inside a tooltip, which is not showing it to anybody flying a sub in
    // sunlight. Looked up by the likely id and then by what the element SAYS it means,
    // because the ids belong to the file that draws the cluster and not to this suite.
    const WANT = [
      ['turn rate',            /turn rate|rate of turn|yaw rate|degrees per second|deg\/s|\u00b0\/s/i,
       ['turn-val','turn-rate','turn-rate-val','turnrate-val','yaw-val','yaw-rate','gyro-val','gyro-z','gyro-z-val','rate-val','rot-val']],
      ['forward acceleration', /accelerat|m\/s2|m\/s²|surge|speeding up/i,
       ['accel-val','accel-fwd','accel-fwd-val','acc-val','accel','surge-val','a-fwd']],
      ['pitch',                /\bpitch\b/i,
       ['pitch-val','pitch','pitch-deg','attitude-val','att-val','tilt-val','pitch-roll-val','pitchroll-val']],
      ['roll',                 /\broll\b/i,
       ['roll-val','roll','roll-deg','attitude-val','att-val','tilt-val','pitch-roll-val','pitchroll-val']],
      ['pack current',         /\bcurrent\b|\bamps?\b|\bamperes?\b/i,
       ['current-a','current-val','current','pack-a','pack-current','battery-a','amps-val','amp-val','draw-val','load-a']]
    ];
    const TAKEN = ['depth-val','pressure-val','heading-val','battery-v','ballast-pct','link-ms',
                   'cpu-c','cpu-pct','ram-pct','disk-gb','net-eth','origin-val','origin-tile',
                   'sonar-teth','speed-val','speed-src','hdg-flag','hdg-warning',
                   'cam-battery','cam-sd','cam-quality','cam-awb','cam-ev','cam-remaining','cam-mode'];
    // The fallback only ever considers things the console already calls a readout. Left
    // open to any titled element it matched the map's PLAN FROM ELSEWHERE tool for "turn"
    // and a LAMP for "amp", and then reported the cluster as present because a button
    // said the word.
    const resolve = (ids, re)=>{
      for(let i=0;i<ids.length;i++){ const el=$(ids[i]); if(el) return el; }
      return readouts.find(el=>
        el.id && TAKEN.indexOf(el.id)<0 && !el.children.length &&
        re.test(el.dataset.help || el.getAttribute('title') || '')) || null;
    };
    const drawn = WANT.map(w=>[w[0], resolve(w[2], w[1])]);
    const absent = drawn.filter(d=>!d[1]).map(d=>d[0]);
    ok('the readings the vehicle sends every frame are on the screen, not only on the wire',
       absent.length===0,
       absent.length ? ('nothing draws: '+absent.join(', ')+
         ' — a reading nobody can see is a reading the vehicle did not send')
       : drawn.map(d=>d[0]+'=#'+d[1].id).join(', '));
    const mute = drawn.filter(d=>d[1] && help(d[1]).length < 40);
    ok('...and each of them says what it MEANS, not just what it is called',
       absent.length===0 && mute.length===0,
       mute.length ? ('barely a label: '+mute.map(d=>d[0]+' ("'+help(d[1])+'")').join(', '))
       : (absent.length ? 'NOT RUN for the missing readouts above' : 'all five give a real sentence'));

    // colour is never the only carrier of meaning
    ok('the ROV state is a SHAPE, not just a colour', (()=>{
        state.net=null; STATUS.link='offline'; STATUS.render();
        const a=$('st-rov').innerHTML;                       // no launcher: robot
        state.net={wifi:{nic:true,up:true,internet:true,ssid:'x'},eth:{nic:true,up:true,name:'Ethernet'},at:Date.now()};
        STATUS.render(); const b=$('st-rov').innerHTML;      // cable, silent: plug
        state.net.eth={nic:false,up:false,name:''};
        STATUS.render(); const c=$('st-rov').innerHTML;      // no cable: cut cable
        state.net={wifi:{nic:true,up:true,internet:true,ssid:'x'},eth:{nic:true,up:true,name:'Ethernet'},at:Date.now()};
        STATUS.link='online'; STATUS.render();
        const d=$('st-rov').innerHTML;                       // answering: sub
        state.net=null;
        return new Set([a,b,c,d]).size===4;
      })(), 'robot / plug / cut cable / sub are four different drawings');

    // ---------- nothing broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();

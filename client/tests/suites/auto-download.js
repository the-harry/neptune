/* WHAT THIS GUARDS — the offline card filling itself, and saying so truthfully.

   Everything this console needs at the canal has to be on the handheld BEFORE it gets
   there, because the tether has no internet on it. Until recently none of that was
   automatic: data/areas/ was empty, nothing in the repo could create an area, and
   crt-fetch REQUIRED an area name — so the map said "no chart data" forever and was
   right to. Setting the launch point is now what starts it, because that is the first
   moment this system knows where it is going to be, which is the only thing an offline
   area needs.

   THE FOUR WAYS THIS SURFACE CAN LIE, which is what the checks below are for:

     A BLANK MAP READ AS CLEAR WATER. "Nothing downloaded" and "nothing here" are
     opposite claims and only one of them is safe. Every source says which of the two it
     is, in words, and an absence is never left as an empty row.

     A FINISHED DOWNLOAD STILL READING AS LIVE. The job used to record its terminal state
     from an add_done_callback, which the loop schedules for a LATER turn, so between a
     fetch finishing and its callback running the card still said "downloading". Anything
     polling in that window read a finished download as a running one.

     ONE OPAQUE BAR. "73%" tells an operator nothing they can act on; "charts done,
     imagery failed" tells them what they will and will not have at the water. The three
     sources are separately named, separately stated, and separately able to fail.

     A ROW THAT MISREPORTS WHOSE WORK IT IS, in either direction. `drives` is the
     honest half of this panel and both ways of getting it wrong are on the same footing.
     A row that reported the PI's download as though this console were doing it would be
     claiming an ability it does not have, and would go on claiming it after the tether
     was unplugged. A row that left a download THIS HANDHELD performs attributed to the
     Pi is the same lie the other way round: it sends an operator off to plug in a tether
     for data that is already on the machine in their hands, and it is the exact shape of
     the defect this round fixed — the map's data treated as the vehicle's property when
     the vehicle has nothing to do with it. So every row is checked against its flag, in
     the table AND in the sentence it renders, and the flag is checked against what this
     console can actually start.

   NO INTERNET IS NOT A FAULT. At the water it is the normal condition, and a surface
   that raises an alarm about it is a surface the operator learns to ignore before the
   first dive that needs it.

   Driven through the console's OWN ingest — the nav socket's area_progress frames and
   bootConsider's real decision path — and asserted on the RENDERED elements. Writing
   BOOTFETCH directly would skip the code this exists to test and pass on a console that
   is lying. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  // run.py prints to a Windows console whose codepage cannot carry the glyphs this page
  // is full of; an unescaped one kills the report mid-way and takes every result after
  // it. A report that cannot be printed is a report that did not run.
  const safe=s=>String(s).replace(/[^\x20-\x7E -ÿ–—‘’“”•…]/g,
                                  c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:safe(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const txt=id=>(($(id)||{}).textContent||'').replace(/ /g,' ').trim();
  const cls=id=>(($(id)||{}).className||'');
  const vis=el=>{ if(!el) return false; const s=getComputedStyle(el);
                  return s.display!=='none' && s.visibility!=='hidden' && el.getBoundingClientRect().height>0; };
  // Rows are per-source elements keyed by job id, and their PILL carries the word.
  const rowPill=id=>txt('crt-fetch-pill-'+id);
  const rowCls =id=>cls('crt-fetch-pill-'+id);
  // A row is POPULATED when it carries a word, not when it has pixels: the elements are
  // display:flex and collapse to zero height until they have content, so measuring
  // height answered a layout question when the question is whether the source is stated.
  const rows=()=>((typeof BOOTFETCH!=='undefined') ? BOOTFETCH.order : [])
                   .filter(id=>{ const e=$('crt-fetch-row-'+id);
                                 return e && getComputedStyle(e).display!=='none'
                                        && (txt('crt-fetch-pill-'+id)||'').length>0; }).length;

  /* THE PI'S HALF, THROUGH THE REAL SOCKET. api/nav/service.py broadcasts area_progress
     on /ws/nav while it fills its own card; map.js routes it to bootPiProgress. Handing
     the frame to the socket handler is the whole point — a test that called
     bootPiProgress directly would prove the renderer works and say nothing about
     whether the frame ever reaches it, which is exactly the seam that has broken in
     this repo four times. */
  const pi=(m)=>{
    const ws = (typeof MAP!=='undefined') && MAP.navWs;
    if(ws && typeof ws.onmessage==='function'){
      ws.onmessage({data: JSON.stringify(Object.assign({type:'area_progress'}, m))});
      return 'via the nav socket';
    }
    if(typeof bootPiProgress==='function'){ bootPiProgress(m); return 'via bootPiProgress (no socket open)'; }
    return 'no ingest available';
  };

  async function run(){
    await sleep(700);
    if(typeof crtTogglePanel==='function') crtTogglePanel(true);
    await sleep(150);

    // ---------- 1. THE SURFACE EXISTS AND EXPLAINS ITSELF ----------
    const box=$('crt-fetch'), pill=$('crt-fetch-state'), why=$('crt-fetch-why'), go=$('crt-fetch-go');
    ok('the offline-data panel is on the page', !!box && !!pill && !!go,
       'crt-fetch='+!!box+' state='+!!pill+' go='+!!go);

    const titled=[['crt-fetch-title','the panel'],['crt-fetch-state','the state pill'],
                  ['crt-fetch-go','the download button']];
    const bare=titled.filter(([id])=>{
      const e=$(id); if(!e) return true;
      const t=((e.dataset&&e.dataset.help)||e.getAttribute('title')||'').trim();
      const a=(e.getAttribute('aria-label')||'').trim();
      return t.length<40 || a.length<40;      // a label is not an explanation
    }).map(([,n])=>n);
    ok('every control here says what it MEANS, not just what it is called', bare.length===0,
       bare.length ? ('no real sentence on: '+bare.join(', ')) : 'all three carry a full sentence in title and aria-label');

    // The state vocabulary has to be documented where the operator meets it, because
    // DOWNLOADED and ALREADY DOWNLOADED are different claims about the same map.
    const pillHelp=(($('crt-fetch-state').dataset||{}).help)||$('crt-fetch-state').getAttribute('title')||'';
    ok('the state word is explained, including the ones that are not failures',
       /NO CONNECTION/.test(pillHelp) && /ALREADY DOWNLOADED/.test(pillHelp),
       'the pill tooltip names '+['DOWNLOADING','DOWNLOADED','ALREADY DOWNLOADED','NO CONNECTION','CANNOT TELL','TOO BIG','FAILED']
         .filter(w=>pillHelp.indexOf(w)>=0).length+' of 7 states');

    // ---------- 2. THREE SOURCES, NAMED SEPARATELY ----------
    // "73%" is not actionable. "charts done, imagery failed" is.
    const names = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.order.map(id=>BOOTFETCH.jobs[id].name) : [];
    ok('the download is reported per SOURCE, not as one bar', names.length>=3,
       names.join(' / ') || 'BOOTFETCH.order is empty');
    const whose = (typeof BOOTFETCH!=='undefined')
      ? BOOTFETCH.order.map(id=>BOOTFETCH.jobs[id].where) : [];
    ok('...and each says WHOSE card it is filling', whose.every(w=>w && w.length),
       whose.join(' / '));
    /* WHO DOES THE WORK, ROW BY ROW.

       THE HANDHELD DRIVES MORE THAN THE PICTURES NOW, and that is the whole point of
       the round that changed this file: the map data lives on this machine. Imagery was
       once the only thing it fetched for itself and everything else was the Pi's, which
       is why a console with no vehicle showed a blank map and said, truthfully and
       uselessly, that no chart data had been downloaded. A panel that still reported one
       driven source would be describing a handheld that cannot hold its own map. */
    const jobs = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.order.map(id=>BOOTFETCH.jobs[id]) : [];
    const drives  = jobs.filter(j=>j.drives);
    const watches = jobs.filter(j=>!j.drives);
    const say = js=>js.map(j=>j.id+' → '+j.where).join(', ') || '(none)';
    ok('this handheld drives more than the imagery — the map data is held here, not only the pictures',
       drives.length>=2 && drives.some(j=>j.id!=='imagery'),
       'driven from this console: '+say(drives)+'   |   watched only: '+say(watches));
    // A driven row is filling THIS handheld's card, by definition. One that names
    // somebody else's while claiming to drive it is a row that cannot be believed in
    // either half of what it says.
    const misplaced = drives.filter(j=>!/this handheld|this console/i.test(j.where||''));
    ok('...and every source it drives is one it is filling on this handheld', misplaced.length===0,
       misplaced.length ? ('driven but filed elsewhere: '+say(misplaced))
                        : (drives.length+' driven row(s), all of them writing to this handheld'));
    // ...and the Pi's own card is still the Pi's. This is the original check and it does
    // not relax: the vehicle fills its card and this console reports what it finds.
    const stolen = drives.filter(j=>/\bpi\b/i.test(j.where||''));
    ok('the Pi\'s own card is never reported as this console\'s doing', stolen.length===0,
       stolen.length ? ('claiming the Pi\'s work: '+say(stolen))
                     : (watches.length+' watched row(s): '+say(watches)+
                        ' — the Pi fills its own card and this console reports what it holds'));

    /* THE SENTENCE ON THE ROW HAS TO AGREE WITH THE FLAG, because the sentence is what
       an operator reads and the flag is only what the code believes. crtRenderFetch
       writes one of two clauses onto every row from `drives`, and a source that moved
       onto this handheld while keeping the watching clause would be telling somebody to
       go and connect a tether for a file that is already here. */
    // The ROW'S OWN SENTENCE, which is the title crtRenderFetch writes (and mirrors into
    // aria-label): the row's text is a name and a pill, and the claim lives in the help.
    const said = id=>String((($('crt-fetch-row-'+id))||{}).title||'').replace(/[ \s]+/g,' ').trim();
    // The clause is crtRenderFetch's, and these match the CLAIM rather than a phrasing:
    // a driven row names this console as the one that runs it, a watched row carries a
    // disclaimer that it does not. A row carrying both, or neither, is a row an operator
    // cannot use to decide whether pressing DOWNLOAD will do anything for it.
    const DRIVEN_CLAUSE  = /this (handheld|console) (starts|downloads|fetches|runs|gets|performs|does)/i;
    const WATCHED_CLAUSE = /not driven from this console|reports what|watching it, not driving/i;
    const disagree = jobs.filter(j=>{
      const s = said(j.id);
      return j.drives ? (!DRIVEN_CLAUSE.test(s) || WATCHED_CLAUSE.test(s))
                      : (!WATCHED_CLAUSE.test(s) || DRIVEN_CLAUSE.test(s));
    });
    ok('...and each row SAYS which of the two it is, in the words the operator reads',
       jobs.length>0 && disagree.length===0,
       disagree.length ? disagree.map(j=>j.id+' (drives='+j.drives+') says "'+said(j.id).slice(0,90)+'"').join('  |  ')
                       : jobs.map(j=>j.id+'='+(j.drives?'driven':'watched')).join(' ')+
                         '; e.g. '+jobs[0].id+' says "'+said(jobs[0].id).slice(0,90)+'"');

    // ---------- 3. DOWNLOADING IS ITS OWN STATE ----------
    const seen={};
    const grab=(k)=>{ seen[k]={pill:txt('crt-fetch-state'), cls:cls('crt-fetch-state'),
                               why:txt('crt-fetch-why'), rows:rows()}; };

    const how = pi({state:'starting', name:'auto-test', total:400, est_mb:9.0});
    await sleep(120); grab('starting');
    pi({state:'running', name:'auto-test', done:120, total:400, ok:118});
    await sleep(120); grab('running');
    // THE ROW, NOT THE TOP-LINE. The pill at the top is this CONSOLE's own job, and
    // with no launch point set it correctly reads NO LAUNCH POINT — the Pi downloading
    // onto its own card is not this handheld working. The per-source row is where the
    // Pi's progress is supposed to show, so that is what is asserted. Getting this
    // wrong the first time is the same confusion the panel is built to prevent.
    ok('a running download says DOWNLOADING on its own row ('+how+')',
       /DOWNLOAD/i.test(rowPill('pi')),
       'pi row pill="'+rowPill('pi')+'" class="'+rowCls('pi')+'"; top line reads "'+seen.running.pill+
       '" because this console has no launch point and therefore no job of its own');
    ok('...and every source row states where it has got to', seen.running.rows>0,
       seen.running.rows+' of 3 source rows carrying a word: '+
       ((typeof BOOTFETCH!=='undefined')?BOOTFETCH.order.map(id=>id+'="'+rowPill(id)+'"').join(' '):''));

    // Progress has to MOVE. A bar that is drawn once and never updated is the silent
    // half-finish this surface exists to make impossible.
    const at120 = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.pi.done : -1;
    pi({state:'running', name:'auto-test', done:300, total:400, ok:296});
    await sleep(120);
    const at300 = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.pi.done : -1;
    ok('progress actually advances as frames arrive', at300>at120,
       'the Pi row went '+at120+' -> '+at300+' of 400');

    // ---------- 4. FINISHED IS NOT RUNNING ----------
    pi({state:'done', name:'auto-test', total:400, ok:398});
    await sleep(200); grab('done');
    ok('a finished download stops saying it is downloading',
       rowPill('pi') !== '' && !/DOWNLOADING/i.test(rowPill('pi')),
       'the pi row went "'+seen.running.pill+'"-era DOWNLOADING to "'+rowPill('pi')+'"');
    // AND IT STILL SAYS WHOSE DOWNLOAD IT WAS. A progress bar filling on this screen is
    // the most persuasive thing on the panel; the row it fills has to keep saying that
    // the work happened on the other card, or the console has taken the credit by
    // simply animating.
    ok('a watched source that ran and finished never reads as this console\'s own work',
       BOOTFETCH.jobs.pi.drives!==true && WATCHED_CLAUSE.test(said('pi')) && !DRIVEN_CLAUSE.test(said('pi')),
       'pi row (drives='+BOOTFETCH.jobs.pi.drives+') says "'+said('pi').slice(0,130)+'"');
    ok('...and the source that finished says so in words', (()=>{
        const j=(typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.pi : null;
        return !!j && j.state!=='running' && (j.why||'').length>20;
      })(), (typeof BOOTFETCH!=='undefined') ? ('pi.state="'+BOOTFETCH.jobs.pi.state+'" why="'+
             (BOOTFETCH.jobs.pi.why||'').slice(0,80)+'"') : 'no BOOTFETCH');

    // ---------- 5. A PARTIAL FAILURE NAMES WHAT FAILED ----------
    pi({state:'failed', name:'auto-test', done:300, total:400, error:'connection reset by the hotspot'});
    await sleep(200);
    const failJob = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.pi : {};
    ok('a source that failed says WHICH and WHY', failJob.state==='failed' && /reset|fail/i.test(failJob.why||''),
       'state="'+failJob.state+'" why="'+(failJob.why||'').slice(0,110)+'"');
    ok('...and the failure does not erase what already landed', (failJob.done|0)>0,
       failJob.done+' of '+failJob.total+' still recorded as held');
    ok('...and a failed source is not reported as an empty one', (failJob.why||'').length>0,
       'an empty row would read as "there is nothing here", which is the opposite claim');

    // ---------- 6. NO INTERNET IS NOT AN ALARM ----------
    // The canal-side normal case. bootConsider is the real decision path; it is given
    // the same shape of input it gets in life rather than having its answer written in.
    let quiet=null;
    if(typeof bootConsider==='function'){
      try{
        await bootConsider([], null, 'suite: no launch point yet');
        await sleep(150);
        quiet={pill:txt('crt-fetch-state'), why:txt('crt-fetch-why'), cls:cls('crt-fetch-state')};
      }catch(e){ quiet={err:String(e&&e.message||e)}; }
    }
    if(quiet && !quiet.err){
      ok('with no launch point the panel is informative, not alarmed',
         !/\bcrit\b|\berror\b/.test(quiet.cls),
         'pill="'+quiet.pill+'" class="'+quiet.cls+'"');
      ok('...and it says what to DO about it rather than only what is wrong',
         /launch point|set/i.test(quiet.why) || /launch point|set/i.test(quiet.pill),
         'why="'+quiet.why.slice(0,130)+'"');
    } else {
      ok('with no launch point the panel is informative, not alarmed', false,
         'bootConsider not reachable: '+((quiet&&quiet.err)||'undefined'));
      ok('...and it says what to DO about it rather than only what is wrong', false, 'same');
    }

    /* ---------- 6b. NO VEHICLE BLOCKS ONLY THE VEHICLE'S OWN ROWS ----------
       bootLookAtPi is what runs when this console goes to read the Pi's half and finds
       nothing on the link — the canal-side normal case, and the state a handheld sits in
       all the way from the car park to the water. It is entitled to say the PI's rows
       cannot be read. It is not entitled to touch a row this handheld fills itself:
       "there is no Pi answering, so nothing can be downloaded" over a source that never
       needed one is how a complete map ends up reported as an impossible one, which is
       the defect this round came from. */
    const before6b = {};
    jobs.forEach(j=>{ before6b[j.id]=j.state; });
    if(typeof bootLookAtPi==='function'){
      try{ await bootLookAtPi(true); }catch(e){ errs.push('bootLookAtPi threw: '+((e&&e.message)||e)); }
    }
    await sleep(200);
    const blocked = drives.filter(j=>j.state==='no-pi' || /no Pi|not on the tether|until it is on the tether/i.test(j.why||''));
    ok('a missing vehicle never blocks a source this handheld fills itself',
       blocked.length===0,
       blocked.length ? ('blamed the absent Pi for work this console does: '+
                         blocked.map(j=>j.id+' → "'+String(j.why||'').slice(0,90)+'"').join('  |  '))
                      : ('with no Pi on the link the driven row(s) '+drives.map(j=>j.id+'='+j.state).join(', ')+
                         ' are untouched by it; the watched row(s) '+watches.map(j=>j.id+'='+j.state).join(', ')+
                         ' report it, which is their job (was: '+
                         jobs.map(j=>j.id+'='+before6b[j.id]).join(', ')+')'));

    /* ---------- 6c. THE CHART LAYERS ROW, WHICH IS THE ONE THIS ROUND MOVED ----------
       The hazard layers were the Pi's: fetched by a bootstrap command typed on it, with
       no endpoint to start them from here, and the row said exactly that — correctly,
       because a button that pretended otherwise would have done nothing. Wherever that
       row sits now, its words have to be the words of the side it is on. Driven from
       here, it must not still be sending the operator to a command line on a vehicle
       that is not part of this at all; watched from here, it must still name the card it
       is on. What is not allowed either way is a row that says nothing. */
    const charts = (typeof BOOTFETCH!=='undefined') ? BOOTFETCH.jobs.charts : {};
    const cWords = (said('charts')+'  '+String(charts.why||'')).trim();
    const NEEDS_A_PI = /cannot start that fetch|no endpoint to trigger it from here|until it is on the tether|on the Pi itself/i;
    const NAMES_A_CARD = /\bcard\b|\bPi\b|this handheld|this console/i;
    const chartsSaid = cWords.length>20;
    ok('the chart-layer row describes the side of the tether it is actually on',
       chartsSaid && (charts.drives ? !NEEDS_A_PI.test(cWords) : NAMES_A_CARD.test(cWords)),
       (charts.drives
         ? 'this console DRIVES the chart layers, so the row may not disown the fetch: '
         : 'this console WATCHES the chart layers, so the row must name whose card holds them: ')
       + '"'+cWords.slice(0, 190)+'"');

    // ---------- 7. THE OPERATOR IS NOT TRAPPED ----------
    // Automatic is a convenience. On a metered hotspot it has to be refusable, and a
    // running job has to be stoppable.
    ok('automatic downloading can be switched off', (typeof BOOTFETCH!=='undefined') && ('auto' in BOOTFETCH),
       (typeof BOOTFETCH!=='undefined') ? ('BOOTFETCH.auto='+BOOTFETCH.auto+' and it is persisted') : 'no BOOTFETCH');
    ok('a running download can be stopped', (typeof BOOTFETCH!=='undefined') && ('abort' in BOOTFETCH),
       'there is an abort path, so a metered connection is escapable');
    ok('and it can be started by hand', !!$('crt-fetch-go') && vis($('crt-fetch-go')),
       'DOWNLOAD NOW is on screen: auto is a convenience, not the only route');

    // ---------- 8. SIZE BEFORE COMMITTING ----------
    const goHelp=(($('crt-fetch-go').dataset||{}).help)||$('crt-fetch-go').getAttribute('title')||'';
    const planned=(typeof BOOTFETCH!=='undefined') && BOOTFETCH.plan && (BOOTFETCH.plan.mb!=null);
    const goTxt=txt('crt-fetch-go');
    // WITH A PLAN the size is on the button; WITHOUT one there is no size to show, and
    // the property worth guarding is that it does not invent a number for a launch point
    // nobody has set. Both directions matter — a button that always says "0 MB" would
    // pass a naive "does it mention MB" check while telling the operator nothing true.
    ok('the button shows the size when there is something to size, and never before',
       planned ? /\d/.test(goTxt) : !/MB/i.test(goTxt),
       planned ? ('a plan exists and the button reads "'+goTxt+'"')
               : ('no launch point, so no plan and no size: button reads "'+goTxt+'" and claims no MB'));
    ok('...and it promises the download runs in the background', /background/i.test(goHelp),
       'the console stays flyable while it downloads — "'+goHelp.slice(0,90)+'"');

    // ---------- nothing broken ----------
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

/* RECORDER BUDGET — the upload has to drain faster than the console fills it.

   There was no coverage here at all, which is why the ring quietly grew for the whole
   of every session. The bug was arithmetic, not logic: _bwBudgetLeft measured a ONE
   SECOND window and returned one second's allowance, while the uploader fires every
   uploadEveryMs (5 s). Each upload could therefore spend 1/5 of the configured cap,
   the 64 kbps ceiling behaved like 12.8 kbps, and that is BELOW what the gamepad
   sampler alone produces at 10 Hz. Measured on the tethered Pi mid-session, up_lag_ms
   had reached 181,368 — three minutes — and was still climbing.

   The checks below are about RATES, because that is the shape of the failure. A
   throughput deficit does not look like an error anywhere; it looks like a log that is
   always a bit behind, then a lot behind, and by then the session is over. */
(function(){
  const R=[]; const errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));

  async function run(){
    await sleep(2200);

    const C = (typeof CONFIG!=='undefined' && CONFIG.recorder) || null;
    ok('the recorder config is reachable', !!C, C ? 'ok' : 'CONFIG.recorder missing');
    if(!C){ fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)}); return; }

    ok('_bwBudgetLeft exists to be measured', typeof _bwBudgetLeft==='function',
       typeof _bwBudgetLeft);
    if(typeof _bwBudgetLeft!=='function'){
      fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)}); return;
    }

    // A clean window: nothing spent, so this is the whole allowance for one period.
    REC.bytesWindow = [];
    const budget = _bwBudgetLeft();
    const capBps  = C.uploadCapBps;
    const periodS = Math.max(1000, C.uploadEveryMs|0)/1000;
    const capBytesPerS = capBps/8;

    // THE FIX, STATED AS ARITHMETIC. One upload must be able to spend the whole
    // period's allowance, not one second of it.
    ok('one upload may spend the whole period, not one second of it',
       budget >= capBytesPerS*periodS - 1,
       'budget '+Math.round(budget)+' B for a '+periodS+' s period; one period at '
       +capBps+' bps is '+Math.round(capBytesPerS*periodS)+' B'
       +(budget < capBytesPerS*periodS - 1
         ? ' — the cap is throttling itself by '+(capBytesPerS*periodS/Math.max(1,budget)).toFixed(1)+'x'
         : ''));

    // THE CAP IS STILL A CAP. Widening the window must not raise the average rate:
    // whatever a period allows, divided by that period, is still the configured cap.
    const effectiveBps = (budget/periodS)*8;
    ok('...and the configured ceiling is still exactly that',
       effectiveBps <= capBps + 1,
       'effective '+Math.round(effectiveBps)+' bps vs cap '+capBps+' bps');

    // DRAIN VERSUS FILL, which is the check that would have caught this.
    const APPROX_BYTES_PER_EVENT = 250;
    const roomEvents  = Math.floor(budget/APPROX_BYTES_PER_EVENT);
    const drainPerSec = Math.min(C.uploadMaxBatch, roomEvents)/periodS;
    const fillPerSec  = C.gamepadHz;          // the gamepad sampler alone, before anything else
    ok('the uploader drains faster than the gamepad sampler fills',
       drainPerSec > fillPerSec,
       'drain '+drainPerSec.toFixed(1)+' events/s vs gamepad fill '+fillPerSec.toFixed(1)
       +' events/s'+(drainPerSec>fillPerSec ? ' (headroom x'+(drainPerSec/fillPerSec).toFixed(1)+')'
                                            : ' — THE RING GROWS FOREVER'));

    // Headroom for everything else that logs: telemetry, WebRTC stats, commands,
    // focus and visibility events. The gamepad is the loudest but not the only one.
    ok('...with room left over for every other event source',
       drainPerSec > fillPerSec*2,
       'drain '+drainPerSec.toFixed(1)+'/s against a '+fillPerSec+'/s floor');

    // Spending is still ACCOUNTED. A window that forgets what was spent is not a cap.
    REC.bytesWindow = [{t:(typeof _mono==='function'?_mono():performance.now()), n:1000}];
    const afterSpend = _bwBudgetLeft();
    ok('bytes already sent inside the window are deducted',
       afterSpend <= budget - 999,
       'budget '+Math.round(budget)+' -> '+Math.round(afterSpend)+' after a 1000 B send');
    REC.bytesWindow = [];

    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  }
  run().catch(e=>fetch('/__result',{method:'POST',body:'THREW: '+(e&&e.stack||e)}));
})();

"use strict";
/* ============================================================================
   WIRE — everything that crosses the boundary gets logged, without every call
   site having to remember to do it.

   The rule the operator asked for is "if we send it, receive it, succeed at it or
   fail at it, it is in the log". Relying on each caller to log is how half of it
   ends up missing precisely where something went wrong, so `fetch` and
   `WebSocket` are wrapped once, here, and every request, response, socket frame
   and failure is recorded with its outcome and how long it took.

   Loaded straight after core.js so the wrappers are in place before anything
   opens a socket or issues a request.
   ============================================================================ */
(function(){

  // Endpoints that exist to CARRY the log. Logging them would append a line for
  // every flush, which appends a line, which... The disk file still records
  // everything else; this only keeps the bus from feeding itself.
  const SELF = ['/__save', '/__result'];
  const isSelf = (u)=> SELF.some(s => u.indexOf(s) !== -1);

  // Long URLs make the overlay unreadable. Keep the path and the interesting
  // query bits, drop the origin when it is our own.
  function shortUrl(u){
    try{
      const abs = new URL(u, location.href);
      const sameOrigin = abs.origin === location.origin;
      let s = (sameOrigin ? '' : abs.origin) + abs.pathname;
      if(abs.search) s += abs.search.length > 60 ? abs.search.slice(0,60)+'…' : abs.search;
      return s;
    }catch(e){ return String(u).slice(0,120); }
  }

  function preview(d){
    try{
      if(typeof d === 'string') return d.length > 160 ? d.slice(0,160)+'…' : d;
      if(d instanceof Blob) return '[blob '+d.size+'B]';
      if(d instanceof ArrayBuffer) return '[buffer '+d.byteLength+'B]';
      return String(d).slice(0,160);
    }catch(e){ return '[?]'; }
  }

  /* ---- fetch ------------------------------------------------------------ */
  const origFetch = window.fetch && window.fetch.bind(window);
  if(origFetch){
    window.fetch = function(input, init){
      const url = (input && input.url) ? input.url : String(input);
      const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if(isSelf(url)) return origFetch(input, init);
      const short = shortUrl(url);
      const t0 = performance.now();
      LOG.net(method, short);
      return origFetch(input, init).then(function(r){
        const ms = Math.round(performance.now() - t0);
        // A 4xx/5xx is NOT an exception - fetch resolves for those, which is how
        // failed requests get mistaken for successful ones. Split them here.
        if(r.ok) LOG.ok(method, short, r.status, ms + 'ms');
        else     LOG.warn(method, short, 'HTTP ' + r.status, ms + 'ms');
        return r;
      }, function(e){
        const ms = Math.round(performance.now() - t0);
        // An aborted request is a deadline we set on purpose, not a fault.
        const aborted = e && (e.name === 'AbortError');
        (aborted ? LOG.warn : LOG.err)(method, short,
          aborted ? 'aborted after ' + ms + 'ms' : ((e && e.message) || String(e)) + ' after ' + ms + 'ms');
        throw e;
      });
    };
  }

  /* ---- WebSocket -------------------------------------------------------- */
  const OrigWS = window.WebSocket;
  if(OrigWS){
    function NeptuneWS(url, protocols){
      const ws = (protocols === undefined) ? new OrigWS(url) : new OrigWS(url, protocols);
      const name = shortUrl(url);
      const key = 'ws:' + name;
      LOG.net('WS opening', name);
      ws.addEventListener('open',  ()=> LOG.ok('WS open', name));
      ws.addEventListener('error', ()=> LOG.err('WS error', name));
      ws.addEventListener('close', (e)=>{
        // 1000/1005 are ordinary closes; anything else is worth seeing in amber.
        const clean = (e.code === 1000 || e.code === 1005);
        (clean ? LOG.net : LOG.warn)('WS close', name, 'code=' + e.code, e.reason || '');
      });
      // Control frames run at 20 Hz and telemetry not much slower, so these are
      // coalesced by LOG (see core.js) rather than printed per frame.
      ws.addEventListener('message', (e)=> LOG.rxRate(key, 'WS <', name, preview(e.data)));
      const send = ws.send.bind(ws);
      ws.send = function(d){
        try{ return send(d); }
        catch(err){ LOG.err('WS send failed', name, (err && err.message) || err); throw err; }
        finally{ LOG.txRate(key, 'WS >', name, preview(d)); }
      };
      return ws;
    }
    NeptuneWS.prototype = OrigWS.prototype;
    ['CONNECTING','OPEN','CLOSING','CLOSED'].forEach(function(k){ NeptuneWS[k] = OrigWS[k]; });
    window.WebSocket = NeptuneWS;
  }

  /* ---- anything that escaped everything else ---------------------------- */
  window.addEventListener('error', function(e){
    LOG.err('uncaught', (e.message||'error'), (e.filename||'').split('/').pop() + ':' + (e.lineno||0));
  });
  window.addEventListener('unhandledrejection', function(e){
    LOG.err('unhandled rejection', String((e && e.reason && e.reason.message) || e.reason || '').slice(0,200));
  });

  LOG.state('wire instrumented - fetch, websocket, uncaught errors');
})();

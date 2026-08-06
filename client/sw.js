/* NEPTUNE service worker — offline-first app shell + satellite tile cache.
   Architectural rule: the client works with the Pi off, unplugged, or not yet built.
   Once installed, the dashboard launches and runs with NO network of any kind — which
   also permanently solves file:// (an installed PWA is a real secure origin).

   Two caches:
     neptune-shell-vN   the precached app shell (bumped on release)
     neptune-tiles      satellite imagery — the offline map archive (SAVE OFFLINE + runtime)

   NEVER cache the vehicle: /api, /ws, /go2rtc, /stream* pass straight to the network so
   telemetry/video/commands always hit the real Pi (or fail fast — never a stale replay). */
/* Bump SHELL on every client release. It is the ONLY thing that evicts the old
   app shell — a stale cache silently pins the dashboard to old JS, which makes a
   deployed fix look like it did nothing. */
const SHELL = "neptune-shell-v37";
const TILES = "neptune-tiles";
const SHELL_ASSETS = [
  "./", "index.html", "origin.html", "manifest.json", "icon.svg", "css/styles.css",
  "js/config.js", "js/core.js", "js/wire.js", "js/store.js", "js/status.js", "js/recorder.js",
  "js/video.js", "js/net.js", "js/commands.js", "js/input.js", "js/controls.js",
  "js/render.js", "js/camera.js", "js/tiles.js", "js/map.js", "js/navui.js",
  "js/logview.js", "js/main.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith("neptune-shell-") && k !== SHELL).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

function isTile(url) {
  return /services\.arcgisonline\.com/.test(url) || /\/MapServer\/tile\//.test(url) ||
         /\/tiles\/\d+\/\d+\/\d+\.jpg/.test(url);
}
function isVehicle(url) {                       // anything that IS the vehicle → never cache
  return /\/api\//.test(url) || /\/ws\//.test(url) || /\/go2rtc\//.test(url) ||
         /\/stream/.test(url) || /\/clientlog/.test(url) || /\/__quit/.test(url);
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = req.url;

  if (isVehicle(url)) return;                   // pass through — needs the Pi (or fails fast)

  if (isTile(url)) {                            // cache-first: saved imagery works offline
    e.respondWith(
      caches.open(TILES).then((cache) =>
        cache.match(req).then((hit) => hit || fetch(req).then((res) => {
          if (res && res.status === 200) cache.put(req, res.clone());
          return res;
        }).catch(() => hit || Response.error()))
      )
    );
    return;
  }

  // navigations → cached shell when offline (the whole point: launch with nothing connected)
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("index.html").then((r) => r || caches.match("./"))));
    return;
  }

  // Same-origin shell assets -> NETWORK-FIRST with a cache fallback.
  //
  // Cache-first was wrong here: the launcher serves these from localhost, so the
  // network is the local disk and costs nothing, while a cache hit pinned the app
  // to whatever JS was current when the PWA was installed. Fixes to video.js or
  // status.js could then never reach an installed dashboard. Network-first keeps
  // the offline guarantee (the cache still answers when the server is gone) and
  // makes updates land on the next launch.
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.status === 200 && res.type === "basic") {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("index.html")))
  );
});

// SAVE OFFLINE (§2): the page asks the SW to warm the tile cache for a set of URLs.
self.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type === "cache-tiles" && Array.isArray(d.urls)) {
    e.waitUntil(caches.open(TILES).then(async (cache) => {
      let ok = 0, fail = 0;
      for (const u of d.urls) {
        try {
          if (await cache.match(u)) { ok++; continue; }
          const res = await fetch(u, { mode: "no-cors" });
          await cache.put(u, res.clone());
          ok++;
        } catch (_) { fail++; }
        if ((ok + fail) % 20 === 0 && e.source) e.source.postMessage({ type: "cache-progress", area: d.area, done: ok + fail, total: d.urls.length, ok });
      }
      if (e.source) e.source.postMessage({ type: "cache-done", area: d.area, ok, fail, total: d.urls.length });
    }));
  }
  if (d.type === "evict-tiles" && Array.isArray(d.urls)) {
    e.waitUntil(caches.open(TILES).then((cache) => Promise.all(d.urls.map((u) => cache.delete(u)))));
  }
});

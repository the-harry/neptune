"use strict";
/* ============================================================================
   NEPTUNE COMMAND — CONFIG
   ----------------------------------------------------------------------------
   The one file to tune. Every knob the client exposes lives here, grouped by
   concern. Loaded first (before any other script) and shared globally as the
   `CONFIG` object. Nothing else in the app hard-codes these numbers.

   Safe to edit by hand; values are read live at boot (and most are read every
   frame), so a reload picks up any change. No build step.
   ============================================================================ */
const CONFIG = {

  /* ---- NETWORKING -------------------------------------------------------- */
  // Endpoints are same-origin by default; override the host with ?host=IP:PORT
  // (remembered in localStorage) or via the CONFIG panel in the UI.
  paths: {
    control: '/ws/control'     // WebSocket — ROV control out + telemetry in
  },
  // Backend discovery (§6): default same-origin; if opened from disk with no ?host,
  // fall back to this configured host; ?host=IP:PORT always overrides.
  defaultHost:    '',          // e.g. '192.168.1.10:8000'
  sendRateHz:     30,          // control/camera/ballast TX rate (Hz) — always on, even zeros
  pingIntervalMs: 1000,        // WS ping cadence; round-trip shows as link latency
  levelSendMs:    120,         // how often dirty LED brightness levels are pushed
  reconnect: {                 // WebSocket auto-reconnect (capped exponential backoff)
    baseMs: 500,
    maxMs:  8000,
    factor: 1.8
  },
  staleTimeoutMs: 1000,        // telemetry older than this → readouts greyed/dashed as stale
  // How long to keep showing the last real telemetry as STALE before giving up on the
  // vehicle and handing back to the simulator. STALE is for a brief gap on a live
  // socket ("this reading is a moment old"); once the link is actually gone, freezing
  // the last frame forever is worse than useless — the controls stop responding and the
  // console looks broken. The sim resumes from the last real values, so the handover
  // is seamless.
  simFallbackMs:  3000,

  /* ---- MAP / NAVIGATION (dive track + position over a basemap) ----------- */
  map: {
    navWs:          '/ws/nav',   // backend nav telemetry (x/y/heading/depth); client integrates if absent
    redrawHz:       10,          // radar redraw cap (§4 — decoupled from telemetry, never starves video)
    metersPerPixel: 0.6,         // initial zoom for the FULL-SCREEN views (+/- buttons + wheel)
    // The radar circle has its own, tighter zoom and never follows the big map's.
    // 0.6 m/px over a 200 px circle is 120 m across - at the sub's ~1 m/s that is two
    // minutes of full throttle to draw one circle-width of track, which reads as "the
    // trace is not working". 0.25 m/px gives ~50 m across: movement is visible within
    // seconds, which is the entire point of a glance instrument.
    radarMetersPerPixel: 0.25,
    subMaxSpeedMs:  1.0,         // client-side integrator speed at full throttle (disk/SIM fallback)
    maxDepthColorM: 6.0,         // depth at which the track colour saturates (shallow→deep)
    maxTrackPoints: 4000,        // full-res track cap; decimated further for display (§4)
    radarPx:        200,         // collapsed radar diameter in px — a glance instrument (§6: keep 180–220)
    headingUp:      true,        // §2 — collapsed radar rotates heading-up (forward=up); false = north-up
    allStopOnExpand:true,        // §3 — expanding the map commands throttle to zero (safe "pause" analogue)
    originRefineM:  30,          // §2 — above this device-fix accuracy (m), offer tap-to-refine
    // On launch, if a fresh fix lands this far from the STORED origin, the handheld has
    // clearly been moved to a new site — offer to re-set rather than silently sailing on
    // a launch point from somewhere else. Below it, the difference is just Wi-Fi scatter
    // and the stored origin is kept (moving the frame mid-dive would invalidate the track).
    originMoveM:    150,
    // Age at which the ORIGIN readout turns amber. Not an expiry - the origin stays
    // usable - just a prompt to confirm it still refers to where you actually are,
    // since nothing can re-acquire it in the field without internet.
    originStaleH:   8,
    // Open on the sharpest imagery the provider has rather than a fixed metres-per-pixel,
    // and never upscale a coarse tile when a finer one exists. NOTE: which imagery you
    // get is the provider's choice — Esri World Imagery is a single curated mosaic, so
    // "a sunny day" is not something a client can request. Max resolution is.
    startAtMaxZoom:   true,
    preferSharpTiles: true,
    // The ROG Ally paddles: either one ALONE zooms the map (both together = SURFACE).
    zoomKeys:      { in:'F10', out:'F9' },
    autoOrigin:     true,        // §2 — auto-request the handheld's location on load when no origin is set
    // Keep the handheld's OWN position live (watchPosition), not just a fix on load.
    // The marker is always live; the launch point follows it only until a dive starts,
    // because moving the dead-reckoning datum mid-dive would shift the whole track.
    followMe:       true,
    meMinMoveM:     3,           // below this it is GPS jitter, not the operator walking
    meMinGapMs:     5000,        // and never rewrite the stored origin faster than this
    // Past this, the fix is "last known" rather than live: the dot goes yellow and the
    // tether range is tagged LAST KNOWN. A red dot means a position placed by hand.
    meStaleMs:      30000,
    // BLIND NAV — when the camera feed is gone, promote the map to the primary view so
    // the sub can still be driven on instruments instead of a black rectangle. This is
    // NOT the expanded map: that engages all-stop (a planning view), whereas this is a
    // DRIVING view — heading-up, following the sub, throttle live.
    blindNav:       true,
    blindAfterMs:   4000,        // how long the feed must be down before switching (debounce)
    // Ground distance the blind-nav view spans across the SHORTER screen edge. Set as
    // metres, not m/px, so it means the same thing on any display: the scale is derived
    // from the actual canvas on entry. 0.6 m/px inherited from the big map worked out at
    // ~768 m across - useless for driving a 1 m/s vehicle.
    blindSpanM:     60,
    blindBackMs:    1500,        // how long it must be back before switching away again
    // Cold start: no feed has ever existed, so there is no blip to debounce and no
    // reason to sit on a NO FEED screen. Blind nav is the fallback in every mode.
    blindColdMs:    1200,
    // --- satellite basemap (§3) — raster XYZ tiles drawn straight to the radar canvas (zero-dep) ---
    tileProvider:   'esri',      // 'esri' (World Imagery) — see tileProviders below
    tileProviders: {
      esri: { // NOTE the {z}/{y}/{x} order (y before x) — Esri, unlike standard XYZ
        url:'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        maxzoom:19, attribution:'Imagery © Esri' },
      // offline cache served by the Pi from a downloaded area (standard {z}/{x}/{y}, TMS handled server-side)
      offline: { url:'/api/areas/{area}/tiles/{z}/{x}/{y}.jpg', maxzoom:19, attribution:'Imagery © Esri (offline)' }
    },
    tintCollapsed:  0.45,        // §5 — dark overlay opacity over imagery in the radar (readability)
    tintExpanded:   0.25,        // §5 — lighter tint in the expanded view
    tileFadeMs:     220,         // fade tiles in as they load
    // §4 — zoom range per detail level (must match the backend NAV_SAT_ZMIN/ZMAX)
    detailZooms:  { standard:[16,18], high:[16,19] }
  },

  /* ---- CAMERA (WOLFANG control plane + go2rtc WebRTC video) --------------- */
  camera: {
    telemetryWs:  '/ws/telemetry',        // camera status pushed here (~15s)
    webrtcWs:     '/go2rtc/api/ws',        // go2rtc WebRTC signaling (nginx-proxied)
    stream:       'sub',                   // go2rtc stream name (go2rtc.yaml)
    statusPollMs: 5000,                    // REST /api/status poll (backup to the WS)
    videoRetryMs: 2500,                    // WebRTC reconnect backoff floor (§7.4 self-heal)
    stillQuality: 0.92,                    // topside PIC copy: JPEG quality (0-1)
    // A page cannot screenshot itself, so PIC asks the LAUNCHER (which serves this
    // page from localhost) for a real screen capture - metrics, control rail,
    // basemap and all. Set to '' to always use the in-page canvas composite.
    screenshotEndpoint: '/__screenshot',
    screenshotTimeoutMs: 4000,             // never let a wedged launcher block PIC
    // Everything this session produces goes through the launcher into
    // client/navigation_logs/{images,videos,logs}. Empty '' = no launcher writes.
    saveEndpoint:   '/__save',
    recordEndpoint: '/__record',
    recordFps: 30,                         // gdigrab capture rate
    recordCrf: 23                          // x264 quality: lower = better + bigger
  },

  /* ---- BLACKBOX client recorder (logging addendum) ----------------------- */
  /* ---- rendering --------------------------------------------------------- */
  ui: {
    // The AMD display driver on this handheld bugchecks the machine under
    // sustained compositing load (0x133 ISR amdkmdag, confirmed from the crash
    // dump). Reduced mode drops the full-screen backdrop blurs and the scan line,
    // which is where nearly all of the per-frame GPU cost was. Set false to get
    // the glass back on hardware that can take it.
    reduceGpu: true,
  },

  /* ---- LOGS overlay ------------------------------------------------------ */
  log: {
    // Rows kept in the overlay's DOM. The in-memory ring is larger, and the file
    // under navigation_logs/logs is the complete record - this is only what the
    // browser has to lay out.
    viewMaxRows: 1200,
  },

  recorder: {
    // The session log writes itself to navigation_logs/logs as it happens. Flushed
    // on a timer, not at shutdown: this handheld has an unresolved kernel fault
    // that takes the machine down with no unload event, and a log kept in memory
    // until exit is lost exactly in the sessions worth reading.
    diskFlushMs: 5000,
    diskQueueMax: 5000,        // bounded if the launcher is not there; oldest dropped
    enabled:       true,
    session:       '/api/session',    // adopt the Pi's session id on connect (§1)
    clientlog:     '/api/clientlog',  // batched upload target (§5)
    uploadEveryMs: 5000,              // upload every 5 s …
    uploadMaxBatch:200,               // … or every 200 events, whichever first (§5)
    maxEvents:     200000,            // IndexedDB ring cap (~50 MB at ~250 B/event), oldest-out (§5)
    uploadCapBps:  64000,             // 64 kbps upload cap — never starve video/telemetry (§5)
    backoffMaxMs:  30000,             // exponential backoff ceiling on repeated upload failure (§5)
    webrtcHz:      1,                 // WebRTC receiver-stats sample rate (§4.1)
    gamepadHz:     10,                // raw gamepad sample rate (§4.1)
    tlmWindow:     100,               // telemetry frames per compact tlm_rx line (§4.2)
    stalenessMs:   500                // surface max_age in the HUD when the newest frame is older (§4.2)
  },

  /* ---- INPUT ------------------------------------------------------------- */
  deadzone:       0.08,        // analog stick deadzone (0..1)

  /* ---- ON-SCREEN CONTROLS (LEDs + ballast sliders, SURFACE button) ------- */
  ledStep:          0.10,      // brightness change per arrow TAP (0..1)
  ledRampPerS:      0.80,      // brightness change per second while an arrow is HELD
  lightLevelRateS:  0.70,      // brightness change per second from D-pad / [ ] keys
  lightOnDefault:   0.33,      // level a light jumps to when toggled on from ~zero (33%)
  lightOnThreshold: 0.02,      // level at/below which a light counts as "off"
  ballastStep:      0.01,      // ballast change per arrow TAP = 1% (fine, hardware-safe)
  ballastRampPerS:  0.12,      // ballast change per second while an arrow is HELD (slow for the stepper)
  ballastSlewPerS:  0.10,      // MAX rate the *commanded* target moves toward what you set,
                               //   so drags/jumps are applied smoothly (the stepper can't teleport)
  ballastDeadband:  0.01,      // chase tolerance: |target-actual| under this → send "hold"
  arrowHoldDelayMs: 250,       // press longer than this on an arrow → ramp instead of single step
  surfaceHoldMs:    900,       // press-and-hold duration to fire the SURFACE emergency (UI button)
  // Gamepad SURFACE: hold BOTH paddles (ROG Ally M1/M2 → F9/F10) together for this long.
  // Same deliberate hold as the UI button, so the dangerous emergency can't fire on a single tap.
  surfaceComboKeys:   ['F9','F10'],
  surfaceComboHoldMs: 3000,

  /* ---- TETHER — the cable is a hard physical limit, so plan against it -----
     Range is the STRAIGHT-LINE distance from the launch point (the local frame's
     0,0), taken in 3D: depth eats into how far you can reach horizontally, which
     is exactly the trade-off worth seeing before committing to a departure point.

     SIM clamps to it. A dive you cannot physically reach must not look reachable
     on the bench — that is the whole point of planning one there.

     REAL never clamps, only warns. The launch point moves: pay out more cable,
     walk the bank, drift the boat, and a "limit" the console enforced would be
     both wrong and dangerous. The operator is the one who knows.                */
  tether: {
    lengthM:    100,     // cable you actually have
    warnFromM:  80,      // amber from here
    clampInSim: true,    // SIM cannot exceed lengthM
    showRing:   true     // draw the reachable circle around the origin
  },

  /* ---- SIMULATION (used only when NO real telemetry is arriving) ---------
     These shape the fake telemetry so every gauge animates offline. They have
     no effect once the server is sending real telemetry.                     */
  sim: {
    maxDepthM:      9.0,       // depth at 100% ballast fill
    basePressurePsi:14.7,      // surface pressure (≈1 atm); depth adds to this
    psiPerMeter:    1.42,      // pressure gain per metre of depth
    ballastRatePerS:0.12,      // how fast the sim tank fills/empties (fraction/sec) — slow, like a syringe stepper
    headingRatePerS:40,        // heading change per unit of steer input (deg/sec)
    surfaceDrainMs: 4000,      // after SURFACE, force-drain the tank for this long
    depthLerp:      0.8,       // depth easing toward target (per second, 0..1-ish)
    batteryDrainVPerS: 0.0004  // cosmetic battery sag over time
  }
};

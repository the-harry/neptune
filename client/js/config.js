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
  sendRateHz:     30,          // control/camera/ballast TX rate (Hz) — always on, even zeros
  pingIntervalMs: 1000,        // WS ping cadence; round-trip shows as link latency
  levelSendMs:    120,         // how often dirty LED brightness levels are pushed
  reconnect: {                 // WebSocket auto-reconnect (capped exponential backoff)
    baseMs: 500,
    maxMs:  8000,
    factor: 1.8
  },
  staleTimeoutMs: 1000,        // telemetry older than this → readouts greyed/dashed as stale

  /* ---- MAP / NAVIGATION (dive track + position over a basemap) ----------- */
  map: {
    navWs:          '/ws/nav',   // backend nav telemetry (x/y/heading/depth); client integrates if absent
    redrawHz:       10,          // map redraw cap (§7.5 — decoupled from telemetry, never starves video)
    metersPerPixel: 0.6,         // initial zoom (0.6 m/px). +/- buttons + wheel adjust.
    subMaxSpeedMs:  1.0,         // client-side integrator speed at full throttle (disk/SIM fallback)
    maxDepthColorM: 6.0,         // depth at which the track colour saturates (shallow→deep)
    maxTrackPoints: 4000,        // decimate older points beyond this (§7.5)
    gridMeters:     10,          // grid spacing in metres
    sonarFadeMs:    1400         // direction overlay stays this long after the last input, then fades
  },

  /* ---- CAMERA (WOLFANG control plane + go2rtc WebRTC video) --------------- */
  camera: {
    telemetryWs:  '/ws/telemetry',        // camera status pushed here (~15s)
    webrtcWs:     '/go2rtc/api/ws',        // go2rtc WebRTC signaling (nginx-proxied)
    stream:       'sub',                   // go2rtc stream name (go2rtc.yaml)
    statusPollMs: 5000,                    // REST /api/status poll (backup to the WS)
    videoRetryMs: 2500                     // WebRTC reconnect backoff floor (§7.4 self-heal)
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
  surfaceHoldMs:    900,       // press-and-hold duration to fire the SURFACE emergency

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

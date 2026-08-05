# NEPTUNE COMMAND — client

Vanilla-JS control client for the tethered canal-cleaning ROV. Pure front-end
API consumer: WebRTC video (go2rtc), WebSockets for ROV control + camera
telemetry, and REST for camera commands/config/files. No framework, no build
step, no dependencies.

**This runs on the ROG Ally, not the Pi.** The Pi is backend-only. Double-click
**[`launch/Neptune.bat`](launch/)** — it makes a desktop shortcut, starts a tiny local
static server (a secure origin — geolocation and the PWA need one), and opens
**Chrome/Edge fullscreen** at `http://localhost:8080/?host=<pi-ip>`. The Pi is plain HTTP
(sealed tether), so there's no cert to trust. Run `launch/tether-setup.ps1` once as admin
first (fixed tether IP). See [`launch/README.md`](launch/README.md).

`?host=<pi-ip>` is remembered (localStorage) and can also be set in the CONFIG panel.
(Opening from `file://` is blocked — it has no secure context and can't reach the Pi;
that's why the launcher serves it from `localhost` instead.)

## Offline-first: the client works without the backend

**Standing rule:** the only things that require the Pi are the things that *are*
the vehicle (live video, live telemetry, and any command that moves the sub).
Everything else works with the Pi off, unplugged, or not yet built — and no panel
checks backend availability for a dependency it doesn't have.

- **PWA (`sw.js`):** a service worker precaches the whole app shell and keeps a
  separate `neptune-tiles` cache. Once installed, the dashboard launches and runs
  with **no network of any kind** — which also permanently solves `file://`, since
  an installed PWA is a real secure origin (geolocation etc. stay available). No CDN
  references anywhere; everything is vendored.
- **Client owns its state (`store.js`, IndexedDB + Cache API):** origin, settings,
  the saved-area registry, and dive logs live client-side. `SAVE OFFLINE` writes
  satellite tiles into the Cache API **from the browser** — you can save an area
  the night before with the Pi powered off. Mirroring to the Pi is an optional
  second copy, never a precondition.
- **Client-first services:** address search and geocoding go **browser → Nominatim
  directly** (they need *internet*, not the Pi); map imagery is fetched by the
  browser and served from the tile cache offline. The old `MAP AREAS: backend
  unreachable` / `search unavailable` anti-patterns are gone.
- **Degradation model (`status.js`):** five states, tracked and shown separately
  because they genuinely fail one at a time — **NET** (internet), **PI** (ROV control
  link), **VIDEO** (go2rtc feed), **CAM** (WOLFANG control plane), **VEH** (vehicle).

  Each control declares what it needs in markup (`data-needs="link"`, `"cam"`, …) and
  **only** the controls owned by a down subsystem are greyed. Losing the ROV link no
  longer disables the camera buttons, and nothing disables the map, radar, search,
  saved areas, dive logs, the config panel or the input remapper — those are
  client-owned and work with the Pi switched off.

  This replaced a single `body.backend-down aside { pointer-events: none }` rule that
  killed the entire control rail as one blob whenever the Pi was unreachable.

  The Pi's own health is polled separately (`/api/system`), so CPU/RAM/disk and both
  network interfaces stay visible even while the vehicle link is down. Reconnection is
  automatic and silent everywhere (no retry buttons).
- **Blind nav (`map.js`):** when the camera feed drops for more than
  `CONFIG.map.blindAfterMs` (4 s), the map takes over the full screen as the *driving*
  view so the sub can still be flown on instruments instead of a black rectangle. A
  `BLIND NAV · NO CAMERA` banner says so plainly — the operator must never think they
  are looking at water.

  It is deliberately **not** the expanded map: expanding engages an all-stop and switches
  to north-up, because that is a *planning* view. Blind nav keeps `MAP.expanded === false`,
  so everything keyed on it stays in its piloting form — heading-up, following the sub,
  throttle live, no all-stop. Only the layout changes.

  Debounced both ways so a brief WebRTC hiccup cannot flip the view mid-manoeuvre, with a
  shorter window on a cold start (`blindColdMs`) where there is no established feed to blip.
  The video shrinks to a corner tile rather than disappearing: it is how you notice the feed
  return. The tile is a **status indicator, not an exit** — there is no full-screen NO FEED
  state to go back to, and the feed returning is what restores the camera view.
- **Commands never queue (§4):** a `Command` fails fast and visibly when the
  backend is down — rejected, logged (`cmd_rejected`), never buffered or replayed
  (a late `throttle 100%` is a hazard). Only inert data (telemetry/log records)
  buffers through an outage and uploads on reconnect.

## Layout

```
client/
├── index.html          # markup only — links the CSS + loads the scripts in order
├── origin.html          # standalone phone GNSS capture page → POST /api/origin
├── css/
│   └── styles.css       # all styles (self-contained; replaces Tailwind + fonts + icons)
└── js/
    ├── config.js        # ★ the tunable config — every knob lives here
    ├── core.js          # $ / clamp helpers, LOG bus, state object, host resolution
    ├── video.js         # WebRTC player (go2rtc) + NO-FEED / reconfiguring overlay
    ├── net.js           # ROV WebSocket link, telemetry ingest, send/ping/level loops
    ├── commands.js      # discrete commands (arm, stop, surface, magnet, lights)
    ├── input.js         # gamepad + keyboard, remappable actions/bindings, computeInput
    ├── controls.js      # on-screen sliders, SURFACE hold, CONFIG/mapper modal
    ├── render.js        # local simulation + telemetry→UI + status badges
    ├── camera.js        # WOLFANG camera plane: telemetry, record/capture, config, files
    ├── tiles.js         # zero-dep raster XYZ satellite tile engine (Esri), overzoom, screen↔latlon
    ├── map.js           # radar: camera-primary circular minimap, satellite basemap, track, expand
    ├── navui.js         # origin (device geolocation + tap-to-refine) + navigate-and-select area download
    └── main.js          # RAF frame loop + bootstrap + window.NEPTUNE console API
```

### Why classic scripts (not ES modules)

The scripts are plain `<script>` tags sharing global scope, loaded in dependency
order (`config` first, `main` last). This is deliberate: ES modules (`import`/
`export`) are blocked over `file://` (null origin → CORS), and a hard rule here
is that the **same files must run from disk and from the Pi**. Classic scripts
do that. If you add a file, add its `<script>` tag to `index.html` in the right
spot (after anything it references at load time).

## Tuning

Open **`js/config.js`**. Everything is grouped and commented — networking,
input, on-screen controls, and the simulation model. Values are read live, so
just edit and reload. Nothing else hard-codes these numbers.

## App / fullscreen (feels like an app, no URL bar)

- The page **goes fullscreen on your first tap/key** (browsers block auto-fullscreen
  on load). `Esc` exits. It also ships a **web app manifest** (`display:fullscreen`),
  so when served over http you can **Install** it (Chrome ⋮ → *Install Neptune*) and
  launch it chromeless from the app list.
- For a **truly URL-less kiosk boot** on the ROG Ally, launch Chrome/Edge in app mode:
  ```
  chrome --kiosk --app="http://<pi>/?host=<pi>:8000"
  #   or from disk:
  chrome --kiosk --app="file:///path/to/client/index.html?host=<pi>:8000"
  ```
  (`--app` = no tabs/URL bar; `--kiosk` = fullscreen locked. Add `--start-fullscreen`
  if you prefer a normal window that starts maximized.)

## Talking to the Pi backend

The Pi is **backend-only** — it does not serve this folder. Run the client on the Ally
(above) and point it at the Pi with `?host=<pi-ip>` (remembered in localStorage; also
settable in the CONFIG panel). The Pi's nginx sends permissive CORS headers, so the
cross-origin fetches work; WebSockets aren't subject to CORS.

Endpoints the client expects on the Pi:
- `WS  /go2rtc/api/ws?src=sub` → WebRTC video signaling (go2rtc)
- `WS  /ws/control`   → ROV control out + telemetry in
- `WS  /ws/telemetry` → camera status (~15s)
- `REST /api/status · /api/menu · /api/config/{p} · /api/record/toggle · /api/capture · /api/files · …`

For pure UI/dev work with **no Pi**, run the backend locally in mock mode
(`cd api && python main.py`) and just open **`http://localhost:8000/`** — FastAPI
serves the client same-origin, so every gauge animates from the simulator. (A cross-origin
`?host` defaults to plain `http`/`ws`, matching the Pi; add `&secure=1` only if you ever
front the Pi with HTTPS.)

## Navigation & radar

The **camera feed is the primary instrument** and fills the viewport. The map is a
**GTA-style circular radar** in the bottom-left (`#radar`, ~200 px) — the live
basemap clipped to a circle, with the heading needle / input vector, `FWD`/`REV`,
and a rotating **N** drawn on top. `THROTTLE` / `STEER` read out to its right; the
scale bar sits below and updates with zoom. There is exactly **one** map instance;
in the collapsed state it lives inside that circle.

**Heading-up by default** (`CONFIG.map.headingUp`): the map rotates under a fixed
forward-pointing sub marker (via the map's bearing / a canvas transform — never a
CSS transform on a square, which would leave uncovered corners in the circle). Set
`headingUp:false` for north-up.

**Tap the radar to expand** it to fullscreen. The same instance animates open; the
video shrinks to a **picture-in-picture** tile (never unmounted — no WebRTC
reconnect). `Esc`, the ✕ button, or tapping the PiP video collapses it back.

### A submarine can't be paused (§3)

GTA freezes the world when the map opens; a sub keeps drifting. So expanding the
map issues a **safe all-stop** instead (`CONFIG.map.allStopOnExpand`, default on):
throttle+steer are held at zero and **`ALL STOP — MAP OPEN`** is shown. Telemetry,
video, recording, and every safety indicator keep running at full rate; depth, the
`SURFACE` warning, and the whole status strip stay live over the map. **Any thrust
or steer input instantly collapses the map and returns control** — the operator
never has to find a close button to drive. Set `allStopOnExpand:false` to hold
station under power in current instead.

### Satellite basemap (§3)

All map views default to **satellite imagery** — `js/tiles.js` is a tiny zero-dep
raster XYZ tile layer drawn straight to the radar canvas (no MapLibre needed). It
handles the **Esri World Imagery** `{z}/{y}/{x}` order (y before x), heading-up
rotation (a canvas transform, so no uncovered corners in the circle), **overzoom**
from the nearest cached parent when a high-zoom tile 404s (blurry beats blank), and
a dark **readability tint** over the imagery (darker in the radar, lighter expanded).

Provider is configurable in `config.js` (`tileProvider` / `tileProviders`):
- **online** (default): Esri World Imagery, fetched straight from the browser while
  the Pi has connectivity — used to find and download areas. `Imagery © Esri` is
  shown in the expanded view.
- **offline**: when a downloaded area is **active**, tiles come from the Pi's cached
  MBTiles (`/api/areas/{name}/tiles/{z}/{x}/{y}.jpg`) — works with no internet, the
  field case.

The **OSM waterway centreline** (the snapping target) is fetched during download and
drawn as a bright-cored, dark-cased line over the imagery.

**Honest empty states (§6):** no area/origin → compact `NO MAP` / `NO ORIGIN` in the
circle (no fake marker), full explanation in the expanded view. The overlay steps
aside while you're actively tapping an origin or selecting an area.

### Setting the origin (§2)

The fix comes from the **handheld's own browser**. On load with no origin, Neptune
**auto-requests** `getCurrentPosition` (needs a secure context — which is why the
launcher serves it from `localhost`) and centres the map on you. The ROG Ally has no GNSS — Windows resolves position by WiFi,
so accuracy reads tens–hundreds of metres; the **ORIGIN** tile shows it (`SET ±120m`),
and above `originRefineM` (30 m) a non-blocking **TAP TO REFINE** prompt lets you tap
your bank on the imagery (which beats WiFi). **North comes from the sub's IMU**
(`heading0`, captured atomically in the same set-origin action) — never the handheld.
Manual entry and post-hoc dx/dy/rotation adjustment remain via the ORIGIN tile. The
phone page `origin.html` also still works.

### Navigate-and-select area download (§4)

No coordinates to type. Expand the map, **pan/zoom** to the spot (starting from your
location), press **＋ AREA**: a fixed selection rectangle overlays the viewport and a
live **`~N tiles · ~M MB`** readout updates as you move. Pick **Standard** (z16–18) or
**High** (z19), press **DOWNLOAD THIS AREA** — one button. The Pi walks the tile
pyramid (rate-limited, real User-Agent), writes an MBTiles archive, fetches the
waterway centreline, and **auto-names** the area by reverse geocoding. Progress
streams over `/ws/nav`. Downloaded areas are a simple list (thumbnail · size ·
activate · delete). Place search (Nominatim, online) pans the map from the toolbar.

### The HUD is sacrosanct

The whole map (tile fetch, projection, snapping, redraw) runs inside an error
boundary. A basemap failure, a 404, or a draw error leaves the radar **blank but all
instruments — thrust, steering, video — fully live**. The map never captures
gamepad/keyboard piloting input (handlers are scoped to the canvas; MapLibre, if ever
vendored, is `keyboard:false`).

## PIC takes two copies

The camera's own JPEG goes to the SD card — which is inside the vehicle, in the water,
on a card that has to be physically recovered. If the camera is flat, absent or
unreachable there is no copy at all. So **PIC also grabs what the operator is looking
at**, topside, into IndexedDB *and* as a file download.

The two halves are independent (§3): a dead camera does not stop the local still, and a
failed local save does not stop the camera. The toast reports each separately and never
claims a copy that was not made.

The frame source is the live video when there is one, and **the map otherwise** — in
blind nav the map *is* the view, so a still of a black video element would be worse than
nothing: it would look like the camera worked. That is also why PIC does something
useful in **sim**, where there is no camera at all, so the whole path can be exercised
on the bench.

Telemetry travels with the image (time, depth, heading, pressure, ballast, pack volts,
local x/y, origin, and whether it was taken in sim) — a still with no depth or heading is
a holiday snap; the point is being able to place it in the dive afterwards.

The id carries milliseconds because it is the IndexedDB key: at second resolution, two
presses inside the same second silently overwrote each other.

**The map canvas is tainted, on purpose.** Satellite tiles are loaded without
`crossOrigin` and the offline archive stores them as *opaque* responses — requiring CORS
would break the map in the field, which matters far more than a screenshot. The cost is
that the browser refuses to export that canvas (`Tainted canvases may not be exported`),
so a capture of the map **re-renders the same frame without the imagery** and marks the
record `basemap:false`. Every vector layer — grid, centreline, track, sub — survives,
which is what carries the navigational information. The video is a `MediaStream` and
never taints, so the camera view is unaffected.

A slim caption is burnt along the bottom (time, depth, heading, `SIM`, `MAP`/`CAM`,
`NO BASEMAP`) because the downloaded file leaves the app and loses the record around it —
the same reason the camera's own timestamp is kept `ACTIVE`. On a narrow canvas it
degrades deliberately: shrink, then shorten the timestamp, then drop from the least
important end. `SIM` outranks the date — mistaking a simulated frame for a real one is a
worse error than not knowing the day, and the filename carries the date anyway.

```js
await NEPTUNE.stills()          // metadata for every still, newest first
await NEPTUNE.openStill(id)     // pop one out of IndexedDB into a tab
```

## Blackbox recorder (two-sided logging)

`js/recorder.js` is the topside half of a two-sided flight recorder. It logs the
same events the Pi logs — from *the operator's side* — so the two can be differenced
afterwards to locate faults neither log pins down alone ("it didn't respond" =
command never sent / never arrived / ack lost; "video froze" while the encoder was
healthy = tether, not camera; a bad manoeuvre made on stale data).

- **Session (§1):** adopts the Pi's `session_id`/`pi_boot_id` from `GET /api/session`
  on connect; a Pi reboot starts a fresh client file.
- **Clock sync (§2):** an SNTP-style `t1/t2/t3/t4` exchange rides the existing WS
  ping every second → `rtt`/`offset` logged as `clock_sync` (and `rtt` feeds the
  LINK readout). Client timestamps are **always** the client's own monotonic time —
  never rewritten; correction happens only in analysis.
- **Correlation IDs (§3):** every discrete command gets a UUID at operator intent
  (`commands.js`), carried through the socket and echoed in the Pi's `ack`, so the
  full 8-stage lifecycle (`cmd_intent→send→recv→validate→apply→ack_send→ack_recv→
  confirm`) ties together across both logs.
- **Client-only signals (§4):** 1 Hz WebRTC receiver stats, 10 Hz raw gamepad,
  browser/visibility/error events, environment, and compact `tlm_rx` sequence
  ranges + gaps. `max_age_ms` (age of the newest telemetry at render) drives a
  **STALE DATA** HUD badge over `CONFIG.recorder.stalenessMs`.
- **Durability (§5):** an IndexedDB ring buffer (oldest-out) written immediately;
  uploaded to `POST /api/clientlog` every 5 s / 200 events, deleted only after the
  Pi confirms; backlog flushes first on reconnect; `up_lag_ms` tags how long each
  record waited; exponential backoff + a 64 kbps cap keep it off the tether;
  `beforeunload` does a `sendBeacon` flush. **CONFIG → BLACKBOX → EXPORT LOG**
  downloads the whole ring as JSONL even with the link fully down; **MARK EVENT**
  drops a bookmark.

Console: `NEPTUNE.mark('note')`, `NEPTUNE.exportLog()`, `NEPTUNE.REC`.

### Analysis — `rovlog`

On the Pi (`api/`): `python -m blackbox.rovlog <cmd>`
- `merge nav.jsonl client.jsonl` — one clock-aligned stream (interpolated offsets;
  flags windows where the offset estimate is unreliable).
- `diverge <session>` — the payoff: lost commands (by `c_id`), lost telemetry (Pi
  sent-ranges vs client received-ranges + worst gap), per-stage latency p50/p95/max,
  staleness distribution, video divergence, one-sided outages, clock anomalies.
- `timeline <session> --around <t> --window <s>` — side-by-side text around an incident.
- `bundle <session>` — incident zip (both logs + merge + diverge + config).

## Testing the fallbacks

- **SIM mode** — open from disk, no server → gauges animate from your inputs.
- **NO FEED / RECONFIGURING** — no WebRTC → `NO FEED`; a camera mode change → `RECONFIGURING` (auto-heals).
- **Camera controls** — REC + STILL buttons (bottom-right) are in-dive; config + files are gated behind CONFIG → **SURFACED**.
- **Keyboard ↔ gamepad** — plug/unplug a pad; the INPUT source switches live.
- **Remapping** — CONFIG → watch the raw-input monitor, hit `MAP`, press a button/key (great for the ROG Ally back paddles).

## Debug console

`window.NEPTUNE` is exposed:

```js
NEPTUNE.logRate(true)    // log every control tick + telemetry
NEPTUNE.log(false)       // silence
NEPTUNE.state            // inspect live state
NEPTUNE.openMapper()      // open CONFIG
NEPTUNE.resetBindings()   // restore default input map
NEPTUNE.connectVideo()    // (re)connect the WebRTC feed
NEPTUNE.camRecordToggle() // camera record toggle
NEPTUNE.openOrigin()      // open the origin modal
NEPTUNE.openAreas()       // open the area manager
```

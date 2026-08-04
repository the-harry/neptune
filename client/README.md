# NEPTUNE COMMAND — client

Vanilla-JS control client for the tethered canal-cleaning ROV. Pure front-end
API consumer: WebRTC video (go2rtc), WebSockets for ROV control + camera
telemetry, and REST for camera commands/config/files. No framework, no build
step, no dependencies.

**Serve it over HTTPS from the Pi** — `https://<pi>/` (nginx, self-signed cert
trusted once on the handheld). This is required (§1): geolocation (the origin
fix) only works in a secure context, and a `file://` origin can't fetch the Pi
(video/telemetry/maps) — so opening `index.html` from disk now shows a **blocking
message** explaining how to launch correctly (with a SIM-only escape hatch for
quick offline UI checks). The backend resolves **same-origin**; `?host=IP:PORT`
remains an override only.

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

## Serving from FastAPI (the Pi)

The backend in [`../api`](../api) already serves this folder (it mounts
`client/` at `/` with `html=True`) and implements the endpoints below. Just run
it (`cd api && python main.py`) and open `http://<host>:8000/`.

Endpoints the client expects (all same-origin, proxied by nginx on the Pi):
- `WS  /go2rtc/api/ws?src=sub` → WebRTC video signaling (go2rtc)
- `WS  /ws/control`   → ROV control out + telemetry in
- `WS  /ws/telemetry` → camera status (~15s)
- `REST /api/status · /api/menu · /api/config/{p} · /api/record/toggle · /api/capture · /api/files · …`

Override the host for local dev with `?host=192.168.1.10:8000` (remembered in
localStorage) or via the CONFIG panel.

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
**auto-requests** `getCurrentPosition` (secure context required — hence HTTPS) and
centres the map on you. The ROG Ally has no GNSS — Windows resolves position by WiFi,
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

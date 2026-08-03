# NEPTUNE COMMAND — client

Vanilla-JS control client for the tethered canal-cleaning ROV. Pure front-end
API consumer: WebRTC video (go2rtc), WebSockets for ROV control + camera
telemetry, and REST for camera commands/config/files. No framework, no build
step, no dependencies.

It runs two ways from these exact same files:

1. **From disk** — double-click `index.html` (`file://`). Fully interactive in
   simulation mode with no server, no video, no gamepad.
2. **Served by FastAPI on the Pi** — mount this folder as static files.

## Layout

```
client/
├── index.html          # markup only — links the CSS + loads the scripts in order
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
```

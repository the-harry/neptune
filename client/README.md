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

  Four of them have a glyph in the top-left status row, in this order:

  | | Glyph | Subsystem | Green / amber / red |
  |---|---|---|---|
  | 1 | signal arcs | **Internet** | online / — / offline |
  | 2 | chain link | **ROV link** — the tether to the Pi | online / connecting / offline |
  | 3 | eye | **Video** | live feed / connecting / blind |
  | 4 | submarine | **Vehicle** | connected / — / simulated (pulsing on a leak) |

  The link glyph was a server-rack drawing that nobody could read on a 18 px icon; a chain
  link is the same idea in a shape that survives the size. `CAM` no longer has its own
  glyph — the REC button's colour carries whether a camera is present.

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
  view so the sub can still be flown on instruments instead of a black rectangle. The
  **video status icon is an eye** — open and green with a live feed, struck through and
  red when blind — so the operator can never think they are looking at water. That
  replaced a full-width `BLIND NAV · NO CAMERA` banner: one glyph in the status row says
  the same thing and gives the screen back.

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

### On a real link, nothing is synthesised

**We never fall back to SIM while a vehicle is on the link.** `vehicleLinked()`
(`core.js`) is the switch, and it separates two things that must never be confused:

| | Comes from | Moves when |
|---|---|---|
| **Input vector** (the purple line on the dial) | `state.input` — your stick, directly | always, in every mode |
| **Sub marker + track** on the map | the vehicle's own navigation output | only when the sub reports movement |

The input vector is *your command* and is drawn from the raw stick every frame, so
the dial answers immediately whether or not anything is fitted to the hull. The sub
marker is *the vehicle*, and it moves on the sub's output or it does not move at all.

The client integrator that advances the marker from commanded throttle is now
**SIM-only, and only with no vehicle linked at all**. Running it against a real hull
draws progress the sub may not be making: a dead thruster, a snagged tether, or a sub
held against a wall would all keep the marker sliding forward exactly as if the dive
were going fine. Underwater that is the one failure you cannot afford to hide — so a
linked sub with no navigation **holds position**, and the **`NO NAV`** badge on the
radar says why (`NO NAV · NO SENSORS` once the map is the driving view, where the
words fit — the collapsed circle only has ~98 px of chord at that height).

A held marker and a working-but-stationary one look identical, which is why the badge
is not optional. This is also why a Pi with nothing wired is worth flying: it reports
`mock:true`, the map stays honest and still, and the dial still shows you steering.

### The tether is 100 m, and the console plans against it

`CONFIG.tether.lengthM` is the cable you actually have. **TETHER** reads out beside the
dial as the straight-line range from the cable's anchor, and a dashed **reachable
circle** is drawn around that anchor on the map. That circle is the answer to *"is this
a good place to put in, and is this mission doable from here"*.

The anchor is the **live handheld position** when there is a fix (see below) — the
cable is held by whoever holds the handheld — falling back to the frame origin, which
is the launch point by definition, in SIM and before any fix.

Range is taken in **3D**: the cable has to reach down as well as out, so descending
shrinks the circle by `√(L² − depth²)`. At 60 m down a 100 m tether reaches 80 m out.

The two modes deliberately differ:

| | Behaviour | Why |
|---|---|---|
| **SIM** | **clamped** at the limit — the sub cannot go further | A dive the cable can't reach must not look reachable on the bench, or planning against it is theatre. `TETHER END 100 m`. |
| **REAL** | **warning only**, never enforced | The launch point moves — you pay out more cable, walk the bank, the boat drifts. A limit the console enforced would be both wrong and dangerous. `TETHER OVER 137/100 m`. |

Amber from `warnFromM` (80 m), red at or past the limit. The clamp only ever pulls the
sub **back toward** the launch point, never pushes it, so a sub at the end of its cable
can always drive home.

### The dial reads as numbers, and never moves

The dial shows **four numbers, 0–100, one per compass point** — how hard you are
pushing that way. Each shows only its own half of an axis, so "how much am I giving
it forward" is read straight off the top rather than decoded from a signed percentage.
They are dim at rest and lit on the side actually being driven, which keeps the circle
clean. They replace the old `FWD`/`REV` word labels and the separate THROTTLE/STEER
text block, which said the same thing twice.

**BLIND NAV does not move the dial.** Same corner, same size, same side as with a live
feed — only the *map* changes, leaving the circle to fill the screen behind it. Losing
the camera should feel like the picture falling away from behind the instruments, not
like a different application. `#radar` keeps its 200 px box (and its border — the dial
is `inset:0` inside it, so dropping the border shifted the dial by 1 px and grew it by
2); `#map-panel` is `position:fixed`, so it escapes the circle on its own. The dial's
blind-mode backdrop ring is a **`box-shadow` spread, not a border**, precisely so it
adds nothing to the box and the geometry stays identical.

The dial is **not tappable in blind nav** — a stray tap would expand the map, and that
engages ALL STOP on a sub that is being driven.

Those numbers are also how you **tune the simulator against the real vehicle**: run the
same throttle on both, compare distance covered, set `CONFIG.map.subMaxSpeedMs`.

### The handheld's position is live

`watchPosition` keeps the operator's own position current, like any other map app — a
fix taken once on load is wrong the moment you walk the bank looking for somewhere to
put in, which is exactly when the reachable circle matters most.

Two things move, and they are **not** the same thing:

| | What it is | Colour | When it moves |
|---|---|---|---|
| `MAP.me` | **the operator** — where the handheld is now | green / yellow / red, see below | always, automatically |
| sub marker | **the ROV** | purple arrow | only on the sub's own navigation, or when you pinpoint it |
| `MAP.origin` | the datum the sub is dead-reckoned **from** | orange cross | only before a dive |

**The operator dot is colour-coded by where its position actually came from.** The
tether range is measured *from* that dot, so how much to trust it has to be readable at
a glance rather than looked up:

| Colour | `meSource()` | Meaning | Tether tag |
|---|---|---|---|
| 🟢 green | `live` | a fresh fix from the handheld | — |
| 🟡 yellow | `stale` | last known fix, older than `meStaleMs` (30 s) | `LAST KNOWN` |
| 🔴 red | `mock` | placed by hand — planning, not a measurement | `PLANNED` |

A mocked dot also gets a **dashed ring**, so the state survives a colour-blind operator
and a sunlit screen.

**Planning from somewhere you are not standing.** *"Could I reach that culvert if I put
in from the far bank?"* is a question about a launch point you are not on. The **⚑**
button arms a tap that moves your dot there and turns it red; pressing it again returns
to the live fix (`NEPTUNE.mockMe()` / `mockMeAt(lat,lon)` / `clearMock()`). Pre-dive it
takes the launch point with it, because that is the thing being planned. Real fixes keep
arriving underneath and are recorded in `MAP.meReal` — they just do not overwrite the
mock or drag the launch point back — so clearing lands you on something current.

This is deliberately allowed on a real link as well as in SIM: the Pi is usually
connected during bench planning, so forbidding it would make the feature useless exactly
when it is wanted. Safety comes from the state being *unmistakable* rather than from
prohibition — red dot, dashed ring, `PLANNED` on the range.

The origin follows the handheld until a dive is under way — you are still choosing a
departure point. After that the datum **freezes**: moving it would shift every
coordinate already plotted, so the sub would appear to jump sideways and the recorded
track would be a lie.

"Under way" is `diveUnderway()`, and it is **`track.length > 1`, not `> 0`**. `pushTrack`
records a point the moment an origin exists and then dedupes anything within 0.25 m, so
a stationary sub holds at exactly *one* point however long it sits there. Testing for
`> 0` therefore meant "the map has been running", which silently disabled the whole
follow-the-operator behaviour about a second after boot. More than one point means the
sub actually moved — the only definition that does not depend on remembering to press
start. When the datum does move, `rebaseFrame()` shifts the sub **and every plotted
point** by the same delta, so nothing changes position in the world.

When the origin does follow, the sub is **re-based into the new frame**, not zeroed.
The operator moved; the ROV did not. Zeroing dragged the sub along with whoever was
holding the handheld, which is exactly backwards — the growing gap between the two
*is* the tether, and that gap is what the range readout measures.

The **tether anchors on the operator**, not the datum, because the cable is held by
whoever holds the handheld. Walk 20 m up the bank and the reachable circle walks with
you and the range updates.

**Pinpointing the ROV.** The operator's position is known; the sub's is not — there is
no GNSS underwater and, until the IMU is wired, nothing on board can say where it
drifted to. So the default assumption is the only honest one available: the ROV is
where the operator was when the launch point was set. That is a guess, and the operator
is the one who can correct it by eye. The **◎** button on the map (or `NEPTUNE.setRov()`,
or `NEPTUNE.setRovAt(lat,lon)`) arms a tap that places the sub. It moves **only** the
sub — the datum stays put, so the track and every earlier coordinate stay valid.

A placement **further from the operator than the cable is long is refused**, with the
range and the reason. The tether is a hard physical limit, so such a position cannot
exist; silently clamping it would invent a location nobody chose. Refusing says which
of the two points is actually wrong — nearly always the operator's own, since the ROV
is the one they can see. Move your position first (or plan from one, below) and the
same spot is then accepted.

### Old paths stay, but journeys are never joined

Traces accumulate across planning runs, which is the point — you can see everywhere
you have tried. But two disjoint journeys must never be drawn as one stroke: a straight
line between them reads as the sub having travelled it, which it never did.

`breakTrack()` marks the next point as the start of a new segment, and the drawing code
starts a fresh subpath there instead of joining. Breaks are inserted when a plan starts
or ends and when the ROV is placed by hand — every jump that is not travel. They are
preserved through **both** thinning passes (display decimation and the stored-track cap);
a decimation that drops a break silently re-joins the journeys it exists to separate.

The **eye button** hides and shows the traces — after a few runs the map fills up, which
is exactly what makes them worth keeping *and* worth hiding. It only affects the history:
the sub marker, origin, operator dot and tether ring are always drawn, and hiding
discards nothing.

There is deliberately **no accuracy halo**. It was a large translucent disc sitting on
top of the imagery, and the imagery is the point: underwater structures and obstacles
have to be readable. The orange tether ring is the only circle on this map.

Guards: it never prompts (the watch only starts once permission is already granted, and
picks it up live via `permissions.onchange` if you grant it later); movement under
`meMinMoveM` (3 m) is treated as jitter; the origin is never rewritten faster than
`meMinGapMs`; a fix that is less accurate than the stored one is never adopted; and
beyond `originMoveM` you are somewhere else entirely, which still gets the explicit
**USE MY POSITION / KEEP** prompt.

> **On the ROG Ally there is no GNSS, and on a sealed tether there is no fix at all.**
> The handheld has no GPS receiver, so the browser locates it by sending nearby Wi-Fi
> networks to Google's location service — which needs **internet**. With the tether
> offline there is nothing to ask, so no amount of granting permissions will produce a
> position; `POSITION_UNAVAILABLE` is the correct answer, not a fault. The failure text
> says exactly that when `STATUS.internet` is false, rather than sending you hunting for
> a satellite that was never there.
>
> `NEPTUNE.geoCheck()` answers "why is there no position" in one call — secure context,
> permission API, whether the watch is running, internet, and the last genuine fix.
> **Tapping the map is the accurate route anyway**: ±8 m against ±50 m from Wi-Fi.

### Depth reads as twelve even bands, with a key

The original ramp swept one hue into another at matching lightness, so about four steps
were separable and the rest read as "some sort of green". Depth is quantised into
**12 bands generated in OKLCH**, running **orange at the surface → purple at the bottom**.
Evenly spaced HSL is *not* evenly spaced to the eye (equal hue steps crawl through the
yellows and sprint through the blues), which is what left a wide flat teal at the deep end
when the ramp was hand-picked. Oklch lightness and hue are perceptually uniform, so equal
numeric steps really do look equal: measured in Oklab the 11 steps span 0.048–0.072, a
ratio of 1.49 across a **258° hue sweep** — the extra travel is what buys separable
neighbours, and both ends are now colours with names rather than shades of the same thing.
Lightness still falls the whole way, so it reads as depth and not merely as a rainbow.

Twelve, not twenty — twenty was an arbitrary number and past the point where the eye holds
the steps apart. A dozen leaves each band clearly its own colour and keeps the key short.

The expanded and blind views draw a compact vertical **depth scale**
(`drawDepthLegend`), surface at the top, `maxDepthColorM+` at the bottom — the `+` because
the deepest band is a clamp that catches everything below it. It hides with the tracks,
since it exists only to explain them.

### Moving the map

Both full-screen views (expanded and BLIND NAV) can be **dragged with a finger**. Where
imagery is drawn the pan is computed absolutely from the drag-start centre so it tracks
the finger exactly; with no basemap it falls back to the metre frame, so a drag still
moves the map instead of doing nothing.

The **right stick pans too** whenever the map is the view. Nothing on the hull moves with
that stick yet (camera pan/tilt is an unwired `TODO(hardware)`), and a full-screen map you
can only move by reaching across the screen is awkward on a handheld. While the map has
the stick the camera is deliberately **not** commanded — otherwise leaving the map would
hand back a camera pointed somewhere the operator never chose. Close the map and the stick
returns to the camera on the next frame.

**Which axes are the right stick is detected, not assumed.** The left stick is axes 0/1
everywhere, which is why driving always worked. The right stick is only axes **2/3** under
the Gamepad API's *standard* mapping, where triggers are buttons. A pad reporting a
non-standard mapping commonly puts the triggers in the axis list instead —
`[LX, LY, LT, RX, RY, RT]` — and then axis 2 is a trigger and the stick is **3/4**. Reading
2/3 on such a pad gives a dead horizontal axis and a vertical one wired to the stick's X,
which presents exactly as *"it only pans up and down, sideways does nothing"*.

`rightStickAxes()` picks 3/4 for a non-standard pad with 6+ axes and 2/3 otherwise, logs
which it chose, and is overridden by `CONFIG.rightStickAxes = {x:3, y:4}`.
**`NEPTUNE.axes()`** prints the live values of every axis, so finding the right pair on an
unknown pad takes one glance.

### Zoom: maximum imagery, and the paddles

The map **opens on the sharpest imagery the provider has** rather than a fixed
metres-per-pixel. Mercator resolution is latitude-dependent, so the scale that lands on
the deepest tile zoom is computed once a view centre exists (`maxZoomScale`) — at 51.5°N,
z19 is 0.186 m/px against the old fixed 0.600. It is a one-shot pin: after that the
operator owns the zoom and nothing moves it under them. Tile selection also **rounds up**
now (`preferSharpTiles`), so a coarse tile is never upscaled when a finer one exists.

> Which imagery you get is the provider's choice, not ours. Esri World Imagery is a
> single curated, largely cloud-free mosaic — there is no way for a client to ask for
> "a sunny day" or a particular capture date. Maximum resolution is what *is* selectable,
> and that is what this does. Downloading an offline area caches whatever that mosaic
> currently holds for the bbox.

The **ROG Ally paddles zoom the map**: `F10` in, `F9` out (`CONFIG.map.zoomKeys`), and
they zoom whichever view is on screen — the radar has its own scale, so zooming the big
map from the collapsed view would look like a dead control.

They fire on **release**, not press, and only if the other paddle was never touched
during the hold. Both paddles held together is SURFACE; zooming on keydown would mean
every attempt at that combo started by zooming the map, and an emergency has to feel
like one deliberate gesture.

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

## Reduced GPU rendering (default ON)

The handheld froze repeatedly with `0x133 DPC_WATCHDOG_VIOLATION`. The crash dump names it:

```
Failure.Bucket : 0x133_ISR_amdkmdag!unknown_function
amdkmdag.sys   : 32.0.23027.3001      (AMD Radeon kernel display driver)
```

The AMD display driver overruns in its interrupt handler. **Nothing in this client can repair
that** - the fix is an AMD driver update or rollback - but it is a load-triggered fault, and
this dashboard was asking for a lot: two permanently visible full-size
`backdrop-filter: blur(16px)` surfaces (the instrument bar and the control rail) composited
over a live H.264 video every frame, plus a full-width scan line animating forever above it.

Continuous blur over video is the most expensive thing a page can ask of a compositor, and on
a dark theme it is almost invisible - raising panel opacity looks the same and costs nothing
per frame. `CONFIG.ui.reduceGpu` (default `true`) drops the blurs, the scan line and the
full-viewport gradient. Set it `false` to get the glass back on hardware that can take it.

There were **no `4101` display-timeout events at all**, which is why "GPU" went unconfirmed
for so long: it never TDR'd, it went straight to a bugcheck - so raising `TdrDelay` was never
going to help.

`launch/crash-diagnostics.ps1` now runs the dump analysis itself, so the next bugcheck is one
command rather than an afternoon.

## LOGS - reading the log without leaving the dive

A fault underwater has to be diagnosed while the vehicle is still in the water, and the
operator cannot leave the console to open a file mid-session. So **CONFIG -> LOGS** opens a
live view: centred, deliberately **not** full screen, over a dimmed-but-visible background,
because the point is to read the log while still seeing what the vehicle is doing behind it.

- **tails** by default; scrolling up pauses the tail so a line you are reading does not slide
  away, and scrolling back to the bottom resumes it
- **filter** box and **ALL / WARN+ / ERR** chips
- colour-coded by level, timestamped to the millisecond
- the footer says where the complete file is - the overlay's scrollback is the in-memory ring
  and is bounded; `navigation_logs/logs/{mode}_{iso}.log` is not

### Everything crosses the log

`js/wire.js` wraps `fetch` and `WebSocket` once, at load, so every request, response, socket
frame, close code and failure is recorded with its outcome and duration - without each call
site having to remember. Relying on callers to log is how half of it turns out to be missing
exactly where something went wrong.

A `4xx`/`5xx` is logged as a **warning**, not a success: `fetch` resolves for those, which is
how failed requests get mistaken for working ones. Aborts are distinguished from faults,
because an abort is a deadline we set on purpose.

High-frequency categories (control frames at 20 Hz) are **coalesced**, not dropped: the
suppressed count rides on the next line as `(+N more)`, and a burst that stops gets its tail
swept out within a second as `(+N more, then quiet)`. Without that, "telemetry was flowing and
then it wasn't" is exactly the case that vanishes.

The two endpoints that carry the log are deliberately not logged, or the bus feeds itself.

Console: `NEPTUNE.logs()` / `NEPTUNE.closeLogs()` / `NEPTUNE.ring()`.

## Everything a session produces

```
client/navigation_logs/
  images/   {mode}_{iso}.png   PIC stills (.jpg for the composite fallback)
  videos/   {mode}_{iso}.mp4   screen recordings
  logs/     {mode}_{iso}.log   the session log, NDJSON, written as it happens
```

One naming scheme for all three: **`{mode}_{iso}`**, where mode is what the console was
actually doing (`sim` / `real` / `stale`). A folder therefore sorts by time *and* still
says which files were real dives. ISO colons are stripped — Windows will not have them in
a filename, and that is the kind of thing that only fails once there is real data.

The launcher creates a **Neptune Recordings** desktop shortcut pointing at that folder, on
the same run that creates the Neptune one. Files this deep in an install tree are otherwise
unfindable.

### Screen recording

REC drives **two recorders**: the camera's own card, and the handheld's screen. Either can
be unavailable — no camera on the bench, no ffmpeg on a fresh machine — and neither absence
stops the other. The toast reports each separately.

The screen half is ffmpeg, run by the launcher:

```
ffmpeg -f gdigrab -framerate 30 -i desktop -an        -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -movflags +faststart out.mp4
```

Same trade as re-encoding a Mac screen recording with `-vcodec h264` afterwards — done once,
live, instead. `-an` because there is nothing to hear and audio only costs bytes. Measured
at ~**1.4 MB/min** on a mostly-static screen (more with live video in frame).

Stopping sends `q` to ffmpeg's stdin rather than killing it, so the moov atom gets written —
a hard-killed MP4 is unplayable, the same class of loss as the camera's unsegmented `.MOV`.

`h264_amf` (the AMD GPU encoder) would be lighter on CPU and is deliberately **not** the
default: this handheld has an unresolved kernel fault under sustained GPU load
(`DPC_WATCHDOG_VIOLATION` / `VIDEO_ENGINE_TIMEOUT_DETECTED`), and a recorder that can take
the machine down mid-dive is worse than one that uses more CPU.

ffmpeg is installed by `tether-setup.ps1` (winget). Without it, stills and logs are
unaffected and only recording is unavailable — the launcher says so at startup.

### The session log writes itself

There is no EXPORT LOG button any more. A log you have to remember to save is a log you find
missing exactly when you needed it — the same reasoning that made dive logging automatic. It
starts with the session, appends to disk **every 5 s as it happens**, and ends when the
console does.

Flushed on a timer rather than at shutdown on purpose: this handheld has a kernel fault that
takes the whole machine down with no unload event, so a log held in memory until exit is lost
precisely in the sessions worth reading. `NEPTUNE.sessionLog()` reports where it is going and
how much has landed.

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

**PIC takes a REAL screenshot.** A page cannot screenshot itself: a canvas composite
only ever knows about the video and the map, never the top bar, the control rail or the
banners around them. So PIC asks the **launcher** — which already serves this page from
localhost — for a genuine screen capture, the same thing PrintScreen does. It arrives
same-origin, so it does not taint the canvas, which means the satellite basemap survives
too. `GET /__screenshot` (loopback-only listener; nothing off the machine can ask).

**The launcher writes the file too.** Chrome permits exactly ONE automatic download per
origin and then blocks the rest — it had already recorded `automatic_downloads: 2` (block)
for `http://localhost:8080`, so only the first PIC of a session ever reached the disk, with
no visible prompt in an `--app` window. PIC now sends the filename with the request
(`/__screenshot?save=<id>`) and the launcher writes the PNG to **Downloads** itself, taking
the browser out of the path entirely. The composite fallbacks still use `<a download>`,
which is why `tether-setup.ps1` also sets `AutomaticDownloadsAllowedForUrls`.

That capture is left **unmodified** — no caption. The top bar is already in frame and the
filename carries the timestamp; a strip along the bottom would cover the control rail.

Everything below is the **fallback**, used when the page is not being served by the
launcher (from the Pi, a plain static server, or a test harness) or the capture fails. It
composites what it can reach and *does* get a caption, because there the surrounding UI is
genuinely absent from the image.

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

Console: `NEPTUNE.mark('note')`, `NEPTUNE.sessionLog()`, `NEPTUNE.REC`.

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

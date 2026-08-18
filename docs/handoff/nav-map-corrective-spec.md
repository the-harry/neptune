# Corrective spec: fix the navigation/map implementation

You previously implemented the navigation and map subsystem for the ROV dashboard. The
underlying plumbing is broadly right, but **four things are wrong or missing**, and the
layout priority is inverted. This document describes only what to change. Do not rewrite
working code.

---

## 1. Current state (what the running app actually looks like)

Observed on the topside display:

- **The map panel fills the entire viewport.** It renders a synthetic blue grid only —
  there is no basemap, no waterway geometry, no tiles of any kind. Just a grid, a centred
  position marker with a heading arrow, and a `10 m` scale bar.
- **The camera feed is a small box in the top-right corner**, showing
  `NO FEED — NO BACKEND (OPEN WITH ?HOST=…)`.
- The top status strip (PRESSURE, BALLAST, HDG, CAM BATT, SD, RES, WB, EV, SHOTS, MODE,
  PACK, LINK, CPU, RAM, DISK) renders correctly; camera-sourced fields show `--` because
  the backend is unreachable.
- The right rail (record, PIC, SURFACE, LIGHTS, BALLAST, zoom, CAM, CONFIG) renders correctly.
- The bottom-left heading/throttle instrument (THROTTLE, STEER) renders correctly.
- **There is no UI anywhere to set the origin.**
- **There is no UI anywhere to search, download, load, or select an offline map area.**

The instrument chrome is good. The problem is the map itself, its priority relative to the
video, and the entire bootstrap flow being absent.

---

## 2. Fix 1 — Invert the layout priority

**The camera feed is the primary view. It is what the operator pilots on.** The map is
situational awareness and is secondary at all times.

### Default state (must be the state on load)

- **Camera feed fills the viewport**, full-bleed, behind all instrument overlays.
- **Map is a mini-map in a corner** — bottom-right is suggested, but it must not collide with
  the existing bottom-left heading/throttle instrument or the right rail. Roughly 280×200 px.
- All existing overlays (top status strip, right rail, heading/throttle instrument) keep their
  current positions and sizes.

### Expanded state

- **Clicking the mini-map expands the map to fill the viewport.**
- **The camera feed and the mini-map swap roles** — the video shrinks into the corner panel and
  keeps playing. The feed must never be hidden entirely, in any state.
- Collapse via: clicking the now-mini video panel, an explicit close control, or `Esc`.
- The transition is animated. **Call `map.resize()` on `transitionend`, not at transition
  start**, or the map renders half-grey.

### Rules that hold in both states

- All instrument overlays stay on top and stay in the same screen positions. Expanding the map
  must not move, resize, or hide the status strip, right rail, or heading/throttle instrument.
- The map never captures gamepad or keyboard input used for piloting. `keyboard: false` on the
  map instance; map handlers scoped to the map container only.
- The video element is never unmounted or re-created on transition — reparent or resize it.
  Tearing down the WebRTC peer connection to move the video is not acceptable; it costs seconds
  of reconnect.

---

## 3. Fix 2 — Render the real basemap (in BOTH states)

**The mini-map currently shows no map. This is the most visible defect.** A blue grid is not a
map. Both the mini-map and the expanded map must render the actual basemap, from the same single
map instance.

- One MapLibre GL instance, reparented or resized between mini and expanded containers. Not two
  instances, not a static image for the mini state.
- Basemap source is a local PMTiles archive served by nginx from `/areas/{name}.pmtiles`, with
  Range requests enabled.
- The waterway centreline GeoJSON renders as a layer above the basemap.
- The grid may remain as a *fallback* when no area is loaded — but see §6, it must be labelled
  as such, not presented as a map.
- The position marker, heading arrow, and dive track render as layers on top of the basemap in
  both states. The mini-map is a real, live, scaled-down map — same layers, fewer labels, no
  interaction beyond the click-to-expand.

Verify by loading an area and confirming streets/water render in the 280×200 corner panel before
touching anything else.

---

## 4. Fix 3 — Origin acquisition (currently absent)

Nothing in the app lets the operator set the launch point. Every metre of the dead-reckoned track
is relative to this, so it is a first-class flow, not a config field.

Build **three input paths**, all writing the same atomic record:

**4.1 Phone GNSS (primary).** The topside handheld has no GNSS receiver — it resolves position by
WiFi SSID lookup, which needs internet and is accurate to tens or hundreds of metres. The
operator's phone has real GNSS. Serve a minimal capture page the phone opens; it POSTs to
`/api/origin`:

```js
navigator.geolocation.getCurrentPosition(p => {
  fetch('/api/origin', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      lat: p.coords.latitude,
      lon: p.coords.longitude,
      accuracy: p.coords.accuracy,
      source: 'phone'
    })
  });
}, showError, {enableHighAccuracy: true, timeout: 15000});
```

`navigator.geolocation` **requires a secure context** — plain HTTP is blocked on both phone and
handheld. Serve this over HTTPS with a self-signed cert trusted on both devices, or it fails
silently.

**4.2 Tap the map (fallback, and often better).** The operator is standing at the launch point
with the basemap loaded. Clicking their position works with zero signal and is frequently more
accurate than WiFi positioning, because they can see which bank they are on. Implement as a
first-class option, not a degraded mode.

**4.3 Manual lat/lon entry.** Paste coordinates read off any device.

**In all three cases:**

- Capture `heading0` from the sub's IMU in the same action, while it floats on the surface
  pointed in the launch direction. `lat0`, `lon0`, `accuracy`, `heading0`, and `source` are one
  atomic record.
- **Display `accuracy` prominently.** It is the radius in metres the fix is good to, and it is
  the floor on the whole track's accuracy. Under canopy or against a cutting it can exceed 20 m
  while the device still shows a confident dot. Refuse to arm above a configurable threshold
  (default 15 m) without explicit override.
- Provide **origin adjustment after the fact** — translate and rotate a stored track. When the
  operator surfaces and the trail runs 30 m through a field, they drag it back onto the water.
  Store the transform alongside the raw log; never mutate raw samples.

Surface the origin state in the top status strip: a `ORIGIN` field showing `SET (±8 m)` or
`NOT SET` in warning colour.

---

## 5. Fix 4 — Offline area management (currently absent)

There is no way to search for, download, load, or select a map region. This is the bootstrap
flow and it must exist before the map can render anything.

### API

```
GET    /api/areas                  → [{name, bbox, maxzoom, size_bytes, has_centreline, active}]
POST   /api/areas                  → {name, bbox, maxzoom} — queued job, progress over WS
DELETE /api/areas/{name}
POST   /api/areas/{name}/activate
GET    /areas/{name}.pmtiles       → served by nginx, Range-enabled
GET    /areas/{name}.geojson       → waterway centreline
```

### Download job

Runs **on the Pi**, not in the browser — the Pi has the storage and the browser has no internet
at dive time. Shell out to `go-pmtiles`, which extracts a bbox from a hosted world build without
downloading the whole archive:

```
pmtiles extract https://build.protomaps.com/<date>.pmtiles <name>.pmtiles \
  --bbox=<minlon>,<minlat>,<maxlon>,<maxlat> --maxzoom=17
```

Each extra zoom level roughly doubles file size — cap `--maxzoom` at 16–17; a canal stretch lands
around 10–20 MB. Show estimated size before starting, enforce a configurable cap, and stream
progress over the existing WebSocket. Fetch the matching waterway centreline via Overpass in the
same job:

```
[out:json];
way["waterway"~"canal|river"](<minlat>,<minlon>,<maxlat>,<maxlon>);
(._;>;);
out geom;
```

Do **not** build a `{z}/{x}/{y}` tile scraper against OSM's servers. Bulk downloading them
violates their tile usage policy, and PMTiles is a single file per area rather than tens of
thousands.

### UI

Reachable from `CONFIG` in the right rail, and from the map's empty state:

- **List of downloaded areas** — name, size, zoom range, whether a centreline is present, which
  is active. Select to activate. Delete.
- **Draw-a-rectangle** on the map to define a bbox for download. Build this first — in practice
  it is used almost every time, because the operator wants "this 2 km of canal", not a whole city.
- **Nominatim place search** as a convenience (1 req/s, real User-Agent). Bootstrap-phase only:
  disable and grey it out when offline rather than letting it hang.

---

## 6. Fix 5 — Honest empty states

The current full-screen grid is what "no area loaded and no origin set" looks like, but it is
presented as though it were a working map. That is why the missing bootstrap flow was not
obvious. Every unsatisfied precondition must be visible and actionable.

- **No area loaded** → map panel shows the grid *with* an overlaid message and a button:
  `NO MAP AREA LOADED — LOAD OR DOWNLOAD`, opening the area manager from §5.
- **No origin set** → `ORIGIN NOT SET` with a button opening the origin flow from §4. The
  position marker must not be drawn at a fake centre when there is no origin — currently it is
  centred as though position were known.
- **No centreline for the active area** → note that snapping is unavailable, and disable it
  rather than silently not snapping.
- **Backend unreachable** → the existing `NO FEED / NO BACKEND (OPEN WITH ?HOST=…)` message is
  fine, but requiring a query parameter for backend discovery is poor. Default to same-origin,
  fall back to a configured host in settings, and keep `?host=` as an override.

The rule: the operator must never be looking at a plausible-seeming display that is not actually
tracking anything.

---

## 7. Do not change

These work and are not in scope:

- Top status strip fields, layout, and styling.
- Right rail: record, PIC, SURFACE, LIGHTS sliders, BALLAST slider, zoom controls, CAM, CONFIG.
- Bottom-left heading/throttle instrument (THROTTLE / STEER dial and scale bar).
- The neon-on-blue visual style throughout.
- Camera control API integration, telemetry WebSocket, dead-reckoning maths.

The heading/throttle instrument keeps its own data path from the telemetry store. A map render
failure, a missing PMTiles archive, or a snapping error must leave thrust, steering, and video
fully functional — wrap the map in an error boundary that degrades to a blank panel with all
instruments intact, never a blank screen.

---

## 8. Acceptance criteria

Test each explicitly. Several of these failed in the current build.

1. On load with no configuration, the **camera feed fills the viewport** and the map is a corner
   mini-panel. Not the reverse.
2. With an area loaded, the **mini-map renders actual basemap geometry** — streets and water
   visible in the 280×200 panel, not a bare grid.
3. Clicking the mini-map expands the map to fullscreen; the **video shrinks to the corner and
   keeps playing** without a WebRTC reconnect.
4. `Esc` and the close control both collapse back to camera-primary.
5. The map is fully rendered after expanding — no grey half-render (verifies `resize()` on
   `transitionend`).
6. Gamepad thrust and steering work identically with the map collapsed, expanded, and focused.
7. With no area loaded, the map shows an actionable `LOAD OR DOWNLOAD` empty state.
8. With no origin set, no position marker is drawn and an actionable `ORIGIN NOT SET` state shows.
9. A bbox can be drawn, downloaded, listed, activated, and rendered — end to end, from the UI.
10. Origin can be set by all three paths, and `accuracy` is displayed in the status strip.
11. Pulling the WAN cable after bootstrap leaves everything except Nominatim search functional.

Fix in this order: §3 basemap rendering, then §2 layout inversion, then §5 area management, then
§4 origin, then §6 empty states.

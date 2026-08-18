# Build spec: ROV navigation & map subsystem

Companion to the camera control/streaming spec. This covers position estimation, the offline
map, and the dive-track display — **added to an existing dashboard without disturbing the
piloting HUD that already works.**

Read §2 (Non-negotiables) before designing anything. Several obvious approaches to this
problem are wrong for physical reasons, and the spec calls them out explicitly.

---

## 1. Existing system

A Raspberry Pi inside an RC submarine runs:

- **go2rtc** on `:1984` — WebRTC video from an action camera, no transcode.
- **A control API** on `:8000` — REST + WebSocket telemetry, wrapping the camera's CGI.
- **nginx** on `:80` — serves the dashboard SPA, reverse-proxies both.

Topside is an **ASUS ROG Ally** handheld, connected to the Pi over a wired tether
(`eth0`). The Ally runs the dashboard in a browser and pilots the sub via gamepad.

The dashboard already has a working **thrust and steering HUD overlay** — a corner
instrument showing commanded thrust and current heading. **This must survive the changes in
this spec unmodified in behaviour.** See §7.

---

## 2. Non-negotiables

These are physical constraints, not preferences. Do not design around them.

**2.1 GPS does not work underwater.** L-band is absorbed within centimetres. There is no
satellite fix once submerged, at any depth, ever.

**2.2 Do not double-integrate accelerometer data for position.** This is the single most
common wrong answer. Position error grows with the square of time and is dominated by gravity
leaking through attitude error: a 1° tilt error produces sin(1°) × 9.81 = 0.17 m/s² of phantom
horizontal acceleration, which integrates to roughly **300 m of error in 60 seconds**. Even a
well-calibrated 0.01 m/s² bias gives ~18 m in 60 s. No filter fixes this without an external
position fix, which we do not have. Inertial-only navigation requires fibre-optic gyros costing
five figures.

**2.3 Position comes from heading + a speed model, integrated once.** Error is then *linear*
in distance travelled, roughly 5–15%. That is the accuracy target. Do not promise better.

**2.4 Depth is measured, never estimated.** A pressure sensor gives unambiguous depth. It is
the one cheap, genuinely accurate underwater measurement available. Never derive depth from
integrated vertical acceleration.

**2.5 The ROG Ally has no GNSS receiver and no magnetometer.** Windows Location Service
resolves position by WiFi SSID lookup — needs internet, accurate to tens or hundreds of metres,
and returns nothing on a canal bank with no APs. It cannot supply the origin fix. See §4.

---

## 3. Operating phases

The system runs in two distinct phases. **Make the transition explicit in the UI and enforced
in code.**

### 3.1 Bootstrap (internet available)

Everything requiring the outside world happens here, once, before deployment:

- Download/extract the PMTiles basemap for the operating area.
- Fetch OSM waterway centreline geometry for the same bbox.
- Acquire the origin fix from the operator's phone.
- Set the system clock.
- Any package installation.

### 3.2 Isolated (no internet, no WAN)

The Pi and the Ally operate on the tether network alone. This is an **isolated segment**, not
a DMZ — use that term in code and docs, so nobody later writes firewall rules for a
semi-trusted exposed subnet that does not exist here.

**Failure modes that appear only in this phase.** Each has caused a mysterious stall in
systems like this; handle all four:

| Hazard | Mitigation |
|---|---|
| **Clock drift** — no NTP, Pi has no RTC. Corrupts dive log timestamps and gets written into the camera via `TimeSettings`. | Fit a DS3231 RTC on I²C (~£3). Also set the clock from the Ally during bootstrap as a fallback. |
| **DNS** — any hostname lookup against an unreachable resolver blocks 5–15 s before failing. | Literal IPs everywhere. `/etc/hosts` entries for anything named. Resolver timeout of 1 s. |
| **CDN assets** — every `unpkg`/`cdnjs` script tag is a hard failure offline. | Vendor all JS into the bundle. **Including fonts** — vector basemaps fetch glyph ranges separately, and missing glyphs render a map with no labels rather than throwing an error. |
| **Geocoding** — Nominatim is bootstrap-only. | Disable and grey out the search control when offline. Never let it hang. |

**Acceptance test:** run the entire flow with the WAN physically unplugged from cold boot.
Anything that works only because a cache is warm will surface immediately.

---

## 4. Origin acquisition

Every metre of the estimated track is relative to the origin. A bad origin offsets the whole
dive, so treat this as a first-class operation, not a config field.

### 4.1 Source: the operator's phone

The phone has real GNSS. The Ally does not. The phone talks to the Ally (hotspot or same LAN);
the Ally forwards the fix to the Pi. The phone does not need to reach the Pi directly.

Serve a minimal origin-capture page:

```js
navigator.geolocation.getCurrentPosition(p => {
  fetch('/api/origin', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      lat: p.coords.latitude,
      lon: p.coords.longitude,
      accuracy: p.coords.accuracy
    })
  });
}, err => showError(err), {enableHighAccuracy: true, timeout: 15000});
```

**`navigator.geolocation` requires a secure context.** Plain HTTP is blocked in Chrome and
Firefox on both the phone and the Ally. Generate a self-signed cert for the Pi and trust it on
both devices, or the capture page will silently fail.

### 4.2 Always store and display `accuracy`

It is the radius in metres the phone believes the fix is good to. Under tree canopy or against
a canal cutting it can exceed 20 m while the phone still shows a confident dot. **This number
is the floor on the entire track's accuracy.** Show it, and refuse to arm if it exceeds a
configurable threshold (default 15 m) without an explicit override.

### 4.3 Fallback: tap the map

The operator is standing at the launch point with the basemap already loaded. Clicking their
position is free, works with zero signal, and is often *more* accurate than WiFi positioning
because they can see which bank they are on. Implement this as a first-class alternative, not
a degraded mode.

### 4.4 Initial heading

The Ally has no magnetometer. Capture `heading0` from the sub's IMU while it floats on the
surface pointed in the launch direction. **`lat0`, `lon0`, `accuracy`, and `heading0` are
captured in one atomic "set origin" action** and stored with the dive.

### 4.5 Origin adjustment after the fact

Provide a control to translate and rotate a stored track. When the operator surfaces and sees
the trail running 30 m through a field, they must be able to drag it back onto the waterway
rather than lose the dive. Store the adjustment as a transform alongside the raw log — never
mutate the raw samples.

---

## 5. Position estimation

### 5.1 Sensors

| Sensor | Part | Purpose | Approx cost |
|---|---|---|---|
| IMU | BNO085 | Fused heading (yaw) + attitude | £20 |
| Pressure | MS5837-30BA | Depth, measured directly | £25 |
| Tether encoder | Any rotary encoder on the spool | Payout length → range bound | £10 |

### 5.2 Dead reckoning, 10 Hz

```python
v = SPEED_LUT[throttle_setting]          # m/s, from calibration — see 5.3
x += v * math.cos(heading) * dt          # metres east of origin
y += v * math.sin(heading) * dt          # metres north of origin
depth = pressure_sensor.read()           # measured, never integrated
```

Convert to lat/lon with a flat-earth approximation — exact enough at pond and canal scale:

```python
R = 6378137
lat = lat0 + (y / R) * 180/math.pi
lon = lon0 + (x / (R * math.cos(math.radians(lat0)))) * 180/math.pi
```

### 5.3 Speed calibration

Build `SPEED_LUT` empirically and ship a routine for it: measure 20 m along the bank, run the
sub at each throttle step, time the traverse. **This is the single largest accuracy win
available** — half an hour of work, and it converts a guess into a model. Store the table per
hull configuration.

### 5.4 Current compensation

In a river the sub moves relative to *water* while the track is relative to *ground*. A 0.5 m/s
flow against a 1 m/s sub halves real ground speed. Accept a constant flow vector (direction +
magnitude) entered at launch and apply it as an offset. Crude, but far better than ignoring it.

### 5.5 Tether payout as a bound

Payout length bounds how far the sub can possibly be from the launch point. When the dead
reckoning estimate exceeds that radius, clamp it and flag low confidence. Slack in the line
means payout is an *upper* bound, never a position — do not treat it as a fix.

### 5.6 Magnetometer hazard

The BNO085's magnetometer reads garbage near brushless thrusters and steel ballast. Mount it as
far from both as the hull allows. Ship a **hard/soft-iron calibration routine performed in the
water**, not on the bench, and surface the IMU's own calibration status in the HUD — a heading
that has quietly gone bad is indistinguishable from a working one until the track is obviously
wrong.

### 5.7 Waterway snapping

A canal is effectively one-dimensional. Projecting the estimate onto the OSM centreline
collapses the problem to *distance along the waterway* and eliminates cross-track error for
free.

- Do the projection **on the Pi** (Shapely: `line.interpolate(line.project(point))`) so the
  CLI and the SPA agree. Turf.js `nearestPointOnLine` is the client-side equivalent if needed.
- Render the **raw estimate faintly and the snapped position solidly**. Divergence between the
  two is the drift indicator — when they separate badly, it is time to surface and re-fix.
- Snapping applies to canals and rivers only. On open water, disable it and display honest
  drift.

---

## 6. Map subsystem

### 6.1 Basemap: PMTiles, not a tile scraper

PMTiles is a single-file tiled archive read via HTTP Range requests, fetching only the tiles
needed. One file per area — no `{z}/{x}/{y}` trees of tens of thousands of files, and no
violating the OSM tile usage policy by bulk-downloading their servers.

Extract a bbox from a hosted world build without downloading the whole thing:

```
pmtiles extract https://build.protomaps.com/<date>.pmtiles canal.pmtiles \
  --bbox=<minlon>,<minlat>,<maxlon>,<maxlat> --maxzoom=17
```

Each additional zoom level roughly doubles file size. Cap `--maxzoom` at 16–17; a canal stretch
lands around 10–20 MB.

**The extract runs on the Pi, not in the browser** — the Pi has the storage, and the browser
has no internet at dive time.

### 6.2 Renderer

Use **MapLibre GL JS**. The vector `protomaps-leaflet` library is in maintenance mode and
Protomaps recommend MapLibre for new projects. Given that a bathymetry raster and sonar
overlays are planned, MapLibre is the better substrate — decide now rather than porting later.

If Leaflet is retained for other reasons, `leafletRasterLayer` from the `pmtiles` package works,
but state the tradeoff explicitly.

### 6.3 Waterway centreline

Fetch once during bootstrap via Overpass, store as GeoJSON:

```
[out:json];
way["waterway"~"canal|river"](<minlat>,<minlon>,<maxlat>,<maxlon>);
(._;>;);
out geom;
```

Used twice: drawn on the map, and as the snapping target in §5.7.

### 6.4 Offline area management

SPA sends bbox + maxzoom → Pi runs `pmtiles extract` → writes `areas/<name>.pmtiles` alongside
`areas/<name>.geojson` → SPA lists what is on disk.

```
GET    /api/areas                  → list: name, bbox, maxzoom, size, has_centreline
POST   /api/areas                  → {name, bbox, maxzoom} — queued, progress over WS
DELETE /api/areas/{name}
POST   /api/areas/{name}/activate  → sets the active area for the session
GET    /areas/{name}.pmtiles       → served by nginx, Range-enabled
```

Area selection is bootstrap-time. Show estimated size before download, enforce a configurable
cap, and stream progress — an extract is slow enough to need feedback.

**Search:** Nominatim for place lookup (1 req/s, real User-Agent), plus a draw-rectangle
control. In practice the rectangle is used almost every time — the operator wants "this 2 km of
canal", not "Birmingham". Build the rectangle first; treat search as a convenience.

### 6.5 Layer stack

Define this now so the planned sonar work is a new consumer, not a schema migration:

```
basemap (PMTiles)
  └ waterway centreline (GeoJSON)
      └ bathymetry raster          ← echo sounder, later
          └ track history          ← one polyline per past dive
              └ live track         ← current dive, depth-coloured
                  └ position marker ← heading-rotated
```

Colour the live track by depth: a readable dive profile for free.

### 6.6 Mini-map ↔ fullscreen

**One map instance, not two.** Two means two tile caches, two sets of layer state, and drift
between them. Put one map in a container that transitions between corner and fullscreen.

**Call `invalidateSize()` / `map.resize()` after the CSS transition ends**, not when it starts —
otherwise you get a half-rendered grey map:

```js
panel.addEventListener('transitionend', () => map.resize());
```

Track state lives in one store consumed by both the map and the HUD, so path history survives
the resize with no extra work.

---

## 7. HUD overlay preservation — the hard requirement

The existing thrust and steering overlay is the piloting instrument. **The map is secondary to
it in every conflict.** Explicit rules:

**7.1 The HUD renders above the map at all times**, including fullscreen. Fullscreen map means
the map fills the viewport *behind* the HUD, never that the HUD is dismissed.

**7.2 The map must never capture piloting input.** This is the highest-risk item in the spec.
On a handheld, gamepad and keyboard events used for thrust and steering must not be intercepted
by map pan/zoom handlers. Scope map keyboard handlers to the map container only, disable
MapLibre's keyboard navigation outright (`keyboard: false`), and verify that gamepad polling is
unaffected by map focus.

**7.3 Pointer events.** HUD elements are interactive and sit over a pannable map. Set
`pointer-events: none` on non-interactive HUD chrome so it does not block map gestures, and
`pointer-events: auto` only on actual controls. Do not let a transparent HUD backdrop swallow
drags.

**7.4 The HUD keeps its own data path.** It reads heading and thrust from the telemetry store
directly. A map render failure, a missing PMTiles archive, or a snapping error must leave
thrust and steering fully functional. Wrap the map in an error boundary that degrades to a blank
panel with the HUD intact — never a blank screen.

**7.5 Rendering budget.** The map must not starve the video or the HUD. Throttle map redraws to
10 Hz, decouple them from the telemetry rate, and cap the live track polyline (decimate older
points rather than growing unbounded). Verify WebRTC frame rate is unchanged with the map open
in fullscreen.

**7.6 Rename in code.** The existing corner instrument is a *heading indicator*, not sonar.
Rename before actual sonar is added — the confusion is cheap to prevent now and expensive later.

---

## 8. Dive log format

One GeoJSON LineString per dive. Per-coordinate properties carry the rest:

```json
{
  "type": "Feature",
  "properties": {
    "dive_id": "...",
    "started_at": "2026-08-03T16:41:24Z",
    "origin": {"lat": 0.0, "lon": 0.0, "accuracy_m": 8, "heading_deg": 0, "source": "phone|map_tap"},
    "adjustment": {"dx_m": 0, "dy_m": 0, "rotation_deg": 0},
    "speed_lut_id": "...",
    "flow_vector": {"bearing_deg": 0, "speed_ms": 0}
  },
  "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
  "samples": [{"t": 0.0, "depth_m": 0.0, "heading_deg": 0, "throttle": 0, "snapped": true, "confidence": 1.0}]
}
```

**Log from day one, before any echo sounder exists.** Sub depth alone builds a picture of where
the sub can go, and the same log becomes a real bathymetry raster the moment a sounder is
fitted — no format change.

---

## 9. Readiness check

A "go isolated" action that runs a checklist and reports pass/fail per item. Far better than
discovering a missing archive on the water.

1. Basemap present for the active area, and the area covers the intended launch point.
2. Waterway centreline cached (or snapping explicitly disabled for open water).
3. Origin set, with `accuracy` within threshold.
4. `heading0` captured, IMU calibration status good.
5. System clock sane (RTC present, or set during bootstrap).
6. Speed LUT loaded for the current hull config.
7. Camera pre-flight green (existing check from the camera spec).
8. Video stream healthy.
9. Tether encoder zeroed at launch.

---

## 10. Deliverables

1. Navigation service on the Pi: sensor ingest, dead reckoning, snapping, dive logging.
   REST + WebSocket, sharing the existing telemetry socket.
2. Area management API and the `pmtiles extract` job runner with progress reporting.
3. SPA map component: single instance, mini ↔ fullscreen, the §6.5 layer stack, offline area
   manager, origin capture and adjustment UI.
4. Origin capture page for the phone, served over HTTPS.
5. Speed calibration routine and magnetometer calibration routine, both usable from the CLI.
6. Readiness check, in API and UI.
7. **A simulator** producing synthetic IMU, depth, throttle, and encoder streams along a
   scripted path — including drift, magnetometer disturbance near thrusters, and current — so
   the whole navigation stack is testable without water.

Build the simulator first, then dead reckoning against it, then the map, then the offline area
manager. **Do not touch the HUD overlay until §7 has explicit tests.**

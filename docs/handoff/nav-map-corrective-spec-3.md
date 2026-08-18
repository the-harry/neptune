# Corrective spec 3: satellite basemap, origin on load, simplified area download

The radar instrument is now correct in shape — circular, compact, heading needle, `N` indicator,
`FWD`/`REV`, scale bar, `THROTTLE`/`STEER` alongside. Keep that.

Five changes follow. **Start with §1 — it is the root cause of two separate reported bugs.**

---

## 1. Serve the SPA over HTTP. Stop opening it from the filesystem.

The browser address bar reads `.../sub/client/index.html`. The app is being loaded from a
`file://` origin, and that single fact causes both of these reported symptoms:

- **Geolocation never prompts.** `navigator.geolocation` requires a secure context. `file://` is
  not one, so the API is unavailable and the permission dialog never appears. This is not a bug
  in the location code.
- **"Backend unreachable"** in the Map Areas dialog. A `file://` origin cannot fetch
  `http://<pi>:8000` — it is a cross-origin request from an opaque origin, and it is blocked
  before it leaves the browser.

**Required:**

- Serve the SPA from nginx on the Pi. The dashboard is opened at `http://<pi>/` or
  `https://<pi>/`, never as a file path.
- **HTTPS with a self-signed certificate**, trusted once on the topside handheld. `localhost` is
  the only insecure-origin exemption and does not apply to a remote Pi. Without this, geolocation
  stays unavailable no matter what else is built.
- Backend base URL defaults to **same origin**. Keep `?host=` as an override only. Remove it as
  the primary discovery mechanism.
- Add a startup self-check that detects a `file://` origin and renders a blocking message
  explaining how to launch correctly, rather than degrading into confusing partial failures.

---

## 2. Request location on page load, from the handheld

The origin fix comes from the **topside ROG Ally's own browser**, not a separate device.

**Required behaviour:**

- On page load, if no origin is set for the current session, **automatically call
  `navigator.geolocation.getCurrentPosition`** and prompt for permission. Do not wait for the
  operator to open a dialog.
- Use the result to set the origin **and** to centre the map, so the very first render shows
  where the operator actually is.
- Store `lat`, `lon`, `accuracy`, `source: 'device'`, and the timestamp.

```js
navigator.geolocation.getCurrentPosition(
  p => setOrigin({
    lat: p.coords.latitude,
    lon: p.coords.longitude,
    accuracy: p.coords.accuracy,
    source: 'device'
  }),
  err => showOriginFallback(err),
  {enableHighAccuracy: true, timeout: 15000, maximumAge: 0}
);
```

**North comes from the sub, not the handheld.** The Ally has no magnetometer. Capture `heading0`
from the sub's IMU while it floats on the surface pointed in the launch direction, in the same
"set origin" action. The device supplies position; the IMU supplies orientation.

**Accuracy handling.** The Ally has no GNSS receiver — Windows resolves position by WiFi SSID
lookup, so `accuracy` will typically read tens to low hundreds of metres, not the few metres a
phone would report. Therefore:

- Display `accuracy` in the `ORIGIN` status field: `SET (±120 m)`.
- When accuracy exceeds a threshold (default 30 m), show a non-blocking prompt offering
  **tap-the-map to refine** — the operator can see which bank they are standing on, which beats
  WiFi positioning every time.
- Keep manual lat/lon entry and post-hoc origin adjustment as they are.

Auto-request on load, then refine. Do not block startup on a good fix.

---

## 3. Switch the basemap to satellite imagery

**All map views default to satellite.** The current Protomaps PMTiles basemap is *vector* data —
road and water geometry, no photography — so this is a change of data source, not a style
toggle. Remove the vector basemap as the default.

### 3.1 Provider

**Default: Esri World Imagery.**

```
https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
```

Note the `{z}/{y}/{x}` ordering — **y before x**, unlike standard XYZ schemes. Getting this wrong
yields a plausible-looking but scrambled map.

<cite index="18-1">Esri World Imagery provides 1 m or better satellite and aerial imagery in many parts of the world, with 0.3 m resolution across parts of Western Europe.</cite> That is ample for
identifying canal banks, locks, and bridges. <cite index="18-1">Esri permits its use in OSM mapping without restrictions, and attribution is not legally required there</cite> — but display an
`Imagery © Esri` attribution in the expanded map regardless, and treat broader offline caching as
subject to Esri's terms of use.

**Licensed alternative:** MapTiler Satellite, which sells explicit offline tileset downloads if
you want a provider whose terms unambiguously cover cached local storage. Make the provider
configurable so this is a settings change, not a rewrite.

**Do not use Google satellite tiles.** Their terms prohibit this use.

### 3.2 Storage format

Raster tiles, not vector. **MBTiles** (SQLite) is the natural container; raster PMTiles also
works if you prefer to keep one archive format across the project.

Do not attempt to use `pmtiles extract` against `build.protomaps.com` for this — that build
contains vector basemap tiles only and has no imagery in it.

Downloader options for the Pi:

- A small Python job: walk the tile pyramid for the bbox, fetch, insert into MBTiles. ~100 lines,
  no dependencies beyond `requests` and `sqlite3`. Recommended — you control the rate limiting.
- `mbgl-tile-renderer`, which supports Esri World Imagery, Bing, and Mapbox Satellite sources and
  emits MBTiles directly.

Rate-limit the downloader (a few requests per second, retries with backoff) and set a real
User-Agent. A naive parallel fetch of a thousand tiles will get you blocked.

### 3.3 Size budget — smaller than you'd expect

At latitude ~51.5°, a 256 px tile covers:

| Zoom | Tile ground size | Tiles for a 2 km × 1 km area |
|---|---|---|
| z16 | 380 m | 18 |
| z17 | 190 m | 66 |
| z18 | 95 m | 242 |
| z19 | 48 m | 946 |

Roughly **1,270 tiles for z16–z19, about 25 MB** at typical JPEG sizes. Satellite imagery for a
canal stretch is a trivially small download. Default to z16–z18 and offer z19 as a checkbox.

### 3.4 Missing high-zoom tiles

Imagery coverage varies — rural waterways often stop at z18. When a tile 404s, **overzoom from
the highest available level** (MapLibre `maxzoom` on the raster source handles this) rather than
rendering blank. A blurry map is usable; a black hole is not.

### 3.5 Keep the vector waterway overlay

The OSM waterway centreline is still required — it is the snapping target for position
estimation and it draws the channel over imagery where water is dark and low-contrast. Keep the
Overpass fetch in the download job, and render the centreline as a vector layer above the
imagery.

---

## 4. Simplify the area download to navigate-and-select

The current Map Areas dialog asks for a name, a `minlon,minlat,maxlon,maxlat` text field, a zoom
number, and separate `ESTIMATE` and `DOWNLOAD` actions, with a Nominatim search box below.
**That is far too much.** The operator should never type coordinates.

**Replace the whole dialog with an in-map flow:**

1. Operator expands the map and **pans/zooms to the area of interest** — starting from their
   current location, which §2 now provides on load.
2. A **fixed selection rectangle** is overlaid on the viewport, inset from the edges. What is
   inside it is what gets downloaded. It moves with the map; there is nothing to draw or drag.
3. A **live readout** below it updates continuously as the map moves: estimated tile count and
   size, e.g. `~1,270 tiles · ~25 MB`.
4. **One button: `DOWNLOAD THIS AREA`.** Progress streams over the existing WebSocket.
5. The area is **auto-named** from reverse geocoding when online, or from coordinates when not.
   The operator can rename it later; they should not be asked up front.

Remove: the bbox text field, the separate estimate button, the name field, and the zoom number
input (replace with a simple `Detail: Standard / High` toggle mapping to z18 / z19).

**Downloaded areas** become a simple list — name, size, a thumbnail of the imagery, active state,
delete. Tap to activate.

Keep Nominatim search, but move it into the expanded map as a search field that pans the map.
Disable it when offline. It is a convenience for finding a spot, not part of the download flow.

---

## 5. Radar must show the trajectory

The radar currently renders the grid, heading needle, and centre marker but **no track**. The
trail is the point of the instrument — it is how the operator sees where they have been in low
visibility.

- Render the dive track as a polyline inside the radar circle, in both collapsed and expanded
  states, from the same track store.
- **Decimate for display** — full resolution stays in the log; the radar draws a downsampled
  polyline. Do not grow it unbounded.
- Fade the trail with age, or colour it by depth, so recent movement is visually distinct.
- Clip it to the circle along with the map.

### Readability over imagery

Satellite imagery is visually busy and dark, and the current neon-on-dark styling will disappear
against it:

- Apply a **dark tint or desaturation layer** between the imagery and the HUD elements — roughly
  40–50% dark overlay in the radar, lighter in the expanded view.
- Give the track polyline a **contrasting casing** (dark outline under a bright core) so it reads
  over both water and vegetation.
- The cyan ring, needle, and grid stay above the tint at full brightness.

Also fix: the `N` indicator currently overlaps the `FWD` label at the top of the ring. Offset one
of them.

---

## 6. Acceptance criteria

1. The app is served over HTTPS from the Pi; opening it as `file://` shows a blocking explanatory
   message.
2. Backend resolves same-origin with no `?host=` parameter, and the Map Areas list populates.
3. On first load with no origin, the browser prompts for location automatically and the map
   centres on the operator's position.
4. `ORIGIN` in the status strip shows the fix with accuracy in metres.
5. Above the accuracy threshold, a tap-to-refine prompt appears and works.
6. All map views render **satellite imagery** by default — radar and expanded.
7. Esri tiles render correctly oriented (verifies `{z}/{y}/{x}` ordering).
8. Missing high-zoom tiles overzoom rather than render blank.
9. The waterway centreline draws over the imagery and snapping still functions.
10. Area download requires **zero typed coordinates**: pan, read the live size, press one button.
11. Live tile count and size update as the map is panned and zoomed.
12. The radar shows the dive trajectory, clipped to the circle, legible over imagery.
13. `N` and `FWD` no longer overlap.
14. Video frame rate is unchanged with satellite imagery rendering in the radar.

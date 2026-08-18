# Corrective spec 4: make the map work, then make it simple

Three screenshots show the same underlying failure: **there is no map.** Every view renders a
blue grid. Because of that, every feature built on top of it is unusable — "pan the map to select
an area", "drop it on the map", "refine your fix" all require a map the operator cannot see.

Fix §1 first. Several of the reported usability problems disappear on their own once there is a
visible map.

---

## 1. The root cause: online display and offline download were conflated

The current design requires downloading an area before any map can be shown. But downloading an
area requires panning a map to choose it. That circular dependency is why the grid is all anyone
has ever seen.

**Separate the two concepts completely:**

| Concept | When | Source | Required? |
|---|---|---|---|
| **Map display** | Always | Live tiles from the provider when online; local archive when offline | Never requires a download |
| **Offline area** | Bootstrap only | Saved copy of a region | Only needed for the isolated phase |

**Required behaviour:**

- **When internet is available, the map streams satellite tiles directly from the provider.** No
  download, no area selection, no setup. The map just works, immediately, on first load.
- When an offline archive covers the current view, prefer it. Otherwise fall back to live tiles.
- Only when both are unavailable does the grid appear — and then with an explicit message:
  `NO IMAGERY — OFFLINE AND NO SAVED AREA`, not a silent grid pretending to be a map.

```js
sources: {
  sat: {
    type: 'raster',
    tiles: [ isOffline ? '/areas/{area}/{z}/{x}/{y}.jpg'
                       : 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}' ],
    tileSize: 256,
    maxzoom: 19
  }
}
```

Remember Esri's ordering is `{z}/{y}/{x}` — **y before x**.

**The map must never depend on the backend to render.** Tiles go from browser to provider
directly. `backend unreachable` should degrade telemetry, not blank the map.

---

## 2. Fix the backend reachability

`MAP AREAS` still reports `backend unreachable` while parts of the status strip show values. Resolve
this properly:

- Serve the SPA from nginx on the Pi over HTTPS. Backend base URL defaults to **same origin**.
- Health-check endpoint polled on an interval, with the result shown once in the status strip —
  not repeated as an error inside every dialog.
- **A backend failure must not block map display, address search, or origin setting.** All three
  work client-side. Only the saved-areas list and the download job need the Pi.

---

## 3. One address bar, always on the map

Replace the separate search fields in the `+ AREA` bar and the `WHERE ARE YOU LAUNCHING?` modal
with **one persistent search field, top-left of the expanded map.**

- Placeholder: `Search address or place`
- Type, press enter or pick a suggestion, **the map flies there.** That is all it does.
- Geocode **directly from the browser to Nominatim** — it permits browser requests. Do not proxy
  through the Pi; that is why search currently dies whenever the backend is unreachable.
- Set a real `User-Agent`/`Referer`, debounce to respect 1 req/s, and cache results for the session.
- Disable with `Search needs internet` only when the browser reports genuinely offline
  (`navigator.onLine === false`), not when the Pi is unreachable.
- Typing `nw1` should return London NW1 results. Test with partial UK postcodes specifically.

---

## 4. Origin: one draggable pin, no modal

Delete the `WHERE ARE YOU LAUNCHING?` modal entirely. It offers four competing paths — use my
location, search, drop on map, advanced nudge — and none of them work without a map. Replace with
a single direct interaction.

**The flow:**

1. Operator opens the map (or it is already open on load).
2. A **launch pin** sits at the map centre, or at the device fix if one was obtained.
3. They search an address (§3) or pan freely, then **drag the pin** — or tap anywhere to move it.
4. Live readout under the pin: `51.5234, -0.1467` and, if a device fix exists, `device fix ±120 m`.
5. **One button: `SET LAUNCH POINT`.** Done.

**Rules:**

- Auto-request device location on load per the previous spec, but treat it as **a starting
  suggestion for the pin, not the answer.** The handheld's WiFi-derived fix is tens to hundreds of
  metres out; the operator dragging the pin onto the bank they are standing on will always beat it.
- If the device fix times out or is denied, **do nothing visible except leave the pin at map
  centre.** Delete the `LOCATION UNAVAILABLE / Timeout expired / SET MANUALLY` toast — it announces
  a failure for a path that was never the good one anyway.
- Once set, the pin stays visible on the map as the launch marker, and remains draggable to correct
  the origin later. That replaces the `Advanced — nudge the recorded track` disclosure entirely.
- `heading0` is still captured from the sub's IMU at the moment `SET LAUNCH POINT` is pressed.

---

## 5. Offline areas: one button, no mode

Delete the `MAP AREAS` modal and the `+ AREA` selection mode with its dashed rectangle and bottom
action bar. Both exist only because the map could not be seen.

**Replace with:**

- A single **`SAVE OFFLINE`** button in the map's control bar. It saves **the region currently in
  view** — what you see is what you get. No rectangle to position, no mode to enter or cancel.
- Next to it, live text: `~1,270 tiles · ~25 MB` updating as the map moves, and a `Standard / High`
  detail toggle (z18 / z19).
- Press it, a progress bar appears inline, done. Auto-named by reverse geocoding, renameable later.
- **Saved areas** are a simple dropdown in the same control bar: name, size, delete. Selecting one
  pans the map there. Coverage of saved areas is drawn as a translucent outline on the map so the
  operator can see what they already have.

That is the whole feature: pan to where you'll be diving, press `SAVE OFFLINE`.

---

## 6. Consolidated map UI

Everything lives on the map. No modals at all.

```
┌──────────────────────────────────────────────────────────┐
│ [Search address or place        ] [Areas ▾] [SAVE OFFLINE]│  ← top-left control bar
│                                          ~25 MB · Std/High│
│                                                            │
│                          📍                                │  ← draggable launch pin
│                    51.5234, -0.1467                        │
│                   [ SET LAUNCH POINT ]                     │
│                                                            │
│                                                    [+][−]  │
└──────────────────────────────────────────────────────────┘
```

Also remove: the `Tap to expand the map` tooltip, which currently shows while the map is *already*
expanded.

---

## 7. Acceptance criteria

1. On first load with internet and **no saved areas and no backend**, the map renders live
   satellite imagery. This is the primary test — it currently fails.
2. Imagery renders correctly oriented (verifies `{z}/{y}/{x}`).
3. With the Pi unreachable, the map still renders, address search still works, and the launch pin
   can still be set.
4. Typing `nw1` returns results and the map flies there.
5. The launch pin can be dragged and tapped to reposition, showing live coordinates.
6. `SET LAUNCH POINT` sets the origin in one press from an open map.
7. No modal dialogs remain for map areas or origin.
8. `SAVE OFFLINE` saves the current view with no rectangle to position and no mode to cancel.
9. Saved-area coverage is visible as an outline on the map.
10. Disconnecting the internet with a saved area covering the view keeps imagery rendering from
    the local archive.
11. Disconnecting with no saved area shows an explicit `NO IMAGERY` message, not a bare grid.
12. No `LOCATION UNAVAILABLE` toast appears when the device fix fails; the pin simply sits at map
    centre.
13. The `Tap to expand the map` tooltip does not appear on an expanded map.

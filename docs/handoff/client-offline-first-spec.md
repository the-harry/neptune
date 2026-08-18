# Architectural principle: the client works without the backend

Applies across the whole dashboard, not just the map. Treat this as a standing rule that
overrides convenience in design decisions.

> **The only things that require the Pi are the things that *are* the vehicle.** Everything else
> must work with the Pi switched off, unplugged, or not yet built.

The map failure was one instance of a general bug: features were gated on backend availability
for no reason other than that the backend happened to be where they were implemented. `MAP AREAS`
said `backend unreachable`; address search said `search unavailable (offline)`; neither needed the
Pi at all.

---

## 1. The split

**Works with no backend — client-only, always:**

- Map rendering (live tiles fetched browser → provider)
- Address search and geocoding (browser → Nominatim)
- Launch pin, origin setting, origin adjustment
- Offline tile caching and saved-area management
- Past dive log browsing, track replay, depth profiles
- Client-side blackbox recording
- All settings, preferences, units, layout state
- The entire UI shell — it must render and be navigable with nothing connected

**Genuinely requires the Pi:**

- Live video
- Live telemetry (depth, heading, IMU, power, link)
- Any command to the vehicle (thrust, steer, ballast, lights, record, capture, camera config)
- Pi-side blackbox log and incident bundles
- Mirroring saved areas to the Pi for a second copy

If a feature is not in the second list, **it must not check backend availability at all.**

---

## 2. The client owns its own storage

Do not route client state through the Pi. The browser has everything needed.

| Data | Store | Notes |
|---|---|---|
| App shell (JS, CSS, fonts, icons) | Cache API via Service Worker | Precached at install |
| Satellite tiles | Cache API, separate named cache | This is the offline map archive |
| Origin, settings, layout | IndexedDB | Small, structured |
| Dive logs and tracks | IndexedDB | Ring-buffered by total size |
| Client blackbox | IndexedDB | Per the two-sided logging spec |

### Make it a PWA

Ship a Service Worker with a precached app shell and a web manifest. Once installed on the
handheld, the dashboard launches and runs **with no network of any kind** — no Pi, no internet.
This also permanently solves the `file://` problem, since an installed PWA is served from a real
origin with a secure context, so `navigator.geolocation` and everything else stays available.

Precache everything. No CDN references anywhere — vendor MapLibre, the fonts, the glyph ranges,
and all icons into the bundle. A single `unpkg` script tag makes the app fail to boot offline.

### Offline tiles belong to the client first

`SAVE OFFLINE` writes tiles into the Cache API **from the browser**, not via a Pi job. The
operator can save an area with the Pi powered off, sitting at home the night before.

Mirroring to the Pi is optional and secondary — useful as a second copy and for a Pi-side map
view, but never a precondition. If the mirror fails, the area is still saved and still usable.

---

## 3. Degradation model

Three independent states, tracked and displayed separately, because they fail independently:

```
INTERNET   online | offline      → affects search, live tiles, new area downloads
BACKEND    up | down             → affects telemetry, video, all commands
VEHICLE    armed | idle | fault  → affects what commands are meaningful
```

**Rules:**

- One compact indicator in the status strip. No modals, no toasts, no repeated inline errors.
- Backend down does not disable map, search, pin, logs, or settings. It disables **controls and
  live data only** — those grey out with a single shared reason.
- Internet offline does not disable saved-area maps or anything client-side. It disables search
  and new downloads.
- Never show an error inside a panel for a dependency that panel doesn't need. `MAP AREAS`
  reporting `backend unreachable` is the anti-pattern.
- Reconnection is automatic and silent. No "retry" buttons the operator has to find.

---

## 4. One thing that must NOT be offline-first

**Never queue vehicle commands for later delivery.**

Offline-first architectures tempt you to buffer writes and replay them on reconnect. For a control
system this is dangerous: a `throttle 100%` queued during a link outage and delivered thirty
seconds later, after the operator has given up and put the sub down, is a genuine hazard.

- Commands **fail fast and visibly** when the backend is down. The control is greyed, the press is
  rejected, and the rejection is logged.
- No retry, no queue, no replay — for any command that moves the vehicle, changes ballast, or
  triggers recording.
- **Log uploads are the exception** and may buffer freely: they are inert data, and the buffered
  records are the most valuable ones in the file.

Make this distinction explicit in code — a `Command` type that never queues, separate from a
`Telemetry`/`LogRecord` type that always does.

---

## 5. Acceptance criteria

1. With the Pi powered off and no internet, the installed PWA launches, renders its full UI, shows
   saved-area imagery, and allows browsing past dive logs.
2. With the Pi powered off and internet available, the map renders live imagery, address search
   works, and a launch point can be set and persisted.
3. `SAVE OFFLINE` completes successfully with the Pi powered off.
4. No panel displays a backend-related error unless it requires the backend.
5. Vehicle controls grey out when the backend is down, with one shared reason shown once.
6. A command issued during a backend outage is rejected immediately and never delivered late —
   verify by cutting the link, pressing throttle, restoring the link, and confirming nothing fires.
7. Client blackbox records buffer through the outage and upload on reconnect.
8. Disabling the network in devtools at any point never produces an unhandled rejection or a blank
   screen.

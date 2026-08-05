# Navigation & map subsystem (backend)

Position from **heading + a speed model, integrated once** (never double-integrate
accel — spec §2.2). **Depth is measured, never estimated** (§2.4). Error is *linear*
in distance travelled (~5–15%). Mounts into the same `:8000` FastAPI app.

```
nav/
├── config.py         # tunables (rates, thresholds, dirs, pmtiles), env-overridable
├── models.py         # Pydantic: SensorSample, Origin, NavState, dive log, readiness
├── speedlut.py       # throttle→m/s LUT (§5.3) — the biggest accuracy win, per-hull
├── sim.py            # ★ SIMULATOR (§10.7): scripted path + IMU/depth/throttle/encoder,
│                     #   with heading bias, magnetometer disturbance near thrusters, current
├── geo.py            # flat-earth local↔lat/lon (§5.2)
├── snap.py           # centreline snapping (§5.7) — pure-Python projection (no Shapely dep)
├── deadreckoning.py  # DR (§5.2): integrate once, current comp (§5.4), tether bound (§5.5), snap
├── divelog.py        # GeoJSON dive log (§8) — raw kept, adjustment applied on output only (§4.5)
├── areas.py          # offline area mgmt + pmtiles extract runner (§6.4) — bootstrap-only, graceful offline
├── sensors.py        # sensor source: sim (default) | real (BNO085/MS5837/encoder stub)
├── service.py        # NavService + APIRouter (origin, dive, areas, readiness, /ws/nav)
├── app.py            # standalone app + mount helper
└── cli.py            # sim runner, speed-cal, magnetometer-cal (§10.5)
```

## Two phases (§3)

- **Bootstrap** (internet): extract the PMTiles basemap + OSM waterway centreline, set the
  clock, acquire the origin. `areas.py` runs `pmtiles extract` here.
- **Isolated segment** (no WAN): everything runs on the tether alone. No hostnames — literal
  IPs only. The area extractor reports "unavailable" cleanly instead of hanging.

## API (mounted in the main app)

```
POST /api/origin                      set lat/lon/accuracy/heading0 atomically; 422 if accuracy > threshold (?override=true)
GET  /api/nav/state                   current NavState (raw + snapped + confidence)
POST /api/nav/flow                    constant current vector (§5.4)
POST /api/nav/dive/start | /stop      start / save a dive
GET  /api/nav/dive/current            live dive as GeoJSON
POST /api/nav/dive/current/adjust     translate+rotate the track (output only; raw untouched)
GET  /api/nav/dives                   list saved dives
GET  /api/areas · POST · DELETE · /{name}/activate    offline areas (§6.4)
GET  /api/readiness                   the §9 "go isolated" checklist
WS   /ws/nav                          NavState @ broadcast_hz + area-extract progress
```

## CLI (§10.5)

```
python -m nav.cli sim          # run the simulator through DR, print accuracy, write a dive
python -m nav.cli speed-cal --distance 20 --pairs 0.25:36,0.5:19,0.75:13,1.0:10 --id hullA
python -m nav.cli mag-cal      # guide in-water magnetometer calibration (poll cal status)
python -m nav.cli readiness
```

## Tested (no water needed)

Simulator + DR verified: **DR error 4.8%** of distance in calibrated conditions (linear, not the
100s-of-metres of double-integration), **current compensation exact**, magnetometer disturbance
degrades heading + drops confidence, tether payout clamps range, and **snapping removes
cross-track error**. Service verified: origin gating, dive start/log/stop, GeoJSON output,
adjustment-on-output, readiness checklist, offline area-extractor graceful failure, `/ws/nav`.

## Not done yet (next turn)

- **SPA map component** (§10.3): MapLibre single-instance mini↔fullscreen, the §6.5 layer stack,
  offline area manager UI, origin capture + adjustment UI.
- **Phone origin page over HTTPS** (§4.1) — `getUserMedia`/geolocation need a secure context.
- **§7 HUD-preservation tests FIRST**, then reconcile the `sonar`→`heading indicator` rename (§7.6).

## Pi notes

- Fit a **DS3231 RTC** (I²C) for clock survival with no NTP (§3.2).
- Install the **`pmtiles`** binary during bootstrap for area extraction (`NAV_PMTILES_SRC` =
  the world build URL). Not needed at dive time.
- Wire `RealSensorSource` (BNO085 / MS5837 / spool encoder) — `TODO(hardware)` in `sensors.py`.

## Automatic navigation log (safety)

**Every session is logged, unasked.** A dive nobody remembered to start recording is
exactly the dive you needed afterwards, so `NavService` opens a log the moment an
origin exists — no `POST /api/nav/dive/start` required. Disable only for bench work
with `NAV_AUTOLOG=0`.

**The record is the journal, not the GeoJSON.** Each dive writes two files into
`dives_dir`:

| File | What | When |
|---|---|---|
| `dive-<ts>.jsonl` | append-only, one line per sample | **as it happens** |
| `dive-<ts>.geojson` | finished track, origin-adjusted | on stop |

That split is the point. Previously samples lived only in memory and were written
once, by `stop_dive()` — so a crash, a power cut or a killed process lost the entire
track. The `.jsonl` is flushed per line and `fsync`'d every few seconds, which bounds
the worst case to that window rather than the whole dive.

**Unfinished dives are recovered on the next start.** A `.jsonl` with no matching
`.geojson` means the process died mid-dive, so `_recover_orphans()` rebuilds the
GeoJSON from it and marks `properties.recovered = true`. The parser deliberately
tolerates a truncated final line — a journal from a crash almost always ends
mid-write, and that must not cost the rest of the dive.

Logging never blocks navigation: a full disk or an unwritable path drops the journal
and logs a warning, and the vehicle carries on.

# NEPTUNE Sub API

FastAPI backend for the ROV. Speaks the client's contract directly: a real-time
control **WebSocket**, an **MJPEG** camera feed, and it serves the static client.
One authoritative `RovState`; a single background loop advances it, runs the
safety **watchdog**, and broadcasts telemetry to every connected client.

## Layout

```
api/
├── main.py        # FastAPI app: /ws/control, /stream.mjpg, /healthz, static mount, loop
├── protocol.py    # Pydantic WS message contract (in + out) — source of truth
├── rov.py         # RovState: applies control/commands, watchdog, telemetry
├── hardware.py    # HW abstraction: MockHardware (bench) + RealHardware (GPIO, TODO)
├── camera.py      # Picamera2 MJPEG, with a synthetic bench fallback
├── rov_camera.py  # the vehicle's own camera plane, wired into the ROV loop
├── sysinfo.py     # REAL Pi health from /proc + /sys (no dependencies)
├── config.py      # all tunables (env-overridable)
├── nav/           # the estimator, /ws/nav, dive logs, tiles, calibration (see nav/README.md)
├── camera/        # the WOLFANG control plane: defaults, CGI, files (see camera/README.md)
├── blackbox/      # the vehicle half of the two-sided flight recorder, and `rovlog`
├── tests/         # standard-library unittest + run.py (see "Tests", below)
└── requirements.txt
```

## Run

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py            # or: uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` (server serves the client), or open the client
from disk and point it at the host via `?host=…`.

- **On a laptop** (no Pi hardware/camera): auto-selects `MockHardware` +
  synthetic camera, so telemetry animates and `/stream.mjpg` shows a test
  pattern. `mock: true` rides in telemetry.
- **On the Pi**: install Picamera2 (`sudo apt install -y python3-picamera2`) and
  fill in the `TODO(hardware)` methods in `hardware.py` (pins are mapped at the
  top of `RealHardware`), then flip the `wired` flag in `RealHardware._gpio_available()`.
  Until you do, `RealHardware.__init__` **raises on purpose** so `NEPTUNE_HW=auto`
  falls back to the bench simulator. That is deliberate: a backend that reports
  `mock: false` while every sensor returns a constant presents fabricated zeros
  (`0.0 V`, `heading 0`, "at the surface") as genuine instrument readings, which is
  strictly worse than an honest simulation. Set `NEPTUNE_HW=real` to require real
  hardware and fail loudly instead.

  > **SOFTWARE GAP (2026-08-18):** the vehicle (`docs/hardware.md`) puts all sensing on
  > an ESP32 brainstem over USB serial; the Pi keeps only two DRV8871 pairs
  > (GPIO 23/24, 5/6). `RealHardware`'s pin constants describe the retired bench
  > vehicle — do **not** fill in the `TODO(hardware)` methods against Pi GPIO. The
  > integration work is `RealHardware` as a serial client of the brainstem
  > (`docs/hardware.md` §8; ledger §20).

  **Pi system health is always real**, regardless of the vehicle backend — see below.

## API (matches the client exactly)

- `GET  /stream.mjpg` — multipart MJPEG.
- `WS   /ws/control` — client→server: `control{throttle,steer}`, `camera{pan,tilt}`,
  `ballast{cmd}`, `command{name,value}`, `ping`. server→client: `telemetry{…}`,
  `alarm{name:"leak"}`, `pong`. See `protocol.py`.
- `WS   /ws/nav` — the estimator's own frame: position, track quality, and the
  wrapper keys that say whose hull it is about. See `nav/service.py`.
- `GET  /healthz`, `GET /api/healthz` — status, hardware/camera backend, client count.
  (The `/api/` alias exists so it is reachable through the nginx reverse proxy topside.)
- `GET  /api/system` — **real Pi hardware + network health** (`sysinfo.py`). CPU
  temperature/load/percent/frequency, RAM, swap, disk, uptime, per-interface link state,
  negotiated speed, addresses and RX/TX throughput, Wi-Fi association + signal, systemd
  service states, undervoltage/throttling flags, and camera reachability.

  Zero dependencies — it reads `/proc`, `/sys` and `os.statvfs` directly (psutil was
  dropped). Slow probes (`vcgencmd`, `systemctl`, `iw`) run on a background task and are
  cached, so the endpoint never blocks the control loop.

  **Every probe degrades on its own.** A field that cannot be read is `null`, never `0` —
  the dashboard renders `--`. That distinction matters: `CPU 0 °C` reads as a measurement
  and hides the fault, which is exactly how the old psutil-less path made every gauge look
  plausible and be wrong.

  The compact subset (`cpu_c`, `cpu_pct`, `ram_pct`, `disk_gb`, `uptime_s`,
  `net_tether_up/_mbps`, `net_cam_up/_signal`) also rides on every telemetry frame.
- `GET  /` … — the static client (`html=True`).

## Safety

- **Watchdog** (`NEPTUNE_WATCHDOG_S`, default 0.5s): if control frames stop while
  armed, thrusters are zeroed until control resumes.
- **Disarm/E-STOP** immediately zero thrusters.
- On shutdown the vehicle is safed (disarm, thrusters off, ballast hold).

## Tuning

Everything is in `config.py`, each value overridable by env var (rates, watchdog
window, camera size/fps/quality, pressure model, hardware/camera backend).

---

## Tests

```bash
python api/tests/run.py            # every suite, from the repo root
python api/tests/run.py replay     # one suite (substring match)
python api/tests/run.py --list     # the suites and how many checks each holds
```

Standard-library `unittest`, no pytest — deliberately. The api's dependency list is what
has to be installed on a Raspberry Pi 3B+ over a canal-side hotspot, and a test framework
that must be installed before the tests can run is a test suite that quietly stops being
run.

**The runner is the only thing in this repo entitled to state a total**, and nothing on
this page repeats one. Four different check counts for the client suite were once in
circulation simultaneously, two of them going stale in the very commit that "fixed" them;
the rule that came out of that is written up in
[`client/tests/README.md` → *Where the numbers live*](../client/tests/README.md). Run
`--list` and it will tell you, in well under a second, because it discovers without
running.

Exit status is what to read in a script:

| | Meaning |
|---|---|
| `0` | every check **ran** and passed |
| `1` | a check failed — a **finding**: the code was exercised and came out the wrong shape |
| `2` | nothing failed, but something could not be **run** — a missing dependency |

`2` exists because a suite that never loaded is an *absence* of findings, not a pass. In a
python with no `pydantic`, two suites cannot even be imported; the run reports them as
`DEPS`, counts them separately, names them in the verdict and exits non-zero, instead of
adding them into one reassuring total. `unittest` would otherwise represent an
un-importable module as a single synthetic failing test — arithmetically tidy and
completely wrong.

---

## Every field on the wire, and what the console does with it

**Read this before adding a sensor.** Producing a field is not the same as landing it, and
this repo has repeatedly done the first and called it the second. The two frames below
carry a lot of facts the dashboard never shows — some for good reasons, some because
nobody finished the job — and the difference between those two cases is only knowable if
it is written down.

The field counts are derivable, so ask rather than trust this page:

```bash
cd api && python -c "from protocol import Telemetry; print(len(Telemetry.model_fields))"
cd api && python -c "from nav.models import NavState; print(len(NavState.model_fields))"
```

The *fates* below are not derivable — they are an audit, taken at `1da969f` on
2026-08-07 by grepping `client/index.html`, `js/net.js`, `js/render.js`, `js/core.js`,
`js/map.js` and `js/recorder.js` for every field name. Re-do it the same way when you
change the shape of either frame; it is one `grep` per field and it is the only thing that
tells a deliberate omission from a forgotten one.

### The vocabulary this table uses

| Fate | Meaning |
|---|---|
| **RENDERED** | it reaches a readout, a glyph or an alert chip the operator can see |
| **TOOLTIP ONLY** | it is on screen but only under a hover — invisible to someone in sunlight with wet hands |
| **CONSUMED** | something branches on it, but it is never displayed as itself |
| **INGESTED** | it is parsed into `state`/`MAP` and then read by nothing |
| **IGNORED** | the client does not mention the name at all |
| **DEAD STORE** | a specific, worse kind of INGESTED: a named slot that looks like a feature and is written every tick by nobody's reader |

`INGESTED` and `DEAD STORE` are called out separately from `IGNORED` on purpose. An
ignored field is honest — nothing pretends otherwise. A dead store looks like a working
consumer at every call site *except* the one that would draw it, which is why three of
them survived on `/ws/nav` for months.

### Frame 1 — `protocol.Telemetry`, on `/ws/control`

| # | field | null / cannot-tell means | fate |
|---|---|---|---|
| 1 | `type` | — | **routing** (`net.js handleMessage`) |
| 2 | `seq` | nobody was listening when this frame was recorded | **CONSUMED** — `recorder.js` gap detection into `tlm_rx` blackbox records |
| 3 | `t` | no clock stamp | **IGNORED** — the client times staleness from arrival (`state.realTelAt`), never from a Pi clock it has not synchronised |
| 4 | `armed` | — | **INGESTED** — `renderArmed()` is an explicit no-op |
| 5 | `left` | — | **INGESTED** — `setThrust()` is defined and never called |
| 6 | `right` | — | **INGESTED** |
| 7 | `ballast_level` | **NOT HOMED** — the position is genuinely unknown, never `0.0` = empty | **RENDERED** — `#ballast-pct` + the syringe fill; `?` and a hatched barrel on null |
| 8 | `ballast_target` | — (a command has no unknown state) | **INGESTED** — the target mark is drawn from the client's own `ballastTargetRaw` |
| 9 | `depth` | MS5837 not answering | **RENDERED** — `#depth-val`, `?` + amber + alert chip |
| 10 | `pressure` | MS5837 not answering; null together with `depth`, always | **RENDERED** — `#pressure-val` |
| 11 | `heading` | BNO085 not answering | **RENDERED** — `#heading-val` + radar rotation |
| 12 | `heading_card` | null whenever `heading` is | **IGNORED** — zero client references |
| 13 | `magnet` | — | **INGESTED** — `renderMagnet()` is a no-op (v2 hardware) |
| 14 | `light_green` | — | **RENDERED** — the lamp button |
| 15 | `light_white` | — | **RENDERED** — the lamp button |
| 16 | `light_green_level` | — | **IGNORED** — the gauge shows the client's own commanded level, so the vehicle's echo is never compared against it |
| 17 | `light_white_level` | — | **IGNORED** |
| 18 | `leak` | "not certified dry" — true for WARN, FLOOD *and* UNKNOWN | **CONSUMED** — fallback when `leak_state` is absent; also clears the latch |
| 19 | `leak_state` | cannot-tell is the **value** `UNKNOWN`; there is no null to spend | **RENDERED** — `#leak-icon`, four shapes + alert chip |
| 20 | `battery_v` | INA219 not answering; null together with `current_a` | **RENDERED** — `#battery-v` |
| 21 | `signal` | cannot-tell is the **value** `-1` | **IGNORED** |
| 22 | `link_ms` | no link measurement | **IGNORED** — `#link-ms` shows the client's own pong RTT, which is the number the operator can act on |
| 23 | `ballast_homed` | a definite "never zeroed", not a cannot-tell | **RENDERED** — drives `?`, the HOME button and an alert chip |
| 24 | `ballast_needs_rehome` | — | **RENDERED** — `BALLAST LOST COUNT` chip |
| 25 | `speed_ms` | nothing measured *or* estimated a speed | **RENDERED** — `#speed-val` |
| 26 | `speed_src` | no speed at all; also carries `no-origin` | **RENDERED** — `#speed-src`, EST / NO SPEED / NO DATUM |
| 27 | `snagged` | **three-valued**: null = nav cannot say, `false` = nav looked and says no | **RENDERED** — alert chip, three states |
| 28 | `gyro_only` | **three-valued**; nulled whenever the frame carries no bearing | **RENDERED** — HDG flag |
| 29 | `mag_cal` | NO IMU ANSWERING — not the same as `0`, "a compass answered, badly" | **RENDERED** — HDG flag `MAG?` / `NO COMPASS` |
| 30 | `gyro_z_dps` | no gyro answering; `0.0` is the measurement "not turning" | **diagnostics cluster** — new on the wire this round |
| 31 | `accel_fwd_ms2` | no accelerometer; `0.0` is the measurement "coasting" | **diagnostics cluster** |
| 32 | `pitch_deg` | no attitude source; `0.0` is the measurement "level" | **diagnostics cluster** |
| 33 | `roll_deg` | no attitude source; `0.0` is the measurement "level" | **diagnostics cluster** |
| 34 | `current_a` | no current sense; null together with `battery_v` | **diagnostics cluster** — previously TOOLTIP ONLY, spent inside the pack hover as `"— drawing 3.1 A"` |
| 35 | `leak_probe_fault` | both probes look sane | **RENDERED** — alert chip |
| 36 | `sensor_faults` | empty is **not** a certificate of health, it is "nothing currently named" | **RENDERED** — names the chip on every cannot-tell, plus a `NOT ANSWERING` chip |
| 37 | `cpu_c` | probe unavailable | **INGESTED** — `#cpu-c` is fed by `/api/system`, which survives the control link going down |
| 38 | `cpu_pct` | probe unavailable | **IGNORED** — not even ingested |
| 39 | `ram_pct` | probe unavailable | **INGESTED** |
| 40 | `disk_gb` | probe unavailable | **INGESTED** |
| 41 | `uptime_s` | probe unavailable | **IGNORED** |
| 42 | `net_tether_up` | probe unavailable | **IGNORED** — `#net-eth` comes from `/api/system` |
| 43 | `net_tether_mbps` | probe unavailable | **IGNORED** |
| 44 | `net_cam_up` | probe unavailable | **IGNORED** |
| 45 | `net_cam_signal` | probe unavailable | **IGNORED** |
| 46 | `mock` | — | **CONSUMED** — `vehicleHasSensors()`; drives the whole SIM presentation |

Audited fates, 2026-08-07, counted off the rows above (17 + 5 + 3 + 8 + 12 + 1 = 46):
rendered 17 · diagnostics cluster 5 (rows 30–34, landing this round) · consumed without a
readout 3 (`seq`, `leak`, `mock`) · ingested and read by nothing 8 · ignored entirely 12 ·
routing 1. **Twenty of the forty-six reach no readout at all** — which is not by itself a
defect, and the rest of this section is about which twenty are decisions.

The five cluster rows are the only ones whose fate is stated ahead of the audit rather than
out of it. They are guaranteed by `client/tests/suites/instrument-cluster.js`, which is
written against the **behaviour** and not the element names — each reading has to be
findable, has to render its value, has to render a real zero *as* zero, has to render a
null as `?` and never as the stale `--`, and has to explain itself in a sentence to both
the eye and a screen reader. A missing readout fails its own named check and takes its five
behaviour checks down with it, each reported as NOT RUN, so it cannot leave a green run
behind it. If you are reading this and one of them is not on screen, that suite is red and
this row is the specification, not the record.

**The Pi-health block (37–45) is duplicated on purpose and that is fine.** Those nine
fields also arrive on `GET /api/system`, which is a separate poll on a separate socket, so
CPU/RAM/disk and both interfaces stay visible while the *vehicle* link is down — which is
exactly when someone wants to know whether the Pi is alive. Carrying them in telemetry too
costs nothing and puts them in the dive log beside the readings they explain. Do not
"fix" this by wiring the telemetry copies into the same tiles: two writers on one readout
is how a stale value gets to overwrite a fresh one.

**16–17, 21–22, 42–45 are the ones a reviewer should be suspicious of.** Each is a fact
the vehicle knows and the console shows from somewhere else, so a disagreement between the
two is currently invisible. The light-level echoes are the clearest case: the gauge shows
what was *commanded*, so a lamp driver that clamps, fails or comes up at half brightness
looks perfect on screen. None of these is a safety signal, which is why none has been
promoted — but none of them is a deliberate *omission* either, and this row is where that
gets recorded rather than rediscovered.

### Frame 2 — `/ws/nav`

The frame is `{"type":"nav", **NavState.model_dump(), "simulated":…, "reads_vehicle":…}`,
with `gyro_only` forced to `null` when `heading_deg` is null (`NavService.nav_frame`).
Client fate is in `map.js`'s `connectNavWs` handler.

| # | key | null / cannot-tell means | fate |
|---|---|---|---|
| 1 | `type` | — | **routing** |
| 2 | `t` | — | **IGNORED** |
| 3 | `lat` | — | **IGNORED** — the map recomputes from `x_m`/`y_m` + its own origin |
| 4 | `lon` | — | **IGNORED** |
| 5 | `depth_m` | nothing measured depth this tick | **RENDERED** — track depth bands, the 3-D tether range, the reachable circle |
| 6 | `heading_deg` | no heading, and the track is **not advancing** | **RENDERED** — radar rotation, `NO BEARING` badge |
| 7 | `x_m` | — | **RENDERED** — sub marker + track |
| 8 | `y_m` | — | **RENDERED** |
| 9 | `raw_lat` | — | **IGNORED** — spec §5.7 wants the un-snapped estimate drawn faint |
| 10 | `raw_lon` | — | **IGNORED** |
| 11 | `snapped` | — | **IGNORED** |
| 12 | `snap_offset_m` | — | **IGNORED** — spec §5.7 calls this "the drift indicator" |
| 13 | `range_m` | — | **DEAD STORE** — `MAP.rangeM`, written every frame, read by nothing |
| 14 | `payout_m` | — | **DEAD STORE** — `MAP.payoutM` |
| 15 | `confidence` | *no null to spend* | **DEAD STORE** — `MAP.confidence` (`map.js:699`), written every frame, zero readers. Topside only: the number does reach the dive journal and `GET /api/nav/state`, so replay can grade a recorded track. It gets to the log and the API and stops there — every individual *cause* has a badge, only the composite is unshown, and that is parked deliberately until real dive logs say how it behaves (`.specs/tasks.md` → Open) |
| 16 | `mag_cal` | no IMU answered; `0` = one did, and says do not trust it | **RENDERED**, behind two gates |
| 17 | `speed_ms` | *no null to spend* | **RENDERED**, behind the gates — the Speed tile |
| 18 | `speed_src` | *no null to spend* | **RENDERED**, behind the gates |
| 19 | `snagged` | *no null to spend* | **RENDERED**, and into `state` behind the gates |
| 20 | `gyro_only` | nulled **on the wire** when there is no bearing | **RENDERED** — HDG flag |
| 21 | `no_heading` | — | **IGNORED** — the client infers "held" from `setMapHeading(null)` |
| 22 | `has_origin` | — | **IGNORED** — `MAP.hasOrigin` comes from `GET /api/origin` |
| 23 | `simulated` *(wrapper)* | — | **CONSUMED as a gate** — stored, never displayed |
| 24 | `reads_vehicle` *(wrapper)* | — | **CONSUMED as a gate** |

Audited fates, 2026-08-07, counted off the rows above (9 + 2 + 3 + 9 + 1 = 24 keys, being
21 model fields plus 3 the wrapper adds): rendered 9 · consumed as a gate only 2 · dead
store 3 · ignored entirely 9 · routing 1.

**The two gates on rows 16–19 are the important thing on this page.** Those four keys
exist on *both* sockets with different types: tri-state `Optional` on `/ws/control`, plain
`bool`/`float` with reassuring defaults on `/ws/nav`. A nav frame writing into `state`
could therefore only ever overwrite a cannot-tell with "nav looked, and everything is
fine". So `map.js` requires `reads_vehicle && !simulated` (is this frame about *this*
hull?) **and** `!vehicleRecent()` (is anything better already speaking?) before any of them
reaches `state`. That is not defensive coding; it is the reason
`test_consumers.TwoSocketsOneVehicleTest` exists.

### The rule this table is here to enforce

**A fact belongs on exactly one socket.** `gyro_z_dps`, `accel_fwd_ms2`, `pitch_deg` and
`roll_deg` are readings taken off the same hardware handle as `heading` and `mag_cal`, so
they ride on `protocol.Telemetry` and are deliberately **not** on `NavState` — a comment
block at the end of `nav/models.py` records why, so a later round cannot re-add them and
recreate a two-socket disagreement. Two reasons beyond that:

1. **They are readings, not estimates.** They arrive with exactly the authority `heading`
   has, and blanking them should mean the same thing blanking `heading` means.
2. **`NavState` does not exist until a dive does.** The estimator is built in
   `start_dive()`, which needs an origin — so a field there would be blanked by the nav
   freshness gate for the whole pre-dive phase of every healthy boot, spelling *"no datum
   yet"* and *"the IMU is dead"* with one null. The freshness gate therefore applies to
   none of the four, by construction.

All four are `Optional` with **no default**, because every plausible default is itself a
measurement: `0.0 deg/s` is "not turning", `0.0 m/s²` is "coasting", `(0.0, 0.0)` is
"level". Each is the calm answer — which is precisely how a dead IMU used to look like a
well-behaved vehicle. Verified against the real chain (`MockHardware` + `RovState` +
`NavService`): on a healthy hull all four carry values; on `kill("bno085")` all **six**
BNO085 fields go null in the same frame with `sensor_faults=['bno085']`, so the blank
always arrives with the chip that caused it. A frame that omits all four still validates
and lands on cannot-tell — an old vehicle talking to a new console.

`current_a` was left exactly as it was. Its wire shape was already right: `Optional[float]`,
null exactly when `battery_v` is null, because it is one chip with `"ina219"` naming it in
`sensor_faults`. Its only defect was topside, and a readout built on it inherits the pack's
cannot-tell behaviour for free and cannot drift from it.

### What the four new readings are for

They are **diagnostic**, not navigational — nothing safety-critical branches on attitude,
and forward acceleration is never integrated twice into a position. What each one is worth
at the bench, what a healthy one looks like, and what a bad one means is in
[`docs/hardware.md` §13](../docs/hardware.md) — including the one that reads a fouled prop
before the operator can feel it.

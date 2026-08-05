# Neptune — Design

How the requirements are met, and **why** each decision is the way it is. Where a choice
looks odd, the reason is usually a failure that has already happened on this hardware.

---

## 1. Shape of the system

```
 ROG Ally (topside)                     Raspberry Pi (on the sub)              WOLFANG cam
 ┌───────────────┐   Ethernet tether   ┌────────────────────────────┐   Wi-Fi  ┌──────────┐
 │ dashboard PWA │◀───────eth0────────▶│ nginx (plain HTTP proxy)   │◀─wlan0──▶│ RTSP/CGI │
 │ served from   │  API · WS · WebRTC  │  ├─ FastAPI  /api  /ws      │  AP      └──────────┘
 │ localhost     │                     │  └─ go2rtc   /go2rtc        │
 └───────────────┘                     └────────────────────────────┘
```

**The Pi is backend-only.** It never serves the dashboard. The client runs topside from
`localhost`, which is a secure origin (so geolocation and the PWA work) and keeps the Pi
light — no static files, no SPA, no TLS.

**Four architectural rules** everything else follows from:

| # | Rule | Consequence |
|---|---|---|
| §1 | The client owns its state | origin, areas, dive logs live in IndexedDB; the Pi is an optional second copy |
| §2 | The client works with the Pi off | offline-first PWA; a missing Pi is never an error |
| §3 | Subsystems fail independently | one status per subsystem, not one "backend up/down" |
| §4 | Vehicle commands never queue | transmission-time rule, not a UI rule |

---

## 2. Topside launcher (`client/launch/`)

### Why not a kiosk
`--kiosk` removes every window control. The Ally has **no physical keyboard**, so "press
Alt+F4 to exit" is not an exit — the only way out was a hard reboot. The default is a
fullscreen **app window**, and the page carries its own EXIT control that stops the local
server (`/__quit`) and closes the window. `-Kiosk` remains as an explicit opt-in.

### Why liveness is a process scan, not a PID
Chromium's process-singleton is keyed on `--user-data-dir`. When a window already exists for
that profile, a newly spawned browser hands its command line to the existing instance and
**exits immediately with code 0**. Treating that PID as a liveness signal made the launcher
conclude "the browser closed" and shut its own web server down ~180 ms after starting — while
the orphaned window stayed on screen rendering the cached shell. Liveness is therefore decided
by scanning for processes using our own profile directory, throttled to ~2.5 s so the scan
cannot stall the accept loop.

### Why the server is concurrent
Browsers open half a dozen sockets at once, including speculative ones that never send a byte.
A single-threaded accept loop with a blocking read served one at a time and stalled on every
silent socket. Requests are handled on a runspace pool; a socket that says nothing is dropped
rather than waited on.

### Why profiles are per browser
Chromium forks are not profile-compatible. The launcher preferred Brave, then Edge, then
Chrome, and each inherited the previous one's directory — leaving Chrome reading a profile
full of `brave_shields` / `edge_wallet` keys, out of which it would not honour its own
geolocation content setting. Profiles are now `browser-<brand>`, and a pre-split shared
directory is retired on sight.

### Cleanup is the invariant
On exit the launcher stops **both** the listener and the browser. An orphaned window is what
arms the next launch to fail, so leaving one behind is the bug, not the symptom.

### Graphics escape hatches
`-SafeGraphics` (software H.264) and `-NoGpu` (no GPU process at all) exist because this
handheld faults at the kernel level under sustained GPU load. They reduce the app's exposure;
they do not fix the driver. See §10.

---

## 3. Degradation model (`client/js/status.js`)

Six states are tracked separately, because on this vehicle they genuinely fail one at a time:

| Subsystem | Down means | Still works |
|---|---|---|
| Internet | no search, no new tile downloads | saved offline areas |
| ROV link | vehicle commands not transmitted | map, radar, areas, dives, config, camera buttons |
| Video | NO FEED on the video panel | everything else |
| Camera control | REC disabled; PIC still saves a topside still | piloting, video, map |
| Nav | no live track | piloting, video, camera |
| Vehicle | armed / idle / fault | — |

Controls declare their dependency in markup (`data-needs="link"`, `"cam"`, …) and only the
owning subsystem gates them. This replaced a single rule —
`body.backend-down aside { pointer-events: none }` — that killed the entire control rail as
one blob whenever the Pi was unreachable.

### Two kinds of "down"
- **`simulated`** — no vehicle link, but there *is* something to simulate. Tinted, **fully
  interactive**. The console must be flyable on the bench.
- **`gated`** — genuinely unavailable, nothing to simulate (camera REC with no camera).
  Disabled, because pretending would be a lie. PIC is deliberately *not* gated: it keeps a
  topside copy that does not need the camera at all.

### Stale vs gone
`STALE` means a brief gap on a **still-open socket** — bounded by `simFallbackMs` and
requiring `wsStatus === 'online'`. Once the socket is actually down, the simulator takes over
and resumes from the last real values. Falling back only when telemetry had *never* arrived
meant that once the Pi had connected even once, losing the link parked the console in `stale`
forever: the model stopped advancing, and every control appeared dead while still accepting
input.

### The vehicle indicator is binary
"No host configured" and "host configured but unreachable" both mean *there is no vehicle* to
the operator. Red for simulated, green for connected; pulsing red reserved for an actual leak.
Splitting them produced a muted grey in exactly the situation the icon exists to warn about.

---

## 4. Commands and simulation (`client/js/commands.js`)

The §4 rule is about **transmission**, not usability. `send()` is already a no-op on a closed
socket and nothing is buffered, so the safety property holds regardless of what the UI allows.
With no live link, `cmd()` applies the command to the local mirror only, logs it as `cmd_sim`,
and transmits nothing. One code path drives both modes.

Light level changes bail out of `cmd()` entirely — they only mutate local state, and the dirty
level pump transmits only on an open socket.

---

## 5. Map, radar and blind navigation (`client/js/map.js`, `tiles.js`)

### One map instance
Collapsed it lives clipped inside the radar circle; expanded it fills the viewport; the video
is reparented by CSS and never unmounted.

### Three views, three intents

| View | Purpose | Heading | Throttle |
|---|---|---|---|
| Radar (collapsed) | glance instrument | heading-up | live |
| Expanded | **planning** | north-up | **all-stop** |
| Blind nav | **driving** | heading-up | live |

Blind navigation is deliberately **not** `expandMap()`. Expanding engages an all-stop — a
submarine cannot be paused, so an open planning map means throttle zero. Blind nav keeps
`MAP.expanded === false`, so every behaviour already keyed on it (heading-up, follow-the-sub,
live throttle, no all-stop) stays in its piloting form for free. Only the layout changes.

Entry and exit are debounced both ways: a WebRTC hiccup throwing the operator between views
mid-manoeuvre is worse than either view alone. On a **cold start** the debounce is shorter
(`blindColdMs`) — there is no established feed to blip, so the full window would only park a
useless NO FEED on screen.

**There is no full-screen NO FEED state.** Blind nav is the fallback in every mode. The X is
hidden (there is nothing to close) and tapping the video tile no longer exits — both used to
land the operator on a black rectangle carrying strictly less information than the map it
replaced. The tile remains as a status indicator, which is how the return of the feed is
noticed, and that return is what restores the camera view. A tap cannot expand the map while
blind, because expanding would zero the throttle.

### Two zooms, on purpose
`MAP.radarScale` (fixed) and `MAP.scale` (adjustable) are separate. A single shared value meant
zooming the big map silently rescaled the radar — and once blind nav gained zoom controls, that
happened constantly. The radar is a glance instrument: it must mean the same thing every time.

Its zoom is also *tight* (`radarMetersPerPixel`). At 0.6 m/px a 200 px circle spans 120 m,
which at the sub's ~1 m/s is two minutes of full throttle per circle-width — measured, 12 s of
driving drew 20 px, which reads as "the trace is not working". Blind nav derives its scale from
the real canvas so it spans `blindSpanM` across the shorter edge, on any display.

### Canvas sizing uses layout, not the rect
`getBoundingClientRect()` includes CSS transforms, and both full-screen layouts animate in from
`scale(.94)`. Measuring mid-animation sized the canvas to 94% of the panel and left it there
(1280 → 1203), so the map's centre sat ~39 px from the dial's — the sub and its track drew at
the canvas centre while the dial's input vector drew at the viewport centre, appearing as **two
parallel offset lines**. `offsetWidth`/`offsetHeight` are the untransformed layout size.

### What the dial actually shows
Three things that legitimately point different ways: fixed **FWD/REV** labels, the **input
vector** (`x = steer`, `y = −throttle`) which is what you are *commanding*, and the **north
indicator**. The plotted track is where you have *been*. Only the first is fixed.

---

## 6. Origin (`client/js/navui.js`)

The origin is the **(0,0) of the local frame** and the sub is dead-reckoned from it. Moving it
mid-dive invalidates the track, so it is fixed *during* a dive and reconsidered *between* them.

- Read from IndexedDB and rendered immediately, so the map works offline and instantly.
- Refreshed on open **only when the permission is already granted**, so opening never produces
  a prompt. Requesting unconditionally re-prompted on every launch, because Chrome does not
  persist the grant unless it came from a real user gesture.
- Adoption is conditional: beyond `originMoveM` it is a different site and gets an explicit
  **USE MY POSITION / KEEP**; within it, a fix is adopted only if it is no less accurate — a
  ±58 m Wi-Fi fix must never overwrite a ±8 m tap.
- The ORIGIN tile turns amber past `originStaleH`, which needs neither permission nor internet
  — and therefore works in the field, where a fresh fix cannot be obtained at all.

**Boot ordering matters.** `STORE.init()` must be awaited before anything reads it; leaving it
fire-and-forget meant the saved origin was invisible and a position was requested on every
single boot.

**Platform reality.** The Ally has no GNSS. Windows geolocation is Wi-Fi triangulation, which
needs internet — so in the field, tap-on-map (±8 m) is both more accurate and the only method
that works. Windows also gates desktop apps behind a *second* location switch that is off by
default; nothing in the page can fix that, so it lives in the setup script.

---

## 7. Video plane (`client/js/video.js`, `deploy/go2rtc.yaml`)

Zero-transcode: `#video=copy` passes the H.264 bitstream through untouched, `#audio=drop`
avoids a known sync-stall on these cameras.

### Generation guarding
Every connection attempt carries a generation number and superseded callbacks are ignored.
Without it a stale `onclose` re-armed the retry timer a fresh attempt had just cleared, and the
two churned peer connections forever — each holding a decoder and a socket. Teardown strips
**all** handlers and releases the `MediaStream`; leaving `srcObject` attached kept a dead decode
pipeline alive across every reconnect.

### The two configuration traps
1. **Cross-origin signaling.** The dashboard is served from the Ally's localhost and signals to
   the Pi, so the WebRTC signaling WebSocket is cross-origin. go2rtc's upgrader enforces
   same-origin by default and rejected every attempt with `request origin not allowed by
   Upgrader.CheckOrigin` — presenting as a permanent NO FEED with a healthy camera and a
   working RTSP pull. `api.origin: "*"` is required for this architecture.
2. **The ICE candidate.** It must be an address the Ally can actually reach. It used to be
   stamped at install time from whatever `eth0` happened to hold, and left as a literal
   placeholder when `eth0` had no IPv4 — the exact tether condition. It is now the fixed tether
   address, and the installer refuses to start a video plane still containing a placeholder.

nginx raises `proxy_read_timeout` on `/go2rtc/`: the signaling socket goes idle once the stream
is up, and the default 60 s close killed video mid-session while the client, seeing a live feed,
did not reconnect.

### Camera defaults (`api/camera/defaults.py`)

The factory state is actively hostile. `PowerSaving=5MIN` powers the camera off mid-dive, and
topside that is **indistinguishable from a tether fault** — which is exactly how it gets
misdiagnosed. `VideoClipTime=OFF` writes one continuous `.MOV`, and a file still being written
when power is cut is unrecoverable, so segmenting is the highest-value setting on the device.

**Nothing is written blind, because a blind write already lied.** `preflight()` reported
`PowerSaving=OFF (critical) OK` for months on a camera that then slept: it wrote `PowerSaving`,
read back a property of *that* name rather than `Camera.Menu.PowerSaving`, got `None`, and the
check `ps == "OFF" or ps is None` scored `None` as a pass. The protocol makes this easy to do —
names are asymmetric (write `Videores`, read `Camera.Menu.VideoRes`), and an **unknown property
name is accepted with `0 OK` and silently ignored**.

So each setting carries candidate write names and candidate values in preference order, every
attempt is verified by re-reading, and the two failure modes are told apart on the wire:

| Response | Means | Do |
|---|---|---|
| `722` | the property parsed, the **value** was refused | keep the name, try the next value |
| `0 OK`, read-back unchanged | the **name** is probably wrong | try the next name |

What worked is cached per `FWversion` in `/var/lib/neptune/camera-caps.json`, so a cold probe
costs a couple of seconds once. Losing the cache costs a re-probe, nothing more.

Settings are tiered (`critical` / `quality` / `hull`) and carry their own reason, so the report
explains itself. `defaults.py` also lists what is **deliberately not set** — `LCDPower`, whose
`OFF` may mean "never blanks" rather than "screen off"; `UpsideDown`, which depends on the
physical mounting; `Timelapse`, whose `5SEC` may be an interval rather than an engaged mode.
That list is load-bearing: without it the next reader assumes they were forgotten.

**Hot vs cold.** `Videores`, `Imageres` and the preview bump stall the camera's single-threaded
server, and RTSP shares it — applying one under way is a second of blind piloting. They are
connect-time only and are skipped while recording.

**The guard loop is also the keepalive.** The 15 s telemetry poll only runs while a dashboard is
subscribed, so with nobody watching there is no CGI traffic at all and an idle timer we failed to
disable has nothing to reset. One menu read every 60 s covers three jobs: keepalive, drift
correction, and detecting a camera that came back — which needs the whole connect sequence again,
because a rebooted camera has a wrong clock (it has no RTC, and burns the clock into the image).

**AWB is the one conditional setting.** Water absorbs red first, so with no lamps the warmest
preset counteracts the blue-green cast, and with the white LEDs on the same preset produces an
orange one. It follows the vehicle's white-light state, reconciled on the next guard tick.

### Stills (`client/js/camera.js`, `store.js`)
PIC takes **two copies**, because the camera's own JPEG lands on an SD card inside a vehicle
that is in the water, and if the camera is flat or absent there is no copy at all. The topside
grab goes to IndexedDB *and* to a file download — the download is the copy that survives the
browser profile being cleared, the IndexedDB copy is the one that survives the download being
blocked, and the toast reports each independently rather than claiming both.

The frame source is the live video when there is one and **the map otherwise**. In blind nav
the map *is* the view, so capturing a black `<video>` would be actively misleading: it would
look like the camera worked. This is also what makes PIC exercisable in sim.

**The map canvas cannot be exported.** Tiles are loaded without `crossOrigin` and cached as
opaque responses so the offline archive works, which taints the canvas — `toBlob` throws
`Tainted canvases may not be exported`. Making the tiles CORS-clean would fix the screenshot
and break the offline map in the field, which is the wrong trade on this vehicle. A capture
therefore re-renders the frame with `noTiles` at the same pixel size and dpr (so the overlays'
projection state still lines up) and records `basemap:false`. The video is a `MediaStream` and
never taints, so the camera path — the one that matters — is untouched.

Two details that are not cosmetic:
- **The local grab happens first.** The camera's capture blocks its single-threaded server for
  ~2 s, so grabbing afterwards would save a frame from well after the moment PIC was pressed.
- **The id carries milliseconds.** It is the IndexedDB key, and at second resolution two
  presses inside the same second silently overwrote each other — losing an image in the
  feature whose entire purpose is not losing images.
- **The caption degrades in a chosen order.** The radar canvas is ~198px wide, where the full
  caption was clipped — taking the `SIM` and `NO BASEMAP` markers with it, so the image no
  longer said what it was. It now shrinks, then shortens the timestamp, then drops from the
  least important end, with `SIM` ranked above the date.

### The store must never hang the boot
`boot()` awaits `STORE.init()`, so a path that does not settle is a console that never starts.
Adding the `stills` store raised the IndexedDB version, and a version bump introduces
`onblocked`: an older connection held open by a second window (or a page left behind by a
previous launch) blocks the upgrade and fires *neither* `onsuccess` nor `onerror`. Every branch
now settles, a timeout backstops the rest, and the connection sets `onversionchange` so this
window is never the one blocking the next upgrade. Losing the database costs persistence, not
the dashboard.

### The top bar sizes to its content
Twenty metric tiles were laid out `flex:1 1 0` — equal columns filling the bar. Each got 48px
whatever it held, `min-width:0` let the box shrink below its own text, and `white-space:nowrap`
then spilled that text over its neighbours: 13 of 20 tiles overflowed with live values and the
bar became unreadable exactly when it had something to say. With `--` in every field it looked
fine, which is why it survived — the failure only appears once the Pi is attached.

Tiles are content-sized now and cannot shrink. Three things were also costing width for no
information: `INCANDESCENT` (106px — introduced by the camera defaults work), `SET ±65m · 3d`
repeating what the tile's own label and colour already said, and a header that ran *under* the
EXIT button. Measured at 1280px: 20 tiles, one row, zero collisions, with the worst-case values
for every field simultaneously. If it ever does run out of width it wraps rather than overlaps.

### Wi-Fi power save on the camera link
`wlan0` *is* the camera. Raspberry Pi OS enables Wi-Fi power management by default, the radio
parks between beacons, and the RTSP pull stalls — topside, identical to the camera sleeping.
Both halves have to be off. NetworkManager owns the persistent setting (`wifi.powersave 2` on
the `neptune-cam` profile); `neptune-wifi.service` re-asserts it every 60 s because the driver
re-enables it **on re-association**, and the AP drops every time the camera reboots.

---

## 8. Navigation (`api/nav/`)

### Sensor source
`NAV_SENSORS` selects where position comes from:

| Value | Source | Follows the operator? |
|---|---|---|
| `vehicle` *(default)* | live ROV — heading from hardware, depth from pressure, speed from thrusters | **yes** |
| `sim` | scripted path with preset heading legs | no — ignores input entirely |
| `real` | unwired IMU/depth/encoder stubs | no — returns zeros |

This defaulted to `sim`, so on a connected vehicle the map traced a canned route and steering
changed nothing — *"I can only go straight"*. `NavService` also had no reference to the ROV, so
it could not have followed the operator even in principle; it now takes a `get_rov` callable.

Speed comes from **actual thruster output** `(left+right)/2`, not commanded throttle. Heading
only changes when the thrusters really run, so sourcing speed from the command while heading
came from the hardware meant a **disarmed** sub advanced across the map without ever turning.
Both now agree: disarmed means neither.

Tether payout has no encoder, so it integrates commanded speed with a 1.2× margin. It is used
only as an **upper bound** on range, so over-estimating loosens the clamp and never invents
precision.

### The dive log is a journal, not a file written at the end
Each dive writes `dive-<ts>.jsonl` **as it happens**, line-buffered with a periodic `fsync`,
plus the finished origin-adjusted `dive-<ts>.geojson` on stop. Previously samples lived in
memory and were written once by `stop_dive()` — so a crash, power cut or killed process lost
the entire track. On this hardware that is the normal case, not the edge case.

Logging starts automatically as soon as an origin exists. A `.jsonl` with no matching
`.geojson` means the process died mid-dive, so it is rebuilt on the next start and marked
`recovered`. The parser tolerates a truncated final line, because a journal from a crash almost
always ends mid-write. **Failing to record must never fail to fly**: an unwritable path drops
the journal with a warning and the vehicle carries on.

---

## 9. Pi health (`api/sysinfo.py`)

Two tiers, because they cost very different amounts:

- **`fast()`** — pure `/proc`, `/sys`, `os.statvfs` reads. Microseconds; safe on the asyncio
  loop every second. CPU temp/load/percent/frequency, memory, swap, disk, uptime, per-interface
  link state, negotiated speed, addresses and RX/TX throughput, Wi-Fi association and signal.
- **`deep()`** — `vcgencmd`, `systemctl`, `iw`, and a TCP reachability probe. Runs on a
  background task and is cached.

**Zero dependencies.** psutil was dropped: it is unnecessary on Linux and a build risk on some
Pi images.

**`None` means unavailable and is preserved end-to-end.** A field that cannot be read renders
as `--`. `CPU 0 °C` reads as a measurement and hides the fault — which is exactly how the old
psutil-less path made every gauge look plausible and be wrong.

Every probe degrades on its own; one missing sensor cannot blank the reading.

### Honest hardware
`RealHardware.__init__` **raises** while the GPIO is unwired, so `NEPTUNE_HW=auto` falls back to
the flagged simulator. Reporting `mock: false` while every sensor returns a constant presented
`0.0 V`, `heading 0`, "at the surface" as genuine instrument readings — strictly worse than an
honest simulation. Logs report the sensor *source* and the *simulated-ness* separately, because
collapsing them into one word is part of why the scripted-path bug survived so long.

---

## 10. The tether, and the machine underneath it

A direct Ally↔Pi cable has **no DHCP server**. Left on automatic, both ends fall back to
`169.254.x.x` link-local and neither can find the other — "no connection with the cable plugged
in". `install.sh` never configured `eth0` at all; it only *read* an address.

The tether is now a fixed point-to-point pair — Pi `192.168.42.1`, Ally `192.168.42.2` —
applied **additively** to DHCP so the Pi still works plugged into a router, with
`ipv4.dhcp-timeout` capped so NetworkManager stops parking the device in "getting IP
configuration" forever. `neptune-tether.service` re-asserts the address as belt-and-braces.
mDNS (`neptune.local`) is a fallback only: Windows resolves it unreliably over a link-local
adapter — measured, one success followed by ten consecutive failures.

Topside also stops power-suspending the USB NIC, which was dropping the link mid-session and
looking exactly like the Pi going away.

### Known open hardware faults
Two faults are **not** software and are not fixed by this codebase:

- **`DPC_WATCHDOG_VIOLATION` (0x133)** on the handheld, with a matching `LiveKernelEvent 141`
  (`VIDEO_ENGINE_TIMEOUT_DETECTED`), repeating with an identical signature. Kernel dumps are
  now enabled and `C:\Windows\MEMORY.DMP` will name the driver. `-NoGpu` reduces exposure;
  raising `TdrDelay` tolerates stalls. Neither is a cure.
- **The USB tether NIC dropping off the bus entirely** (`Present: False`), requiring a physical
  replug.

Both argue the same thing: **the handheld should not be in the safety path.** The vehicle must
be able to safe itself without topside, which is why the watchdog and the on-vehicle journal
matter more than anything topside can promise.

---

## 11. Client persistence and the service worker

`STORE` (IndexedDB) holds origin, settings, the saved-area registry and dive logs. The tile
Cache API holds the offline satellite archive. Everything degrades to a no-op if unavailable —
it must never throw into the app.

The service worker precaches the app shell so the dashboard launches with no network of any
kind. **Vehicle paths are never cached** (`/api`, `/ws`, `/go2rtc`, `/stream`, `/__quit`) so
telemetry, video and commands always hit the real Pi or fail fast — never a stale replay.

Shell assets are **network-first with a cache fallback**. Cache-first pinned the app to whatever
JS was current when the PWA was installed, so fixes could never reach an installed dashboard;
the network here is the local disk and costs nothing. `SHELL` must be bumped on every client
release — it is the only thing that evicts the old shell.

---

## 12. Flight recorder (`client/js/recorder.js`, `api/blackbox/`)

Two-sided: both ends record the same event classes, each in its own monotonic time, so the two
can be differenced afterwards (`rovlog diverge`). Clock correction happens in analysis, never in
the record.

Events go to an IndexedDB ring immediately and upload separately. Upload must survive the very
link failure it is recording, so it never blocks recording, is bandwidth-capped and backs off.
The batch is **sized to the remaining budget**: building a full batch and then rejecting it for
exceeding the cap meant nothing was ever uploaded or deleted while the log reported
backpressure. The ring cap is enforced against a count read from disk at startup, not one that
restarts at zero every page load.

---

## 13. Conventions that bite

- **Line endings are load-bearing.** `set -euo pipefail\r` fails with `pipefail: invalid option
  name`; a systemd `Environment=X=y\r` puts a CR inside the value; PowerShell 5.1 reads `.ps1`
  as ANSI, so a UTF-8 em-dash corrupts the script into a parse error. `.gitattributes` pins LF
  for `*.sh`/`*.service`/`*.conf`/`*.yaml`/`*.py` and CRLF for `*.ps1`/`*.bat`. Launcher scripts
  are kept **pure ASCII**.
- **CSS `inset` is shorthand.** Writing it after `left`/`top` resets them to `auto`.
- **Never hide a parent to hide a child.** `#map-panel` is a child of `#radar`; `opacity: 0` on
  the ring blanked the entire map.
- **Verify in a real browser.** Brace counting does not catch an unescaped apostrophe in a
  string. `--dump-dom` will not render `chrome://policy`, headless denies geolocation outright,
  and fullscreen suppresses permission prompts — each lies in a different direction. Screenshot
  the window and measure `getBoundingClientRect()`.

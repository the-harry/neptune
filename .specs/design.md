# Neptune — Design

How the requirements are met, and **why** each decision is the way it is. Where a choice
looks odd, the reason is usually a failure that has already happened on this hardware.


---

## 0. What this system is for

**The client is not a dashboard. It is the system that commands a set of instruments,
and its mission is to bring the ROV back to the operator — whatever fails.**

Every subsystem below the client is an *instrument*: the camera, the video feed, the
control link, the IMU, the depth sensor, the tether encoder, the map imagery, the
handheld's own GPS. Instruments are not infrastructure. They are fallible by nature and
they **will** fail — in cold water, on a long cable, on a handheld with a display driver
that bugchecks, on a Pi that browns out. Designing as though they will not is how you get
a console that is confident and wrong.

So the client treats every one of them the same way:

1. **Assume it fails.** Not "handle the error" — assume the steady state includes it
   being gone. Losing the camera is not an exception path; it is Tuesday.
2. **Fail alone.** One instrument going dark takes nothing else with it. Losing the ROV
   link must not disable the camera buttons, the map, the logs or the settings.
3. **Say so, in one glance.** Every instrument has exactly one indicator, and the
   indicator changes *shape*, not just colour, so it survives sunlight, a cracked screen
   and an operator who is also driving.
4. **Never invent its reading.** A missing number is `--`, never `0`. A vehicle that
   cannot measure its heading does not get a heading drawn for it. This rule has been
   broken twice in this repo and both times it produced a console that looked healthy
   while hiding the failure it existed to show.
5. **Degrade to the next mechanism, automatically.** No retry buttons, no dialogs, no
   asking. The operator has a sub to fly.

### The fallback chain

The mission is *get the ROV home*. Each step is what remains when the one above it dies:

| When this fails | What takes over |
|---|---|
| Camera video | **BLIND NAV** — the map becomes the full-screen driving view, heading-up, throttle live |
| Map imagery | the metre-frame canvas: grid, track, tether ring, heading — no basemap needed |
| Vehicle navigation | the sub marker **holds position** and says `NO NAV`; the dial still answers the stick |
| The control link | the simulator keeps the console flyable, clearly badged, and the vehicle's own watchdog zeroes the thrusters within `watchdog_timeout_s` |
| The handheld's GPS | tap the map — more accurate than Wi-Fi positioning anyway (±8 m vs ±50 m) |
| The Pi's own dead reckoning | the tether range still bounds where the sub *can* be: a 100 m cable is a 100 m circle |
| Everything topside | **SURFACE** blows ballast on a hold, and the drop-weight is mechanical |

The last row is the point. The chain ends in a mechanism that needs no software at all,
because a fallback chain that terminates in "and then the computer helps" is not a chain.

### What this forbids

- Synthesising a position, heading or depth over a real link. Ever. A simulated track
  drawn over a real dive hides a dead thruster, a snagged tether or a sub pinned against
  a wall — all of which look exactly like normal progress if the map keeps advancing.
- Queuing a vehicle command. A late `throttle 100%` arriving after a reconnect is a
  hazard, not a nicety.
- A single "backend up/down" flag. It is always wrong about something.
- Any control that needs the Pi to *decide* whether it is usable.

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

**A page cannot screenshot itself.** A canvas composite only ever knows about the video and
the map — never the instrument bar, the control rail or the banners the operator is actually
looking at. So the *launcher* takes the capture: it already serves the page from localhost, so
`GET /__screenshot` returns a real `CopyFromScreen` PNG, the same thing PrintScreen does. Being
same-origin it does not taint the canvas it is drawn into, so the satellite basemap survives as
a side effect. The listener is loopback-only, so nothing off the machine can ask for it.

`SetProcessDPIAware()` has to be called before anything asks how big the screen is: this
handheld runs 1920×1080 at 150%, and a DPI-unaware process is told the screen is 1280×720 and
silently captures only its top-left corner.

**The launcher writes the file, not the browser.** Chrome permits one automatic download per
origin and then blocks the rest, storing the decision — the profile had already recorded
`automatic_downloads: 2` for `http://localhost:8080`, so exactly one still per session reached
the disk and the rest vanished with no prompt (an `--app` window has nowhere to show one). PIC
sends the id with the request and the launcher writes the PNG to Downloads. The name is
sanitised to a bare filename: anything the page sends is untrusted, and nothing should be able
to steer that write out of the folder. The composite fallbacks still go through `<a download>`,
so `AutomaticDownloadsAllowedForUrls` is set as well.

The capture is stored **unmodified** — no caption. The instrument bar is already in frame and
the filename carries the timestamp, so a strip along the bottom would only cover the control
rail. The composite fallbacks below *do* get one, because there the surrounding UI is genuinely
absent from the image.

Everything below is the fallback, for when the page is not served by the launcher (from the Pi,
a static server, a test harness) or the capture fails. A slow endpoint is bounded by
`screenshotTimeoutMs` — PIC must never hang on it.

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

### The log is a bus (`client/js/core.js`, `wire.js`, `logview.js`)
`LOG` stopped being a console call. Every line goes three places: the console, a bounded
in-memory ring, and the on-disk session log. Levels (`ok` / `info` / `warn` / `err`) exist so
the overlay can filter.

**`wire.js` wraps `fetch` and `WebSocket` once, at load.** Relying on each call site to log is
how half of it turns out to be missing exactly where something went wrong, so instrumentation
happens at the boundary instead: every request, response, socket frame, close code and failure
is recorded with its outcome and duration. Two details that matter:
- A `4xx`/`5xx` is a **warning**, not a success. `fetch` resolves for those, which is precisely
  how a failed request gets mistaken for a working one.
- An `AbortError` is a deadline we set on purpose, so it is distinguished from a fault.

**High-frequency categories are coalesced, not dropped.** Control frames at 20 Hz would be
200 s of scrollback in a 4000-line ring, evicting everything explaining how the dive got there.
The suppressed count rides on the next line (`(+N more)`), and a **sweep** flushes the tail of
a burst that stops (`(+N more, then quiet)`) - without it, "telemetry was flowing and then it
wasn't" is the one case that disappears.

**The two endpoints that carry the log are not logged**, or the bus feeds itself: a flush would
append a line, which would trigger a flush.

### The LOGS overlay
A fault underwater is diagnosed while the vehicle is still in the water, and the operator
cannot leave the console mid-session. So the log is an overlay: centred, deliberately **not**
full screen, over a dimmed-but-visible backdrop - the vehicle behind it has to stay readable.

Tails by default; scrolling up suspends the tail so a line being read does not slide away, and
returning to the end resumes it. Rows are **appended**, not re-rendered - at 20 Hz a full
redraw per line would make the log viewer the thing slowing the console down - and the DOM is
capped independently of the ring. Closing unsubscribes, so nothing is built for a view nobody
is looking at. The filter input stops key propagation, because the HUD rule is that the map and
its overlays never capture piloting input.

The footer names the file on disk and says plainly that the view is the bounded in-memory tail,
so the scrollback is never mistaken for the whole record.

### One diagnostics button
CONFIG had MARK EVENT, EXPORT LOG and DIVE LOGS - three controls for things that now happen by
themselves (the session log writes itself; dives and media live in `navigation_logs/`). They
are replaced by one **LOGS** button. `openDiveLog()` is still on the console API rather than
deleted: removing a control should not silently remove the capability.

### Session artefacts (`client/navigation_logs/`)
Stills, screen recordings and the session log land in `images/`, `videos/` and `logs/` under
one folder, all named `{mode}_{iso}` - mode being what the console was actually doing, so a
directory listing sorts by time and still says which files were real dives. ISO colons are
stripped: Windows will not have them in a filename, which fails only once there is real data.
The launcher adds a **Neptune Recordings** desktop shortcut, because files this deep in an
install tree are otherwise unfindable.

The launcher writes all of them (`/__save`, `/__record`, `/__screenshot`). That is not
tidiness: the browser can only write through a download, and Chrome blocks every automatic
download after the first. Anything the page sends is sanitised to a bare filename before it
reaches the filesystem.

### Screen recording (`/__record`)
REC drives two recorders - the camera's card and the handheld's screen - reported separately,
because either can be absent (no camera on the bench, no ffmpeg on a fresh machine) and
neither absence should stop the other.

`gdigrab -> libx264 -crf 23 -preset veryfast -an`, which is the same trade as re-encoding a
screen recording with `-vcodec h264` afterwards, done once and live. ~1.4 MB/min measured on a
mostly-static screen.

Two decisions worth keeping:
- **Stopping writes `q` to ffmpeg's stdin**, it does not kill the process. A hard-killed MP4
  has no moov atom and will not play - the same class of loss as the camera's unsegmented
  `.MOV`, and just as silent.
- **The GPU encoder (`h264_amf`) is deliberately not used.** It would be lighter on CPU, and
  this handheld has an unresolved kernel fault under sustained GPU load (10). A recorder that
  can take the machine down mid-dive is worse than one that costs CPU.

### The session log writes itself (`client/js/recorder.js`)
The EXPORT LOG button is gone. A log that needs remembering is missing exactly when it is
wanted - the same reasoning as R2.4's automatic dive logging.

Events are **teed at `REC.log()`** into a separate disk queue rather than read back out of the
IndexedDB ring, because the Pi upload deletes from that ring: a disk writer reading the same
rows would race the uploader and lose whichever it got to first.

Flushed on a 5 s timer, not at shutdown. This handheld's kernel fault takes the machine down
with no unload event, so a log held in memory until exit is lost precisely in the sessions
worth reading. The queue is bounded and drops oldest-first, so an absent launcher costs the
tail of the log rather than the browser's memory. `pagehide`/`beforeunload` send the remainder
with `sendBeacon`, which survives unload where `fetch` does not.

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

### The GPU driver, and what the dashboard stopped doing to it
The handheld froze repeatedly with `0x133 DPC_WATCHDOG_VIOLATION`. The dump names it:

```
Failure.Bucket : 0x133_ISR_amdkmdag!unknown_function
amdkmdag.sys   : 32.0.23027.3001      (AMD Radeon kernel display driver)
```

The AMD display driver overruns in its **ISR**. Nothing here can repair that; the fix is an
AMD driver update or rollback. Note there were **no `4101` display-timeout events at all** -
it never TDR'd, it went straight to a bugcheck, which is why raising `TdrDelay` never helped
and why "GPU" stayed unconfirmed for so long.

What the dashboard *was* contributing: two permanently visible full-size
`backdrop-filter: blur(16px)` surfaces - the instrument bar and the control rail - composited
over a live H.264 video every frame, plus a full-width scan line animating forever above it.
Continuous blur over video is the most expensive thing a page can ask of a compositor.

`CONFIG.ui.reduceGpu` (default **on**) drops the blurs, the scan line and the full-viewport
gradient, and raises panel opacity so legibility survives. On a dark theme the visual
difference is almost nil. Losing a little glass is not a trade when the alternative is the
machine freezing with a vehicle in the water.

This does not make the driver correct. The machine has bugchecked with the dashboard closed.
It removes this application as a contributor, which is the only part that was ours.

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

---

## 14. Tests (`client/tests/`)

Browser checks against the **real dashboard**: `run.py` serves `client/`, injects one
suite as an extra `<script>` and drives headless Chrome. The page under test is the
shipping client byte for byte plus that tag, so the suites drive the same `MAP`,
`STATUS`, `CONFIG` and `state` the operator drives. Standard library and an installed
Chrome — no framework, no dependencies, matching the client's own rule.

Ten suites, 225 checks, ~95 s, exit 0 only if every one passes.

### The visual layer, and why its tolerance is measured

Each suite is photographed when it finishes, over a ~130-line CDP WebSocket client
(`cdp.py`) — Chrome's `--screenshot` flag fires on load and then *exits*, so it can never
capture a state a suite drove the page into.

Two shots: the real thing (looked at) and a **layout portrait** with the live map and
video hidden (compared). That split exists because two identical runs of the map suite
differed by 36% and 66% as tiles arrived from the network under a moving vehicle. With
the live surfaces hidden the floor is **0.000–0.016%**, so the threshold is **0.1%**.

The number matters more than it sounds. At the 2% first reached for, the exit button
growing from 28 px to 44 px went completely unnoticed — it is 0.13% of the screen. At
0.1% the same change reports 0.91% drift *while all 24 numeric checks still pass*, which
is exactly the gap a picture exists to cover. `map-zoom-and-rov` is recorded and never
compared: a check that cannot be stable should not pretend to be.

---

## 15. Calibrating the model against reality (`api/nav/calibrate.py`)

Every constant in the motion model is currently a guess — `subMaxSpeedMs` at 1.0 m/s,
`headingRatePerS` at 40 °/s, the ballast→depth curve, the server's `SpeedLUT`. A guessed
speed model is the **largest error term in dead reckoning**, because the error is not
random: it is a constant multiplier on every metre of the track.

The dive log could not fix that, because it recorded only where we *thought* we were.
Position over time says the sub moved; only **throttle next to distance** says how fast
it moves per unit of throttle. So the sample now carries the control channels — steer,
thruster output, ballast level and target, raw pressure, armed — alongside the nav state.

`calibrate` derives turn rate from measured heading, the depth model from measured
pressure, and speed from tether payout or a measured run.

**Speed must never be taken from the log's own x/y.** Those coordinates were produced
*by* the speed model, so checking them against it is circular and will cheerfully confirm
whatever is already configured. Speed needs an outside reference: a measured stretch
(`--ground-truth 20`), a spool encoder, or GNSS on the surface. Where the data cannot
support a number the tool says so and returns nothing — a calibration tool that always
produces an answer is worse than none. `--selftest` proves the maths against a synthetic
dive with known constants, including that a sensorless log yields nothing rather than a
guess.

---

## 16. The public demo (`.github/workflows/pages.yml`)

GitHub Pages serves `client/` **directly** on every push. Not a `demo/` copy, which
drifts the first time someone forgets to sync it — and a demo that lies about the product
is worse than no demo. Not symlinks either: this repo is developed on Windows, where git
checks them out as plain text files (`core.symlinks=false`), and Pages does not follow
them regardless.

`?sim=1` is demo mode: no host, no WebSocket, the simulator immediately — rather than
three seconds of failing to reach a Pi that was never there. Everything on screen stays
honest about being simulated (red robot, SIM state).

Because this is most people's first contact with a submarine control, **every glyph,
number and control carries a written explanation** of what it means — not what it is
called — in `title` and `aria-label`. The `demo-mode` suite enforces that: 44 elements
must each have one, each must be a real sentence rather than a label, and the ROV state
must be carried by *shape* as well as colour.

The explanation lives in the HTML and is captured into `data-help` at boot, because the
renderers also want the title for live state and whoever wrote last used to win — which
quietly erased the explanations a few seconds after launch.

---

## 18. The three connection glyphs, and what counts as evidence

The top bar carries one icon per link the operator can actually lose. Each has a fixed
meaning, and none of them may report a state on anything weaker than direct evidence.

**WI-FI** (`st-net`) — four states, because "no card", "not joined", "joined but going
nowhere" and "working" call for four different reactions:

| state | glyph | meaning |
|---|---|---|
| green | wifi arcs | joined to a network *and* that network reaches the internet |
| amber, steady | wifi arcs | a wireless adapter is present, not joined to anything |
| amber, **blinking** | wifi arcs | joined, but the network has no internet |
| red | wifi arcs, slashed | no wireless adapter on this handheld at all |

The two amber states are told apart by the BLINK, never by colour alone. Wi-Fi is for
map imagery and address search; it is **never** in the path of driving the sub.

**TETHER** (`st-rov`) — the cable from the handheld to the vehicle. Its red state is
about the *cable*, not the vehicle: with no wired adapter there is nothing for a sub to
be on the end of.

| state | glyph | meaning |
|---|---|---|
| green | submarine | wired adapter, API answering, control link up |
| red, pulsing | submarine | the sub is answering and reporting a **leak** |
| amber, blinking | plug | the API answers but the control link is not up yet |
| amber, steady | plug | a wired adapter is present, nothing answering on it |
| red | cut cable | no wired adapter — the simulator is flying this |
| red | robot | no launcher, so the adapters cannot be checked; it says so |

**A socket in `connecting` is not evidence.** It reports that state for as long as the
handshake has not failed, which against an address that will never answer is forever —
so it read as amber for an entire session spent in the simulator with nothing plugged in.
Amber now requires a real adapter or a real HTTP answer.

**CAMERA** (`st-video`) — green when the Pi has the camera and is transmitting; amber
when the Pi cannot see it but *this handheld* can see its access point; red when neither
can. Two independent observers, because a Pi with a dead antenna and a dead camera look
identical from the Pi alone.

None of this is visible to a browser: it cannot enumerate adapters, and `navigator.onLine`
cannot tell a network from the internet. It comes from the launcher's `/__net`, which
answers from `Get-NetAdapter -Physical` (`InterfaceType 71` = wireless),
`Get-NetConnectionProfile` (`IPv4Connectivity`) and `netsh wlan show networks`, cached for
6 s. When there is no launcher — the Pages demo, or the dashboard served from the Pi —
each glyph falls back to what it can honestly prove and the tooltip states the limit.

---

## 17. Bootstrap (`bootstrap.py`)

One entry point that reports what a machine has and what it lacks, for both halves of the
system: topside needs a browser and the launcher, the vehicle needs `install.sh`.
Deliberately **read-only** unless asked (`--dev` builds the API venv, `--test` runs the
suites) — a bootstrap that silently installs things is one you cannot run just to find out
where you stand, which is the main reason to have one.

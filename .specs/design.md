# Neptune — Design

Architecture, mechanism, and **why** each decision is the way it is. Where a choice looks
odd, the reason is usually a failure that has already happened on this hardware.

**Presentation is not in this file.** How the console *speaks* — the state ladder, the marks
on a reading, the badges, the connection glyphs, the colour language, the words on the
glyphs — is `docs/playbook.md`, and that copy is canonical. This file explains the mechanisms
that produce those states and cites the playbook section that governs how they look. A rule
about how something is *shown*, written out below, is a defect.

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
4. **Never invent its reading.** A missing number is a cannot-tell, never `0`. A vehicle
   that cannot measure its heading does not get a heading drawn for it. This rule has
   been broken repeatedly in this repo and every time it produced a console that looked
   healthy while hiding the failure it existed to show. **§24 is the full statement of
   it** — including the two ways it kept getting broken by people who believed they were
   obeying it.
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
A control marked `simulated` has something to simulate; one marked `gated` does not, and
gating is reserved for the cases where pretending would be a lie (camera REC with no
camera). Which mark a control gets is that judgement and nothing else. What each looks like
and how each behaves is `docs/playbook.md` §5.

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

Its zoom is also *tight*: `radarMetersPerPixel` is 0.25, so the 200 px circle spans ~50 m and
movement is visible within seconds. At 0.6 m/px the same circle spanned 120 m — at the sub's
~1 m/s, two minutes of full throttle per circle-width, and measured, 12 s of driving drew
20 px, which reads as "the trace is not working". Blind nav derives its scale from the real
canvas so it spans `blindSpanM` across the shorter edge, on any display.

### Canvas sizing uses layout, not the rect
`getBoundingClientRect()` includes CSS transforms, and both full-screen layouts animate in from
`scale(.94)`. Measuring mid-animation sized the canvas to 94% of the panel and left it there
(1280 → 1203), so the map's centre sat ~39 px from the dial's — the sub and its track drew at
the canvas centre while the dial's input vector drew at the viewport centre, appearing as **two
parallel offset lines**. `offsetWidth`/`offsetHeight` are the untransformed layout size.

### What the dial actually shows
Two things that legitimately point different ways: the **input vector** (`x = steer`,
`y = −throttle`), which is what you are *commanding*, and the **north indicator**, which
turns because the radar is heading-up. Around them sit four fixed numbers, 0–100, one per
direction — how hard the stick is being pushed that way, each showing only its own half of an
axis, so "how much am I giving it forward" is read straight off the top instead of decoded
from a signed percentage. The plotted track is a third thing again: where you have *been*.

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
`LOG` is not a console call. Every line goes three places: the console, a bounded
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
CONFIG carries one diagnostics control, **LOGS**. There is nothing to press for the other
three jobs: the session log writes itself, and dives and media land in `navigation_logs/`
unasked. `openDiveLog()` and `REC.mark()` stay on the console API rather than being deleted —
removing a control should not silently remove the capability.

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

- **`DPC_WATCHDOG_VIOLATION` (0x133)** on the handheld, repeating with an identical
  signature and a matching `LiveKernelEvent 141` (`VIDEO_ENGINE_TIMEOUT_DETECTED`). The
  kernel dump names the driver:

  ```
  Failure.Bucket : 0x133_ISR_amdkmdag!unknown_function
  amdkmdag.sys   : 32.0.23027.3001      (AMD Radeon kernel display driver)
  ```

  The AMD display driver overruns in its **ISR**. Nothing here can repair that; the fix is an
  AMD driver update or rollback. There were **no `4101` display-timeout events at all** — it
  never TDR'd, it went straight to a bugcheck, which is why raising `TdrDelay` cannot help
  and why "GPU" stayed unconfirmed for so long. `-NoGpu` and `-SafeGraphics` (§2) reduce this
  application's exposure; neither is a cure. `launch/crash-diagnostics.ps1` runs the dump
  analysis, so the next bugcheck is one command rather than an afternoon.
- **The USB tether NIC dropping off the bus entirely** (`Present: False`), requiring a physical
  replug.

**What the dashboard contributes, and what it stopped contributing.** Two permanently
visible full-size `backdrop-filter: blur(16px)` surfaces — the instrument bar and the control
rail — composited over a live H.264 video every frame, plus a full-width scan line animating
forever above it. Continuous blur over video is the most expensive thing a page can ask of a
compositor. `CONFIG.ui.reduceGpu` (default **on**) drops the blurs, the scan line and the
full-viewport gradient and raises panel opacity so legibility survives; on a dark theme the
difference is almost nil, and losing a little glass is not a trade when the alternative is
the machine freezing with a vehicle in the water. This does not make the driver correct —
the machine has bugchecked with the dashboard closed. It removes this application as a
contributor, which is the only part that was ours.

Both faults argue the same thing: **the handheld should not be in the safety path.** The
vehicle must be able to safe itself without topside, which is why the watchdog and the
on-vehicle journal matter more than anything topside can promise.

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
  for `*.sh`/`*.service`/`*.conf`/`*.yaml`/`*.yml`/`*.py` and CRLF for
  `*.ps1`/`*.bat`/`*.cmd`. Launcher scripts are kept **pure ASCII**.
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

**The runners are the only thing entitled to state a total, and they print it.** A number
copied out of a runner ages the moment anyone adds an assertion, and the copies then disagree
with each other and with reality at the same time — so no total for either suite is written
here, or anywhere this file points. Run them:

    python client/tests/run.py
    python api/tests/run.py

The rule, and the one place a number may still be written down, is
`client/tests/README.md` → *Where the numbers live*.

WHAT IS WORTH WRITING DOWN is the SHAPE of the verdict, because that does not age. Both
runners separate three outcomes that a single number would flatten into one: a check that
failed is a FINDING; a suite that never loaded is an ABSENCE of findings; and drift in the
layout portrait is a THIRD thing again. A suite blocked by a missing dependency reports
`DEPS -/-`, is counted apart from the pass total, is named in an **INCOMPLETE** verdict and
exits **2** — never a number, because there is no measurement to state. In a python with no
`pydantic`, `replay` and `telemetry` do exactly that rather than quietly shrinking the
denominator. And an over-tolerance visual drift fails the run on its own, because a drift
that merely reports and exits 0 is a baseline nobody re-blesses and a set of pictures nobody
looks at.

### The visual layer, and why its tolerance is measured

Each suite is photographed when it finishes, over a CDP WebSocket client (`cdp.py`) —
Chrome's `--screenshot` flag fires on load and then *exits*, so it can never capture a state
a suite drove the page into.

Two shots: the real thing (looked at) and a **layout portrait** with the live map and
video hidden (compared). That split exists because two identical runs of the map suite
differed by 36% and 66% as tiles arrived from the network under a moving vehicle. With
the live surfaces hidden the floor is **0.000–0.016%**, so the threshold is **0.1%**.

The threshold matters more than it sounds. At the 2% first reached for, the exit button
growing from 28 px to 44 px went completely unnoticed — it is 0.13% of the screen. At
0.1% the same change reports 0.91% drift *while every numeric check in the suite still
passes*, which is exactly the gap a picture exists to cover. `map-zoom-and-rov` is recorded
and never compared: a check that cannot be stable should not pretend to be.

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

Because this is most people's first contact with a submarine control, every glyph, number
and control carries a written explanation of what it means for this vehicle. That contract —
what the sentence has to say, why it is captured into `data-help` at boot, and that the
`demo-mode` suite enforces it — is `docs/playbook.md` §4.

---

## 17. Bootstrap (`bootstrap.py`)

One entry point that reports what a machine has and what it lacks, for both halves of the
system: topside needs a browser and the launcher, the vehicle needs `install.sh`.
Deliberately **read-only** unless asked (`--dev` builds the API venv, `--test` runs the
suites) — a bootstrap that silently installs things is one you cannot run just to find out
where you stand, which is the main reason to have one.

It also reports the **Pi-only hardware libraries** (`gpiozero`, `smbus2`, the BNO085
driver) present/absent, and installs none of them. Their absence on the bench is *correct*:
every hardware import is lazy inside `RealHardware`, and `NEPTUNE_HW=auto` then lands on the
flagged simulator. On the Pi the same absence is the difference between a real dive and a
simulated one presented as real, so there — and only there — it counts as missing.

---

## 18. The three connection glyphs, and what counts as evidence

The top bar carries one icon per link the operator can actually lose: **WI-FI** (`st-net`),
**TETHER** (`st-rov`) and **CAMERA** (`st-video`). Which glyph and which colour each state
wears is `docs/playbook.md` §2 — *The connection glyphs*. This section is the rule about what
a state may be claimed **from**, which is the part that keeps being got wrong.

**Nothing reports on anything weaker than direct evidence, and a socket in `connecting` is
not evidence.** It reports that state for as long as the handshake has not failed, which
against an address that will never answer is forever — so it read as amber for an entire
session spent in the simulator with nothing plugged in. Amber requires a real adapter or a
real HTTP answer.

**The tether's red state is about the *cable*, not the vehicle.** With no wired adapter there
is nothing for a sub to be on the end of, and that is what the glyph says. Wi-Fi is a
separate question entirely — it is for map imagery and address search, and is **never** in
the path of driving the sub.

**The camera needs two independent observers**, because a Pi with a dead antenna and a dead
camera look identical from the Pi alone. One witness is the Pi's own *association* — the
association, not "wlan0 is enabled", which is true on any Pi that has ever booted. The other
is this handheld's own radio sighting of the camera's access point, which is what separates
"the sub's radio has failed" from "the camera is off". Only a **positive** sighting counts,
and a sighting older than `apScanMaxAgeMs` is dropped rather than believed: cannot-tell is
never evidence of absence, and a minute-old answer is not a sighting.

None of this is visible to a browser: it cannot enumerate adapters, and `navigator.onLine`
cannot tell a network from the internet. It comes from the launcher's `/__net`, which
answers from `Get-NetAdapter -Physical` (`InterfaceType 71` = wireless),
`Get-NetConnectionProfile` (`IPv4Connectivity`) and `netsh wlan show networks`, cached for
6 s. When there is no launcher — the Pages demo, or the dashboard served from the Pi —
each glyph falls back to what it can honestly prove and the tooltip states the limit.

---

## 19. The ballast syringe, and one colour for depth

The tank is a syringe, so the control is one: a flat solid flange across a square top, a
barrel, and a V that tapers to a centred point. **No needle** — a needle would say the
water leaves the sub, and it does not. The liquid *is* the plunger: it sits in the taper
when the tank is empty and rises up the barrel as it fills.

**Drag UP to fill.** Down-to-fill was defensible while this was a bar — down means go
down — and became wrong the moment it looked like a syringe, because pushing a plunger
down expels the liquid rather than drawing it in. The water is drawn up from the tip so
the gesture and the picture agree, and the arrows follow: FILL on top with an up chevron,
EMPTY below with a down one.

The wall, the inside and therefore the liquid are all cut from **one declared shape**
(`--syr-shape`, a `clip-path` polygon on `.rail-slider.syringe`). Drawing the outline and
the fill separately is what lets a fill square off a taper it is supposed to follow, or
spill past a barrel it is supposed to sit in; there is only one shape here, so it cannot.

The barrel starts *below* the flange, and `bindVerticalControl` takes an `insetTop` so the
drag maps to the visible barrel rather than the element box. Without it a full tank is
one a few percent of which sits behind a solid bar, and the top of the travel is a band
where dragging moves the number and nothing appears.

### One colour for depth

The dive track, the ballast fill and the **Depth / Pressure / Ballast** readouts all wear
the same depth bands, so "how deep" is one visual language instead of a convention learned
twice. The bands, and the rule about who may wear them in which mode, are
`docs/playbook.md` §3. The mechanism that makes that rule enforceable is here.

Ballast is coloured by the depth that much water *buys* — `ballastLevel × sim.maxDepthM` —
not by the fraction itself. Straight 0–1 would look right and be wrong: the tank reaches
further than `maxDepthColorM`, where the ramp saturates, so a half-full tank would show a
band the track it is about to draw does not.

Depth and pressure are never tinted from ballast on a real dive. A sub descending on a full
tank with a dead depth sensor would then show a deepening colour it never earned, and the one
symptom that gives the failure away would be the symptom we had painted over. Absence is
shown as absence — the readout returns to its default look rather than taking a "neutral"
band, and its tooltip says the number is not tracking a sensor. A reading older than
`staleTimeoutMs` is dropped rather than believed, for the same reason the camera drops a
stale AP sighting (§18).

This needs telemetry to say **which** fields actually arrived: a frame with no `depth`
leaves `state.depth` holding its last value, which on a sensorless sub is a number from
the simulator. `net.js` stamps `state.depthAt` / `state.pressureAt` only when the field is
present, and those stamps are what the colours are gated on.

---

## 20. The estimator (`NAV_FILTER`)

Two estimators, one interface (`update(SensorSample) -> NavState`), selected by config
alone:

| `NAV_FILTER` | Backend | What differs |
|---|---|---|
| `dr` *(default)* | the existing `DeadReckoner` | nothing — behaviour untouched |
| `filtered` | the same dead reckoner | only its **heading and speed inputs** are filtered |

The position integration, the tether clamp, snapping and the confidence logic are the same
code in both. The filter does not get to move the sub; it gets to decide what heading and
what speed the sub is moved *with*. That boundary is deliberate: the parts that have been
exercised in the field stay exactly as they are, and the new maths is confined to two
scalars whose quality can be measured against a log.

**`dr` stays the default, and the reason is not conservatism.** There are no real dive logs
yet. A filter tuned against the simulator has been validated against its author's
assumptions about the water, which is the one thing the simulator cannot know. Promotion is
therefore a decision made **with data** — `python -m nav.cli replay <log> --filter both`,
§20.5 — and it is an environment variable, so a bad dive is undone by a restart rather than
by a revert.

### 20.1 Heading — a complementary filter, never a step

The BNO085's fused yaw is polluted by the thrusters' own magnetic field; the simulator
models 22° of error at full throttle, and it is modelled because it is real. The gyro is
immune to magnetism and drifts. So: integrate the gyro short-term, and correct toward the
magnetic heading **only when the magnetic heading is worth believing**.

Per tick, `dt = s.t - prev_t`:

1. **Predict** — `h ← (h + (gyro_z_dps − b)·dt) mod 360`.
2. **Trust gate** — trusted ⟺ `mag_cal ≥ 2` **and** `thrust_level < 0.5`, where
   `thrust_level = max(|left|, |right|)` — the **actual output**, not the command, so a
   disarmed sub reads as zero thrust and the compass is believed.
3. **Innovation** — `e = wrap180(mag_heading − h)`.
4. **Correct, only when trusted** — `alpha = dt/(tau+dt)` with `tau = 2.0 s`, the correction
   **slew-capped to ±5 °/s · dt**. Even a large accumulated error walks back smoothly. On
   re-entering trust there is no snap; the same capped blend handles re-convergence.
5. **Learn the bias** only while trusted and `|e| < 10°`: `b ← clamp(b − k_b·e·dt, ±3 °/s)`,
   `k_b = 0.01/s`. Learning a bias from a disturbed compass teaches the gyro the
   disturbance, which it then coasts on.
   **The minus sign is deliberate — read this before "fixing" it.** The predict step
   *subtracts* `b`, so `b` must converge on the gyro's own offset. Stationary sub at 100°,
   gyro reading a steady +2 °/s of pure bias: with `b = 0` the prediction walks `h` to 102
   while the compass still says 100, so `e = wrap180(100 − 102) = −2`. The bias needed is
   `+2` and `e` is negative, so the correction has to run *against* `e`. Written the other
   way round the feedback is positive and `b` diverges to its clamp over about a minute,
   taking the heading with it.
6. **Expose `gyro_only = not trusted`**, so the console can say the compass is being ignored
   *on purpose*.

`h` initialises from the first sample's `heading_deg` **regardless of trust**: a wrong start
converges, an unset start is NaN poison that spreads through every downstream number.

A `dt > 0.5 s` is a gap, not a long tick: re-seed from the magnetic heading if trusted, else
hold, and do **not** integrate across it. Integrating a gyro across a stall means the drift
of the stall is added to the map as travel.

**Every subtraction of two headings goes through `wrap180`.** The 359→1 crossing is the
classic bug in this whole class of code, it produces a 358° innovation that the slew cap
then walks the wrong way round the circle for a minute, and there is an explicit unit test
for it.

### 20.2 Speed — one 1-D Kalman filter, and only one

State `x = [v, b_a]` — water-relative forward speed, and forward-accelerometer bias.
`P` init `diag(0.25, 0.01)`. Predict integrates `accel_fwd − b_a` once; `Q =
diag(σ_a²·dt², q_b·dt)` with `σ_a = 0.15 m/s²`, `q_b = 1e-4`.

Exactly **one measurement per tick**, `H = [1, 0]`:

| Condition | `z` | `R` | Why |
|---|---|---|---|
| paddlewheel fresh | `sign(throttle) · speed_ms_measured` | `max(0.03, m_per_pulse/window)²` | the wheel is coarse at low pulse counts and the filter must know that |
| stale **and** `\|throttle\| < 0.1` | `0` | `0.05²` | a stopped, unpowered sub is genuinely stopped — this is what kills accel-bias drift at rest |
| stale **and** `\|throttle\| ≥ 0.1` | `lut.speed(throttle)` | `(0.3·\|z\| + 0.1)²` | the LUT is a **weak prior**, never a crisp measurement |

`sign(v)` is clamped to follow the throttle (the wheel is directionless) and `|b_a| ≤ 0.5`.

**The never-double-integrate rule (spec §2.2), because this looks like a violation and is
not.** Acceleration is
integrated **once**, into velocity, inside a filter whose velocity is continuously corrected
by a measurement. Position still integrates velocity exactly once. That is the agreed
boundary and it is not to be crossed: double-integrated accelerometer position is the
hundreds-of-metres error this project rejected at the start.

### 20.3 The snag detector runs in both modes

It is a **safety signal**, not an estimator feature, so it is not gated on `NAV_FILTER`:

> `snagged` ⟺ `thrust_level > 0.5` sustained for **> 2 s** while the KF speed — or the
> *measured* speed in `dr` mode — is below **0.05 m/s**. The LUT does not count, because the
> LUT would report exactly the speed the throttle implies and cheerfully confirm the sub is
> moving.

Effect: `NavState.snagged = true` and `confidence = min(confidence, 0.4)`.

This is the *"the map marches forward while the sub is pinned on a shopping trolley"*
detector, and it is the entire reason there is a paddlewheel on the bill of materials. It is
also the one place where a sensor's silence is the signal: no pulses at idle means "slower
than the wheel can see", no pulses at full thrust means something is holding the sub.

### 20.4 What is deliberately NOT built (and why)

Written here **and in code comments**, so a future agent does not helpfully improve it:

- **No position-domain EKF.**
- **No online current estimation.**
- **No surface-refix fusion.**

With only heading, speed and payout there is not enough observability to learn a current
vector — the filter would attribute every modelling error to "current" and produce a
confident, wrong number, which is the exact failure mode this codebase exists to avoid. And
there are no real dive logs to validate any of it against. The replay harness is what will
justify (or kill) that work later, **with data**.

### 20.5 The replay A/B harness

```
python -m nav.cli replay <divelog> [--filter dr|filtered|both]
```

Runs the logged `SensorSample`s back through the estimator(s) and reports track divergence
over time, final-position delta, % of time gyro-only, % of time on each speed source, and
snag events. `--filter both` prints them side by side.

Two tests are the **acceptance gate for the whole filter**, not a nicety:

1. A sim log **with the mag-disturbance episode** — `filtered` must beat `dr` on track error
   against the simulator's own ground truth (which is why `sim.py` exposes its true position
   in the log).
2. A **clean** log — `filtered` must not be worse than `dr` beyond a small tolerance.

A filter that only wins on the pathological case is a filter you cannot leave switched on.

---

## 21. The leak has two wet stages, and one of them is advisory

One leak flag answered the wrong question. "There is water in the hull" is not one event: a
film in the bilge means *finish the pass and come home*, and water 2 cm up means *surface
now*. Collapsing them either cries wolf on condensation or says nothing until it is too
late. The ladder the operator reads — four drops, because there is a fourth thing that can be
true — is `docs/playbook.md` §2.

`leak_state_from()` is the single verdict, and everything downstream reads it rather than
re-deciding: `FLOOD` if the flood probe is wet regardless of the warn probe, else `WARN`,
else — and only if the probes are actually being sampled — `NORMAL`. The probes are
independent inputs; the precedence is in the read, not in the wiring. Nobody sampling them is
`UNKNOWN`, because `NORMAL` is a positive claim that the hull is dry and a positive claim may
never be a fallback. `Telemetry.leak` stays a bool and is true for **either** wet stage, so
nothing that already consumes it goes quiet.

Both wet stages debounce: `leak_debounce_samples` consecutive wet samples at 10 Hz.
Condensation, a launch splash and a droplet running down the hull all touch a probe briefly;
real ingress does not stop. The debounce is what makes FLOOD worth believing, and an alarm
nobody believes is an alarm that gets ignored on the day it is right.

**A latch is one-way, so clearing it is a deliberate act.** A probe drying is not evidence
the hull is sound, so nothing decays a latched stage by itself. `leak_reset` is a full
command — the whole recv → validate → apply → ack lifecycle, so the operator gets an answer
rather than a silence — and it **refuses while a probe is still wet**, reading the live pins
rather than the debouncers that are latched. `leak_rearms` rides on the wire so the console
can say how many times it has been done. Without that command the only thing that clears a
latched WARN is restarting the service, which on a canal bank means either the console lies
for the rest of the session or the dive ends.

**The failure this design would otherwise hide.** A dead probe reads dry forever, and dry is
the answer everyone wants. Nothing in a bare digital input can distinguish *dry* from
*disconnected*, so two things are done: `leak_probe_fault` reports the combinations that are
physically impossible (flood wet while warn is dry — water reaching the upper probe passed
the lower one; both wet on a dry deck), and the pre-dive readiness check surfaces it at arm
time. The rest is covered by a five-second dip test on the bench, documented in
`docs/hardware.md` §5.1, because a procedure that is written down is cheaper than a sensor
that lies.

---

## 22. The battery is 2S, and the 24 V scale is dead

The pack is **2S Li-ion: 8.4 V full, 7.4 V nominal**. The mock's old `24.8` and the client's
`20.0` floor were placeholders from before the pack existed, and a threshold that describes
a different vehicle does not fail loudly — it reads "full" forever.

| Band | Volts | Setting | Colour |
|---|---|---|---|
| full | 8.4 | `battery_full_v` | top of the scale |
| dive on | ≥ 7.0 | `battery_warn_v` | green |
| head back | < 7.0 | `battery_warn_v` | amber |
| surface | < 6.6 | `battery_crit_v` | red + SURFACE prompt |
| hard floor | 6.0 | `battery_floor_v` | 3.0 V/cell — the cells are damaged below it, not merely flat |

These thresholds are the numbers `docs/playbook.md` §3 delegates here; the rule they are read
under is stated there — colour comes only from the bands, and the voltage number is always
beside it, because a band is a judgement and the number is the measurement.

Nothing in software enforces the floor. It is deliberately a documented number rather than a
cut-off: a sub that safes itself at 6.0 V in the middle of a canal has swapped a damaged
pack for an unrecoverable vehicle. The operator is told, early and twice, and the operator
decides.

---

## 23. Ballast is unknown until it is homed

The syringe is driven by a stepper through an A4988 with **no position sensor**. Level is
`steps / span`, and the step counter means nothing until it has been zeroed against the
EMPTY limit switch. So from power-on until the first `ballast_home()`, the honest answer is
that the position is **unknown**.

`get_ballast_level()` returns `float | None`, and `None` is that answer. Returning `0.0`
would assert *empty*, which is a specific claim about something the vehicle cannot see, and
the operator would dive on it. `Telemetry.ballast_level` is `Optional[float]` for the same
reason, and the glyph shows an explicit unknown — **not 0 %, not 50 %** — plus an
affordance to home. A gauge sitting confidently at half is worse than a gauge admitting it
does not know: one of them prompts the action that fixes it.

Everything downstream had to learn the same word. `rov.py`'s initial ballast target, its
`hold` branch and its telemetry rounding, and `nav/sensors.py`'s sample construction, all
previously assumed a float.

The two limit switches are wired **normally-closed to ground** with internal pull-ups, so a
cut lead reads as *triggered*: a broken switch fails to a stop rather than to a silent
absence, and the plunger does not drive itself into the end of the barrel. Either switch
stops motion in its own direction always, even mid-command.

**A skipped step is not a glitch.** On an open-loop axis it is the reported level quietly
drifting away from where the plunger actually is. So when the FULL switch closes at a count
disagreeing with `ballast_span_steps` by more than `ballast_span_tolerance` (5 %), it is
logged, `ballast_needs_rehome` goes into telemetry, and the console surfaces it. Swallowing
that event is how a syringe becomes quietly wrong, and a quietly wrong syringe strands a
sub.

---

## 24. Sensor liveness — the doctrine, and the four rounds it took to get right

This is the most important thing in this document. It took four adversarial review
rounds, each of which fixed something real and each of which left the next layer intact,
and it is written out at length because every one of those rounds was staffed by people
who believed they were already obeying the rule in §0.4.

### 24.1 The rule

> **A signal whose sensor is absent shows CANNOT-TELL, never a plausible number.**

Two clauses do the work, and each was learned by getting it wrong.

**"Absent" includes "was here and stopped."** Everybody reasons about the sensor that was
never wired. Almost nobody reasons about the sensor that worked for four minutes and then
stopped, and that is the one that kills you, because it is the one that leaves a *number*
behind. The MS5837 stops answering at 4.33 m. Every later attempt raises. The cache keeps
20.85 psi. Sixty seconds of dead-bus ticks change nothing. `rov.py` turns that cache into
`depth=4.33` in every frame at 15 Hz, the client stamps each arriving frame as fresh, and
the console paints a confident, fully colour-banded 4.3 m while the sub descends to 8.
Every check anyone had written asked *"did the chip come up?"* — and the chip **had** come
up. A liveness test that a dead sensor passes is not a liveness test.

**A cannot-tell default that is itself a measurement is not a cannot-tell.** This is the
clause that survived three rounds, because it hides inside code that reads as careful.
The values are all chosen to look inert, and not one of them is:

| The "safe" default | What it actually asserts |
|---|---|
| `heading = 0.0` | **due north.** The radar is heading-up, so the whole map swings north and the dead reckoner runs the track north |
| `depth = 0.0` | **at the surface.** Written into the permanent dive log, where it is indistinguishable from a real surface sample |
| `pressure = surface_psi` | *the sensor is reading exactly atmospheric* — a specific, checkable claim about a chip that is not answering |
| `mag_cal = 0` | **"a compass answered, and it says it is uncalibrated."** The strongest thing you can say about a bearing short of trusting it — attached to a chip that is silent |
| `leak = "NORMAL"` | **a positive safety claim**: *I looked, and the hull is dry* |
| `snagged = False` | *navigation looked, and the sub is moving freely* |

A zero is not the absence of a number. It is the number zero, and on an instrument that is
exactly what it looks like. The test to apply is not *"is this default harmless?"* but
*"if an operator read this off the screen as a measurement, would they act on it?"* — and
for every row above the answer is yes.

The corollary that catches the subtler case: **a subsystem's death must never look like
good news.** When navigation goes quiet, leaving `snagged` and `gyro_only` at their
defaults is not neutral, because their defaults are the two reassuring answers. At the
exact instant nav died the console got *quieter*: a standing snag warning cleared itself
and the GYRO badge went out. `False` means "nav looked and says no"; `None` means "nav
cannot tell".

### 24.2 The chain is only as honest as its weakest link

The signal crosses six files, and it is a *conjunction*: every one of them has to preserve
the null, and any single one that coerces it destroys the property for the whole system —
silently, and while every test on either side of it still passes.

```
api/hardware.py     DeviceHealth decides a chip is not answering; every readback on it
                    returns None. sensor_faults() names which chip.
        |
api/protocol.py     Telemetry fields are Optional; sensor_faults: list[str] rides along.
        |
api/rov.py          Builds the frame. Passes the nulls through; does not fill them in.
        |
api/nav/sensors.py  Reads the same hardware for the estimator.     <-- THE LINK THAT BROKE
        |
api/main.py         fill_nav_fields() stitches nav's answers into the frame.
        |
client/js/*         net.js ingests, core.js judges, render.js draws '?' + amber wavy.
```

**Round three did five of those six and shipped.** `api/nav/sensors.py` had no owner, and
it coerces every cannot-tell straight back into a plausible number:

```python
heading = _num(_readback(hw, "read_heading", 0.0), 0.0) % 360.0
pressure = _num(_readback(hw, "read_pressure", surface_psi), surface_psi)
mag_cal  = int(_num(_readback(hw, "read_mag_cal", 0), 0.0))
```

The helper's own docstring says *"Neither case invents a number"* — and the **default it
is handed** is an invented number. The docstring is true about the helper and false about
the call. Then `fill_nav_fields()` stamps nav's heading unconditionally over the null
`rov.py` had correctly sent, and the invention arrives on screen wearing the estimator's
authority.

Reproduced end to end against the real `NavService`: `rov.py` sent
`heading=None card=None mag_cal=None faults=['bno085']`, and the frame that reached the
client read `heading=0.0 card='N'` — **a confident bearing of DUE NORTH sitting beside a
NO COMPASS badge and a "bno085 not answering" fault, all on one screen.** That is worse
than the frozen bearing round three set out to fix: a frozen bearing is at least a
direction the sub once pointed.

Three things follow, and they are design rules, not process notes:

1. **A file in this chain with no owner is a defect in the change, not an oversight in the
   review.** Round four's brief had to say so out loud, because rounds one to three each
   assigned the files somebody had thought of.
2. **The null must be the only thing that can travel.** Anything that accepts a `default=`
   for a sensor reading is a place the property can be lost. The safe shape is a readback
   that returns `None` and a caller that must handle it — not a readback with a fallback
   the caller never sees, because then the coercion is invisible at the call site and the
   call site is where it has to be noticed.
3. **Verification has to cross the whole chain.** Every layer had passing tests
   throughout. What nobody had was one check that puts a dead sensor in at the hardware
   end and looks at what comes out at the client end, which is the only test that could
   have caught this.

### 24.3 With no heading there is no track

Heading is not one reading among many, because everything downstream is built on it.

- The radar is **heading-up**. A heading nobody is measuring rotates the entire map.
- Dead reckoning integrates speed **along** the heading. A placeholder heading does not
  produce a slightly-wrong track; it produces a confident track in an arbitrary direction,
  drawn at full length and full apparent precision.
- The dive log records it. A wrong bearing in a `.jsonl` is permanent, and it is what
  `calibrate` will later be handed as ground truth.

So the rule is stronger here than elsewhere: **no heading means no track**, not a track on
a substituted heading. The map holds and says so. The bearing renders `?`, the badge reads
`NO BEARING`, and the radar stays drawn on the *last angle the compass actually gave* —
a stale picture the operator can be told about, rather than a fresh lie.

`NO BEARING` is kept distinct from `MAG?`, `GYRO` and `NO COMPASS` because the operator's
next action differs in each, and collapsing any pair of them sends them the wrong way: a
bearing that is suspect, a compass being ignored on purpose, no IMU that ever answered
(`mag_cal` null, not 0), and one that answered this dive and has stopped are four different
facts about four different errands. The badge table is `docs/playbook.md` §2.

### 24.4 How liveness is actually decided (`DeviceHealth`)

One chip, one verdict, **computed rather than probed** — `_answering()` sits on the front
of every cache read and must never touch I²C, because a blocking bus read on the event
loop stops telemetry, the watchdog and the camera together. It is one dict lookup, one
clock read, one compare.

Two ways to fail, because there are two ways a bus dies:

- **Consecutive raises.** One NAK on a canal-side loom is noise and the retry usually
  takes; blanking a working gauge on it would teach the operator that a blank means
  nothing. `fail_streak` in a row is not noise.
- **Silence.** *Nothing has to raise for a device to stop answering.* A conversion state
  machine that never reaches its collect stage, a sensor thread that took an exception and
  ended, a driver that returns without writing — none produce an error, and all leave the
  cache exactly as frozen. So a good read must also have happened **recently**, inside a
  window sized to how often that device is polled. This is the half a raise-counting
  design misses, and it is the half the frozen-MS5837 dive turned on.

**Never answered is faulted**, not a gentler state: a device that has not produced one
good read has nothing behind its cache at all.

`DeviceHealth` is pure and **clock-injected** — `now` is passed in, never read inside — so
the whole rule runs on a bench in microseconds. Logic reachable only by waiting on a real
dying sensor is logic nobody tests, which is most of why this took four rounds.

`MockHardware._kill_sensor(name)` / `_revive_sensor(name)` are the fixture that makes the
failure reachable at all. Killed means the readbacks go to cannot-tell and the name
appears in `sensor_faults()`, **while the simulation underneath keeps running** — so a
test can assert that the depth readout neither followed the water down nor sat frozen at
the last value. Recovery is half the contract and the half that gets skipped: a gauge that
goes blank and *stays* blank after the connector is reseated is its own fault, and one
nobody would find until a dive.

### 24.5 The null and the name are one decision read twice

`sensor_faults()` unions the per-chip liveness verdicts with the latched subsystem faults
(the I²C bus that would not open at all; limit switches reading impossibly). It is
deliberately **the same verdict the readbacks gate on**, read a second time, so a named
chip and a blank gauge cannot drift apart and contradict each other on screen.

Blanking depth says *"do not believe 4.3 m"*. It does not say whether the sensor died, the
bus died, or the vehicle is doing something clever — and a blank gauge with no cause reads
as a dashboard glitch, which is something an operator waits out while the sub keeps
descending. Naming the chip turns it into an errand: **DEPTH — MS5837 NOT ANSWERING.**

An **empty** `sensor_faults` is *not* a certificate of health. A backend that cannot track
liveness reports empty, so the nulls on the individual readings remain the authoritative
claim and the list only ever supplies the cause. A dead `i2c` stands behind all three
chips, so one unplugged connector reports one fault rather than three unrelated ones.

### 24.6 Presentation: three shapes, because there are three facts

Reporting, stale and cannot-tell are three facts and get three looks. Those looks are the
state ladder in `docs/playbook.md` §1, and the marks themselves are its §2. Three things
about them are mechanism rather than presentation, and belong here.

**The two marks differ by construction, not by degree.** A dash reads as a dropped frame,
and a dead depth sensor dressed as a dropped frame is a sub flown on a number nobody is
taking. `?` is this console's existing word for genuinely-not-known — the unhomed syringe has
always used it. **The last number is never shown**, because a frozen reading and a steady one
look identical.

**Three independent carriers** — the mark, the amber, and an alert chip naming the part — so
none of them has to be the one that gets noticed.

**The tint is a statement about the sensor, not about the link**, and this is worth stating
because it looks like a bug. Gate it on the stamp `net.js` writes on every arriving *frame*
and the MS5837 that died at 4.33 m keeps that stamp fresh at 15 Hz, so the frozen number
wears a full, confident depth-band colour all the way down. The tint is the loudest thing on
the tile, which makes it the part that lies hardest.

### 24.7 Acceptance criteria

Stated here in the requirements' voice; the normative copy is **R7.6**.

1. WHEN a sensor has stopped answering, THEN every reading derived from it SHALL report
   cannot-tell, AND "stopped answering" SHALL cover both never-wired and
   wired-then-stopped.
2. A cannot-tell SHALL NOT be represented by a value that is itself a valid reading —
   including `0.0` heading, `0.0` depth, atmospheric pressure, `mag_cal` 0, `NORMAL` leak
   and `False` snag.
3. NO stage between the sensor and the screen SHALL substitute a default for a
   cannot-tell, AND every file on that path SHALL have a named owner in any change that
   touches the property.
4. THE console SHALL distinguish a dead sensor from a dropped frame by SHAPE, not by
   colour alone.
5. THE vehicle SHALL name which chip stopped, AND that name SHALL be derived from the same
   verdict the nulls are.
6. WHEN there is no heading, THEN the system SHALL NOT advance the track, SHALL NOT rotate
   the map onto a substituted bearing, AND SHALL say that there is no bearing.
7. THE liveness rule SHALL be exercisable on a bench without a real dying sensor.

---

## 25. The hazard card (`api/nav/crt.py`, `service.py`, `client/js/crt.js`)

A canal is full of things that will stop this vehicle and are invisible from the surface:
sluice intakes that pull, stop-plank grooves that eat a tether, culvert mouths, weirs,
safety gates, outfalls. The Canal & River Trust publish every one of them as open data.
The console drew none of them, so **an unfetched card and a clear channel were the same
picture** — which is §24's rule with the sensor swapped for a download, and it fails the
same way: the map looks healthy and is hiding the thing it exists to show.

### 25.1 Two phases, and which half is allowed a hostname

Same split as `areas.py` and `satellite.py`. `crt.py` is the **only** module in this
subsystem that opens a socket, it runs at bootstrap, and it is **never imported by the
runtime path**. There is no hostname anywhere in it: every URL lives in `nav/config.py`,
is reached exactly once by `python -m nav.cli crt-fetch <area>`, and is never resolved
canal-side — where a DNS lookup does not fail so much as *hang*, and a hung overlay on a
handheld is a driving view that has stopped updating.

`nominal.py` and `soundings.py` open nothing at all. `service.py` reaches into `crt.py`
for exactly three pure functions — `safe_area_name`, `area_dir`, `provenance_path` — and
imports it lazily, so a build without the downloader still serves everything else rather
than failing to start. The runtime answer to *"is the hazard layer here"* is therefore a
`stat()` and a parse, and it is instant whether the answer is yes or no.

**The operational consequence is the whole point: new water means a new fetch, at home,
before you go.** There is no internet at the canal, so a card that was not downloaded is a
card that cannot be downloaded, and the console with no card draws an empty map. That is
why it is a go/no-go item in `NavService.readiness()` and not a nicety.

### 25.2 What lands on disk

```
data/crt/<area>/<layer>.geojson      clipped FeatureCollection, attribution inside it
data/crt/<area>/<layer>.prov.json    that one file's provenance, beside that one file
data/crt/<area>/provenance.json      the index: every layer on the CARD, every skip,
                                     every warning
```

The layer key is built from the **service** name, not the layer's display name —
`Sluices` is the display name of five different services on this org — plus the ArcGIS
layer id, giving `sluices-0`, `canals-by-km-length-1`, `pumping-station-points-3`.

All three files go through `_write_atomic`: write beside, `os.replace`. `write_text()`
truncates the target and *then* writes into it, so a fetch killed at the bank left a
truncated GeoJSON where a whole one had been — and a fragment has a name and a size, which
was all the index and the readiness gate ever looked at. The temporary lives in the same
directory (a cross-filesystem rename is a copy, and a copy is not atomic) and its suffix
goes on the **end** of the full name, so `sluices-0.geojson.part` matches neither
`*.geojson` nor `*.prov.json` and nothing globbing that directory — here or in
`nominal.py` — can pick a fragment up as a layer.

**The index describes the CARD, not the run.** A layer this run could not refresh is
listed with the date it was *actually* fetched, carried forward out of its own
`.prov.json`. An index that listed only what this run downloaded would tell the console
the operator has nothing while two dozen good layers sit beside it.

### 25.3 The traps, in one paragraph each

These are measured against the live services, and the reason to write them down is that
somebody will re-run this against a changed API. **The full detail, with the request
shapes, belongs in `api/nav/README.md`; this is the index to it.**

- **The layer id is not 0.** Every ArcGIS example ends in `/0/query`. The Canals services
  are layer 1 and `Pumping_Station_(Points)` is layer 3. The service is *asked* for its
  layers and the ids it answers with are the ids that get queried.
- **A service error arrives as HTTP 200.** A bad layer id or a throttled key returns
  `{"error": {...}}` with a 200 status, and `json.loads` is perfectly happy with it. A
  caller that checked only the status code pages that error body forever, finds zero
  features in it each time, and writes an empty layer that reads as *surveyed, nothing
  here*. `_get_json` raises on an `error` key.
- **`outSR=4326` going out, `inSR=4326` going in.** Storage CRS is a genuine mix: on the
  card in `data/crt/gas-street/` it is 13 layers in EPSG:3857 and 13 in EPSG:27700.
  Without `outSR` the coordinates come back as eastings and land in a GeoJSON file as
  though they were degrees — 435000 is a valid number and a longitude in the Pacific.
  Without `inSR` the *area box* is read in the layer's storage CRS and a Birmingham bbox
  selects nothing, which arrives as an empty layer: the false reassurance, by arithmetic.
- **The Hub item's `extent` is junk.** Fourteen of the twenty Hub datasets report a
  world-spanning `[-160,-80,160,80]`, which passes any bbox-overlap test ever written and
  tells you a Birmingham layer covers Peru. It is ignored on purpose.
- **The Hub is a window, not the catalogue.** It publishes ~20 datasets out of 204
  services on the org, and sluices, safety gates, stop-plank grooves and outfalls — the
  four that most directly stop a small ROV — are all outside it. Those seven are
  hardcoded in `crt_extra_services`, which is also the floor of discovery: with the Hub
  unreachable the run still knows what to ask for.
- **The near-duplicates diverge.** Five separate Sluices services answer 886, 892, 893 and
  937; the 937 one is `Sluices_View_Public`, not the Hub-curated view. Picking one because
  its name is shorter is how a survey acquires 44 sluices it will never be told about.

### 25.4 Two independent counts, because truncation is this API's signature failure

Before paging, the server is asked `returnCountOnly` for the **same** bbox. After paging,
what landed is compared with it, and the verdict is written into the record as
`count_check: agrees | disagrees | unavailable`. Separately, each layer's **national**
count is compared with `_EXPECTED_FEATURES` — numbers measured against the live services,
not copied from anywhere — so a service quietly swapped for one of its own legacy
near-duplicates shows up as *drift* rather than as a shorter list nobody counted.

Drift is **recorded and never acted on**. The Trust adds real culverts too, and this module
has no business deciding which of the two happened.

Paging stops on a **short page**, because these services cap at their own
`maxRecordCount` regardless of what was asked for and do not reliably send
`exceededTransferLimit`. `_MAX_PAGES = 400` is a guard on nothing: the largest layer is
7691 features, so reaching it means the server has stopped honouring `resultOffset` and is
serving page 1 forever — which is a failure, not a long download, and it is treated as one.

### 25.5 Clipping keeps features whole

The file holds every feature that **touches** the area, uncut, and both the collection and
the provenance record `clip_rule = "intersects"`. Cutting at the boundary would grow an
edge nobody surveyed — on the planning-buffer layer that new edge reads as the edge of the
buffer — and the canal centrelines are the snapping target for `snap.py`, so a centreline
truncated at the area edge stops snapping exactly where a tether-limited ROV spends its
time, since the area is drawn around the launch point and the far end of the cable is near
its boundary.

The clip is re-run locally even though the server was asked for the same envelope, because
the server's answer is not in our coordinate system. Measured on `Canals_By_KM_Length`
(stored EPSG:27700) with an envelope of `[-2.6,52.0,-1.0,53.2]`: 1218 features returned,
15 of them wholly east of longitude -1.0, the furthest at -0.977 — about 1.5 km outside. A
lat/lon rectangle is not a rectangle in 27700, and the area bbox has to mean the box the
tile pyramid was cut from, not the server's transformed approximation of it.

The FeatureCollection's own `bbox` member is the bbox **of its features** (RFC 7946 §5),
and the area it was cut for is a separate, honestly-named `clip`. Writing the area box into
the member would be a small lie with a specific victim: a whole canal navigation, kept for
touching this area, extends far past it, and a consumer trusting the member would think it
had the whole route in a 2 km box. An empty collection gets **no** `bbox` member rather
than a zero-size one at `[0,0]` — a box off the coast of Ghana is not a better answer than
no box.

### 25.6 Empty, absent, corrupt — three answers, and only one is safe

This is the doctrine of §24 applied to a download, and it is the part of this subsystem
that exists for safety rather than for tidiness.

| The fact | What is on disk | What is served | What the operator sees |
|---|---|---|---|
| Fetched cleanly, nothing of this kind in this area | a file with **zero features** | `FeatureCollection`, `status: present` | **NONE MAPPED** |
| Fetch died part-way | **no file at all** | `AbsentLayer` + the skip reason | **ABSENT** |
| File is there and will not parse | a fragment | `UnreadableLayer` | see §25.9 |
| Nobody could be asked | — | — | **CANNOT TELL** |

A layer that fetched cleanly and matched nothing **still gets a file**. That is a survey
result — *no tunnels here* — and it is worth writing. A layer whose fetch failed part-way
gets **no file**, because a truncated page is a well-formed FeatureCollection with the
exact shape of *no hazards here* and nothing downstream can ever tell the two apart again;
the partial result is dropped and the failure is recorded instead.

**Presence is decided by READING, not by `stat()`.** `service._read_layer` used to call a
layer present because the file had a size, and a `stat()` cannot tell 400 kB of GeoJSON
from 400 kB *of* a GeoJSON. A card pulled mid-write went green at the pre-dive gate, the
index published `status: present` with a feature count copied out of the fetch's own
provenance — a number describing a file nobody had opened — and the console drew clear
water over a culvert mouth. The parse is now cached against each file's `(mtime_ns, size)`,
so steady state is one `stat()` per layer per request and the parse is paid again only when
the file changes, which is exactly when the answer could have changed. The **verdict** is
cached, not the document: holding two dozen decoded FeatureCollections would park the whole
fetch in RSS for the session, on a board with a gigabyte of it, to answer a yes/no question.

A file that parses and is **not** a FeatureCollection is unreadable too — a saved service
error body is the common case, and it parses perfectly and contains no features, which is
the empty-canal lie arriving by the one route a JSON parse cannot catch.

`unreadable` is deliberately kept **out of** the `failed` list rather than folded into it.
Both are go/no-go and both are quoted to the operator, but the sentences are opposite:
*absent* is "you never downloaded this — go and get it", and that sentence over a corrupt
file sends somebody to re-run a fetch that already ran and will be refused by the very card
that broke it. *Unreadable* is "what you downloaded is not usable — delete it and re-fetch
while there is still internet."

### 25.7 What entitles a run to delete anything

The sweep exists so that a layer withdrawn by the Trust, or dropped by flipping
`NAV_CRT_RESTRICTED` to `skip`, does not sit on the card being served as current with
nothing in the new index describing it.

It used to build its keep-set purely from what the current run fetched. **A run in which
nothing downloaded therefore deleted every layer file on the card and returned `ok: true`,
exit 0.** That is not a corner case: it is a bootstrap started after the hotspot has
already dropped — the exact condition this module exists to get ahead of — and it destroyed
a complete offline hazard card, for the water a sub was about to go into, while telling the
operator it had worked.

So a sweep is a **refresh**, and it may only take away what it has put back. Two gates:

1. The run must have **written at least one layer**. A fetch that retrieved nothing has
   replaced nothing.
2. It must have **accounted for** — written, or left out by decision — at least
   `_SWEEP_FLOOR = 0.75` of the layers that were on the card when it started. That fraction
   is the only thing that separates *"the Trust no longer publishes this"* from *"we could
   not ask"*, and a half-connected run produces the second while looking exactly like the
   first. Three quarters: one dead service in twenty-six is a bad afternoon at the Trust,
   twenty dead is a bad network, and only the second must freeze the card.

Even then, a file is removed only for a **positive** reason: this run's configuration left
that layer out on purpose, or discovery — which gate 2 says actually ran — no longer lists
it. *"The fetch failed"* is not such a reason. Last month's sluices are worth incomparably
more than no sluices; they are kept, dated with the fetch that really produced them, and
the console draws them and says how old they are.

A layer file with **no readable provenance beside it** is kept and named as *unaccounted*
— never deleted, and never listed as a layer either, because nothing can say what is in it
or when it was fetched. Inventing an entry for it would be this module doing precisely what
it refuses to let a truncated ArcGIS page do.

**Success is not "the command finished."** It is: something was downloaded, and every layer
that was on this card at the start is either refreshed or gone by decision. Anything else
returns `ok: false` with the reason spelled out, and both `crt._main` and `nav/cli.py` exit
1 — because the moment to learn that the card is thinner than you think is while there is
still internet to fix it.

The regression that holds this shut is the dropped-hotspot run: a whole card in place,
`_http_get` failing every request, and the sweep required to remove nothing, carry every
layer forward with its own fetch date, and exit non-zero with the error text naming what the
console will actually draw.

### 25.8 The licence is read, never assumed

Three answers, kept apart: **yes** (OGL), **no** ("Internal use only"), and **cannot-tell**
— a licence that names real terms which are not quoted in the item metadata. Only the first
two are conclusions; the third is the honesty doctrine applied to a legal document, and
collapsing it into a polite hedge is how an unread licence gets published.

The FeatureServer root's `copyrightText` is an **attribution**, not terms; reading it as a
licence is how every one of these ends up labelled OGL by wishful thinking. So the terms
are fetched from the ArcGIS *item*, and a lookup that fails leaves the licence `null`.

The attribution goes **inside** each FeatureCollection as well as into the provenance,
because a bare collection copied out of that directory has lost it otherwise, and
attribution is the one obligation OGL v3 puts on us. `client/js/crt.js` shows whatever
credit line the file itself carries rather than assuming the standard OGL line covers
everything it was handed.

*The card is not uniformly OGL, and the code is right not to assume it is.* On
`data/crt/gas-street/`: 13 layers OGL, 9 "Canal & River Trust data licence", 2 INSPIRE —
all eleven of those `redistributable: null`, cannot-tell — and 2 that read "Internal use
only" (`towpath-access-points-2022-0`, `moorings-all-0`), fetched under the default
`NAV_CRT_RESTRICTED=flag` as this operator's own safety copy and marked
`redistributable: false` in every record. `NAV_CRT_RESTRICTED=skip` leaves the tree clean
enough to publish.

### 25.9 Three tiers, and the tier is a safety decision

`client/js/crt.js` carries one table (`CRT_LAYERS`) and the row *is* the layer: its tier, its
mark, its keep-away radius, and a sentence saying what it means **for this vehicle** rather
than what it is called. "Weir" is a noun;
*"anything that gets over the sill goes with the water and does not come back, and so does
the tether"* is the fact an operator needs on a wet towpath. Adding a layer is adding a row
there, and there is no second place to remember.

| Tier | Rule | Why |
|---|---|---|
| 1 HAZARDS | always drawn, **no switch** | there is no operator preference that makes it right to hide a lock, a weir, a sluice, a culvert, a tunnel or an outfall |
| 2 OPERATIONS | on by default, toggleable | where you get in, get out, turn round, and what is moored in the way |
| 3 EXTRAS | off by default | worth keeping, not worth the clutter — and *off* means **NOT ASKED**, which is not a claim |

That tier-1 rule is also `docs/playbook.md` §6; the enforcement is here. Tier 1 has no toggle
rendered, `crtIsOn` short-circuits to true, and `crtSetOn` refuses and **logs the refusal** —
a control that does nothing and says nothing is how somebody concludes the hazard layer was
switched off when it never was. Draw order is a safety order: extras, operations, hazards on
top, so a mooring glyph can never sit over a lock. Shape carries the tier before colour does,
for the same reason the leak drop does.

**A layer the table has never heard of** is adopted rather than dropped, and if its name
contains a hazard word (`weir|lock|sluice|culvert|tunnel|outfall|siphon|intake|penstock|
spillway|paddle`) it is drawn as a hazard — with a sentence saying it was classified **by
its name** and not by a rule anybody wrote. An inference, marked as an inference. **One
file, one row**: a second wire layer matching an already-claimed row gets its own adopted
row instead of overwriting the first, because the Trust does publish near-duplicates and
the loser would vanish from a console that had already reported it SHOWN.

**Absence is said on the map, not only in a panel.** The `crt-absent` badge reads
`HAZARD LAYERS ABSENT (n)` — or `HAZARD LAYERS · CANNOT TELL` when nothing could be asked —
because a panel the operator has to open first cannot deliver a doctrine about what an
empty-looking map means.

**There is exactly one weir row, and a second would be a defect.** The org publishes one weir
service, so a companion `overflow_weirs` row has no file behind it — and a tier-1 row with no
file can only ever report ABSENT, which means a **complete, correctly-downloaded card** lights
the loudest alarm on the map on every single dive. An alarm that fires on a healthy vehicle is
an alarm that gets ignored, and the one it teaches you to ignore is the one that means you are
missing hazard data. The relief-weir names ride as aliases on the single row.

### 25.10 No flow is ever shown

CRT publish **no flow measurement of any kind**. Every current claim on this console is
therefore an inference from where the structures are, and every hazard mark says so in its
own words — the clause is appended in **one place** (`crtWhat`), not typed into seven rows,
because a clause that has to be remembered seven times will be missing from the eighth. The
`flowProxy` flag exists for the row that is *not* a hazard and still talks about water
moving: FEEDERS shipped stating a current as fact, over a dataset that publishes none.

The standoff distance is labelled as **this console's** convention, chosen by us, and not a
surveyed danger area — the real one may be larger. It is carried in the layer's own words and
**not** by a drawn ring: one ring per tier-1 mark buries the centreline under overlapping
circles, and a ring drawn around everything stops meaning anything.

---

## 26. Depth: two inks that are never mixed (`nominal.py`, `soundings.py`)

There is no canal bathymetry to download. i-Boating's UK layer is proprietary UKHO-derived
coastal data with no canal soundings and a licence forbidding reuse, the Environment
Agency's multibeam is estuarine, EA LIDAR cannot see through water, and the Trust's own
hydrographic surveys are internal. So a depth drawn over this water either comes from
somewhere honest or is not drawn at all, and there turn out to be exactly two honest
sources, worth very different amounts:

| | **NOMINAL** (`nominal.py`) | **SURVEYED** (`soundings.py`) |
|---|---|---|
| What it is | the authority's published **guideline draught** for a class of waterway | the deepest this hull got while the journal showed it **resting on something solid** |
| Who measured it | nobody | this vehicle's MS5837 |
| Direction of error | too shallow, on purpose | too shallow, unavoidably |
| Stored | computed on demand, never cached | `data/soundings/<area>.json` |

**How they are drawn apart is `docs/playbook.md` §3**: provenance is carried by texture, not
by hue, so the twelve depth bands can go on meaning only "how deep".

They are stored apart, served apart (`/api/areas/{a}/depth/nominal` and `…/depth/surveyed`)
and drawn apart. The measured one wins on screen, but it wins as a **different layer, with
its own name on it** — an estimate must never dress as a measurement, and a measurement must
not be diluted by an estimate averaged into it. `data/soundings/` is explicitly *not* a
candidate input to `nominal.py`.

### 26.1 A guideline draught is a floor on the depth, not the depth

"Maximum draught 3 ft 6 in" means the authority believes a boat drawing 1.07 m can pass, so
the bed is **deeper** than 1.07 m by a margin nobody publishes. Reporting the draught as
the depth understates the water — and that is the direction this file deliberately errs in,
because the vehicle's hazard is hitting the bottom: a nominal that is too shallow makes an
operator cautious, and one that is too deep makes them confident. The understatement is
stated in `basis` on every feature rather than silently baked in, so nobody later
"corrects" it by adding an invented clearance.

**And it shoals.** Every figure is mid-channel. A narrow canal is a shallow trapezoid: the
mid-channel depth holds for a few metres and then the bed climbs to the bank, where there is
often less than half of it plus whatever has been thrown in. `shoals_to_banks` is true on
every section and the sentence goes into the title, because a depth drawn as a flat wash
over a water polygon looks exactly like a claim that the edge is as deep as the middle.

The word NOMINAL is stamped in **five places** — the collection, every feature, `title`,
`aria_label`, and `basis` — plus three deliberately redundant booleans (`nominal: true`,
`measured: false`, `is_survey: false`). The layer travels through a renderer this file
cannot see; a label that exists in one place is a label one refactor away from being
dropped, and all three booleans being wrong at once takes intent.

Four rules keep the figure honest:

- **The gauge outranks the class.** `waterway=canal` says a canal; the Trust's own
  `sapwidth` = Narrow/Broad says *which* guideline applies, and it is the Trust saying it.
  Which attribute chose the row is reported (`class_source_field`), and where the figure
  itself came from is reported separately (`depth_source`, `depth_source_field`) — "1.07
  from the layer's `max_draught`" and "1.07 from a table in a Python file" are worth very
  different amounts to the person deciding whether to dive, and a bare 1.07 cannot tell them
  apart. The hand-typed table says in every row that it was hand-typed.
- **Not-navigable withdraws the guidance entirely.** `sapnavstatus` reads "Fully Navigable"
  on a working pound. Anything else — drained, closed, partly navigable — comes out with
  **no** nominal depth and the status attached, because a confident metre of water drawn
  over a pound that may have none is this layer's worst available failure.
- **An implausible value is refused, never clamped.** Outside 0.1–40 m it is a unit error or
  a corrupt attribute; 3.5 read as metres instead of feet triples the water. A clamp turns a
  wrong number into a plausible one, which is the failure this project is named after. The
  refusal is kept and reported (`refused_attributes`), because a `max_draught` column
  arriving unparsed is a downloader bug that would otherwise present as the whole area
  quietly falling back to the table.
- **A river gets nothing.** CRT publish river draughts per navigation and per reach, not per
  class, so there is no class-level figure to quote and the section comes out with a null
  and the reason attached rather than an average nobody published.

**Computed, never cached to a file.** The inputs are two files on the same disk and the
arithmetic is a table lookup per section, so the only thing a cached copy could add is the
chance of being stale — a nominal layer still describing the centreline that was on the card
three area-downloads ago, with nothing on it to say so. `service.py` memoises it against a
**signature of the files it is computed from**, not a timer, for the same reason: a timer
has to choose between rebuilding work nobody asked for and serving a depth layer that
describes the card as it was ten minutes ago.

Absent stays absent: no waterway geometry at all returns `None`, which the endpoint turns
into an `AbsentLayer`, never an empty FeatureCollection. *"No water mapped here"* and *"no
depth guidance here"* are different claims and neither of them is *"the canal is not
there."*

### 26.2 What counts as bottom evidence

The MS5837 measures the depth of the **sub**. Nothing aboard measures the depth of the
**bed** — there is no echosounder and no altimeter. So a depth sample is evidence about the
bed only where there is evidence the sub was *on* it.

The sub's only vertical control is a syringe: filling it makes the sub heavier, and heavier
means it sinks. So the signature is

> the sub was **descending**, then **stopped** descending, while the syringe was **still
> taking on water**.

Something that is not buoyancy is holding it up, and the only thing down there is the bed.
The awkward twin — reaching neutral buoyancy and hovering — is exactly what the *still
filling* clause excludes, because a hovering sub handed more ballast has to go down.

In practice a run must clear five rungs: depth flat within 0.05 m (the same band
`calibrate.py` calls settled, on purpose, so the two tools cannot disagree about whether a
hold was a hold), for at least 3 s **and** at least 8 samples (so an unexpectedly slow log
rate cannot turn two readings into a "steady" stretch), with at least 0.10 of the calibrated
stroke going *in* during the hold, deeper than 0.30 m, and preceded by a descent of at least
0.30 m within the last 20 s. That last one is what separates the bed from the surface: a sub
bobbing at the surface with the syringe filling is a textbook contact — depth flat, ballast
climbing — while the hull is in the air.

**`MIN_FILL_RISE = 0.10` is a placeholder, like every other pre-hardware constant here.**
Nobody has watched a real hull land yet. If the first dives report equilibrium holds as
touchdowns, raise it; if genuine landings are missed because the fill finished before the
sub arrived, the fix is the **procedure** — arrive with the fill still running — not a lower
threshold, because lowering it trades away the one thing this file exists to protect.

**Rejected, so nobody re-adds them:** depth simply flat (that is the settled hold
`calibrate.py` fits the ballast curve to — equilibrium, at any depth); ballast at full with
depth flat (*stopped sinking* and *landed* are different claims); `snagged` (high thrust
with no measured speed means pinned on *something*, and a lock gate or a bridge pier at
mid-water pins just as well as the bed does — the depth then belongs to the obstruction); a
static pitch/roll offset (real when a hull settles on a slope, but the trim swings with the
ballast anyway, so it cannot carry the claim on its own); accel spikes (a bump is a bump; a
wall is also a bump).

A null in **either** channel ends a run rather than being stepped over. This is a claim
about a whole stretch of time, and a hole with the ends closed up would read as one
continuous hold on the bed while the sub was, for all this file knows, halfway to the
surface.

Contact detection is deliberately **not** gated on `armed`, unlike `calibrate.py`'s
segments: a sounding is a pressure reading taken while the hull is on the bed and the
thrusters have nothing to do with it. Gating on armed would throw away exactly the quiet
minutes when the sub is sitting still on the bottom doing the work it is for.

### 26.3 Why every cell is a lower bound

Three errors, and they all have the same sign:

- the pressure port sits **above the keel**, so a landed sub reads short of the bed by its
  own draft;
- it may have landed on silt, weed, a sunken trolley or a fallen branch, all of which stop
  it above the hard bottom;
- canal levels move with rainfall and lock use, so the surface it is measured from is the
  surface of **that day**. There is no vertical datum here, and two dives a month apart can
  disagree by a hand's width.

The bed is **at least** this deep and may be deeper. A lower bound is the shape of answer
that is safe to be wrong in — it under-promises clearance. Dressed as a measurement it would
do the opposite. So the quantity is *named* `lower_bound_m` in the store and on disk, every
cell also carries `bound: "lower"`, every feature carries a `what` sentence spelling it out
(a file-level note does not travel with a feature that gets picked up and drawn on its own),
and `service.py` reads the quantity's name **out of the store** rather than hardcoding it —
that rename has already happened once, and a serving file that went on publishing numbers
under a name nothing had checked would be worse than the break.

**The temptation this refuses.** Every depth sample is technically a lower bound on the bed
beneath it: if the sub was at 1.2 m and floating free, the bed there is at least 1.2 m.
Binning all of them would produce a full, plausible, technically-true map that would be read
as *"the canal is 1.2 m here"* when it is 2.5 m. A bound is only worth recording when it is
**tight**, and contact with the bottom is the only thing aboard that makes it tight.

Cells absent from the file are **UNSURVEYED**, and the file says so in its own header:
absent is not shallow and it is not zero. A survey with no cells at all still writes a file
that says *why* it is empty — *"nothing has been surveyed here"* and *"this water has been
surveyed and there is nothing in it"* are the same empty list with opposite meanings, and
only the second is safe to draw as clear water.

### 26.4 Why the bins are longitudinal

Position error here runs to metres and in places exceeds the canal's half-width, so an
(x, y) raster would draw cross-channel structure the navigation cannot support — false
precision, and the prettier it looks the more it lies. A canal is a 1-D object: samples are
projected onto the CRT centreline with `nav/snap.py` (the one point-to-polyline projection
in this codebase; `soundings.py` picks the nearest of several disjoint lines and does not
write a second projection) and binned by **distance along** the channel. Cross-track
information is thrown away rather than invented, and the cross-track distance survives as
provenance (`offset_m_max`).

Cells are 5–10 m, default 8 — a little over four sub-lengths and comfortably wider than the
along-track error of a short dive. A value outside that range is **refused, not clamped**: a
1 m cell would imply a longitudinal precision the dead reckoner does not have, and a 100 m
cell would average two pounds and a lock together, and either would still look like a
perfectly good map.

Two things are deliberately excluded at binning time. A sample whose position was **HELD**
because the compass died (`no_heading`) is dropped — `divelog.py` writes that flag precisely
so a run of identical coordinates cannot be read as a sub sitting still, and a sounding filed
at a held position is filed wherever the compass died. A sample more than
`snap_max_dist_m` (25 m, the estimator's own snapping limit, so the two agree about what
"on this waterway" means) from the centreline is dropped too: that is a marina arm, a lock
chamber or a drifted track, and binning it longitudinally would file a real sounding under
the wrong stretch of canal. Both are counted and reported.

The **lines are kept separate**, unlike `service.py`'s flattened centreline. Flattening is
right for *"how far am I from the water"* and wrong here: the join between two disjoint ways
becomes a phantom segment, and distance-along measured through it is a distance nothing
travelled.

The operator's post-hoc drag of a track is applied here, because `divelog.py` keeps raw
x/y in the `.jsonl` and applies the translate+rotate only when writing the `.geojson`.
Ignoring it would put every sounding from that dive at the offset the operator had already
corrected — and the whole reason they dragged it is that they knew where it really was.

### 26.5 How dives accumulate

Each cell keeps a **per-dive** breakdown, and everything above it is derived fresh on every
merge. That is what makes re-running a dive **idempotent** rather than double-counting it: a
tool that double-counts turns *"how many samples back this cell"* — the provenance a reader
weighs the number by — into a count of how many times somebody ran the command.

Within a cell the **deepest** contact wins, not the mean. For a lower bound that is the most
the cell knows: averaging a 1.4 m touchdown with a 0.6 m touchdown on the same trolley
produces 1.0 m, a number nothing measured and a bound weaker than one already in hand. A
later, shallower dive therefore cannot pull the bound back up; it is recorded, it adds
samples and provenance, and the bound stays.

A store refuses to accumulate three things, all silent if unchecked: a different **area**, a
different **cell size**, and a different **centreline fingerprint**. Cell 47 means "376–384 m
along line 0 **of this geometry**"; re-download the area, get the ways back in a different
order, and cell 47 is somewhere else entirely while every old sounding still claims it. The
fingerprint is rounded to 1e-7° (~1 cm) so a re-serialised copy of the same geometry still
matches.

Confidence that was never recorded stays **null**, never 1.0. "Perfectly trusted" is the
strongest claim in the system and the last thing an unrecorded fix is.

### 26.6 The refusal names the rung

A tool that always produces an answer is worse than no tool. `_why_no_contact` walks a
ladder and each rung sends the operator to a **different** job — fit the part, home the
syringe, fly the dive differently — so collapsing them into "no soundings found" sends them
to the wrong one, which is the same class of harm as inventing a number.

- no samples at all → the journal is empty or was truncated before the first
- no `depth_m` column → this journal predates depth logging; the MS5837 may have been fine
- `depth_m` present and null throughout → the pressure sensor never answered once
- no `ballast` column → the raw control channels were not logged; the depths are real and
  are not lost, but nothing in the file can tell a landing from neutral buoyancy
- `ballast` present and null throughout → **the stepper was never homed**, so the syringe
  had no position to report all dive. *Run `ballast_home()` before the next dive.*
- flat runs but none with a fill → arrive on the bed with the fill still running
- ...and the rest: too shallow, no descent into them, too brief

Contact samples that were found and then **discarded for position** get their own refusal,
which says the depths were real and that this journal cannot say where they were taken. A
dive whose sensor died halfway keeps the landing it measured, invents no second one, and
reports the column as `partial` above the numbers — a survey that quietly covers half the
canal it appears to cover is a substituted zero in a better suit.

`python -m nav.soundings --selftest` is the gate on all of it: two landings recovered at the
right cells and depths from a synthetic bed the maths was not told about, a deeper dive
combining, a shallower one not weakening the bound, a re-run replacing rather than
double-counting, and every refusal above reached by a log built to earn it.

### 26.7 What the renderer had to be taught

The claim survives the trip only if the drawing does too, and four separate bugs here all
had the same shape: a layer reporting SHOWN while painting something other than what it
described.

- **`properties.cell` is an index, not a length.** Reading it as a length drew cell 0 as a
  point and cell 1 one metre across on a survey binned in tens. `_crtCellM` uses `cell_m`,
  then `to_m - from_m`, then the collection's own bin width.
- **`cell_m` had two names on one document.** The collection published `cell_length_m` while
  the features carried `cell_m`; the client found nothing and fell back to a hardcoded 5 m,
  so a survey binned in tens drew at half size and the sounded water looked like water
  somebody had not coloured in. Both names now ride on the collection, assigned from the
  same local.
- **A nominal section is not a cell.** `nominal.py` passes the waterway section geometry
  straight through — hundreds of metres to a couple of kilometres of cut each — and an early
  renderer handed it to the *cell* path: 6.3 km of canal came out as nine 5 m squares, 0.7%
  of the channel, under a row that said the layer was drawn everywhere. Line sections are
  stroked along their own geometry now.
- **The hatch tiled to nothing.** One diagonal crosses a repeat tile at exactly one corner
  pixel, so the pattern rendered empty and the nominal cells looked identical to the
  surveyed ones — losing the *published versus measured* distinction the texture exists to
  carry. Three diagonals, at -s, 0 and +s.
- **`_crtDepthOf` read only `depth_m`.** Both producers name the field after what it
  *means*, so every cell fell through to the no-depth grey and the twelve-colour scale
  switched itself off with nothing anywhere saying the numbers had been dropped.

The band drawn along a nominal section is honest about which half of it is a fact: its
**length** is the section's own published geometry, its **width** is a drawing convention of
this console's (7 m, about a narrow cut) because nobody publishes how wide the channel is.
Same family as the standoff ring, and the row says so in its own words.

This session's own soundings are binned live and drawn with the **same** surveyed treatment
— same quantity, same sensor — but counted **apart** in the panel row, because no dive log
has been written for them yet and a row that added them into one number would claim the Pi
holds a survey it has never been sent.

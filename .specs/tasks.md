# Neptune — Tasks / Changelog

Newest first. Each entry names the defect, not just the change, because on this project the
*why* has repeatedly been the expensive part.

Legend: ✅ done and verified on hardware · 🧪 verified in test only · ⚠️ open

---

## Navigation follows the vehicle

- ✅ **`16dba9f` — Log the nav sensor source and its simulated-ness separately.**
  Startup printed `sensors=sim` while `VehicleSensorSource` was in use, because `is_sim`
  reports whether the *vehicle hardware* is mocked, not which source feeds nav. That
  conflation is part of why the scripted-path bug survived so long.

- ✅ **`b17434f` — Make navigation follow the vehicle, not a scripted route.**
  `NAV_SENSORS` defaulted to `sim`, a scripted path with preset heading legs that ignores the
  operator entirely; `NavService` had no reference to the ROV at all. Added
  `VehicleSensorSource` (heading from hardware, depth from pressure, speed from thrusters) and
  bound it to the live `RovState`. Speed now comes from actual thruster output `(left+right)/2`
  rather than commanded throttle, so a **disarmed** sub no longer advances without turning.
  *Verified on the Pi:* steer RIGHT `284.0 → 301.9`, steer LEFT `301.9 → 278.5`, straight
  `4.20 m` in `6.1 s`, auto dive log `dive-20260805-014654.jsonl` written unasked.

## Diagnostics

- 🧪 **Make the log answer questions it was not prepared for, and readable mid-dive.**
  Logging was per-call-site, console-only, and gone the moment devtools closed - so the state
  of the vehicle five minutes ago was answerable only if someone had thought to log it. And a
  fault underwater has to be diagnosed while the vehicle is still in the water; leaving the
  console to open a file is not an option.
  `LOG` is now a bus: console + a bounded ring + the on-disk session log, with levels.
  `wire.js` wraps `fetch` and `WebSocket` once at load, so every request, response, frame,
  close code and failure is recorded with outcome and duration without any caller opting in.
  A `4xx`/`5xx` is logged as a WARNING, not a success - `fetch` resolves for those, which is
  exactly how a failed request gets read as a working one - and an abort is distinguished from
  a fault.
  High-frequency categories are coalesced rather than dropped: the suppressed count rides on
  the next line, and a **sweep** flushes the tail of a burst that stops. Without the sweep,
  "telemetry was flowing and then it wasn't" was the one case that vanished silently - found
  by a test asserting the count was reported, which failed.
  CONFIG -> **LOGS** opens it live: centred, NOT full screen, over a visible background, with
  tail-follow, a filter and ALL/WARN+/ERR chips. Rows are appended rather than re-rendered,
  because at 20 Hz a redraw per line would make the log viewer the thing slowing the console
  down.
  MARK EVENT, EXPORT LOG and DIVE LOGS are gone from CONFIG - all three were controls for
  things that now happen by themselves. `openDiveLog()` stays on the console API rather than
  being deleted: removing a control should not silently remove the capability.
  *Verified in a real browser, 33 checks: send/success/failure all logged, the log's own
  writes NOT logged (no feedback loop), WebSocket wrapper preserving constants, 500 events
  coalescing to 1 line with the count reported, and the overlay measured centred at 1000x518
  in a 1280x720 viewport with tail suspend/resume on scroll.*

## Top bar and stills

- 🧪 **Stop the top-bar metrics rendering on top of each other.**
  Twenty tiles were laid out `flex:1 1 0` — equal columns filling the bar. Each got 48px
  whatever it held, `min-width:0` let the box shrink below its own text, and
  `white-space:nowrap` spilled that text over its neighbours. Measured at 1280px: **13 of 20
  tiles overflowed and 12 pairs of text collided** with live values. With `--` in every field
  it looked fine, which is why it survived — it only breaks once the Pi is attached.
  Tiles are content-sized now and cannot shrink. Also reclaimed width that carried no
  information: `INCANDESCENT` (106px, introduced by the camera-defaults work) is abbreviated
  in the HUD with the full value in the tooltip; the origin tile dropped `SET ` and its spaces,
  which repeated what the tile's own label and colour already said; and the header ran *under*
  the EXIT button, truncating the last tile. *Verified in Chrome: 20 tiles, one row, 0
  collisions, 0 escaping the bar, with the worst-case value in every field simultaneously.*

- 🧪 **PIC keeps a topside copy, so a camera you cannot recover is not the only copy.**
  The camera's JPEG goes to an SD card inside a vehicle that is in the water, and with no
  camera there was no still at all — so PIC did nothing in sim. It now also grabs the current
  view into IndexedDB *and* as a file download, independently of the camera: either half can
  fail without the other, and the toast reports each rather than claiming both. The frame comes
  from the live video when there is one and **the map otherwise** — in blind nav the map *is*
  the view, and a still of a black `<video>` would look like the camera worked. Telemetry
  travels with the image so it can be placed in the dive afterwards.
  Two bugs found while testing it: the local grab ran *after* the camera's ~2 s blocking
  capture (saving a frame from well after the press), and second-resolution ids meant two
  presses in the same second **silently overwrote each other**. *22/22 checks in a real
  browser, with no camera present.*

- 🧪 **Stop a version bump from hanging the boot.**
  Adding the `stills` store raised the IndexedDB version, which introduces `onblocked`: an
  older connection held open by a second window fires *neither* `onsuccess` nor `onerror`, and
  `boot()` awaits `STORE.init()` — so the whole console never starts. Reproduced, then fixed:
  every branch settles, a timeout backstops the rest, and `onversionchange` means this window
  never blocks the next upgrade. Losing the database now costs persistence, not the dashboard.

- 🧪 **Record the screen too, and give every artefact one home and one name.**
  The camera records what it sees, onto a card inside the vehicle. Nothing recorded what the
  OPERATOR saw, so a dive had no topside account of itself. REC now drives both, reported
  separately so a missing camera or a missing ffmpeg does not stop the other.
  The screen half is `gdigrab -> libx264 -crf 23 -preset veryfast -an`, run by the launcher -
  the same trade as re-encoding a screen recording with `-vcodec h264` afterwards, done once
  and live. **~1.4 MB/min** measured. Stopping writes `q` to ffmpeg's stdin rather than
  killing it, because a hard-killed MP4 has no moov atom and will not play. The AMD GPU
  encoder is deliberately NOT used: it would be lighter on CPU, and this handheld has an
  unresolved kernel fault under sustained GPU load.
  Everything a session produces now lands in `client/navigation_logs/{images,videos,logs}`
  as `{mode}_{iso}`, with a **Neptune Recordings** desktop shortcut, instead of being
  scattered through the browser's download folder.
  *Verified: 9 launcher-handler checks (append does not overwrite, binary bytes survive,
  traversal contained), 13 with real ffmpeg (H.264 High, 1920x1080, zero audio streams,
  ffprobe parses it so the moov atom was written, a second start refused), 19 client checks.*

- 🧪 **The session log no longer needs remembering.**
  There was an EXPORT LOG button. A log you have to remember to save is a log you find
  missing exactly when you needed it - the same reasoning that made dive logging automatic.
  It now starts with the session and appends to disk every 5 s as it happens.
  Events are teed at `REC.log()` rather than read back out of the IndexedDB ring, because the
  Pi upload deletes from that ring and a disk writer reading the same rows would race it.
  Timer-flushed rather than written at shutdown on purpose: the kernel fault on this handheld
  takes the machine down with no unload event, so a log held in memory until exit is lost in
  precisely the sessions worth reading.

- 🧪 **Only the first PIC of a session ever reached the disk.**
  Chrome permits ONE automatic download per origin and then blocks the rest, storing the
  decision: the profile had `automatic_downloads: 2` recorded against
  `http://localhost:8080`. In an `--app` window there is nowhere to show the prompt, so every
  press after the first vanished silently — the still was in IndexedDB, the file never
  appeared, and the toast said "saved locally" because as far as the page knew it had been.
  The launcher already had the bytes, so it writes the file itself now
  (`/__screenshot?save=<id>` -> Downloads) and the browser is out of the path. The name is
  sanitised to a bare filename so nothing the page sends can steer the write elsewhere.
  `AutomaticDownloadsAllowedForUrls` is set too, for the composite fallback that still uses
  `<a download>`. Same commit: the toast now names the file it wrote, since the whole failure
  was invisible from the console.
  *Verified: five presses in a row against the launcher's real handler produce five distinct
  files on disk; path traversal is stripped; and client-side, five presses request zero
  browser downloads with the launcher present, five without it.*

- 🧪 **Make PIC take an actual screenshot, which is what was asked for.**
  Two rounds of fixing the wrong thing. A canvas composite can only ever reach the video and
  the map — it cannot see the instrument bar, the control rail or the banners, and the
  basemap taints it — so no amount of work on that path produces "the screen as I see it".
  The launcher already serves the page from localhost, so it takes the capture instead:
  `GET /__screenshot` returns a real `CopyFromScreen` PNG. Same-origin, so it does not taint
  the canvas, which incidentally brings the basemap back too. Stored unmodified — no caption
  over a screenshot.
  `SetProcessDPIAware()` is called first: at 1920×1080 / 150% a DPI-unaware process is told
  the screen is 1280×720 and captures only its top-left corner.
  *Verified end to end against the launcher's REAL handler (extracted from `neptune.ps1`, not
  copied): 200, `image/png`, `X-Screen: 1920x1080`, decodes at 1920×1080. Client side, 11/11
  with the pixels proven to come from the endpoint, plus the 404 / 500 / wedged-endpoint
  fallbacks — the last bounded at 4 s so PIC never hangs.*

- 🧪 **Make PIC work on the map, which is where it actually failed.**
  Shipped broken: the first version captured fine in a headless test because no satellite
  tiles had loaded, so the map canvas was clean. On the real handheld, with imagery on
  screen, `toBlob` threw `Tainted canvases may not be exported` — tiles are deliberately
  loaded without `crossOrigin` and cached as opaque responses so the OFFLINE map works, and
  the code even said so: *"we never read pixels back, so a tainted canvas is fine"*. That was
  true until PIC read pixels back.
  Making the tiles CORS-clean would have fixed the screenshot and broken the offline map in
  the field, so instead a capture re-renders the frame without the imagery and marks the
  record `basemap:false`. Every vector layer survives; the video path never taints and was
  never affected. Also fixed the caption being clipped off the ~198px radar canvas, which was
  losing exactly the `SIM` / `NO BASEMAP` markers that say what the image is.
  *Reproduced the taint first with a cross-origin image, then verified: 24/24 checks
  including a real `MediaStream` for the live-feed path.*

## Camera configuration

- 🧪 **Set the camera up for a dive, and stop `preflight()` reporting a check it never made.**
  `preflight()` printed `PowerSaving=OFF (critical) OK` on a camera that then powered itself
  off mid-dive. It wrote `PowerSaving`, read back a property of *that* name rather than
  `Camera.Menu.PowerSaving`, got `None`, and the check `ps == "OFF" or ps is None` scored
  `None` as a pass. Nothing called preflight automatically either, so even the vacuous check
  rarely ran.
  Added `api/camera/defaults.py`: a tiered table of what the camera should be, each entry
  carrying its reason. Nothing is written blind — the write names and valid values are
  unknown for almost every property, and a wrong *name* is accepted with `0 OK` and silently
  ignored, so each setting probes candidate names and values and verifies by re-read. A `722`
  (value refused) and a silent no-op (name wrong) are told apart and drive different retries.
  Results are cached per `FWversion`.
  The two settings that matter most: **`PowerSaving=OFF`**, because the factory 5MIN sleep is
  indistinguishable topside from a tether fault; and **`VideoClipTime` segmented**, because a
  `.MOV` still being written when power is cut is unrecoverable.
  Applied on connect, re-applied whenever the camera returns (a rebooted camera has a wrong
  clock and no RTC), and drift-corrected every 60 s — a loop that doubles as the **keepalive**,
  since the 15 s telemetry poll only runs while a dashboard is subscribed. Slow settings that
  blank the feed are connect-time only and skipped while recording. `LCDPower`, `UpsideDown`
  and `Timelapse` are deliberately left alone and listed as such.
  *Verified against the mock only — the camera is dead, so none of this has met hardware.*
  40/40 unit checks and 24/24 boot checks, including: the probe still finds the setting when
  the emulated firmware uses the opposite naming convention, and reports `ignored` rather than
  success when it honours neither.

- 🧪 **Stop Wi-Fi power save stalling the camera link.**
  `wlan0` *is* the camera, and Raspberry Pi OS enables power management on it by default —
  the radio parks between beacons and the RTSP pull stalls, which topside looks exactly like
  the camera going to sleep. Nothing anywhere turned it off. Added `wifi.powersave 2` to the
  `neptune-cam` profile and `neptune-wifi.service` to re-assert it, because the driver
  re-enables it on **re-association** and the AP drops every time the camera reboots.

## Map geometry and readability

- 🧪 **`c9d5fd1` — Drop the full-screen NO FEED; blind nav is the fallback in every mode.**
  Blind nav had an opt-out: an X, and a tap on the video tile. Both returned the operator to a
  full-screen black rectangle carrying strictly less information than the map it replaced —
  including on a **cold start**, where a feed had never existed and the 4 s debounce (there to
  absorb a WebRTC blip) had nothing to absorb. The opt-out is gone: the X is hidden, the video
  tile is a status indicator, and the feed *returning* is what restores the camera view.
  `blindColdMs` (1.2 s) applies until a feed has been live once.
  *Verified in a fresh profile with no camera:* cold start / after X / after tapping the tile
  all `blind=true banner=block closeBtn=none`, `ERRS=0`.

- ✅ **`d56d90c` — Size the map canvas from layout, not a mid-animation transform.**
  `getBoundingClientRect()` includes CSS transforms and both full-screen layouts animate from
  `scale(.94)`, so the canvas was sized to 94% (1280 → 1203) permanently. The map centre sat
  39 px from the dial centre — the *two parallel lines*. Now `offsetWidth`/`offsetHeight`.
  *Before* `sub vs dialCentre = -39,-20` → *after* `0,0`.
  Same commit: radar and blind-nav zooms retuned so a real track is visible within seconds
  (12 s of driving spanned 20 px; now 101 px).

- 🧪 **`6d994ee` — Give the radar its own zoom.**
  `MAP.scale` was shared by radar, expanded map and blind nav. `collapseMap()` reset it, masking
  the problem; `exitBlindNav()` did not — so the zoom controls added to blind nav silently
  rescaled the minimap for good. Radar now keeps a fixed glance zoom.

- 🧪 **`43eca61` — Fix blind nav geometry.**
  Two self-inflicted CSS bugs: `inset:auto` written *after* `left/top` reset them, flinging the
  dial into the corner at 64vh; then `opacity:0` on `#radar` blanked the whole map because
  `#map-panel` is its child. Also closed the gaps that exposed — blind nav had no zoom controls,
  and a stray tap could call `expandMap()` and zero the throttle.

- 🧪 **`93e28a4` — Add blind nav.**
  With no camera the dashboard showed a black rectangle at the one moment the map is most
  useful. Blind nav is a third layout, deliberately not `expandMap()` (which engages all-stop
  and goes north-up for planning). `MAP.expanded` stays false so heading-up, follow-the-sub and
  live throttle come for free. Debounced both ways.

## Automatic navigation logging (safety)

- ✅ **`78416cb` — Log every session automatically, and make the log survive a crash.**
  Nothing was logged unless someone remembered to `POST /api/nav/dive/start`, and worse, samples
  lived in memory and were written once by `stop_dive()` — a crash lost the entire track. Each
  dive now appends `dive-<ts>.jsonl` as it happens; orphaned journals are rebuilt on next start
  and marked `recovered`; a truncated final line is tolerated. Failing to record never fails to
  fly. *Verified against the real classes, then on the Pi.*

## Origin and location

- 🧪 **`2c81085` — Never prompt for location on open.**
  Requesting on every open re-prompted every launch, because Chrome does not persist a grant
  unless it came from a user gesture. Gated on `granted`; the ORIGIN tile is marked so one tap
  sets it up permanently.

- 🧪 **`8dd4f43` — Take a fresh fix on open without downgrading a better origin.**
  Beyond `originMoveM` it is a different site and gets USE MY POSITION / KEEP; within it, a fix
  is adopted only if no less accurate, so a ±58 m Wi-Fi fix never overwrites a ±8 m tap.
  *(Superseded in part by `2c81085`.)*

- 🧪 **`0073336` — Notice when the origin no longer refers to where you are.**
  A launch point set at home followed the operator to the canal silently. The ORIGIN tile now
  ages and turns amber — which needs neither permission nor internet, so it works in the field.

- ✅ **`d6e124d` — Await the store before reading it.**
  `boot()` called `STORE.init()` fire-and-forget then synchronously read the origin, so the
  saved origin was invisible and a position was requested on **every** boot.
  *Verified:* `ASKED_BROWSER_FOR_POSITION=false` with an origin stored.

- ✅ **`5ce06dc` — Stop launching fullscreen, which was swallowing the location prompt.**
  Chromium suppresses permission prompts in fullscreen, so the prompt could be accepted and
  never stick. The page takes itself fullscreen on first tap anyway.

- ✅ **`875ee59` — Per-browser profiles.**
  One `--user-data-dir` across Brave → Edge → Chrome left Chrome reading a foreign fork's
  profile, out of which it would not honour its geolocation setting.

- ⚠️ **`f506015`, `e03752a` — Chrome geolocation policy.**
  `GeolocationAllowedForUrls` set via `tether-setup.ps1` (policy writes need elevation even in
  HKCU). **Not demonstrably effective** — kept as belt-and-braces only; the fixes above do not
  depend on it. Also fixed: `tether-setup.ps1` aborted entirely when the tether NIC was absent,
  skipping the unrelated location steps.

## Crash and stability (topside)

- ⚠️ **`9ce0233`, `d75d4e6` — GPU/kernel fault mitigations.**
  Every crash logs `LiveKernelEvent 141` (`VIDEO_ENGINE_TIMEOUT_DETECTED`) with
  `DPC_WATCHDOG_VIOLATION (0x133)`. `-SafeGraphics` (software decode) did **not** stop it — a
  fault fired 11 s after Chrome started — so `-NoGpu` takes the browser off the GPU entirely.
  `crash-diagnostics.ps1` enables kernel dumps and raises `TdrDelay` 2 s → 10 s.
  **Root cause unresolved**; `C:\Windows\MEMORY.DMP` (1.9 GB) will name the driver.
  Also in `d75d4e6`: the sub icon now reads red for simulated / green for connected, instead of
  a muted grey in exactly the situation it exists to warn about.

- ✅ **`0e954c7` — Hand back to the simulator when the link dies.**
  `main.js` fell back to sim only when telemetry had *never* arrived, so once the Pi had
  connected even once, losing the link parked the console in `stale` forever — the model stopped
  advancing and every control looked dead while still accepting input.

- ✅ **`e27681e` — Keep every control usable with no vehicle link.**
  Gating vehicle controls on the link meant the whole rail died on the bench. Split into
  `simulated` (interactive) and `gated` (genuinely unavailable).

## Installation and reproducibility

- ✅ **`b48ae76` — Stop `install.sh` mangling the comment explaining the placeholder.**
  Found by checksumming all 41 deployed files against `origin/master`.

- ✅ **`5187ae2` — Make `install.sh` reproduce the working state offline; pin line endings.**
  Added `ipv4.dhcp-timeout` (the last setting that existed only on the machine), end-of-run
  verification that the tether address stuck, and offline operation — apt/pip/git each skip
  cleanly with no internet, which is the normal state on the tether. `.gitattributes` pins LF
  for shell/systemd/YAML/Python and CRLF for PowerShell/batch.

## The big one

- ✅ **`b92d25d` — Tether, video plane, subsystem isolation, topside lockout.**
  Five independent faults that together made the dashboard look dead with the cable plugged in:
  1. **Tether had no addressing.** `install.sh` only *read* `eth0`'s address. A direct cable has
     no DHCP, so `eth0` had no IPv4 at all while the client was hard-coded to a home-LAN IP.
     Fixed point-to-point pair, additive to DHCP, plus `neptune-tether.service`.
  2. **Video could never connect.** go2rtc rejected every WebRTC handshake with
     `request origin not allowed by Upgrader.CheckOrigin` — the signaling socket is cross-origin
     by design here. `api.origin: "*"`. Separately, `go2rtc.yaml` kept a literal
     `<PI_TETHER_IP>` whenever `eth0` had no IPv4 — the exact tether condition.
  3. **The whole control rail died as one blob** on `body.backend-down aside{pointer-events:none}`.
     Replaced with a five-subsystem model and `data-needs`.
  4. **Metrics were fabricated, not mocked.** `NEPTUNE_HW=real` was forced and
     `RealHardware.__init__` could not fail, so the API reported `mock: false` while every sensor
     returned a constant. Added `api/sysinfo.py` — real health from `/proc` and `/sys`, zero
     dependencies, `null` for unreadable rather than `0`.
  5. **Topside could only be recovered by rebooting.** PID-based browser liveness + Chromium's
     process-singleton shut the web server down ~180 ms after launch, leaving a `--kiosk` window
     with no on-screen exit on a keyboard-less handheld.
  Also: blackbox upload was deadlocked (a full batch always exceeded the cap, so nothing ever
  uploaded or was deleted), the ring cap never applied across reloads, WebRTC/nav/camera
  reconnects leaked sockets and stacked timers, and the service worker pinned `js/*` cache-first
  behind a version that was never bumped.

---

## Open

| | Item | Owner |
|---|---|---|
| ⚠️ | **`DPC_WATCHDOG_VIOLATION`** — kernel dumps now enabled; `MEMORY.DMP` needs WinDbg `!analyze -v` to name the driver | hardware |
| ⚠️ | **USB tether NIC drops off the bus** (`Present: False`), needs a physical replug; suspect the hub/port/power path | hardware |
| ⚠️ | **`RealHardware` is a stub** — depth, pressure, heading and pack voltage are simulated; only Pi health is real. `TODO(hardware)` in `api/hardware.py` | firmware |
| ⚠️ | **No GNSS on the Ally** — Wi-Fi positioning needs internet, so the field workflow is tap-on-map. A USB GNSS on the Pi feeding `/api/origin` is the real answer | hardware |
| ⚠️ | **Chrome geolocation policy unverified** — kept as belt-and-braces; nothing depends on it | topside |
| ⚠️ | **Blind nav zoom/dial size are judgement calls** — `radarMetersPerPixel`, `blindSpanM` and the dial size were tuned by measurement, not by driving | field trial |
| ⚠️ | **Nav track unexercised in the field** — needs an origin set at a real site and a dive | field trial |

# Neptune — Tasks / Changelog

Newest first. Each entry names the defect, not just the change, because on this project the
*why* has repeatedly been the expensive part.

Legend: ✅ done and verified on hardware · 🧪 verified in test only · ⚠️ open

---

## Sensor liveness — four rounds

- 🧪 **A sensor that stops answering now says so, and nothing downstream fills it in.**
  This one took **four adversarial review rounds**. Each round fixed something real, each
  round's verification found the next layer, and **two of the four introduced a regression
  while fixing something else**. The rounds are written out below rather than collapsed
  into a summary, because the way this defect kept surviving is more useful than the fix.

  **The defect.** Every reading on this vehicle is served from a cache that a background
  sensor thread fills. A cached value is only a measurement while the chip behind it is
  still answering — and nothing checked. The MS5837 stops answering at 4.33 m; every later
  attempt raises; the cache keeps 20.85 psi; sixty seconds of dead-bus ticks change
  nothing. `rov.py` turned that into `depth=4.33` in every frame at 15 Hz, the client
  stamped each arriving frame as fresh, and the console painted a confident, fully
  colour-banded 4.3 m while the sub descended to 8. Every check anyone had written asked
  *"did the chip come up?"* — and the chip **had** come up.

  **Round 1 — the null did not exist.** Both backends returned a literal `0` when they had
  nothing, so `Telemetry.mag_cal` was `Optional` in name only and the client's `NO COMPASS`
  flag was unreachable code on every real hull. Made the hardware layer able to say
  cannot-tell at all.
  *Regression introduced, and caught in round 2:* adding the `nomag` state to the heading
  flag table left the hand-written list of "which classes mark the number" unextended, so
  `NO COMPASS` badged itself and left the bearing looking like every trustworthy number on
  the bar — **the badge as the only carrier**, which is the one thing the shape-not-colour
  rule forbids. Caught by a review that asked what the *number* looked like rather than
  what the badge said. Fixed by deriving that list from the table, so a flag added tomorrow
  cannot repeat it. In the same pass: `GYRO` was being shown on a hull with **no IMU at
  all** — the filter reports `gyro_only` for the trivial reason that it reads `mag_cal` as
  0 and stops trusting it — so a badge meaning "coasting on the spin sensor, deliberate,
  not a fault" was promising a gracefully-decaying bearing on a vehicle with no spin sensor
  either. `NO IMU` now outranks `GYRO`.

  **Round 2 — "absent" still meant "never wired".** Everyone had reasoned about the sensor
  that was never fitted; nobody had reasoned about the sensor that worked for four minutes
  and then stopped, which is the one that matters because it is the one that leaves a
  *number* behind. There was also no way to *make* one stop: on the bench every reading is
  healthy from power-on to shutdown, so the path was unreachable.

  **Round 3 — liveness became a first-class signal.** `DeviceHealth` per chip, faulted on
  either **consecutive raises** *or* **silence** (nothing has to raise for a device to stop
  answering — a conversion state machine that never reaches its collect stage, a driver
  that returns without writing, a sensor thread that ended, all leave the cache frozen and
  raise nothing). Never-answered is faulted. Pure and clock-injected, so the rule runs on a
  bench in microseconds. `MockHardware._kill_sensor()` / `_revive_sensor()` make the
  failure reachable with the simulation still running underneath, so a test can assert the
  readout neither followed the water down nor sat frozen. `Telemetry` fields went
  `Optional` and gained `sensor_faults`, naming which chip — the same verdict the nulls are
  computed from, read twice, so a blank gauge and a named chip cannot contradict each other
  on screen. The client learned a third shape: `?` in amber with a wavy underline for
  cannot-tell, kept deliberately distinct from the `--` of a dropped frame.
  Also in this round, and the same rule applied to two non-chips: leak detection was the
  **only** reading with no liveness gate at all — the probes were sampled inside the same
  try-block as the I²C ticks, so one unexpected raise from a bus chip stopped them being
  sampled entirely while `read_leak()` went on answering `NORMAL` at full rate. `NORMAL` is
  a positive safety claim, so it now needs a probe that was actually read; a fourth state,
  `UNKNOWN`, carries the rest. Wet still outranks cannot-tell — a latched `FLOOD` never
  decays to `UNKNOWN`, because the sampler dying does not un-establish water that has
  already arrived. And navigation going quiet stopped looking like good news: `snagged` and
  `gyro_only` default to the two *reassuring* answers, so at the instant nav died the
  console got **quieter** — a standing snag warning cleared itself and the GYRO badge went
  out. `False` now means "nav looked and says no", `None` means "nav cannot tell".
  *Regression introduced, and caught in round 4:* `leak_probe_fault()` was left reading the
  raw probe pins while `read_leak()` read the debounced latches — two rules on one pair of
  probes. A launch splash that the 5-sample debouncer correctly threw away still reached
  the raw read, so the vehicle reported `NORMAL` and *"probe wiring is broken"* **in the
  same frame** and failed the pre-dive check over one wet droplet. Caught because the two
  statements contradicted each other on one screen. One droplet cannot be both nothing and
  a fault; both now answer from the same latched evidence, and the failure the check exists
  for — a probe that reads wrong *continuously* — survives intact.

  **Round 4 — one unassigned file defeated the other five.** `api/nav/sensors.py` had no
  owner, and it coerced every cannot-tell straight back into a plausible number:
  `_readback(hw, "read_heading", 0.0)`, `_readback(hw, "read_pressure", surface_psi)`,
  `_readback(hw, "read_mag_cal", 0)`. The helper's own docstring says *"Neither case invents
  a number"* — and the **default it is handed** is an invented number, so the docstring was
  true about the helper and false about every call. `fill_nav_fields()` in `api/main.py`
  then stamped nav's heading unconditionally over the null `rov.py` had correctly sent.
  Reproduced end to end against the real `NavService`: `rov.py` sent
  `heading=None card=None mag_cal=None faults=['bno085']`, and the frame reaching the client
  read `heading=0.0 card='N'` — **a confident bearing of DUE NORTH beside a NO COMPASS badge
  and a "bno085 not answering" fault, all on one screen.** Worse than the frozen bearing
  round 3 set out to fix: a frozen bearing is at least a direction the sub once pointed. The
  radar is heading-up, so the whole map swings north and the dead reckoner runs the track
  north; the `0.0` depth from the same path is written into the permanent dive log.

  **The doctrine this turned out to be about**, now written down in `.specs/design.md` §24
  and normative as **R7.6**: *a signal whose sensor is absent shows cannot-tell, never a
  plausible number* — where **"absent" includes "was here and stopped"**, and where **a
  cannot-tell default that is itself a measurement is not a cannot-tell**. `0.0` heading is
  due north. `0.0` depth is the surface. `mag_cal` 0 is *"a compass answered, and it says it
  is uncalibrated"*. Leak `NORMAL` is a positive claim that the hull is dry. `snagged` false
  is a positive claim that the sub is moving freely. The test is not *"is this default
  harmless?"* but *"would an operator act on it?"*, and for all five the answer is yes.
  Two structural lessons, both now design rules: **the chain is only as honest as its
  weakest link** — six files have to preserve the null and any one that coerces destroys the
  property for the whole system, silently, while every test on either side still passes; and
  **a file on that path with no owner is a defect in the change, not an omission from the
  review**. What nobody had, through all four rounds, was one check that puts a dead sensor
  in at the hardware end and asserts what comes out at the client end. Every layer passed its
  own tests the entire time.

  *Verified 2026-08-07 by running, on the ROG Ally.* Both suites green: client
  **295/295 in 114 s across 12 suites**, api **147/147 in 1 s across 4 suites**; the api
  runner in a python with no `pydantic` correctly reports `100/103 across 2 of 4 suites`,
  verdict INCOMPLETE, exit 2, rather than a reassuring total.
  Against `MockHardware` + the real `RovState`: healthy frame
  `heading=284.0 card='W' mag_cal=3 depth=3.59 pressure=19.8 batt=8.3 current=0.75 faults=[]`;
  after `_kill_sensor` on all three chips,
  `heading=None card=None mag_cal=None depth=None pressure=None batt=None current=None
  faults=['bno085','ina219','ms5837']`; after `_kill_sensor("leak-probes")`,
  `leak=True leak_state='UNKNOWN' faults=['leak-probes']`. The vehicle side is honest.
  **The nav-side link was still being landed while this was written**, and at the time of
  writing `VehicleSensorSource.read()` with `bno085` and `ms5837` killed still returned
  `heading_deg=0.0 depth_m=0.0 mag_cal=0 pressure_psi=14.7`. Re-run that one-liner before
  believing this entry — it is three lines against the real class and it is the only check
  that crosses the join this round exists to close.

## Closing the hardware loop

- 🧪 **Specify the vehicle that actually gets built, and write it down once.**
  `docs/hardware.md` is the document taken shopping and to the workbench: the v1 bill of
  materials with a purpose per line, the full BCM/header pin map with a blank column for the
  wire colours, the I²C addresses, the wiring notes per subsystem, the build notes, the power
  tree and every calibration procedure. Everything in it is mirrored from the code that reads
  it — `api/hardware.py`, `api/config.py`, `api/nav/config.py` — because a hardware document
  that drifts from the firmware is worse than none: it is believed. v2 (electromagnet,
  pan/tilt servos, burn-wire drop-weight) is quarantined in its own section so it cannot be
  bought by accident, and v1 recovery stays *empty the ballast and pull the tether*, which
  needs no software at all.
  It also carries the traps that cost a build day each: **GPIO12/18 and GPIO13/19 share the
  two hardware PWM channels**, so the thrusters take both and the lights run software PWM;
  GPIO9/10/11 are the SPI pins the encoder and paddlewheel sit on; the paddlewheel must be out
  of the prop wash (wash spins the wheel and fakes speed, defeating the snag detector) and
  more than 20 cm from the BNO085 (that failure is silent — `mag_cal` just degrades and the
  whole track leans).

- 🧪 **Two-stage leak.** One flag answered the wrong question. A film in the bilge means
  *finish the pass*; water 2 cm up means *surface now*. WARN is amber and changes the sub
  glyph's SHAPE; FLOOD keeps the red pulsing sub plus a surface prompt and stays
  unmistakable against a link dropout. Five consecutive wet samples at 10 Hz latch a stage,
  because condensation and a launch splash both touch a probe briefly and an alarm nobody
  believes is ignored on the day it is right. A dead probe reads dry forever — so the
  impossible combinations (flood wet, warn dry) are reported at arm time and the bench dip
  test is documented, since nothing in a digital input can tell *dry* from *disconnected*.

- 🧪 **The 24 V scale is dead; the pack is 2S.** `MockHardware` was sitting at 24.8 V with a
  20.0 V sag floor and the client mirrored both, on a vehicle whose pack is 8.4 V full. A
  threshold describing a different vehicle does not fail loudly — it reads "full" forever.
  Bands are now config (`NEPTUNE_BATT_WARN/CRIT/FLOOR`): green ≥ 7.0, amber below, red and a
  surface prompt below 6.6, with 6.0 documented as the hard floor and deliberately **not**
  enforced — safing a sub mid-canal trades a damaged pack for an unrecoverable vehicle.

- 🧪 **Ballast admits it does not know.** The syringe is open-loop: a stepper, an A4988 and
  no position sensor, so the step counter means nothing until it is zeroed against the EMPTY
  end stop. `get_ballast_level()` is now `float | None`, and `None` reaches the glyph as an
  explicit unknown — not 0 %, not 50 % — with the affordance to home. Both end stops are
  wired **normally-closed to ground** so a cut lead reads as *triggered* and a broken switch
  fails to a stop instead of driving the plunger into the end of the barrel. A full-stop
  count disagreeing with the configured span by > 5 % is a skipped-step event: logged,
  flagged `needs-rehome`, surfaced — a quietly wrong syringe strands a sub.

- 🧪 **An estimator you can switch, and a harness that decides whether to.** `NAV_FILTER`
  selects `dr` (the existing dead reckoner, behaviour untouched — and the default) or
  `filtered`, which changes only the heading and speed *inputs*: a complementary filter that
  coasts on the gyro whenever the compass is untrustworthy (`mag_cal < 2` or thrust ≥ 0.5,
  from actual output so a disarmed sub still reads the compass) and never steps, plus a 1-D
  Kalman on speed that treats the throttle LUT as a weak prior and zero-locks at rest. The
  snag detector runs in **both** modes because it is a safety signal, not an estimator
  feature. `dr` stays the default on purpose: there are no real dive logs yet, and a filter
  tuned against the simulator has been validated against its author's assumptions about the
  water. `python -m nav.cli replay <log> --filter both` is how that gets decided later, with
  data — and no EKF, no online current estimation and no surface-refix fusion until it does.

- 🧪 **`bootstrap.py` reports the Pi-only hardware libraries**, present or absent, and
  installs none of them — absence on the bench is correct, because every hardware import is
  lazy and `NEPTUNE_HW=auto` then lands on the flagged simulator. Also fixed the stale client
  check count it had been printing (214 → 286 at the time; **295** as measured 2026-08-07,
  and that line has now carried four different totals — re-measure it by running the suite,
  never by adjusting it until it looks right), and `--test` now runs the API suite too when
  one is present.

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

## Stability

- ✅ **Named the thing that has been freezing the handheld, and stopped feeding it.**
  `0x133 DPC_WATCHDOG_VIOLATION` had been recurring for weeks with no cause. `C:\Windows\MEMORY.DMP`
  finally captured one and `!analyze -v` was unambiguous:

      Failure.Bucket : 0x133_ISR_amdkmdag!unknown_function
      amdkmdag.sys   : 32.0.23027.3001   (AMD Radeon kernel display driver)

  The AMD display driver overruns in its **interrupt service routine**. That is a driver
  defect and nothing in this repo can repair it - the fix is an AMD driver update or
  rollback. Worth recording that there were **zero** `4101` display-timeout events, which is
  why "GPU" was never confirmed before: it never TDR'd, it went straight to a bugcheck.

  What this repo *could* fix is how much it was asking of that driver. The dashboard had
  **two permanently visible full-size `backdrop-filter: blur(16px)` surfaces** - the
  instrument bar and the control rail - composited over a live H.264 video every frame, plus
  a full-width scan line animating forever on top. Blur over video is the most expensive
  thing a page can ask a compositor to do continuously, and on a dark theme it is very nearly
  invisible: raising the panel opacity looks the same and costs nothing per frame.
  `CONFIG.ui.reduceGpu` (default ON) drops all of it.
  This does not make the driver correct, and the machine can still bugcheck with the
  dashboard closed - it has before. It removes this application as a contributor.
  *Verified: nothing in the page requests `backdrop-filter` any more, the bar and rail are
  opaque enough to stay legible without it, and the top bar, map, LOGS overlay, PIC and
  controls all still work (14 checks).*
  `crash-diagnostics.ps1` now runs the dump analysis itself, so the next one is one command
  rather than an afternoon.

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
| ⚠️ | **`DPC_WATCHDOG_VIOLATION` — IDENTIFIED: `amdkmdag.sys` (AMD display driver) overruns its ISR.** `Failure.Bucket: 0x133_ISR_amdkmdag!unknown_function`, driver `32.0.23027.3001`. The dashboard no longer adds sustained compositing load, but the defect itself needs an AMD driver update or rollback | hardware |
| ⚠️ | **USB tether NIC drops off the bus** (`Present: False`), needs a physical replug; suspect the hub/port/power path | hardware |
| ⚠️ | **The parts are not bought.** The v1 build is specified end-to-end in `docs/hardware.md`, but `RealHardware._gpio_available()` still returns a hardcoded `wired = False`, so `NEPTUNE_HW=auto` lands on the bench simulator and says so. Flipping that flag is the **last** step of the bring-up, after §10's readbacks are proven — not the first | hardware |
| ⚠️ | **Every calibration constant is still a placeholder** — `NAV_M_PER_PULSE`, `NAV_M_PER_SPOOL_TICK`, `NEPTUNE_BALLAST_SPAN_STEPS`, `NEPTUNE_SURFACE_PSI`, `NAV_IMU_YAW_OFFSET_DEG`. They exist so the code runs on the bench; each has a procedure in `docs/hardware.md` §8 and each needs water | field trial |
| ⚠️ | **`NAV_FILTER` promotion is undecided** — `dr` remains the default until `nav.cli replay --filter both` says otherwise on a real dive log. There are no real dive logs | field trial |
| ⚠️ | **No GNSS on the Ally** — Wi-Fi positioning needs internet, so the field workflow is tap-on-map. A USB GNSS on the Pi feeding `/api/origin` is the real answer | hardware |
| ⚠️ | **Chrome geolocation policy unverified** — kept as belt-and-braces; nothing depends on it | topside |
| ⚠️ | **Blind nav zoom/dial size are judgement calls** — `radarMetersPerPixel`, `blindSpanM` and the dial size were tuned by measurement, not by driving | field trial |
| ⚠️ | **Nav track unexercised in the field** — needs an origin set at a real site and a dive | field trial |
| ⚠️ | **No check crosses the whole liveness chain.** Every layer passes its own tests, which is precisely what let this defect ship three times (see `.specs/design.md` §24). There is no check that kills a sensor at the hardware end and asserts what reaches the client — and **not one of the 295 browser checks exercises the `?` mark, the amber wavy underline, `NO BEARING` or `sensor_faults` at all**. The one test that would have caught all four rounds is still the one that does not exist | api + client tests |
| ⚠️ | **`leak_state: "UNKNOWN"` is collapsed to `NORMAL` topside.** The vehicle correctly refuses to claim the hull is dry when nothing is sampling the probes (verified: `leak=True leak_state='UNKNOWN' faults=['leak-probes']`), but `leakStage()` in `client/js/core.js` maps anything that is not `FLOOD`/`WARN` to `NORMAL`, so the drop glyph reads *"both probes dry"* on evidence nobody is collecting — the exact failure the fourth state was added to remove, undone one file later. `api/protocol.py`'s comment still lists three states | client |
| ⚠️ | **A dead INA219 renders as `--V`, not `?`.** Voltage and current both null correctly and `ina219` is named in `sensor_faults`, but the pack readout falls back to the **stale** mark instead of the cannot-tell one and raises no alert chip, so "the link blinked" and "nothing is measuring the pack" look identical on the one gauge that decides whether the dive continues | client |
| ⚠️ | **`client/tests/README.md` still advertises `~95 s, 249 checks`** — the stalest of the four totals this project has carried, and the one file with the counts in it that nobody was assigned. Same class of miss as the unowned `api/nav/sensors.py`: a document nobody owned quietly contradicted every other document that had been fixed | client docs |
| ⚠️ | **Visual baselines are unblessed after this round.** 11 of 12 suites report drift above the 0.1% tolerance (0.53%–2.04%, `ballast-syringe` worst) because the client changed and `client/tests/baseline/*.layout.png` did not. Drift only reports unless `--strict-visual`, so the run still exits 0 — which means the picture layer is currently telling nobody anything | client tests |

---

## Since the last spec pass — what now exists

- **Tests are real, on both halves.** `client/tests/`: **12 suites, 295 browser checks**
  against the shipping dashboard, plus a screenshot + drift layer with a measured 0.1%
  tolerance. `api/tests/`: **4 suites, 147 checks**, standard-library `unittest`, no pytest
  — a framework that has to be installed on a Pi over a canal-side hotspot is a suite that
  quietly stops being run. Both measured 2026-08-07 (`295/295 in 114s`, `147/147 in 1s`),
  both exit 0 only if everything passes. Previously there were none at all.
  The api runner separates **failed** from **never loaded**: without `pydantic` two of its
  four suites cannot be imported, and it reports `100/103 across 2 of 4 suites`, verdict
  INCOMPLETE, exit 2 — a failed check is a finding, a suite that never ran is an absence of
  findings, and adding them into one total is exactly the reassuring-but-false report this
  project refuses from its instruments.
- **Dive logs can be calibrated** (`api/nav/calibrate.py`): the sample carries the control
  channels, and the analyser derives turn rate, depth and speed — refusing to answer where
  the data cannot support it.
- **A public simulator demo** ships from `client/` on every push (`?sim=1`), with every
  glyph and number carrying a written explanation.
- **`bootstrap.py`** reports what a machine has and lacks, for both halves of the system —
  including the Pi-only hardware libraries, which it never installs.
- **The vehicle is specified** (`docs/hardware.md`): bill of materials, pin map, I²C
  addresses, wiring and build notes, power tree and every calibration procedure, all mirrored
  from the code that reads them. What is missing is the parts, not the plan.

### Still open — and honest about it

- **Nothing has been on a bench yet.** The v1 vehicle is fully specified (`docs/hardware.md`)
  and the backend is written against it, but not one of those parts has been bought, wired or
  measured. `_gpio_available()`'s `wired` flag stays `False` until they have been, so
  `NEPTUNE_HW=auto` falls back to the bench simulator and flags itself — which is the correct
  behaviour for a vehicle that cannot yet see. Until then a "real" dive has no IMU, no depth
  sensor and no encoder, which is why `calibrate` refuses most numbers and why the operator
  dot, not the sub, is the only position the system actually knows.
- **The motion constants are guesses.** `subMaxSpeedMs`, `headingRatePerS` and the
  ballast→depth curve have never been measured against water. The tooling to fix that now
  exists; the measurement does not.
- **No GNSS on the ROG Ally**, and no internet on a sealed tether, so browser geolocation
  cannot produce a fix at all. Tap-to-set is the accurate path, not the fallback.
- **The AMD display driver** (`amdkmdag.sys`) bugchecks the handheld under sustained
  compositing load. Mitigated by `CONFIG.ui.reduceGpu`; not fixed, and not fixable here.
- **The USB tether NIC drops off the bus** under load — a hardware fault, logged in §10.

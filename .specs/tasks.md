# Neptune — Tasks / Changelog

Newest first. Each entry names the defect, not just the change, because on this project the
*why* has repeatedly been the expensive part.

Legend: ✅ done and verified on hardware · 🧪 verified in test only · ⚠️ open

---

## The client gate was red with zero checks failing, and it was measuring the browser

- 🧪 **Two reds on the pipeline, one of them weeks old — and the demo was never down.**
  Reported as "the deployment failed and Pages is down"; the Pages half was a red
  herring worth recording: the Demo workflow succeeded on every recent push, the site
  serves the current client (the 3S config is live on it), and the red X that read as
  an outage belonged to the **Checks** workflow next door.

  **The old red: every client suite reported NONE — `the browser exited (-6) before
  the suite reported` — since 3aef128.** The runner image carries a
  `/usr/bin/chromium` that answers `--version` politely and SIGABRTs the moment it is
  asked to render (a snap-style shim the host confines), and it sits FIRST in the
  Linux candidate list because on a Raspberry Pi chromium-first is correct. The
  workflow's own guard step (`google-chrome --version`) passed, because it checked
  the browser the suite then didn't use — a gate that measured the wrong browser's
  pulse. `find_chrome()` now launch-probes every auto-detected candidate
  (`_survives_launch`: headless, about:blank, dump-DOM, exit — under the same
  headless-mode and sandbox decisions the suites use, because a probe launched
  differently certifies a browser the suites cannot start) and skips a corpse OUT
  LOUD before trying the next. Explicit choices (`--chrome`, `NEPTUNE_CHROME`) stay
  unprobed on purpose: an operator's browser that cannot start must fail the run on
  that fact, not be silently substituted. Deliberately NOT "fixed" by adding the
  runner's AppArmor userns knob to `_no_sandbox_reason()` — that would have flipped
  CI onto the dev-build chromium with the sandbox off, when the intended browser was
  three entries further down the list the whole time.

  **The new red: this repo's own gate caught the brainstem round.** black wanted
  three of the split's files, flake8 had three new findings (two imports the old
  backend used and the rewrite orphaned; an `is_mock` attr/property duplicate), and
  the one `type: ignore[override]` carried no written reason. All fixed; and the
  rewrite had quietly RETIRED four mypy findings, so the mypy ceiling ratchets
  156 → 152 per the ceiling's own rule. Two tidy-ups the fixes surfaced:
  `warn_unused_configs` did exactly its documented job the moment the I2C driver
  stack left the Pi (the stale mypy overrides for smbus2/bno08x/board/busio are
  pruned; `serial` joins `gpiozero` as the backend's real imports), and lint.py's
  verdict parser is now colour-proof (`NO_COLOR`/`TERM=dumb` on the tool
  subprocesses) — on a pty, mypy dressed `error:` in ANSI and the parser counted
  zero findings in a report full of them, printing RAN BUT DID NOT REPORT.

  *Verified by running, macOS bench, 2026-08-21.* `python lint.py --ceiling` exits 0
  (isort/black/suppressions clean, flake8 at its ceiling, mypy 152 under the lowered
  152). The browser fall-through is proven end to end with a scripted corpse that
  mimics the runner's shim (`--version` answered, exit 134 on launch): the skip line
  names it and the real Chrome is picked; the tether suite then runs green through
  the probed path. The CI verdict itself lands with the next push — this entry is
  written before it, and the push is the test.

## The brainstem exists on both ends of its cable

- 🧪 **The commander/brainstem split is written — ESP32 firmware, Pi serial client, and
  the backend rebuilt around them — so the console can be tested the day the parcel
  arrives instead of the day the boat is finished.** Ledger rows 1–3 and 8 of
  `docs/hardware.md` §20, plus the vehicle halves of 4–7, in one round.

  **What exists now.** `firmware/brainstem/brainstem.ino` (Arduino C++, one sketch, one
  library — Adafruit BNO08x; the MS5837 and INA219 are raw-register `Wire` ports of the
  Pi's known-good code, kept as non-blocking state machines so pump metering and
  telemetry never stall behind a 17.2 ms conversion). It owns the chips, three leak
  zones with the five-sample latch and wet-at-boot capture, the pump loop (`pump_ml`
  metered by flow count, purge-home where flow-silence IS the datum, no-flow fault as
  the mechanism's skipped-step), lamp/beacon, the ARM+FIRE burn interlock (FIRE refused
  unless armed — two pins, two commands, one check), the reflexes (2-of-3 leak → beacon
  + bag-empty + flag; sustained undervolt below the 3S floor), a 48-event ring buffer
  (the blackbox's third witness), and an **announced bench mode** with per-chip
  kill/revive — the whole cannot-tell chain, rehearsable over the real cable against a
  bare devkit. `api/brainstem.py` is the Pi half: 10 Hz JSONL up, id'd/acked commands
  down, the link wearing its own DeviceHealth as a **bus-front** (silence ⇒ the one
  name "brainstem" fronts every reading behind it — naming the chips too would claim
  knowledge nobody has). `RealHardware` is now two DRV8871 pairs (GPIO 23/24, 5/6 — the
  Pi's whole GPIO) plus that link; **either half may be missing**, is named, and arming
  is refused without the bridges — so a laptop with only the breadboard ESP32 lights
  the whole console, which is the point of the split. The 3S purge rode along: bands
  12.6/10.5/9.9/9.0 through config, mock, client config and every fixture, and the
  dead-scale police in `test_telemetry.py` now hunts BOTH dead corridors (a 2S 8.4 V
  banding "critical" on the 3S scale is the trap pointing the other way).

  **Three honesty traps caught while writing it, kept because they are the expensive
  part.** Bench mode must never stamp the REAL DeviceHealth records — a bench session
  that left `ever=true` behind would have a never-fitted probe read "was here and
  stopped" forever after: simulation leaking into reality through the liveness
  bookkeeping. Bench frames must not carry the real leak pins either — input-only
  GPIO 34/35/39 have no internal pull-ups, so a bare devkit's zones float WET, and the
  bench walk would have opened on a FLOOD. And the Pi mirrors leak latches STICKY, so
  wet outranks cannot-tell across a link death — a FLOOD seen in any frame stands
  until a frame shows the vehicle's own latch cleared.

  **One hardware fact the firmware forced into the open** (now on the §19 watch list):
  a peristaltic pump reverses by reversing its motor, and the handoff's power tree
  drew a single IRLZ44N — which can only ever FILL the bag. No purge-home, no
  pump-out, no reflex bag-empty. A third DRV8871 on ESP32 pins 18/17 is the natural
  part; the firmware supports the single-MOSFET build (`PIN_PUMP_IN2 -1`) and refuses
  empty-direction commands out loud rather than pretending.

  *Verified by running, macOS bench, 2026-08-19.* The api suite is green across all 15
  suites — including the new `brainstem` suite (link liveness, fault passthrough,
  ladder mapping, sticky latches, command vocabulary, the two-step interlock order, the
  breadboard construction case), all over an injected in-memory transport — and the
  latency suite's debounce derivation now greps the FIRMWARE for its rate, so the
  budget follows the sampler to its new home. The client suite passes every check
  (sensor-loss's two hardcoded `8.1V` assertions were the last 2S survivors, invisible
  to the identifier police); its exit code remains the standing unblessed-baseline
  finding logged below, and this Mac renders a different window size than the Ally's
  baselines, so blessing here would vandalise them — totals are not quoted, run them.
  The firmware **compiles clean** (arduino-cli 1.5.1, esp32 core 3.3.11: 26 % flash,
  11 % RAM, zero warnings) and carries an LEDC shim for core 2.x — but **it has never
  met silicon**: the first devkit out of the parcel walks `firmware/README.md` §2
  before anything else trusts it. Still owed from ledger row 1: acks into the blackbox
  `c_id` chain, and firmware version + SHA into `session_start`.

## The parts are bought, and they are not the parts the software describes

- ⚠️ **The vehicle this codebase implements stopped existing on 2026-08-18, and for a few
  hours the repo went on saying its parts were not bought while ~£500 of different parts
  were on their way.** A two-week mobile design campaign (recorded end to end; distilled
  into `docs/handoff/NEPTUNE-HANDOFF-PROMPT.md` plus 20 companion files, now the canonical
  hardware description) specified and ordered the whole v1 boat — and it is **not** the
  boat `docs/hardware.md`'s body, `api/hardware.py` and `api/config.py` describe. The
  campaign deleted the syringe (peristaltic pump + TPU bag, flow-counted), retired the
  paddlewheel (YF-TM02 ram-flow + PAS quadrature with direction), replaced the 2S pack
  (3S3P, 12.6 V full — the same class of change as "the 24 V scale is dead", one pack
  later), moved every sensor and slow actuator onto an **ESP32 brainstem over USB serial**
  with reflexes that survive a hung Pi, promoted the burn-wire drop weight from v2 stub to
  bought v1 hardware behind a two-pin ARM+FIRE interlock, and left the Pi holding exactly
  four GPIO pins (two DRV8871 pairs) plus camera, nav, blackbox and tether.

  **What landed: the documents now describe the vehicle that was bought.**
  `docs/hardware.md` was rewritten wholesale around the ordered BOM — hull, frame, pump
  ballast, 3S power tree, commander/brainstem pin maps, burn-wire release, sonar plan,
  bench checklist, bathtub ceremony — with the campaign's drawings embedded from
  `docs/handoff/` (all 21 campaign files, now in the repo) and a `SOFTWARE GAP` marker
  wherever the code lags; **§20 of that page is the gap ledger and is the integration
  backlog.** The retired vehicle's full documentation lives in that file's git history.
  `design.md` §27 records what happens to each mechanism section; R7.4/R7.5/R10.6 in
  `requirements.md` are rewritten normative to the fitted mechanisms (the syringe-era
  end-stop criteria live in git history); `playbook.md` §8 reserves the new console
  vocabulary. The honesty doctrine (§24, R7.6) transfers untouched: a brainstem that dies
  on a USB cable is the same shape as a chip that dies on I²C, one level up.

  **Open, named, and owned by the integration that starts next** (the code side of the same
  delta): the ESP32 serial protocol + firmware and `RealHardware`-as-serial-client; the
  3S battery bands (12.6/10.5/9.9/9.0 — confirm at the bathtub); `ballast_ml` with
  purge-homing replacing step-homing; flow/PAS speed ingest with direction; three leak
  zones with 2-of-3 reflex gating; the burn-wire two-step command with its own `c_id`
  stage; the ESP32 link joining the liveness table; and the soundings bottom-contact
  signature moving from "syringe still taking on water" to "`ballast_ml` still rising".
  Bench facts that gate all of it, from handoff §15: **the recovered 3S pack sits deeply
  discharged (~3.0 V/group) — BMS on and a supervised recovery charge is the only item in
  the project with a clock on it**; the YF-TM02's flow floor and the PAS minimum-cadence
  gating are unverified and decide which speed sensor is primary.

## The console can be trusted about a hull that is only half built

- 🧪 **A console that stopped reading disabled the failsafe.** `ConnectionManager`
  awaited each client in turn on the control loop's own task, and an awaited send blocks the
  moment a transport stops accepting bytes. So one console that stopped reading stalled the
  whole loop — telemetry to every other client, the blackbox journal, and `rov.watchdog()`
  with them. Driven at full throttle over a real socket with the control frames then stopped,
  the watchdog timeout passed and the thrusters stayed at full. No hostile actor is needed: a
  suspended browser tab or a handheld swapping wifi does it. Each client now owns a bounded
  queue and its own writer task; telemetry frames are droppable because a frame delivered
  four seconds late is a lie about the present, and alarms are not.

- 🧪 **A part that was never fitted was reported as a part that broke.** The vehicle
  had always known the difference — `DeviceHealth.answered_ever()` is what stops the boot log
  announcing a recovery on every first good read — and it stopped at that class's edge. A
  hull with no IMU screwed to it read `NO BEARING`, *"the compass answered earlier in this
  dive and has now stopped"*, and sent its owner to check a cable that had never existed.
  `NO COMPASS` was unreachable. `sensors_absent` now carries the distinction, a strict subset
  of `sensor_faults` in the same vocabulary. Absence buys silence and nothing else: the
  reading behind it stays null. Only leaf parts are ever absent — a bus that will not open is
  an errand with a fix, so it stands in front of every chip behind it. The leak ladder is
  exempt on purpose: *nobody is watching the hull* is never a fact that asks nothing of
  anybody.

- ✅ **A bring-up card per instrument, and the leak card walked on the vehicle.** The first
  real bring-up attempt was abandoned, and the reasons were all findable afterwards: the book
  recommended a wet finger, which cannot work against the pull-up; a latched alarm could only
  be cleared by restarting the service; and powering up with a wet probe pinned the console
  to cannot-tell for the session. Each instrument now has a card giving pins, flags, what the
  dashboard shows at every stage, a Pi-side trick that drives the input before the hardware
  exists, and what *cannot* be distinguished — a bare digital input cannot tell dry from
  disconnected, and finding that out costs an evening. The leak card was walked on the real
  vehicle and written from what happened, including the sentence it must lead with: with
  nothing on the pins at all, the console reports the hull `NORMAL`.

- 🧪 **Three of those cards were wrong in the same way, and the gate caught it by
  following one.** They promised that a down I²C bus appears in `sensors_absent`, so a
  half-built vehicle would be quiet. It does not and must not. On the vehicle as it stands
  today that card would have produced red chips where it promised silence, and a reader
  would reasonably conclude the software was broken.

- ✅ **The vehicle had no clock.** No RTC, no battery, no route to the internet — so it booted
  to whatever its filesystem implied and stayed there, days out, misdating every dive record
  and making any figure derived from both machines' clocks meaningless. The handheld now
  serves NTP over the tether and the sub asks on connection; both halves are in setup rather
  than being remembered. `RootDistanceMaxSec` had to be raised, because Windows advertises an
  honestly mediocre root dispersion and `timesyncd` was discarding every valid reply.

- 🧪 **Nothing exercised the sockets, and nothing measured what the tests touched.**
  Both runners now report coverage as a dev-only tool that is absent on the vehicle by
  design. A network suite drives the real transports with a hand-rolled client, because a
  well-behaved library cannot send the malformed frames worth proving. An integration suite
  kills and revives every part the mock can break and asserts the wire goes to cannot-tell
  rather than to a plausible lookalike. A latency suite times pin to socket and fails against
  a budget set from the measurement.

- ⚠️ **Open, and named rather than quietly carried.** `signal`, `cpu_c`, `ram_pct` and
  `disk_gb` reach the client and are drawn by nothing, and their ingest guards leave the last
  value in place on a null — the shape that was fixed for depth, heading and battery.
  `headingFlag()` applies two of the three tests `sensed()` applies, so a badge could describe
  a number that is not on screen; unreachable today only because one handle nulls both, which
  is now asserted every frame. `api/blackbox/rovlog.py` has no coverage in either suite.

## A map of water nobody had surveyed, drawn as though somebody had

- 🧪 **"There are no hazards here" and "nobody downloaded the hazards for here" were the
  same blank map, and "the bed is 1.07 m down" was a figure no instrument had ever taken.**
  A canal is full of things that will stop this vehicle and are invisible from the surface —
  sluice intakes that pull, stop-plank grooves that eat a tether, culvert mouths, weirs,
  safety gates — and the console drew none of them, so an unfetched card and a clear channel
  were one picture. The same trap one layer down: there is no canal bathymetry to download
  anywhere (i-Boating's UK layer is proprietary UKHO coastal data with no canal soundings,
  the EA's multibeam is estuarine, LIDAR cannot see through water, and the Trust's own
  hydrographic surveys are internal), so a depth drawn over the water had to come from
  somewhere honest or not be drawn at all.

  **What landed.** `api/nav/crt.py` fetches the Canal & River Trust's published hazard
  layers for one area into `<crt_dir>/<area>/<layer>.geojson`, each with its own
  `.prov.json` beside it and a `provenance.json` index over the lot. `api/nav/nominal.py`
  derives the published **guideline draught** per waterway section from geometry already on
  disk. `api/nav/soundings.py` turns dive journals into measured cells. `api/nav/service.py`
  serves all three (`/api/areas/{name}/crt`, `…/crt/{layer}`, `…/depth/nominal`,
  `…/depth/surveyed`) and gates them at pre-dive; `client/js/crt.js` carries them in one table
  — **33 rows, 7 tier-1 KEEP AWAY / 11 tier-2 operations / 15 tier-3 extras** — where the row
  *is* the layer: its tier, its mark, its keep-away radius, and a sentence saying what it means
  **for this vehicle** rather than what it is called. Adding a layer is adding a row there;
  there is no second place to remember, which is the point.

  **`crt.py` is the only half that touches the network, and it is BOOTSTRAP-ONLY.** It is
  never imported on the runtime path, and there is **no hostname in it** — every one lives in
  `nav/config.py`, is reached exactly once by `python -m nav.cli crt-fetch <area>`, and is
  never resolved canal-side, where a DNS lookup does not fail so much as hang. `nominal.py`
  and `soundings.py` touch nothing at all: on a bank with no internet they behave identically
  to the bench. Same two-phase split as `areas.py` and `satellite.py`.

  **THE FETCHED CARD IS PER-AREA, AND NEW WATER MEANS A NEW FETCH — BEFORE YOU GO.** This is
  the operational fact this whole entry exists to state. There is no internet at the canal, so
  a card that was not downloaded at home cannot be downloaded at the waterside, and a console
  with no card draws a map with nothing on it, which is the exact shape of a map of water with
  nothing in it. It is therefore a **go/no-go readiness item**, not a nicety:
  `NavService.readiness()` fails the check labelled `CRT hazard layers cached…` when the
  active area has no card, when any layer's fetch failed, or when a file on the card will not
  parse — and its detail prints `fetched <date>` so the operator can judge the age themselves.
  Activating a different area (`activate_area()`) re-points that check at a different
  directory, so arriving at new water cannot silently inherit the last site's answer.

  **The honesty rules, applied to a download and to a depth.** A layer that fetched cleanly
  and matched nothing still gets a file with zero features — that is a survey result and worth
  writing. A layer whose fetch failed part-way gets **no file**, because a truncated page has
  exactly the shape of "no hazards here"; the serving side then reports it **ABSENT**, and
  `crt.js` speaks three words where a map would normally have two — **PRESENT**, **ABSENT**
  ("the Pi looked and that file is not on the disk") and **CANNOT TELL** ("nobody could be
  asked"). A licence that could not be read is recorded as `null`, never as "OGL v3". Two
  independent counts guard every fetch, because a silently truncated page is this API's
  signature failure: `returnCountOnly` for the same bbox before paging, compared with what
  landed after, plus a national count per layer measured against the live service so a
  service quietly swapped for one of its own legacy near-duplicates shows as drift.

  **A sounding is a LOWER BOUND, and it says so in six places.** The MS5837 measures the depth
  of the *sub*; nothing aboard measures the depth of the *bed*. So a sample counts only where
  the journal shows bottom contact — **descent stopped while the syringe was still taking on
  water**, which is the one signature that is not neutral buoyancy — and even then the pressure
  port sits above the keel, the sub may have landed on silt or a sunken trolley, and the datum
  is the surface of *that day*. Every error has the same sign, so the quantity is named
  `lower_bound_m`, each cell carries `bound="lower"`, and cells absent from the file are
  **UNSURVEYED**, which is not shallow and not zero. Cells are longitudinal along the
  centreline rather than an (x, y) grid, because position error here exceeds the canal's
  half-width and a raster would draw cross-channel structure the navigation cannot support.
  `nominal.py` is stamped **NOMINAL** in five places — collection, feature, title, aria-label,
  `basis` — because it is guidance, not bathymetry, and a guideline draught is a *floor* on the
  depth: "max draught 1.07 m" means the bed is deeper by a margin nobody publishes. It errs
  shallow on purpose (a nominal that is too shallow makes an operator cautious) and
  `shoals_to_banks` is true on every section, because these are mid-channel figures.

  **NO FLOW IS EVER SHOWN.** CRT publish no flow measurement of any kind. So the hazard marks
  are the honest proxy for *expect current here* and every one of them carries the sentence
  saying so — a mark implying a measured current at a weir would be inventing the single
  number this system has no way to take.

  *Verified by running, ROG Ally, 2026-08-07.* api **306/306 in 5 s across 8 suites**
  (`crt` 24/24, `soundings` 22/22); client **484/484 across 15 suites in 151 s**
  (`crt-overlay` 50/50), the run exiting 1 on unblessed baselines only — logged in its own row
  below. Against the live services the same day, `crt-fetch` pulled **26 layers / 721
  features** for one area — and then died with `UnicodeEncodeError` on the *last* thing it
  prints, a Trust licence string carrying U+FFFD that cp1252 has no character for: a good
  download reporting itself as a crash, with an exit code saying the fetch had failed while
  721 features sat on the card. Fixed once in `nav.cli`'s `main()` (`stream.reconfigure(
  errors="replace")`) rather than guarded at each print, because every string arriving from
  off this vehicle can do it.

## The readings nobody could see

- 🧪 **Twenty facts were leaving the vehicle on every frame and reaching no readout, a
  twenty-first reached one only on hover, and nothing in the repo could tell which of them
  were decisions.**
  The defect is not that a field was unrendered — plenty of them should be. It is that
  *deliberately not shown* and *forgotten* looked identical from every file, so each round
  re-discovered the same list and each round guessed differently about it.

  **The inventory.** Every field on both frames is now written down with what its null
  means and what the console actually does with it — `api/README.md` → *Every field on the
  wire, and what the console does with it*, which is the page you land on when you add a
  sensor. Taken by grepping the five client files for each field name, not from memory. It
  separates five fates that were previously one word: **rendered**, **tooltip only**,
  **consumed** (something branches on it, nothing shows it), **ingested** (parsed into
  `state` and read by nothing), and **dead store** — a named slot written every tick that
  looks like a working consumer at every call site except the one that would draw it.
  `/ws/nav` had three of those (`range_m`, `payout_m`, `confidence`) and they had survived
  months precisely because `MAP.rangeM = m.range_m` reads like a feature.

  **Four readings went onto the wire, and one came out of a tooltip.** `gyro_z_dps`,
  `accel_fwd_ms2`, `pitch_deg` and `roll_deg` existed only on nav's internal
  `SensorSample` — no amount of client work could have displayed them, because neither
  frame carried them. They ride on `protocol.Telemetry` and **deliberately not** on
  `NavState`, for a reason worth keeping: `NavState` does not exist until a dive does (the
  estimator is built in `start_dive()`, which needs an origin), so a field there would be
  blanked by the nav freshness gate through the whole pre-dive phase of every healthy
  boot — spelling *"no datum yet"* and *"the IMU is dead"* with one null. A comment block
  in `nav/models.py` records that, so a later round cannot re-add them and recreate a
  two-socket disagreement of the kind `TwoSocketsOneVehicleTest` already exists for.
  `current_a` needed no contract change at all: its wire shape was already right, and it
  was being spent inside the pack tooltip — *"drawing 3.1 A"* — which an operator on a bank
  in sunlight with wet hands is never going to hover. A reading nobody can see is a reading
  that does not exist.

  **All four are `Optional` with no default, and that is the whole point.** Every plausible
  default is itself a measurement: `0.0 deg/s` is *"not turning"*, `0.0 m/s²` is
  *"coasting"*, `(0.0, 0.0)` is *"level"*. Each is the **calm** answer, which is exactly
  how a dead IMU used to look like a well-behaved vehicle — the same defect as `mag_cal` 0,
  one round later and four fields wider. On `kill("bno085")` all **six** BNO085 fields go
  null in one frame with `sensor_faults=['bno085']`, so the blank always arrives with the
  chip that caused it; amps and volts likewise die together, because they are one chip.

  **What they are for is now written where the person holding the soldering iron will read
  it** (`docs/hardware.md` §13): turn rate is the independent witness that catches a
  compass being pushed by the thrusters' own magnetic field, or a gyro bias that walks the
  dead-reckoned track sideways; forward acceleration catches a 90° mounting error before it
  becomes a mystery in the dive log, and separates *"the paddlewheel died"* from *"the sub
  genuinely is not moving"*; a standing roll at rest is lead and foam, not software. And
  pack current is the reading that finds a **fouled prop** — this is a canal-cleaning
  vehicle, the propellers will pick up line and weed, the camera looks forward rather than
  aft, and a wrapped prop still spins and still makes noise. The signature is draw **up**
  with paddlewheel speed **down** at the same throttle. *Measured against `MockHardware` on
  the ROG Ally, 2026-08-07:* 0.35 A idle, 2.85 A with both thrusters at full, +0.80 A for
  the white spots and +0.50 A for the green ring — **4.15 A worst case, above the stock
  0.1 Ω shunt's ±3.2 A ceiling.** A clipped reading is not a maximum; it is a ceiling
  wearing a measurement's clothes, and the one thing it hides is the overload the sensor
  was fitted to see.

  **What is still not shown, and now says so out loud.** Twenty of `Telemetry`'s
  forty-six fields reach no readout, and twelve of `/ws/nav`'s twenty-four keys reach
  nothing at all. Most are fine — the Pi-health block is duplicated on
  `/api/system` on purpose, so it survives the control link going down. Three are worth a
  reviewer's suspicion and are called out as such: the two `light_*_level` echoes (the
  gauge shows what was *commanded*, so a lamp driver that clamps or comes up at half
  brightness looks perfect on screen), `signal`, and `link_ms`. None is a safety signal,
  which is why none has been promoted — and none is a deliberate omission either, which is
  why the row exists rather than being rediscovered next round.

## The checks stop being able to lie

- 🧪 **Four stale check totals, two of which went stale in the commit that "fixed" them.**
  214 in `bootstrap.py`, 249 in `client/tests/README.md`, 286 in `client/README.md` and
  `.specs/design.md`, and a fifth number in reality — every one copied forward from
  whichever tree its writer had open rather than from a run. The cost is not one wrong
  number. Someone who reads 286 and watches a different total scroll past has been told the
  bench is running something other than what it is, and then has no reason to believe
  anything else on the page.

  **So the counts now live in exactly one place: the runners, which print them.** No
  document in this repo states a check total any more. `bootstrap.py` globs `suites/*.js`
  for the suite count (derived off the tree, so it cannot drift) and *asks*
  `api/tests/run.py --list` for the api figures rather than remembering them. The
  reasoning, and the list of what may still be written down, is
  `client/tests/README.md` → *Where the numbers live*. Wall time is the one measured figure
  kept, because it is the difference between "time for a coffee" and "something has hung" —
  and it is stamped with the machine and the date and labelled a measurement, not a
  contract.

  **The suites run on three platforms**, which matters because the api half is meant to be
  checkable on the Pi it deploys to and the client half on whatever anyone has: Windows
  finds Chrome or Edge, macOS finds `/Applications/Google Chrome.app`, Raspberry Pi OS
  finds `chromium-browser` on `PATH`. Standard library either way, and nothing needs the
  internet once the browser is on the card — the Pi is normally on a sealed tether.

  **And on the Ally there is now one touch:** `Neptune.bat -Test` (`-Test client` /
  `-Test api` for one half). There is no terminal on that handheld and no keyboard to type
  one with, so *"run the suites before you get in the boat"* was a ritual nobody could
  perform at the waterside — and a suite that cannot be run where the vehicle is, is a
  suite that quietly stops being run. It sits ahead of the single-instance mutex and opens
  no port, so it works with a dive already up on the machine.

  **Green now means it ran.** The launcher quotes the runner's own total rather than
  counting anything itself, and treats a zero exit with **no total printed, a total of
  zero, or an `INCOMPLETE` verdict** as a failure to run rather than a pass — the same
  three-value language `api/tests/run.py` already speaks (`0` ran and passed · `1` a check
  failed, which is a *finding* · `2` nothing failed but something could not be **run**,
  which is an *absence* of findings). The verdict block carries its answer in a frame
  character as well as a colour (`=` passed, `#` failed, `?` could not tell), because a
  phone photo of that window may be all anyone has by the time it is discussed, and because
  in this project colour is never the only carrier — the same rule as the gauges.

  *Verified by running, on the ROG Ally (RC71L, Ryzen Z1 Extreme, Windows 11, PowerShell
  5.1), 2026-08-07.* The api suite is green and exits 0. The client suite **exited 1 as this
  was first written**, on one check — `Speed — a null renders as "?" and never as the stale
  "--"` — which was a real finding in the round landing beside this one, logged rather than
  waited out. **That check now passes** (`renderSpeed()` in `client/js/render.js` maps a null
  `speed_ms`/`speed_src` to `'?'`, with `NO SPEED` beside it and `NO DATUM` for the different
  failure of having no origin yet); `instrument-cluster` is 76/76 and every check in the
  client suite passes. The suite still exits 1, for an unrelated reason that is logged in its
  own row below — unblessed screenshot baselines, which stopped being advisory in the same
  work. Wall time on this machine: **api ≈5 s, client ≈150 s**; that is a measurement of one
  box on one day, not a contract. Totals are deliberately not written here — run them.

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

  *Verified 2026-08-07 by running, on the ROG Ally.* Both suites green, both exit 0; the
  api runner in a python with no `pydantic` correctly reports the un-importable suites as
  `DEPS`, verdict INCOMPLETE, exit 2, rather than folding them into a reassuring total.
  (Totals are not quoted here on purpose — this entry has been re-read after the suites
  grew, and a figure frozen into a changelog is a figure that describes a tree nobody has
  any more. `python bootstrap.py --test` states them; see *The checks stop being able to
  lie*, above.)
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
  lazy and `NEPTUNE_HW=auto` then lands on the flagged simulator. It also stopped printing a
  remembered client check count: that line carried four different totals over four rounds,
  so it now globs `suites/*.js` for the suite figure (derived off the tree, so it cannot
  drift) and asks `api/tests/run.py --list` for the api ones, leaving the check totals to
  the runners — which are the only things entitled to state them. `--test` runs both suites
  when both are present.

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

The first four rows are one **pre-first-dive batch**, grouped at the top and labelled in the
Item column so the set that must land before water is readable at a glance rather than
reconstructed from owners. They are not the largest defects on this page; they are the ones
that stop being fixable afterwards. Three of them write something permanently wrong or
permanently ambiguous into the first real dive logs, and the fourth is the gate that would
otherwise certify the estimator against a world where the speed model is exactly right — and
those first dive logs are the evidence half the rest of this table is waiting on. None is
fixed, and none is being fixed in the round that logged them, because `api/hardware.py` and
`api/nav/` are frozen until the loom is on the pins.

**How to read a citation in this table, and why they changed shape.** Every one now names a
**function** and quotes a **distinctive line of code**, with the line number kept only as a
hint. That is not decoration. Those six rows were written against a committed HEAD and were
already pointing at the wrong lines one round later, because the round that followed inserted
code above them: `client/js/map.js:702` had become `:653`, `client/js/map.js:699` had become
`:713`, `api/nav/service.py:355` had become `:358`, and `api/nav/cli.py:73` had become `:88` —
four of six wrong, and every one of them still *resolving* to a real line of code, which is the
dangerous kind of wrong. A bare line number is a citation with a shelf life measured in
commits, and a backlog written to survive a hardware bring-up cannot have one. Re-verified
against the working tree on 2026-08-07 by opening every file; if a number below has moved
again, grep the quoted line and it will be there.

| | Item | Owner |
|---|---|---|
| ⚠️ | **PRE-FIRST-DIVE BATCH (1 of 4) — the drift penalty is nested inside the snap branch.** In **`DeadReckoner.update()`** (`api/nav/deadreckoning.py`, ~line 158), find `if near and near[2] <= settings.snap_max_dist_m:` and read the two lines indented under it: `if snap_off > 8.0:` → `confidence = min(confidence, 0.7)`. Between 8 m and 25 m of raw↔snapped divergence the snap happens *and* confidence is floored at 0.7; past `snap_max_dist_m` (25 m) the magnet lets go and the knock goes with it, because it lives inside the same `if`. So the drift indicator falls silent exactly when the drift is worst, and the score written into the journal carries no drift penalty at all for the ticks that deserve it most. A *missing* 0.7 currently spells "no drift" and "wandered off the mapped water" with one number. `docs/maths.md` §5 and §12 both set the gap out. **Must land before the first real dive** | api |
| ⚠️ | **PRE-FIRST-DIVE BATCH (2 of 4) — modelled tether payout is journalled without provenance.** Two lines, in two files. **`VehicleSensorSource.read()`** (`api/nav/sensors.py`, ~line 345) accumulates `self._payout += abs(fields["throttle"]) * dt * 1.2` whenever no spool answers, and the line after it picks between them: `payout = spool_m if spool_m > 0.0 else self._payout`. That 1.2 is a deliberate over-estimate, admissible as a *bound* (`docs/maths.md` §4) and never as a measurement. **`DiveLog.add()`** (`api/nav/divelog.py`, ~line 153) then writes `"encoder_m": getattr(raw, "encoder_m", 0.0),` into the journal — under the encoder's own name, unlabelled, with nothing beside it saying which of the two branches produced it. Every log written before the fix is therefore permanently ambiguous about whether its payout was counted or modelled, and no later pass can repair a log that did not record which it was. **Must land before the first real dive** | api |
| ⚠️ | **PRE-FIRST-DIVE BATCH (3 of 4) — `calibrate.py` lets the model outrank the witness.** In **`report()`** (`api/nav/calibrate.py`, ~line 440), under `print("\n--- SPEED ---")`: `enc, why_enc = encoder_speed(samples)` is tried first, and four lines below it an explicitly supplied `--ground-truth` sits in the `elif ground_truth:` behind it. So on an encoder-less hull the payout column is the 1.2 model of the row above, and the speed LUT is derived from the model's own constant: circular, biased fast, and produced by the one tool in the repo written to refuse rather than guess. A tape measure and a stopwatch that an operator went and used must outrank anything derived. `docs/maths.md` §8 sets out the three witnesses and the encoder's known bias; **the precedence between them is stated nowhere but this row**, which is why it must not be retired without being written into the code. **Must land before the first real dive** | api |
| ⚠️ | **PRE-FIRST-DIVE BATCH (4 of 4) — the simulator shares its speed table with the estimators.** **`Simulator.__init__`** (`api/nav/sim.py`, ~line 72) accepts `true_lut: SpeedLUT \| None = None` and resolves it ~19 lines later as `self.true_lut = true_lut or DEFAULT_LUT` — the estimators' own table. Grep `Simulator(` across `api/`: **four** construction sites, **none** passes one. `_sim()` in `api/nav/cli.py` (`sim = Simulator()`, ~line 88 — it was `:73` before this round moved it); `SimSensorSource.__init__` and `SimSensorSource.reset()` in `api/nav/sensors.py` (`self._sim = Simulator(hold_at_end=True)`, ~lines 209 and 231); and the A/B gate's own `_fly_journal()` in `api/tests/test_replay.py` (`sim = Simulator(mag_gain_deg=mag_gain_deg)`, ~line 68). So `NAV_FILTER` would be promoted on a world where the speed model is exactly right, with the largest real error term in the system removed from the trial that exists to measure it. A one-argument, test-only fix: the door is cut and nobody has walked through it (`docs/maths.md` §7, §14). Before **NAV_FILTER promotion** | api tests |
| ⚠️ | **Snapping is invisible on the console.** In **`connectNavWs()`**'s `ws.onmessage` (`client/js/map.js`, ~line 653) the handler opens `if(m.type==='nav'){ MAP.x=m.x_m; MAP.y=m.y_m;` and closes ~63 lines later with `pushTrack(m.x_m,m.y_m,m.depth_m);` — both the dot and the track are the **raw, un-snapped** coordinates. The snapped `lat`/`lon`, the `snapped` flag and `snap_offset_m` all ride the frame — **`NavService.nav_frame()`** builds it as `f = {"type": "nav", **ns.model_dump(),` (`api/nav/service.py`, ~line 358, in a function that starts at ~317) — and not one of them is consumed anywhere in `client/js`. The faint raw dot promised by `raw_lat: float  # un-snapped estimate (rendered faint, §5.7)` (`api/nav/models.py`, ~line 141) does not exist either, so the operator sees neither the correction nor its size, and a snap that is quietly hauling the estimate 20 m sideways looks identical to no snap at all. Parked for the **post-hardware audit** on purpose: what the offset does against real logs is what decides how it should be drawn | client |
| ⚠️ | **Confidence is never rendered.** Near the end of that same handler: `if(typeof m.confidence==='number') MAP.confidence=m.confidence;` in **`connectNavWs()`**'s `ws.onmessage` (`client/js/map.js`, ~line 713). The check that survives any edit is `grep -rn confidence client/js/` — it returns **exactly two hits, both in `map.js`**: that write, and the `confidence:1` slot in the `MAP` initialiser (~line 42). Zero readers. The number is not lost — **`DiveLog.add()`** writes `"confidence": ns.confidence,` into the journal (`api/nav/divelog.py`, ~line 119) and it reaches `GET /api/nav/state`, so replay can grade a recorded track — but it stops there. Every individual CAUSE has a badge (snagged, gyro-only, no-compass, heading-suspect) and only the composite, the estimator's own account of what the dot it just drew is worth, is invisible. Held back **deliberately until real dive logs show how it behaves**: a score that sits at 0.6 through most of every dive is a badge the operator learns to stop reading | client |
| ⚠️ | **A hazard card's AGE is printed and compared with nothing.** The pre-dive gate covers *presence* and *readability*: **`NavService.readiness()`** (`api/nav/service.py`) fails the `CRT hazard layers cached…` check when the active area has no card, when any layer's fetch failed, or when a file on it will not parse — so arriving at new water cannot silently produce an empty map. What no code anywhere does is judge the date it then prints: the detail string ends `f"fetched {crt_block.get('fetched')}; "` and nothing compares that with today. `grep -rn "max_age\|age_days\|expiry\|expires" api/nav/crt.py api/nav/nominal.py client/js/crt.js` returns **nothing**, and `nav/config.py` has no interval either — so a card pulled a year ago passes the identical check as one pulled this morning, while the Trust adds stop-plank grooves and takes weirs out of service in between. Deliberately not guessed at now, and that is the whole finding: the right interval is a question about how fast those layers actually change, nobody here has watched them long enough to answer it, and a number invented to fill the gap would be the estimate-dressed-as-measurement this system refuses everywhere else. Needs one season of re-fetches to say | field trial |
| ⚠️ | **`DPC_WATCHDOG_VIOLATION` — IDENTIFIED: `amdkmdag.sys` (AMD display driver) overruns its ISR.** `Failure.Bucket: 0x133_ISR_amdkmdag!unknown_function`, driver `32.0.23027.3001`. The dashboard no longer adds sustained compositing load, but the defect itself needs an AMD driver update or rollback | hardware |
| ⚠️ | **USB tether NIC drops off the bus** (`Present: False`), needs a physical replug; suspect the hub/port/power path | hardware |
| ⚠️ | **The parts are bought (2026-08-18) — and they are not the parts `RealHardware` drives.** The full v1 bill is ordered (`docs/handoff/` §13; ~£500), which retires the old first line of this row — but the software side of it got harder, not easier: `RealHardware` implements the pre-campaign vehicle, and the bought one puts everything except two DRV8871 pairs behind an ESP32 serial link (`docs/hardware.md` §8; ledger §20). What survives from the old row unchanged: `NEPTUNE_HW_WIRED` flips **with the first module on the pins** (`docs/hardware.md` §16's staged bring-up), not the last, because a part that is absent reads as absent, per part, all the way to the console, and each module visibly comes alive as its connector (now possibly a USB cable) seats | integration |
| ⚠️ | **Every calibration constant is still a placeholder** — `NAV_M_PER_PULSE`, `NAV_M_PER_SPOOL_TICK`, `NEPTUNE_BALLAST_SPAN_STEPS`, `NEPTUNE_SURFACE_PSI`, `NAV_IMU_YAW_OFFSET_DEG`. They exist so the code runs on the bench; each has a procedure in `docs/hardware.md` §14 and each needs water — and the pump/flow constants that replace the syringe/paddlewheel ones arrive with integration (§20 ledger) | field trial |
| ⚠️ | **`NAV_FILTER` promotion is undecided** — `dr` remains the default until `nav.cli replay --filter both` says otherwise on a real dive log. There are no real dive logs | field trial |
| ⚠️ | **No GNSS on the Ally** — Wi-Fi positioning needs internet, so the field workflow is tap-on-map. A USB GNSS on the Pi feeding `/api/origin` is the real answer | hardware |
| ⚠️ | **Chrome geolocation policy unverified** — kept as belt-and-braces; nothing depends on it | topside |
| ⚠️ | **Blind nav zoom/dial size are judgement calls** — `radarMetersPerPixel`, `blindSpanM` and the dial size were tuned by measurement, not by driving | field trial |
| ⚠️ | **Nav track unexercised in the field** — needs an origin set at a real site and a dive | field trial |
| ⚠️ | **The client suite exits 1 and not one check fails.** Visual drift stopped being advisory: `run.py`'s verdict is now `if failed or crashed or (drifted and not args.loose_visual): return 1`, and the comment above it says why — the old gate printed `VISUAL DRIFT 1.2%` on twelve suites and exited 0, which is the shape of a check nobody acts on, and the baselines went twelve rounds without being looked at. The sources of honest noise were removed first (map and video hidden for the layout shot, animations frozen, so two identical runs now differ by **zero** pixels), so a drift now means the picture really changed. What it is reporting is three unblessed baselines and one missing one. *Measured by running, ROG Ally, 2026-08-07, Chrome 151.0.7922.76:* **484/484 checks pass across 15 suites in 151 s — and the run exits 1.** `operator-marker` and `status-and-rail` both drift **0.20%** (1753 of 883 116 px, the *identical* count on both, so it is one shared rail change and not two coincidences), `input-dial` **0.10%** (903 px) against a 0.10% tolerance, and the new `crt-overlay` suite has no baseline at all. `--bless` belongs in the commit that changed the UI, after looking at the shots — which is exactly what has not happened yet | client tests |
| ⚠️ | **`.specs/design.md` §14 still quotes four stale totals** — `295 checks`, `~114 s`, `four suites, 147 checks`, and a `100/103 across 2 of 4` no-deps figure, all of which the runners now contradict. It was outside this round's file ownership, which is the same shape of miss as the unowned `api/nav/sensors.py`: the one document nobody was assigned went on saying what every other document had stopped saying. It should say what `client/tests/README.md` → *Where the numbers live* says, and quote nothing | specs |
| ⚠️ | **The `/ws/nav` dead stores are still dead.** `range_m` and `payout_m` are written into `MAP.*` every frame and read by nothing — the tether readout on screen is the client's own straight-line arithmetic against the cable length in `CONFIG`, and never touches either server figure. The other two, `confidence` and `snap_offset_m` (spec §5.7's "drift indicator"), now carry their own rows above and are not reasoned about twice. The inventory in `api/README.md` names all four rather than letting each round rediscover them, but naming a gap is not closing it | client |
| ⚠️ | **Three telemetry fields are shown from somewhere else, so a disagreement is invisible.** `light_green_level` / `light_white_level` (the gauge shows what was *commanded*, so a lamp driver that clamps, fails or comes up at half brightness looks perfect), `signal`, and `link_ms` (the readout is the client's own pong RTT). None is a safety signal; all three are listed in the inventory as suspicious rather than as decided | client |

---

## Since the last spec pass — what now exists

- **Tests are real, on both halves**, where previously there were none at all.
  `client/tests/` drives the shipping dashboard in headless Chrome, plus a screenshot +
  drift layer with a measured 0.1% tolerance; `api/tests/` is standard-library `unittest`,
  no pytest — a framework that has to be installed on a Pi over a canal-side hotspot is a
  suite that quietly stops being run. Both exit 0 only if everything passes, both run on
  Windows, macOS and Raspberry Pi OS, and `Neptune.bat -Test` runs them from the handheld's
  desktop with no terminal.
  **Neither total is written down anywhere**, deliberately: `python bootstrap.py --test`
  runs both and prints them, and *Where the numbers live* in `client/tests/README.md` says
  why four hand-copied totals were once in circulation at once.
  The api runner separates **failed** from **never loaded**: without `pydantic` two of its
  suites cannot be imported, and they are reported as `DEPS`, counted apart, named in the
  verdict, INCOMPLETE, exit 2 — a failed check is a finding, a suite that never ran is an
  absence of findings, and adding them into one total is exactly the
  reassuring-but-false report this project refuses from its instruments.
- **Every field on both frames is inventoried** (`api/README.md`), with what its null means
  and what the console does with it — rendered, consumed, ingested-and-read-by-nothing, or
  a dead store. It is the page to read before adding a sensor, and it exists because
  *deliberately not shown* and *forgotten* had looked identical from every file in the repo.
- **Dive logs can be calibrated** (`api/nav/calibrate.py`): the sample carries the control
  channels, and the analyser derives turn rate, depth and speed — refusing to answer where
  the data cannot support it.
- **The published hazards are on the card, and the sub surveys its own depth**
  (`api/nav/crt.py`, `api/nav/nominal.py`, `api/nav/soundings.py`). One of the three needs the
  internet and is bootstrap-only — `python -m nav.cli crt-fetch <area>`, run before you go,
  because there is none at the canal. **The card is per-area: new water needs a new fetch**, and
  the pre-dive readiness check fails without one, since an absent hazard layer is not a clear
  channel. Depth comes in two inks that are never mixed: NOMINAL (the authority's guideline
  draught, a floor on the depth, guidance and not bathymetry) and SURVEYED (a lower bound the
  sub itself established by landing on the bed). Anywhere unsurveyed is drawn as unsurveyed.
- **A public simulator demo** ships from `client/` on every push (`?sim=1`), with every
  glyph and number carrying a written explanation.
- **`bootstrap.py`** reports what a machine has and lacks, for both halves of the system —
  including the Pi-only hardware libraries, which it never installs.
- **The vehicle is specified** (`docs/hardware.md`): bill of materials, pin map, I²C
  addresses, wiring and build notes, power tree and every calibration procedure, all mirrored
  from the code that reads them. The parts arrived on 2026-08-18 — to a newer design than
  the one mirrored, so `docs/hardware.md` was rewritten to the bought vehicle; its §20 and
  the entry at the top of this file carry what the code still owes it.

### Still open — and honest about it

- **Nothing has been on a bench yet — but the parts are bought (2026-08-18), and they are
  not the parts the backend was written against.** The ordered vehicle
  (`docs/hardware.md`; gap ledger in its §20) moves all
  sensing to an ESP32 brainstem, swaps the syringe for a pump, the paddlewheel for
  flow/PAS, and the 2S pack for a 3S — so integration is reconciliation work, not just
  wiring. `_gpio_available()`'s `wired` flag stays `False` until the first module actually
  goes on the pins (`docs/hardware.md` §16), so `NEPTUNE_HW=auto` falls back to
  the bench simulator and flags itself — which is the correct behaviour for a vehicle that
  cannot yet see. Until then a "real" dive has no IMU, no depth sensor and no encoder,
  which is why `calibrate` refuses most numbers and why the operator dot, not the sub, is
  the only position the system actually knows.
- **The motion constants are guesses.** `subMaxSpeedMs`, `headingRatePerS` and the
  ballast→depth curve have never been measured against water. The tooling to fix that now
  exists; the measurement does not.
- **No GNSS on the ROG Ally**, and no internet on a sealed tether, so browser geolocation
  cannot produce a fix at all. Tap-to-set is the accurate path, not the fallback.
- **The AMD display driver** (`amdkmdag.sys`) bugchecks the handheld under sustained
  compositing load. Mitigated by `CONFIG.ui.reduceGpu`; not fixed, and not fixable here.
- **The USB tether NIC drops off the bus** under load — a hardware fault, logged in §10.

# NEPTUNE COMMAND — client

Vanilla-JS control client for the tethered canal-cleaning ROV. Pure front-end
API consumer: WebRTC video (go2rtc), WebSockets for ROV control + camera
telemetry, and REST for camera commands/config/files. No framework, no build
step, no dependencies.

**This runs on the ROG Ally, not the Pi.** The Pi is backend-only. Double-click
**[`launch/Neptune.bat`](launch/)** — it makes a desktop shortcut, starts a tiny local
static server (a secure origin — geolocation and the PWA need one), and opens
**Chrome/Edge fullscreen** at `http://localhost:8080/?host=<pi-ip>`. The Pi is plain HTTP
(sealed tether), so there's no cert to trust. Run `launch/tether-setup.ps1` once as admin
first (fixed tether IP). See [`launch/README.md`](launch/README.md).

`?host=<pi-ip>` is remembered (localStorage) and can also be set in the CONFIG panel.
(Opening from `file://` is blocked — it has no secure context and can't reach the Pi;
that's why the launcher serves it from `localhost` instead.)

## What this is

**Not a dashboard — the system that commands a set of instruments, whose mission is to
bring the ROV back to the operator whatever fails.**

Everything below it is an instrument: camera, video feed, control link, IMU, depth
sensor, tether encoder, map imagery, the handheld's own GPS. Instruments are fallible by
nature and **will** fail — cold water, a long cable, a handheld whose display driver
bugchecks, a Pi that browns out. So each is assumed to fail, fails *alone*, says so in one
glance with a shape rather than only a colour, never has its reading invented, and hands
over to the next mechanism automatically — no retry buttons, no dialogs. The operator has
a sub to fly.

The fallback chain ends somewhere that needs no software at all:

| When this fails | What takes over |
|---|---|
| Camera video | **BLIND NAV** — the map becomes the driving view |
| Map imagery | the metre frame: grid, track, tether ring, heading |
| Vehicle navigation | the marker **holds** and says `NO NAV`; the dial still answers the stick |
| The control link | the simulator keeps the console flyable, badged; the vehicle's watchdog zeroes the thrusters |
| The handheld's GPS | tap the map — more accurate anyway (±8 m vs ±50 m) |
| Dead reckoning | the tether still bounds where the sub *can* be: 100 m of cable is a 100 m circle |
| Everything topside | **SURFACE** on a hold, and a mechanical drop-weight |

See [`.specs/design.md` §0](../.specs/design.md) for what that forbids.

## Offline-first: the client works without the backend

**Standing rule:** the only things that require the Pi are the things that *are*
the vehicle (live video, live telemetry, and any command that moves the sub).
Everything else works with the Pi off, unplugged, or not yet built — and no panel
checks backend availability for a dependency it doesn't have.

- **PWA (`sw.js`):** a service worker precaches the whole app shell and keeps a
  separate `neptune-tiles` cache. Once installed, the dashboard launches and runs
  with **no network of any kind** — which also permanently solves `file://`, since
  an installed PWA is a real secure origin (geolocation etc. stay available). No CDN
  references anywhere; everything is vendored.
- **Client owns its state (`store.js`, IndexedDB + Cache API):** origin, settings,
  the saved-area registry, and dive logs live client-side. `SAVE OFFLINE` writes
  satellite tiles into the Cache API **from the browser** — you can save an area
  the night before with the Pi powered off. Mirroring to the Pi is an optional
  second copy, never a precondition.
- **Client-first services:** address search and geocoding go **browser → Nominatim
  directly** (they need *internet*, not the Pi); map imagery is fetched by the
  browser and served from the tile cache offline. The old `MAP AREAS: backend
  unreachable` / `search unavailable` anti-patterns are gone.
- **Degradation model (`status.js`):** five states, tracked and shown separately
  because they genuinely fail one at a time — **NET** (internet), **PI** (ROV control
  link), **VIDEO** (go2rtc feed), **CAM** (WOLFANG control plane), **VEH** (vehicle).

  Three glyphs carry them in the top-left status row: **Wi-Fi**, **the tether**, and
  **the camera**. Each has a fixed vocabulary, and none of them reports a state on
  anything weaker than direct evidence.

  **Wi-Fi** has four states, because "no card", "not joined", "joined but going nowhere"
  and "working" call for four different reactions:

  | Glyph | Colour | State |
  |---|---|---|
  | 📶 arcs | green | joined to a network *and* that network reaches the internet |
  | 📶 arcs | amber, steady | a wireless adapter is present, joined to nothing |
  | 📶 arcs | amber, **blinking** | joined, but the network has no internet |
  | 📶 arcs, slashed | red | no wireless adapter on this handheld at all |

  The two ambers are separated by the **blink**, never by colour alone. Wi-Fi is for map
  imagery and address search; it is never in the path of driving the sub.

  **The tether icon changes SHAPE, not just colour**, because "is the link up" and "is
  there a vehicle" were never two questions to the operator — they are one question about
  one cable, and two icons for it was two things to learn. Its red state is about the
  *cable*: with no wired adapter there is nothing for a sub to be on the end of.

  | Shape | Colour | State |
  |---|---|---|
  | 🛥 sub | green | adapter, API and control link all up — a real vehicle is answering |
  | 🛥 sub | red, pulsing | a **leak** — the sub shape is kept on purpose, so a fault can never be mistaken for a dropout |
  | 🔌 plug | amber, blinking | the sub answers, but the control link is not up yet |
  | 🔌 plug | amber, steady | a wired adapter is there with nothing answering on it |
  | ⚡ cut cable | red | no wired adapter — the simulator is flying this |
  | 🤖 robot | red | no launcher, so the adapters cannot be checked; it says so rather than guessing |

  **A socket in `connecting` is not evidence** and no longer reaches amber. It reports
  that state for as long as the handshake has not failed, which against an address that
  will never answer is indefinitely — so the tether light sat amber through an entire
  session spent in the simulator with nothing plugged in. Amber needs a real adapter or a
  real HTTP answer.

  None of this is visible to a browser: it cannot enumerate adapters, and
  `navigator.onLine` cannot tell a network from the internet. It comes from the launcher's
  `/__net`. Shape survives being read at a glance, in sunlight, by someone who is also
  driving — colour alone does not.

  **BALLAST is a syringe**, because that is what the tank is: a barrel of water with a
  plunger. Flat solid flange across a square top, a barrel, and a V tapering to a centred
  point — no needle, because the water does not leave the sub. The liquid *is* the
  plunger: it sits in the taper when empty and rises up the barrel as it fills. Wall and
  liquid are cut from one `clip-path`, so the fill can never square off the taper or spill
  past the barrel, and the drag maps to the visible barrel rather than the element box so
  a full tank does not end up part-hidden behind the flange.

  **Drag UP to fill**, the way a syringe is drawn. Down-to-fill made sense while this was
  a bar — down means go down — and stopped making sense the moment it looked like a
  syringe: pushing a plunger down expels the liquid. FILL is the top arrow now, EMPTY the
  bottom one.

  **One colour means one thing.** The map draws the dive track in twelve depth bands; the
  ballast fill and the Depth / Pressure / Ballast readouts wear the same bands. In SIM
  everything is driven by the ballast input, so it all moves together. On a **real dive**
  depth and pressure are coloured by their own sensor **or not at all** — never from
  ballast, because a sub descending with a dead depth sensor would then show a deepening
  colour it never earned. An unchanging cyan number beside a purple tank is the alarm, and
  painting over it would remove the only symptom.

  **The eye is the ONLY camera indicator**, and it has three states because there are
  three genuinely different situations and the next action differs in each:

  | Eye | State | What to do |
  |---|---|---|
  | 🟢 open | the Pi is talking to the camera | nothing |
  | 🟡 open, **blinking** | the camera's radio is there but the Pi is getting nothing from it | wait, or power-cycle the camera |
  | 🔴 crossed | no radio and no camera | the map is the driving view now |

  **Two observers, because one is not enough.** The Pi's own association (`iwgetid`, via
  `deep.ssid`) says whether *it* is connected — note *association*, not `camera.up`,
  which only means wlan0 is enabled and is true on any Pi that has ever booted; reading
  that as a sighting pinned the eye to amber permanently, camera powered off in another
  building included — but if the Pi's antenna dies while the camera is happily
  broadcasting, the Pi sees nothing and would report the camera dead. The handheld is
  standing right there with a radio of its own, so the launcher's **`/__net`**
  (`netsh wlan show networks`, cached 6 s) tells the page whether the camera's SSID is
  visible *from here* — the same call that carries the Wi-Fi and cable state above. AP visible + Pi silent means the fault is on the sub's
  side and the camera is fine: **amber, not red**.

  Put the camera AP's SSID (or any distinctive part of it) in
  `launch/neptune-camera-ssid.txt` — currently `ActionCam_b981`, matching `CAM_SSID` in
  `install.sh`. Both copies exist because `install.sh` runs on the Pi from a curl pipe
  with the client stripped out; change them together. A browser cannot scan Wi-Fi
  itself, which is why this goes through the launcher at all.

  **The RATE backs off once the camera is up**, but the call never stops: it also carries
  the adapter state, which changes on its own and is never "settled". A radio sweep costs
  something on a handheld that is flying a submarine, so it runs every `apScanMs` (5 s)
  while the eye is red and `apScanIdleMs` (15 s) once it is green, speeding up again by
  itself the moment the camera drops.

  A sighting older than `apScanMaxAgeMs` (20 s) is **dropped rather than believed**, so
  carrying the camera out of range turns the eye red instead of leaving it amber on the
  strength of a minute-old answer.

  Only a **positive** sighting counts. No launcher, radio off, or no SSID configured all
  mean *cannot tell*, and cannot-tell is never evidence of absence — the eye falls back
  to the Pi's own view and nothing is made to look worse than it is. Only the amber state
  blinks; a permanent blink is just noise.

  This replaced three components saying one thing: a `CAMERA LINK DEGRADED` banner across
  the middle of the map (over the very view you fly when the camera is what you lost), a
  `CAM WIFI` readout in the top bar, and the eye.

  Each control declares what it needs in markup (`data-needs="link"`, `"cam"`, …) and
  **only** the controls owned by a down subsystem are greyed. Losing the ROV link no
  longer disables the camera buttons, and nothing disables the map, radar, search,
  saved areas, dive logs, the config panel or the input remapper — those are
  client-owned and work with the Pi switched off.

  This replaced a single `body.backend-down aside { pointer-events: none }` rule that
  killed the entire control rail as one blob whenever the Pi was unreachable.

  The Pi's own health is polled separately (`/api/system`), so CPU/RAM/disk and both
  network interfaces stay visible even while the vehicle link is down. Reconnection is
  automatic and silent everywhere (no retry buttons).
- **Blind nav (`map.js`):** when the camera feed drops for more than
  `CONFIG.map.blindAfterMs` (4 s), the map takes over the full screen as the *driving*
  view so the sub can still be flown on instruments instead of a black rectangle. The
  **camera status icon is an eye** (three states, above), so the operator can never think
  they are looking at water. That replaced a full-width `BLIND NAV · NO CAMERA` banner:
  one glyph in the status row says the same thing and gives the screen back.

  It is deliberately **not** the expanded map: expanding engages an all-stop and switches
  to north-up, because that is a *planning* view. Blind nav keeps `MAP.expanded === false`,
  so everything keyed on it stays in its piloting form — heading-up, following the sub,
  throttle live, no all-stop. Only the layout changes.

  Debounced both ways so a brief WebRTC hiccup cannot flip the view mid-manoeuvre, with a
  shorter window on a cold start (`blindColdMs`) where there is no established feed to blip.
  The video shrinks to a corner tile rather than disappearing: it is how you notice the feed
  return. The tile is a **status indicator, not an exit** — there is no full-screen NO FEED
  state to go back to, and the feed returning is what restores the camera view.
- **Commands never queue (§4):** a `Command` fails fast and visibly when the
  backend is down — rejected, logged (`cmd_rejected`), never buffered or replayed
  (a late `throttle 100%` is a hazard). Only inert data (telemetry/log records)
  buffers through an outage and uploads on reconnect.

## What the sub reports about itself

The signals above are about the *links*. These are about the vehicle on the end of them —
each one a reading that can be taken, or admitted to be untakeable, and never faked.

### A dead sensor and a dropped frame are different words

The link going quiet for a moment and the chip behind a gauge dying are two different
events that demand two different reactions, so they get two different marks. Reading one
as the other is how a sub gets flown on a number nobody is taking.

| On screen | Means | What to do |
|---|---|---|
| `42.7`, tinted by its band | the sensor is reporting | nothing |
| **`--`**, dim, whole bar dashes together | **STALE** — the socket went quiet for a moment | nothing; it comes back on its own |
| **`?`**, amber, **wavy underline** | **CANNOT-TELL** — the chip behind this reading has stopped answering | waiting will not help; go and look at that cable |

The question mark is this console's existing word for *genuinely not known* — the unhomed
syringe has always used it — and it is deliberately **not** the stale dash. A dash reads
as a dropped frame, and a dropped frame is something an operator waits out.

**The last number is not shown, on purpose.** A reading that has frozen and one that is
holding steady look identical, and a frozen depth is the more dangerous of the two: the
sub keeps descending while the console keeps painting the depth it last managed to read.

**Three carriers, so none of them has to be the one that gets noticed:** the mark (`?`),
the amber, and an alert chip that *names the chip* — `NO DEPTH · SENSOR STOPPED`, with
"the vehicle names the MS5837 depth/pressure sensor" in the explanation. A blank gauge
with no cause attached reads as a glitch in the dashboard, and a glitch is something you
wait out. Naming the part turns it into an errand.

Telemetry carries `sensor_faults` — the bare chip names the vehicle uses (`ms5837`,
`bno085`, `ina219`, `i2c`), the same ones in `docs/hardware.md` and on the wiring
diagram. The list only ever supplies the **cause**: a reading goes cannot-tell because
its value is null, so a vehicle too old to report faults still blanks the number instead
of showing a frozen one. A dead `i2c` bus names itself rather than its three passengers,
so one unplugged connector does not report three unrelated sensor failures.

**With no heading there is no track.** The radar is heading-up, so a heading nobody is
measuring rotates the entire map and runs the dead reckoner off in whatever direction the
placeholder happened to be. The bearing shows `?`, the badge reads **`NO BEARING`**, and
the radar stays drawn on the *last angle the compass actually gave* rather than swinging
to a fresh invented one.

`NO BEARING` is deliberately distinct from its two neighbours, because the operator's
next action differs in each:

| Badge | Means |
|---|---|
| `MAG?` | a compass answered and says it is **uncalibrated** — the bearing is suspect |
| `GYRO` | the filter is ignoring the compass **on purpose** — deliberate, not broken |
| `NO COMPASS` | no IMU answered **at all** — `mag_cal` is null, not 0 |
| `NO BEARING` | one answered **earlier in this dive and has now stopped** |

### The leak has two stages, and only one of them is an emergency

One leak flag was answering the wrong question. A film of water in the bilge means *finish
the pass and come home*; water 2 cm higher means *surface now*. Collapsing those into one
signal either cries wolf on condensation or says nothing until it is too late.

| Stage | Probe | What you see | What to do |
|---|---|---|---|
| `NORMAL` | both dry | nothing | — |
| `WARN` | the probe at the lowest point of the hull | **amber, and the sub glyph changes SHAPE** | advisory, non-blocking: water is collecting, finish up |
| `FLOOD` | the probe 2 cm above it | the **red pulsing sub** plus a SURFACE prompt | come up |

FLOOD keeps the pulsing-sub shape it has always had, and keeps it **deliberately distinct
from a link dropout** — a leak is the sub telling you something, a dropout is the sub
telling you nothing, and they demand opposite actions. A probe has to read wet for five
consecutive samples (~0.5 s) before its stage latches, because a launch splash and a droplet
running down the inside of the hull both touch a probe briefly and real ingress does not
stop. An alarm nobody believes is one that gets ignored on the day it is right.

A dead probe reads *dry* forever, which is the one failure this design would otherwise hide,
so the pre-dive readiness check reports a probe whose state is physically impossible — flood
wet while the lower probe is dry cannot happen, since water reaching the upper probe passed
the lower one.

### The battery is 2S, and 24 V was somebody else's vehicle

The pack is **8.4 V full, 7.4 V nominal**. The old `24.8 V` reading and the `20.0 V` sag
floor were placeholders from before the pack existed, and a threshold that describes a
different vehicle does not fail loudly — it reads "full" forever.

| Band | Volts | Colour |
|---|---|---|
| dive on | ≥ 7.0 | green |
| head back | < 7.0 | amber — finish the pass |
| surface | < 6.6 | red, with a SURFACE prompt |
| hard floor | 6.0 | 3.0 V/cell. Below this the cells are damaged, not merely flat |

The **number is always shown** and the colour comes **only** from these bands — one colour,
one meaning, same rule as the depth ramp. Nothing in software enforces the floor: safing a
sub in the middle of a canal trades a damaged pack for an unrecoverable vehicle, so the
operator is told early and the operator decides.

### An estimate never dresses as a measurement

There is a paddlewheel on the hull now, so **SPEED** can be a real number — and when it
cannot, it says so instead of quietly becoming one.

| `speed_src` | Where the number came from | How it reads |
|---|---|---|
| `paddle` / `kf-paddle` | the water-speed sensor | a measurement |
| `lut` / `kf-lut` | the throttle→speed model | visibly styled as an **estimate** |

The wheel cannot sense direction — the sign comes from the throttle — and it stalls below
about 0.1 m/s, so no pulses is reported as *unknown*, never as `0.0 m/s`. "Slower than I can
see" and "stopped" are different claims and only the throttle can tell them apart.

Which is also what makes **SNAGGED** possible, and it is its own warning with its own shape
and words rather than a generic error: sustained thrust with no measured speed means
something is holding the sub. That is the *"the map marches forward while the sub is pinned
on a shopping trolley"* case — the whole reason the wheel is fitted — and navigation
confidence drops with it.

### GYRO ONLY means deliberate, not broken

Heading comes from the IMU's fused yaw, which the thrusters' own magnetic field pollutes.
Two states are surfaced for it, and they are not the same thing:

- **Heading suspect** — the magnetometer calibration is below 2. Flagged everywhere heading
  is shown, HUD and map alike, consistent with the existing confidence degradation.
- **`GYRO ONLY`** — the heading filter is **ignoring the compass on purpose** and coasting on
  the gyro, because the calibration is low or the thrusters are running hard enough to lie.
  The operator has to be able to tell that apart from a broken compass; one is the filter
  working and the other is the filter failing, and they look identical if nobody says which.

### The syringe admits when it doesn't know

Ballast is a stepper driving a plunger with **no position sensor**: the level is a step
count, and a step count means nothing until it has been zeroed against the empty end stop.
So from power-on until the first homing, the syringe shows an **explicit unknown** — not
0 %, not 50 %, per the cannot-tell rule — together with the prompt to home it. A gauge
sitting confidently at half is worse than one admitting it does not know, because only one
of them prompts the action that fixes it.

**Drag up to fill is unchanged**, and so is 0..1 of the stroke; it is now backed by step
truth rather than a simulated tank. If the plunger reaches the full end stop at a count that
disagrees with the calibrated span, steps were skipped — the level is now wrong by an unknown
amount, so `needs re-home` is surfaced rather than swallowed.

### The diagnostics cluster — five readings that were being thrown away

Five things the vehicle measures on every frame reached no readout at all. Four of them —
**turn rate**, **forward acceleration**, **pitch** and **roll** — did not even exist on the
wire: they lived only on navigation's internal sensor sample, so no amount of client work
could have shown them. The fifth, **pack current**, was on the wire and was spent inside
the pack voltage's tooltip (`— drawing 3.1 A`), which an operator on a canal bank in
sunlight with wet hands is never going to hover. A reading nobody can see is a reading the
vehicle did not send.

| Readout | Field | What it tells you |
|---|---|---|
| **turn rate** | `gyro_z_dps`, + = clockwise | the independent witness on the compass. Heading moving with no turn rate is magnetic interference, not rotation; turn rate moving with no heading is a dead magnetometer |
| **forward accel** | `accel_fwd_ms2`, + = ahead | thrust with no acceleration *and* no paddlewheel pulses is the **snag** signature — and it is what separates "the wheel sensor died" from "the sub genuinely is not moving" |
| **pitch** | `pitch_deg`, + = nose up | noses down as the ballast fills. Nose-down getting worse with the ballast steady is water in the bow |
| **roll** | `roll_deg`, + = starboard down | heels into a turn and returns. A standing heel at rest is weight and buoyancy, not software |
| **pack current** | `current_a` | **the fouled-prop reading.** Draw up with speed down at the same throttle is something wrapped round a propeller — the camera looks forward, not aft, and a fouled prop still spins and still makes noise |

What a healthy one looks like at the bench, and what each bad one means, is in
[`docs/hardware.md` §6.4](../docs/hardware.md). What the fields mean on the wire is in
[`api/README.md`](../api/README.md).

**Every one of these has a real zero, and every one of those zeros is the calm answer.**
`0.0 °/s` is *"not turning"*. `0.0 m/s²` is *"coasting"*. `0.0°` is *"level"*. `0.0 A` is
*"drawing nothing"*. So a single `value || null` on the ingest path spells all four
*"the chip is dead"*, and the same coercion written the other way
(`value == null ? 0 : value`) turns a dead IMU into a vehicle sitting perfectly still and
perfectly level — which is exactly the shape of every liveness bug this project has already
shipped four times. Both directions are checked, on every readout that has a real zero
(`client/tests/suites/instrument-cluster.js`).

They obey the same three-way vocabulary as every other reading here, and are held to it by
test rather than by intention: the **number** when the chip is answering, **`?`** in amber
with a wavy underline when it has stopped, and **`--`** dim when the frame is merely late.
All four IMU readings blank *together*, in one frame, with `bno085` named in
`sensor_faults`; pack current blanks *with* the pack voltage, because they are one chip.
Six gauges going to `?` at once with one chip named is the signature of a dead IMU — one of
them alone is not a thing that can happen.

And each carries a written `title` **and** `aria-label` saying in a whole sentence what the
number means, because a label is not an explanation: `Turn` tells a stranger nothing. That
is the same bar `demo-mode` holds the rest of the console to.

#### Where they sit, and what folds

They are **one table** — `FLIGHT_METRICS` in `js/core.js` — and the entry *is* the
reading: the wire field it arrives under, the `state` slot it lands in, the
element it draws into, the group it mounts in, the chip behind it, how it formats, what the
bench model may honestly say about it, and the sentence explaining what it means. `net.js`
ingests off that list, `render.js` builds the markup off it and paints off it. That is
deliberate and it is about *removal*: the operator intends to fly with all five and then cut
back to whichever two earn their room, and the way that has gone wrong four rounds running
is a metric added as markup in one file, ingest in a second and a renderer in a third.
Deleting a reading is deleting one entry.

The two groups mount differently because they answer different questions:

| Group | Where | Folds? |
|---|---|---|
| **`pack`** — Draw (`current_a`) | the top bar, as a **direct sibling immediately after the pack voltage** | no |
| **`attitude`** — Turn, Surge, Pitch, Roll | `#flight-cluster`, **down by the minimap with Speed and the tether range** | yes — tap the `ATTITUDE` heading |

Amps sit beside volts and nowhere else: one chip, one question. A voltage sagging with no
draw beside it cannot be told from a pack that is simply flat, and that is the entire
reading. It is a *sibling* rather than a wrapped pair because the bar is a `space-between`
flex row — a container holding two readings would be one flex item, and the bar's even gaps
would go with it.

The four attitude readings deliberately do **not** go in the top bar. That bar is one
`nowrap` row and it has overlapped itself once already — measured at 1280 px, 13 of 20 tiles
overflowed and 12 pairs of text collided, and it looked fine until a Pi was attached and the
fields filled up. Four more tiles is how that comes back. They belong next to Speed anyway:
they are read the same way Speed is, while flying, with the eye already down at the map.
They travel with Speed into the expanded map too, which is a planning view under all-stop
where nothing is being flown.

Tapping the `ATTITUDE` heading folds and unfolds it, and **the choice survives a reload**
(`localStorage`): a group that reopens itself on every launch is a group the operator folds
on every launch and then stops trusting to stay folded. The heading hands the keyboard back
after the tap, because this handheld is flown on WASD and the paddles and a button that
keeps focus swallows the next Space as *"press me again"* instead of passing it to the sub.

**Folding never hides an admission.** `ATTITUDE` is advisory — nothing on the hull is flown
off it — which is what makes it foldable at all. But the heading carries the group's own
cannot-tell mark, so a folded `ATTITUDE` over a dead IMU still says so, on the line the
operator can see, and it names the chip: `NO COMPASS STOPPED`, or `I2C BUS DOWN` when the
whole bus has gone rather than one connector. A control that can hide a fault is not a
convenience.

The mark is raised only by a **hull**, and never while the frame is merely stale. On the
bench there is no IMU by construction, so a mark lit from power-on would be a mark nobody
reads — the same reasoning that keeps the snag-watch chip silent on a vehicle whose
estimator never ran.

Every one of these readings still carries the mock/real distinction. A simulated number is
always badged as one.

## Layout

```
client/
├── index.html          # markup only — links the CSS + loads the scripts in order
├── origin.html          # standalone phone GNSS capture page → POST /api/origin
├── css/
│   └── styles.css       # all styles (self-contained; replaces Tailwind + fonts + icons)
└── js/
    ├── config.js        # ★ the tunable config — every knob lives here
    ├── core.js          # $ / clamp helpers, LOG bus, state object, host resolution,
    │                    #   and FLIGHT_METRICS / HUD_GROUPS — one entry per secondary reading
    ├── wire.js          # wraps fetch + WebSocket once at load, so everything crosses the log
    ├── store.js         # IndexedDB + Cache API: origin, settings, saved areas, dive logs, stills
    ├── status.js        # the five-subsystem degradation model + /api/system + /__net
    ├── video.js         # WebRTC player (go2rtc) + NO-FEED / reconfiguring overlay
    ├── net.js           # ROV WebSocket link, telemetry ingest, send/ping/level loops
    ├── commands.js      # discrete commands (arm, stop, surface, magnet, lights)
    ├── input.js         # gamepad + keyboard, remappable actions/bindings, computeInput
    ├── controls.js      # on-screen sliders, SURFACE hold, CONFIG/mapper modal
    ├── render.js        # local simulation + telemetry→UI + status badges + the cluster
    ├── camera.js        # WOLFANG camera plane: telemetry, record/capture, config, files
    ├── recorder.js      # the topside half of the two-sided blackbox
    ├── logview.js       # the live LOGS overlay (CONFIG → LOGS)
    ├── tiles.js         # zero-dep raster XYZ satellite tile engine (Esri), overzoom, screen↔latlon
    ├── crt.js           # the chart overlay: CRT layer table (tier · mark · standoff · what it
    │                    #   means for the sub), the three states, and the two depth treatments
    ├── map.js           # radar: camera-primary circular minimap, satellite basemap, track, expand
    ├── navui.js         # origin (device geolocation + tap-to-refine) + navigate-and-select area download
    └── main.js          # RAF frame loop + bootstrap + window.NEPTUNE console API
└── tests/
    ├── run.py           # browser test runner (stdlib + headless Chrome, no deps)
    ├── cdp.py           # ~130-line CDP WebSocket client, for the screenshots
    ├── baseline/        # committed layout portraits; screenshots/ is gitignored
    └── suites/          # one file per concern; see tests/README.md
```

## Tests

```bash
python client/tests/run.py          # every suite; exit 0 only if all pass
python client/tests/run.py tether   # one suite
python client/tests/run.py --list   # what the suites are
python bootstrap.py --test          # both halves of the system, one command
```

Browser checks against the **real dashboard** — `run.py` serves `client/`, injects one
suite as an extra `<script>`, and runs it in headless Chrome. Nothing is stubbed or
rebuilt, so a passing check passed in a browser rather than in an approximation of one.
No framework and no dependencies, same as the client.

**No check total appears on this page, and that is deliberate.** This line used to read
`295 checks`, `tests/README.md` said `249`, `bootstrap.py` said `214` and `.specs/design.md`
said `286` — four totals for one suite, in circulation simultaneously, each copied forward
from whichever tree its writer had open. The runner prints the number; nothing else claims
it. The reasoning, and what may still be written down, is in
[`tests/README.md` → *Where the numbers live*](tests/README.md).

### Running the suites on each platform

The runner is standard-library Python 3.9+ and a browser it locates itself — install
locations first, then `NEPTUNE_CHROME`, then `PATH`, with `--chrome <path>` overriding.

| Platform | What it finds |
|---|---|
| **Windows** (the ROG Ally) | Chrome in `Program Files` / `Program Files (x86)` / `%LOCALAPPDATA%`, then Edge — same engine |
| **macOS** | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| **Raspberry Pi OS / Linux** | `chromium-browser`, `chromium` or `google-chrome` on `PATH` (`sudo apt install -y chromium-browser`) |

Nothing here needs the internet once the browser is on the machine, which is the point —
the Pi is normally on a sealed tether.

**On the Ally, one touch:**

```
Neptune.bat -Test           both suites, then a pass/fail block
Neptune.bat -Test client    the dashboard only
Neptune.bat -Test api       the vehicle only
```

Same double-click as launching the console, because that is the only interaction this
handheld has: no terminal, no keyboard in the field. A check gate that needs a shell is a
check gate that stops being run on the machine it exists to guard. It runs ahead of the
single-instance mutex and opens no port, so it works with a dive already up.

**Green means it ran.** The launcher quotes the runner's own total rather than counting
anything itself, and treats a zero exit with *no total printed*, *a total of zero checks*,
or an `INCOMPLETE` verdict as a failure to run — not a pass. The verdict block carries its
answer in a frame character as well as a colour (`=` passed, `#` failed, `?` could not
tell), for the same reason every gauge on this console does: colour is never the only
carrier, and a phone photo of that window may be all anyone has by the time it is
discussed.

See [`tests/README.md`](tests/README.md) for the suites, how to add one, and the list of
gotchas that have each produced a test which passed while the product was wrong.

### Why classic scripts (not ES modules)

The scripts are plain `<script>` tags sharing global scope, loaded in dependency
order (`config` first, `main` last). This is deliberate: ES modules (`import`/
`export`) are blocked over `file://` (null origin → CORS), and a hard rule here
is that the **same files must run from disk and from the Pi**. Classic scripts
do that. If you add a file, add its `<script>` tag to `index.html` in the right
spot (after anything it references at load time).

## Tuning

Open **`js/config.js`**. Everything is grouped and commented — networking,
input, on-screen controls, and the simulation model. Values are read live, so
just edit and reload. Nothing else hard-codes these numbers.

## App / fullscreen (feels like an app, no URL bar)

- The page **goes fullscreen on your first tap/key** (browsers block auto-fullscreen
  on load). `Esc` exits. It also ships a **web app manifest** (`display:fullscreen`),
  so when served over http you can **Install** it (Chrome ⋮ → *Install Neptune*) and
  launch it chromeless from the app list.
- For a **truly URL-less kiosk boot** on the ROG Ally, launch Chrome/Edge in app mode:
  ```
  chrome --kiosk --app="http://<pi>/?host=<pi>:8000"
  #   or from disk:
  chrome --kiosk --app="file:///path/to/client/index.html?host=<pi>:8000"
  ```
  (`--app` = no tabs/URL bar; `--kiosk` = fullscreen locked. Add `--start-fullscreen`
  if you prefer a normal window that starts maximized.)

## Talking to the Pi backend

The Pi is **backend-only** — it does not serve this folder. Run the client on the Ally
(above) and point it at the Pi with `?host=<pi-ip>` (remembered in localStorage; also
settable in the CONFIG panel). The Pi's nginx sends permissive CORS headers, so the
cross-origin fetches work; WebSockets aren't subject to CORS.

Endpoints the client expects on the Pi:
- `WS  /go2rtc/api/ws?src=sub` → WebRTC video signaling (go2rtc)
- `WS  /ws/control`   → ROV control out + telemetry in
- `WS  /ws/telemetry` → camera status (~15s)
- `REST /api/status · /api/menu · /api/config/{p} · /api/record/toggle · /api/capture · /api/files · …`

For pure UI/dev work with **no Pi**, run the backend locally in mock mode
(`cd api && python main.py`) and just open **`http://localhost:8000/`** — FastAPI
serves the client same-origin, so every gauge animates from the simulator. (A cross-origin
`?host` defaults to plain `http`/`ws`, matching the Pi; add `&secure=1` only if you ever
front the Pi with HTTPS.)

## Navigation & radar

The **camera feed is the primary instrument** and fills the viewport. The map is a
**GTA-style circular radar** in the bottom-left (`#radar`, ~200 px) — the live
basemap clipped to a circle, with the heading needle / input vector, `FWD`/`REV`,
and a rotating **N** drawn on top. `THROTTLE` / `STEER` read out to its right; the
scale bar sits below and updates with zoom. There is exactly **one** map instance;
in the collapsed state it lives inside that circle.

**Heading-up by default** (`CONFIG.map.headingUp`): the map rotates under a fixed
forward-pointing sub marker (via the map's bearing / a canvas transform — never a
CSS transform on a square, which would leave uncovered corners in the circle). Set
`headingUp:false` for north-up.

**Tap the radar to expand** it to fullscreen. The same instance animates open; the
video shrinks to a **picture-in-picture** tile (never unmounted — no WebRTC
reconnect). `Esc`, the ✕ button, or tapping the PiP video collapses it back.

### On a real link, nothing is synthesised

**We never fall back to SIM while a vehicle is on the link.** `vehicleLinked()`
(`core.js`) is the switch, and it separates two things that must never be confused:

| | Comes from | Moves when |
|---|---|---|
| **Input vector** (the purple line on the dial) | `state.input` — your stick, directly | always, in every mode |
| **Sub marker + track** on the map | the vehicle's own navigation output | only when the sub reports movement |

The input vector is *your command* and is drawn from the raw stick every frame, so
the dial answers immediately whether or not anything is fitted to the hull. The sub
marker is *the vehicle*, and it moves on the sub's output or it does not move at all.

The client integrator that advances the marker from commanded throttle is now
**SIM-only, and only with no vehicle linked at all**. Running it against a real hull
draws progress the sub may not be making: a dead thruster, a snagged tether, or a sub
held against a wall would all keep the marker sliding forward exactly as if the dive
were going fine. Underwater that is the one failure you cannot afford to hide — so a
linked sub with no navigation **holds position**, and the **`NO NAV`** badge on the
radar says why (`NO NAV · NO SENSORS` once the map is the driving view, where the
words fit — the collapsed circle only has ~98 px of chord at that height).

A held marker and a working-but-stationary one look identical, which is why the badge
is not optional. This is also why a Pi with nothing wired is worth flying: it reports
`mock:true`, the map stays honest and still, and the dial still shows you steering.

### The tether is 100 m, and the console plans against it

`CONFIG.tether.lengthM` is the cable you actually have. **TETHER** reads out beside the
dial as the straight-line range from the cable's anchor, and a dashed **reachable
circle** is drawn around that anchor on the map. That circle is the answer to *"is this
a good place to put in, and is this mission doable from here"*.

The anchor is the **live handheld position** when there is a fix (see below) — the
cable is held by whoever holds the handheld — falling back to the frame origin, which
is the launch point by definition, in SIM and before any fix.

Range is taken in **3D**: the cable has to reach down as well as out, so descending
shrinks the circle by `√(L² − depth²)`. At 60 m down a 100 m tether reaches 80 m out.

The two modes deliberately differ:

| | Behaviour | Why |
|---|---|---|
| **SIM** | **clamped** at the limit — the sub cannot go further | A dive the cable can't reach must not look reachable on the bench, or planning against it is theatre. `TETHER END 100 m`. |
| **REAL** | **warning only**, never enforced | The launch point moves — you pay out more cable, walk the bank, the boat drifts. A limit the console enforced would be both wrong and dangerous. `TETHER OVER 137/100 m`. |

Amber from `warnFromM` (80 m), red at or past the limit. The clamp only ever pulls the
sub **back toward** the launch point, never pushes it, so a sub at the end of its cable
can always drive home.

### The dial reads as numbers, and never moves

The dial shows **four numbers, 0–100, one per compass point** — how hard you are
pushing that way. Each shows only its own half of an axis, so "how much am I giving
it forward" is read straight off the top rather than decoded from a signed percentage.
They are dim at rest and lit on the side actually being driven, which keeps the circle
clean. They replace the old `FWD`/`REV` word labels and the separate THROTTLE/STEER
text block, which said the same thing twice.

**BLIND NAV does not move the dial.** Same corner, same size, same side as with a live
feed — only the *map* changes, leaving the circle to fill the screen behind it. Losing
the camera should feel like the picture falling away from behind the instruments, not
like a different application. `#radar` keeps its 200 px box (and its border — the dial
is `inset:0` inside it, so dropping the border shifted the dial by 1 px and grew it by
2); `#map-panel` is `position:fixed`, so it escapes the circle on its own. The dial's
blind-mode backdrop ring is a **`box-shadow` spread, not a border**, precisely so it
adds nothing to the box and the geometry stays identical.

The dial is **not tappable in blind nav** — a stray tap would expand the map, and that
engages ALL STOP on a sub that is being driven.

Those numbers are also how you **tune the simulator against the real vehicle**: run the
same throttle on both, compare distance covered, set `CONFIG.map.subMaxSpeedMs`.

### The handheld's position is live

`watchPosition` keeps the operator's own position current, like any other map app — a
fix taken once on load is wrong the moment you walk the bank looking for somewhere to
put in, which is exactly when the reachable circle matters most.

Two things move, and they are **not** the same thing:

| | What it is | Colour | When it moves |
|---|---|---|---|
| `MAP.me` | **the operator** — where the handheld is now | green / yellow / red, see below | always, automatically |
| sub marker | **the ROV** | purple arrow | only on the sub's own navigation, or when you pinpoint it |
| `MAP.origin` | the datum the sub is dead-reckoned **from** | orange cross | only before a dive |

**The operator dot is colour-coded by where its position actually came from.** The
tether range is measured *from* that dot, so how much to trust it has to be readable at
a glance rather than looked up:

| Colour | `meSource()` | Meaning | Tether tag |
|---|---|---|---|
| 🟢 green | `live` | a fresh fix from the handheld | — |
| 🟡 yellow | `stale` | last known fix, older than `meStaleMs` (30 s) | `LAST KNOWN` |
| 🔴 red | `mock` | placed by hand — planning, not a measurement | `PLANNED` |

A mocked dot also gets a **dashed ring**, so the state survives a colour-blind operator
and a sunlit screen.

**Planning from somewhere you are not standing.** *"Could I reach that culvert if I put
in from the far bank?"* is a question about a launch point you are not on. The **⚑**
button arms a tap that moves your dot there and turns it red; pressing it again returns
to the live fix (`NEPTUNE.mockMe()` / `mockMeAt(lat,lon)` / `clearMock()`). Pre-dive it
takes the launch point with it, because that is the thing being planned. Real fixes keep
arriving underneath and are recorded in `MAP.meReal` — they just do not overwrite the
mock or drag the launch point back — so clearing lands you on something current.

This is deliberately allowed on a real link as well as in SIM: the Pi is usually
connected during bench planning, so forbidding it would make the feature useless exactly
when it is wanted. Safety comes from the state being *unmistakable* rather than from
prohibition — red dot, dashed ring, `PLANNED` on the range.

The origin follows the handheld until a dive is under way — you are still choosing a
departure point. After that the datum **freezes**: moving it would shift every
coordinate already plotted, so the sub would appear to jump sideways and the recorded
track would be a lie.

"Under way" is `diveUnderway()`, and it is **`track.length > 1`, not `> 0`**. `pushTrack`
records a point the moment an origin exists and then dedupes anything within 0.25 m, so
a stationary sub holds at exactly *one* point however long it sits there. Testing for
`> 0` therefore meant "the map has been running", which silently disabled the whole
follow-the-operator behaviour about a second after boot. More than one point means the
sub actually moved — the only definition that does not depend on remembering to press
start. When the datum does move, `rebaseFrame()` shifts the sub **and every plotted
point** by the same delta, so nothing changes position in the world.

When the origin does follow, the sub is **re-based into the new frame**, not zeroed.
The operator moved; the ROV did not. Zeroing dragged the sub along with whoever was
holding the handheld, which is exactly backwards — the growing gap between the two
*is* the tether, and that gap is what the range readout measures.

The **tether anchors on the operator**, not the datum, because the cable is held by
whoever holds the handheld. Walk 20 m up the bank and the reachable circle walks with
you and the range updates.

With **no fix at all** the operator is *assumed* to be at the launch point — that is
where they were when they set it — and the dot says so by being yellow. Without that,
`MAP.me` stayed null and everything anchored to the operator quietly fell back to the
launch point, which draws a circle around somewhere the sub can no longer necessarily
reach. The ring's radius is now measured **through the same projection that placed its
centre** (project a point one tether-length away, take the screen distance); deriving it
from `dpr/curScale()` was wrong over imagery, where the tiles use the tile projection —
so centre and radius came from two different mappings and the circle did not sit where
its own arithmetic said it did.

**Pinpointing the ROV.** The operator's position is known; the sub's is not — there is
no GNSS underwater and, until the IMU is wired, nothing on board can say where it
drifted to. So the default assumption is the only honest one available: the ROV is
where the operator was when the launch point was set. That is a guess, and the operator
is the one who can correct it by eye. The **◎** button on the map (or `NEPTUNE.setRov()`,
or `NEPTUNE.setRovAt(lat,lon)`) arms a tap that places the sub. It moves **only** the
sub — the datum stays put, so the track and every earlier coordinate stay valid.

A placement **further from the operator than the cable is long is refused**, with the
range and the reason. The tether is a hard physical limit, so such a position cannot
exist; silently clamping it would invent a location nobody chose. Refusing says which
of the two points is actually wrong — nearly always the operator's own, since the ROV
is the one they can see. Move your position first (or plan from one, below) and the
same spot is then accepted.

### Old paths stay, but journeys are never joined

Traces accumulate across planning runs, which is the point — you can see everywhere
you have tried. But two disjoint journeys must never be drawn as one stroke: a straight
line between them reads as the sub having travelled it, which it never did.

`breakTrack()` marks the next point as the start of a new segment, and the drawing code
starts a fresh subpath there instead of joining. Breaks are inserted when a plan starts
or ends and when the ROV is placed by hand — every jump that is not travel. They are
preserved through **both** thinning passes (display decimation and the stored-track cap);
a decimation that drops a break silently re-joins the journeys it exists to separate.

The **eye button** hides and shows the traces — after a few runs the map fills up, which
is exactly what makes them worth keeping *and* worth hiding. It only affects the history:
the sub marker, origin, operator dot and tether ring are always drawn, and hiding
discards nothing.

There is deliberately **no accuracy halo**. It was a large translucent disc sitting on
top of the imagery, and the imagery is the point: underwater structures and obstacles
have to be readable. The orange tether ring is the only circle on this map.

Guards: it never prompts (the watch only starts once permission is already granted, and
picks it up live via `permissions.onchange` if you grant it later); movement under
`meMinMoveM` (3 m) is treated as jitter; the origin is never rewritten faster than
`meMinGapMs`; a fix that is less accurate than the stored one is never adopted; and
beyond `originMoveM` you are somewhere else entirely, which still gets the explicit
**USE MY POSITION / KEEP** prompt.

> **On the ROG Ally there is no GNSS, and on a sealed tether there is no fix at all.**
> The handheld has no GPS receiver, so the browser locates it by sending nearby Wi-Fi
> networks to Google's location service — which needs **internet**. With the tether
> offline there is nothing to ask, so no amount of granting permissions will produce a
> position; `POSITION_UNAVAILABLE` is the correct answer, not a fault. The failure text
> says exactly that when `STATUS.internet` is false, rather than sending you hunting for
> a satellite that was never there.
>
> `NEPTUNE.geoCheck()` answers "why is there no position" in one call — secure context,
> permission API, whether the watch is running, internet, and the last genuine fix.
> **Tapping the map is the accurate route anyway**: ±8 m against ±50 m from Wi-Fi.

### Depth reads as twelve even bands, with a key

The original ramp swept one hue into another at matching lightness, so about four steps
were separable and the rest read as "some sort of green". Depth is quantised into
**12 bands generated in OKLCH**, running **orange at the surface → purple at the bottom**.
Evenly spaced HSL is *not* evenly spaced to the eye (equal hue steps crawl through the
yellows and sprint through the blues), which is what left a wide flat teal at the deep end
when the ramp was hand-picked. Oklch lightness and hue are perceptually uniform, so equal
numeric steps really do look equal: measured in Oklab the 11 steps span 0.048–0.072, a
ratio of 1.49 across a **258° hue sweep** — the extra travel is what buys separable
neighbours, and both ends are now colours with names rather than shades of the same thing.
Lightness still falls the whole way, so it reads as depth and not merely as a rainbow.

Twelve, not twenty — twenty was an arbitrary number and past the point where the eye holds
the steps apart. A dozen leaves each band clearly its own colour and keeps the key short.

The expanded and blind views draw a compact vertical **depth scale**
(`drawDepthLegend`), surface at the top, `maxDepthColorM+` at the bottom — the `+` because
the deepest band is a clamp that catches everything below it. It hides with the tracks,
since it exists only to explain them.

The same twelve bands colour the two **chart depth layers**, so one colour keeps one
meaning everywhere on this console — see
[*The chart layers*](#the-chart-layers--what-is-in-this-water-and-what-nobody-looked-for),
where the key grows two more swatches to say how much the number behind each colour is
worth.

### Moving the map

Both full-screen views (expanded and BLIND NAV) can be **dragged with a finger**. Where
imagery is drawn the pan is computed absolutely from the drag-start centre so it tracks
the finger exactly; with no basemap it falls back to the metre frame, so a drag still
moves the map instead of doing nothing.

The **right stick pans too** whenever the map is the view. Nothing on the hull moves with
that stick yet (camera pan/tilt is an unwired `TODO(hardware)`), and a full-screen map you
can only move by reaching across the screen is awkward on a handheld. While the map has
the stick the camera is deliberately **not** commanded — otherwise leaving the map would
hand back a camera pointed somewhere the operator never chose. Close the map and the stick
returns to the camera on the next frame.

**Driving retakes the view.** Panning is a halted-operator's luxury: any throttle or
steer past the deadzone re-arms `MAP.follow`, so a parked view can never outlive the
decision to move. Without it the craft simply swims out of frame and the operator ends up
flying the *view* as well as the vehicle — the wrong thing to be doing while under way.
Below the deadzone nothing is stolen, so a deliberate pan survives a twitchy stick. The
expanded map has always handled its own case in `computeInput` (driving collapses it
outright, since it engages ALL STOP).

**Which axes are the right stick is detected, not assumed.** The left stick is axes 0/1
everywhere, which is why driving always worked. The right stick is only axes **2/3** under
the Gamepad API's *standard* mapping, where triggers are buttons. A pad reporting a
non-standard mapping commonly puts the triggers in the axis list instead —
`[LX, LY, LT, RX, RY, RT]` — and then axis 2 is a trigger and the stick is **3/4**. Reading
2/3 on such a pad gives a dead horizontal axis and a vertical one wired to the stick's X,
which presents exactly as *"it only pans up and down, sideways does nothing"*.

`rightStickAxes()` picks 3/4 for a non-standard pad with 6+ axes and 2/3 otherwise, logs
which it chose, and is overridden by `CONFIG.rightStickAxes = {x:3, y:4}`.
**`NEPTUNE.axes()`** prints the live values of every axis, so finding the right pair on an
unknown pad takes one glance.

### Zoom: maximum imagery, and the paddles

The map **opens on the sharpest imagery the provider has** rather than a fixed
metres-per-pixel. Mercator resolution is latitude-dependent, so the scale that lands on
the deepest tile zoom is computed once a view centre exists (`maxZoomScale`) — at 51.5°N,
z19 is 0.186 m/px against the old fixed 0.600. It is a one-shot pin: after that the
operator owns the zoom and nothing moves it under them. Tile selection also **rounds up**
now (`preferSharpTiles`), so a coarse tile is never upscaled when a finer one exists.

> Which imagery you get is the provider's choice, not ours. Esri World Imagery is a
> single curated, largely cloud-free mosaic — there is no way for a client to ask for
> "a sunny day" or a particular capture date. Maximum resolution is what *is* selectable,
> and that is what this does. Downloading an offline area caches whatever that mosaic
> currently holds for the bbox.

The **ROG Ally paddles zoom the map**: `F10` in, `F9` out (`CONFIG.map.zoomKeys`), and
they zoom whichever view is on screen — the radar has its own scale, so zooming the big
map from the collapsed view would look like a dead control.

They fire on **release**, not press, and only if the other paddle was never touched
during the hold. Both paddles held together is SURFACE; zooming on keydown would mean
every attempt at that combo started by zooming the map, and an emergency has to feel
like one deliberate gesture.

### A submarine can't be paused (§3)

GTA freezes the world when the map opens; a sub keeps drifting. So expanding the
map issues a **safe all-stop** instead (`CONFIG.map.allStopOnExpand`, default on):
throttle+steer are held at zero and **`ALL STOP — MAP OPEN`** is shown. Telemetry,
video, recording, and every safety indicator keep running at full rate; depth, the
`SURFACE` warning, and the whole status strip stay live over the map. **Any thrust
or steer input instantly collapses the map and returns control** — the operator
never has to find a close button to drive. Set `allStopOnExpand:false` to hold
station under power in current instead.

### Satellite basemap (§3)

All map views default to **satellite imagery** — `js/tiles.js` is a tiny zero-dep
raster XYZ tile layer drawn straight to the radar canvas (no MapLibre needed). It
handles the **Esri World Imagery** `{z}/{y}/{x}` order (y before x), heading-up
rotation (a canvas transform, so no uncovered corners in the circle), **overzoom**
from the nearest cached parent when a high-zoom tile 404s (blurry beats blank), and
a dark **readability tint** over the imagery (darker in the radar, lighter expanded).

Provider is configurable in `config.js` (`tileProvider` / `tileProviders`):
- **online** (default): Esri World Imagery, fetched straight from the browser while
  the Pi has connectivity — used to find and download areas. `Imagery © Esri` is
  shown in the expanded view.
- **offline**: when a downloaded area is **active**, tiles come from the Pi's cached
  MBTiles (`/api/areas/{name}/tiles/{z}/{x}/{y}.jpg`) — works with no internet, the
  field case.

The **OSM waterway centreline** (the snapping target) is fetched during download and
drawn as a bright-cored, dark-cased line over the imagery.

**Honest empty states (§6):** no area/origin → compact `NO MAP` / `NO ORIGIN` in the
circle (no fake marker), full explanation in the expanded view. The overlay steps
aside while you're actively tapping an origin or selecting an area.

### Setting the origin (§2)

The fix comes from the **handheld's own browser**. On load with no origin, Neptune
**auto-requests** `getCurrentPosition` (needs a secure context — which is why the
launcher serves it from `localhost`) and centres the map on you. The ROG Ally has no GNSS — Windows resolves position by WiFi,
so accuracy reads tens–hundreds of metres; the **ORIGIN** tile shows it (`SET ±120m`),
and above `originRefineM` (30 m) a non-blocking **TAP TO REFINE** prompt lets you tap
your bank on the imagery (which beats WiFi). **North comes from the sub's IMU**
(`heading0`, captured atomically in the same set-origin action) — never the handheld.
Manual entry and post-hoc dx/dy/rotation adjustment remain via the ORIGIN tile. The
phone page `origin.html` also still works.

### Navigate-and-select area download (§4)

No coordinates to type. Expand the map, **pan/zoom** to the spot (starting from your
location), press **＋ AREA**: a fixed selection rectangle overlays the viewport and a
live **`~N tiles · ~M MB`** readout updates as you move. Pick **Standard** (z16–18) or
**High** (z19), press **DOWNLOAD THIS AREA** — one button. The Pi walks the tile
pyramid (rate-limited, real User-Agent), writes an MBTiles archive, fetches the
waterway centreline, and **auto-names** the area by reverse geocoding. Progress
streams over `/ws/nav`. Downloaded areas are a simple list (thumbnail · size ·
activate · delete). Place search (Nominatim, online) pans the map from the toolbar.

### The HUD is sacrosanct

The whole map (tile fetch, projection, snapping, redraw) runs inside an error
boundary. A basemap failure, a 404, or a draw error leaves the radar **blank but all
instruments — thrust, steering, video — fully live**. The map never captures
gamepad/keyboard piloting input (handlers are scoped to the canvas; MapLibre, if ever
vendored, is `keyboard:false`).

## The chart layers — what is in this water, and what nobody looked for

Under the imagery sits the Canal & River Trust's own published asset data: locks, weirs,
sluices, culverts, tunnel portals, outfalls, and the long tail of operational and
incidental features. `js/crt.js` draws it and — more to the point — says what each mark
means **for a 5 kg tethered sub on a reel of cable**, which is never what the layer is
called. *Weir* is a noun. *"Anything that gets over the sill goes with the water and does
not come back, and so does the tether"* is the fact you actually need standing on a wet
towpath, and it is the only version of it that is any use there.

Open it with the **layers** button (the stack-of-sheets glyph) in the map tool column.
The panel shows in the **expanded map and in blind nav** — blind nav is the *driving*
view, which is precisely when knowing where the culverts are matters most.

Nothing in this file touches the internet. Every request goes to the Pi over the tether
carrying its own 4 s timeout (`CRT_API.timeoutMs`), and a Pi that does not answer
produces a stated unknown rather than a hung overlay. The data got onto the Pi at
bootstrap; see *[Before you dive in new water](#before-you-dive-in-new-water)* below.

### CRT publish no flow data

**The Trust publish no flow measurement of any kind. Every current claim on this console
is an inference from where the structures are, and nothing anywhere on it claims a
measured flow.** The hazard marks are the honest proxy for *expect current here* — a
place, not a reading. If you take one sentence out of this file, take that one.

It is enforced rather than remembered. Any row that is a hazard, is drawn in the hazard
colour, or merely *talks about water moving* has the no-flow clause appended to its
explanation automatically (`crtWhat`), so a hazard added next year cannot be the one that
forgets to say it. FEEDERS is why the `flowProxy` flag exists at all: it shipped stating a
current as fact — *"It is a current entering the cut"* — which made it the one
water-movement claim on the console not marked as an inference. A feeder can be shut. The
mark is a position out of a structure file either way.

### Three tiers, and tier 1 has no switch

The tier is a **safety decision, not a display preference**.

| Tier | | What is in it | Default |
|---|---|---|---|
| **1 HAZARDS** | red octagon | LOCKS · WEIRS · SLUICES · CULVERTS · TUNNEL PORTALS · TUNNELS · OUTFALLS | **always drawn, no switch** |
| **2 OPERATIONS** | green rounded square | TOWPATH ACCESS · SLIPWAYS · WHARVES · WINDING HOLES · BRIDGES · MOORINGS · SAFETY GATES · STOP PLANK GROOVES · FEEDERS, plus the two DEPTH layers | on, toggleable |
| **3 EXTRAS** | lilac dot | AQUEDUCTS · WATER POINTS · BOATER FACILITIES · PUMPING STATIONS · BOATYARDS · MILEPOSTS · NOTICES & STOPPAGES · TOWPATH · DOCKS · BOAT LIFTS · EMBANKMENTS · RESERVOIRS · CANAL LINES · PLANNING BUFFER · ANGLING | **off**, and off means *not asked* |

Seven hazards, eleven operations, fifteen extras — thirty-three rows in one table
(`CRT_LAYERS`), where the entry *is* the layer: its tier, its mark, its standoff, and the
sentence explaining it. Adding a layer is adding a row; the panel builds itself from it,
so a layer cannot ship as a bare glyph with nobody's explanation attached.

**Tier 1 cannot be switched off, and that is the whole design.** These are entrainment,
suction, and nowhere-to-retrieve-a-snagged-ROV-from. A toggle on those is a toggle
somebody turns off once, on the bench, to see the imagery underneath — and then forgets,
and then dives. `crtSetOn` *refuses* a tier-1 layer and writes a line to the log saying it
refused, rather than silently ignoring the call: a control that does nothing and says
nothing is how somebody concludes the hazard layer was switched off when it never was. In
the panel the word **`ALWAYS`** sits in a dashed **red** box exactly where the switch would
be, so the *absence of a control* is itself visible instead of looking like a row that
failed to render.

Red, and specifically not the console's `--hazard` orange, which it used to share. The chart
marks went red when the hazard glyphs did, and a panel still labelling them in the same
orange the console uses for its own alarms made "a lock in the water" and "a download has
stalled" read as the same class of thing. Tier-1 identity — the `ALWAYS` pill, the HAZARDS
heading, the key glyph — is `--crt-hazard`, paired with `CRT_C.hazard` in `crt.js` so the
panel and the map cannot disagree about what a hazard looks like. Fetch and network chrome
stays orange, because a stalled download is a warning about the console, not about the cut.

Draw order is a safety order — extras, then operations, then hazards on top. A mooring
glyph must never be able to sit over a lock.

**Shape first, colour second**, the same rule the leak drop and the ROV glyph follow: an
operator who cannot pick red out of green still has to tell a lock from a slipway. The
hazard mark is an octagon — the stop-sign shape — filled solid with the letters knocked
out of it. Operations are hollow rounded squares, extras plain dots. The panel rows draw
the *same* shapes in SVG, so the key and the map are one vocabulary and not two.

### What each mark means for this vehicle

The marks were not chosen because the Trust publishes them. They were chosen because each
one names a way to lose this vehicle, and the three tier-1 families are three different
ways:

- **entrainment and suction** — locks and sluices: water moving faster than a 5 kg sub can
  swim, and nothing anywhere sized to keep a 30 cm vehicle out;
- **one-way doors** — weirs and culverts: over the sill or into the pipe and the sub goes
  with the water and cannot come back, and *the tether goes with it*;
- **water arriving with no throttle applied** — outfalls, and FEEDERS one tier down;
- **nowhere to recover from** — tunnels and their portals: no daylight, no line of sight,
  no bank, and a cable rubbing brickwork the whole way.

| Mark | Layer | What it is for this vehicle |
|---|---|---|
| **L** | LOCKS | Tonnes of water in a couple of minutes. The pull at an open paddle is far beyond anything this sub can swim against — it ends up in the chamber, under a gate, or inside the side culverts, and nothing is recovered from those. Keep the sub **and the slack of the tether** outside the ring |
| **W** | WEIRS | A fixed sill and a drop. A tether over it is being pulled by the whole overspill, not by the sub. Dry and unremarkable most of the year; running hard exactly after the rain that washed in the rubbish you came to lift |
| **S** | SLUICES | A drain with a suction field in front of it. Whatever goes through is on the far side of a structure you cannot reach |
| **C** | CULVERTS | The water goes into a buried pipe. A sub that follows the flow in cannot turn round, cannot be seen and cannot be swum out — and **the tether must never follow it**, because a cable jammed inside a pipe you cannot reach is how the sub is lost for good |
| **T** / **TN** | TUNNEL PORTALS / TUNNELS | The mouth, and the length. Two rows because the Trust publishes two services, both tier 1 — splitting them would draw a keep-away mark at the mouth and an optional dotted line through the thing the mark is warning about. The tunnel gets **no ring**: the hazard is not a point to stay away from, it is the entire line |
| **OF** | OUTFALLS | Inflow from a drain, stream or spillway. It pushes the sub off course with no throttle applied at all, and it is strongest after heavy rain — which is also when a canal is most worth cleaning |
| **A** · **SL** | TOWPATH ACCESS · SLIPWAYS | Where you can get the sub *in and out*, and where you could walk to and reach in if it had to be recovered by hand rather than driven home. A slipway is the one edge a heavy sub can be walked in and out of without being lifted over a coping stone by somebody kneeling on wet stone |
| **WH** | WHARVES | Deep water hard against a built vertical wall, mooring ironwork and chains under the surface, and no shelving bank anywhere to land the sub on |
| **WD** | WINDING HOLES | The widest open water on most stretches — room to manoeuvre, and also the one place a full-length boat will be swinging its propeller across the whole channel |
| **B** | BRIDGES | The channel narrows and darkens, and the bed collects whatever went off the parapet — very often exactly what you came to lift. Narrow also means the tether has two walls to find instead of none |
| **M** | MOORINGS | Chains, ropes, pins and propellers under the surface, and a moored boat can start its engine without warning while the sub is beside it |
| **SG** · **SP** | SAFETY GATES · STOP PLANK GROOVES | Heavy ironwork in a slot, closing when the water needs it to rather than when you are ready; and narrow sharp-edged grooves exactly the size to trap a tether. A stretch that has been planked off can be drained with no notice reaching you at all |
| **F** | FEEDERS | The structural reason to *expect* water pushing in sideways, and the sub is the lightest thing in it |

Tier 3 is landmarks, context and the things worth keeping but not worth the clutter —
except for two, which carry `hazardish` and are therefore **drawn in the hazard colour and
carry the hazard clauses** the moment you switch them on: **PUMPING STATIONS** (the part
that matters is the intake, an entrainment hazard of the same family as a sluice, and it
is not always published as a feature of its own) and **BOAT LIFTS** (treat it as a lock
several times the size). They sit in EXTRAS because the hazard tier is a fixed list and
they are not on it — so the honest thing is to keep the list fixed and paint them the
warning colour, rather than quietly grow the tier.

**Layers this console has never heard of.** The Pi can publish a layer that is not in the
table. The safe default for an unknown *name* is not "extras, off": if it has `weir`,
`lock`, `sluice`, `culvert`, `tunnel`, `outfall`, `siphon`, `intake`, `penstock`,
`spillway` or `paddle` in it, it is drawn as a **hazard** — and its explanation says, in
its own words, that it was classified **by its name rather than by a rule anybody wrote**.
An inference marked as an inference, exactly like the flow clause. Anything else is
adopted into tier 3 and says plainly that nothing on this handheld knows what it means for
the vehicle, which is why it is off — *not* that it is unimportant.

**The standoff is a sentence, not a ring.** Each tier-1 mark carries a keep-away distance
(locks 30 m, weirs 35 m, sluices 30 m, culverts 25 m, portals 25 m, outfalls 20 m) and every
one of those sentences says it is a fixed standoff **this console states, chosen by us** —
not a surveyed danger area, and the real one may be larger.

A dashed ring used to be drawn at that radius, and it is gone. Around every tier-1 mark it
put eight overlapping circles across one screen and buried the centreline underneath; a ring
drawn around everything stops meaning anything, the same way an alarm that fires on a healthy
console does. Hazards are simply RED now, which survives clutter, and the distance lives in
the words where it can be read as a number instead of estimated from a radius — judge it
against the scale bar.

### Nominal versus surveyed depth

Both depth layers use the **same twelve OKLCH bands** as the dive track and the ballast
tank, so a colour means one thing everywhere on this console. **Only the texture says how
much the number behind the colour is worth** — which is the same measured-versus-estimated
distinction the SPEED readout already draws with its tilde and `EST` tag; this is that
idiom applied to the map.

| | Drawn as | The claim |
|---|---|---|
| **DEPTH — NOMINAL** (`DN`) | washed out (28 %), **hatched**, with a dashed thread down the middle | the *published design depth* — what the channel is **supposed** to be |
| **DEPTH — SURVEYED** (`DS`) | **solid** (62 %) and **outlined** in white | cells this sub has actually been through |

**Nominal is a claim, and it is drawn as one.** The figure comes from the Trust's
published guideline draught for the class of waterway (`api/nav/nominal.py`), never from
an instrument. Read the band the way it is drawn: its **length** is the waterway section's
own published geometry and the figure applies along all of it, but its **width** is a
drawing convention of ours — nobody publishes how wide the cut is, so 7 m is us saying
"about the width of a canal" so the claim is visible over the water it is about. The
shallow edges are in no number here. A section the Trust records as not fully navigable is
painted **grey**, not shallow and not absent: the guideline is withheld rather than a
confident metre of water invented over it.

The hatch is not decoration and it is deliberately loud. A first pass drew it at a quarter
of this strength and it simply vanished over satellite imagery, at which point the nominal
cells looked exactly like surveyed ones with the outline turned off — which is precisely
the published-versus-measured confusion the texture exists to prevent.

**A surveyed cell is a lower bound, not the bed.** Nothing aboard measures the depth of
the bed — there is no echosounder and no altimeter. What the Pi stores is the deepest the
*sub* got in that cell while the journal showed it arriving on something solid, and every
error in that has the same sign: the pressure port sits above the keel, it may have landed
on silt or weed or a sunken trolley, and canal levels move with rainfall and lock use so
the surface it was measured from is the surface of *that day*. **The bed is at least this
deep and may be deeper.** A lower bound is the shape of answer that is safe to be wrong in
— it under-promises clearance. The map starts nominal everywhere and turns solid where the
sub has been.

**Anywhere not drawn is UNSURVEYED**, which is neither shallow nor zero. It is the same
doctrine as the layer states below, one level down: an uncoloured cell means nobody has
been there, not that there is nothing there.

**`+N` on the surveyed row is this session.** Cells the sub drives through now are binned
by the console (3 m bins in the local frame, deepest sample wins) and drawn with the
surveyed treatment immediately, before any dive log has been written. They are **counted
apart** in the pill and in the sentence, because folding them into one number would claim
the Pi holds a survey it has never been sent — and would put the row in the position of
reporting the saved survey ABSENT while measured cells are visibly painted on the map.

> Be aware of what the live `+N` cells actually are. The Pi's saved survey only accepts a
> sample as a sounding when the journal shows **bottom contact** — the sub stopped
> descending while the syringe was still taking on water — because `api/nav/soundings.py`
> deliberately refuses to bin every sample: technically every depth reading is a lower
> bound on the bed under it, and binning them all produces a full, plausible,
> technically-true map that reads as *"the canal is 1.2 m here"* when it is 2.5 m. The
> console's live cells have no such test — they are every track point deeper than 5 cm —
> so they are **valid but much looser** bounds than the saved ones beside them, drawn in
> the same ink. Read a run of fresh cells as *"there was at least this much water under the
> hull"*, not as *"the bed is here"*.

Both treatments have a **key**, drawn under the depth ramp in the expanded and blind views
whenever either depth layer is on: two swatches labelled `NOMINAL` and `SURVEYED`. The
ramp says how deep; the swatches say how much the number behind the colour is worth.

### ABSENT, CORRUPT and EMPTY — three answers, one of them safe

*"You never downloaded this"*, *"what you downloaded is unusable"* and *"there are no
hazards here"* are three different claims and they must never collapse into one. On a map
they look identical: a stretch of water with no marks on it. This is the safety-critical
distinction in the whole feature.

Every row carries a state pill — the glance-level answer — and no two states share a
colour. The whole sentence behind each one is the row's `title` and `aria-label`, rewritten
live as the state changes, so it is available on hover and to a screen reader; the pill is
what has to survive being read while driving.

| Pill | Colour | What happened | What to do |
|---|---|---|---|
| `SHOWN · 12` | green | the Pi has the file and there are 12 features in this area | — |
| `NONE MAPPED` | dim | the Pi **has** this layer and there is nothing of this kind inside the downloaded area. **A real answer** | nothing — this is a survey result, not a gap |
| `ABSENT` | red | the Pi looked and the file is not on the disk. **An empty map here means NO DATA, not NONE** | fetch it, with a network, before you dive |
| `CANNOT TELL` | amber | nobody could be asked at all | replug, restart, or wait — it retries itself |
| `NOT ASKED` | dim lilac | the layer is switched off, so the console has never requested it. **Not a claim** | switch it on and it will say which of the above applies |
| `NO AREA` | dim lilac | no map area is active, so there is nothing to ask about | activate or download an area |

`ABSENT` deliberately shows **no count**. `ABSENT · 0` would be the same lie with a number
on it.

**The index is the gate, and this is why the vocabulary works.** A per-layer `404` means
"that file is not on the disk" — but it *also* happens on a Pi with no chart service at
all, and those are not the same claim. So the console asks the index first
(`GET /api/areas/{area}/crt`), and **the index answering is what earns it the right to
report per-layer absence**. No index, no claim: every row reads CANNOT TELL, which is the
truth about a console that has not been able to ask anybody anything. Equally, `off` never
becomes `absent`: a layer nobody asked about cannot be reported missing.

**CORRUPT is the Pi's third state**, and the client shows it by *quoting the Pi rather than
paraphrasing it*. A file that is present and will not parse is neither an empty canal nor a
missing download, and the remedies are opposite — re-fetch versus *delete the file, then
re-fetch*. The server never serves such a file as an empty layer (`api/nav/service.py`
`_read_layer`); it reports it separately, and the row's explanation carries the Pi's own
words: *"the file is on this card and could not be parsed … delete the file and re-run the
fetch while there is still internet"*.

**Its pill reads `ABSENT`** — the console has one red pill where the Pi has two different
kinds of missing — so on a red row, read the sentence as well: the pill tells you data is
missing, the Pi's sentence tells you which errand fixes it, and re-running a fetch that
already ran will be refused by the very card that broke it. The same applies to a layer the
fetch skipped part-way: the Pi wrote **no file at all** rather than a truncated one, because
a truncated hazard layer is indistinguishable from an empty canal, and the row quotes that
reason too.

**And the map says it, not only the panel.** A doctrine about what an empty-looking map
means cannot be delivered by a panel somebody has to open first. A badge sits on the map
itself, over the radar, with tier 1 getting its own louder wording:

| Badge | When |
|---|---|
| **`HAZARD LAYERS ABSENT (n)`** — red | one or more tier-1 layers are missing from this Pi |
| **`HAZARD LAYERS · CANNOT TELL`** — red | *every* missing tier-1 layer is a cannot-tell: nothing has been ruled in or out |
| `n CHART LAYERS MISSING` — dim | the hazards are loaded; some operational or extra layers are not held at all. MISSING, not switched off: with nothing hidden any more, "not shown" would read as a display choice |

The collapsed circle has no room for those words, so it shortens them to **`NO HAZARD
DATA`** and `CHART GAPS` and keeps the colour. The panel is where the row-by-row answer is,
and the badge's own explanation ends with the sentence the whole feature exists for: **do
not read the absence of a mark as clear water.**

This is also why there is exactly **one** weir row. There used to be two — `weirs` and an
`overflow_weirs` beside it — and the second was backed by nothing, because the Trust
publishes one weir service. A tier-1 row with no file behind it can only ever report
ABSENT, so a complete, correctly-downloaded card lit `HAZARD LAYERS ABSENT (1)` — the
loudest alarm on this map — on every single dive. An alarm that fires on a healthy vehicle
is an alarm that gets ignored, and the one it teaches you to ignore is the one that means
you are missing hazard data for this water. The relief-weir wording folded into the
remaining row; the names a relief weir could arrive under stay as aliases.

**One file, one row**, for the same reason. The Trust publishes near-duplicate services —
five of them are called Sluices, and they answer 886, 892, 893 and 937 features for what
is supposedly the same national layer. If two of the Pi's files bound to one row, the second would
overwrite the first's data and the first would vanish from a console that had already told
the operator it was SHOWN. A row that is already spoken for sends the newcomer to its own
adopted row, named `… (2ND FILE)`, where it is visible and says what it is.

### The licence, and where the credit is discharged

The Open Government Licence v3.0 asks for one thing: the words, wherever the data is
shown. They are discharged in **two** places, and both are deliberate:

- the **panel footer** — selectable and screen-readable, next to the layers themselves;
- the **map's attribution strip**, beside the `Imagery © Esri` line, drawn only when a CRT
  mark is actually on screen (the same rule the imagery credit follows). A credit painted
  into a canvas is legible to a human and to nothing else, which is why the panel copy
  exists as well.

> **The card is not uniformly OGL, and the console does not pretend it is.** On the real
> fetched card in `data/crt/gas-street/`, 13 of the 26 layers carry OGL v3; the rest carry
> the Trust's own data licence, an INSPIRE licence, or — for `moorings-all` and
> `towpath-access-points-2022` — **"Internal use only"**. `api/nav/crt.py` refuses to write
> "OGL v3" over something that says otherwise: each file carries the licence **as
> published**, and a licence it cannot read as permissive is recorded as *cannot-tell*, not
> as permission. The client honours that — `crtNoteCredit` appends any credit line that
> differs from the standard one to the panel footer, so a layer under different terms
> credits its own source instead of being quietly filed under somebody else's licence.
> Note that only the standard OGL line is painted on the **map** strip; the differing lines
> appear in the panel. If you redistribute anything out of that directory, read
> `provenance.json` — its `warnings` array names every layer whose terms were not quoted in
> the item metadata.

### Toggles, and before you dive in new water

**The choice is remembered on this handheld** — `STORE.set('crt.layers', …)`, IndexedDB,
keyed by layer id. A layer that reopens itself on every launch is a layer the operator
switches off on every launch and then stops trusting to stay switched off. Tier 1 is not in
that object at all, because it is not a preference.

Switching a layer **on** is the first time the console has ever asked for it, so it fetches
straight away and re-renders when the answer lands — "off" must never be allowed to look
like "absent", and a row left saying `ASKING…` forever would be a fourth state nobody
chose: the one that means this console has stopped telling you.

The layers are **per-area**, like the tiles. When the active area changes, the adopted
rows, the wire bindings and the borrowed credit lines are all thrown away — carrying them
over would leave the last canal's layer list on screen over this one's water, which is the
same class of error as leaving its hazards drawn.

**`REFRESH` asks the Pi again**, which is worth pressing after the tether has been
replugged or the Pi restarted. You should not usually need it: a `CANNOT TELL` is a
question, not a verdict, so any layer in that state is quietly re-asked every 30 s in the
background — bounded, never while a fetch is in flight, and never a poll.

#### Before you dive in new water

**Bootstrap needs the internet; the runtime never does, and has no hostnames in it.** The
chart card is per-area and fetched **once, on the Pi, while you still have a network**:

```bash
python -m nav.cli crt-fetch <area>       # needs internet — run it before you go
python -m nav.cli crt-fetch --list       # what the Trust currently publishes
```

So, for water you have not dived before:

1. download the area (**＋ AREA** → **SAVE OFFLINE**) — hazards belong to an area, and the
   fetch takes its bbox from it;
2. run `crt-fetch` for that area **while there is still a network**;
3. activate the area on the console and **open the LAYERS panel before you get in the
   van** — every tier-1 row should read `SHOWN` or `NONE MAPPED`. Anything red or amber is
   an errand you cannot do on the towpath;
4. check that no chart badge is showing on the map. `HAZARD LAYERS ABSENT` read for the
   first time at the put-in is too late.

Sluices, weirs, stop-plank grooves and outfalls are invisible from the surface. There is no
canal-side network to fix this from.

## Reduced GPU rendering (default ON)

The handheld froze repeatedly with `0x133 DPC_WATCHDOG_VIOLATION`. The crash dump names it:

```
Failure.Bucket : 0x133_ISR_amdkmdag!unknown_function
amdkmdag.sys   : 32.0.23027.3001      (AMD Radeon kernel display driver)
```

The AMD display driver overruns in its interrupt handler. **Nothing in this client can repair
that** - the fix is an AMD driver update or rollback - but it is a load-triggered fault, and
this dashboard was asking for a lot: two permanently visible full-size
`backdrop-filter: blur(16px)` surfaces (the instrument bar and the control rail) composited
over a live H.264 video every frame, plus a full-width scan line animating forever above it.

Continuous blur over video is the most expensive thing a page can ask of a compositor, and on
a dark theme it is almost invisible - raising panel opacity looks the same and costs nothing
per frame. `CONFIG.ui.reduceGpu` (default `true`) drops the blurs, the scan line and the
full-viewport gradient. Set it `false` to get the glass back on hardware that can take it.

There were **no `4101` display-timeout events at all**, which is why "GPU" went unconfirmed
for so long: it never TDR'd, it went straight to a bugcheck - so raising `TdrDelay` was never
going to help.

`launch/crash-diagnostics.ps1` now runs the dump analysis itself, so the next bugcheck is one
command rather than an afternoon.

## LOGS - reading the log without leaving the dive

A fault underwater has to be diagnosed while the vehicle is still in the water, and the
operator cannot leave the console to open a file mid-session. So **CONFIG -> LOGS** opens a
live view: centred, deliberately **not** full screen, over a dimmed-but-visible background,
because the point is to read the log while still seeing what the vehicle is doing behind it.

- **tails** by default; scrolling up pauses the tail so a line you are reading does not slide
  away, and scrolling back to the bottom resumes it
- **filter** box and **ALL / WARN+ / ERR** chips
- colour-coded by level, timestamped to the millisecond
- the footer says where the complete file is - the overlay's scrollback is the in-memory ring
  and is bounded; `navigation_logs/logs/{mode}_{iso}.log` is not

### Everything crosses the log

`js/wire.js` wraps `fetch` and `WebSocket` once, at load, so every request, response, socket
frame, close code and failure is recorded with its outcome and duration - without each call
site having to remember. Relying on callers to log is how half of it turns out to be missing
exactly where something went wrong.

A `4xx`/`5xx` is logged as a **warning**, not a success: `fetch` resolves for those, which is
how failed requests get mistaken for working ones. Aborts are distinguished from faults,
because an abort is a deadline we set on purpose.

High-frequency categories (control frames at 20 Hz) are **coalesced**, not dropped: the
suppressed count rides on the next line as `(+N more)`, and a burst that stops gets its tail
swept out within a second as `(+N more, then quiet)`. Without that, "telemetry was flowing and
then it wasn't" is exactly the case that vanishes.

The two endpoints that carry the log are deliberately not logged, or the bus feeds itself.

Console: `NEPTUNE.logs()` / `NEPTUNE.closeLogs()` / `NEPTUNE.ring()`.

## Everything a session produces

```
client/navigation_logs/
  images/   {mode}_{iso}.png   PIC stills (.jpg for the composite fallback)
  videos/   {mode}_{iso}.mp4   screen recordings
  logs/     {mode}_{iso}.log   the session log, NDJSON, written as it happens
```

One naming scheme for all three: **`{mode}_{iso}`**, where mode is what the console was
actually doing (`sim` / `real` / `stale`). A folder therefore sorts by time *and* still
says which files were real dives. ISO colons are stripped — Windows will not have them in
a filename, and that is the kind of thing that only fails once there is real data.

The launcher creates a **Neptune Recordings** desktop shortcut pointing at that folder, on
the same run that creates the Neptune one. Files this deep in an install tree are otherwise
unfindable.

### Screen recording

REC drives **two recorders**: the camera's own card, and the handheld's screen. Either can
be unavailable — no camera on the bench, no ffmpeg on a fresh machine — and neither absence
stops the other. The toast reports each separately.

The screen half is ffmpeg, run by the launcher:

```
ffmpeg -f gdigrab -framerate 30 -i desktop -an        -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -movflags +faststart out.mp4
```

Same trade as re-encoding a Mac screen recording with `-vcodec h264` afterwards — done once,
live, instead. `-an` because there is nothing to hear and audio only costs bytes. Measured
at ~**1.4 MB/min** on a mostly-static screen (more with live video in frame).

Stopping sends `q` to ffmpeg's stdin rather than killing it, so the moov atom gets written —
a hard-killed MP4 is unplayable, the same class of loss as the camera's unsegmented `.MOV`.

`h264_amf` (the AMD GPU encoder) would be lighter on CPU and is deliberately **not** the
default: this handheld has an unresolved kernel fault under sustained GPU load
(`DPC_WATCHDOG_VIOLATION` / `VIDEO_ENGINE_TIMEOUT_DETECTED`), and a recorder that can take
the machine down mid-dive is worse than one that uses more CPU.

ffmpeg is installed by `tether-setup.ps1` (winget). Without it, stills and logs are
unaffected and only recording is unavailable — the launcher says so at startup.

### The session log writes itself

There is no EXPORT LOG button any more. A log you have to remember to save is a log you find
missing exactly when you needed it — the same reasoning that made dive logging automatic. It
starts with the session, appends to disk **every 5 s as it happens**, and ends when the
console does.

Flushed on a timer rather than at shutdown on purpose: this handheld has a kernel fault that
takes the whole machine down with no unload event, so a log held in memory until exit is lost
precisely in the sessions worth reading. `NEPTUNE.sessionLog()` reports where it is going and
how much has landed.

## PIC takes two copies

The camera's own JPEG goes to the SD card — which is inside the vehicle, in the water,
on a card that has to be physically recovered. If the camera is flat, absent or
unreachable there is no copy at all. So **PIC also grabs what the operator is looking
at**, topside, into IndexedDB *and* as a file download.

The two halves are independent (§3): a dead camera does not stop the local still, and a
failed local save does not stop the camera. The toast reports each separately and never
claims a copy that was not made.

The frame source is the live video when there is one, and **the map otherwise** — in
blind nav the map *is* the view, so a still of a black video element would be worse than
nothing: it would look like the camera worked. That is also why PIC does something
useful in **sim**, where there is no camera at all, so the whole path can be exercised
on the bench.

Telemetry travels with the image (time, depth, heading, pressure, ballast, pack volts,
local x/y, origin, and whether it was taken in sim) — a still with no depth or heading is
a holiday snap; the point is being able to place it in the dive afterwards.

The id carries milliseconds because it is the IndexedDB key: at second resolution, two
presses inside the same second silently overwrote each other.

**PIC takes a REAL screenshot.** A page cannot screenshot itself: a canvas composite
only ever knows about the video and the map, never the top bar, the control rail or the
banners around them. So PIC asks the **launcher** — which already serves this page from
localhost — for a genuine screen capture, the same thing PrintScreen does. It arrives
same-origin, so it does not taint the canvas, which means the satellite basemap survives
too. `GET /__screenshot` (loopback-only listener; nothing off the machine can ask).

**The launcher writes the file too.** Chrome permits exactly ONE automatic download per
origin and then blocks the rest — it had already recorded `automatic_downloads: 2` (block)
for `http://localhost:8080`, so only the first PIC of a session ever reached the disk, with
no visible prompt in an `--app` window. PIC now sends the filename with the request
(`/__screenshot?save=<id>`) and the launcher writes the PNG to **Downloads** itself, taking
the browser out of the path entirely. The composite fallbacks still use `<a download>`,
which is why `tether-setup.ps1` also sets `AutomaticDownloadsAllowedForUrls`.

That capture is left **unmodified** — no caption. The top bar is already in frame and the
filename carries the timestamp; a strip along the bottom would cover the control rail.

Everything below is the **fallback**, used when the page is not being served by the
launcher (from the Pi, a plain static server, or a test harness) or the capture fails. It
composites what it can reach and *does* get a caption, because there the surrounding UI is
genuinely absent from the image.

**The map canvas is tainted, on purpose.** Satellite tiles are loaded without
`crossOrigin` and the offline archive stores them as *opaque* responses — requiring CORS
would break the map in the field, which matters far more than a screenshot. The cost is
that the browser refuses to export that canvas (`Tainted canvases may not be exported`),
so a capture of the map **re-renders the same frame without the imagery** and marks the
record `basemap:false`. Every vector layer — grid, centreline, track, sub — survives,
which is what carries the navigational information. The video is a `MediaStream` and
never taints, so the camera view is unaffected.

A slim caption is burnt along the bottom (time, depth, heading, `SIM`, `MAP`/`CAM`,
`NO BASEMAP`) because the downloaded file leaves the app and loses the record around it —
the same reason the camera's own timestamp is kept `ACTIVE`. On a narrow canvas it
degrades deliberately: shrink, then shorten the timestamp, then drop from the least
important end. `SIM` outranks the date — mistaking a simulated frame for a real one is a
worse error than not knowing the day, and the filename carries the date anyway.

```js
await NEPTUNE.stills()          // metadata for every still, newest first
await NEPTUNE.openStill(id)     // pop one out of IndexedDB into a tab
```

## Blackbox recorder (two-sided logging)

`js/recorder.js` is the topside half of a two-sided flight recorder. It logs the
same events the Pi logs — from *the operator's side* — so the two can be differenced
afterwards to locate faults neither log pins down alone ("it didn't respond" =
command never sent / never arrived / ack lost; "video froze" while the encoder was
healthy = tether, not camera; a bad manoeuvre made on stale data).

- **Session (§1):** adopts the Pi's `session_id`/`pi_boot_id` from `GET /api/session`
  on connect; a Pi reboot starts a fresh client file.
- **Clock sync (§2):** an SNTP-style `t1/t2/t3/t4` exchange rides the existing WS
  ping every second → `rtt`/`offset` logged as `clock_sync` (and `rtt` feeds the
  LINK readout). Client timestamps are **always** the client's own monotonic time —
  never rewritten; correction happens only in analysis.
- **Correlation IDs (§3):** every discrete command gets a UUID at operator intent
  (`commands.js`), carried through the socket and echoed in the Pi's `ack`, so the
  full 8-stage lifecycle (`cmd_intent→send→recv→validate→apply→ack_send→ack_recv→
  confirm`) ties together across both logs.
- **Client-only signals (§4):** 1 Hz WebRTC receiver stats, 10 Hz raw gamepad,
  browser/visibility/error events, environment, and compact `tlm_rx` sequence
  ranges + gaps. `max_age_ms` (age of the newest telemetry at render) drives a
  **STALE DATA** HUD badge over `CONFIG.recorder.stalenessMs`.
- **Durability (§5):** an IndexedDB ring buffer (oldest-out) written immediately;
  uploaded to `POST /api/clientlog` every 5 s / 200 events, deleted only after the
  Pi confirms; backlog flushes first on reconnect; `up_lag_ms` tags how long each
  record waited; exponential backoff + a 64 kbps cap keep it off the tether;
  `beforeunload` does a `sendBeacon` flush. **CONFIG → BLACKBOX → EXPORT LOG**
  downloads the whole ring as JSONL even with the link fully down; **MARK EVENT**
  drops a bookmark.

Console: `NEPTUNE.mark('note')`, `NEPTUNE.sessionLog()`, `NEPTUNE.REC`.

### Analysis — `rovlog`

On the Pi (`api/`): `python -m blackbox.rovlog <cmd>`
- `merge nav.jsonl client.jsonl` — one clock-aligned stream (interpolated offsets;
  flags windows where the offset estimate is unreliable).
- `diverge <session>` — the payoff: lost commands (by `c_id`), lost telemetry (Pi
  sent-ranges vs client received-ranges + worst gap), per-stage latency p50/p95/max,
  staleness distribution, video divergence, one-sided outages, clock anomalies.
- `timeline <session> --around <t> --window <s>` — side-by-side text around an incident.
- `bundle <session>` — incident zip (both logs + merge + diverge + config).

## Testing the fallbacks

- **SIM mode** — open from disk, no server → gauges animate from your inputs.
- **NO FEED / RECONFIGURING** — no WebRTC → `NO FEED`; a camera mode change → `RECONFIGURING` (auto-heals).
- **Camera controls** — REC + STILL buttons (bottom-right) are in-dive; config + files are gated behind CONFIG → **SURFACED**.
- **Keyboard ↔ gamepad** — plug/unplug a pad; the INPUT source switches live.
- **Remapping** — CONFIG → watch the raw-input monitor, hit `MAP`, press a button/key (great for the ROG Ally back paddles).

## Debug console

`window.NEPTUNE` is exposed:

```js
NEPTUNE.logRate(true)    // log every control tick + telemetry
NEPTUNE.log(false)       // silence
NEPTUNE.state            // inspect live state
NEPTUNE.openMapper()      // open CONFIG
NEPTUNE.resetBindings()   // restore default input map
NEPTUNE.connectVideo()    // (re)connect the WebRTC feed
NEPTUNE.camRecordToggle() // camera record toggle
NEPTUNE.openOrigin()      // open the origin modal
NEPTUNE.openAreas()       // open the area manager
```

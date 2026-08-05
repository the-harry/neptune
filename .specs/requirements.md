# Neptune — Requirements

Control system for a DIY **tethered canal-cleaning ROV**, piloted topside from an ASUS ROG
Ally over an Ethernet tether to a Raspberry Pi on the vehicle.

**Who this is for.** A single operator, outdoors, on a handheld, often with no internet,
frequently with one subsystem broken. The vehicle is in water and cannot be paused.

**The governing rule.** The console must never lie. A reading it cannot take shows `--`,
never a plausible zero. A simulation says it is a simulation. A subsystem that is down
says so, and says which one.

---

## 1. Topside dashboard

### R1.1 — The dashboard runs without the vehicle
**As an** operator, **I want** the dashboard to open and work with the Pi off, unplugged or
not yet built, **so that** I can plan, review dives and rehearse without a vehicle.

**Acceptance criteria**
1. WHEN the dashboard is opened AND no Pi answers, THEN it SHALL still open and run.
2. WHEN there is no vehicle link, THEN the map, radar, saved areas, dive logs, settings and
   the input remapper SHALL remain fully usable.
3. WHEN the Pi later becomes reachable, THEN the dashboard SHALL connect by itself with no
   operator action and no reload.
4. A missing Pi SHALL NOT be reported as a launch failure.

### R1.2 — One click to launch, and always a way out
**As an** operator on a handheld with no keyboard, **I want** one icon to launch and an
obvious way to exit, **so that** I am never trapped in a fullscreen app.

**Acceptance criteria**
1. WHEN the launcher runs, THEN it SHALL find the Pi, create a desktop shortcut, serve the
   dashboard locally and open it fullscreen, without prompting.
2. THE dashboard SHALL present an on-screen EXIT control at all times.
3. THE launcher SHALL NOT open a locked kiosk window by default.
4. WHEN the launcher cannot complete, THEN it SHALL report why and close by itself, and
   SHALL NOT block waiting for a keypress.
5. WHEN a previous session left a browser window behind, THEN a new launch SHALL recover
   without a reboot.
6. THERE SHALL be a documented single command that stops everything (`Neptune.bat -Stop`).

### R1.3 — The console is flyable on the bench
**As an** operator, **I want** every control to work with no vehicle attached, **so that** I
can rehearse and demonstrate.

**Acceptance criteria**
1. WHEN there is no vehicle link, THEN ballast, lights and SURFACE SHALL remain operable and
   SHALL drive a local simulation.
2. WHEN a control is driven with no link, THEN nothing SHALL be transmitted and nothing
   SHALL be queued for later.
3. WHEN simulating, THEN the HUD SHALL say so unmistakably.
4. WHEN a subsystem is genuinely unavailable with nothing to simulate (camera REC/PIC with no
   camera), THEN those controls SHALL be disabled rather than pretend.

---

## 2. Safety

### R2.1 — Vehicle commands never queue
**As an** operator, **I want** a command sent while the link is down to be discarded,
**so that** a late `throttle 100%` cannot arrive after I have stopped expecting it.

**Acceptance criteria**
1. WHEN the control link is not open, THEN commands SHALL NOT be buffered, stored or replayed.
2. WHEN the link is restored, THEN no command issued during the outage SHALL be transmitted.
3. WHEN the link is restored, THEN state that survived the outage (the ballast target) SHALL
   be re-anchored to the vehicle's actual value before any command is derived from it.

### R2.2 — Deliberate destructive actions
**As an** operator, **I want** dangerous actions to require intent, **so that** a brush of a
control cannot blow ballast or delete a dive.

**Acceptance criteria**
1. SURFACE SHALL fire only from a press-and-hold (UI) or a two-paddle 3 s hold (gamepad).
2. Deleting an area or a dive, and SD operations, SHALL require explicit confirmation.
3. WHEN the map is expanded for planning, THEN throttle SHALL be held at zero (all-stop), AND
   any real drive input SHALL immediately return control.

### R2.3 — The vehicle safes itself
**As an** operator, **I want** the vehicle to stop when it stops hearing from me, **so that**
a topside failure does not become a runaway.

**Acceptance criteria**
1. WHEN no control frame arrives within the watchdog window, THEN thrusters SHALL be zeroed.
2. WHEN the API shuts down, THEN the vehicle SHALL be safed (disarmed, thrusters off).
3. WHEN the vehicle is disarmed, THEN it SHALL neither move nor turn, and the map SHALL
   reflect that rather than continuing to advance.

### R2.4 — Every session is recorded, unasked
**As an** operator, **I want** the navigation log to exist without me remembering to start
it, **so that** the dive I needed to review is never the one I forgot to record.

**Acceptance criteria**
1. WHEN an origin exists, THEN a navigation log SHALL start automatically with no operator
   action.
2. Samples SHALL reach disk as they happen, NOT only when a dive is stopped.
3. WHEN the process dies mid-dive, THEN the recorded track SHALL survive and SHALL be
   recoverable on the next start, marked as recovered.
4. WHEN a log cannot be written (full disk, unwritable path), THEN navigation and piloting
   SHALL continue unaffected.
5. THE operator SHALL be able to disable automatic logging for bench work.

---

## 3. Degradation

### R3.1 — Subsystems fail independently
**As an** operator, **I want** one broken thing to break only itself, **so that** I can keep
working with whatever still functions.

**Acceptance criteria**
1. THE system SHALL track internet, ROV link, video, camera control, navigation and vehicle
   state separately.
2. WHEN a subsystem is down, THEN only the controls belonging to that subsystem SHALL be
   affected.
3. WHEN the ROV link drops, THEN the camera buttons, map, radar, saved areas, dive logs and
   settings SHALL remain available.
4. WHEN the camera is unreachable, THEN piloting, video and the map SHALL be unaffected.
5. Each subsystem's state SHALL be visible at a glance, and the indicator SHALL distinguish
   "no vehicle" from "vehicle present" unmistakably.
6. Reconnection SHALL be automatic and silent; there SHALL be no retry buttons.

### R3.2 — Stale is not the same as gone
**As an** operator, **I want** a brief gap and a dead link to look different, **so that** I
can tell a hiccup from a failure.

**Acceptance criteria**
1. WHEN telemetry is momentarily late but the socket is open, THEN readings SHALL be marked
   stale rather than replaced.
2. WHEN the link is actually gone, THEN the console SHALL hand back to the simulator and
   remain flyable, resuming from the last real values.
3. THE console SHALL NOT freeze on the last received frame indefinitely.

---

## 4. Connectivity

### R4.1 — The tether is deterministic
**As an** operator, **I want** the Pi to be findable every time, **so that** "cable plugged
in, no connection" cannot happen.

**Acceptance criteria**
1. THE tether SHALL NOT depend on DHCP; a direct cable has no DHCP server.
2. THE Pi SHALL hold a fixed address on the tether interface, in addition to any DHCP lease
   so it still works plugged into a router.
3. THE topside machine SHALL hold the matching fixed address.
4. WHEN the launcher starts, THEN it SHALL probe candidate addresses and use whichever
   actually answers, not the first that merely pings.
5. WHEN no Pi answers AND the topside tether address is missing, THEN the launcher SHALL say
   so and name the setup script.
6. Topside SHALL NOT power-suspend the tether network adapter.

### R4.2 — The Pi is backend-only
**As an** operator, **I want** the Pi to serve only the backend, **so that** it stays light
and the dashboard is not coupled to it.

**Acceptance criteria**
1. THE Pi SHALL NOT serve the dashboard; the client runs topside.
2. THE tether SHALL be plain HTTP with no certificate to manage.
3. THE camera network SHALL never own the default route.

---

## 5. Video

### R5.1 — Live video when possible, honest failure when not
**As an** operator, **I want** the camera feed with minimal latency, **so that** I can see
what the sub sees.

**Acceptance criteria**
1. Video SHALL be zero-transcode (H.264 passthrough) so a Pi 3 keeps up.
2. WHEN video is unavailable, THEN the reason SHALL be shown rather than a frozen frame.
3. Video SHALL reconnect by itself after a camera reconfiguration or a transient drop.
4. Repeated reconnection SHALL NOT leak peer connections, sockets, timers or decoders.
5. WHEN the camera is asleep or absent, THEN it SHALL rejoin automatically when powered on.

### R5.2 — Blind navigation
**As an** operator, **I want** the map to take over when the camera dies, **so that** I can
still drive rather than stare at a black rectangle.

**Acceptance criteria**
1. WHEN the feed has been down beyond a debounce, THEN the map SHALL become the primary
   driving view.
2. Blind navigation SHALL NOT engage an all-stop; the vehicle SHALL remain drivable.
3. THE view SHALL be heading-up and follow the sub.
4. THE display SHALL state plainly that there is no camera.
5. WHEN the feed returns beyond a debounce, THEN the camera view SHALL be restored.
6. THERE SHALL be no full-screen "no feed" state. Blind navigation is the fallback in
   every mode, including a cold start where a feed has never existed.
7. THE operator SHALL NOT be able to leave blind navigation into a camera view that has
   nothing to show; the feed returning is what restores it.
8. A stray tap in this view SHALL NOT zero the throttle.

---

## 6. Navigation and the map

### R6.1 — The map follows the vehicle
**As an** operator, **I want** the map to show what my vehicle is doing, **so that** I can
navigate by it.

**Acceptance criteria**
1. THE track SHALL be derived from the live vehicle's heading, depth and thruster output.
2. Steering the vehicle SHALL change the plotted heading and track.
3. A scripted demo path SHALL be available but SHALL NOT be the default.
4. WHEN the vehicle hardware is simulated, THEN the dashboard SHALL continue to flag it.

### R6.2 — The radar is a glance instrument
**As an** operator, **I want** the minimap to mean the same thing every time, **so that** I
can read it at a glance.

**Acceptance criteria**
1. THE radar SHALL keep a fixed zoom that the full-screen views cannot change.
2. THE radar's zoom SHALL be tight enough that vehicle movement is visible within seconds.
3. THE full-screen views SHALL be independently zoomable.
4. THE direction indicator and the plotted track SHALL share one coordinate frame and SHALL
   NOT be offset from one another.

### R6.3 — The origin is honest
**As an** operator, **I want** the launch origin to reflect where I actually am, **so that**
the map does not plot my sub relative to somewhere else.

**Acceptance criteria**
1. THE origin SHALL be client-owned and SHALL work with the Pi off.
2. WHEN no origin is stored, THEN the dashboard SHALL request one.
3. WHEN an origin is stored, THEN opening the dashboard SHALL NOT produce a permission
   prompt.
4. WHEN a fresh fix is available and materially distant from the stored origin, THEN the
   operator SHALL be offered the choice, and SHALL be able to decline.
5. A refreshed fix SHALL NOT replace a more accurate origin with a less accurate one.
6. WHEN the stored origin is old enough to have come from another site, THEN this SHALL be
   visible without needing a fix or internet.
7. THE origin SHALL be settable by tapping the map, which works with no internet.

---

## 7. Vehicle and Pi health

### R7.1 — Real readings or none
**As an** operator, **I want** the Pi's health to be real, **so that** I can trust it.

**Acceptance criteria**
1. CPU temperature, load, memory, disk, uptime and per-interface network state SHALL be read
   from the actual system.
2. WHEN a probe cannot be read, THEN it SHALL report "unavailable", and SHALL NOT report zero.
3. Health SHALL be available independently of the vehicle link and the camera.
4. Health collection SHALL NOT block the control loop.
5. A failing probe SHALL NOT fail the whole reading.
6. Health SHALL require no additional runtime dependency.

### R7.2 — Simulated hardware admits it
**As an** operator, **I want** to know when sensor values are not real, **so that** I never
fly on fabricated instruments.

**Acceptance criteria**
1. WHEN the vehicle hardware backend is not wired, THEN it SHALL fail rather than return
   constants presented as readings.
2. WHEN the system falls back to simulated hardware, THEN telemetry SHALL carry that fact and
   the dashboard SHALL show it.
3. Logs SHALL distinguish which sensor source is in use from whether the vehicle is simulated.

---

## 8. Flight recorder

### R8.1 — Two-sided black box
**As an** operator, **I want** both ends logged, **so that** a link failure can be analysed
from both sides.

**Acceptance criteria**
1. Topside and Pi SHALL each record the same event classes with their own monotonic clocks.
2. Client records SHALL be written locally first and uploaded separately.
3. Upload SHALL never block recording, SHALL be bandwidth-capped, and SHALL back off.
4. THE local buffer SHALL be bounded across restarts, not only within a session.
5. Recording SHALL never throw into the piloting path.

---

## 9. Installation and reproducibility

### R9.1 — Repeatable from the scripts
**As a** maintainer, **I want** everything reproducible from committed scripts, **so that**
no fix exists only on a machine.

**Acceptance criteria**
1. Re-running the Pi installer SHALL be idempotent.
2. THE installer SHALL work with no internet, applying configuration and skipping only what
   genuinely needs a network.
3. THE installer SHALL verify the tether address actually applied, and SHALL say what to
   check if not.
4. THE installer SHALL refuse to start a video plane that cannot work.
5. Topside one-time machine setup SHALL be a committed script.
6. Line endings SHALL be enforced per platform: a stray CR breaks both shell and PowerShell.

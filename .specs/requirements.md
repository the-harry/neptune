# Neptune — Requirements

Control system for a DIY **tethered canal-cleaning ROV**, piloted topside from an ASUS ROG
Ally over an Ethernet tether to a Raspberry Pi on the vehicle.

**Who this is for.** A single operator, outdoors, on a handheld, often with no internet,
frequently with one subsystem broken. The vehicle is in water and cannot be paused.

**The governing rule.** The console must never lie. A reading it cannot take says
cannot-tell — never a plausible zero, and never the last number it managed to take. A
simulation says it is a simulation. A subsystem that is down says so, and says which one.

"Cannot take" includes *the sensor was here and has stopped*, and a cannot-tell that is
itself a valid reading (`0.0` heading is due north; `0.0` depth is the surface) is not a
cannot-tell. That is **R7.6**, and it is the rule this system exists to keep.

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
4. WHEN a subsystem is genuinely unavailable with nothing to simulate (camera REC with no
   camera), THEN those controls SHALL be disabled rather than pretend.
5. A control that still does something useful without its subsystem SHALL NOT be disabled with
   it — PIC keeps a topside still with no camera attached (see R5.4).

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
1a. THE indicator for each SHALL report only what it can prove. A connection attempt that
   has not yet failed SHALL NOT be shown as progress, and an observation older than its
   freshness window SHALL be dropped rather than believed.
1b. WHERE a state cannot be determined from the browser alone — which network adapters
   exist, whether a network reaches the internet, which access points are in range — THE
   system SHALL obtain it from the topside launcher, and WHERE no launcher is present it
   SHALL fall back to what it can prove and say so.
2. WHEN a subsystem is down, THEN only the controls belonging to that subsystem SHALL be
   affected.
3. WHEN the ROV link drops, THEN the camera buttons, map, radar, saved areas, dive logs and
   settings SHALL remain available.
4. WHEN the camera is unreachable, THEN piloting, video and the map SHALL be unaffected.
5. Each subsystem's state SHALL be visible at a glance, and the indicator SHALL distinguish
   "no vehicle" from "vehicle present" unmistakably.
5a. State SHALL be carried by SHAPE as well as colour, and WHERE two states share a colour
   they SHALL differ by motion (blinking) — so nothing depends on colour discrimination in
   sunlight.
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

### R5.4 — A still is kept topside, not only on the vehicle
**As an** operator, **I want** PIC to keep a copy of the shot on the handheld, **so that** a
camera I cannot recover, or never had, does not mean I have no picture.

**Acceptance criteria**
1. WHEN PIC is pressed, THEN a still SHALL be saved topside regardless of the camera.
2. THE topside still and the camera's own SD copy SHALL be independent: either failing
   SHALL NOT prevent the other, AND the result of each SHALL be reported separately.
3. THE console SHALL NOT report a copy it did not make.
4. THE topside still SHALL be a capture of the WHOLE SCREEN as the operator sees it —
   including the instrument bar, the controls and the map basemap — not of one layer.
5. THE screen capture SHALL NOT be annotated or altered.
6. WHEN a whole-screen capture is unavailable, THEN a still SHALL still be produced from
   whatever the console can reach, AND it SHALL say what it is.
7. WHEN there is no live feed, THEN the fallback still SHALL be of the view actually in use
   (the map), NOT a blank video frame.
8. WHEN there is no camera at all (bench or simulation), THEN PIC SHALL still produce a real
   image, so the path can be exercised without hardware.
9. Each still SHALL carry the telemetry needed to place it in the dive afterwards.
10. Two stills taken in quick succession SHALL BOTH be kept.
11. PIC SHALL NOT be disabled because the camera is unavailable.
12. A capture that is slow or unavailable SHALL NOT block PIC.
13. EVERY press SHALL write a file, not just the first of a session.

### R5.5 - The dive is recorded on both ends
**As an** operator, **I want** the handheld screen recorded alongside the camera, **so that**
a dive has a topside account of itself - instruments included - even if the camera card never
comes back.

**Acceptance criteria**
1. ONE control SHALL start and stop both the camera recording and the screen recording.
2. Either recorder being unavailable SHALL NOT prevent the other, AND each outcome SHALL be
   reported separately.
3. THE screen recording SHALL cover the whole screen, carry no audio, and be H.264 in a
   container that plays without conversion.
4. A recording SHALL be playable even though the operator ended it, rather than the encoder
   reaching an end of input.
5. THE recording SHALL NOT be left running when the console exits.
6. WHERE screen recording is unavailable, THEN stills and logs SHALL be unaffected and the
   console SHALL say which is missing.
7. Screen recording SHALL NOT be given a job that risks the known GPU fault on this handheld.

### R5.6 - Session artefacts are findable
**As an** operator, **I want** everything a session produced in one predictable place with
predictable names, **so that** I can find a dive afterwards without hunting.

**Acceptance criteria**
1. Stills, recordings and logs SHALL be written under one folder, separated by kind.
2. Every artefact SHALL be named `{mode}_{timestamp}`, where mode is what the console was
   actually doing, so files sort by time and declare whether they were a real dive.
3. Names SHALL be legal filenames on the operator's platform.
4. THE launcher SHALL provide a desktop shortcut to that folder if one does not exist.
5. Artefacts SHALL NOT be scattered into the browser's download folder when the launcher is
   available to write them.

### R5.7 - The session log needs no operator action
**As an** operator, **I want** the log to start and finish by itself, **so that** the session
I needed to review is never the one I forgot to export.

**Acceptance criteria**
1. THE session log SHALL start with the session, with no operator action.
2. THERE SHALL be no manual export control.
3. Events SHALL reach disk AS THEY HAPPEN, not only at shutdown.
4. WHEN the machine dies without an orderly shutdown, THEN everything logged up to that point
   SHALL already be on disk.
5. WHEN the log cannot be written, THEN piloting SHALL continue unaffected, AND the buffered
   backlog SHALL be bounded rather than growing without limit.

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

### R5.3 — The camera is configured for the dive, not left at factory
**As an** operator, **I want** the camera brought to the right settings by itself and kept
there, **so that** it does not power itself off mid-dive or lose the recording, and so that
I never have to remember a menu sequence on a handheld at the waterside.

**Acceptance criteria**
1. WHEN the camera is reachable, THEN the settings needed for a dive SHALL be applied with
   no operator action.
2. THE camera SHALL NOT be allowed to power itself down on an idle timer.
3. Recorded video SHALL be segmented, NOT written as one continuous file — a file still
   being written when power is cut is unrecoverable.
4. WHEN a setting is written, THEN it SHALL be verified by re-reading it, AND a write the
   firmware accepted but ignored SHALL be reported as not applied.
5. WHEN a setting cannot be applied, THEN the console SHALL say so; it SHALL NOT report
   success for a setting it did not verify.
6. WHEN the camera reboots, is power-cycled, or otherwise returns after being unreachable,
   THEN the settings and the camera clock SHALL be re-applied automatically.
7. Applying settings SHALL NOT interrupt the video feed while the vehicle may be under way,
   AND SHALL NOT disturb a recording in progress.
8. WHERE a setting's meaning or valid values are unverified, THEN it SHALL be left alone and
   listed as deliberately unset, rather than guessed at.
9. WHEN enforcement is disabled, THEN the camera state SHALL still be audited and reported.
10. Neither the camera nor the link to it SHALL be allowed to enter a power-saving state.

### R5.8 - The log is readable during the dive
**As an** operator with a vehicle in the water, **I want** to read the live log without
leaving the console, **so that** I can diagnose a fault while it is still happening.

**Acceptance criteria**
1. THE log SHALL be viewable from within the console, without navigating away or opening a
   file.
2. THE view SHALL NOT occupy the whole screen; what the vehicle is doing SHALL remain visible
   behind it.
3. THE view SHALL follow new lines by default, SHALL stop following when the operator scrolls
   back, and SHALL resume when they return to the end.
4. THE operator SHALL be able to narrow the view by text and by severity.
5. THE view SHALL state where the complete record is, AND SHALL NOT imply it holds more than
   it does.
6. THE view SHALL NOT capture piloting input.
7. Opening or holding the view open SHALL NOT degrade piloting.

### R5.9 - Everything that crosses a boundary is logged
**As an** operator debugging after the fact, **I want** every send, receive, success and
failure recorded, **so that** a question about what happened is answerable without having
prepared for it.

**Acceptance criteria**
1. EVERY outbound request and socket frame SHALL be logged.
2. EVERY response SHALL be logged with its outcome, INCLUDING failures that do not raise.
3. Failures, refusals and warnings SHALL be distinguishable from successes at a glance.
4. Logging SHALL NOT require the calling code to opt in.
5. High-frequency events SHALL be bounded in the log, AND any suppression SHALL be stated
   rather than silent.
6. Writing the log SHALL NOT itself generate log entries.
7. A logging fault SHALL NOT affect piloting.

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

### R6.4 — A measured speed and an estimated one never look alike
**As an** operator, **I want** to know whether the speed on screen was measured or inferred
from the throttle, **so that** I do not plan a manoeuvre on a number the vehicle guessed.

**Acceptance criteria**
1. THE system SHALL carry, with every speed it reports, which source produced it.
2. WHEN the water-speed sensor is fresh, THEN the speed SHALL be taken from it and SHALL be
   presented as a measurement.
3. WHEN the sensor is stale, stalled or not fitted, THEN it SHALL report unknown, SHALL NOT
   report 0.0 m/s, AND any speed shown SHALL be presented visibly as an estimate.
4. THE sensor SHALL NOT be asked for direction; the sign SHALL come from the commanded
   thrust.
5. WHEN thrust is sustained with no measured speed, THEN the system SHALL raise a distinct
   SNAGGED warning, SHALL degrade navigation confidence, AND SHALL do so whichever estimator
   is selected.
6. A modelled speed SHALL NOT satisfy the snag check, because a model reports the speed the
   throttle implies.

### R6.5 — Heading survives the thrusters
**As an** operator, **I want** the heading to stay usable while the motors are running,
**so that** the track does not bend every time I accelerate.

**Acceptance criteria**
1. WHEN the magnetometer calibration is below the trusted level, OR thrust is high enough to
   pollute it, THEN the magnetic heading SHALL NOT be applied.
2. WHEN the magnetic heading is not applied, THEN the system SHALL say so distinctly, AND
   that state SHALL read as deliberate rather than as a fault.
3. THE filtered heading SHALL NEVER step; corrections SHALL be rate-limited, including after
   a period of distrust.
4. Heading differences SHALL be wrapped, so the 359°→1° crossing SHALL NOT produce a
   correction the long way round.
5. WHEN samples are interrupted beyond the gap threshold, THEN the system SHALL NOT integrate
   turn rate across the gap.
6. WHEN calibration is below the trusted level, THEN heading SHALL be flagged suspect
   everywhere heading is shown.

### R6.6 — The estimator is promoted with data, not taste
**As a** maintainer, **I want** the choice of estimator decided by replaying real dives,
**so that** a filter is adopted because it was better and not because it was newer.

**Acceptance criteria**
1. THE estimator SHALL be selectable by configuration alone, with no code change, AND the
   dead reckoner SHALL remain the default.
2. Both estimators SHALL present the same interface and SHALL share the position
   integration, tether bound, snapping and confidence logic.
3. THERE SHALL be a command that replays a recorded dive through either or both estimators
   and reports track divergence, final-position delta, time spent without a trusted compass,
   time on each speed source, and snag events.
4. WHEN a dive contains a magnetic disturbance, THEN the filtered estimator SHALL beat the
   dead reckoner against known ground truth; AND on a clean dive it SHALL NOT be worse beyond
   a stated tolerance. These SHALL be the acceptance gate for adopting it.
5. THE system SHALL NOT estimate a current vector, refit position from surface fixes, or run
   a position-domain filter, WHILE there is insufficient observability and no real dive data
   to validate against.
6. Acceleration SHALL NEVER be integrated twice into position.

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

### R7.3 — Water in the hull is reported in two stages
**As an** operator, **I want** "water is collecting" and "the hull is flooding" to be
different signals, **so that** I neither surface for condensation nor keep working through a
flood.

**Acceptance criteria**
1. THE system SHALL report a leak in three states: normal, warning and flood.
2. WHEN the lower probe is wet, THEN the console SHALL give a non-blocking advisory carried by
   a SHAPE change as well as a colour.
3. WHEN the upper probe is wet, THEN the console SHALL raise the flood alarm AND prompt to
   surface, regardless of the lower probe.
4. THE flood presentation SHALL remain unmistakably different from a link dropout.
5. A probe SHALL read wet for a debounce period before its stage latches, so a splash or
   condensation SHALL NOT raise an alarm.
6. THE two probes SHALL be independent; neither SHALL mask the other's state.
7. AT arm time THE system SHALL report a probe whose reading is physically impossible, since
   a dead probe otherwise reads dry forever.
8. THE existing single leak flag SHALL remain true for EITHER stage, so a consumer that knows
   only the flag SHALL NOT go silent.

### R7.4 — The battery is read against the pack that is fitted
**As an** operator, **I want** the voltage bands to describe this vehicle's pack, **so that**
the gauge is capable of ever reading low.

**Acceptance criteria**
1. THE thresholds SHALL be those of the 2S pack actually fitted, AND the obsolete 24 V scale
   SHALL NOT remain anywhere in the system, including mocks, tests and client expectations.
2. THE console SHALL show the voltage as a number at all times.
3. Colour SHALL come ONLY from the configured bands, AND that colour SHALL NOT be borrowed by
   anything else.
4. WHEN the pack falls below the critical threshold, THEN the console SHALL prompt to surface.
5. THE documented hard floor SHALL be stated to the operator; software SHALL NOT enforce it,
   because safing a sub mid-canal trades a damaged pack for an unrecoverable vehicle.
6. THE thresholds SHALL be configurable without a code change.

### R7.5 — Ballast position is unknown until it is homed
**As an** operator, **I want** the syringe to admit it does not know where it is, **so that**
I do not dive on a number derived from a step counter that was never zeroed.

**Acceptance criteria**
1. BEFORE the first homing, THE system SHALL report the ballast level as unknown, AND SHALL
   NOT report 0, 50 % or any other plausible value.
2. THE console SHALL present that unknown explicitly AND SHALL offer the action that resolves
   it.
3. WHEN homing completes against the empty end stop, THEN the counter SHALL be zeroed and the
   level SHALL become known.
4. Reaching either end stop SHALL stop motion in that direction immediately, including
   mid-command.
5. AN end-stop wire that breaks SHALL read as triggered, so a failed switch SHALL stop motion
   rather than be silently absent.
6. WHEN the full end stop is reached at a count disagreeing with the configured span beyond
   the configured tolerance, THEN the system SHALL log it, flag that re-homing is needed, and
   surface that to the operator rather than continuing to publish a level it knows is wrong.
7. THE operator-facing behaviour of the syringe control SHALL be unchanged: 0..1 of the
   calibrated stroke, drag up to fill.

### R7.6 — A sensor that has stopped answering says so, and nothing downstream fills it in
**As an** operator, **I want** a reading whose sensor has died to go blank rather than
freeze at its last value, **so that** I never fly the sub on a number nobody is taking.

This is the governing rule stated in full. It took four review rounds, and the design
rationale — including why three of them missed it — is `.specs/design.md` §24.

**Acceptance criteria**

1. WHEN a sensor is not answering, THEN every reading derived from it SHALL be reported as
   cannot-tell.
2. "Not answering" SHALL cover BOTH a sensor that was never wired AND a sensor that
   answered earlier in this dive and has since stopped. A liveness check that a
   stopped-mid-dive sensor passes SHALL NOT be considered a liveness check.
3. A cannot-tell SHALL NOT be represented by a value that is itself a valid reading. In
   particular, `0.0` heading (due north), `0.0` depth (the surface), atmospheric surface
   pressure, `mag_cal` 0 ("a compass answered and reports itself uncalibrated"), leak
   `NORMAL` (a positive claim that the hull is dry) and `snagged` false (a positive claim
   that the sub is moving freely) SHALL NOT be used to mean "unknown".
4. THE last value read before a sensor stopped SHALL NOT be published as a current
   reading, because a frozen reading and a steady one are indistinguishable on screen.
5. A subsystem that stops answering SHALL NOT cause the console to become quieter: a
   standing warning SHALL NOT clear itself because the subsystem that raised it has died,
   AND a state that means "nothing looked" SHALL be distinct from one that means "it
   looked and says no".
6. NO stage between the sensor and the screen SHALL substitute a default for a
   cannot-tell. THIS applies to every layer on that path — hardware abstraction, wire
   protocol, control state, navigation sensor sampling, telemetry stitching and the
   client — AND the property SHALL be treated as lost if any single one of them coerces.
7. ANY change that touches this property SHALL name an owner for every file on that path,
   AND a file on the path with no owner SHALL be treated as a defect in the change rather
   than as an omission from the review.
8. THE vehicle SHALL report WHICH sensor has stopped, using the same designations as the
   wiring documentation, AND that report SHALL be derived from the same verdict the
   cannot-tell values are, so the two SHALL NOT be able to contradict each other.
9. AN empty fault list SHALL NOT be read as a certificate of health; the cannot-tell on
   each individual reading SHALL remain the authoritative claim.
10. THE console SHALL distinguish a dead sensor from a momentarily-quiet link by SHAPE and
    not by colour alone, AND SHALL NOT present the two with the same mark.
11. THE explanation of a blank reading SHALL name the part to go and check, because a
    blank with no cause attached reads as a display glitch and a display glitch is
    something an operator waits out.
12. WHEN there is no heading, THEN the system SHALL NOT advance the plotted track, SHALL
    NOT rotate the map onto a substituted bearing, AND SHALL state that there is no
    bearing. A heading that is absent SHALL be presented distinctly from one that is
    uncalibrated, one that is being ignored deliberately, and one that was never fitted.
13. A cannot-tell SHALL NOT be written into the dive log as though it were a measurement.
14. THE liveness rule SHALL be exercisable on a bench with no hardware and without waiting
    on a real sensor to die, AND recovery SHALL be exercisable too: a reading that goes
    blank SHALL return when its sensor answers again.
15. THERE SHALL be a check that crosses the whole path in one go — a sensor stopped at the
    hardware end, and what reaches the client asserted at the other — since every layer
    passing its own tests is what allowed this to ship three times.

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

"""RovState — the authoritative, server-side model of the vehicle.

The client sends *inputs*; this owns the *truth*. It applies control/commands to
the hardware, runs the safety watchdog (zero thrusters if control frames stop),
advances the mock sim, and produces telemetry snapshots.

Single-threaded: all methods run on the asyncio event loop, so no locking.
"""

from __future__ import annotations

import logging
import time

from config import settings
from hardware import HardwareBase
from protocol import (
    COMMAND_NAMES,
    BallastMsg,
    CameraMsg,
    CommandMsg,
    ControlMsg,
    Telemetry,
)

log = logging.getLogger("neptune.rov")

_CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def cardinal(deg: float | None) -> str | None:
    """Compass point for a bearing — and NOTHING for a bearing that does not exist.

    None in, None out. A cardinal is a restatement of the heading, not a second
    opinion about it, so it cannot outlive the number it restates: `cardinal(None)`
    returning "N" would put a confident letter beside a blank bearing, and a letter
    is exactly what an operator reads when the number is missing. It used to be
    unable to receive None at all — `None % 360` is a TypeError — which is the same
    hole seen from the other side: the heading could not be absent, so the vehicle
    had to invent one.
    """
    if deg is None:
        return None
    return _CARDINALS[round((deg % 360) / 45) % 8]


class RovState:
    # Leak stages ordered by severity. Ranking them (rather than comparing strings)
    # is what lets the alarm fire on a RISE only: water receding from FLOOD to WARN
    # is not a new emergency and must not announce itself as one.
    _LEAK_RANK = {"NORMAL": 0, "WARN": 1, "FLOOD": 2}

    def __init__(self, hw: HardwareBase) -> None:
        self.hw = hw
        self.armed = False
        self.throttle = 0.0
        self.steer = 0.0
        self.left = 0.0
        self.right = 0.0
        self.pan = 0.0
        self.tilt = 0.0
        self.ballast_dir = "hold"
        # The stepper is open-loop: until it has homed, get_ballast_level() is None.
        # A *target* is a command, though, not a measurement, so it cannot be None —
        # something has to be commanded from power-on. 0.0 (empty) is the only safe
        # answer: an empty syringe is positively buoyant, so if anything ever drives
        # toward this target before a human touches the control, it drives the sub up.
        level = hw.get_ballast_level()
        self.ballast_target = 0.0 if level is None else level
        self.last_control_ts = 0.0
        self._failsafe = False
        self._leak_rank = 0
        # The leak alarm that is currently STANDING, or None while the hull is dry.
        # Separate from _leak_rank because the two answer different questions: the
        # rank decides whether this tick is an edge, the latch remembers that an
        # edge happened at all. See latched_alarms().
        self._alarm_latch: str | None = None
        # HOW MANY TIMES THE LEAK DETECTOR HAS BEEN RE-ARMED BY HAND this run. On
        # the wire because "NORMAL" after a re-arm and "NORMAL" that was never in
        # doubt are not the same claim, and the console should be able to tell an
        # operator which one it is showing them.
        self.leak_rearms = 0
        hw.set_armed(False)

    # ---- inbound application --------------------------------------------
    def apply_control(self, m: ControlMsg) -> None:
        self.throttle = _clamp(m.throttle)
        self.steer = _clamp(m.steer)
        self.last_control_ts = time.monotonic()
        if self._failsafe:
            self._failsafe = False
            log.info("control resumed — watchdog cleared")
        self._drive_thrusters()

    def apply_camera(self, m: CameraMsg) -> None:
        self.pan = _clamp(m.pan)
        self.tilt = _clamp(m.tilt)
        self.hw.set_camera(self.pan, self.tilt)

    def apply_ballast(self, m: BallastMsg) -> None:
        self.ballast_dir = m.cmd
        if m.cmd == "fill":
            self.ballast_target = 1.0
        elif m.cmd == "empty":
            self.ballast_target = 0.0
        else:  # hold
            # "Hold" means "the target is wherever you are now". If the stepper has
            # never homed there is no "now" to adopt, so the last commanded target
            # stands instead of being clobbered — writing 0.0 here would silently
            # turn a stop request into a command to empty the ballast, and writing
            # anything else would invent a position the vehicle cannot see.
            level = self.hw.get_ballast_level()
            if level is not None:
                self.ballast_target = level
        self.hw.ballast_pump(m.cmd)

    def apply_command(self, m: CommandMsg) -> dict | None:
        """Apply one command. Returns a verdict dict for commands that can be
        REFUSED for a reason, None for the ones that simply happen.

        Most commands here cannot fail meaningfully — arming is a software gate,
        a light either has a pin or does not. leak_reset is the first that can be
        legitimately declined by the vehicle, so it needs a way to say why, and
        the caller turns that into the ack.
        """
        name = m.name
        if name not in COMMAND_NAMES:
            log.warning("unknown command %r ignored", name)
            return None
        val = m.value
        if name == "arm":
            self._set_armed(True)
        elif name == "disarm":
            self._set_armed(False)
        elif name == "stop":  # E-STOP
            self._set_armed(False)
            self.ballast_dir = "hold"
            self.hw.ballast_pump("hold")
            log.warning("E-STOP")
        elif name == "surface":
            self.ballast_dir = "empty"
            self.ballast_target = 0.0
            self.hw.ballast_pump("empty")
        elif name == "ballast_home":
            self.hw.ballast_home()
            self.ballast_dir = "hold"
        elif name == "magnet":
            self.hw.set_magnet(bool(val))
        elif name in ("light_green", "light_white"):
            self.hw.set_light("green" if name.endswith("green") else "white", bool(val))
        elif name in ("light_green_level", "light_white_level"):
            which = "green" if "green" in name else "white"
            self.hw.set_light_level(which, float(val or 0.0))
        elif name == "dropweight":
            if val == "release":
                self.hw.release_dropweight()
            else:
                log.warning("dropweight command without 'release' value ignored")
        elif name == "leak_reset":
            # THE HARDWARE DECIDES, NOT THIS LINE. The refusal-while-wet rule lives
            # in the backend beside the pins it reads, so it cannot be bypassed by
            # anything that reaches the hardware another way. This returns the
            # verdict so the ack carries the REASON — a button that silently does
            # nothing when refused teaches an operator that the button is broken,
            # and the next thing they do is stop believing the console.
            res = self.hw.reset_leak_latches()
            if res.get("ok"):
                self.leak_rearms += 1
            else:
                log.warning("leak re-arm refused: %s", res.get("why"))
            return res

    # ---- driving / watchdog ---------------------------------------------
    def _set_armed(self, on: bool) -> None:
        self.armed = on
        self.hw.set_armed(on)
        if not on:
            self.throttle = self.steer = 0.0
            self.left = self.right = 0.0
            self.hw.set_thrusters(0.0, 0.0)

    def _drive_thrusters(self) -> None:
        if self.armed and not self._failsafe:
            self.left = _clamp(self.throttle + self.steer)
            self.right = _clamp(self.throttle - self.steer)
        else:
            self.left = self.right = 0.0
        self.hw.set_thrusters(self.left, self.right)

    def watchdog(self, now: float) -> None:
        """Fail-safe: if control frames stopped, zero the thrusters."""
        if not self.armed:
            return
        stale = (now - self.last_control_ts) > settings.watchdog_timeout_s
        if stale and not self._failsafe:
            self._failsafe = True
            self.left = self.right = 0.0
            self.hw.set_thrusters(0.0, 0.0)
            log.warning("watchdog: no control for >%.2fs — thrusters zeroed", settings.watchdog_timeout_s)

    def update(self, dt: float) -> None:
        self.hw.update(dt)

    # ---- telemetry -------------------------------------------------------
    def leak_alarm_edges(self, leak_state: str) -> list[str]:
        """Alarm names to raise for this leak stage — RISING edges only.

        WARN and FLOOD are different events and the client draws them differently:
        WARN is a non-blocking advisory ("water is collecting — finish up"), FLOOD is
        a surface prompt on a pulsing hull. One shared alarm name cannot carry that,
        so there are two, and the caller broadcasts whichever this returns.

        Only a rise fires. Level-triggering would put telemetry_hz alarm frames a
        second on the socket the operator is trying to read, and re-announcing WARN
        as the water drains back out of the flood probe would train them to ignore
        it. Falling all the way back to NORMAL re-arms, so a probe that dries and
        wets again does fire again — that one is a genuinely new edge.

        Returns a list (never more than one name today) so a caller can just iterate;
        adding a stage later must not change the call site's shape.
        """
        # A STAGE THIS MACHINE DOES NOT KNOW IS NOT EVIDENCE OF A DRY HULL. This
        # used to be `.get(leak_state, 0)`, and 0 is NORMAL's rank — so any stage
        # added later fell straight through to "dry, re-arm the alarm, drop the
        # latch". "UNKNOWN" (the probes are not being sampled) is exactly such a
        # stage, and under the old default a standing WARN would have been retracted
        # by a sampling hiccup: a client attaching during it would be told nothing
        # about water that had already been found, because a cannot-tell had quietly
        # been read as an all-clear.
        #
        # Cannot-tell is not evidence in EITHER direction, so it moves nothing: no
        # rise, no re-arm, no latch cleared. The last stage anyone actually measured
        # stands until something measures another one. It does not raise an alarm of
        # its own either — the sampler stopping is a fault, not a flood, and it
        # reaches the operator as the UNKNOWN stage and its named fault rather than
        # as a leak siren that turns out to be a loose connector.
        rank = self._LEAK_RANK.get(leak_state)
        if rank is None:
            return []
        prev, self._leak_rank = self._leak_rank, rank
        if rank == 0:
            # Dry again. The standing alarm is over, so a client attaching now must
            # not be greeted by an alarm about water that has already gone. Cleared
            # at the same point the edge re-arms on purpose: one condition, one
            # memory, and they can never disagree about whether the hull is wet.
            self._alarm_latch = None
        if rank <= prev:
            return []
        # A jump straight to FLOOD announces FLOOD only — the worse stage supersedes,
        # and stacking two alarms on one event is how an operator misses the real one.
        name = "leak_flood" if rank >= self._LEAK_RANK["FLOOD"] else "leak_warn"
        self._alarm_latch = name
        return [name]

    def latched_alarms(self) -> list[str]:
        """The leak alarm still STANDING — what a client that just attached missed.

        The edge machine above is consumed by the control loop on every tick,
        listener or not, and that is deliberate: the blackbox has to record the
        edge at the instant the water reached the probe, not at the instant someone
        happened to be watching. But it means an alarm that rises during a tether
        dropout is broadcast into an empty socket set and never mentioned again —
        and a dropout is exactly the minute the water gets in. The stage keeps
        arriving in every telemetry frame, yet the stage is a value; the ALARM is
        the announcement, and the announcement is the thing that was lost.

        So a raised alarm is also latched here, and it is cleared by the leak
        returning to NORMAL rather than by having been delivered. The latch tracks
        the CONDITION, not the message: a client attaching while the hull is still
        wet is told, every time, including the same client attaching again after
        its tether flapped. (This mirrors the client's own model — it latches
        alarmLeak and drops it when telemetry says NORMAL — so the two ends re-arm
        on the same event.)

        Returns a list to match leak_alarm_edges(): both feed the same send loop.
        """
        return [self._alarm_latch] if self._alarm_latch else []

    def telemetry(self, metrics: dict, link_ms: int | None = None) -> Telemetry:
        # The RAW compass, and only a FALLBACK. When navigation has a fresh estimate
        # main.py overwrites heading/heading_card with the estimator's heading —
        # the one the map is drawing and the one gyro_only/mag_cal are describing.
        # Under NAV_FILTER=filtered the two differ by the thrusters' magnetic error,
        # and shipping this number while the map shows the other puts two
        # disagreeing headings in front of one operator. This stands only when nav
        # is not answering, because then it is the only heading left to take.
        #
        # None here means the BNO085 is not answering, and that null is the thing
        # to protect: a frozen bearing is worse than a blank one because the radar
        # is heading-up, so the whole map turns with a number nothing is measuring.
        # An INVENTED bearing is worse again, and that is what used to happen to
        # this null — fill_nav_fields() overwrote it unconditionally from the
        # estimator, whose own default for "no compass" was 0.0, so the frame that
        # reached the client read heading=0.0 card="N": a confident DUE NORTH
        # sitting beside a NO COMPASS badge and a "bno085 not answering" fault, and
        # the radar swung the whole map north on it.
        #
        # main.py now states and implements the precedence rule (see
        # fill_nav_fields): the estimator refines this reading and may only replace
        # it when BOTH headings exist. A null set here therefore survives to the
        # client, and a real reading here is never blanked by navigation going
        # quiet. Whatever else changes, that rule lives in exactly one place.
        heading = self.hw.read_heading()
        # DEPTH IS ARITHMETIC ON PRESSURE, so it inherits the pressure's silence.
        # This used to be `round(read_pressure(), 1)` followed by a subtraction,
        # which raises on None — and the tempting repair, `read_pressure() or
        # surface_psi`, would put the sub at exactly 0.00 m on screen while it
        # descends. Neither is acceptable: one takes the telemetry loop down, the
        # other says "at the surface" about a vehicle nobody can measure. A sensor
        # that is not answering reaches the client as null and the two travel
        # together — a depth without its pressure would be a number with no
        # provenance.
        raw_psi = self.hw.read_pressure()
        pressure = None if raw_psi is None else round(raw_psi, 1)
        depth = (
            None
            if pressure is None
            else max(0.0, round((pressure - settings.surface_pressure_psi) / settings.psi_per_meter, 2))
        )
        leak_state = self.hw.read_leak()
        # `leak` stays the old single bit so a client that only knows about the bool
        # keeps working, and `leak_state` carries which stage it is; the two travel
        # together and never disagree.
        #
        # IT IS "NOT CERTIFIED DRY", NOT "WARN OR FLOOD", and the test is written
        # against NORMAL rather than against the wet stages on purpose. The hardware
        # layer now also answers "UNKNOWN" — the probes are not being sampled, so
        # nothing has been established in either direction — and False here would
        # hand a bool-only client the one claim that state exists to withhold: that
        # the hull is dry. Comparing against NORMAL means every stage this file has
        # never heard of lands on the alarming side by construction, which is the
        # only direction to be wrong in about water: over-warning costs a cancelled
        # dive, under-warning costs the sub. Do not "tidy" this into
        # `in ("WARN", "FLOOD")` — that reintroduces the exact hole.
        leaking = leak_state != "NORMAL"
        g_on, g_lvl = self.hw.get_light("green")
        w_on, w_lvl = self.hw.get_light("white")
        # Unknown has to survive the trip topside. round(None) is a TypeError, and
        # the tempting round(level or 0.0, 3) would be worse than a crash: it turns
        # "this stepper has never been homed" into "the syringe is empty", which is a
        # specific claim about buoyancy that an operator would dive on. None goes out
        # as JSON null and the client draws the cannot-tell syringe instead.
        level = self.hw.get_ballast_level()
        current = self.hw.read_current_a()
        # THE EXCEPTION IS GONE: a dead pack monitor now sends null like every
        # other silent sensor. This used to land on 0.0 because Telemetry.battery_v
        # was a required float — main.py banded it (`v < settings.battery_crit_v`)
        # on the control loop's own path with no guard, so a null would have raised
        # inside the journal and taken telemetry, the watchdog and the blackbox
        # down together. That reasoning was right and the consequence was still
        # wrong: 0.0 V is not a neutral filler, it is the most alarming number the
        # gauge can show, so a dead INA219 painted a red "BATTERY 0.0V · SURFACE"
        # over a full pack — a critical alarm invented by an absent sensor. "The
        # right direction to be wrong in" is still being wrong, and an alarm that
        # cries wolf is an alarm the operator learns to clear without reading.
        # battery_band() is None-safe now and answers "unknown", so nothing on the
        # loop's path can trip over this and "ina219" still rides in sensor_faults
        # to say WHY the gauge is blank.
        volts = self.hw.read_voltage()
        # THE REST OF THE BNO085, off the same handle as `heading` above and gated
        # by the hardware layer on the same liveness answer — so all six IMU fields
        # in this frame (heading, heading_card, mag_cal, and these four) are null
        # together whenever the chip stops, without this file having to coordinate
        # anything. They were measured and logged into the dive journal for months
        # and never put on the wire; see protocol.py for why they belong on this
        # frame and not on NavState.
        gyro_z = self.hw.read_gyro_z_dps()
        accel_fwd = self.hw.read_accel_fwd_ms2()
        # UNPACKING A PAIR IS A NEW WAY FOR THE CONTROL LOOP TO DIE, which is why
        # this one call is guarded and the scalar readbacks around it are not.
        # `pitch, roll = hw.read_pitch_roll()` raises on None, on a bare float and
        # on a three-tuple, and rov.telemetry() is called from _control_loop with
        # nothing above it catching anything — so a backend that returns the wrong
        # shape would take telemetry, the watchdog and the blackbox down together,
        # over a reading nothing safety-critical branches on. api/nav/sensors.py
        # guards the identical call for the identical reason. A broken backend is
        # a backend that cannot tell us the attitude; it is not a reason to stop
        # driving the sub.
        try:
            pitch, roll = self.hw.read_pitch_roll()
        except Exception:  # noqa: BLE001 — not a pair; the backend is broken, not the sub
            log.debug("read_pitch_roll() did not return a pair; reporting cannot-tell", exc_info=True)
            pitch, roll = None, None
        return Telemetry(
            armed=self.armed,
            left=round(self.left, 3),
            right=round(self.right, 3),
            ballast_level=None if level is None else round(level, 3),
            ballast_target=round(self.ballast_target, 3),
            depth=depth,
            pressure=pressure,
            heading=None if heading is None else round(heading, 1),
            heading_card=cardinal(heading),
            magnet=self.hw.get_magnet(),
            light_green=g_on,
            light_white=w_on,
            light_green_level=round(g_lvl, 2),
            light_white_level=round(w_lvl, 2),
            leak=leaking,
            leak_state=leak_state,
            battery_v=None if volts is None else round(volts, 1),
            signal=self.hw.link_quality(),
            link_ms=link_ms,
            # --- vehicle truth the hardware layer knows and nothing else does -----
            # Same discipline as the Pi metrics below: a reading the backend cannot
            # take arrives as None and renders as cannot-tell, never as a healthy
            # default. homed=False is not a cannot-tell — it is a definite "the
            # counter has never been zeroed", which is why it is a bool and why
            # ballast_level above is None whenever it is False.
            ballast_homed=self.hw.ballast_homed(),
            ballast_needs_rehome=self.hw.ballast_needs_rehome(),
            # 0 = "a compass answered, and it says it is uncalibrated" (heading
            # suspect); None = "no compass is answering", never wired or wired and
            # stopped. Passed straight through now. It used to go through an
            # _overrides() probe that asked whether the backend had bothered to
            # implement read_mag_cal, because the ABC's cannot-tell for it was the
            # integer 0 and 0 is also a real reading — a distinction that could only
            # be recovered by inspecting the class. That was a workaround for the
            # hardware layer being unable to say "nothing", and it only ever covered
            # the never-wired case: a backend that DID implement the method and then
            # had its IMU die passed the probe and shipped a frozen 3. The layer
            # answers None itself now, so the probe is gone and both failures land
            # on the same honest null.
            mag_cal=self.hw.read_mag_cal(),
            # Rounded for a READOUT, and only a readout. The heading filter and the
            # speed KF never read this frame — they take their own copy off
            # SensorSample at full precision (api/nav/sensors.py) — so no estimate
            # is being made from a rounded number.
            #
            # The None-guard is spelled out on each one, four times, rather than
            # folded into a helper, because the short version is the bug: round(None)
            # raises and the tempting repair is `round(x or 0.0, 2)`, which lands a
            # dead gyro on 0.0 deg/s — "measured, and the sub is not turning" — and a
            # dead accelerometer on "coasting". Those are the two calmest readings
            # each gauge can show, invented by the absence of the chip that takes
            # them. Same shape as depth, heading and battery_v above; written out so
            # it can be argued with.
            gyro_z_dps=None if gyro_z is None else round(gyro_z, 2),
            accel_fwd_ms2=None if accel_fwd is None else round(accel_fwd, 2),
            pitch_deg=None if pitch is None else round(pitch, 1),
            roll_deg=None if roll is None else round(roll, 1),
            current_a=None if current is None else round(current, 2),
            leak_probe_fault=self.hw.leak_probe_fault(),
            leak_rearms=self.leak_rearms,
            # WHICH chips are silent, so a blank gauge arrives with its reason. The
            # nulls above and this list are one verdict read twice — they are both
            # taken from the same hardware call in the same frame, so the console
            # can never show a number the vehicle has already admitted it cannot
            # measure. list() because the hardware hands back a tuple and the
            # contract is JSON.
            sensor_faults=list(self.hw.sensor_faults()),
            # speed_ms / speed_src / snagged / gyro_only are deliberately left at
            # their defaults here. They are ESTIMATOR outputs, not hardware readings:
            # the paddlewheel yields an unsigned magnitude and only the nav filter
            # decides what that becomes, whether it is trustworthy, and whether the
            # sub is snagged. RovState has no handle on the nav service and must not
            # grow one — the control plane holding a reference into navigation is how
            # a nav fault ends up able to stall the thruster loop. main.py's control
            # loop stitches them in from NavService.fresh_state() — along with the
            # heading, so the number and the trust marks that qualify it come from
            # one estimate — in exactly one place. Until it does all four stay
            # None, which reads as cannot-tell; and when nav goes quiet they go
            # BACK to None, because a frozen speed looks like a measurement and a
            # snagged flag that clears itself looks like good news. snagged and
            # gyro_only default to None rather than False for exactly that reason:
            # False is navigation's answer, and RovState has no navigation to ask.
            # Pi system health passes through verbatim — including None, which means
            # "probe unavailable". Never coerce a missing reading into a plausible 0.
            cpu_c=metrics.get("cpu_c"),
            cpu_pct=metrics.get("cpu_pct"),
            ram_pct=metrics.get("ram_pct"),
            disk_gb=metrics.get("disk_gb"),
            uptime_s=metrics.get("uptime_s"),
            net_tether_up=metrics.get("net_tether_up"),
            net_tether_mbps=metrics.get("net_tether_mbps"),
            net_cam_up=metrics.get("net_cam_up"),
            net_cam_signal=metrics.get("net_cam_signal"),
            mock=self.hw.is_mock,
        )

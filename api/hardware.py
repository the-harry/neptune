"""Hardware abstraction layer — GPIO / I2C live behind this interface.

Two backends:
  * MockHardware — a self-contained bench simulator. Fully working with no
    hardware attached, so the whole server (and the client) can be exercised on
    a laptop. `is_mock` is True → telemetry carries `mock: true`.
  * RealHardware — the Pi 3B+ backend: gpiozero for GPIO, smbus2 for I2C, one
    background sensor thread, one bounded-rate stepper thread. Every hardware
    import is lazy and inside that class, because this file is edited on a
    machine with no GPIO and a module-scope `import gpiozero` there takes the
    whole server down at import time.

`get_hardware()` selects one based on settings.hardware_backend ("auto" tries
real, falls back to mock).

All methods MUST be fast and non-blocking — they are called from the asyncio
event loop. Every actuator write is a GPIO write (effectively instant); every
sensor read returns a value the sensor thread already fetched. No method here
waits on a bus, a conversion, or a lock.

LIVENESS IS PART OF EVERY READING. A cached value is only a measurement while
the chip that produced it is still answering, so each readback is gated on
DeviceHealth and returns None — cannot-tell — the moment its device stops. This
is not defensive decoration: a sensor that dies MID-DIVE freezes its last value,
and a frozen value shipped as live is how a sub that is at 8 m shows a confident,
colour-banded 4.3 m. "Absent" therefore means never wired OR wired and stopped,
and both get the same null. sensor_faults() names which chip, so the null on a
gauge arrives with the reason beside it.

THAT GATE COVERS THE SAMPLER, NOT ONLY THE CHIP. The three I2C parts are not the
only things that can stop: the leak probes are GPIO, the paddlewheel and the
carrier check are neither, and all of them are sampled by ONE thread. A thread
that dies, or a tick that raises before their turn comes, freezes them exactly
as a dead chip freezes its cache — and their frozen values are the reassuring
ones ("NORMAL", "4 bars", the last speed). So the leak probes and the sensor
loop itself carry a DeviceHealth of their own, they are sampled in their own
try-block ahead of any bus work, and the readbacks behind them answer
cannot-tell rather than the last comfortable number. LEAK IS THE ONE READING
THAT MUST NEVER FAIL QUIETLY: it is the difference between a recoverable dive
and a lost sub.

The pure sensor arithmetic (leak debounce, paddlewheel window, quadrature
decode, ballast step accounting) lives at MODULE scope rather than inside
RealHardware, because RealHardware cannot be constructed on a laptop — logic
locked inside it is logic no bench test can ever run. Both backends share it.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from collections import deque

from config import settings

# Sensor CALIBRATION constants (metres per pulse, mounting offset, windows) are
# navigation's, not the vehicle's — they are what the dive log and the estimator
# are calibrated against, so they live in one place and this file reads them.
# Safe to import at module scope: nav.config imports nothing but the stdlib and
# nav/__init__.py is a docstring, so there is no cycle back into here.
from nav.config import settings as nav_settings

log = logging.getLogger("neptune.hw")

Which = str  # "green" | "white"
BallastDir = str  # "fill" | "empty" | "hold"


# ---------------------------------------------------------------------------
# Shared sensor logic — pure, backend-agnostic, bench-testable
# ---------------------------------------------------------------------------
class DeviceHealth:
    """Is ONE chip answering RIGHT NOW — which is not "did it ever work".

    THE FAILURE THIS EXISTS FOR, and it is the one two review rounds walked past:
    a sensor that dies MID-DIVE freezes its last value, and the frozen value
    ships as live. The MS5837 stops answering at 4.33 m; every later attempt
    raises; the cache keeps 20.85 psi; sixty seconds of dead-bus ticks change
    nothing; the console paints a confident, colour-banded 4.3 m while the sub
    descends to 8. Every check anyone had written asked "did the chip come up?"
    and the chip HAD come up. Absent has to include "was here and stopped".

    Two ways to fail liveness, because there are two ways a bus dies:

      * CONSECUTIVE RAISES. One NAK on a canal-side loom is noise and the retry
        usually takes; blanking a working gauge on it would teach the operator
        that the blank means nothing. `fail_streak` in a row is not noise.
      * SILENCE. Nothing has to raise for a device to stop answering. A
        conversion state machine that never reaches its collect stage, a sensor
        thread that took an exception and ended, a driver that returns without
        writing — none of them produce an error and all of them leave the cache
        exactly as frozen. So a good read must also have happened RECENTLY,
        inside a window sized to how often this device is actually polled.

    NEVER ANSWERED IS FAULTED, not a gentler state: a device that has not yet
    produced one good read has nothing behind its cache at all.

    Pure and clock-injected — `now` is passed in, never read here — so the whole
    liveness rule can be exercised on a bench in microseconds. Logic that can
    only be reached by waiting on a real dying sensor is logic nobody tests.
    """

    def __init__(self, name: str, fail_streak: int = 3, silence_s: float = 2.0) -> None:
        self.name = name
        self.fail_streak = max(1, int(fail_streak))
        self.silence_s = float(silence_s)
        self.fails = 0
        self.last_ok: float | None = None

    def ok(self, now: float) -> None:
        """A read SUCCEEDED and the cache behind it is fresh."""
        self.fails = 0
        self.last_ok = float(now)

    def failed(self) -> None:
        """An attempt raised. Deliberately does NOT clear last_ok: how long ago
        the device last worked is the other half of the verdict."""
        self.fails += 1

    def answered_ever(self) -> bool:
        return self.last_ok is not None

    def faulted(self, now: float) -> bool:
        if self.last_ok is None:
            return True
        if self.fails >= self.fail_streak:
            return True
        return (now - self.last_ok) > self.silence_s


class LeakDebouncer:
    """One leak probe: N consecutive wet samples latch it.

    Condensation, the splash of a launch, and a droplet running down the inside
    of the hull all touch a probe for a moment. A real ingress does not stop, so
    the run counter resets on any dry sample and only sustained water latches.
    Debouncing is the entire reason the FLOOD alarm is worth believing.

    LATCHING IS ONE-WAY ON PURPOSE. Once water has reached a probe it has
    reached it; the probe drying out later (the sub tilts, the drop rolls off)
    is not evidence that the hull is sound. Clearing the latch takes reset() —
    i.e. a human who has opened the hull and looked.
    """

    def __init__(self, samples: int) -> None:
        self.samples = max(1, int(samples))
        self.wet_run = 0
        self.latched = False

    def sample(self, wet: bool) -> bool:
        if wet:
            self.wet_run += 1
            if self.wet_run >= self.samples:
                self.latched = True
        else:
            self.wet_run = 0
        return self.latched

    def reset(self) -> None:
        """Clear the latch. For tests and for a deliberate post-repair reset —
        never because the alarm is inconvenient."""
        self.wet_run = 0
        self.latched = False


class PaddleWheel:
    """Hall-effect paddlewheel: pulses in, water-relative speed out.

    TWO PHYSICAL LIMITS THAT NO ARITHMETIC HERE CAN FIX, and every caller is
    built around them:

      * It cannot sense DIRECTION. A wheel spun backwards emits identical
        pulses, so the sign of the speed belongs to the throttle and is never
        invented here.
      * It STALLS below roughly 0.1 m/s — bearing friction beats the water. So
        "no pulses" means either genuinely slow OR held still by something.
        Throttle idle + no pulses is a stopped sub; throttle open + no pulses is
        the snag signal (§4c). This class refuses to guess which; it reports what
        the wheel did and lets the estimator decide.

    Quantisation is coarse by construction: one pulse in a 0.5 s window at
    0.05 m/pulse IS 0.1 m/s, so the smallest non-zero reading equals the stall
    speed. The speed KF widens its measurement noise to match rather than
    treating a one-pulse window as a crisp measurement.
    """

    def __init__(self, m_per_pulse: float, window_s: float, stale_s: float) -> None:
        self.m_per_pulse = float(m_per_pulse)
        self.window_s = max(1e-3, float(window_s))
        self.stale_s = float(stale_s)
        self._pulses: deque[float] = deque()
        self._last_pulse_t: float | None = None

    def pulse(self, t: float) -> None:
        """One magnet passed the sensor.

        Called from a GPIO edge callback, so it does the least work that is
        possible: one append. No bus traffic, no logging, no locks — at canal
        speeds these arrive tens of times a second and anything slow here shows
        up as missed pulses, i.e. as a sub that looks slower than it is.
        """
        self._pulses.append(t)
        self._last_pulse_t = t

    def read(self, now: float) -> tuple[float, bool]:
        """(m/s magnitude, fresh). fresh=False means the magnitude is meaningless."""
        while self._pulses and (now - self._pulses[0]) > self.window_s:
            self._pulses.popleft()
        if self._last_pulse_t is None or (now - self._last_pulse_t) > self.stale_s:
            # Never turned at all (no wheel fitted?) or has been silent long
            # enough that it has stopped being evidence. 0.0 rides along so a
            # caller that ignores the flag at least does not get a confident
            # number, but the flag is the answer: carry it as None.
            return (0.0, False)
        # A count of ZERO inside the stale window is a real measurement — the
        # wheel is not turning right now — and it is exactly the reading the snag
        # detector needs. It is reported fresh rather than suppressed.
        return (len(self._pulses) * self.m_per_pulse / self.window_s, True)


# (prev << 2) | cur over the A/B state (A<<1)|B. The eight valid transitions are
# the Gray-code cycle in each direction; the four "both bits changed" cells are
# absent (→ 0) because two edges arrived between reads and the direction is then
# genuinely unknowable — inventing ±2 there would fabricate tether length.
_QUAD_STEP = {
    0b0010: +1,
    0b1011: +1,
    0b1101: +1,
    0b0100: +1,  # A leads B → paying out
    0b0001: -1,
    0b0111: -1,
    0b1110: -1,
    0b1000: -1,  # B leads A → rewinding
}


class QuadratureDecoder:
    """Tether spool encoder A/B → signed tick count.

    Quadrature rather than plain pulse counting because DIRECTION is the whole
    point: payout must be able to go DOWN. Payout is an upper bound on how far
    away the sub can possibly be (§5.5), and winding cable back in genuinely
    tightens that bound. NO MONOTONIC MAX IS APPLIED ANYWHERE — latching the
    high-water mark would leave a sub that has been hauled halfway home still
    claiming it might be at the far end of the tether, which is the opposite of
    what a bound is for.

    Which direction is "out" depends on how the encoder ends up mounted on the
    drum. If a metre of tether pulled off the spool counts DOWN, swap A and B in
    the wiring (or negate here) — the calibration procedure in docs/hardware.md
    says to check exactly that before believing any of it.
    """

    def __init__(self) -> None:
        self.ticks = 0
        self.missed = 0  # both bits changed between reads: an edge got past us
        self._prev: int | None = None

    def update(self, a: bool, b: bool) -> int:
        cur = (int(bool(a)) << 1) | int(bool(b))
        prev, self._prev = self._prev, cur
        if prev is None or prev == cur:
            return 0
        step = _QUAD_STEP.get((prev << 2) | cur, 0)
        if step == 0:
            self.missed += 1
        self.ticks += step
        return step


class BallastAxis:
    """Open-loop syringe position: a step counter between two limit switches.

    There is no position sensor on this axis. The plunger's position is a NUMBER
    WE ARE KEEPING, not a thing anything can see, which is why:

      * before homing there is no number at all — level() is None, never 0.0,
        because 0.0 is the specific claim "the syringe is empty" and the operator
        would dive on it;
      * the limit switches always win. They are the only real position
        information in the entire subsystem;
      * a FULL switch that closes at the wrong count means the count is wrong —
        steps were skipped under load, or the configured span is stale — and the
        level derived from it is not to be believed until homing repeats.
    """

    def __init__(self, span_steps: int, tolerance: float) -> None:
        self.span = max(1, int(span_steps))
        self.tolerance = float(tolerance)
        self.steps = 0
        self.homed = False
        self.needs_rehome = False

    def level(self) -> float | None:
        if not self.homed:
            return None
        return min(1.0, max(0.0, self.steps / self.span))

    def try_step(self, direction: int, at_empty: bool, at_full: bool) -> bool:
        """Take one step unless a limit switch refuses it. True = the step happened.

        HARD RULE, evaluated per step and therefore honoured MID-COMMAND: motion
        into a closed switch never happens. It is checked here, in the same place
        the counter lives, so there is no arrangement of commands that can walk
        the plunger through a hard stop — including a "fill" that was already
        running when the switch closed.
        """
        if direction > 0 and at_full:
            return False
        if direction < 0 and at_empty:
            return False
        if direction == 0:
            return False
        self.steps += direction
        return True

    def mark_empty_limit(self) -> None:
        """The EMPTY switch closed: the plunger is at a known physical stop.

        This is the reference the span was measured from, so touching it zeroes
        the counter and declares the axis homed — whether or not anyone called it
        homing. ballast_home() is simply the command that goes and finds this
        switch on purpose. It also clears needs_rehome: the count is freshly
        referenced against real metal, which is exactly what the flag was asking
        for.
        """
        self.steps = 0
        self.homed = True
        self.needs_rehome = False

    def mark_full_limit(self) -> bool:
        """The FULL switch closed. Returns True if that was a skipped-step event.

        The switch is real position; the counter is bookkeeping. So the counter
        is snapped to the span — but if it had drifted more than the tolerance,
        the axis is flagged for rehoming and the numbers are LOGGED, because the
        discrepancy is the only evidence that will ever exist of steps being
        lost, and swallowing it leaves a syringe that is quietly wrong in the
        middle of its stroke.

        Reaching this stop also counts as homed: the plunger is against known
        metal, so a level can be given. The mismatch check needs a prior
        reference to compare against, which is why it only fires when the axis
        was already homed — an unhomed arrival has nothing to disagree with.
        """
        actual, expected = self.steps, self.span
        skipped = self.homed and abs(actual - expected) > self.tolerance * self.span
        self.steps = self.span
        self.homed = True
        if skipped:
            self.needs_rehome = True
            log.warning(
                "ballast: FULL limit closed at %d steps but the configured span is %d "
                "(%.1f%% out) — steps were skipped, or NEPTUNE_BALLAST_SPAN_STEPS is "
                "stale. The level is not trustworthy until ballast_home() runs again.",
                actual,
                expected,
                100.0 * abs(actual - expected) / self.span,
            )
        return skipped


def thruster_duty(v: float, deadband: float) -> tuple[int, int, float]:
    """-1..1 → (IN1, IN2, EN duty) for one H-bridge. Pure: testable off-Pi.

    Sign picks the direction pins, magnitude becomes the PWM duty. Below the
    deadband both direction pins go low (the bridge coasts) and duty is zero: a
    few percent of duty cannot turn a prop but it does make the bridge sing, and
    a whining idle sounds exactly like a fault to whoever is holding the tether.
    """
    v = max(-1.0, min(1.0, float(v)))
    if abs(v) < deadband:
        return (0, 0, 0.0)
    if v > 0:
        return (1, 0, v)
    return (0, 1, -v)


def leak_probe_fault_from(
    warn_wet: bool, flood_wet: bool, warn_wet_at_boot: bool = False, flood_wet_at_boot: bool = False
) -> str | None:
    """Which leak probe is lying: "warn" | "flood" | "warn+flood" | None.

    A dead probe reads dry forever, and that is the ONE failure the two-probe
    design otherwise hides completely — so the two detectable impossibilities
    are checked instead of assumed away:

      * WET AT POWER-ON. The hull is sealed dry on the bench and then powered up,
        so a probe already reading wet is shorted (corrosion, a stray strand of
        tinned wire) — or the sub really is flooded before it has been launched.
        Both are things the operator must be told at arm time.
      * FLOOD WET WHILE WARN IS DRY. The flood probe sits ~2 cm ABOVE the warn
        probe at the lowest point of the hull. Water cannot touch the upper one
        without having already covered the lower one, so this is not a leak
        pattern, it is a broken probe: either the flood pair is shorted or the
        warn pair is open. One bit each cannot tell us WHICH, and guessing sends
        the operator to strip the wrong probe, so both are named.
    """
    faulty: set[str] = set()
    if warn_wet_at_boot:
        faulty.add("warn")
    if flood_wet_at_boot:
        faulty.add("flood")
    if flood_wet and not warn_wet:
        faulty.update(("warn", "flood"))
    if not faulty:
        return None
    return "warn+flood" if len(faulty) == 2 else faulty.pop()


# The leak subsystem's cannot-tell, spelled once so nobody has to guess at the
# casing. It is a FOURTH stage on the wire — "NORMAL" | "WARN" | "FLOOD" |
# "UNKNOWN" — and it had to be, because the other three are all positive claims
# about the hull. Two of them alarm; the third is the strongest reassurance this
# vehicle ever gives, and a subsystem that has stopped sampling is not entitled
# to give it.
LEAK_UNKNOWN = "UNKNOWN"


def leak_state_from(warn_wet: bool, flood_wet: bool, sampling: bool = True, probe_fault: str | None = None) -> str:
    """The leak stage: "FLOOD" | "WARN" | "UNKNOWN" | "NORMAL".

    THE FAILURE THIS EXISTS FOR. Leak detection was the only reading on the
    vehicle with no liveness gate at all, and the leak probes were sampled inside
    the same try-block as the I2C ticks — so one unexpected raise from a bus chip
    skipped the rest of the tick, the probes stopped being sampled ENTIRELY, and
    read_leak() went on answering "NORMAL" at full telemetry rate for the rest of
    the dive. Every other gauge on the console correctly went blank and named its
    chip; the hull integrity readout, the one that decides whether the dive is
    recoverable, stayed green on evidence nobody was collecting. "NORMAL" is a
    positive safety claim — both probes were read, and both were dry — and it has
    to be earned by a probe that was actually read.

    WET OUTRANKS CANNOT-TELL, and the order below is the whole rule:

      * Water that has ALREADY reached a probe is an established fact, and the
        sampler stopping afterwards does not un-establish it. This is the same
        one-way argument LeakDebouncer.latched is built on — the probe drying is
        not evidence the hull is sound, and neither is the sampler dying. A
        latched FLOOD that decayed to UNKNOWN would be this layer talking the
        console down off a flood, which is unthinkable.
      * Only the REASSURANCE needs liveness. So the gate sits between "WARN" and
        "NORMAL": nothing latched and the probes are being read is NORMAL;
        nothing latched and nobody is reading them is UNKNOWN.

    AND A PROBE ALREADY KNOWN TO BE BROKEN DOES NOT GET TO CERTIFY THE HULL DRY.
    `probe_fault` is leak_probe_fault_from()'s verdict, and it is the vehicle
    saying, in the same frame, that one of these two probes cannot be believed —
    a pair that read wet in a hull sealed dry on the bench, or the physically
    impossible upper-wet-lower-dry. "NORMAL" is a conjunction over BOTH probes
    ("both were read, both were dry"), which is exactly why the two are wired at
    two heights, so one broken probe is enough to make the conjunction
    unavailable. Shipping "NORMAL" beside a named probe fault put the strongest
    reassurance the vehicle gives next to the reason it cannot be given.

    Pure and at module scope so both backends run this identical verdict and a
    bench can exercise it — RealHardware cannot be constructed on a laptop, and a
    rule locked inside it is a rule no test will ever run.
    """
    # FLOOD first: the upper probe being wet says the water is past the point
    # where "finish up" was the right advice, whatever the lower probe says. Water
    # outranks every cannot-tell below, so an alarm is never swallowed by one.
    if flood_wet:
        return "FLOOD"
    if warn_wet:
        return "WARN"
    if not sampling or probe_fault:
        return LEAK_UNKNOWN
    return "NORMAL"


class HardwareBase(ABC):
    is_mock: bool = False

    # --- actuators ---------------------------------------------------------
    @abstractmethod
    def set_armed(self, on: bool) -> None: ...
    @abstractmethod
    def set_thrusters(self, left: float, right: float) -> None: ...
    @abstractmethod
    def set_camera(self, pan: float, tilt: float) -> None: ...
    @abstractmethod
    def ballast_pump(self, direction: BallastDir) -> None: ...
    @abstractmethod
    def ballast_home(self) -> None: ...
    @abstractmethod
    def set_magnet(self, on: bool) -> None: ...
    @abstractmethod
    def set_light(self, which: Which, on: bool) -> None: ...
    @abstractmethod
    def set_light_level(self, which: Which, level: float) -> None: ...
    @abstractmethod
    def release_dropweight(self) -> None: ...

    # --- readbacks / sensors ----------------------------------------------
    @abstractmethod
    def get_magnet(self) -> bool: ...
    @abstractmethod
    def get_light(self, which: Which) -> tuple[bool, float]: ...

    # 0..1 of the calibrated stroke — or None, meaning NEVER HOMED and the
    # position is genuinely unknown. The syringe is driven open-loop by a stepper
    # with no position sensor, so from power-on until ballast_home() has zeroed
    # the counter against the EMPTY limit switch there is no number to give.
    # Returning 0.0 there would assert "empty", which is a specific claim about a
    # thing the vehicle cannot see, and the operator would dive on it.
    @abstractmethod
    def get_ballast_level(self) -> float | None: ...

    # THE THREE READINGS BELOW MAY BE None, AND None IS THE MOST URGENT ANSWER
    # ANY OF THEM CAN GIVE: the device behind it is not answering RIGHT NOW —
    # never wired, or wired and stopped. It is NOT "this frame is a bit old".
    # Returning the last good value instead is the failure this whole layer was
    # rebuilt around: a depth sensor that dies at 4.33 m and keeps shipping 4.33
    # while the sub descends to 8 is not a stale reading, it is a false one, and
    # it is false in the direction that drowns a vehicle in a canal lock.
    @abstractmethod
    def read_pressure(self) -> float | None: ...  # PSI, or None = no depth sensor answering
    @abstractmethod
    def read_heading(self) -> float | None: ...  # degrees 0..360, or None = no compass answering

    # "NORMAL" | "WARN" | "FLOOD" | "UNKNOWN" (== LEAK_UNKNOWN). The fourth one is
    # the cannot-tell and it is NOT decoration: "NORMAL" asserts that both probes
    # were read and both were dry, which is the strongest reassurance this vehicle
    # gives, so a backend whose probes have stopped being sampled says UNKNOWN
    # instead. A backend that cannot fail this way simply never returns it. See
    # leak_state_from(), which both backends run.
    @abstractmethod
    def read_leak(self) -> str: ...
    @abstractmethod
    def read_voltage(self) -> float | None: ...  # volts, or None = no pack monitor answering

    # 0..4 bars, -1 = tether down AND the cannot-tell: an int field on the wire has
    # no null to spend, and -1 is the only value here that is not a claim the link
    # is working. A backend whose link sampler has stopped reports -1 rather than
    # the last count of bars it happened to hold.
    @abstractmethod
    def link_quality(self) -> int: ...

    # --- optional readbacks: the base answers CANNOT-TELL ------------------
    # These are deliberately NOT @abstractmethod. Adding an abstract method
    # breaks every subclass that has not caught up yet — and worse, it pressures
    # a backend into writing a plausible-looking stub purely to satisfy the ABC,
    # which is exactly how a made-up number reaches a gauge. The defaults below
    # all say "I cannot measure that" (None / a None-carrying tuple / not-fresh),
    # so a backend that does not override one is DECLARING it has no such sensor.
    # That is a real answer and callers must handle it; it is never a healthy
    # default.
    #
    # THEY USED TO ANSWER 0.0 AND 0, AND THAT WAS THE BUG. A zero is a
    # measurement — "not turning", "not accelerating", "the compass is fitted and
    # says do not trust it" — so a backend with no IMU at all was making three
    # confident claims about a chip that was not on the board, and every one of
    # them was the reassuring claim rather than the alarming one. Cannot-tell now
    # has its own value, None, which no real reading can collide with.
    def reset_leak_latches(self) -> dict:
        """Re-arm the leak detector: a human has opened the hull and looked.

        LATCHING IS ONE-WAY, AND IT HAS TO STAY THAT WAY — a probe drying out is
        not evidence the hull is sound. But one-way with NO way back meant the
        only thing that cleared a latch was restarting the service, which on the
        water is SSH-ing into a submarine. So the way back exists, and it is
        explicit, logged, and counted rather than quiet.

        THE ONE RULE THAT MAKES THIS SAFE: it clears the MEMORY of water, never
        water that is there NOW. A backend that can still see a wet probe must
        refuse, because at that point the operator is not re-arming a detector,
        they are dismissing a live reading. Everything else about the leak path
        is built so an alarm cannot be talked down; this must not be the hole in
        it. See RealHardware.reset_leak_latches for the enforcement.

        Returns a dict the control plane can hand straight back as an ack:
        {"ok": bool, "cleared": [str], "why": str}. The default is a refusal
        because a backend with no latches has nothing to re-arm, and saying so is
        a real answer.
        """
        return {"ok": False, "cleared": [], "why": "this hardware backend has no leak latches to clear"}

    def read_gyro_z_dps(self) -> float | None:
        # IMU yaw rate, deg/s, + = clockwise (compass convention). None = no gyro
        # answering. 0.0 is reserved for its real meaning, "measured, and it is
        # not turning" — the two used to be the same value and the estimator had
        # no way to tell a still vehicle from a dead chip.
        return None

    def read_accel_fwd_ms2(self) -> float | None:
        # Forward linear acceleration, m/s², + = ahead. Feeds the snag detector
        # and the speed filter ONLY; never integrated twice into position.
        # None = no accelerometer answering; 0.0 means "measured: coasting".
        return None

    def read_mag_cal(self) -> int | None:
        # BNO085 magnetometer calibration status, 0..3, or None.
        #
        # THIS DISTINCTION IS THE WHOLE POINT AND IT WAS BROKEN ON EVERY REAL
        # HULL. 0 is a reading: "a compass answered, and it says it is
        # uncalibrated". None is the absence of one: "nothing answered". They
        # send an operator to do different things — recalibrate, versus go and
        # find out why the IMU is dead — and the protocol has always carried both
        # (Telemetry.mag_cal is Optional). But both shipping backends returned a
        # literal 0 when they had nothing, so None never reached the wire and the
        # client's NO COMPASS flag was unreachable code on a real vehicle: a hull
        # with no IMU wired read as "compass fitted, uncalibrated".
        return None

    def read_pitch_roll(self) -> tuple[float | None, float | None]:
        # (pitch_deg, roll_deg); + pitch = nose up, + roll = starboard down.
        # (None, None) = no attitude source answering. (0.0, 0.0) is reserved for
        # the measurement "level". Attitude is advisory here — nothing
        # safety-critical branches on it — but advisory is not licence to invent.
        return (None, None)

    def read_water_speed(self) -> tuple[float, bool]:
        # (m/s water-relative, fresh). The paddlewheel cannot sense direction —
        # the sign belongs to the throttle — and it stalls below ~0.1 m/s.
        # fresh=False means stale/stalled/not fitted: the magnitude is then
        # meaningless and callers MUST carry it as None, not as 0.0.
        return (0.0, False)

    def read_payout_m(self) -> float:
        # Tether paid out, metres, from the spool encoder — an UPPER BOUND on how
        # far the sub can be (§5.5), not a distance. With no encoder this stays
        # 0.0, which means "no bound known", NOT "the sub is at the origin"; the
        # clamp treats a zero bound as absent rather than pinning the track home.
        return 0.0

    def read_current_a(self) -> float | None:
        # Pack current draw, amps, from the INA219 — free from the same chip as
        # the voltage. None = no current sense fitted; 0.0 would be a claim that
        # nothing is drawing power, which is never true on a running vehicle.
        return None

    def ballast_homed(self) -> bool:
        # True once the EMPTY limit switch has zeroed the step counter this power
        # cycle. False = get_ballast_level() is None and the UI must say unknown.
        return False

    def ballast_needs_rehome(self) -> bool:
        # True after a skipped-step event (the FULL switch closed at a count that
        # disagrees with the configured span). The counter is known to be wrong,
        # so the level it produces is not to be believed until homing repeats.
        # Surfaced, never swallowed — a quietly-wrong syringe strands a sub.
        return False

    def leak_probe_fault(self) -> str | None:
        # Which leak probe reads open or shorted: "warn" | "flood" | "warn+flood".
        # None = both probes look sane. A dead probe reads dry forever, and that
        # is the single failure the two-probe design otherwise hides completely,
        # so it is checked at arm time rather than assumed away.
        #
        # DELIBERATELY THREE STRINGS AND A None, with no cannot-tell of its own.
        # The verdict is built from facts that are already ESTABLISHED — a probe
        # wet at power-on, a latch that has closed — and an established fact does
        # not expire when the sampler stops. What DOES need a cannot-tell is the
        # question "are the probes being read at all", and that is read_leak()'s
        # UNKNOWN plus the subsystem's name in sensor_faults(). Adding a fourth
        # string here would instead push an unrenderable word into the client's
        # probe-fault vocabulary, which is a display bug on top of a sensor fault.
        return None

    def sensor_faults(self) -> tuple[str, ...]:
        """What is NOT ANSWERING right now, e.g. ("ms5837", "ina219").

        On the contract rather than only on RealHardware, because a null on a
        gauge that nobody can explain is only half a fix. The operator staring at
        a blank depth needs to be told WHICH box to go and look at, and this is
        the only layer that knows. It rides in every telemetry frame.

        The names are the I2C chip designations the wiring diagram and
        docs/hardware.md use — "ms5837", "bno085", "ina219" — so the console
        names the same part a human will be unplugging. Not everything that can
        stop IS a chip, though, so the vocabulary also carries the subsystems
        that have their own way of dying: "leak-probes" (GPIO, not on the bus at
        all) and "sensor-thread" (the loop that samples all of the above). Those
        two name a thing to go and look at just as usefully, and blanking a
        reading without naming its cause is the half-fix this exists to close.

        An empty tuple means nothing is currently named as faulted. It is NOT a
        certificate of health from a backend that does not track liveness: the
        nulls on the individual readings stay the authoritative claim, and this
        only ever adds a name to one.
        """
        return ()

    # --- lifecycle ---------------------------------------------------------
    def update(self, dt: float) -> None:
        """Advance any internal simulation (mock only). No-op for real HW."""

    def safe(self) -> None:
        """Bring the vehicle to a safe state (motors off, disarmed)."""
        self.set_armed(False)
        self.set_thrusters(0.0, 0.0)
        self.ballast_pump("hold")

    def close(self) -> None:
        """Release GPIO/bus handles."""


# ---------------------------------------------------------------------------
# Bench simulator
# ---------------------------------------------------------------------------
class MockHardware(HardwareBase):
    """The whole vehicle, simulated well enough to fly the real control loop.

    This is not a stub table of constants: it models the TRUTH the sensors would
    be measuring (a plunger position the vehicle cannot see until it homes, a
    paddlewheel that stalls, a pack that sags, probes that can be wired wrong) so
    that the estimator, the dashboard and the tests all exercise the same shapes
    they will meet on the Pi. Everything it produces is flagged `mock: true`
    upstream, which is what makes simulating this much acceptable at all.

    Time is SIMULATED: `update(dt)` advances an internal clock rather than
    reading the wall clock, so a test can run ten minutes of dive in a loop
    without sleeping. Nothing here adds noise — sim.py owns realistic noise, and
    randomness inside a fixture is a flaky test waiting to happen.

    Test hooks are the underscore methods at the bottom. They are deliberate,
    documented, and each one reproduces a specific real failure.
    """

    is_mock = True

    # Full throttle ≈ 1 m/s, matching the small-canal-sub default speed LUT.
    # Deliberately a constant here rather than an import of nav.speedlut: a mock
    # that derives its truth from the model under test lets the estimator grade
    # its own homework, and every LUT error would cancel out invisibly.
    FULL_SPEED_MS = 1.0
    # Below this the wheel's bearing friction beats the water and it stops
    # turning. The paddlewheel then goes silent — which is the input the snag
    # detector is built to interpret.
    STALL_SPEED_MS = 0.1
    # Mock depth: a full syringe sinks the sub to about 9 m. Arbitrary, but it
    # spans every depth band the dashboard draws.
    FULL_DEPTH_M = 9.0
    # The chips _kill_sensor() can take out, named EXACTLY as RealHardware's
    # fault keys and as sensor_faults() reports them on the wire. A test written
    # against the bench is then written in the vehicle's own vocabulary, and a
    # console string that works here works on the Pi.
    #
    # I2C DESIGNATIONS ONLY, deliberately. A chip stops ANSWERING — there is a
    # transaction that fails and a part a human can go and unplug. The other two
    # things that can stop on this vehicle are not chips and do not belong in this
    # vocabulary: the leak probes are two wires on a GPIO pin, and the sensor loop
    # is software. They stop in their own way and they have their own hooks,
    # _stall_leak_sampling() and _stall_sensor_thread().
    DEVICES = ("bno085", "ina219", "ms5837")
    # What the STALL hooks can stop, in the names RealHardware faults them under.
    # Separate from DEVICES for the reason above, and named here so the two vocabularies
    # are declared in the same place rather than inferred from the hooks.
    SUBSYSTEMS = ("leak-probes", "sensor-thread")

    def __init__(self) -> None:
        self._clock = 0.0  # simulated seconds since construction
        self._armed = False
        self._left = 0.0
        self._right = 0.0
        self._pan = 0.0
        self._tilt = 0.0
        self._magnet = False
        self._lights = {"green": (True, 0.8), "white": (False, 0.2)}
        self._dropped = False
        # 2S Li-ion: 8.4 V full, 7.4 V nominal. 8.3 V is a healthy pack an hour
        # off the charger. THE OLD 24.8 V IS GONE — it described a vehicle that
        # was never built, and every threshold in the system now reads 2S.
        self._voltage = 8.3
        self._heading = 284.0
        self._gyro_z = 0.0
        self._accel_fwd = 0.0
        self._speed = 0.0  # signed water-relative speed, m/s
        self._mag_cal = 3
        self._payout_m = 0.0
        # --- ballast: what the vehicle BELIEVES vs what is actually true ------
        # `_axis` is the vehicle's belief (a step counter, unknown until homed).
        # `_true_steps` is where the plunger really is — the simulator knows, the
        # vehicle does not. Keeping them apart is the point: it is what makes
        # unknown-until-homed and skipped steps simulable at all.
        self._axis = BallastAxis(settings.ballast_span_steps, settings.ballast_span_tolerance)
        self._true_steps = int(0.40 * self._axis.span)
        self._ballast_dir: BallastDir = "hold"
        self._step_frac = 0.0
        # --- leak probes (raw wet/dry, exactly as the GPIO pins would read) ---
        self._probe_warn_wet = False
        self._probe_flood_wet = False
        self._probe_warn_boot = False
        self._probe_flood_boot = False
        self._leak_rearms = 0
        # --- paddlewheel ------------------------------------------------------
        self._paddle = PaddleWheel(nav_settings.m_per_pulse, nav_settings.paddle_window_s, nav_settings.paddle_stale_s)
        self._paddle_frac = 0.0
        self._paddle_jammed = False
        # --- chips that have been killed by the test hook ---------------------
        # Empty on the bench, which is the point: under NEPTUNE_HW=auto every
        # sensor here is healthy forever, so the cannot-tell paths are unreachable
        # in normal operation and can only be walked deliberately. See
        # _kill_sensor() — this set is the entire mechanism.
        self._dead: set[str] = set()
        # --- subsystems the STALL hooks have stopped --------------------------
        # Kept apart from _dead because a stalled sampler is not a dead chip: no
        # transaction failed, nothing NAKed, and there is no part to unplug. The
        # readings behind it simply stop being taken while the cache goes on
        # holding the last comfortable value — "NORMAL", "4 bars", the last speed.
        # See _stall_leak_sampling() and _stall_sensor_thread().
        self._stalled: set[str] = set()
        log.info("MockHardware active (bench simulation)")

    # actuators
    def set_armed(self, on: bool) -> None:
        self._armed = on
        if not on:
            self._left = self._right = 0.0

    def set_thrusters(self, left: float, right: float) -> None:
        # Same gate the real bridges apply: disarmed is zero, not "whatever was
        # last commanded". Otherwise the mock's speed model keeps the sub moving
        # after a disarm and the map quietly disagrees with the vehicle.
        if not self._armed:
            left = right = 0.0
        self._left, self._right = left, right

    def set_camera(self, pan: float, tilt: float) -> None:
        # v2: no pan/tilt servos are fitted. The numbers are remembered so the
        # client's control path stays exercised end to end on the bench; nothing
        # in the vehicle moves, on the bench or on the Pi.
        self._pan, self._tilt = pan, tilt

    def ballast_pump(self, direction: BallastDir) -> None:
        self._ballast_dir = direction

    def ballast_home(self) -> None:
        # The bench has no plunger to drive, so homing lands immediately. What is
        # preserved is the STATE MACHINE everything downstream consumes: unknown
        # before, 0.0 and homed after, needs_rehome cleared by a real reference.
        self._ballast_dir = "hold"
        self._true_steps = 0
        self._axis.mark_empty_limit()
        log.info("MOCK: ballast homed against the EMPTY switch")

    def set_magnet(self, on: bool) -> None:
        self._magnet = on

    def set_light(self, which: Which, on: bool) -> None:
        _, lvl = self._lights[which]
        self._lights[which] = (on, lvl)

    def set_light_level(self, which: Which, level: float) -> None:
        on, _ = self._lights[which]
        self._lights[which] = (level > 0.02, max(0.0, min(1.0, level)))

    def release_dropweight(self) -> None:
        self._dropped = True
        log.warning("MOCK: drop-weight released (irreversible) — v2 hardware, " "nothing is fitted on the real vehicle")

    # readbacks
    def get_magnet(self) -> bool:
        return self._magnet

    def get_light(self, which: Which) -> tuple[bool, float]:
        return self._lights[which]

    def get_ballast_level(self) -> float | None:
        # None until homed, exactly like the real axis. The bench must show the
        # cannot-tell presentation too, or the one state the operator most needs
        # to recognise is the one state never seen during development.
        return self._axis.level()

    def ballast_homed(self) -> bool:
        return self._axis.homed

    def ballast_needs_rehome(self) -> bool:
        return self._axis.needs_rehome

    def read_pressure(self) -> float | None:
        # A killed MS5837 answers NOTHING — it does not answer its last depth.
        # The sim keeps sinking underneath, exactly as the water does when a
        # connector lets go at 4 m, so a test can watch the true depth run away
        # from the last number the vehicle ever managed to read.
        if "ms5837" in self._dead:
            return None
        # Depth follows the TRUE plunger position, not the believed one: the
        # water does not care whether the sub has homed. This is what makes the
        # unhomed case interesting on the bench — the sub sinks while the syringe
        # gauge honestly says it does not know how full it is.
        depth_m = (self._true_steps / self._axis.span) * self.FULL_DEPTH_M
        return settings.surface_pressure_psi + depth_m * settings.psi_per_meter

    def read_heading(self) -> float | None:
        if "bno085" in self._dead:
            return None
        return self._heading % 360.0

    def read_gyro_z_dps(self) -> float | None:
        if "bno085" in self._dead:
            return None
        return self._gyro_z

    def read_accel_fwd_ms2(self) -> float | None:
        if "bno085" in self._dead:
            return None
        return self._accel_fwd

    def read_mag_cal(self) -> int | None:
        # A dead IMU must NOT keep shipping the calibration it last claimed.
        # _mag_cal freezing at 3 alongside a frozen heading is the worse half of
        # that failure: the bearing stops moving AND keeps the "compass
        # calibrated, in use" trust mark, so the radar stays heading-up and the
        # whole map turns with a number nothing is measuring.
        if "bno085" in self._dead:
            return None
        return self._mag_cal

    def read_pitch_roll(self) -> tuple[float | None, float | None]:
        # Cosmetic but coherent: the hull heels into a turn and noses down while
        # it floods. Nothing safety-critical branches on attitude, so a plausible
        # shape is enough — it exists so the display has something real-shaped to
        # draw, not so anyone navigates by it.
        if "bno085" in self._dead:
            return (None, None)
        roll = max(-25.0, min(25.0, (self._left - self._right) * 15.0))
        pitch = {"fill": -6.0, "empty": 6.0}.get(self._ballast_dir, 0.0)
        return (pitch, roll)

    def read_water_speed(self) -> tuple[float, bool]:
        # A stopped sensor loop stops TOTTING UP the pulses, so the magnitude
        # freezes at whatever the wheel was last doing — 0.8 m/s, fresh, forever,
        # while the sub sits still. Not-fresh is the honest answer and it is a
        # shape every caller already handles, because a stalled wheel produces it.
        if "sensor-thread" in self._stalled:
            return (0.0, False)
        return self._paddle.read(self._clock)

    def read_payout_m(self) -> float:
        return self._payout_m

    def read_leak(self) -> str:
        # The Pi samples these probes ON the sensor thread, so a stopped thread
        # stops them just as surely as a cut probe line does. The mock inherits
        # that coupling rather than modelling two independent failures the vehicle
        # cannot have — a bench test that passed on a shape the Pi cannot produce
        # is a test that proves something about the mock.
        sampling = not self._stalled & {"leak-probes", "sensor-thread"}
        # The raw probe bits stand in for the Pi's debounce latches, and the
        # verdict itself is the shared one, so this is the code the vehicle runs —
        # including the rule that a probe already known faulty does not get to
        # certify the hull dry.
        return leak_state_from(self._probe_warn_wet, self._probe_flood_wet, sampling, self.leak_probe_fault())

    def leak_probe_fault(self) -> str | None:
        # Same rule the Pi runs — the mock feeds raw probe states through the
        # shared detector instead of reimplementing the verdict, so a test that
        # exercises this here is testing the code the vehicle uses.
        return leak_probe_fault_from(
            self._probe_warn_wet, self._probe_flood_wet, self._probe_warn_boot, self._probe_flood_boot
        )

    def reset_leak_latches(self) -> dict:
        # SAME REFUSAL AS THE VEHICLE, and it has to be here rather than only on
        # RealHardware: this is the rule a bench can actually exercise, and a
        # guard that only exists on hardware no laptop can construct is a guard
        # no test will ever run. The mock has raw probe bits where the Pi has
        # debouncers, so "wet right now" is read off those.
        wet_now = [n for n, w in (("warn", self._probe_warn_wet), ("flood", self._probe_flood_wet)) if w]
        if wet_now:
            log.warning("leak re-arm REFUSED (mock): %s probe(s) wet", "+".join(wet_now))
            return {
                "ok": False,
                "cleared": [],
                "wet_now": wet_now,
                "why": (
                    f"the {' and '.join(wet_now)} probe is WET RIGHT NOW. This clears "
                    f"the memory of water, never water that is present"
                ),
            }
        cleared = [
            n
            for n, b in (("warn-wet-at-boot", self._probe_warn_boot), ("flood-wet-at-boot", self._probe_flood_boot))
            if b
        ]
        self._probe_warn_boot = False
        self._probe_flood_boot = False
        self._leak_rearms += 1
        log.warning(
            "leak detector RE-ARMED by operator (mock; cleared: %s; re-arm #%d)",
            ", ".join(cleared) or "nothing was latched",
            self._leak_rearms,
        )
        return {
            "ok": True,
            "cleared": cleared,
            "rearms": self._leak_rearms,
            "why": (
                "both probes read dry and the detector is re-armed"
                if cleared
                else "nothing was latched; both probes read dry and remain armed"
            ),
        }

    def read_voltage(self) -> float | None:
        if "ina219" in self._dead:
            return None
        return self._voltage

    def read_current_a(self) -> float | None:
        # Volts and amps come off the SAME chip, so they die together. Killing
        # the INA219 and leaving a current reading alive would model a failure
        # the hardware cannot have, and a test that passed against it would be
        # proving something about the mock rather than about the vehicle.
        if "ina219" in self._dead:
            return None
        # Pi + electronics idling, plus the motors and the lamps: 2x3 W of bow
        # spot is about 0.8 A off a 2S pack and the ring about 0.5 A. Rough, but
        # honest in SHAPE — the thrusters dominate everything else, which is the
        # entire reason the power budget wanted this number displayed.
        lights = sum(lvl * (0.8 if which == "white" else 0.5) for which, (on, lvl) in self._lights.items() if on)
        return round(0.35 + 2.5 * (abs(self._left) + abs(self._right)) / 2.0 + lights, 2)

    def link_quality(self) -> int:
        # -1 (tether down) rather than a frozen 4 when nothing is sampling the
        # carrier. The bench has no tether to lose, so this is the ONLY way the
        # console's tether-down presentation can be reached under NEPTUNE_HW=auto.
        if "sensor-thread" in self._stalled:
            return -1
        return 4

    def sensor_faults(self) -> tuple[str, ...]:
        # Whatever the kill and stall hooks have taken out, in the same vocabulary
        # the Pi uses. Nothing on the bench ever dies by itself, so this is empty
        # in normal operation — see _kill_sensor() and _stall_leak_sampling().
        return tuple(sorted(self._dead | self._stalled))

    # sim advance
    def update(self, dt: float) -> None:
        if dt <= 0.0:
            return
        self._clock += dt
        self._step_ballast(dt)
        # Turn rate and heading come from ONE expression. The gyro must agree
        # with the heading it is supposed to explain, or the complementary filter
        # is being tested against a contradiction that no real vehicle produces.
        turn_rate = (self._left - self._right) * 20.0  # deg/s, + = clockwise
        self._heading = (self._heading + turn_rate * dt) % 360.0
        self._gyro_z = turn_rate
        speed = (self._left + self._right) / 2.0 * self.FULL_SPEED_MS
        # Clamped: with a small dt a step change in throttle differentiates to an
        # absurd number, and a 40 m/s² spike into the speed filter is a fixture
        # artefact being mistaken for physics. A hull this size cannot exceed
        # about 1 m/s² however hard it is pushed.
        self._accel_fwd = max(-1.0, min(1.0, (speed - self._speed) / dt))
        self._speed = speed
        self._spin_paddle(dt)
        # Payout follows the sub: forward pays cable out, reverse winds it back.
        # It is allowed to DECREASE for the same reason the real decoder is.
        self._payout_m = max(0.0, self._payout_m + speed * dt)
        # Gentle sag, faster under load, and it stops at the documented hard
        # floor rather than pretending a Li-ion pack keeps delivering below
        # 3.0 V/cell. From 8.3 V the amber band arrives in about an hour of
        # loitering, which is roughly what the real pack does.
        load = (abs(self._left) + abs(self._right)) / 2.0
        self._voltage = max(settings.battery_floor_v, self._voltage - (0.0004 + 0.0015 * load) * dt)

    def _step_ballast(self, dt: float) -> None:
        """Walk the stepper at the configured rate, switches and all."""
        direction = {"fill": +1, "empty": -1}.get(self._ballast_dir, 0)
        if direction == 0:
            self._step_frac = 0.0
            return
        span = self._axis.span
        self._step_frac += settings.ballast_step_rate * dt
        whole = int(self._step_frac)
        self._step_frac -= whole
        for _ in range(whole):
            # The switches are physical, so they see the TRUE position. The
            # counter only finds out about them through try_step, exactly as the
            # Pi's stepper thread does.
            if not self._axis.try_step(direction, self._true_steps <= 0, self._true_steps >= span):
                break
            self._true_steps += direction
        if self._true_steps <= 0:
            self._axis.mark_empty_limit()
        elif self._true_steps >= span:
            self._axis.mark_full_limit()

    def _spin_paddle(self, dt: float) -> None:
        """Emit hall pulses at the rate the current speed would produce."""
        v = abs(self._speed)
        if self._paddle_jammed or v < self.STALL_SPEED_MS:
            return  # stalled: no pulses, and after 2 s, stale
        self._paddle_frac += v * dt / max(1e-6, nav_settings.m_per_pulse)
        while self._paddle_frac >= 1.0:
            self._paddle_frac -= 1.0
            self._paddle.pulse(self._clock)

    # ---- test hooks ------------------------------------------------------
    # Each of these reproduces a specific failure the real vehicle can have.
    # They are the bench's only way to reach states that otherwise need water,
    # a broken wire, or a stalled motor.
    def _set_leak(self, state: str) -> None:
        """Drive the probes to a consistent NORMAL / WARN / FLOOD.

        Sets the raw probe bits rather than a state string, because FLOOD means
        both probes are wet — the upper one cannot be under water while the lower
        one is dry. `_set_probe_wet` is the hook for producing that impossible
        pair on purpose.
        """
        state = (state or "NORMAL").upper()
        self._probe_warn_wet = state in ("WARN", "FLOOD")
        self._probe_flood_wet = state == "FLOOD"

    def _set_probe_wet(self, warn: bool, flood: bool) -> None:
        """Set the two probes independently — including physically impossible
        combinations, which is how the probe-fault detector gets exercised."""
        self._probe_warn_wet = bool(warn)
        self._probe_flood_wet = bool(flood)

    def _set_probe_wet_at_boot(self, warn: bool, flood: bool) -> None:
        """Simulate a probe that was already reading wet at power-on, i.e. a
        shorted or corroded probe. The real backend latches this in __init__."""
        self._probe_warn_boot = bool(warn)
        self._probe_flood_boot = bool(flood)

    def _set_mag_cal(self, cal: int) -> None:
        """0..3. Below 2 the heading is suspect everywhere it is displayed and
        the pre-dive check fails — this is how that path gets walked.

        This is a COMPASS THAT ANSWERED, including at 0: "fitted, and telling you
        not to trust it". For "there is no compass" — mag_cal null, the NO COMPASS
        flag — use _kill_sensor("bno085"); the two are different claims and there
        is no value of `cal` that can express the second one.
        """
        self._mag_cal = max(0, min(3, int(cal)))

    def _jam_paddle(self, jammed: bool) -> None:
        """Stop the wheel while the thrusters keep running: the sub is pinned on
        a shopping trolley. High thrust with no measured speed for 2 s is exactly
        what the snag detector is looking for."""
        self._paddle_jammed = bool(jammed)

    def _stall_leak_sampling(self, stalled: bool) -> None:
        """STOP SAMPLING the leak probes — the probes are fine, nobody is reading them.

        A DIFFERENT FAILURE FROM A WET PROBE OR A BROKEN ONE, and the one that had
        no hook at all. On the Pi the probes are sampled at 10 Hz by the sensor
        thread, and that sampling used to share a try-block with the I2C ticks: one
        unexpected raise from a bus chip skipped the rest of the tick, the probes
        stopped being read ENTIRELY, and read_leak() went on answering "NORMAL" —
        a hull integrity guarantee, at full telemetry rate, from debouncers nobody
        was sampling. Every other gauge on that console blanked and named its chip.

        Not a _kill_sensor device on purpose: there is no chip here to stop
        answering and no transaction to fail, so it would be the wrong vocabulary
        (see DEVICES). What it produces is read_leak() == LEAK_UNKNOWN and
        "leak-probes" in sensor_faults() — the same pair the Pi produces when its
        leak-probe DeviceHealth goes quiet.

        Water that has ALREADY reached a probe still reports: an established fact
        does not expire because the sampler stopped. Only the reassurance does.
        """
        if stalled:
            self._stalled.add("leak-probes")
            log.warning(
                "MOCK: leak probe sampling stalled — the hull state goes to "
                "%s, because NORMAL is a claim nobody is checking",
                LEAK_UNKNOWN,
            )
        else:
            self._stalled.discard("leak-probes")

    def _stall_sensor_thread(self, stalled: bool) -> None:
        """STOP THE SENSOR LOOP GOING ROUND — the failure that hides all the others.

        On the Pi one thread samples everything, so when it dies (or wedges in a
        driver that never returns) every cache below it freezes at once. The three
        chips fall out on their own, because their liveness windows expire with
        nothing refreshing them — but the two readings with no chip to blame,
        water speed and link bars, would go on handing back the last value
        forever: a paddlewheel "measurement" of the speed the sub was making
        before the thread stopped, and four bars of a tether nobody is checking.

        Here that is a stall rather than a kill for the same reason as the leak
        probes: no chip, nothing to unplug. The leak probes stop with it, because
        on the Pi they are sampled BY this thread — the mock inherits that
        coupling rather than pretending the two can fail apart.
        """
        if stalled:
            self._stalled.add("sensor-thread")
            log.warning(
                "MOCK: sensor loop stopped — water speed goes not-fresh, the "
                "link reads -1, and the leak probes stop being sampled with it"
            )
        else:
            self._stalled.discard("sensor-thread")

    def _kill_sensor(self, device: str) -> None:
        """Stop a named chip MID-RUN: from now on it answers nothing at all.

        THE HOOK THIS WHOLE ROUND EXISTS FOR. Two review passes reasoned about
        sensors that never answered and nobody reasoned about sensors that
        STOPPED, because there was no way to make one stop. A vehicle whose
        sensors are healthy from power-on to shutdown cannot demonstrate the
        failure that matters: the connector that vibrates loose at 4.33 m, the
        BNO085 that browns out when the thrusters spike, the I2C line that
        corrodes through halfway down a canal. On the bench under NEPTUNE_HW=auto
        every reading is healthy forever, so these paths are unreachable unless
        something reaches in and breaks one on purpose. This is that something.

        Killed is not "returns zero" and not "returns its last value" — it is the
        real shape of a dead chip as far as everything above it can tell: the
        readbacks on it go to cannot-tell and its name appears in
        sensor_faults(). The SIMULATION UNDERNEATH KEEPS RUNNING, which is the
        part that makes it a useful fixture: the sub goes on descending while the
        depth readout stays blank, so a test can assert that the number did not
        follow the water down and did not sit frozen at the last one either.

        `device` is one of DEVICES, and an unknown name RAISES rather than
        quietly killing nothing — a typo in a test that silently exercises a
        healthy vehicle is a test that passes for the wrong reason forever.

        Death here is a SET MEMBERSHIP, not a DeviceHealth window, and that is
        deliberate. The mock's clock is simulated and DeviceHealth's is the wall
        clock, so routing the bench through it would make every liveness test
        depend on how long the test itself took to run — the flaky-fixture
        problem this class avoids everywhere else. The real rule is not going
        untested for it: DeviceHealth is pure and lives at module scope, exactly
        so its streaks and windows can be driven directly with an injected clock.
        This hook tests what everything ABOVE the hardware layer does with a dead
        sensor; DeviceHealth tests when a sensor counts as dead.

        Usage:
            hw._kill_sensor("ms5837")     # depth stops answering
            ...                           # drive the sim on; the sub keeps sinking
            assert hw.read_pressure() is None
            assert hw.sensor_faults() == ("ms5837",)
            hw._revive_sensor("ms5837")   # the connector is pushed back on

        For the two things that stop WITHOUT being a chip — the leak probes, the
        sensor loop — use _stall_leak_sampling() / _stall_sensor_thread(). They are
        not devices and naming them here would put software in a vocabulary of
        parts.
        """
        if device not in self.DEVICES:
            raise ValueError(
                f"_kill_sensor({device!r}): unknown device. Killable chips are "
                f"{', '.join(self.DEVICES)} — the same names RealHardware faults "
                f"under and sensor_faults() reports. For a sampler that stopped "
                f"rather than a chip that died, see _stall_leak_sampling() and "
                f"_stall_sensor_thread()."
            )
        self._dead.add(device)
        log.warning(
            "MOCK: %s killed — it now answers cannot-tell, and the sim "
            "underneath keeps running so the truth can drift away from "
            "the last value the vehicle read",
            device,
        )

    def _revive_sensor(self, device: str) -> None:
        """The chip answers again — a reseated connector, a bus that recovered.

        Recovery is half the contract and it is the half that gets skipped: a
        depth readout that goes blank and STAYS blank after the sensor comes back
        is its own fault, and one nobody would notice until a dive. The mock's
        readings resume from the CURRENT simulated truth, never from the value
        that was frozen when it died.
        """
        if device not in self.DEVICES:
            raise ValueError(
                f"_revive_sensor({device!r}): unknown device. Killable chips are " f"{', '.join(self.DEVICES)}."
            )
        self._dead.discard(device)
        log.info("MOCK: %s answering again", device)

    def _force_skipped_steps(self, steps: int) -> None:
        """The driver pulsed `steps` times and the motor did not move.

        The counter is advanced and the plunger is not, which is precisely what a
        stepper stalling against a stiff syringe does: the reported level is
        wrong from that instant and NOTHING can tell, because the axis has no
        position sensor. The lie only becomes detectable when a limit switch
        closes at the wrong count — so drive to the FULL stop after calling this
        and the mismatch surfaces. Anything over ballast_span_tolerance (5% of
        the span = 200 steps at the defaults) must raise needs_rehome.
        """
        self._axis.steps += int(steps)


# ---------------------------------------------------------------------------
# Real Pi backend — Pi 3B+, gpiozero + smbus2, one sensor thread
# ---------------------------------------------------------------------------
class RealHardware(HardwareBase):
    """The vehicle as actually built (docs/hardware.md is this class, on paper).

    THREADING. Three threads touch this object:
      * the asyncio event loop calls every public method, and none of them may
        block — they write a GPIO pin or return a cached value, nothing else;
      * `_sensor_loop` owns ALL I2C traffic and the 10 Hz leak sampling, and is
        the only writer of the `_c_*` cache;
      * `_stepper_loop` owns the ballast axis and is the only writer of its
        counter. gpiozero's own edge threads call the two interrupt handlers.

    There is no lock on the sensor cache. Each cached value is a single object
    rebound by one writer and read by the loop; under the GIL that rebinding is
    atomic, and taking a lock on the read path is precisely the blocking this
    module's header forbids. Values that must stay mutually consistent (pitch
    with roll, speed with its freshness flag) are stored as one tuple and
    rebound whole, so a reader can never see half an update.
    """

    is_mock = False

    # ---- pin map (BCM) — mirrored exactly in docs/hardware.md -------------
    #
    # THE PWM CHANNEL-SHARING PITFALL, which dictates this entire map: the Pi's
    # two hardware PWM channels are each exposed on two pins. GPIO12 and GPIO18
    # are both channel 0; GPIO13 and GPIO19 are both channel 1. Driving 12 and 18
    # at once does not give two independent PWMs — the second takes the first's
    # frequency and duty with it, and the symptom is one motor mysteriously
    # tracking another. FOUR INDEPENDENT HARDWARE PWMs ARE IMPOSSIBLE ON THIS
    # CHIP. The thrusters need clean PWM more than anything else does, so they
    # take channel 0 and channel 1. The lights run software PWM at ~200 Hz, which
    # no LED and no eye can tell apart. GPIO18 and GPIO19 are left UNUSED so that
    # nothing can quietly claim half a channel later.
    PIN_THRUST_L_EN = 12  # H-bridge A EN  — port motor speed   (hardware PWM ch0)
    PIN_THRUST_L_IN1 = 5  # H-bridge A IN1 — port motor, ahead
    PIN_THRUST_L_IN2 = 6  # H-bridge A IN2 — port motor, astern
    PIN_THRUST_R_EN = 13  # H-bridge B EN  — starboard motor speed (hardware PWM ch1)
    PIN_THRUST_R_IN1 = 16  # H-bridge B IN1 — starboard motor, ahead
    PIN_THRUST_R_IN2 = 26  # H-bridge B IN2 — starboard motor, astern
    PIN_LIGHT_WHITE = 20  # MOSFET gate — both 3 W bow spots, switched as ONE channel
    PIN_LIGHT_GREEN = 21  # MOSFET gate — 5 V green LED ring around the hull
    PIN_BALLAST_STEP = 23  # A4988 STEP — one pulse, one step
    PIN_BALLAST_DIR = 24  # A4988 DIR  — high = fill (plunger toward the FULL stop)
    PIN_BALLAST_EN = 25  # A4988 /EN  — ACTIVE LOW: low enables the driver
    PIN_LIMIT_EMPTY = 22  # limit switch at the EMPTY end of the syringe stroke
    PIN_LIMIT_FULL = 27  # limit switch at the FULL end
    PIN_LEAK_WARN = 17  # leak probe at the lowest point of the hull
    PIN_LEAK_FLOOD = 4  # leak probe ~2 cm above the WARN probe
    PIN_PADDLE = 10  # paddlewheel hall sensor (A3144), one pulse per magnet
    PIN_SPOOL_A = 9  # tether spool encoder channel A
    PIN_SPOOL_B = 11  # tether spool encoder channel B
    #
    # BOTH limit switches are wired NORMALLY-CLOSED TO GROUND against the
    # internal pull-ups. Un-triggered the switch holds the pin LOW; pressing the
    # lever OPENS the contact and the pull-up takes the pin HIGH. So TRIGGERED ==
    # HIGH — and a cut switch lead, a pulled connector or a corroded contact all
    # read as HIGH too, i.e. as "triggered". A broken switch therefore fails to a
    # stop instead of to a silent absence, which is the only acceptable direction
    # for a hard stop on an axis with no position sensor. The visible consequence
    # is that with the switches unplugged BOTH read triggered and the syringe
    # refuses to move at all — which is exactly how an unfinished harness should
    # behave, and the stepper thread names that case rather than obeying it.
    #
    # Both leak probes use the internal pull-ups the other way round: dry is an
    # open circuit (pin HIGH), and water bridging the interleaved wires pulls the
    # pin LOW. WET == LOW.
    #
    # I2C1 is GPIO2 (SDA) / GPIO3 (SCL) — one bus, three chips, no address
    # conflicts:
    I2C_BUS = 1
    ADDR_BNO085 = 0x4A  # IMU: fused yaw, gyro, linear accel, mag cal status
    ADDR_MS5837 = 0x76  # MS5837-30BA depth/pressure
    ADDR_INA219 = 0x40  # pack voltage + current, high-side shunt

    # The INA219 breakout ships with a 0.1 Ω shunt. If a different shunt is
    # fitted, the current reading scales by exactly the wrong factor and nothing
    # looks broken — change it here and in docs/hardware.md together.
    INA219_SHUNT_OHMS = 0.1

    # Sensor thread base rate. The IMU runs every tick (50 Hz, inside the
    # BNO085's 20-50 Hz report range), the depth state machine advances on its
    # own schedule (~10 Hz), leak sampling divides down to 10 Hz and the INA219
    # to 2 Hz — pack voltage does not move fast enough to be worth more.
    SENSOR_HZ = 50.0

    # MS5837 commands. D1/D2 at OSR 8192 (the highest oversampling) take 17.2 ms
    # each — the reason the depth read is a state machine rather than a sleep.
    _MS_RESET = 0x1E
    _MS_PROM = 0xA0
    _MS_CONVERT_D1 = 0x4A
    _MS_CONVERT_D2 = 0x5A
    _MS_ADC_READ = 0x00

    def __init__(self) -> None:
        # Until the wiring exists this backend cannot read anything, and it must
        # say so LOUDLY rather than return zeros. Reporting mock=False while every
        # sensor returns a constant is the worst of both worlds: the dashboard
        # drops the SIM badge and presents "0.0 V / heading 0 / at the surface" as
        # genuine instrument readings. Raising here lets get_hardware()'s "auto"
        # mode fall back to the honest bench simulator, which at least flags itself.
        if not self._gpio_available():
            raise RuntimeError(
                "RealHardware: no GPIO/I2C backend wired up yet "
                "(flip `wired` in RealHardware._gpio_available once the harness "
                "is built). Set NEPTUNE_HW=mock to silence this, or wire the sensors."
            )
        # EVERY hardware import is lazy and lives in here. This file is edited on
        # a laptop with no GPIO; a module-scope `import gpiozero` would take the
        # entire server down at import time on the bench, which is a far worse
        # failure than the one it would be trying to report.
        from gpiozero import DigitalInputDevice, DigitalOutputDevice, PWMOutputDevice

        self._armed = False
        self._lights: dict[str, tuple[bool, float]] = {"green": (False, 0.0), "white": (False, 0.0)}
        self._magnet = False
        self._faults: set[str] = set()
        self._fault_logged: dict[str, float] = {}
        self._stop = threading.Event()

        # --- ONE GROUP AT A TIME, AND A GROUP THAT IS NOT THERE IS NAMED -----
        #
        # This block used to be one straight run of constructors, so the FIRST pin
        # that would not come up took the whole backend down with it and
        # NEPTUNE_HW=auto fell all the way back to the bench simulator. On a
        # finished vehicle that is defensible. On a vehicle being BUILT it is not:
        # it means the only way to test the first sensor soldered is to have
        # soldered all of them, and the failure it produces — a silent demotion to
        # mock — is the one shape this file exists to prevent, because a simulator
        # answers every question smoothly and truthfully answers none of them.
        #
        # So each group is brought up on its own. A group that raises leaves its
        # devices as None, latches a fault under its own name, and the methods
        # that would drive it refuse rather than crash. is_mock stays False,
        # because the vehicle IS real — it is a real vehicle with three sensors
        # fitted, and sensor_faults() says which three.
        #
        # THE ONE PLACE THIS IS NOT ALLOWED TO DEGRADE QUIETLY IS THE THRUSTERS.
        # Everything else that is missing costs a reading; a missing thruster
        # group costs control of a vehicle in water. set_armed() refuses to arm
        # without it — see there.
        self._have: dict[str, bool] = {}

        def _group(name: str, build, what: str) -> None:
            """Bring up one group; on failure name it and carry on."""
            try:
                build()
                self._have[name] = True
            except Exception as exc:  # noqa: BLE001 — the whole point is not to raise
                self._have[name] = False
                self._fault(name, "%s did not come up (%s) — %s", name, exc, what)

        # --- outputs first, and safe before anything else runs ---------------
        # Order matters on a cold boot: the H-bridge inputs float until they are
        # driven, and a floating IN pin with the motor rail already up is a prop
        # that spins the moment the Pi powers on.
        self._l_en = self._l_in1 = self._l_in2 = None
        self._r_en = self._r_in1 = self._r_in2 = None

        def _build_thrusters() -> None:
            self._l_en = PWMOutputDevice(self.PIN_THRUST_L_EN, frequency=settings.thruster_pwm_hz)
            self._l_in1 = DigitalOutputDevice(self.PIN_THRUST_L_IN1, initial_value=False)
            self._l_in2 = DigitalOutputDevice(self.PIN_THRUST_L_IN2, initial_value=False)
            self._r_en = PWMOutputDevice(self.PIN_THRUST_R_EN, frequency=settings.thruster_pwm_hz)
            self._r_in1 = DigitalOutputDevice(self.PIN_THRUST_R_IN1, initial_value=False)
            self._r_in2 = DigitalOutputDevice(self.PIN_THRUST_R_IN2, initial_value=False)
            # Set BEFORE the zeroing call below, because set_thrusters() now checks
            # it and would otherwise decline to do the one thing that must happen
            # on every boot.
            self._have["thrusters"] = True
            self.set_thrusters(0.0, 0.0)

        _group("thrusters", _build_thrusters, "the sub CANNOT BE ARMED and will not answer the sticks")

        # Software PWM (see the channel-sharing note above). gpiozero's default
        # pin factory drives PWMOutputDevice in software on every pin anyway;
        # GPIO12/13 are still the right pins for the thrusters because they are
        # the only two that CAN be promoted to hardware PWM later (pigpio pin
        # factory or a dtoverlay) without moving any wires.
        self._light_dev: dict[str, object] = {}

        def _build_lights() -> None:
            self._light_dev = {
                "white": PWMOutputDevice(self.PIN_LIGHT_WHITE, frequency=settings.light_pwm_hz),
                "green": PWMOutputDevice(self.PIN_LIGHT_GREEN, frequency=settings.light_pwm_hz),
            }

        _group("lights", _build_lights, "both lamp channels are dead on this vehicle")

        # --- ballast axis ----------------------------------------------------
        # The axis bookkeeping is plain Python and is built whatever the pins do,
        # so the readbacks have something coherent to answer with.
        self._axis = BallastAxis(settings.ballast_span_steps, settings.ballast_span_tolerance)
        self._ballast_cmd: BallastDir = "hold"
        self._homing = False
        self._step_pin = self._dir_pin = self._en_pin = None
        self._limit_empty = self._limit_full = None

        def _build_ballast() -> None:
            self._step_pin = DigitalOutputDevice(self.PIN_BALLAST_STEP, initial_value=False)
            self._dir_pin = DigitalOutputDevice(self.PIN_BALLAST_DIR, initial_value=False)
            # active_high=False makes .on() drive the pin LOW, so `.on()` reads as
            # "driver enabled" instead of the double negative the A4988 datasheet
            # leaves you with.
            self._en_pin = DigitalOutputDevice(self.PIN_BALLAST_EN, active_high=False, initial_value=False)
            self._limit_empty = DigitalInputDevice(self.PIN_LIMIT_EMPTY, pull_up=True)
            self._limit_full = DigitalInputDevice(self.PIN_LIMIT_FULL, pull_up=True)

        _group("ballast", _build_ballast, "the syringe cannot be driven and depth must be flown on thrust alone")

        # --- leak probes ------------------------------------------------------
        # The debouncers exist either way: read_leak() consults them, and a
        # debouncer that has never been sampled reports the not-certified-dry
        # answer, which is the correct one for probes that are not there.
        self._leak_warn_in = self._leak_flood_in = None
        self._warn_debounce = LeakDebouncer(settings.leak_debounce_samples)
        self._flood_debounce = LeakDebouncer(settings.leak_debounce_samples)
        self._warn_wet_at_boot = False
        self._flood_wet_at_boot = False
        # How many times an operator has re-armed the detector this run. On the
        # wire so the console can say the reassurance it is showing was restored
        # by hand rather than never having been in doubt.
        self._leak_rearms = 0

        def _build_leak() -> None:
            self._leak_warn_in = DigitalInputDevice(self.PIN_LEAK_WARN, pull_up=True)
            self._leak_flood_in = DigitalInputDevice(self.PIN_LEAK_FLOOD, pull_up=True)
            # The hull is sealed dry and then powered up, so a probe already reading
            # wet right now is shorted — or the sub is genuinely flooded before it has
            # been launched. Either way it is a fault, and it is captured HERE because
            # a second later it is indistinguishable from a leak that just started.
            self._warn_wet_at_boot = self._is_wet(self._leak_warn_in)
            self._flood_wet_at_boot = self._is_wet(self._leak_flood_in)
            if self._warn_wet_at_boot or self._flood_wet_at_boot:
                log.error(
                    "leak probe(s) already WET at power-on (warn=%s flood=%s) — shorted "
                    "probe, or the hull is flooded before launch. Do not dive on this.",
                    self._warn_wet_at_boot,
                    self._flood_wet_at_boot,
                )

        _group("leak", _build_leak, "hull integrity is UNKNOWN — not dry, unwatched. Do not dive on this")

        # --- pulse inputs: interrupts, never polling --------------------------
        # gpiozero calls these back on its own edge threads. A polling loop would
        # either sit on the event loop (blocking the whole server) or miss pulses
        # between polls, and a missed pulse is a sub that reads slower than it is.
        self._paddle = PaddleWheel(nav_settings.m_per_pulse, nav_settings.paddle_window_s, nav_settings.paddle_stale_s)
        self._spool = QuadratureDecoder()
        self._paddle_in = None
        self._spool_a = self._spool_b = None

        def _build_pulses() -> None:
            # A3144 hall sensors are open-collector: a magnet pulls the line LOW, so
            # with pull_up=True the ACTIVE state is that low, and when_activated is
            # the falling edge.
            self._paddle_in = DigitalInputDevice(self.PIN_PADDLE, pull_up=True)
            self._paddle_in.when_activated = self._on_paddle_pulse
            self._spool_a = DigitalInputDevice(self.PIN_SPOOL_A, pull_up=True)
            self._spool_b = DigitalInputDevice(self.PIN_SPOOL_B, pull_up=True)
            for dev in (self._spool_a, self._spool_b):
                # BOTH edges of BOTH channels, or the decoder sees half the
                # transitions and loses the direction information entirely.
                dev.when_activated = self._on_spool_edge
                dev.when_deactivated = self._on_spool_edge

        _group("pulses", _build_pulses, "water speed and tether payout both go to cannot-tell")

        # --- sensor cache (written only by _sensor_loop) ----------------------
        # EVERY ONE OF THESE IS A REMEMBERED VALUE, and a remembered value is only
        # a reading while the chip that produced it is still answering. That is
        # what _health below decides; the readbacks consult it before handing any
        # of this out. Without that gate the cache is a liar with a good memory:
        # the writer simply stops writing when the device dies and every reader
        # keeps getting the last number at full telemetry rate.
        self._c_heading = 0.0
        self._c_gyro_z = 0.0
        self._c_accel_fwd = 0.0
        self._c_mag_cal = 0
        self._c_pitch_roll = (0.0, 0.0)
        self._c_pressure_psi: float | None = None
        self._c_voltage = 0.0
        self._c_current: float | None = None
        self._c_speed = (0.0, False)
        self._c_link = 4

        # --- per-chip liveness ------------------------------------------------
        # One DeviceHealth per I2C chip, and the windows are sized to how often
        # the sensor thread ACTUALLY polls that chip — a window shorter than the
        # poll interval blanks a healthy gauge between reads, and one much longer
        # leaves a dead chip's last value on screen for exactly that long.
        #
        # Written by the sensor thread, read by the event loop, same single-writer
        # rebinding discipline as the cache above: an int and a float per device,
        # no lock, no blocking on the read path.
        self._health = {
            # BNO085: read every 50 Hz tick. Five raises in a row is 0.1 s, which
            # no plausible transient survives, and a whole second with no good
            # report is fifty missed ones — that is a dead driver, not jitter.
            "bno085": DeviceHealth("bno085", fail_streak=5, silence_s=1.0),
            # MS5837: ~10 Hz when healthy, but the failure path backs off to one
            # retry a second, so the streak has to be SHORT or the backoff itself
            # decides how long a dead depth sensor keeps showing its last depth.
            # Two raises ≈ 2 s; the silence window catches the other shape, a
            # conversion state machine that stops reaching its collect stage
            # without ever raising.
            "ms5837": DeviceHealth("ms5837", fail_streak=2, silence_s=2.5),
            # INA219: polled at 2 Hz, so two raises is already a second and the
            # silence window has to clear several poll intervals. Pack voltage
            # moves slowly enough that a wider window costs nothing.
            "ina219": DeviceHealth("ina219", fail_streak=2, silence_s=5.0),
            # NOT A CHIP, and in this dict precisely because everything in this
            # dict gets gated and named the same way. The leak probes are two GPIO
            # pins sampled at 10 Hz; three raises in a row is 0.3 s of a pin that
            # will not read, and a whole second of silence is ten missed samples —
            # two entire debounce windows, i.e. long enough for water to have
            # arrived and latched nothing. The window is deliberately TIGHTER than
            # any of the chips': a stale depth is a wrong number, a stale "NORMAL"
            # is a hull integrity guarantee nobody is checking.
            "leak-probes": DeviceHealth("leak-probes", fail_streak=3, silence_s=1.0),
            # THE SAMPLER ITSELF, which is the failure that hides all the others:
            # _sensor_loop dying (or wedging in a driver that never returns) stops
            # every cache below being written, and the readings that are not gated
            # on a chip — water speed, link bars — would otherwise hand back their
            # last value forever. Only the SILENCE half of the verdict applies:
            # a loop does not raise, it stops, so fail_streak is 1 and unused and
            # nothing ever calls _device_failed on this key. One second is fifty
            # missed ticks, which no GIL hiccup on a 3B+ produces.
            "sensor-thread": DeviceHealth("sensor-thread", fail_streak=1, silence_s=1.0),
        }

        # --- buses ------------------------------------------------------------
        self._bus = None
        self._imu = None
        self._ms_prom: list[int] | None = None
        self._ms_stage = 0
        self._ms_next = 0.0
        self._ms_d1 = 0
        self._ms_d2 = 0
        self._open_i2c()
        self._open_imu()
        self._open_depth()
        self._open_power()

        from sysinfo import TETHER_IFACE

        self._carrier_path = f"/sys/class/net/{TETHER_IFACE}/carrier"

        self._sensor_thread = threading.Thread(target=self._sensor_loop, name="neptune-sensors", daemon=True)
        self._stepper_thread = threading.Thread(target=self._stepper_loop, name="neptune-ballast", daemon=True)
        self._sensor_thread.start()
        self._stepper_thread.start()
        # sensor_faults(), not _faults: every chip is faulted at this instant
        # because none of them has answered yet — the sensor thread has only just
        # started. That is the honest line to print, and it is the same list the
        # console will be shown, so a boot log and a screen can be compared.
        log.info("RealHardware active (GPIO + I2C); not answering yet: %s", ",".join(self.sensor_faults()) or "none")

    @staticmethod
    def _gpio_available() -> bool:
        """True once a real GPIO stack is importable AND the sensor code is wired.

        The import check alone is not enough, because gpiozero installs fine on a
        Pi with nothing attached, and this class would then come up reporting
        mock=False over a loom that does not exist. So there are two gates and
        both must pass: a human saying the harness exists, and the GPIO stack
        actually importing.

        THE HUMAN HALF MOVED OUT OF THIS FUNCTION. It was a `wired = False`
        literal here, which meant asserting "the wires are in the holes" required
        editing this file on the vehicle — a source edit to state a fact about a
        workbench. It is now settings.hardware_wired (NEPTUNE_HW_WIRED), which is
        the same assertion made by the same human in a place that does not need a
        commit. What has NOT changed is that something outside the software has to
        make it: nothing here can see a connector.

        WHAT THIS FLAG NO LONGER MEANS is "every sensor is fitted". It used to be
        all-or-nothing — one flag for a whole vehicle — which made the first sensor
        on the bench untestable, because turning it on claimed the other eleven
        were there too. __init__ now brings up each group separately and names the
        ones that did not come up, so this says only "there is a harness worth
        talking to", and the console reports the rest per group.
        """
        if not settings.hardware_wired:
            return False
        try:
            import gpiozero  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    # ---- actuators --------------------------------------------------------
    def set_armed(self, on: bool) -> None:
        # There is no ESC to arm: these are brushed motors on H-bridges, so
        # arming is a software gate and this is where it is enforced. Disarming
        # zeroes the bridges immediately rather than waiting for the next control
        # frame, because "the next control frame" may never come.
        #
        # ARMING IS REFUSED WITHOUT THE BRIDGES. Every other group in __init__ may
        # be missing and the vehicle still flies, one reading poorer. This one is
        # different in kind: arming a sub whose H-bridges never came up hands the
        # operator a live console, a moving stick and a vehicle in water that
        # cannot answer it — and the console would have no way to know, because
        # "armed" would be true and the thrust command would be accepted and
        # dropped. Refuse, say so, and stay disarmed.
        if on and not self._have.get("thrusters"):
            self._fault(
                "thrusters",
                "REFUSING TO ARM: the H-bridges did not come up, so "
                "the sticks would move nothing. Fix the thruster "
                "wiring before arming this vehicle",
            )
            self._armed = False
            return
        self._armed = bool(on)
        if not on:
            self.set_thrusters(0.0, 0.0)

    def set_thrusters(self, left: float, right: float) -> None:
        if not self._have.get("thrusters"):
            return
        if not self._armed:
            left = right = 0.0
        self._drive_bridge(self._l_in1, self._l_in2, self._l_en, left)
        self._drive_bridge(self._r_in1, self._r_in2, self._r_en, right)

    @staticmethod
    def _drive_bridge(in1, in2, en, v: float) -> None:
        a, b, duty = thruster_duty(v, settings.thruster_deadband)
        # Duty goes to zero BEFORE the direction pins move and comes back after:
        # flipping IN1/IN2 while the bridge is conducting is a shoot-through, and
        # these H-bridge modules are only sometimes protected against it. The cost
        # is a few microseconds of coast on every reversal, which no one can feel.
        en.value = 0.0
        in1.value = a
        in2.value = b
        en.value = duty

    def set_camera(self, pan: float, tilt: float) -> None:
        # v2 — DOCUMENTED NO-OP. No pan/tilt servos are fitted in v1. The
        # protocol fields and the client control stay in place so that adding the
        # servos later is a wiring job and not a protocol change; nothing here
        # pretends the camera moved.
        return

    def ballast_pump(self, direction: BallastDir) -> None:
        # Non-blocking: this only tells the stepper thread which way to walk.
        if direction not in ("fill", "empty", "hold"):
            log.warning("ballast_pump(%r): unknown direction, holding", direction)
            direction = "hold"
        self._ballast_cmd = direction
        if direction == "hold":
            # "hold" also cancels an in-progress homing run. E-STOP calls this,
            # and an E-STOP that leaves the syringe walking is not a stop.
            self._homing = False

    def ballast_home(self) -> None:
        # Arms the homing run; the stepper thread drives toward the EMPTY switch
        # and zeroes the counter when it closes. Returns immediately — a full
        # stroke at 400 steps/s is ten seconds, and blocking the event loop for
        # ten seconds would stop telemetry, the watchdog and the camera with it.
        self._homing = True
        self._ballast_cmd = "hold"
        log.info("ballast: homing toward the EMPTY switch")

    def set_magnet(self, on: bool) -> None:
        # v2 — no electromagnet is fitted. The flag is deliberately NOT latched:
        # get_magnet() must keep answering False, because a magnet indicator lit
        # over a magnet that does not exist is a claim about the world, and an
        # operator would try to pick something up with it.
        log.warning("set_magnet(%s) ignored — the electromagnet is v2 hardware, " "nothing is fitted", on)

    # THE COMMANDED STATE IS RECORDED EVEN WITH NO LAMP ON THE PIN, and the pin is
    # only written if there is one. get_light() answers from _lights, so with the
    # lamps unwired the console shows what you asked for and "lights" sits in
    # sensor_faults() saying it went nowhere. The alternative — dropping the
    # command — would leave the switch flicking back on its own with no
    # explanation anywhere.
    def set_light(self, which: Which, on: bool) -> None:
        _, lvl = self._lights[which]
        self._lights[which] = (on, lvl)
        dev = self._light_dev.get(which)
        if dev is not None:
            dev.value = lvl if on else 0.0

    def set_light_level(self, which: Which, level: float) -> None:
        on, _ = self._lights[which]
        lvl = max(0.0, min(1.0, level))
        self._lights[which] = (on, lvl)
        dev = self._light_dev.get(which)
        if dev is not None:
            dev.value = lvl if on else 0.0

    def release_dropweight(self) -> None:
        # v2 — LOUD NO-OP. There is no burn-wire and no drop-weight on the v1
        # vehicle, so this cannot do anything, and the dangerous failure is an
        # operator who believes it did. v1 recovery is: empty the ballast and
        # pull the sub in on the tether. That is the procedure; this is not.
        log.warning(
            "DROP-WEIGHT RELEASE COMMANDED BUT NOT FITTED IN V1 — nothing "
            "was released. Recovery: empty the ballast and pull the tether in."
        )

    # ---- readbacks (all cache reads: no bus, no blocking) -----------------
    def get_magnet(self) -> bool:
        return self._magnet  # always False on v1; see set_magnet

    def get_light(self, which: Which) -> tuple[bool, float]:
        return self._lights[which]

    def get_ballast_level(self) -> float | None:
        return self._axis.level()

    def ballast_homed(self) -> bool:
        return self._axis.homed

    def ballast_needs_rehome(self) -> bool:
        return self._axis.needs_rehome

    def _answering(self, key: str) -> bool:
        """Is this chip still answering? Gate on the front of every cache read.

        Cheap enough to sit on the event loop's path — one dict lookup, one
        clock read, an int compare — which is the reason liveness is a verdict
        computed here rather than a bus probe taken here. This method must never
        touch I2C; the module header forbids it and a blocking read on the loop
        stops telemetry, the watchdog and the camera together.
        """
        return not self._health[key].faulted(time.monotonic())

    def read_pressure(self) -> float | None:
        # THE FIX FOR THE FAILURE THAT STARTED ALL THIS. This used to hand back
        # self._c_pressure_psi whenever it was not None — and it stays not-None
        # forever once the sensor has answered even once. So an MS5837 that died
        # at 4.33 m returned 20.85 psi for the rest of the dive, rov.py turned it
        # into depth=4.33 in every frame at 15 Hz, the client stamped each
        # arriving frame as fresh, and the console showed a confident, fully
        # colour-banded 4.3 m while the sub went to 8. The cache was never the
        # problem; treating "I remember a number" as "I can measure it" was.
        #
        # The old never-answered corner (a pinned 0.00 m, documented as an
        # anomaly because the protocol had no cannot-tell for depth) is gone with
        # it: Telemetry.depth is Optional now, so both flavours of absence — never
        # wired, and wired then stopped — say the same honest nothing.
        if self._c_pressure_psi is None or not self._answering("ms5837"):
            return None
        return self._c_pressure_psi

    def read_heading(self) -> float | None:
        # A frozen bearing is worse than no bearing: the radar is heading-up, so
        # the whole map rotates with a number nothing is measuring and it keeps
        # looking exactly as authoritative as it did a minute ago.
        return self._c_heading if self._answering("bno085") else None

    def read_gyro_z_dps(self) -> float | None:
        return self._c_gyro_z if self._answering("bno085") else None

    def read_accel_fwd_ms2(self) -> float | None:
        return self._c_accel_fwd if self._answering("bno085") else None

    def read_mag_cal(self) -> int | None:
        # None whenever the BNO085 is not answering — never wired, or wired and
        # stopped. This used to return the cached int unconditionally, and the
        # two failures it hid are both bad in the same direction:
        #   * never wired: _c_mag_cal sat at its initial 0, so a hull with no IMU
        #     at all claimed "compass fitted, uncalibrated" and the client's NO
        #     COMPASS flag was unreachable code on every real vehicle;
        #   * died mid-dive: _c_mag_cal FROZE at whatever it last reported —
        #     typically 3 — so a frozen heading shipped wearing the "calibrated
        #     and in use" trust mark, which is the strongest claim this system
        #     can make about a bearing, attached to a chip that had stopped.
        return self._c_mag_cal if self._answering("bno085") else None

    def read_pitch_roll(self) -> tuple[float | None, float | None]:
        return self._c_pitch_roll if self._answering("bno085") else (None, None)

    def read_water_speed(self) -> tuple[float, bool]:
        # PaddleWheel.read() has its own stale window, so a wheel that stops
        # turning already reports not-fresh — but that window is only consulted
        # when somebody CALLS it, and the caller is the sensor thread. Kill the
        # thread and _c_speed keeps whatever it held: (0.83, True), fresh forever,
        # on a sub that is stationary or snagged. The wheel's own freshness cannot
        # answer for the loop that reads the wheel, so the loop answers here.
        return self._c_speed if self._answering("sensor-thread") else (0.0, False)

    def read_payout_m(self) -> float:
        # No monotonic max: rewinding the spool genuinely reduces how far away
        # the sub can be. Negative ticks (the drum turned past its start, or A/B
        # are swapped) clamp at zero rather than reporting negative tether.
        return max(0.0, self._spool.ticks * nav_settings.m_per_spool_tick)

    def read_leak(self) -> str:
        # THE ONE READING THAT MUST NEVER FAIL QUIETLY, and until this round it
        # was the only one with no liveness gate at all. _leak_tick() shared a
        # try-block with the I2C ticks, so a single unexpected raise from a bus
        # chip skipped the rest of the tick and the probes stopped being sampled
        # ENTIRELY — while this method went on answering "NORMAL" at 15 Hz. Every
        # other gauge on that console correctly blanked and named its chip; the
        # hull integrity readout stayed green on evidence nobody was collecting.
        #
        # The latches are checked FIRST and are not gated: water that has already
        # reached a probe is an established fact, and the sampler stopping
        # afterwards does not un-establish it. Only the reassurance needs
        # liveness — and a probe this vehicle has already named as broken cannot
        # supply it either. See leak_state_from() for the full argument.
        return leak_state_from(
            self._warn_debounce.latched,
            self._flood_debounce.latched,
            self._answering("leak-probes"),
            self.leak_probe_fault(),
        )

    def reset_leak_latches(self) -> dict:
        """Clear the latches and the wet-at-boot verdict. REFUSED WHILE WET.

        The live pins are read HERE rather than trusting the debouncers, and that
        is the whole guard: the debouncer is a memory, and a memory is exactly
        what this call is asking to erase. Asking the pin instead means the
        refusal is based on the water, not on the bookkeeping about the water.

        Clearing the boot verdict too is deliberate. _warn_wet_at_boot makes
        read_leak() answer UNKNOWN for the life of the process — correctly, because
        a probe wet in a hull sealed dry is a probe that cannot certify anything —
        but it is precisely the state a human returns from having inspected. If
        this cleared only the latches, a bench-wet boot would leave the console
        stuck on UNKNOWN with no way back short of a restart, which is the problem
        this method exists to remove.
        """
        if not self._have.get("leak"):
            return {
                "ok": False,
                "cleared": [],
                "why": (
                    "there are no leak probes on this vehicle to re-arm — the " "leak group did not come up at boot"
                ),
            }
        try:
            wet_now = [
                n for n, dev in (("warn", self._leak_warn_in), ("flood", self._leak_flood_in)) if self._is_wet(dev)
            ]
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "cleared": [],
                "why": f"the probes would not read ({exc}), so nothing can be vouched for",
            }
        if wet_now:
            log.warning("leak re-arm REFUSED: %s probe(s) are wet right now", "+".join(wet_now))
            return {
                "ok": False,
                "cleared": [],
                "wet_now": wet_now,
                "why": (
                    f"the {' and '.join(wet_now)} probe is WET RIGHT NOW. This clears "
                    f"the memory of water, never water that is present — dry the hull "
                    f"and find out where it came from first"
                ),
            }
        cleared = []
        if self._warn_debounce.latched:
            cleared.append("warn-latch")
        if self._flood_debounce.latched:
            cleared.append("flood-latch")
        if self._warn_wet_at_boot:
            cleared.append("warn-wet-at-boot")
        if self._flood_wet_at_boot:
            cleared.append("flood-wet-at-boot")
        self._warn_debounce.reset()
        self._flood_debounce.reset()
        self._warn_wet_at_boot = False
        self._flood_wet_at_boot = False
        self._leak_rearms += 1
        # WARNING, not INFO. Someone dismissed the strongest claim this vehicle
        # makes, and the boat's own log is where that has to be findable later.
        log.warning(
            "leak detector RE-ARMED by operator (cleared: %s; re-arm #%d). Both " "probes read dry at this moment.",
            ", ".join(cleared) or "nothing was latched",
            self._leak_rearms,
        )
        return {
            "ok": True,
            "cleared": cleared,
            "rearms": self._leak_rearms,
            "why": (
                "both probes read dry and the detector is re-armed"
                if cleared
                else "nothing was latched; both probes read dry and remain armed"
            ),
        }

    def leak_probe_fault(self) -> str | None:
        # The DEBOUNCED latches, not the live pins — the same evidence read_leak()
        # answers from. These two used to read the same probes by different rules:
        # a launch splash on the upper probe that the 5-sample debouncer correctly
        # threw away still reached this raw pin read, so the sub said NORMAL and
        # "probe wiring is broken" in the same frame and failed the pre-dive check
        # over one wet frame. One droplet cannot be both nothing and a fault.
        #
        # The detection survives intact, because the failure it exists for is a
        # probe that reads WRONG CONTINUOUSLY: a shorted flood pair sits wet and
        # latches in ~0.5 s, and an open warn pair never latches no matter how
        # deep the water gets — flood latched with warn never latched is still
        # the impossibility, just held to the same half-second of evidence the
        # leak alarm itself needs. Latching is one-way, so once real water has
        # reached the lower probe first this correctly stops accusing anything.
        return leak_probe_fault_from(
            self._warn_debounce.latched, self._flood_debounce.latched, self._warn_wet_at_boot, self._flood_wet_at_boot
        )

    def read_voltage(self) -> float | None:
        # None when the INA219 is not answering. It used to return _c_voltage
        # regardless, which was 0.0 before the chip had ever spoken (defended as
        # "bands critical, and a missing pack voltage should nag") — but the same
        # line handed back a FROZEN 7.9 V once the chip had spoken and then
        # stopped, and that does not nag, it reassures. A pack that stopped being
        # measured has to be distinguishable from a pack that is fine.
        return self._c_voltage if self._answering("ina219") else None

    def read_current_a(self) -> float | None:
        # Same chip as the voltage, so the same gate: amps and volts die
        # together on a dead INA219 and reporting one live while the other is
        # blank would describe a failure the hardware cannot have.
        return self._c_current if self._answering("ina219") else None

    def link_quality(self) -> int:
        # -1 (tether down) when nothing is sampling the carrier file any more. The
        # int has no null to spend, and of the values it can carry only -1 is not
        # a claim that the link is up — a frozen 4 says "full bars" about a check
        # that stopped running, and the operator reads bars as proof the vehicle
        # is still talking. -1 is also exactly what _link_tick() itself reports
        # when it cannot read the carrier, so an unreadable link and an unread one
        # land on the same honest answer.
        return self._c_link if self._answering("sensor-thread") else -1

    def sensor_faults(self) -> tuple[str, ...]:
        """What is not answering right now, e.g. ("ms5837", "ina219").

        Two sources, unioned, and both are needed:
          * `_health`, the per-device liveness verdict — this is what the readbacks
            gate on, so a name here and a None on the corresponding gauge are the
            SAME decision read twice. They cannot drift apart and contradict each
            other on screen. Three I2C chips, plus the two things that are not
            chips and can stop anyway: "leak-probes" (GPIO) and "sensor-thread"
            (the loop that samples all of them).
          * `_faults`, the latched subsystem faults that are not a device failing
            to answer: the I2C bus that would not open at all, and the pair of
            ballast limit switches that read impossibly.

        It reaches telemetry now (Telemetry.sensor_faults), so a blank gauge
        arrives with the name of the box to go and look at. It is still what a
        pre-dive check should refuse to arm on.
        """
        now = time.monotonic()
        dead = {key for key, h in self._health.items() if h.faulted(now)}
        return tuple(sorted(self._faults | dead))

    # ---- interrupt handlers (gpiozero edge threads) -----------------------
    def _on_paddle_pulse(self) -> None:
        self._paddle.pulse(time.monotonic())

    def _on_spool_edge(self) -> None:
        # Read BOTH channels on every edge: the decoder needs the full A/B state,
        # not the pin that happened to move. Note that pull_up=True inverts both
        # `.value`s together, and complementing both bits of a Gray code maps the
        # cycle onto itself — the phase shifts, the direction does not, so this
        # is correct either way round.
        # Belt and braces: with no pulse group there is nothing to have registered
        # this callback, so reaching it should be impossible — but an edge thread
        # already in flight when a group is torn down would find the pins gone,
        # and a raise on a gpiozero callback thread is silent.
        if self._spool_a is None or self._spool_b is None:
            return
        self._spool.update(bool(self._spool_a.value), bool(self._spool_b.value))

    @staticmethod
    def _is_wet(probe) -> bool:
        # Internal pull-up, water bridges the interleaved wires to ground: WET is
        # the pin pulled LOW. gpiozero's pull_up=True already reports that low as
        # the ACTIVE state, so `.value` is 1 when wet.
        return bool(probe.value)

    @staticmethod
    def _is_triggered(limit) -> bool:
        # NC-to-ground: un-triggered the switch holds the pin low, so gpiozero
        # (pull_up=True, active-low) reports it ACTIVE while everything is fine.
        # Triggered — or cut, or unplugged — the pin floats high and the device
        # goes INACTIVE. Hence the inversion: no switch reads as a stop.
        return not bool(limit.value)

    # ---- stepper thread ---------------------------------------------------
    def _stepper_loop(self) -> None:
        """Walk the ballast axis at a bounded step rate.

        Python is a poor step generator, and 400 steps/s was chosen partly
        because it is a rate a GIL-scheduled thread can hold without visibly
        stuttering. If the syringe ever needs to move faster than this, the
        answer is a hardware step generator (pigpio waveforms), not a tighter
        loop — a Python loop that misses its deadline does not slow the plunger
        down, it loses steps, and a lost step on an open-loop axis is the
        reported level quietly drifting away from where the plunger actually is.
        """
        # NO AXIS, NO LOOP. The thread still exists and still exits cleanly on
        # _stop; it simply never pretends to drive a driver that is not there. The
        # "ballast" fault latched in __init__ is what the console shows, and
        # get_ballast_level() answers from the axis, which stays where it was
        # rather than reporting a plunger travelling on command.
        if not self._have.get("ballast"):
            log.warning(
                "ballast axis not wired — the stepper thread is idle. The syringe "
                "cannot be driven and its reported level will not move."
            )
            self._stop.wait()
            return
        period = 1.0 / max(1.0, settings.ballast_step_rate)
        while not self._stop.is_set():
            direction = -1 if self._homing else {"fill": +1, "empty": -1}.get(self._ballast_cmd, 0)
            if direction == 0:
                self._stop.wait(0.02)  # nothing to do; do not spin the CPU
                continue
            # The limit switches are read HERE, once per step, in the same thread
            # that owns the counter. That is why the hard rule holds mid-command:
            # there is no window in which a step is taken and its bookkeeping is
            # not, and no interrupt can zero the count between the two.
            at_empty = self._is_triggered(self._limit_empty)
            at_full = self._is_triggered(self._limit_full)
            if at_empty and at_full:
                # Physically impossible: the plunger cannot be at both ends of
                # its own stroke. So this is a wiring fault — an unplugged
                # connector carrying both leads, or a lost common ground, both of
                # which read as "triggered" by design. Refuse to move AND refuse
                # to treat either switch as a position fix: homing on a phantom
                # switch would zero the counter wherever the plunger happens to
                # be, which is worse than not homing at all.
                self._fault(
                    "ballast-limits",
                    "BOTH ballast limit switches read triggered "
                    "— they cannot both be true. Check the switch wiring; the axis "
                    "is held and will not home until this clears.",
                )
                self._end_move()
                continue
            self._clear_fault("ballast-limits")
            if at_empty and direction < 0:
                self._axis.mark_empty_limit()
                self._end_move()
                continue
            if at_full and direction > 0:
                self._axis.mark_full_limit()
                self._end_move()
                continue
            self._en_pin.on()  # /EN low = driver enabled
            self._dir_pin.value = 1 if direction > 0 else 0
            if self._axis.try_step(direction, at_empty, at_full):
                self._step_pin.on()
                # The A4988 only needs a 1 µs pulse; a Python sleep overshoots
                # that by an order of magnitude and the driver does not care.
                time.sleep(2e-6)
                self._step_pin.off()
            self._stop.wait(period)

    def _end_move(self) -> None:
        """Stop stepping. The driver stays ENABLED, deliberately.

        Cutting /EN would let the motor freewheel, and a syringe plunger with
        water pressure behind it can back-drive a de-energised stepper. The
        counter would not know, and the level would be wrong with nothing to
        indicate it. Holding current costs the pack a few hundred mA; a silently
        invalid ballast reading costs the sub.
        """
        self._homing = False
        self._ballast_cmd = "hold"

    # ---- sensor thread ----------------------------------------------------
    def _sensor_loop(self) -> None:
        """The ONLY place I2C is touched, and the only writer of the _c_* cache.

        One thread, not three: the BNO085, MS5837 and INA219 share a bus, and
        interleaving repeated-start transactions from separate threads is how
        that bus starts returning coherent-looking rubbish. Rates are divided
        down from one 50 Hz tick.

        THE ORDER AND THE TRY-BLOCKS BELOW ARE A SAFETY PROPERTY, not tidiness.
        There used to be ONE try around the whole tick, so anything that raised
        early — and the maths after _imu_tick's own guard can raise on a driver
        that returns a None quaternion — skipped everything after it. The leak
        probes were last but one. That is unrelated hardware on an unrelated bus
        (two GPIO pins) being silenced by an I2C fault, and it silenced them
        WITHOUT A TRACE: read_leak() kept answering "NORMAL" from debouncers
        nobody was sampling. So the GPIO work is sampled FIRST, in its OWN
        try-block, and a bus failure cannot reach it.
        """
        period = 1.0 / self.SENSOR_HZ
        next_t = time.monotonic()
        tick = 0
        while not self._stop.is_set():
            now = time.monotonic()
            # HEARTBEAT, taken first and unconditionally, because it answers one
            # question only: is this loop still going round. It is what
            # read_water_speed() and link_quality() gate on — neither has a chip
            # of its own to blame, and both hand back a cache this loop is the
            # only writer of. A tick that fails is NOT a loop that stopped, so
            # this is deliberately outside every try below; the failures inside
            # them are reported by their own device's health.
            self._device_ok("sensor-thread", now)
            # --- GPIO, on no bus at all, and therefore isolated from the bus ---
            try:
                if tick % 5 == 0:
                    self._leak_tick(now)  # 10 Hz, per the debounce spec
            except Exception as exc:  # noqa: BLE001
                # A probe pin that will not read is a fault of ITS OWN — named, and
                # after fail_streak in a row read_leak() says UNKNOWN instead of
                # claiming a dry hull it has no evidence for.
                self._device_failed(
                    "leak-probes",
                    "leak probe sampling failed (%s) — the "
                    "hull state goes to cannot-tell rather "
                    "than holding NORMAL, which is a claim "
                    "nobody is checking",
                    exc,
                )
            try:
                # The paddlewheel is counted on gpiozero's edge threads and merely
                # totted up here. Its own try, above the bus work: an I2C fault has
                # no business freezing the speed the hull is making, and a failure
                # here must not be charged to the leak probes — blanking the hull
                # state for a reason that has nothing to do with it is how a
                # cannot-tell starts flickering, and a flickering cannot-tell is
                # one the operator learns to ignore.
                self._c_speed = self._paddle.read(now)
            except Exception as exc:  # noqa: BLE001
                log.warning("paddlewheel window failed: %s", exc)
            # --- I2C: one bad tick must not end the thread ---
            try:
                self._imu_tick(now)  # 50 Hz
                self._pressure_tick(now)  # state machine → ~10 Hz
                if tick % 25 == 0:
                    self._power_tick(now)  # 2 Hz
                    self._link_tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("sensor tick failed: %s", exc)
            tick += 1
            next_t += period
            self._stop.wait(max(0.0, next_t - time.monotonic()))

    def _log_fault(self, key: str, msg: str, *args) -> None:
        """Say a subsystem is broken, at most once a minute.

        Rate-limited because a dead device on a 50 Hz thread produces three
        thousand identical lines a minute and buries everything that mattered.
        """
        now = time.monotonic()
        if now - self._fault_logged.get(key, -1e9) > 60.0:
            self._fault_logged[key] = now
            log.error(msg, *args)

    def _fault(self, key: str, msg: str, *args) -> None:
        """Latch a SUBSYSTEM fault — the bus, the limit switches — and log it.

        For anything with LIVENESS — the three I2C chips, the leak probes, the
        sensor loop — use _device_failed/_device_ok instead. Those keys live in
        `_health` and nowhere else, deliberately: liveness has to be ONE verdict,
        or sensor_faults() can name a device the readbacks are still happily
        answering for and the operator gets a warning next to a number that looks
        fine.
        """
        self._faults.add(key)
        self._log_fault(key, msg, *args)

    def _clear_fault(self, key: str) -> None:
        if key in self._faults:
            self._faults.discard(key)
            log.info("%s is answering again", key)

    def _device_ok(self, key: str, now: float) -> None:
        """This chip just answered: the streak resets and the window restarts."""
        h = self._health[key]
        # Reported before the state is updated, and only for a device that had
        # actually worked before — otherwise the first good read of every boot
        # announces a recovery from a failure that never happened.
        if h.answered_ever() and h.faulted(now):
            log.info("%s is answering again", key)
        h.ok(now)

    def _device_failed(self, key: str, msg: str, *args) -> None:
        """One attempt on this chip raised.

        The LOG line goes out on the first raise — a bus that NAKs once an hour
        is worth knowing about, and the rate limiter keeps it from becoming
        noise. The FAULT is a separate question, answered by DeviceHealth: a
        single transient must not blank a working gauge, because a cannot-tell
        that flickers is a cannot-tell the operator learns to ignore.
        """
        self._health[key].failed()
        self._log_fault(key, msg, *args)

    # ---- BNO085 -----------------------------------------------------------
    def _open_imu(self) -> None:
        """BNO085 on 0x4A: fused yaw, gyro, linear acceleration, mag cal status.

        This one needs a driver rather than smbus2 register pokes: the BNO085
        speaks SHTP, a framed protocol with multi-hundred-byte sensor reports,
        not a register map. Reimplementing that here would be writing a driver,
        not wiring one up. See requirements.txt — the packages are Pi-only.
        """
        try:
            import board
            import busio
            from adafruit_bno08x import (
                BNO_REPORT_GYROSCOPE,
                BNO_REPORT_LINEAR_ACCELERATION,
                BNO_REPORT_ROTATION_VECTOR,
            )
            from adafruit_bno08x.i2c import BNO08X_I2C

            # Two libraries, one bus: Blinka opens /dev/i2c-1 for the IMU and
            # smbus2 opens it for the other two chips. That is safe ONLY because
            # every transaction on both handles is issued from the single sensor
            # thread — the kernel serialises individual messages, but a
            # repeated-start sequence interleaved from two threads would not
            # survive, and the symptom would be plausible wrong numbers.
            i2c = busio.I2C(board.SCL, board.SDA)
            imu = BNO08X_I2C(i2c, address=self.ADDR_BNO085)
            imu.enable_feature(BNO_REPORT_ROTATION_VECTOR)  # mag-fused quaternion
            imu.enable_feature(BNO_REPORT_GYROSCOPE)  # immune to thruster fields
            imu.enable_feature(BNO_REPORT_LINEAR_ACCELERATION)  # gravity already removed
            self._imu = imu
            log.info("BNO085 online at 0x%02X", self.ADDR_BNO085)
        except Exception as exc:  # noqa: BLE001
            # No _fault() call: the chip has simply never answered, which
            # DeviceHealth already treats as faulted, so sensor_faults() names it
            # and heading / gyro / accel / mag_cal / attitude all read
            # cannot-tell. This only has to say so out loud.
            self._device_failed(
                "bno085",
                "BNO085 not responding (%s) — heading, gyro and "
                "attitude all read cannot-tell, and mag_cal ships "
                "as null (no compass), not as 0 (compass fitted "
                "and uncalibrated)",
                exc,
            )

    def _imu_tick(self, now: float) -> None:
        if self._imu is None:
            return
        try:
            qi, qj, qk, qr = self._imu.quaternion
            _gx, _gy, gz = self._imu.gyro
            ax, _ay, _az = self._imu.linear_acceleration
            cal = int(self._imu.calibration_status)
        except Exception as exc:  # noqa: BLE001
            # "Holding the last heading" is what this used to do and what it must
            # never do again: the cache below keeps its values, but _health now
            # decides whether anyone is allowed to read them, and after five
            # consecutive raises (0.1 s at 50 Hz) nobody is.
            self._device_failed(
                "bno085", "BNO085 read failed (%s) — the last heading is " "held in the cache but no longer served", exc
            )
            return
        self._device_ok("bno085", now)

        # ENU yaw counts COUNTER-CLOCKWISE from EAST; a compass counts CLOCKWISE
        # from NORTH. Two different zeros AND two different directions, so the
        # conversion is an offset *and* a flip: heading = (90 - yaw_enu) mod 360.
        # Getting only the offset right (yaw + 90) yields a heading that is
        # correct at north and mirrored everywhere else — the sub turns right, the
        # map turns left, and the track folds back on itself.
        yaw_enu = math.degrees(math.atan2(2.0 * (qr * qk + qi * qj), 1.0 - 2.0 * (qj * qj + qk * qk)))
        heading = (90.0 - yaw_enu) % 360.0
        # The mounting offset is applied AFTER the frame flip because it describes
        # how the board is rotated inside the hull, measured in compass degrees.
        self._c_heading = (heading + nav_settings.imu_yaw_offset_deg) % 360.0

        # Same handedness argument for the rate: the gyro's +Z is up and its
        # positive sense is counter-clockwise, the compass convention is
        # clockwise, so the sign flips. A gyro that disagrees in sign with the
        # heading it is meant to explain makes the complementary filter fight
        # itself — it looks like drift, and no amount of tuning fixes it.
        self._c_gyro_z = -math.degrees(gz)
        # Board +X points ahead. If the BNO ends up bolted in at 90°, this axis
        # must be re-picked here — NAV_IMU_YAW_OFFSET_DEG corrects the HEADING
        # only and cannot rotate an acceleration.
        self._c_accel_fwd = float(ax)
        # 0..3, the accuracy the BNO085 attaches to its own reports. It only
        # reaches 3 after the figure-of-eight dance (docs/hardware.md). If the
        # driver ever stops updating it, this stays 0 and everything downstream
        # treats the heading as suspect — the safe direction.
        self._c_mag_cal = max(0, min(3, cal))

        # ZYX Euler from the same quaternion. Sign discipline: with the board's
        # +X forward, +Y to port and +Z up, the Euler pitch about +Y is positive
        # NOSE DOWN, so it is negated to match the protocol's "+ = nose up".
        # Attitude is advisory (nothing safety-critical branches on it) — verify
        # by tipping the sub on the bench before believing the number.
        sin_p = max(-1.0, min(1.0, 2.0 * (qr * qj - qk * qi)))
        pitch = -math.degrees(math.asin(sin_p))
        roll = math.degrees(math.atan2(2.0 * (qr * qi + qj * qk), 1.0 - 2.0 * (qi * qi + qj * qj)))
        self._c_pitch_roll = (pitch, roll)

    # ---- MS5837-30BA ------------------------------------------------------
    def _open_i2c(self) -> None:
        try:
            import smbus2

            self._bus = smbus2.SMBus(self.I2C_BUS)
        except Exception as exc:  # noqa: BLE001
            self._fault(
                "i2c",
                "I2C bus %d would not open (%s) — depth and pack " "voltage are both unavailable",
                self.I2C_BUS,
                exc,
            )

    def _open_depth(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.write_byte(self.ADDR_MS5837, self._MS_RESET)
            time.sleep(0.02)  # the reset reloads the PROM
            prom = []
            for i in range(7):
                msb, lsb = self._bus.read_i2c_block_data(self.ADDR_MS5837, self._MS_PROM + 2 * i, 2)
                prom.append((msb << 8) | lsb)
            # The PROM CRC catches the failure a bus check does not: a mis-wired
            # or noisy I2C answers 0xFFFF to everything, and 0xFFFF coefficients
            # produce a smooth, plausible, completely wrong depth. It is not
            # sufficient on its own though — the nibble is read over the same bus
            # as the words it vouches for, and one whole class of dead bus forges
            # a match. _ms5837_prom_valid() is the CRC plus that gap closed.
            if not _ms5837_prom_valid(prom):
                raise OSError(
                    "PROM [%s] is not a factory calibration — the "
                    "coefficients are not trustworthy" % " ".join("%04X" % w for w in prom)
                )
            self._ms_prom = prom
            log.info("MS5837 online at 0x%02X (PROM accepted)", self.ADDR_MS5837)
        except Exception as exc:  # noqa: BLE001
            # _ms_prom stays None, so _pressure_tick() returns early forever and
            # the chip never records a successful read: DeviceHealth holds it
            # faulted, sensor_faults() keeps reporting "ms5837", and
            # read_pressure() answers None. A rejected PROM lands on exactly the
            # same path as a sensor that stopped mid-dive, which is right — the
            # coefficients are not trustworthy, so there is no depth here either.
            self._device_failed(
                "ms5837",
                "MS5837 depth sensor unusable (%s) — depth and "
                "pressure ship as null (cannot tell), never as a "
                "surface reading; see sensor_faults()",
                exc,
            )

    def _pressure_tick(self, now: float) -> None:
        """Advance the depth conversion state machine.

        At OSR 8192 each of the two conversions takes 17.2 ms, and sleeping
        through 40 ms of that in this thread would stall the IMU to under 25 Hz.
        So the conversion is a state machine: kick one off, come back a tick
        later (20 ms > 17.2 ms) and collect it. The full cycle lands at ~10 Hz,
        which is faster than the sub can change depth.
        """
        if self._bus is None or self._ms_prom is None or now < self._ms_next:
            return
        try:
            if self._ms_stage == 0:
                self._bus.write_byte(self.ADDR_MS5837, self._MS_CONVERT_D1)
                self._ms_stage, self._ms_next = 1, now + 0.020
            elif self._ms_stage == 1:
                self._ms_d1 = self._read_adc24()
                self._bus.write_byte(self.ADDR_MS5837, self._MS_CONVERT_D2)
                self._ms_stage, self._ms_next = 2, now + 0.020
            else:
                self._ms_d2 = self._read_adc24()
                mbar = _ms5837_mbar(self._ms_prom, self._ms_d1, self._ms_d2)
                # Stay on the PSI path: surface zeroing and psi_per_meter are
                # already configured in those units and the whole depth pipeline
                # (telemetry, nav, the dive log) is calibrated against them.
                self._c_pressure_psi = mbar * 0.0145037738
                self._ms_stage, self._ms_next = 0, now + 0.060
                # Only the COLLECT stage counts as the device answering. Stages 0
                # and 1 merely kick off conversions; a chip that accepts a
                # convert command and then never returns an ADC word is precisely
                # the silent freeze the window exists to catch, and marking it
                # healthy for starting a conversion would defeat that.
                self._device_ok("ms5837", now)
        except Exception as exc:  # noqa: BLE001
            self._device_failed(
                "ms5837",
                "MS5837 read failed (%s) — depth goes to " "cannot-tell rather than holding its last " "metres",
                exc,
            )
            self._ms_stage, self._ms_next = 0, now + 1.0

    def _read_adc24(self) -> int:
        b = self._bus.read_i2c_block_data(self.ADDR_MS5837, self._MS_ADC_READ, 3)
        return (b[0] << 16) | (b[1] << 8) | b[2]

    # ---- INA219 -----------------------------------------------------------
    def _open_power(self) -> None:
        if self._bus is None:
            return
        try:
            # 32 V bus range, 320 mV shunt gain, 12-bit continuous on both
            # channels. Only the bus and shunt voltage registers are used, so the
            # calibration register is deliberately left alone (see _power_tick).
            self._bus.write_i2c_block_data(self.ADDR_INA219, 0x00, [0x39, 0x9F])
            log.info("INA219 online at 0x%02X", self.ADDR_INA219)
        except Exception as exc:  # noqa: BLE001
            self._device_failed(
                "ina219",
                "INA219 not responding (%s) — pack voltage and "
                "current both ship as null; the console shows no "
                "pack reading rather than a number nobody took",
                exc,
            )

    def _power_tick(self, now: float) -> None:
        if self._bus is None:
            return
        try:
            raw_bus = self._read_reg16(self.ADDR_INA219, 0x02)
            raw_shunt = self._read_reg16(self.ADDR_INA219, 0x01)
        except Exception as exc:  # noqa: BLE001
            self._device_failed(
                "ina219",
                "INA219 read failed (%s) — pack voltage goes to "
                "cannot-tell rather than holding the last volts "
                "it managed to measure",
                exc,
            )
            return
        self._device_ok("ina219", now)
        # Bus voltage register: 13 bits, 4 mV/LSB, left-aligned above the flags.
        # With the shunt high-side (between the fuse and everything else) this is
        # the pack voltage less the few millivolts across the shunt.
        self._c_voltage = (raw_bus >> 3) * 0.004
        # Current from the SHUNT VOLTAGE (10 µV/LSB, signed) over the shunt
        # resistance, not from the chip's current register. That register needs a
        # calibration word written to it, and if the word is ever lost — a
        # brown-out, a reset nobody noticed — the chip returns 0 A forever, which
        # reads as "nothing is drawing power" on a vehicle that is very much
        # drawing power. This path has no state to lose.
        shunt_v = _twos16(raw_shunt) * 1e-5
        self._c_current = round(shunt_v / self.INA219_SHUNT_OHMS, 3)

    def _read_reg16(self, addr: int, reg: int) -> int:
        msb, lsb = self._bus.read_i2c_block_data(addr, reg, 2)
        return (msb << 8) | lsb

    # ---- leak + link ------------------------------------------------------
    def _leak_tick(self, now: float) -> None:
        # NO PROBES, NO REASSURANCE — and specifically, no _device_ok below. That
        # omission is the whole mechanism: "leak-probes" stays faulted, read_leak()
        # returns UNKNOWN rather than NORMAL, and the console shows a hull nobody
        # is watching instead of a hull certified dry. Returning early WITH a
        # _device_ok would be this file's oldest bug rebuilt on purpose — a
        # sampler that certifies itself for work it did not do.
        if not self._have.get("leak"):
            return
        # Called from its OWN try-block at the top of the sensor tick, ahead of
        # every bus operation — these are two GPIO pins and the chips are on I2C,
        # unrelated hardware that has to be able to fail independently. Sampling
        # them last, inside the bus's try, is what let one I2C raise stop leak
        # detection dead while read_leak() kept answering NORMAL.
        #
        # Sampled at 10 Hz so that the configured debounce count is the ~0.5 s the
        # config comment claims. WARN and FLOOD debounce INDEPENDENTLY: they are
        # two probes at two heights answering two different questions, and
        # chaining them would make a flood conditional on the warn probe still
        # working — which is exactly the probe most likely to be underwater and
        # corroded.
        self._warn_debounce.sample(self._is_wet(self._leak_warn_in))
        self._flood_debounce.sample(self._is_wet(self._leak_flood_in))
        # HERE, not at the call site, and for the same reason _pressure_tick marks
        # itself only on the COLLECT stage: the liveness has to be attached to the
        # work, not to the call. Marked from the loop, a _leak_tick that returned
        # without reading anything — the third shape DeviceHealth names, a driver
        # that returns without writing — would keep certifying itself healthy and
        # read_leak() would go on saying NORMAL. Both probes are sampled above
        # before this line is reached, so reaching it IS the evidence.
        self._device_ok("leak-probes", now)

    def _link_tick(self) -> None:
        # An Ethernet tether has no signal strength — it is a wire. Rather than
        # invent bars, this reports full while the carrier is up and -1 (tether
        # down) when it is not; the client already draws -1 distinctly.
        try:
            with open(self._carrier_path, "r") as fh:
                self._c_link = 4 if fh.read().strip() == "1" else -1
        except Exception:  # noqa: BLE001 — no such interface = no tether
            self._c_link = -1

    # ---- lifecycle --------------------------------------------------------
    def close(self) -> None:
        self._stop.set()
        for t in (getattr(self, "_sensor_thread", None), getattr(self, "_stepper_thread", None)):
            if t is not None:
                t.join(timeout=1.0)
        try:
            self.set_armed(False)
            for dev in self._light_dev.values():
                dev.value = 0.0
            # Only now is it safe to release the stepper: nothing is going to ask
            # it to move again, and a de-energised driver on a shut-down vehicle
            # is one less thing drawing from the pack.
            #
            # Absent on a part-built vehicle, and absent is not a failure to quiet
            # it. Without this test the warning below fires on every clean shutdown
            # saying the outputs may still be live, which is both false and exactly
            # the kind of routine alarm that gets read past on the day it is real.
            if self._en_pin is not None:
                self._en_pin.off()
        except Exception as exc:  # noqa: BLE001
            log.warning("shutdown: could not fully quiet the outputs (%s)", exc)
        for dev in (
            getattr(self, "_paddle_in", None),
            getattr(self, "_spool_a", None),
            getattr(self, "_spool_b", None),
            getattr(self, "_limit_empty", None),
            getattr(self, "_limit_full", None),
            getattr(self, "_leak_warn_in", None),
            getattr(self, "_leak_flood_in", None),
        ):
            try:
                if dev is not None:
                    dev.close()
            except Exception:  # noqa: BLE001
                pass
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# MS5837 maths — module level so the compensation can be tested against the
# datasheet's worked example without a sensor, which is the only way anyone is
# ever going to notice a transcription error in it.
# ---------------------------------------------------------------------------
def _ms5837_crc4(prom: list[int]) -> int:
    """The 4-bit CRC the MS5837 stores in the top nibble of PROM word 0."""
    words = list(prom) + [0] * (8 - len(prom))
    words[0] &= 0x0FFF  # the CRC nibble itself is not covered
    rem = 0
    for i in range(16):
        rem ^= (words[i >> 1] & 0x00FF) if i % 2 else (words[i >> 1] >> 8)
        for _ in range(8):
            rem = ((rem << 1) ^ 0x3000) if rem & 0x8000 else (rem << 1)
            rem &= 0xFFFF
    return (rem >> 12) & 0x0F


def _ms5837_prom_valid(prom: list[int]) -> bool:
    """Is this a factory calibration, or a bus that is not really answering?

    The CRC nibble cannot settle that by itself, because the nibble is read over
    the very bus it is being used to vouch for. An I2C line held low — a shorted
    SDA, a connector with no pull-ups, a part with no power — reads 0x0000 for
    every register, and an all-zeros PROM CRCs to 0x0, which MATCHES the 0x0
    nibble sitting in word 0. The sensor then came up "online", recorded no
    fault, and zero coefficients compensated to 0 mbar, which rov.py clamps to a
    depth of exactly 0.00 m. A sub reporting "at the surface" all the way to the
    bottom of a canal, on a vehicle flagged mock:false, is the single most
    dangerous wrong reading this system can produce — so the shapes a dead bus
    makes are rejected BEFORE the CRC gets a vote, whatever the nibble says.
    """
    if len(prom) != 7:
        return False
    # Every word identical is one value on a wire, not seven coefficients from a
    # part. Covers 0x0000 (held low), 0xFFFF (nothing driving it) and any other
    # single byte a stuck bus happens to repeat — none of which is a sensor.
    if all(w == prom[0] for w in prom):
        return False
    # C1..C6 are mid-scale factory constants (the datasheet's worked example has
    # every one in the tens of thousands) and _ms5837_mbar multiplies all six
    # into the pressure. A rail value in any of them is a partially stuck bus,
    # not a part that shipped that way.
    if any(w in (0x0000, 0xFFFF) for w in prom[1:7]):
        return False
    return _ms5837_crc4(prom) == (prom[0] >> 12)


def _ms5837_mbar(prom: list[int], d1: int, d2: int) -> float:
    """Datasheet compensation for the MS5837-30BA, including 2nd order terms.

    The second-order block is not optional decoration: without it the reading
    drifts by tens of millibars across the temperature range a canal gives you
    between a sunlit surface and the bottom, which is centimetres of phantom
    depth change appearing exactly when the sub descends.
    """
    c1, c2, c3, c4, c5, c6 = prom[1], prom[2], prom[3], prom[4], prom[5], prom[6]
    dt = d2 - c5 * 256
    temp = 2000 + dt * c6 / 8388608
    off = c2 * 65536 + (c4 * dt) / 128
    sens = c1 * 32768 + (c3 * dt) / 256
    if temp < 2000:  # cold water
        ti = 3 * dt * dt / 8589934592
        offi = 3 * (temp - 2000) ** 2 / 2
        sensi = 5 * (temp - 2000) ** 2 / 8
        if temp < -1500:
            offi += 7 * (temp + 1500) ** 2
            sensi += 4 * (temp + 1500) ** 2
    else:
        ti = 2 * dt * dt / 137438953472
        offi = (temp - 2000) ** 2 / 16
        sensi = 0.0
    off -= offi
    sens -= sensi
    _ = (temp - ti) / 100.0  # water temperature, °C — not plumbed anywhere yet
    return ((d1 * sens / 2097152 - off) / 8192) / 10.0


def _twos16(v: int) -> int:
    return v - 65536 if v & 0x8000 else v


def get_hardware() -> HardwareBase:
    choice = settings.hardware_backend.lower()
    if choice == "mock":
        return MockHardware()
    if choice in ("auto", "real"):
        try:
            return RealHardware()
        except Exception as exc:  # noqa: BLE001 — any init failure → bench mock in auto
            if choice == "real":
                raise
            log.warning("RealHardware init failed (%s); using MockHardware", exc)
            return MockHardware()
    log.warning("unknown NEPTUNE_HW=%r; using MockHardware", choice)
    return MockHardware()

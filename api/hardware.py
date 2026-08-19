"""Hardware abstraction layer — GPIO / I2C live behind this interface.

Two backends:
  * MockHardware — a self-contained bench simulator. Fully working with no
    hardware attached, so the whole server (and the client) can be exercised on
    a laptop. `is_mock` is True → telemetry carries `mock: true`. Its internal
    vehicle model still speaks the syringe-era mechanism (an axis with end
    stops) — interface-identical to the pump at every readback, and its full
    modernisation rides ledger row 4 (docs/hardware.md §20).
  * RealHardware — the vehicle as bought (docs/hardware.md §8): two DRV8871
    thruster pairs on the Pi's own pins, and EVERYTHING else behind the ESP32
    brainstem on USB serial (api/brainstem.py; firmware/brainstem/ is the far
    end). Either half may be missing and the backend still constructs, names
    the missing half, and tells the truth about it — a laptop with only the
    breadboard ESP32 lights the whole console. Every hardware import (gpiozero,
    pyserial) is lazy and inside the class, because this file is edited on a
    machine with neither.

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


# WHAT GOES DARK WHEN EACH PART STOPS ANSWERING, in the operator's words rather
# than the chip's. A log line that says "ms5837 stopped answering" is a fact about
# a part number; the line an operator can act on says which GAUGES just went
# blank, because that is what they are looking at when they reach for the log. The
# keys are exactly the DeviceHealth keys, which are exactly the names
# sensor_faults() puts on the wire — one vocabulary, so a line in the LOGS overlay
# and a chip named on the alert rail can be matched by eye without a decoder ring.
DEVICE_READINGS = {
    "bno085": "heading, turn rate, forward acceleration, pitch/roll and mag_cal",
    "ms5837": "depth and pressure",
    "ina219": "pack voltage and current",
    "ina219-rail": "the thruster rail's own volts and amps (the fouled-prop witness)",
    "leak-probes": f"the hull state (NORMAL becomes {LEAK_UNKNOWN} — nobody is checking it)",
    "sensor-thread": "water speed and the tether link, and nothing refreshes the chips above",
    # THE BUS-FRONT, one level up: the serial link to the ESP32 that carries
    # every chip above. Link down ⇒ all of them are cannot-tell under this one
    # name — naming the chips too would claim knowledge nobody has.
    "brainstem": "every sensor reading on the vehicle — the ESP32 link carries them all",
}


def liveness_edge(
    key: str, health: DeviceHealth, was_live: bool, announced: bool, now: float
) -> tuple[str, str] | None:
    """Has this instrument just CHANGED state, and what should the log say?

    Returns ("info"|"warning", sentence), or None when nothing changed. Pure, and
    the clock is injected — same rule and the same reason as DeviceHealth itself
    (§24.4): logic that can only be reached by waiting on a real dying sensor on a
    real Pi is logic nobody runs, and this is the code that decides whether a
    failure gets written down at all.

    FOUR TRANSITIONS, THREE OF WHICH HAD NO LINE ANYWHERE ON THIS VEHICLE:

      * FIRST GOOD READ — the moment an instrument becomes real. Nothing said this.
        On a vehicle being fitted one sensor at a time it is the most useful line
        in the boot: it is the difference between "the wiring is right" and "the
        console is being polite about nothing".
      * STOPPED ANSWERING — including, and especially, WITHOUT RAISING.
        `_device_failed` fires on a raise; the frozen MS5837 this whole module is
        written around raised nothing at all for the rest of the dive.
      * ANSWERING AGAIN — a reseated connector. Recovery is half the contract.
      * NEVER ANSWERED — which is NOT a transition and deliberately says nothing.
        The boot line names those once; repeating it would drown the other three on
        exactly the half-built vehicle that needs them most.

    `announced` is what separates a first arrival from the third reseat of a bad
    connector: `answered_ever()` stays true forever once it flips, so on its own it
    cannot tell those apart — and they are different lines with different meanings.
    """
    live = not health.faulted(now)
    if live == was_live:
        return None
    what = DEVICE_READINGS.get(key, "the readings taken from it")
    if live:
        if not announced:
            return ("info", f"{key} ANSWERED for the first time this power-cycle — {what} are live")
        return ("info", f"{key} is ANSWERING AGAIN — {what} can be believed once more")
    # WHY it stopped, because the two causes send an operator to different places:
    # a streak of raises is a bus answering badly (loom, connector, address clash),
    # and silence with no raise is a driver or a thread that stopped without
    # complaining. Naming which one it was is the difference between a line that
    # starts an errand and a line that only records a disappointment.
    why = (
        f"{health.fails} consecutive raises"
        if health.fails >= health.fail_streak
        else f"no good read for {now - (health.last_ok or now):.1f}s"
    )
    return ("warning", f"{key} STOPPED ANSWERING ({why}) — {what} now read cannot-tell")


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

    def sensors_absent(self) -> tuple[str, ...]:
        """Which of sensor_faults() have NEVER answered on this hull, e.g. ("bno085",).

        A STRICT SUBSET OF sensor_faults(), and the distinction it carries is the
        difference between two errands: a part that answered this power cycle and
        stopped is a thing to go and look at; a part that has never answered at
        all is, on a vehicle being built one instrument at a time, simply not
        fitted yet. Both are cannot-tell for the READING — neither may ever put a
        number on screen — but only one of them is a fault.

        THE FAILURE THIS EXISTS FOR. `DeviceHealth` has always known the
        difference (`answered_ever()`), and it stayed inside the hardware layer:
        everything above it saw one undifferentiated null plus one name in
        sensor_faults, so the console described every unfitted instrument as a
        part that broke. On a hull with no IMU wired the bearing read "the
        compass answered earlier in this dive and has now stopped" — an accusation
        about a chip that was never in the boat, and an errand nobody can run.
        docs/playbook.md's state ladder calls that state ABSENT and says the
        readout "does not accuse anyone"; this is how the vehicle says which one
        it is, because the vehicle is the only layer that knows.

        AN EMPTY TUPLE MEANS "THIS BACKEND CANNOT TELL THEM APART", not "nothing
        is absent" — and that default is deliberately the LOUD one. Reporting a
        fault as an absence would silence a real failure; reporting an absence as
        a fault only sends somebody to look at a connector that is not there. The
        first is a lie about the hull, the second is a wasted walk, so the
        unknowable case takes the second.
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
        # 3S Li-ion: 12.6 V full, 11.1 V nominal — the pack actually fitted
        # (3S3P INR18650, docs/hardware.md §7). 12.4 V is a healthy pack an
        # hour off the charger. THE 2S SCALE IS GONE the same way the 24 V one
        # went before it: a threshold that describes a different vehicle does
        # not fail loudly, it reads "full" forever (R7.4.1).
        self._voltage = 12.4
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
        # --- chips this hull HAS NEVER HAD --------------------------------------
        # A STRICT SUBSET OF _dead, and the reason it is a second set rather than a
        # third state is that everything above the hardware layer must treat the two
        # identically for the READING: an unfitted chip answers exactly the nothing a
        # dead one does, so every readback gate below stays `in self._dead` and cannot
        # be got wrong per-sensor. What differs is only what the vehicle SAYS about
        # it, and sensors_absent() is where that is said. See _unfit_sensor().
        self._unfitted: set[str] = set()
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

    def sensors_absent(self) -> tuple[str, ...]:
        # Which of those have NEVER answered on this hull. Empty on a healthy bench
        # AND on a bench where something was killed mid-run — the bench's chips were
        # all answering at power-on, so a kill is always a part that stopped. Only
        # _unfit_sensor() puts a name here, because only it describes a vehicle that
        # never had the part in the first place.
        return tuple(sorted(self._unfitted))

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
        # 3.0 V/cell. Rates are the 2S model's ×1.5 (three cells sag three
        # cells' worth): from 12.4 V the amber band arrives in about an hour
        # of loitering, which is roughly what a real pack does.
        load = (abs(self._left) + abs(self._right)) / 2.0
        self._voltage = max(settings.battery_floor_v, self._voltage - (0.0006 + 0.00225 * load) * dt)

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
        elif "leak-probes" in self._stalled:
            # SAID OUT LOUD, like the stall was. Recovery used to be the silent half
            # of this hook, and a log that records a subsystem stopping and never
            # records it starting again cannot be read backwards: an operator
            # scrolling the LOGS overlay would find the stall and have no way to tell
            # whether it is still standing. Guarded on the set so an un-stall of
            # something that was never stalled says nothing.
            self._stalled.discard("leak-probes")
            log.info("MOCK: leak probe sampling resumed — the hull state can be measured again")

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
        elif "sensor-thread" in self._stalled:
            # Same reason as the leak sampler above: a stall that is logged and a
            # recovery that is not leaves the log claiming a fault that has gone.
            self._stalled.discard("sensor-thread")
            log.info("MOCK: sensor loop running again — water speed and the link are being sampled")

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

    def _unfit_sensor(self, device: str) -> None:
        """This hull HAS NEVER HAD this chip: it is not fitted, and never was.

        THE STATE MOST OF THIS VEHICLE IS IN, and the one the bench could not
        produce. The owner is fitting instruments one at a time, so for weeks at a
        stretch the normal condition of most of them is "not wired yet" — and
        without this hook the only shape of absence the bench could make was
        _kill_sensor(), which describes a part that ANSWERED and STOPPED. Every
        check written on the bench therefore described the vehicle being built as
        a vehicle breaking, and so did the console: the bearing on a hull with no
        IMU read "the compass answered earlier in this dive and has now stopped".

        The readings are identical to _kill_sensor's by construction — the device
        is added to `_dead` as well, so every readback gate stays one membership
        test and no sensor can be got half-right. What changes is the SENTENCE the
        vehicle offers for the null: the name appears in sensors_absent() as well
        as sensor_faults(), and the console renders ABSENT (no fault chip, no
        accusation) rather than cannot-tell. See docs/playbook.md §1.

        Fitting it later is _revive_sensor(): a chip that has been screwed in and
        answers is not absent and not dead, which is the same recovery contract
        the kill hook has.

        Usage:
            hw._unfit_sensor("bno085")    # this boat has no compass yet
            assert hw.read_heading() is None
            assert hw.sensor_faults() == ("bno085",)
            assert hw.sensors_absent() == ("bno085",)
        """
        if device not in self.DEVICES:
            raise ValueError(
                f"_unfit_sensor({device!r}): unknown device. Fittable chips are "
                f"{', '.join(self.DEVICES)} — the same names RealHardware faults "
                f"under and sensor_faults() reports."
            )
        self._dead.add(device)
        self._unfitted.add(device)
        log.warning(
            "MOCK: %s is not fitted on this hull — it answers cannot-tell, and "
            "the vehicle reports it as absent rather than as a part that stopped",
            device,
        )

    def _revive_sensor(self, device: str) -> None:
        """The chip answers again — a reseated connector, a bus that recovered.

        Recovery is half the contract and it is the half that gets skipped: a
        depth readout that goes blank and STAYS blank after the sensor comes back
        is its own fault, and one nobody would notice until a dive. The mock's
        readings resume from the CURRENT simulated truth, never from the value
        that was frozen when it died.

        IT ALSO FITS AN ABSENT CHIP — the connector that was never there is now
        there and answering. A part that has answered is neither dead nor absent,
        so both memberships go together; leaving the name in `_unfitted` would
        have the vehicle go on calling a working instrument "not fitted", which is
        the recovery half of this contract failing in the quieter direction.
        """
        if device not in self.DEVICES:
            raise ValueError(
                f"_revive_sensor({device!r}): unknown device. Killable chips are " f"{', '.join(self.DEVICES)}."
            )
        self._dead.discard(device)
        self._unfitted.discard(device)
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
# Real vehicle backend — DRV8871 thrusters on the Pi's own pins, EVERYTHING
# else behind the ESP32 brainstem on USB serial (docs/hardware.md §8)
# ---------------------------------------------------------------------------
def drv8871_duty(v: float, deadband: float) -> tuple[float, float]:
    """-1..1 → (IN1 duty, IN2 duty) for one DRV8871. Pure: testable off-Pi.

    The DRV8871 has no EN pin: PWM on IN1 with IN2 low drives forward, PWM on
    IN2 with IN1 low drives reverse, both low coasts. Below the deadband both
    go to zero — a few percent of duty cannot turn a prop but does make the
    driver sing, and a whining idle sounds exactly like a fault to whoever is
    holding the tether.
    """
    v = max(-1.0, min(1.0, float(v)))
    if abs(v) < deadband:
        return (0.0, 0.0)
    if v > 0:
        return (v, 0.0)
    return (0.0, -v)


class RealHardware(HardwareBase):
    """The vehicle as bought: a commander/brainstem split (docs/hardware.md §8).

    THE PI'S GPIO FOOTPRINT IS FOUR PINS — the two DRV8871 pairs below. Every
    sensor, the pump, the lamps and the burn interlock live on the ESP32
    brainstem, reached over one USB serial cable (api/brainstem.py owns that
    cable and its protocol; firmware/brainstem/ is the other end).

    EITHER HALF MAY BE MISSING AND THE VEHICLE IS STILL REAL — a laptop with
    the breadboard ESP32 on USB has no GPIO, so the thruster group faults, is
    named, and arming is refused; a Pi with the loom but the brainstem cable
    out has thrusters and a console full of honest cannot-tells under the one
    name "brainstem". Only BOTH halves missing refuses to construct, and
    NEPTUNE_HW=auto then falls back to the announced bench simulator.

    LIVENESS moved one level up with the sensors. The serial link carries its
    own DeviceHealth (the bus-front: link down ⇒ every reading behind it is
    cannot-tell under one name, exactly as `i2c` fronted its chips); per-chip
    verdicts are computed AT the bus by the firmware, which nulls a dead chip's
    readings in the same frame that names it in `faults`. This class does not
    second-guess those verdicts — it gates everything on the link and passes
    the vehicle's own word through.

    THREADING: the asyncio event loop calls every public method; none blocks.
    BrainstemLink owns its reader thread and its snapshot is a single rebound
    reference (GIL-atomic), the same discipline the old sensor cache used.
    reset_leak_latches() is the one deliberate exception — it waits up to half
    a second for the vehicle's verdict, because its caller must relay WHY a
    re-arm was refused, and a human pressed that button.
    """

    is_mock = False

    # ---- pin map (BCM) — mirrored exactly in docs/hardware.md §8 ----------
    # DRV8871: IN1/IN2 only, no EN. PWM rides the direction pin itself
    # (drv8871_duty above). gpiozero's default factory bit-bangs PWM on any
    # pin; pigpio's DMA timing remains the jitter upgrade and needs no rewiring.
    PIN_THRUST_L_IN1 = 23  # port motor, ahead
    PIN_THRUST_L_IN2 = 24  # port motor, astern
    PIN_THRUST_R_IN1 = 5  # starboard motor, ahead
    PIN_THRUST_R_IN2 = 6  # starboard motor, astern

    # THE LEAK DEBOUNCE BUDGET'S DERIVATION, preserved. Leak sampling now
    # happens on the brainstem at 10 Hz (firmware LEAK_HZ; the same figure is
    # named in api/brainstem.py as LEAK_SAMPLE_HZ) — which is exactly the rate
    # the Pi used to derive as SENSOR_HZ / LEAK_SAMPLE_DIVIDER. Both constants
    # stay because the pin-to-console latency budget in tests/test_latency.py
    # is built from them, and a budget that loses its derivation goes stale in
    # silence. Changing the firmware's rate means changing these WITH it.
    SENSOR_HZ = 50.0
    LEAK_SAMPLE_DIVIDER = 5

    # How long __init__ listens for the brainstem's first frame before calling
    # the port junk. The firmware streams at 10 Hz unprompted, so 2.5 s is
    # twenty-five missed frames — an absent board, never a slow one.
    HELLO_WAIT_S = 2.5

    def __init__(self, link=None) -> None:
        # A human still has to say the harness exists; nothing here can see a
        # connector. With the flag off, both halves are refused and auto lands
        # on the honest bench simulator.
        if not settings.hardware_wired:
            raise RuntimeError(
                "RealHardware: NEPTUNE_HW_WIRED=false — a human has said there is "
                "no harness. Set NEPTUNE_HW=mock to silence this, or wire the vehicle."
            )
        self._armed = False
        self._lights: dict[str, tuple[bool, float]] = {"green": (False, 0.0), "white": (False, 0.0)}
        self._magnet = False
        self._faults: set[str] = set()
        self._fault_logged: dict[str, float] = {}
        self._have: dict[str, bool] = {}
        # Leak latches, mirrored STICKY on this side of the cable: wet outranks
        # cannot-tell, so a latch seen in any frame stands here even if the
        # link then dies. Cleared only when a frame shows the vehicle's own
        # latch cleared (leak_reset is the only thing that clears it there).
        self._leak_latched: set[str] = set()
        self._leak_rearms = 0

        # ---- thrusters: the Pi half ---------------------------------------
        # gpiozero is imported HERE, per group, so a machine with no GPIO
        # stack still constructs the backend and simply names this group as
        # not fitted — the breadboard-on-a-laptop case the split exists for.
        self._l_in1 = self._l_in2 = self._r_in1 = self._r_in2 = None
        if self._gpio_available():
            try:
                from gpiozero import PWMOutputDevice

                # Outputs first and safe first: a floating DRV8871 input with
                # the motor rail up is a prop that spins at power-on.
                self._l_in1 = PWMOutputDevice(self.PIN_THRUST_L_IN1, frequency=settings.thruster_pwm_hz)
                self._l_in2 = PWMOutputDevice(self.PIN_THRUST_L_IN2, frequency=settings.thruster_pwm_hz)
                self._r_in1 = PWMOutputDevice(self.PIN_THRUST_R_IN1, frequency=settings.thruster_pwm_hz)
                self._r_in2 = PWMOutputDevice(self.PIN_THRUST_R_IN2, frequency=settings.thruster_pwm_hz)
                self._have["thrusters"] = True
                self.set_thrusters(0.0, 0.0)
            except Exception as exc:  # noqa: BLE001 — the whole point is not to raise
                self._have["thrusters"] = False
                self._fault(
                    "thrusters",
                    "thruster group did not come up (%s) — the sub CANNOT BE ARMED "
                    "and will not answer the sticks",
                    exc,
                )
        else:
            self._have["thrusters"] = False
            self._fault(
                "thrusters",
                "no GPIO stack on this machine — the sub CANNOT BE ARMED here. "
                "Sensing continues over the brainstem if one is plugged in",
            )

        # ---- brainstem: everything else -----------------------------------
        # `link` is injectable for the bench (an in-memory transport), which is
        # the only way these rules get exercised without a devkit on USB.
        self._link = link
        if self._link is None:
            try:
                from brainstem import open_link

                self._link = open_link()
            except Exception as exc:  # noqa: BLE001
                self._link = None
                log.warning("brainstem: link could not be opened (%s)", exc)
        if self._link is not None and not self._link.wait_first_frame(self.HELLO_WAIT_S):
            # A port that never says anything NEPTUNE-shaped is not a
            # brainstem — a Bluetooth port, a different device, a dead cable.
            # Close it rather than spend the dive polling junk.
            log.warning("brainstem: port opened but nothing NEPTUNE-shaped arrived — not a brainstem")
            try:
                self._link.close()
            except Exception:  # noqa: BLE001
                pass
            self._link = None
        self._have["brainstem"] = self._link is not None
        if self._link is None:
            self._fault(
                "brainstem",
                "no brainstem answered — every sensor reading is cannot-tell "
                "under this one name until the ESP32 is plugged in",
            )

        if not (self._have.get("thrusters") or self._have.get("brainstem")):
            raise RuntimeError(
                "RealHardware: neither half exists — no GPIO stack for the thrusters "
                "and no brainstem on serial. Set NEPTUNE_HW=mock, plug the ESP32 in, "
                "or wire the Pi."
            )

        # Tether link sampling stays a Pi concern — the carrier file is the
        # Pi's own NIC. Cached briefly so 15 Hz telemetry does not hammer sysfs.
        from sysinfo import TETHER_IFACE

        self._carrier_path = f"/sys/class/net/{TETHER_IFACE}/carrier"
        self._carrier_cache: tuple[float, int] = (0.0, -1)

        log.info(
            "RealHardware active — fitted: %s; not fitted: %s; brainstem: %s",
            ",".join(sorted(n for n, ok in self._have.items() if ok)) or "none",
            ",".join(sorted(n for n, ok in self._have.items() if not ok)) or "none",
            (self._link.hello or {}).get("fw", "answering") if self._link else "absent",
        )

    @staticmethod
    def _gpio_available() -> bool:
        """True once a real GPIO stack is importable AND a human says wired.

        Same two gates as always: the flag (NEPTUNE_HW_WIRED) is the human
        asserting a harness exists, and gpiozero importing is the machine
        being a machine that could drive one. This gate now covers ONLY the
        thruster half — the brainstem has its own (a port that answers).
        """
        if not settings.hardware_wired:
            return False
        try:
            import gpiozero  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- actuators --------------------------------------------------------
    def set_armed(self, on: bool) -> None:
        # ARMING IS REFUSED WITHOUT THE BRIDGES — a live console over a vehicle
        # that cannot answer it is the one failure this method exists to stop.
        if on and not self._have.get("thrusters"):
            self._fault(
                "thrusters",
                "REFUSING TO ARM: the thruster drivers did not come up, so the "
                "sticks would move nothing. Fix the wiring before arming",
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
        self._drive_bridge(self._l_in1, self._l_in2, left)
        self._drive_bridge(self._r_in1, self._r_in2, right)

    @staticmethod
    def _drive_bridge(in1, in2, v: float) -> None:
        d1, d2 = drv8871_duty(v, settings.thruster_deadband)
        # The falling side goes to zero BEFORE the rising side comes up:
        # both inputs high is the DRV8871's brake, and commanding it during a
        # reversal is a current spike the 3.6 A limiter has to eat for nothing.
        if d1 == 0.0:
            in1.value = 0.0
            in2.value = d2
        else:
            in2.value = 0.0
            in1.value = d1

    def set_camera(self, pan: float, tilt: float) -> None:
        # v2 — DOCUMENTED NO-OP. No pan/tilt servos are fitted; the protocol
        # fields stay so adding them later is a wiring job, not a contract change.
        return

    def ballast_pump(self, direction: BallastDir) -> None:
        # The loop closes on the brainstem: this only says which way. The pump
        # meters its own millilitres and stops itself on fault (firmware).
        if direction not in ("fill", "empty", "hold"):
            log.warning("ballast_pump(%r): unknown direction, holding", direction)
            direction = "hold"
        if self._link is not None:
            self._link.send("pump", {"fill": 1, "empty": -1, "hold": 0}[direction])

    def ballast_home(self) -> None:
        # Purge-home: the pump runs out against the empty bag until the flow
        # goes silent, and THAT is the datum (docs/hardware.md §6). Returns
        # immediately; `homed` arrives in telemetry when the vehicle says so.
        if self._link is not None:
            self._link.send("trim_home")
            log.info("ballast: purge-homing against the empty bag")

    def set_magnet(self, on: bool) -> None:
        # v2 — no electromagnet was bought (the burn wire took its role).
        log.warning("set_magnet(%s) ignored — no electromagnet is fitted", on)

    # THE COMMANDED STATE IS RECORDED EVEN WITH NO LINK, and the command is
    # only sent if there is one — same shape as the old unwired lamp pins: the
    # console shows what you asked for, and "brainstem" in sensor_faults says
    # it went nowhere.
    #
    # INTERIM MAPPING (docs/playbook.md §8 owns the real vocabulary): the
    # console's "white" channel is the lamp; "green" drives the red locator
    # BEACON, because the green ring does not exist on the bought vehicle and
    # a dead control tells nobody anything.
    def set_light(self, which: Which, on: bool) -> None:
        _, lvl = self._lights[which]
        self._lights[which] = (on, lvl)
        self._send_light(which)

    def set_light_level(self, which: Which, level: float) -> None:
        on, _ = self._lights[which]
        lvl = max(0.0, min(1.0, level))
        self._lights[which] = (on, lvl)
        self._send_light(which)

    def _send_light(self, which: Which) -> None:
        if self._link is None:
            return
        on, lvl = self._lights[which]
        if which == "white":
            self._link.send("lamp", round(lvl if on else 0.0, 3))
        else:
            self._link.send("beacon", 1 if on else 0)

    def release_dropweight(self) -> None:
        # The two-step interlock, driven in order: ARM, then FIRE. The firmware
        # refuses FIRE unless its own armed state agrees, so no single spurious
        # write on this cable can shed the weight. LOUD on both outcomes — an
        # operator who believes a release happened when it did not is the
        # dangerous half.
        if self._link is None:
            log.error("DROP-WEIGHT RELEASE COMMANDED WITH NO BRAINSTEM — nothing was released")
            return
        log.warning("DROP-WEIGHT RELEASE: arming the burn interlock")
        self._link.send("arm_burn", 1)
        self._link.send("fire_burn")
        log.warning(
            "DROP-WEIGHT RELEASE: fire commanded — watch telemetry burn_fired for "
            "the vehicle's own confirmation; the bridle is a bench re-arm to replace"
        )

    # ---- readbacks (all snapshot reads: no bus, no blocking) --------------
    def _val(self, key: str):
        return None if self._link is None else self._link.value(key)

    def get_magnet(self) -> bool:
        return self._magnet  # always False; see set_magnet

    def get_light(self, which: Which) -> tuple[bool, float]:
        return self._lights[which]

    def get_ballast_level(self) -> float | None:
        # ballast_ml is null on the wire until the vehicle has purge-homed —
        # the same unknown-until-homed honesty the syringe had. The capacity is
        # config because it is the bag's working swing, not a property of code.
        ml = self._val("ballast_ml")
        if ml is None:
            return None
        return min(1.0, max(0.0, float(ml) / settings.ballast_capacity_ml))

    def ballast_homed(self) -> bool:
        return bool(self._val("ballast_homed"))

    def ballast_needs_rehome(self) -> bool:
        # The pump's skipped-step: it ran and the flow sensor stayed silent
        # (worn tube, clog, dry inlet), so the millilitre count is not to be
        # believed until a purge-home re-references it.
        return bool(self._val("ballast_fault"))

    def read_pressure(self) -> float | None:
        return self._val("press_psi")

    def read_heading(self) -> float | None:
        # The mounting offset is the Pi's calibration constant, applied here so
        # the firmware stays a sensor and the calibration stays in nav config —
        # same as it always was, one layer down.
        h = self._val("heading")
        if h is None:
            return None
        return (float(h) + nav_settings.imu_yaw_offset_deg) % 360.0

    def read_gyro_z_dps(self) -> float | None:
        return self._val("gyro_z")

    def read_accel_fwd_ms2(self) -> float | None:
        return self._val("accel_fwd")

    def read_mag_cal(self) -> int | None:
        v = self._val("mag_cal")
        return None if v is None else int(v)

    def read_pitch_roll(self) -> tuple[float | None, float | None]:
        return (self._val("pitch"), self._val("roll"))

    def read_water_speed(self) -> tuple[float, bool]:
        # Flow-log pulses → m/s via the same calibration constant the
        # paddlewheel used (a placeholder until the measured-run; the k-factor
        # is nav's, docs/hardware.md §14). fresh=False when the link is down,
        # the sensor is stale, or the vehicle says so — magnitude then
        # meaningless, carried as cannot-tell by every caller already.
        hz = self._val("speed_hz")
        fresh = self._val("speed_fresh")
        if hz is None or not fresh:
            return (0.0, False)
        return (float(hz) * nav_settings.m_per_pulse, True)

    def read_speed_dir(self) -> int | None:
        # The PAS ring's gift: -1 astern / +1 ahead / 0 unknown — the one thing
        # the throttle sign could never measure. Not yet consumed by nav; it
        # rides here so the estimator can take it when that work lands (§20).
        v = self._val("speed_dir")
        return None if v is None else int(v)

    def read_water_c(self) -> float | None:
        # MS5837 water temperature — sound-speed correction for the sonar tier,
        # not on the telemetry wire yet.
        return self._val("water_c")

    def read_voltage(self) -> float | None:
        return self._val("pack_v")

    def read_current_a(self) -> float | None:
        return self._val("pack_a")

    def read_rail_v(self) -> float | None:
        # INA219 #2, the 8 V thruster rail. Pack-vs-rail divergence is the
        # failing-motor signature (docs/hardware.md §13); not on the wire yet.
        return self._val("rail_v")

    def read_rail_a(self) -> float | None:
        return self._val("rail_a")

    def read_leak(self) -> str:
        """Three zones → the four-stage ladder the console already speaks.

        One zone latched is WARN (water somewhere — finish up and find out);
        two or more agreeing is FLOOD (corroborated water — come up now), the
        same 2-of-3 the vehicle's own reflex fires on. Latches are mirrored
        sticky on this side, so WET OUTRANKS CANNOT-TELL across a link death:
        a FLOOD seen in any frame stands until a frame shows the vehicle's own
        latch cleared (leak_reset is the only thing that clears it there).
        NORMAL still has to be earned — a live link, sampling healthy, no
        boot-wet zone — because it is a positive claim about the hull.
        """
        latches = self._val("leak_latch")
        if isinstance(latches, (list, tuple)) and len(latches) == 3:
            for name, wet in zip(("fwd", "mid", "aft"), latches):
                if wet:
                    self._leak_latched.add(name)
                else:
                    self._leak_latched.discard(name)
        if len(self._leak_latched) >= 2:
            return "FLOOD"
        if self._leak_latched:
            return "WARN"
        if self._link is None or not self._link.link_ok() or not self._val("leak_ok"):
            return LEAK_UNKNOWN
        if self.leak_probe_fault() is not None:
            return LEAK_UNKNOWN
        return "NORMAL"

    def leak_probe_fault(self) -> str | None:
        # A zone wet at power-on in a hull sealed dry is a shorted probe or a
        # flooded hull — either way a probe that cannot certify anything. The
        # firmware captures it at boot and carries it until a leak_reset; the
        # string names the zones so the console names the seal to suspect.
        boot = self._val("leak_boot")
        if not isinstance(boot, (list, tuple)) or len(boot) != 3:
            return None
        wet = [name for name, b in zip(("fwd", "mid", "aft"), boot) if b]
        return "+".join(wet) if wet else None

    def reset_leak_latches(self) -> dict:
        """Re-arm the leak detector — decided by the vehicle, relayed here.

        The refusal-while-wet rule lives beside the pins it reads, which is the
        firmware now. This waits (briefly — the one deliberate wait in this
        class) for the ack because the caller must relay WHY a refusal
        happened, and 'the button did nothing' teaches an operator that the
        button is broken.
        """
        if self._link is None:
            return {"ok": False, "cleared": [], "why": "no brainstem — there is nothing to re-arm"}
        ack = self._link.request("leak_reset", timeout_s=0.6)
        if ack is None:
            return {"ok": False, "cleared": [], "why": "the brainstem did not answer — check the link"}
        if not ack.get("ok"):
            zone = ack.get("err") or "a probe"
            return {
                "ok": False,
                "cleared": [],
                "wet_now": [zone],
                "why": (
                    f"the {zone} zone is WET RIGHT NOW. This clears the memory of "
                    f"water, never water that is present — dry the hull and find out "
                    f"where it came from first"
                ),
            }
        self._leak_latched.clear()
        self._leak_rearms += 1
        log.warning("leak detector RE-ARMED by operator (re-arm #%d)", self._leak_rearms)
        return {"ok": True, "cleared": ["latches"], "rearms": self._leak_rearms, "why": "all three zones read dry"}

    def link_quality(self) -> int:
        # The TETHER, not the brainstem — the Pi's own NIC carrier, cached for
        # half a second so telemetry rate does not hammer sysfs. -1 is the only
        # value that is not a claim the link is up.
        now = time.monotonic()
        at, val = self._carrier_cache
        if now - at < 0.5:
            return val
        try:
            with open(self._carrier_path, "r") as fh:
                val = 4 if fh.read().strip() == "1" else -1
        except Exception:  # noqa: BLE001 — no such interface = no tether
            val = -1
        self._carrier_cache = (now, val)
        return val

    def sensor_faults(self) -> tuple[str, ...]:
        """Pi-side latched faults ∪ the vehicle's own, fronted by the link.

        Link down → the one name "brainstem" stands in front of every chip
        behind it (naming them too would claim knowledge nobody has — the
        i2c-front rule, one level up). Link up → the ESP32's per-chip verdicts
        pass through verbatim, in the vocabulary the console already renders.
        """
        stem = self._link.faults() if self._link is not None else ("brainstem",)
        return tuple(sorted(self._faults | set(stem)))

    def sensors_absent(self) -> tuple[str, ...]:
        # Leaf parts only, decided at the bus by the firmware. Empty when the
        # link is down: "cannot tell them apart" stays the loud default.
        return self._link.absent() if self._link is not None else ()

    @property
    def is_mock(self) -> bool:  # type: ignore[override]
        # ANNOUNCED SIMULATION PROPAGATES. The firmware's bench mode simulates
        # every reading and says so in every frame; the console must show the
        # SIM presentation for exactly as long as that is true. A real link
        # sending real readings — or no link at all — is not a simulation.
        return self._link is not None and self._link.bench_mode

    # ---- lifecycle --------------------------------------------------------
    def close(self) -> None:
        try:
            self.set_armed(False)
        except Exception:  # noqa: BLE001
            pass
        for dev in (self._l_in1, self._l_in2, self._r_in1, self._r_in2):
            try:
                if dev is not None:
                    dev.close()
            except Exception:  # noqa: BLE001
                pass
        if self._link is not None:
            try:
                self._link.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- fault bookkeeping (unchanged rules) ------------------------------
    def _log_fault(self, key: str, msg: str, *args) -> None:
        now = time.monotonic()
        if now - self._fault_logged.get(key, -1e9) > 60.0:
            self._fault_logged[key] = now
            log.error(msg, *args)

    def _fault(self, key: str, msg: str, *args) -> None:
        self._faults.add(key)
        self._log_fault(key, msg, *args)

    def _clear_fault(self, key: str) -> None:
        if key in self._faults:
            self._faults.discard(key)
            log.info("%s is answering again", key)


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

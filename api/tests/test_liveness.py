"""The CANNOT-TELL chain, walked end to end — hardware readback to the JSON on the wire.

THE RULE THIS SUITE GUARDS. A signal whose sensor is absent shows CANNOT-TELL, never
a plausible number — and "absent" includes "was here and stopped". A cannot-tell
default that is itself a measurement is not a cannot-tell: 0.0 heading is DUE NORTH,
0.0 depth is THE SURFACE, mag_cal 0 is "a compass answered and it says it is
uncalibrated", 0.0 V is a claim about a pack, leak "NORMAL" is a claim that the hull
is dry, and 4 bars is a claim that the tether is up.

WHY THIS IS ONE SUITE AND NOT FIVE SETS OF UNIT TESTS. This exact class of bug has now
been found and fixed four review rounds running, in a different file each time, and
every round's per-file tests were green. That is not bad luck, it is the shape of the
defect: the chain has five links and a null only has to be coerced back into a number
at ONE of them for the console to show a confident reading. Round three made the
hardware layer, the wire contract, rov.py and the client all honest — and the frame
that reached the operator still read heading=0.0 card='N' beside a "bno085 not
answering" fault, because nav/sensors.py handed its readback helper an invented
default and main.py's fill_nav_fields() then stamped nav's heading over the null
rov.py had correctly sent. Two files, each locally defensible, and the chain leaked
between them.

So every test here starts at a MockHardware readback and finishes at
`json.loads(Telemetry.model_dump_json())` — the bytes the client actually parses —
through the real VehicleSensorSource, the real NavService tick, the real estimator and
the shipped fill_nav_fields(). A link that quietly invents a number is caught wherever
it is, because nothing in between is stubbed.

    hardware readback -> SensorSample -> NavState -> Telemetry -> the wire

THE INVERSE MATTERS JUST AS MUCH. A chain that answers cannot-tell all the time is
useless in a different way: blank gauges nobody can explain train an operator to
ignore blanks, which is how the one blank that mattered gets ignored too. So the same
harness is flown with every sensor healthy and asserts that NOTHING is null and NO
fault is named.

HOW A SENSOR IS STOPPED. `MockHardware._kill_sensor(chip)` stops one part mid-run: its
readbacks go to cannot-tell, its name appears in sensor_faults(), and the simulation
underneath KEEPS RUNNING, so the truth drifts away from the last value the vehicle
ever read. That is the whole point — a frozen number and a live one are only
distinguishable while the world moves.

TWO VOCABULARIES, AND THE SPLIT IS THE POINT. A CHIP stops answering: a transaction
fails, and there is a part with an I2C designation that a human can go and unplug. A
SUBSYSTEM stops being run: nothing NAKs, nothing raises, the readings simply stop
being taken while the cache goes on holding the last comfortable value. They have
separate hooks because they are separate claims, and this suite keeps them separate
for the same reason.

    _kill_sensor("ms5837")          depth / pressure                I2C
    _kill_sensor("bno085")          heading / mag_cal / rates       I2C
    _kill_sensor("ina219")          pack volts / amps               I2C
    _stall_leak_sampling(True)      hull water — GPIO, on no bus at all
    _stall_sensor_thread(True)      the loop that samples all of the above

NOT EVERY CANNOT-TELL IS A NULL, and the two that are not are the easiest to lose.
`leak_state` is a required string on the wire and `signal` is an int, so neither field
has a null to spend: their cannot-tells are "UNKNOWN" and -1. Same rule, different
spelling, and both are asserted here — a cannot-tell that has to be remembered as a
special case is a cannot-tell that gets forgotten.

stdlib unittest only, matching the rest of api/tests — see run.py for why there is no
framework here.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
import logging
import unittest
from pathlib import Path

from config import settings
from hardware import LEAK_UNKNOWN, MockHardware
from nav.config import settings as nav_settings
from nav.estimator import make_estimator
from nav.models import NavState, Origin, SensorSample
from nav.sensors import VehicleSensorSource
from nav.service import NavService
from protocol import BallastMsg, CommandMsg, ControlMsg, Telemetry
from rov import RovState, cardinal

_API_DIR = Path(__file__).resolve().parents[1]

# The two vocabularies, taken from the hardware layer rather than restated here — a
# copy would let this suite go on testing a chip the vehicle has stopped naming.
I2C_CHIPS = MockHardware.DEVICES  # _kill_sensor
SUBSYSTEMS = MockHardware.SUBSYSTEMS  # the _stall_* hooks
EVERYTHING = tuple(I2C_CHIPS) + tuple(SUBSYSTEMS)

# Which hook stops which subsystem. Named here so Chain can sweep both vocabularies
# together without pretending they are one.
_STALL_HOOKS = {
    "leak-probes": "_stall_leak_sampling",
    "sensor-thread": "_stall_sensor_thread",
}


# ---------------------------------------------------------------------------
# Quiet, deliberately
# ---------------------------------------------------------------------------
# Killing a sensor logs a WARNING and every test here kills something, so without
# this the suite prints a page of alarms that are the fixture working correctly. The
# level is set on "neptune" alone — its children carry no level of their own, so they
# inherit it — and it is put back in tearDownModule, because a test module that
# permanently silences the vehicle's logger is a worse bug than the noise it removed.
_SAVED_LOG_LEVEL: int | None = None


def setUpModule() -> None:
    global _SAVED_LOG_LEVEL
    lg = logging.getLogger("neptune")
    _SAVED_LOG_LEVEL = lg.level
    lg.setLevel(logging.CRITICAL)


def tearDownModule() -> None:
    if _SAVED_LOG_LEVEL is not None:
        logging.getLogger("neptune").setLevel(_SAVED_LOG_LEVEL)


# ---------------------------------------------------------------------------
# The one link that cannot be imported
# ---------------------------------------------------------------------------
def load_fill_nav_fields():
    """Compile just `fill_nav_fields` out of api/main.py.

    Same reason (and the same technique) as test_telemetry's load_battery_band:
    `import main` builds the camera and nav services and constructs a BlackBox, which
    opens a fresh session .jsonl on disk and rewrites current.jsonl. A unit test that
    leaves a dive log behind every run is a unit test that gets deleted.

    Compiling the SHIPPED source rather than reimplementing the rule is the entire
    point — this function is the last link in the chain and it is where round three's
    nulls died. main.py has `from __future__ import annotations`, so its signature
    does not need FastAPI or Telemetry at definition time; `cardinal` is the only name
    the body reaches for, and it comes from rov.py exactly as main.py's does.
    """
    src = (_API_DIR / "main.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "fill_nav_fields":
            ns: dict = {"cardinal": cardinal}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(_API_DIR / "main.py"), "exec"), ns)  # noqa: S102
            return ns["fill_nav_fields"]
    raise AssertionError(
        "api/main.py no longer defines fill_nav_fields() — the place where navigation's "
        "answers are stitched into a telemetry frame has moved, and this suite must "
        "follow it there rather than quietly stop checking the link that broke."
    )


fill_nav_fields = load_fill_nav_fields()


# ---------------------------------------------------------------------------
# What each part is the ONLY instrument for
# ---------------------------------------------------------------------------
# Telemetry fields whose only source is that part. If it is named in sensor_faults,
# every field here must be null ON THE SAME FRAME — the nulls and the fault list are
# one verdict read twice and they can never be allowed to contradict each other in
# front of an operator. A confident bearing beside a NO COMPASS badge is read as the
# bearing.
_NULL_FIELDS = {
    "ms5837": ("depth", "pressure"),
    "bno085": ("heading", "heading_card", "mag_cal"),
    "ina219": ("battery_v", "current_a"),
}

# The same three, at the SensorSample stage — what navigation is fed. A null that has
# already been coerced back into a number here never reaches telemetry as a null
# again, and it is also the number the permanent dive log records.
_SAMPLE_FIELDS = {
    "ms5837": ("depth_m", "pressure_psi"),
    "bno085": ("heading_deg", "mag_cal"),
    "ina219": (),  # the pack is not a navigation input
}

# And at the NavState stage — what the map draws and what DiveLog writes to disk.
_NAV_FIELDS = {
    "ms5837": ("depth_m",),
    "bno085": ("heading_deg", "mag_cal"),
    "ina219": (),
}

# The plausible number each cannot-tell must never collapse into, and what that number
# would be ASSERTING if it reached the console. Every one of these has been handed to
# a "safe" fallback somewhere in this repo at some point.
_PLAUSIBLE_LIE = {
    "heading": (0.0, "due north — and the radar is heading-up, so the whole map swings"),
    "heading_deg": (0.0, "due north — and the dead reckoner runs the track north"),
    "depth": (0.0, "at the surface"),
    "depth_m": (0.0, "at the surface — and this is the number written into the dive log"),
    "pressure": (settings.surface_pressure_psi, "the pressure at the surface"),
    "pressure_psi": (settings.surface_pressure_psi, "the pressure at the surface"),
    "mag_cal": (0, "a compass answered, and it says it is uncalibrated"),
    "battery_v": (0.0, "a pack voltage, and a vehicle transmitting this frame is not at 0 V"),
    "current_a": (0.0, "the vehicle is drawing nothing, which a powered hull is not"),
}

ORIGIN = Origin(lat=52.0, lon=-1.0, accuracy=3.0, heading_deg=0.0, source="manual")


# ---------------------------------------------------------------------------
# The chain, assembled the way the server assembles it
# ---------------------------------------------------------------------------
class Chain:
    """One vehicle, one navigation service, one telemetry frame — nothing stubbed.

    Deliberately NOT a set of fakes standing in for each stage. Every previous round
    of this bug survived tests that mocked the layer next door, because the coercion
    lived in the seam between two real files and both of them looked right on their
    own.

    Two accommodations, and only two:

      * NO ORIGIN IS SET, and the estimator is built directly. NavService._tick
        auto-starts a dive log the moment an origin exists (autolog is on by design —
        the dive you forgot to record is the one you needed), and a unit suite that
        writes dive journals into data/dives on every run is a suite that gets
        switched off. The estimator is the part of that machinery under test and it is
        constructed here exactly as start_dive() constructs it.
      * A TICK THAT RAISES IS CAUGHT, not swallowed. NavService._loop does the same —
        one bad tick is not the end of navigation — but the loop hides the exception
        in a counter, and here it is kept so a test can assert that a dead part
        produced a NULL and not a traceback. A dead sensor must not take the whole of
        navigation down with it: that would blank depth, speed and position too,
        which is a subsystem blackout dressed as per-signal honesty.
    """

    def __init__(self) -> None:
        self.hw = MockHardware()
        self.rov = RovState(self.hw)
        self.svc = NavService(lambda: self.rov)
        if not self.svc.reads_vehicle:
            # NAV_SENSORS=sim in the environment points navigation at a scripted path
            # that ignores this hull entirely. Every assertion below would then pass
            # against canned data, which is the most expensive way for a suite to be
            # green. The source is forced back to the live one rather than skipped:
            # the chain under test is the chain that flies.
            self.svc.sensors = VehicleSensorSource(lambda: self.rov)
        self.svc.dr = make_estimator(ORIGIN)
        self.app = _AppStub(self.svc)
        self.tick_errors: list[str] = []

    # ---- driving ---------------------------------------------------------
    def arm(self, throttle: float = 0.0, steer: float = 0.0) -> "Chain":
        self.rov.apply_command(CommandMsg(type="command", name="arm"))
        self.rov.apply_control(ControlMsg(type="control", throttle=throttle, steer=steer))
        return self

    def ballast(self, cmd: str) -> "Chain":
        self.rov.apply_ballast(BallastMsg(type="ballast", cmd=cmd))
        return self

    def fly(self, seconds: float = 1.0, dt: float = 0.1) -> "Chain":
        """Advance the vehicle and turn the navigation loop for `seconds` of SIMULATED
        time. The mock's clock is simulated, so three seconds of drift costs
        microseconds and no test ever sleeps — a fixture that waits on a wall clock is
        a flaky test with extra steps."""

        async def _run() -> None:
            for _ in range(max(1, round(seconds / dt))):
                self.rov.update(dt)
                try:
                    await self.svc._tick(dt, 0, 1)
                except Exception as exc:  # noqa: BLE001 — mirrors NavService._loop
                    self.tick_errors.append(f"{type(exc).__name__}: {exc}")

        asyncio.run(_run())
        return self

    # ---- stopping things -------------------------------------------------
    # kill/revive take CHIPS and stall/unstall take SUBSYSTEMS, deliberately, because
    # the hardware layer draws that line and a harness that blurred it would let a
    # test claim a chip failed when what actually stopped was the loop reading it.
    # stop/start dispatch across both, and exist only for the sweeps that have to
    # cover every combination.
    def kill(self, *chips: str) -> "Chain":
        for chip in chips:
            self.hw._kill_sensor(chip)
        return self

    def revive(self, *chips: str) -> "Chain":
        for chip in chips:
            self.hw._revive_sensor(chip)
        return self

    def stall(self, *subsystems: str) -> "Chain":
        for name in subsystems:
            getattr(self.hw, _STALL_HOOKS[name])(True)
        return self

    def unstall(self, *subsystems: str) -> "Chain":
        for name in subsystems:
            getattr(self.hw, _STALL_HOOKS[name])(False)
        return self

    def stop(self, *parts: str) -> "Chain":
        for part in parts:
            self.stall(part) if part in _STALL_HOOKS else self.kill(part)
        return self

    def start(self, *parts: str) -> "Chain":
        for part in parts:
            self.unstall(part) if part in _STALL_HOOKS else self.revive(part)
        return self

    # ---- the stages ------------------------------------------------------
    def sample(self) -> SensorSample | None:
        """Stage 2: what navigation was fed this tick."""
        return self.svc.last_sample

    def nav(self) -> NavState | None:
        """Stage 3: the estimate, but only while it is still CURRENT (fresh_state,
        not last_state — a plain attribute holds its final value forever)."""
        return self.svc.fresh_state()

    def raw_frame(self) -> Telemetry:
        """Stage 4a: the frame rov.py builds, BEFORE navigation is stitched in.

        Kept separate because the round-three defect is only visible in the gap
        between this and frame(): rov.py sent heading=None and fill_nav_fields()
        stamped 0.0/'N' over it.
        """
        return self.rov.telemetry({})

    def frame(self) -> Telemetry:
        """Stage 4b: the frame the control loop broadcasts, nav fields and all."""
        tel = self.raw_frame()
        fill_nav_fields(self.app, tel)
        return tel

    def wire(self) -> dict:
        """Stage 5: the JSON the client actually parses. A value that survives
        pydantic as 0 and appears as 0 here is still a lie by the time it is a glyph."""
        return json.loads(self.frame().model_dump_json())


class _AppStub:
    """Just enough FastAPI for fill_nav_fields, which only reads app.state.nav_svc."""

    class _State:
        pass

    def __init__(self, svc) -> None:
        self.state = _AppStub._State()
        self.state.nav_svc = svc


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------
class ChainTestCase(unittest.TestCase):
    def assertCannotTell(self, value, field: str, where: str) -> None:
        """`value` must be the null — and specifically must not be the plausible
        number that this field's cannot-tell has historically collapsed into."""
        lie = _PLAUSIBLE_LIE.get(field)
        if lie is not None and value is not None and value == lie[0]:
            self.fail(
                f"{where}: {field}={value!r} — that is not a blank, it is a "
                f"reading, and it says {lie[1]}. The sensor behind it is not "
                f"answering."
            )
        self.assertIsNone(
            value, f"{where}: {field}={value!r}, expected null " f"(the sensor behind it is not answering)"
        )

    def assertNoContradiction(self, wire: dict) -> None:
        """No frame may name a part as silent and still show that part's reading.

        This is the screen the operator is looking at: a confident bearing of DUE
        NORTH sitting beside a NO COMPASS badge and a "bno085 not answering" fault,
        all at once. Whichever link invented the number, it is caught here.
        """
        bad = []
        faults = set(wire.get("sensor_faults", []))
        for part in faults:
            for field in _NULL_FIELDS.get(part, ()):
                if wire.get(field) is not None:
                    bad.append(
                        f"{part} is reported as not answering, and {field}=" f"{wire[field]!r} is on the same frame"
                    )
        # The two cannot-tells that are not nulls, because their fields have no null
        # to spend. Checked here rather than left to the individual suites: a rule
        # with an exception nobody applies systematically is how leak_state got to be
        # the one reading on the vehicle with no liveness gate at all.
        if faults & {"leak-probes", "sensor-thread"}:
            if wire.get("leak_state") == "NORMAL":
                bad.append(
                    "the leak probes are reported as not being sampled, and "
                    "leak_state='NORMAL' is on the same frame — the strongest "
                    "reassurance this vehicle gives, made from evidence nobody "
                    "collected"
                )
            if wire.get("leak") is False:
                bad.append(
                    "the leak probes are reported as not being sampled, and the "
                    "old single-bit alarm says leak=false, which an old client "
                    "reads as a dry hull"
                )
        if "sensor-thread" in faults and wire.get("signal", -1) >= 0:
            bad.append(
                f"the sensor thread is reported as stopped, and signal="
                f"{wire['signal']} bars is on the same frame — bars are a claim "
                f"that the tether is up, read from a sampler that is not running"
            )
        self.assertEqual(bad, [], "\n".join(["one frame contradicting itself in front of the operator:"] + bad))

    def assertNavSurvived(self, chain: Chain) -> None:
        """A dead part must reach the client as a null, not as a traceback.

        If the nav tick raises, everything navigation produces goes to cannot-tell at
        once — depth, speed, position, snag — which is a subsystem blackout wearing
        per-signal honesty's clothes. The operator loses signals that are still being
        measured perfectly well by parts that are still answering.
        """
        self.assertEqual(
            chain.tick_errors,
            [],
            "\n".join(
                [
                    "a sensor going silent took the navigation tick down with it; "
                    "cannot-tell has to be a VALUE that travels, not an exception:"
                ]
                + chain.tick_errors
            ),
        )


# ---------------------------------------------------------------------------
# The harness itself — if this is wrong, everything below passes for free
# ---------------------------------------------------------------------------
class HarnessTest(ChainTestCase):
    def test_the_chain_reads_this_hull_and_not_a_script(self):
        # If navigation were fed the scripted simulator, fill_nav_fields() would
        # decline to stamp anything (reads_vehicle is false there) and every heading
        # assertion in this file would pass without exercising the bug at all.
        c = Chain()
        self.assertTrue(c.svc.reads_vehicle)
        self.assertIsInstance(c.svc.sensors, VehicleSensorSource)

    def test_navigation_is_actually_answering_by_the_time_it_is_asked(self):
        # The whole suite rests on nav having a fresh state to stamp. A harness that
        # never produced one would make the headline test vacuous.
        c = Chain().arm(throttle=1.0).fly(1.0)
        self.assertIsNotNone(c.nav())
        self.assertTrue(c.svc.health()["answering"])
        self.assertNavSurvived(c)

    def test_a_stopped_part_answers_nothing_at_the_hardware_layer(self):
        # The fixture's own claim, checked before anything is built on it.
        c = Chain().stop(*EVERYTHING)
        self.assertIsNone(c.hw.read_pressure())
        self.assertIsNone(c.hw.read_heading())
        self.assertIsNone(c.hw.read_mag_cal())
        self.assertIsNone(c.hw.read_voltage())
        self.assertIsNone(c.hw.read_current_a())
        self.assertEqual(c.hw.read_leak(), LEAK_UNKNOWN)
        self.assertLess(c.hw.link_quality(), 0)
        self.assertEqual(c.hw.sensor_faults(), tuple(sorted(EVERYTHING)))

    def test_a_typo_in_a_kill_does_not_quietly_exercise_a_healthy_vehicle(self):
        with self.assertRaises(ValueError):
            Chain().kill("bn085")

    def test_a_subsystem_is_not_reachable_through_the_chip_vocabulary(self):
        # A stalled sampler named as a dead chip would send an operator to unplug a
        # part that is not the problem — and there is no part.
        for name in SUBSYSTEMS:
            with self.assertRaises(ValueError):
                Chain().kill(name)

    def test_the_world_keeps_moving_underneath_a_dead_sensor(self):
        # Without this the "did the number freeze?" tests could not tell a frozen
        # reading from a correct one: both only differ once the truth has moved on.
        c = Chain()
        c.hw.ballast_home()
        c.ballast("fill").fly(2.0)
        first = c.hw.read_pressure()
        c.kill("ms5837").fly(2.0)
        self.assertIsNone(c.hw.read_pressure())
        c.revive("ms5837")
        self.assertGreater(
            c.hw.read_pressure(),
            first,
            "the sim must keep sinking while the sensor is dead, or "
            "nothing here proves the readout stopped following it",
        )


# ---------------------------------------------------------------------------
# THE INVERSE: a healthy vehicle must not report cannot-tell anywhere
# ---------------------------------------------------------------------------
class EverySensorHealthyTest(ChainTestCase):
    """A chain that says "I cannot tell" all the time is useless in a different way.

    Blank gauges nobody can explain teach an operator that blanks mean nothing, and
    the next blank is the one that mattered. So the same harness, flown with every
    part answering, has to produce a complete frame and name no faults at all.
    """

    def setUp(self):
        self.c = Chain()
        # Homed first: an un-homed ballast is legitimately null, and it is a DIFFERENT
        # kind of cannot-tell (an open-loop axis that has never been zeroed) with
        # nothing wrong with any sensor. Leaving it unknown here would make this test
        # argue about the wrong thing.
        self.c.hw.ballast_home()
        # Driven, because a paddlewheel below its stall speed is honestly silent: the
        # wheel has to be turning before "the measurement is present" is a fair claim.
        self.c.arm(throttle=1.0).fly(2.0)

    def test_no_fault_is_named(self):
        self.assertEqual(self.c.hw.sensor_faults(), ())
        self.assertEqual(self.c.frame().sensor_faults, [])
        self.assertEqual(self.c.wire()["sensor_faults"], [])

    def test_the_sensor_sample_is_complete(self):
        s = self.c.sample()
        self.assertIsNotNone(s)
        for field in (
            "heading_deg",
            "depth_m",
            "pressure_psi",
            "mag_cal",
            "speed_ms_measured",
            "gyro_z_dps",
            "accel_fwd_ms2",
        ):
            self.assertIsNotNone(getattr(s, field), f"{field} is null on a vehicle whose sensors all answer")

    def test_the_nav_state_is_complete(self):
        ns = self.c.nav()
        self.assertIsNotNone(ns)
        for field in ("heading_deg", "depth_m", "mag_cal", "speed_ms", "speed_src"):
            self.assertIsNotNone(getattr(ns, field), f"{field} is null on a vehicle whose sensors all answer")

    def test_the_frame_on_the_wire_is_complete(self):
        w = self.c.wire()
        # Only the readings a working sensor is behind. link_ms and the cpu_*/net_*
        # metrics are null here because this harness passes no Pi metrics and has no
        # client attached — absent inputs, not absent sensors.
        for field in (
            "depth",
            "pressure",
            "heading",
            "heading_card",
            "mag_cal",
            "battery_v",
            "current_a",
            "leak_state",
            "ballast_level",
            "speed_ms",
            "speed_src",
            "signal",
        ):
            self.assertIsNotNone(w[field], f"{field} is null on a wire frame from a healthy hull")

    def test_a_healthy_compass_produces_a_real_bearing_and_its_cardinal(self):
        w = self.c.wire()
        self.assertEqual(w["heading_card"], cardinal(w["heading"]))
        self.assertGreaterEqual(w["mag_cal"], 0)
        self.assertLessEqual(w["mag_cal"], 3)

    def test_a_healthy_hull_makes_the_positive_safety_claim(self):
        # "NORMAL" is only allowed to be said by probes that can still say otherwise —
        # and this is the case where they can, so it must actually be said.
        w = self.c.wire()
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertIs(w["leak"], False)
        self.assertIsNone(w["leak_probe_fault"])

    def test_a_healthy_sampler_reports_real_bars(self):
        self.assertGreaterEqual(self.c.wire()["signal"], 0)

    def test_the_pack_reads_as_a_2s_pack(self):
        v = self.c.wire()["battery_v"]
        self.assertGreater(v, settings.battery_floor_v)
        self.assertLessEqual(v, settings.battery_full_v)

    def test_the_track_advances(self):
        ns = self.c.nav()
        self.assertGreater(
            abs(ns.x_m) + abs(ns.y_m),
            0.0,
            "a driven, healthy vehicle whose map does not move is the " "opposite failure and just as wrong",
        )

    def test_navigation_reports_no_tick_faults(self):
        self.assertNavSurvived(self.c)
        self.assertEqual(self.c.svc.tick_faults, 0)


# ---------------------------------------------------------------------------
# MS5837 — depth and pressure
# ---------------------------------------------------------------------------
class DepthSensorTest(ChainTestCase):
    """The connector that vibrates loose at 4.33 m while the sub descends to 8.

    0.0 depth is not a blank. It is "at the surface", it is the reading a pilot would
    act on, and it is the number written into the permanent dive log.
    """

    def setUp(self):
        self.c = Chain()
        self.c.hw.ballast_home()
        self.c.ballast("fill").fly(2.0)  # get the sub off the surface first
        self.depth_when_it_died = self.c.raw_frame().depth
        self.assertGreater(
            self.depth_when_it_died,
            0.5,
            "the sub must be genuinely deep before the sensor dies, or "
            "a frozen reading and an honest one look the same",
        )
        self.c.kill("ms5837").fly(2.0)

    def test_the_hardware_readback_is_silent(self):
        self.assertIsNone(self.c.hw.read_pressure())

    def test_the_sensor_sample_carries_no_depth(self):
        s = self.c.sample()
        self.assertCannotTell(s.depth_m, "depth_m", "SensorSample")
        self.assertCannotTell(s.pressure_psi, "pressure_psi", "SensorSample")

    def test_the_nav_state_carries_no_depth(self):
        # This is the number DiveLog.add() writes to disk. A 0.0 here is a permanent
        # record saying the sub was at the surface at the moment it was sinking.
        ns = self.c.nav()
        if ns is not None:
            self.assertCannotTell(ns.depth_m, "depth_m", "NavState")

    def test_the_telemetry_frame_carries_no_depth(self):
        tel = self.c.frame()
        self.assertCannotTell(tel.depth, "depth", "Telemetry")
        self.assertCannotTell(tel.pressure, "pressure", "Telemetry")

    def test_the_wire_carries_null(self):
        w = self.c.wire()
        self.assertCannotTell(w["depth"], "depth", "the JSON on the wire")
        self.assertCannotTell(w["pressure"], "pressure", "the JSON on the wire")
        self.assertNoContradiction(w)

    def test_the_reading_did_not_freeze_at_the_last_good_depth(self):
        # The other half of the failure: not inventing 0.0, but serving 4.33 forever.
        self.c.ballast("fill").fly(3.0)
        self.assertIsNone(self.c.frame().depth)
        self.assertNotEqual(self.c.frame().depth, self.depth_when_it_died)

    def test_the_chip_is_named(self):
        self.assertEqual(self.c.wire()["sensor_faults"], ["ms5837"])

    def test_a_dead_depth_sensor_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


# ---------------------------------------------------------------------------
# BNO085 — heading, cardinal, magnetometer calibration
# ---------------------------------------------------------------------------
class CompassTest(ChainTestCase):
    """The headline of this round: a dead compass reading DUE NORTH.

    The radar is heading-up. A heading of 0.0 does not just print a wrong number, it
    swings the entire map and points the dead reckoner north — so the frozen bearing
    round three set out to fix would be replaced by something strictly worse.
    """

    def setUp(self):
        self.c = Chain()
        # Steering, so the true bearing keeps changing under the dead sensor and a
        # frozen number is distinguishable from a live one.
        self.c.arm(throttle=1.0, steer=0.6).fly(2.0)
        self.bearing_when_it_died = self.c.raw_frame().heading
        self.c.kill("bno085").fly(2.0)

    def test_the_hardware_readback_is_silent(self):
        self.assertIsNone(self.c.hw.read_heading())
        self.assertIsNone(self.c.hw.read_mag_cal())
        self.assertIsNone(self.c.hw.read_gyro_z_dps())
        self.assertEqual(self.c.hw.read_pitch_roll(), (None, None))

    def test_the_sensor_sample_carries_no_heading(self):
        # `heading = _num(_readback(hw, "read_heading", 0.0), 0.0) % 360.0` is the
        # line this test exists for: the helper's docstring said "Neither case
        # invents a number", and the DEFAULT it was handed was an invented number.
        s = self.c.sample()
        self.assertCannotTell(s.heading_deg, "heading_deg", "SensorSample")

    def test_the_sensor_sample_carries_no_mag_cal(self):
        # mag_cal 0 is not a blank either. It is "a compass answered, and it says it
        # is uncalibrated" — a real claim about a fitted part, and it sends an
        # operator to recalibrate a chip that is not there.
        s = self.c.sample()
        self.assertCannotTell(s.mag_cal, "mag_cal", "SensorSample")

    def test_the_nav_state_carries_no_heading(self):
        ns = self.c.nav()
        if ns is not None:
            self.assertCannotTell(ns.heading_deg, "heading_deg", "NavState")
            self.assertCannotTell(ns.mag_cal, "mag_cal", "NavState")

    def test_rov_py_already_sends_the_null(self):
        # Round three's fix, still standing. If this ever fails the leak has moved
        # back upstream and the tests below are chasing the wrong file.
        raw = self.c.raw_frame()
        self.assertIsNone(raw.heading)
        self.assertIsNone(raw.heading_card)
        self.assertIsNone(raw.mag_cal)

    def test_the_nav_stamp_does_not_resurrect_the_bearing(self):
        # THE DEFECT, exactly: rov.py sent heading=None card=None mag_cal=None
        # faults=['bno085'], and fill_nav_fields() stamped the estimator's heading
        # over it unconditionally, so the frame that reached the client read
        # heading=0.0 card='N' — a confident bearing of due north beside a NO COMPASS
        # badge and a "bno085 not answering" fault, all on one screen.
        tel = self.c.frame()
        self.assertCannotTell(tel.heading, "heading", "Telemetry after fill_nav_fields")
        self.assertCannotTell(tel.heading_card, "heading_card", "Telemetry after fill_nav_fields")
        self.assertCannotTell(tel.mag_cal, "mag_cal", "Telemetry after fill_nav_fields")

    def test_no_cardinal_letter_survives_a_blank_bearing(self):
        # A letter is exactly what an operator reads when the number is missing, so
        # 'N' beside a blank bearing IS the bearing as far as the console is concerned.
        w = self.c.wire()
        self.assertIsNone(
            w["heading_card"],
            f"heading_card={w['heading_card']!r} beside heading="
            f"{w['heading']!r} — a cardinal cannot outlive the number "
            f"it restates",
        )

    def test_the_wire_carries_null(self):
        w = self.c.wire()
        self.assertCannotTell(w["heading"], "heading", "the JSON on the wire")
        self.assertCannotTell(w["mag_cal"], "mag_cal", "the JSON on the wire")
        self.assertNoContradiction(w)

    def test_the_bearing_did_not_freeze_at_the_last_good_one_either(self):
        self.c.fly(3.0)
        self.assertNotEqual(self.c.frame().heading, self.bearing_when_it_died)

    def test_a_dead_compass_does_not_advance_the_track(self):
        # The consequence, not the symptom. With heading coerced to 0.0 the dead
        # reckoner integrates due north at whatever speed the LUT says the throttle is
        # worth, so the map marches the sub up the canal while nothing measures where
        # it is pointing. Freezing the track is honest; running it north is not.
        c = Chain().arm(throttle=1.0).fly(2.0)
        before = c.nav()
        self.assertIsNotNone(before)
        self.assertGreater(
            abs(before.x_m) + abs(before.y_m), 0.0, "the healthy track must be moving, or this proves nothing"
        )
        c.kill("bno085").fly(3.0)
        after = c.nav()
        if after is not None:
            moved = ((after.x_m - before.x_m) ** 2 + (after.y_m - before.y_m) ** 2) ** 0.5
            self.assertAlmostEqual(
                moved,
                0.0,
                places=2,
                msg=f"the track ran {moved:.2f} m after the compass died "
                f"({before.x_m},{before.y_m} -> {after.x_m},{after.y_m}). With no "
                f"bearing there is no direction to integrate along.",
            )

    def test_a_compass_that_answers_badly_is_not_a_compass_that_is_gone(self):
        # The two send an operator to do different things: one is "recalibrate the
        # magnetometer", the other is "the IMU is not on the bus". There is no value
        # of mag_cal that can express the second, which is why it has to be null.
        c = Chain()
        c.hw._set_mag_cal(0)
        c.arm(throttle=0.5).fly(1.0)
        self.assertEqual(c.wire()["mag_cal"], 0)
        self.assertIsNotNone(c.wire()["heading"])
        c.kill("bno085").fly(1.0)
        self.assertIsNone(c.wire()["mag_cal"])

    def test_a_dead_compass_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


# ---------------------------------------------------------------------------
# INA219 — pack voltage and current
# ---------------------------------------------------------------------------
class PackMonitorTest(ChainTestCase):
    """Volts and amps come off ONE chip, so they go silent together.

    0.0 V was the interesting argument in this file. rov.py used to send it
    deliberately, and said why: Telemetry.battery_v was a required float, main.py
    bands it with no guard, and 0.0 is impossible rather than plausible, so it pegs
    the gauge critical — "the right direction to be wrong in". That reasoning made 0.0
    a SAFE lie, not an honest answer. It was still a number where there is no
    measurement: it bands, it colours, it drives the low-battery nag, and an operator
    watching the pack collapse to zero volts surfaces a vehicle whose battery is fine
    while the actual fault is a dead monitor. The rule has no "unless the wrong
    direction is safe" clause.
    """

    def setUp(self):
        self.c = Chain().arm(throttle=0.5).fly(1.0)
        self.volts_when_it_died = self.c.raw_frame().battery_v
        self.c.kill("ina219").fly(1.0)

    def test_the_hardware_readback_is_silent(self):
        self.assertIsNone(self.c.hw.read_voltage())
        self.assertIsNone(self.c.hw.read_current_a())

    def test_the_current_reaches_the_wire_as_null(self):
        self.assertCannotTell(self.c.wire()["current_a"], "current_a", "the JSON on the wire")

    def test_the_voltage_reaches_the_wire_as_null(self):
        self.assertCannotTell(self.c.wire()["battery_v"], "battery_v", "the JSON on the wire")

    def test_the_voltage_did_not_freeze_at_the_last_good_reading(self):
        self.assertNotEqual(self.c.frame().battery_v, self.volts_when_it_died)

    def test_the_chip_is_named(self):
        self.assertEqual(self.c.wire()["sensor_faults"], ["ina219"])

    def test_the_frame_does_not_contradict_itself(self):
        self.assertNoContradiction(self.c.wire())

    def test_a_dead_pack_monitor_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


# ---------------------------------------------------------------------------
# Paddlewheel — the only instrument aboard that can contradict the speed model
# ---------------------------------------------------------------------------
class PaddleWheelTest(ChainTestCase):
    """Two different silences, and only one of them is a fault.

    A STALLED WHEEL IS AMBIGUOUS BY CONSTRUCTION. The wheel stops turning below about
    0.1 m/s, so "no pulses" means either a jammed wheel or a sub genuinely slower than
    the wheel can see. There is no liveness verdict to report and nothing to name in
    sensor_faults — only an absence of evidence, which the contract already spells:
    speed_ms_measured travels as None and never as a 0.0 that reads "measured:
    stopped". `_jam_paddle` is the hook, and what is asserted is the rest of the rule:
    the absence must not be quietly replaced by the model. The LUT may still say what
    the throttle is worth, but the frame has to be labelled "lut" so the dashboard
    styles an estimate as an estimate.

    A DEAD SENSOR THREAD IS A FAULT. Nothing is totting the pulses up any more, so the
    magnitude freezes at whatever the wheel was last doing — 0.8 m/s, FRESH, forever,
    while the sub sits still. That one has a name and gets one.
    """

    def setUp(self):
        self.c = Chain().arm(throttle=1.0).fly(2.0)
        self.assertIsNotNone(
            self.c.sample().speed_ms_measured, "the wheel must be turning first, or the jam proves nothing"
        )
        self.c.hw._jam_paddle(True)
        # Past paddle_stale_s: silence only stops being evidence after the window.
        self.c.fly(nav_settings.paddle_stale_s + 1.0)

    def test_the_hardware_flag_is_the_answer_not_the_magnitude(self):
        magnitude, fresh = self.c.hw.read_water_speed()
        self.assertFalse(fresh)
        self.assertEqual(
            magnitude,
            0.0,
            "0.0 rides along so a caller that ignores the flag does not "
            "get a confident number — but the flag is the answer",
        )

    def test_the_sensor_sample_carries_no_measured_speed(self):
        # None, not 0.0. "The wheel is not turning" and "nothing measured the water"
        # are different claims and the estimator treats them differently.
        self.assertIsNone(self.c.sample().speed_ms_measured)

    def test_the_estimate_does_not_dress_as_a_measurement(self):
        ns = self.c.nav()
        self.assertIsNotNone(ns)
        self.assertNotEqual(
            ns.speed_src,
            "paddle",
            "the wheel measured nothing this tick; labelling the LUT's "
            "number 'paddle' is an estimate wearing a measurement's badge",
        )
        self.assertEqual(ns.speed_src, "lut")

    def test_the_wire_says_where_the_speed_came_from(self):
        w = self.c.wire()
        self.assertEqual(w["speed_src"], "lut")
        self.assertIsNotNone(w["speed_ms"])

    def test_a_pinned_sub_is_reported_as_snagged_rather_than_moving(self):
        # High thrust, sustained, no measured speed: the sub is pinned on a shopping
        # trolley and the map is running away from it. The LUT alone cannot notice.
        self.assertTrue(
            self.c.wire()["snagged"],
            "full throttle with a stalled wheel for seconds is the snag "
            "signal; without it the map marches on without the vehicle",
        )

    def test_a_jammed_wheel_is_not_reported_as_a_faulted_part(self):
        # It has no name to report and inventing one would send someone to unplug a
        # working sensor. The blank speed measurement is the whole claim.
        self.assertEqual(self.c.wire()["sensor_faults"], [])

    def test_a_dead_sampler_freezes_nothing(self):
        # The other silence. A wheel that was measuring 1 m/s a moment ago must not
        # keep reporting it, fresh, off a loop that has stopped.
        c = Chain().arm(throttle=1.0).fly(2.0)
        self.assertIsNotNone(c.sample().speed_ms_measured)
        c.stall("sensor-thread").fly(1.0)
        self.assertEqual(c.hw.read_water_speed(), (0.0, False))
        self.assertIsNone(c.sample().speed_ms_measured)
        self.assertIn("sensor-thread", c.wire()["sensor_faults"])

    def test_a_jammed_wheel_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


# ---------------------------------------------------------------------------
# Leak probes — "NORMAL" is a claim, not a default
# ---------------------------------------------------------------------------
class LeakProbeTest(ChainTestCase):
    """The one reading on this vehicle that had no liveness gate at all.

    leak_state "NORMAL" asserts that both probes were read and both were dry. It is
    the strongest reassurance the hull gives and the readout that decides whether a
    dive is recoverable, and it used to be produced at full telemetry rate by a
    sampler that had stopped. There is no null to carry it — the field is a required
    string on the wire and the old alarm beside it is a bare bool — so the cannot-tell
    is spelled "UNKNOWN", and the bool must not sit at its reassuring value either.

    WET OUTRANKS CANNOT-TELL, and that direction is not symmetric: water that has
    already reached a probe is an established fact, and the sampler stopping
    afterwards does not un-establish it. Only the REASSURANCE needs liveness.
    """

    def test_unsampled_probes_do_not_certify_the_hull_as_dry(self):
        c = Chain().fly(0.5)
        self.assertEqual(c.wire()["leak_state"], "NORMAL")
        c.stall("leak-probes").fly(0.5)
        w = c.wire()
        self.assertNotEqual(
            w["leak_state"], "NORMAL", "the probes are not being sampled and the frame still says the hull is dry"
        )
        self.assertEqual(w["leak_state"], LEAK_UNKNOWN)
        self.assertNoContradiction(w)

    def test_the_old_single_bit_alarm_does_not_say_dry_either(self):
        # A client that only knows the bool is still a client that must not be
        # reassured by a probe nobody read. False is the reassuring value here, so
        # False is the one value it may not take.
        c = Chain().stall("leak-probes").fly(0.5)
        self.assertIsNot(c.wire()["leak"], False)

    def test_a_dead_sampler_stops_the_probes_too(self):
        # They are sampled ON that thread, so this is one failure and not two. A mock
        # that let them fail independently would be modelling a shape the Pi cannot
        # produce.
        c = Chain().stall("sensor-thread").fly(0.5)
        self.assertEqual(c.wire()["leak_state"], LEAK_UNKNOWN)

    def test_water_already_found_is_not_talked_back_down_to_unknown(self):
        # A latched FLOOD decaying to UNKNOWN because the sampler died would be this
        # layer talking the console down off a flood, which is unthinkable.
        c = Chain()
        c.hw._set_leak("FLOOD")
        c.fly(0.5)
        self.assertEqual(c.wire()["leak_state"], "FLOOD")
        c.stall("leak-probes").fly(0.5)
        w = c.wire()
        self.assertEqual(w["leak_state"], "FLOOD")
        self.assertIs(w["leak"], True)

    def test_a_broken_probe_pair_is_reported_rather_than_believed(self):
        # A dead probe reads dry forever, and that is the ONE failure the two-probe
        # design would otherwise hide completely. The flood probe sits ~2 cm above the
        # warn probe, so water cannot touch the upper one without covering the lower
        # one: flood-wet-while-warn-dry is a broken probe, not a leak pattern. One bit
        # each cannot say WHICH, so both are named rather than sending the operator to
        # strip the wrong one.
        c = Chain()
        c.hw._set_probe_wet(warn=False, flood=True)
        c.fly(0.5)
        self.assertEqual(c.wire()["leak_probe_fault"], "warn+flood")

    def test_a_named_probe_fault_never_shares_a_frame_with_a_dry_hull(self):
        # Whatever combination of probe states produces a fault, the frame it produces
        # must not also be making the positive claim. A probe established as unable to
        # report water cannot contribute to "there is no water".
        for warn_boot, flood_boot, warn_wet, flood_wet in itertools.product((False, True), repeat=4):
            c = Chain()
            c.hw._set_probe_wet_at_boot(warn=warn_boot, flood=flood_boot)
            c.hw._set_probe_wet(warn=warn_wet, flood=flood_wet)
            w = c.fly(0.5).wire()
            if w["leak_probe_fault"] is None:
                continue
            with self.subTest(boot=(warn_boot, flood_boot), wet=(warn_wet, flood_wet)):
                self.assertNotEqual(
                    w["leak_state"],
                    "NORMAL",
                    f"leak_probe_fault={w['leak_probe_fault']!r} on the same frame as "
                    f"leak_state='NORMAL'. NORMAL is not an absence of information, it "
                    f"is the claim that the hull is dry, and the probe it rests on has "
                    f"already been established as unable to make it.",
                )

    def test_the_probes_come_back(self):
        c = Chain().stall("leak-probes").fly(0.5)
        self.assertEqual(c.wire()["leak_state"], LEAK_UNKNOWN)
        c.unstall("leak-probes").fly(0.5)
        w = c.wire()
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertIs(w["leak"], False)
        self.assertEqual(w["sensor_faults"], [])

    def test_unsampled_probes_do_not_take_navigation_down(self):
        self.assertNavSurvived(Chain().stall("leak-probes").fly(1.0))


# ---------------------------------------------------------------------------
# The sensor thread — the failure that hides all the others
# ---------------------------------------------------------------------------
class SensorThreadTest(ChainTestCase):
    """The loop that samples everything else. When it stops, every cache below it
    holds its last value forever, and the readings that are not gated on a chip — the
    water speed, the link bars — are the ones that would go on being served as fresh.

    `signal` has no null to spend either: it is an int on the wire, so -1 is its
    cannot-tell, and -1 is also exactly what the link probe itself reports when it
    cannot read the carrier. An unreadable link and an unread one land on the same
    honest answer."""

    def setUp(self):
        self.c = Chain().arm(throttle=1.0).fly(2.0)
        self.assertGreaterEqual(self.c.wire()["signal"], 0)
        self.c.stall("sensor-thread").fly(1.0)

    def test_the_bars_are_not_a_frozen_four(self):
        w = self.c.wire()
        self.assertLess(
            w["signal"],
            0,
            f"signal={w['signal']} bars off a sampler that has stopped — "
            f"bars are read as proof the vehicle is still talking",
        )

    def test_the_hull_readout_goes_to_cannot_tell(self):
        self.assertEqual(self.c.wire()["leak_state"], LEAK_UNKNOWN)

    def test_the_water_speed_stops_being_fresh(self):
        self.assertIsNone(self.c.sample().speed_ms_measured)

    def test_the_subsystem_is_named(self):
        self.assertIn("sensor-thread", self.c.wire()["sensor_faults"])

    def test_the_frame_does_not_contradict_itself(self):
        self.assertNoContradiction(self.c.wire())

    def test_it_comes_back(self):
        self.c.unstall("sensor-thread").fly(1.0)
        w = self.c.wire()
        self.assertGreaterEqual(w["signal"], 0)
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertEqual(w["sensor_faults"], [])

    def test_a_dead_sampler_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


# ---------------------------------------------------------------------------
# sensor_faults names EXACTLY the dead parts
# ---------------------------------------------------------------------------
class SensorFaultsTest(ChainTestCase):
    """The list is what turns a blank gauge into a sentence: DEPTH — MS5837 NOT
    ANSWERING. It has to be exact in both directions — a name too few leaves a blank
    nobody can explain, a name too many sends someone to unplug a working part."""

    def test_every_subset_of_stopped_parts_is_named_exactly(self):
        # Both vocabularies, and every combination of them, because the combinations
        # are where a fault list gets built by three separate `if` branches that were
        # each written against one failure at a time.
        for n in range(len(EVERYTHING) + 1):
            for dead in itertools.combinations(EVERYTHING, n):
                with self.subTest(dead=dead):
                    c = Chain().arm(throttle=0.6).fly(1.0)
                    c.stop(*dead).fly(1.0)
                    w = c.wire()
                    self.assertEqual(sorted(w["sensor_faults"]), sorted(dead))
                    self.assertNoContradiction(w)
                    self.assertNavSurvived(c)

    def test_a_healthy_part_never_appears(self):
        c = Chain().arm(throttle=0.6).fly(1.0).kill("bno085").fly(1.0)
        self.assertEqual(c.wire()["sensor_faults"], ["bno085"])

    def test_a_dead_part_does_not_blank_its_neighbours(self):
        # Per-signal honesty, not a subsystem blackout: the depth sensor going quiet
        # must not take the compass and the pack monitor with it, or the operator
        # loses three readings to one fault and cannot tell which one broke.
        c = Chain().arm(throttle=0.6).fly(1.0).kill("ms5837").fly(1.0)
        w = c.wire()
        self.assertIsNone(w["depth"])
        self.assertIsNotNone(w["heading"])
        self.assertIsNotNone(w["mag_cal"])
        self.assertIsNotNone(w["current_a"])
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertGreaterEqual(w["signal"], 0)

    def test_the_names_are_the_ones_a_human_will_go_and_unplug(self):
        # The console prints these verbatim. The I2C ones are bus designations
        # matching docs/hardware.md and the wiring diagram; a rename here is a console
        # naming a part that is not on the vehicle.
        self.assertEqual(set(I2C_CHIPS), {"bno085", "ina219", "ms5837"})
        self.assertEqual(set(SUBSYSTEMS), {"leak-probes", "sensor-thread"})
        self.assertEqual(
            set(I2C_CHIPS) & set(SUBSYSTEMS),
            set(),
            "a name in both vocabularies would make 'is this a part or a " "loop?' unanswerable from the console",
        )
        for name in EVERYTHING:
            self.assertEqual(name, name.strip().lower())


# ---------------------------------------------------------------------------
# Recovery — the half of the contract that gets skipped
# ---------------------------------------------------------------------------
class RecoveryTest(ChainTestCase):
    """A gauge that goes blank and STAYS blank after the connector is pushed back on
    is its own fault, and one nobody notices until a dive. Recovery is also where a
    cached value would betray itself: the reading has to resume from the CURRENT
    truth, never from the number that was frozen when the part died."""

    def test_the_depth_comes_back_and_comes_back_current(self):
        c = Chain()
        c.hw.ballast_home()
        c.ballast("fill").fly(2.0)
        before = c.frame().depth
        self.assertIsNotNone(before)
        c.kill("ms5837").fly(2.0)
        self.assertIsNone(c.frame().depth)
        self.assertEqual(c.wire()["sensor_faults"], ["ms5837"])
        c.revive("ms5837").fly(0.5)
        after = c.frame().depth
        self.assertIsNotNone(after, "a reseated connector must un-blank the gauge")
        self.assertEqual(c.wire()["sensor_faults"], [])
        self.assertGreater(
            after,
            before,
            "the sub went on sinking while the sensor was dead, so the "
            "reading that returns must be the water now, not the water "
            "it last managed to measure",
        )

    def test_the_compass_comes_back_and_comes_back_current(self):
        c = Chain().arm(steer=1.0).fly(2.0)
        before = c.frame().heading
        self.assertIsNotNone(before)
        c.kill("bno085").fly(2.0)
        self.assertIsNone(c.frame().heading)
        c.revive("bno085").fly(0.5)
        tel = c.frame()
        self.assertIsNotNone(tel.heading, "a compass that answers again must be read again")
        self.assertIsNotNone(tel.heading_card)
        self.assertIsNotNone(tel.mag_cal)
        self.assertNotEqual(
            tel.heading,
            before,
            "the hull kept turning while the IMU was dead; the bearing " "that returns is the one it is on now",
        )
        self.assertEqual(c.wire()["sensor_faults"], [])

    def test_the_pack_monitor_comes_back(self):
        c = Chain().arm(throttle=0.5).fly(1.0)
        c.kill("ina219").fly(1.0)
        self.assertIsNone(c.wire()["current_a"])
        c.revive("ina219").fly(0.5)
        w = c.wire()
        self.assertIsNotNone(w["current_a"])
        self.assertIsNotNone(w["battery_v"])
        self.assertEqual(w["sensor_faults"], [])

    def test_the_track_starts_advancing_again_once_the_compass_returns(self):
        c = Chain().arm(throttle=1.0).fly(2.0)
        c.kill("bno085").fly(2.0)
        frozen = c.nav()
        c.revive("bno085").fly(2.0)
        moving = c.nav()
        self.assertIsNotNone(moving)
        if frozen is not None:
            self.assertNotEqual(
                (moving.x_m, moving.y_m),
                (frozen.x_m, frozen.y_m),
                "navigation must resume, not stay latched at the " "position it held when the compass died",
            )

    def test_everything_comes_back_at_once(self):
        c = Chain().arm(throttle=1.0).fly(1.0)
        c.stop(*EVERYTHING).fly(1.0)
        self.assertEqual(sorted(c.wire()["sensor_faults"]), sorted(EVERYTHING))
        c.start(*EVERYTHING).fly(1.0)
        w = c.wire()
        self.assertEqual(w["sensor_faults"], [])
        self.assertNavSurvived(c)
        for field in ("depth", "pressure", "heading", "heading_card", "mag_cal", "battery_v", "current_a"):
            self.assertIsNotNone(w[field], f"{field} stayed blank after everything " f"started answering again")
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertGreaterEqual(w["signal"], 0)


# ---------------------------------------------------------------------------
# The whole I2C bus, corroded through
# ---------------------------------------------------------------------------
class BusFailureTest(ChainTestCase):
    """One corroded loom takes every chip on it. The frame must be blank and say so —
    not blank and silent, and above all not full of confident zeros. It must also
    still carry the things that are NOT on that bus, because a blackout is not honesty
    either: the leak probes are GPIO, and blanking them would hide a flood."""

    def setUp(self):
        self.c = Chain().arm(throttle=1.0).fly(2.0)
        self.c.kill(*I2C_CHIPS).fly(2.0)

    def test_no_reading_from_a_dead_chip_survives_anywhere_on_the_chain(self):
        w = self.c.wire()
        for chip, fields in _NULL_FIELDS.items():
            for field in fields:
                self.assertCannotTell(w[field], field, f"the wire ({chip} is dead)")

    def test_the_sensor_sample_invents_nothing(self):
        s = self.c.sample()
        if s is not None:
            for chip, fields in _SAMPLE_FIELDS.items():
                for field in fields:
                    self.assertCannotTell(getattr(s, field), field, f"SensorSample ({chip} is dead)")

    def test_the_nav_state_invents_nothing(self):
        ns = self.c.nav()
        if ns is not None:
            for chip, fields in _NAV_FIELDS.items():
                for field in fields:
                    self.assertCannotTell(getattr(ns, field), field, f"NavState ({chip} is dead)")

    def test_all_three_chips_are_named(self):
        self.assertEqual(sorted(self.c.wire()["sensor_faults"]), sorted(I2C_CHIPS))

    def test_the_frame_still_carries_what_is_not_on_that_bus(self):
        w = self.c.wire()
        self.assertEqual(w["leak_state"], "NORMAL")
        self.assertGreaterEqual(w["signal"], 0)
        self.assertIsNotNone(w["armed"])
        self.assertIsNotNone(w["left"])
        self.assertIsNotNone(w["right"])
        self.assertIs(w["mock"], True)

    def test_a_dead_bus_does_not_take_navigation_down(self):
        self.assertNavSurvived(self.c)


if __name__ == "__main__":
    unittest.main()

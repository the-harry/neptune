"""Telemetry-layer unit tests — what the vehicle is allowed to claim topside.

Run:  cd api && python -m unittest tests.test_telemetry -v
      (needs pydantic; protocol.py is the WebSocket contract and cannot be
       imported without it. The API cannot run without it either.)

This suite guards the honesty rules at the point where they are easiest to break
by accident: the moment a hardware reading becomes a number on somebody's screen.
The specific failures it exists to prevent:

  * an un-homed ballast arriving as 0.0 instead of null. `round(level or 0.0, 3)`
    is a one-character-looking fix for a TypeError and it converts "this stepper
    has never been homed" into "the syringe is empty" — a claim about buoyancy
    that an operator will dive on. This is the single easiest thing in the whole
    change to get silently wrong, so it is tested from several directions.
  * the old single-bit leak alarm losing WARN, or gaining a FLOOD that fires
    repeatedly and trains the operator to ignore it.
  * a new field going out with a healthy-looking default instead of cannot-tell.
  * the 24 V battery scale surviving anywhere. It described a vehicle that was
    never built; a threshold from it reads "full" forever on the 2S pack.

stdlib unittest only — no pytest, matching the client suite's no-framework ethos.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from config import settings
from hardware import HardwareBase, MockHardware, Which
from protocol import Telemetry
from rov import RovState

_API_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_DIR.parent


class SilentHardware(HardwareBase):
    """A backend with nothing but the mandatory actuators wired.

    Two jobs. First it proves the ten new readbacks really are CONCRETE on the
    ABC: if any of them were made @abstractmethod, this class would refuse to
    instantiate and every existing backend would have broken the same way.
    Second, it is the "no IMU, no current sense, never homed" vehicle — the shape
    telemetry has to render as cannot-tell rather than as a healthy zero.
    """

    def set_armed(self, on: bool) -> None: ...
    def set_thrusters(self, left: float, right: float) -> None: ...
    def set_camera(self, pan: float, tilt: float) -> None: ...
    def ballast_pump(self, direction: str) -> None: ...
    def ballast_home(self) -> None: ...
    def set_magnet(self, on: bool) -> None: ...
    def set_light(self, which: Which, on: bool) -> None: ...
    def set_light_level(self, which: Which, level: float) -> None: ...
    def release_dropweight(self) -> None: ...

    def get_magnet(self) -> bool:
        return False

    def get_light(self, which: Which) -> tuple[bool, float]:
        return (False, 0.0)

    def get_ballast_level(self) -> float | None:
        return None

    def read_pressure(self) -> float:
        return settings.surface_pressure_psi

    def read_heading(self) -> float:
        return 0.0

    def read_leak(self) -> str:
        return "NORMAL"

    def read_voltage(self) -> float:
        return 0.0

    def link_quality(self) -> int:
        return 4


def frame(hw: HardwareBase) -> Telemetry:
    """One telemetry snapshot straight off a backend, exactly as the control loop
    builds it (no Pi metrics — those are sysinfo's and are tested elsewhere)."""
    return RovState(hw).telemetry({})


# Every field Telemetry requires. Used to prove the NEW fields are all optional:
# an old client, or a frame that genuinely cannot know, must still validate.
MINIMUM_FRAME = dict(
    armed=False,
    left=0.0,
    right=0.0,
    ballast_target=0.0,
    depth=0.0,
    pressure=14.7,
    heading=0.0,
    heading_card="N",
    magnet=False,
    light_green=False,
    light_white=False,
    light_green_level=0.0,
    light_white_level=0.0,
    leak=False,
    leak_state="NORMAL",
    battery_v=12.4,
    signal=4,
    mock=True,
)


# ---------------------------------------------------------------------------
# Ballast honesty — the headline rule
# ---------------------------------------------------------------------------
class UnhomedBallastTest(unittest.TestCase):
    def test_an_unhomed_ballast_reaches_telemetry_as_none(self):
        hw = MockHardware()
        self.assertIsNone(hw.get_ballast_level())
        tel = frame(hw)
        self.assertIsNone(tel.ballast_level)

    def test_it_is_not_the_zero_that_would_mean_an_empty_syringe(self):
        # Written as its own test because this is the exact regression: 0.0 is not
        # a placeholder here, it is the specific assertion "empty, positively
        # buoyant, safe to dive". round(None) raises, and the tempting fix
        # `round(level or 0.0, 3)` silently makes that assertion instead.
        tel = frame(MockHardware())
        self.assertIsNot(tel.ballast_level, 0.0)
        self.assertNotEqual(tel.ballast_level, 0.0)
        self.assertNotEqual(tel.ballast_level, 0.5)

    def test_the_json_on_the_wire_carries_null(self):
        # The client branches on `=== null` to draw the hatched cannot-tell
        # syringe. A 0 that survives pydantic but appears as 0 in the JSON is
        # still a lie by the time it is a glyph.
        wire = json.loads(frame(MockHardware()).model_dump_json())
        self.assertIsNone(wire["ballast_level"], f"got {wire['ballast_level']!r}")
        self.assertIs(wire["ballast_homed"], False)

    def test_homing_turns_the_unknown_into_a_number(self):
        hw = MockHardware()
        hw.ballast_home()
        tel = frame(hw)
        self.assertEqual(tel.ballast_level, 0.0)
        self.assertTrue(tel.ballast_homed)
        # And 0.0 now MEANS empty, because a limit switch says so.
        self.assertFalse(tel.ballast_needs_rehome)

    def test_the_target_is_empty_when_the_level_is_unknown(self):
        # A target is a COMMAND, not a measurement, so it cannot be None —
        # something has to be commanded from power-on. 0.0 (empty) is the only
        # safe direction: if anything drives toward it, the sub goes up.
        rov = RovState(MockHardware())
        self.assertEqual(rov.ballast_target, 0.0)

    def test_hold_with_an_unknown_level_does_not_clobber_the_target(self):
        # "Hold" means "the target is wherever you are now", and with no homing
        # there is no "now". Writing 0.0 here would turn a stop request into a
        # command to empty the ballast.
        from protocol import BallastMsg

        rov = RovState(MockHardware())
        rov.apply_ballast(BallastMsg(type="ballast", cmd="fill"))
        self.assertEqual(rov.ballast_target, 1.0)
        rov.apply_ballast(BallastMsg(type="ballast", cmd="hold"))
        self.assertEqual(rov.ballast_target, 1.0)

    def test_a_skipped_step_event_reaches_telemetry_rather_than_being_swallowed(self):
        hw = MockHardware()
        hw.ballast_home()
        hw._force_skipped_steps(int(0.10 * settings.ballast_span_steps))
        hw.ballast_pump("fill")
        with self.assertLogs("neptune.hw", level="WARNING"):
            for _ in range(300):
                hw.update(0.05)
        hw.ballast_pump("hold")
        tel = frame(hw)
        self.assertTrue(tel.ballast_needs_rehome, "a counter known to be wrong must say so topside")
        self.assertTrue(tel.ballast_homed)


# ---------------------------------------------------------------------------
# Leak
# ---------------------------------------------------------------------------
class LeakTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.hw = MockHardware()

    def test_the_leak_bool_is_true_for_warn(self):
        # The old single bit must keep meaning "there is water in the hull". A
        # client that only knows the bool is still a client that must be warned.
        self.hw._set_leak("WARN")
        tel = frame(self.hw)
        self.assertTrue(tel.leak)
        self.assertEqual(tel.leak_state, "WARN")

    def test_the_leak_bool_is_true_for_flood(self):
        self.hw._set_leak("FLOOD")
        tel = frame(self.hw)
        self.assertTrue(tel.leak)
        self.assertEqual(tel.leak_state, "FLOOD")

    def test_a_dry_hull_reports_neither(self):
        tel = frame(self.hw)
        self.assertFalse(tel.leak)
        self.assertEqual(tel.leak_state, "NORMAL")

    def test_the_bool_and_the_stage_never_disagree(self):
        for state in ("NORMAL", "WARN", "FLOOD"):
            self.hw._set_leak(state)
            tel = frame(self.hw)
            self.assertEqual(tel.leak, state != "NORMAL", state)
            self.assertEqual(tel.leak_state, state)

    def test_a_probe_fault_reaches_telemetry_in_the_agreed_vocabulary(self):
        # A dead probe reads dry forever. The client renders this string, so it
        # has to be one of the three the contract names.
        self.hw._set_probe_wet(warn=False, flood=True)
        self.assertEqual(frame(self.hw).leak_probe_fault, "warn+flood")
        self.hw._set_probe_wet(warn=False, flood=False)
        self.assertIsNone(frame(self.hw).leak_probe_fault)


class LeakAlarmEdgeTest(unittest.TestCase):
    """Alarms fire on a RISE. Level-triggering puts telemetry_hz alarm frames a
    second on the socket the operator is trying to read."""

    def setUp(self):
        self.rov = RovState(MockHardware())

    def test_a_dry_hull_raises_nothing(self):
        self.assertEqual(self.rov.leak_alarm_edges("NORMAL"), [])

    def test_the_warn_alarm_fires_once_on_the_rising_edge(self):
        self.assertEqual(self.rov.leak_alarm_edges("WARN"), ["leak_warn"])
        self.assertEqual(self.rov.leak_alarm_edges("WARN"), [])

    def test_water_rising_from_warn_to_flood_is_a_new_alarm(self):
        self.rov.leak_alarm_edges("WARN")
        self.assertEqual(self.rov.leak_alarm_edges("FLOOD"), ["leak_flood"])

    def test_a_jump_straight_to_flood_announces_flood_only(self):
        # Stacking two alarms on one event is how an operator misses the real one.
        self.assertEqual(self.rov.leak_alarm_edges("FLOOD"), ["leak_flood"])

    def test_water_receding_from_flood_to_warn_is_not_a_new_emergency(self):
        self.rov.leak_alarm_edges("FLOOD")
        self.assertEqual(self.rov.leak_alarm_edges("WARN"), [])

    def test_drying_all_the_way_out_re_arms_the_alarm(self):
        self.rov.leak_alarm_edges("WARN")
        self.rov.leak_alarm_edges("NORMAL")
        self.assertEqual(self.rov.leak_alarm_edges("WARN"), ["leak_warn"])


# ---------------------------------------------------------------------------
# The new fields
# ---------------------------------------------------------------------------
class NewFieldTest(unittest.TestCase):
    NEW_FIELDS = (
        "ballast_homed",
        "ballast_needs_rehome",
        "speed_ms",
        "speed_src",
        "snagged",
        "gyro_only",
        "mag_cal",
        "current_a",
        "leak_probe_fault",
    )

    def test_every_new_field_exists_on_the_contract(self):
        # A rename here is a field the client silently stops receiving.
        missing = [f for f in self.NEW_FIELDS if f not in Telemetry.model_fields]
        self.assertEqual(missing, [])

    def test_a_frame_that_cannot_know_anything_still_validates(self):
        # Every new field carries a default, including ballast_level, so an
        # old-shaped frame is not a validation error at the worst moment.
        tel = Telemetry(**MINIMUM_FRAME)
        self.assertIsNone(tel.ballast_level)

    def test_the_defaults_are_cannot_tell_and_not_healthy_values(self):
        tel = Telemetry(**MINIMUM_FRAME)
        self.assertFalse(tel.ballast_homed)
        self.assertFalse(tel.ballast_needs_rehome)
        self.assertIsNone(tel.speed_ms)
        self.assertIsNone(tel.speed_src)
        self.assertFalse(tel.snagged)
        self.assertFalse(tel.gyro_only)
        self.assertIsNone(tel.mag_cal)
        self.assertIsNone(tel.current_a)
        self.assertIsNone(tel.leak_probe_fault)

    def test_a_backend_with_only_the_abstract_methods_still_constructs(self):
        # If any of the ten optional readbacks were @abstractmethod, this raises
        # TypeError and every backend that had not caught up would be broken —
        # which is exactly the pressure that produces a plausible-looking stub.
        SilentHardware()

    def test_a_vehicle_with_no_sensors_reports_cannot_tell_everywhere(self):
        tel = frame(SilentHardware())
        self.assertIsNone(tel.ballast_level)
        self.assertFalse(tel.ballast_homed)
        self.assertIsNone(tel.current_a)
        self.assertIsNone(tel.leak_probe_fault)
        self.assertIsNone(tel.mag_cal)

    def test_no_imu_is_reported_differently_from_an_uncalibrated_one(self):
        # "The compass says don't trust the heading" and "there is no compass"
        # lead an operator to do different things. The ABC's cannot-tell for
        # mag_cal is the integer 0, which is also a real reading, so telemetry is
        # the last place in the stack where the two can still be told apart.
        self.assertIsNone(frame(SilentHardware()).mag_cal)
        hw = MockHardware()
        hw._set_mag_cal(0)
        self.assertEqual(frame(hw).mag_cal, 0)
        hw._set_mag_cal(3)
        self.assertEqual(frame(hw).mag_cal, 3)

    def test_the_estimator_fields_are_left_for_navigation_to_fill(self):
        # speed / speed_src / snagged / gyro_only are the FILTER's answers, not
        # the hardware's: the paddlewheel yields an unsigned magnitude and only
        # the estimator decides what it means. RovState must not invent them —
        # a 0.0 here would claim the sub is stationary.
        tel = frame(MockHardware())
        self.assertIsNone(tel.speed_ms)
        self.assertIsNone(tel.speed_src)
        self.assertFalse(tel.snagged)
        self.assertFalse(tel.gyro_only)

    def test_the_pack_current_reaches_telemetry_from_a_backend_that_measures_it(self):
        self.assertIsNotNone(frame(MockHardware()).current_a)

    def test_a_bench_frame_is_flagged_as_simulated(self):
        # Everything the operator is allowed to believe hangs off this bit.
        self.assertTrue(frame(MockHardware()).mock)


# ---------------------------------------------------------------------------
# The pack is 3S and both dead scales stay dead (24 V once, 2S now — R7.4.1)
# ---------------------------------------------------------------------------
def load_battery_band():
    """Compile just `battery_band` out of api/main.py.

    The band is the ONLY source of the voltage's colour, so it is tested against
    the shipped source rather than against a copy of the rule. It cannot simply be
    imported: `import main` builds the camera and nav services and constructs a
    BlackBox, which opens a fresh session .jsonl on disk and rewrites
    current.jsonl. A unit test that leaves a dive log behind every time it runs is
    a unit test that gets deleted, so the module is parsed and exactly one
    function is compiled out of it.
    """
    src = (_API_DIR / "main.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "battery_band":
            ns: dict = {"settings": settings}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(_API_DIR / "main.py"), "exec"), ns)  # noqa: S102
            return ns["battery_band"]
    raise AssertionError(
        "api/main.py no longer defines battery_band() — the banding rule has "
        "moved and this test must follow it rather than quietly stop checking."
    )


class BatteryBandTest(unittest.TestCase):
    def setUp(self):
        self.band = load_battery_band()

    def test_the_thresholds_are_the_3s_packs(self):
        # The pack actually fitted (2026-08-18): 3S3P INR18650. Same per-cell
        # judgements the 2S bands carried — 4.2 / 3.5 / 3.3 / 3.0 V/cell —
        # three cells now (docs/hardware.md §7).
        self.assertEqual(settings.battery_full_v, 12.6)
        self.assertEqual(settings.battery_warn_v, 10.5)
        self.assertEqual(settings.battery_crit_v, 9.9)
        self.assertEqual(settings.battery_floor_v, 9.0)

    def test_a_full_pack_is_green(self):
        self.assertEqual(self.band(12.6), "ok")
        self.assertEqual(self.band(11.1), "ok")

    def test_exactly_the_warn_threshold_is_still_green(self):
        # "green >= 10.5" — the boundary belongs to the good band. A `<=` here
        # turns a healthy pack amber and teaches the operator to ignore amber.
        self.assertEqual(self.band(10.5), "ok")

    def test_just_under_the_warn_threshold_is_amber(self):
        self.assertEqual(self.band(10.49), "warn")

    def test_exactly_the_critical_threshold_is_still_amber(self):
        # "red < 9.9": 9.9 itself has not crossed.
        self.assertEqual(self.band(9.9), "warn")

    def test_under_the_critical_threshold_is_red(self):
        self.assertEqual(self.band(9.89), "critical")

    def test_the_hard_floor_is_deep_inside_the_red_band(self):
        # 9.0 V is 3.0 V/cell — below it the cells are damaged, not merely flat.
        # Nothing enforces it in software, so it had better be shouting long
        # before the operator arrives there.
        self.assertEqual(self.band(settings.battery_floor_v), "critical")

    def test_a_24v_reading_is_not_special_cased_into_health(self):
        # If anything ever "helpfully" rescaled, this is where it would show.
        self.assertEqual(
            self.band(24.8),
            "ok",
            "24.8 V bands as a healthy 3S pack — which is exactly " "why leaving that number anywhere is dangerous",
        )

    def test_a_2s_reading_cannot_look_healthy_on_the_3s_bands(self):
        # The other direction of the same trap: a full 2S pack (8.4 V) on the
        # 3S scale is below the hard floor. If a stale 2S fixture or mock ever
        # leaks back in, it must scream, not read as a slightly tired pack.
        self.assertEqual(self.band(8.4), "critical")

    def test_the_bench_vehicle_reports_a_3s_voltage(self):
        v = frame(MockHardware()).battery_v
        self.assertLessEqual(v, settings.battery_full_v)
        self.assertGreater(v, settings.battery_floor_v)


class NoDeadVoltageScaleTest(unittest.TestCase):
    """The brief, executed twice now: purge every dead pack scale — mock,
    config, tests, client. The 24 V scale died when the 2S pack arrived; the 2S
    scale died on 2026-08-18 when the 3S pack was bought (R7.4.1). A threshold
    describing a different vehicle does not fail loudly — it reads "full"
    forever (24 V) or "flat" forever (2S) — so both corridors are policed."""

    # Identifiers that name a PACK VOLTAGE, followed by a literal. Prose and
    # comments about the old scales are deliberately not matched: the repo is
    # full of writing that explains why they are gone, and that writing is the
    # point.
    _BATTERY_LITERAL = re.compile(
        r"\b(battery_v|batteryV|battery_volts|batt_v|_voltage|fullV|warnV|critV|floorV)"
        r"\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\b"
    )
    _SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
    _EXTENSIONS = {".py", ".js", ".html", ".json", ".css", ".md"}
    # A 3S pack lives between 9.0 (floor) and 12.6 (full). Above this is the
    # 24 V scale's territory (24.8 start, 24.5 fixture, 20.0 floor all clear it).
    _ABOVE_3S_V = 13.0
    # ...and the 2S corridor sits wholly below the 3S floor: 8.4 full, 8.3/8.1
    # fixtures, 7.0 warn, 6.6 crit, 6.0 floor. The lower bound keeps per-cell
    # figures (3.x V) out of a pack-voltage police report.
    _DEAD_2S_HI = 9.0
    _DEAD_2S_LO = 4.0

    def test_no_battery_voltage_literal_is_on_a_dead_scale(self):
        offenders = []
        for path in sorted(_REPO_ROOT.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self._EXTENSIONS:
                continue
            if self._SKIP_DIRS.intersection(path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                for match in self._BATTERY_LITERAL.finditer(line):
                    volts = float(match.group(2))
                    dead_24 = volts > self._ABOVE_3S_V
                    dead_2s = self._DEAD_2S_LO < volts < self._DEAD_2S_HI
                    if dead_24 or dead_2s:
                        rel = path.relative_to(_REPO_ROOT).as_posix()
                        scale = "24V" if dead_24 else "2S"
                        offenders.append(f"{rel}:{lineno}  {match.group(1)}={volts} [{scale}]  |  {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "\n".join(
                ["battery voltage literals on a dead scale " f"(the 3S pack is {settings.battery_full_v} V full):"]
                + offenders
            ),
        )


if __name__ == "__main__":
    unittest.main()

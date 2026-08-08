"""Hardware-layer unit tests — the honesty rules, exercised on the bench.

Run:  cd api && python -m unittest tests.test_hardware -v

These guard §2 of the hardware brief. Every test here is named for the FAILURE it
prevents, because the failures in this layer are the quiet kind: a leak alarm that
fires on condensation (and is therefore ignored when it matters), a syringe that
reports "empty" for a plunger nobody has ever located, a paddlewheel that reports
0.0 m/s when what it means is "I have no idea", a battery gauge still scaled for a
24 V pack that was never built. None of those look broken on a dashboard. All of
them lose a sub.

Almost everything here runs against MODULE-SCOPE logic (LeakDebouncer, PaddleWheel,
BallastAxis, QuadratureDecoder, thruster_duty, leak_probe_fault_from) or against
MockHardware, and that is deliberate: RealHardware cannot be constructed on a
laptop, so any rule locked inside it is a rule no test can ever run. The shared
helpers are the code the Pi actually executes, so testing them here tests the
vehicle and not a stand-in.

stdlib unittest only — no pytest, matching the client suite's no-framework ethos.
"""
from __future__ import annotations

import contextlib
import dataclasses
import logging
import unittest
from unittest import mock

import hardware
from config import settings
from hardware import (
    BallastAxis,
    HardwareBase,
    LeakDebouncer,
    MockHardware,
    PaddleWheel,
    QuadratureDecoder,
    RealHardware,
    get_hardware,
    leak_probe_fault_from,
    thruster_duty,
)
from nav.config import settings as nav_settings


def advance(hw: MockHardware, seconds: float, dt: float = 0.05) -> None:
    """Run the mock's SIMULATED clock forward. Never sleeps — the mock's update()
    takes dt rather than reading the wall clock precisely so a test can fly ten
    minutes of dive in a millisecond."""
    for _ in range(int(round(seconds / dt))):
        hw.update(dt)


@contextlib.contextmanager
def quiet_hw_log():
    """Swallow the hardware layer's fallback warning for one call.

    get_hardware() announces the fall back to the mock LOUDLY, which is right and
    is asserted on its own below. Here it is only noise, and a suite whose normal
    output carries warnings is a suite whose real warnings get skimmed past.
    """
    logger = logging.getLogger("neptune.hw")
    prev = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prev)


# ---------------------------------------------------------------------------
# Leak debounce
# ---------------------------------------------------------------------------
class LeakDebounceTest(unittest.TestCase):
    """A splash must not raise the alarm; water must."""

    def test_four_consecutive_wet_samples_latch_nothing(self):
        # The exact off-by-one that would make the debounce decorative: a droplet
        # running down the hull can wet a probe for four samples (0.4 s) easily.
        d = LeakDebouncer(5)
        for _ in range(4):
            self.assertFalse(d.sample(True))
        self.assertFalse(d.latched)

    def test_the_fifth_consecutive_wet_sample_latches(self):
        d = LeakDebouncer(5)
        for _ in range(4):
            d.sample(True)
        self.assertTrue(d.sample(True))
        self.assertTrue(d.latched)

    def test_one_dry_sample_restarts_the_count(self):
        # Four wet, one dry, four wet is EIGHT wet samples and still not a leak.
        # If the run counter did not reset, intermittent condensation would latch
        # given enough time, and an alarm that eventually always fires is an alarm
        # the operator stops reading.
        d = LeakDebouncer(5)
        for _ in range(4):
            d.sample(True)
        d.sample(False)
        for _ in range(4):
            self.assertFalse(d.sample(True))
        self.assertTrue(d.sample(True))

    def test_the_latch_survives_the_probe_drying_out(self):
        # One-way on purpose: the sub tilts, the drop rolls off the probe, and the
        # hull is no more sound than it was a second ago. Only a human with the
        # hull open clears this.
        d = LeakDebouncer(5)
        for _ in range(5):
            d.sample(True)
        for _ in range(50):
            self.assertTrue(d.sample(False))
        self.assertTrue(d.latched)

    def test_reset_is_the_only_way_back_to_dry(self):
        d = LeakDebouncer(5)
        for _ in range(5):
            d.sample(True)
        d.reset()
        self.assertFalse(d.latched)
        self.assertFalse(d.sample(True))     # and the count starts over from zero

    def test_the_debounce_count_is_the_configured_five_samples(self):
        # 5 samples at the sensor thread's 10 Hz leak tick ≈ 0.5 s, which is the
        # number the brief and docs/hardware.md both quote. If this drifts, every
        # "~0.5 s" comment in the repo becomes a lie.
        self.assertEqual(settings.leak_debounce_samples, 5)

    def test_a_debouncer_configured_below_one_sample_still_debounces(self):
        # A zero or negative NEPTUNE_LEAK_DEBOUNCE must not mean "latch on the
        # first splash forever" — it clamps to one deliberate sample.
        self.assertEqual(LeakDebouncer(0).samples, 1)
        self.assertEqual(LeakDebouncer(-3).samples, 1)


class LeakStageTest(unittest.TestCase):
    """WARN and FLOOD are two probes answering two questions."""

    def setUp(self):
        self.hw = MockHardware()

    def test_warn_and_flood_debounce_independently(self):
        # Chaining them would make a FLOOD conditional on the WARN probe still
        # working — and the WARN probe is the one sitting at the lowest point of
        # the hull, i.e. the one most likely to be underwater and corroded.
        warn, flood = LeakDebouncer(5), LeakDebouncer(5)
        for _ in range(5):
            warn.sample(True)
            flood.sample(False)
        self.assertTrue(warn.latched)
        self.assertFalse(flood.latched)
        for _ in range(5):
            warn.sample(False)
            flood.sample(True)
        self.assertTrue(flood.latched)      # rose on its own evidence
        self.assertTrue(warn.latched)       # and did not clear the other

    def test_a_dry_hull_reports_normal(self):
        self.assertEqual(self.hw.read_leak(), "NORMAL")

    def test_the_low_probe_alone_reports_warn(self):
        self.hw._set_probe_wet(warn=True, flood=False)
        self.assertEqual(self.hw.read_leak(), "WARN")

    def test_flood_wins_when_the_water_rises_through_warn(self):
        self.hw._set_probe_wet(warn=True, flood=False)
        self.assertEqual(self.hw.read_leak(), "WARN")
        self.hw._set_probe_wet(warn=True, flood=True)
        self.assertEqual(self.hw.read_leak(), "FLOOD")

    def test_flood_wins_even_when_the_warn_probe_never_answered(self):
        # Order-independent by construction: the upper probe being wet says the
        # water is past the point where "finish up" was the right advice, whatever
        # the lower probe claims. A FLOOD gated on WARN would be silenced by
        # exactly the failure most likely to happen (the lower probe dies first).
        self.hw._set_probe_wet(warn=False, flood=True)
        self.assertEqual(self.hw.read_leak(), "FLOOD")

    def test_the_stage_hook_drives_both_probes_consistently(self):
        for state in ("NORMAL", "WARN", "FLOOD"):
            self.hw._set_leak(state)
            self.assertEqual(self.hw.read_leak(), state)


class LeakProbeFaultTest(unittest.TestCase):
    """A dead probe reads dry forever — the one failure the design would hide."""

    def test_both_probes_sane_reports_no_fault(self):
        self.assertIsNone(leak_probe_fault_from(False, False))
        self.assertIsNone(leak_probe_fault_from(True, False))
        self.assertIsNone(leak_probe_fault_from(True, True))

    def test_the_upper_probe_wet_while_the_lower_is_dry_is_impossible(self):
        # The flood probe sits ~2 cm ABOVE the warn probe. Water cannot reach the
        # upper without covering the lower, so this is not a leak pattern, it is a
        # broken probe. One bit each cannot say which, so both are named rather
        # than sending the operator to strip the wrong one.
        self.assertEqual(leak_probe_fault_from(False, True), "warn+flood")

    def test_a_probe_wet_at_power_on_is_reported_all_session(self):
        # The hull is sealed dry on the bench and then powered up. Already-wet at
        # boot is a short, corrosion, or a sub that flooded before launch.
        self.assertEqual(leak_probe_fault_from(False, False, warn_wet_at_boot=True), "warn")
        self.assertEqual(leak_probe_fault_from(False, False, flood_wet_at_boot=True), "flood")
        self.assertEqual(
            leak_probe_fault_from(False, False, warn_wet_at_boot=True, flood_wet_at_boot=True),
            "warn+flood")

    def test_the_fault_vocabulary_is_exactly_the_three_agreed_strings(self):
        # The client renders this string; anything outside the contract's
        # vocabulary lands on the dashboard as gibberish next to a leak icon.
        seen = {
            leak_probe_fault_from(False, True),
            leak_probe_fault_from(False, False, True, False),
            leak_probe_fault_from(False, False, False, True),
        }
        self.assertEqual(seen, {"warn", "flood", "warn+flood"})

    def test_the_mock_runs_the_same_verdict_the_pi_runs(self):
        hw = MockHardware()
        self.assertIsNone(hw.leak_probe_fault())
        hw._set_probe_wet(warn=False, flood=True)
        self.assertEqual(hw.leak_probe_fault(), "warn+flood")
        hw._set_probe_wet(warn=False, flood=False)
        hw._set_probe_wet_at_boot(warn=True, flood=False)
        self.assertEqual(hw.leak_probe_fault(), "warn")


class LeakRearmTest(unittest.TestCase):
    """Re-arming the detector: the way back from a one-way latch.

    A latch that could only be cleared by restarting the service meant a bench
    test left the console alarming for the rest of the session, and on the water
    the cure was SSH-ing into a submarine. The way back exists — but it clears the
    MEMORY of water and never water that is present, and that distinction is the
    only thing keeping it from being a button that dismisses a flood.
    """

    def setUp(self):
        self.hw = MockHardware()

    def test_a_wet_probe_refuses_the_rearm_and_says_which(self):
        """THE GUARD. Without it this is a dismiss-the-alarm button."""
        self.hw._set_probe_wet(warn=True, flood=False)
        got = self.hw.reset_leak_latches()
        self.assertFalse(got["ok"],
                         f"re-arm was ACCEPTED with the warn probe wet — this clears "
                         f"live water, not the memory of it: {got}")
        self.assertIn("warn", got.get("wet_now", []),
                      f"the refusal does not name which probe is wet: {got}")
        self.assertGreater(len(str(got.get("why") or "")), 40,
                           f"a refusal with no sentence is a button that does nothing: {got}")

    def test_a_flood_refuses_too(self):
        self.hw._set_probe_wet(warn=True, flood=True)
        self.assertFalse(self.hw.reset_leak_latches()["ok"],
                         "re-arm accepted DURING A FLOOD")

    def test_the_state_does_not_move_when_the_rearm_is_refused(self):
        """A refused re-arm must change nothing at all — no partial clear."""
        self.hw._set_probe_wet(warn=True, flood=False)
        before = self.hw.read_leak()
        self.hw.reset_leak_latches()
        self.assertEqual(self.hw.read_leak(), before,
                         "a REFUSED re-arm still moved the leak state")

    def test_a_dry_probe_wet_at_boot_can_be_rearmed_back_to_normal(self):
        """The case that sent us here: powering up with a wet probe pins the
        console on UNKNOWN for the whole session, because a probe wet in a hull
        sealed dry cannot certify anything. Drying it and re-arming is exactly the
        human inspection the latch was waiting for."""
        self.hw._set_probe_wet_at_boot(warn=True, flood=False)
        self.assertEqual(self.hw.read_leak(), "UNKNOWN",
                         "a probe wet at boot should not be certifying the hull dry")
        got = self.hw.reset_leak_latches()
        self.assertTrue(got["ok"], f"re-arm refused with both probes dry: {got}")
        self.assertIn("warn-wet-at-boot", got["cleared"],
                      f"the boot verdict was not cleared, so this is still stuck: {got}")
        self.assertEqual(self.hw.read_leak(), "NORMAL",
                         "after a re-arm with both probes dry the hull reads NORMAL")

    def test_rearms_are_counted_so_the_console_can_say_it_happened(self):
        """NORMAL restored by hand and NORMAL never in doubt are different claims."""
        self.assertEqual(self.hw._leak_rearms, 0)
        self.hw.reset_leak_latches()
        self.hw.reset_leak_latches()
        self.assertEqual(self.hw._leak_rearms, 2,
                         "re-arms are not counted, so the console cannot tell an "
                         "operator that the reassurance on screen was restored by hand")

    def test_the_base_class_declines_rather_than_pretending(self):
        """A backend with no latches has nothing to re-arm, and saying so is a real
        answer. Returning ok=True would have the console report a detector re-armed
        on hardware that never had one."""
        got = HardwareBase.reset_leak_latches(self.hw)
        self.assertFalse(got["ok"])
        self.assertTrue(str(got.get("why") or ""))


# ---------------------------------------------------------------------------
# Ballast — an open-loop axis with no position sensor
# ---------------------------------------------------------------------------
class BallastUnknownUntilHomedTest(unittest.TestCase):
    """0.0 is a claim about buoyancy. It must not be made by guessing."""

    def setUp(self):
        self.hw = MockHardware()

    def test_the_level_is_unknown_before_homing(self):
        level = self.hw.get_ballast_level()
        self.assertIsNone(level, "an un-homed stepper must report cannot-tell")
        # Spelled out because these are the two specific wrong answers: 0.0 says
        # "empty, positively buoyant, safe to dive" and 0.5 says "half a tank".
        # Both are inventions about a plunger nothing on the vehicle can see.
        self.assertNotEqual(level, 0.0)
        self.assertNotEqual(level, 0.5)
        self.assertFalse(self.hw.ballast_homed())

    def test_the_sub_can_be_at_depth_while_the_syringe_admits_it_cannot_tell(self):
        # The bench sub boots with the plunger 40 % in, so the water says one thing
        # and the counter says nothing. That combination is the whole point of the
        # unknown state and it must be reachable on the bench.
        self.assertGreater(self.hw.read_pressure(), settings.surface_pressure_psi)
        self.assertIsNone(self.hw.get_ballast_level())

    def test_homing_zeroes_the_counter_and_declares_the_axis_homed(self):
        self.hw.ballast_home()
        self.assertTrue(self.hw.ballast_homed())
        self.assertEqual(self.hw.get_ballast_level(), 0.0)

    def test_an_unhomed_axis_reports_unknown_no_matter_how_many_steps_it_took(self):
        # Steps taken from an unknown start are still an unknown position. Counting
        # from nowhere gets you nowhere.
        axis = BallastAxis(4000, 0.05)
        for _ in range(500):
            axis.try_step(+1, at_empty=False, at_full=False)
        self.assertIsNone(axis.level())

    def test_the_level_is_the_step_count_over_the_configured_span(self):
        axis = BallastAxis(1000, 0.05)
        axis.mark_empty_limit()
        for _ in range(250):
            axis.try_step(+1, at_empty=False, at_full=False)
        self.assertAlmostEqual(axis.level(), 0.25)


class BallastLimitSwitchTest(unittest.TestCase):
    def test_a_closed_switch_refuses_motion_into_it_mid_command(self):
        # The hard rule is evaluated PER STEP, not per command, so a "fill" that
        # was already running when the switch closed still stops. Checking it once
        # at the start of a move is how a plunger gets driven through a hard stop.
        axis = BallastAxis(1000, 0.05)
        axis.mark_empty_limit()
        self.assertFalse(axis.try_step(-1, at_empty=True, at_full=False))
        self.assertTrue(axis.try_step(+1, at_empty=True, at_full=False))
        self.assertFalse(axis.try_step(+1, at_empty=False, at_full=True))
        self.assertTrue(axis.try_step(-1, at_empty=False, at_full=True))

    def test_a_hold_command_takes_no_steps(self):
        axis = BallastAxis(1000, 0.05)
        axis.mark_empty_limit()
        self.assertFalse(axis.try_step(0, at_empty=False, at_full=False))
        self.assertEqual(axis.steps, 0)

    def test_touching_the_empty_stop_is_itself_a_position_fix(self):
        # The switches are the only real position information on this axis, so
        # arriving at one homes it whether or not anyone called it homing.
        axis = BallastAxis(1000, 0.05)
        axis.steps = 812
        axis.mark_empty_limit()
        self.assertTrue(axis.homed)
        self.assertEqual(axis.steps, 0)


class BallastSkippedStepTest(unittest.TestCase):
    """A stepper that stalls under load lies, and nothing can tell until a switch."""

    def test_a_full_switch_beyond_the_tolerance_flags_needs_rehome(self):
        axis = BallastAxis(4000, 0.05)
        axis.mark_empty_limit()
        axis.steps = 4000 + 201        # 5.025 % out — past the 5 % tolerance
        with self.assertLogs("neptune.hw", level="WARNING"):
            self.assertTrue(axis.mark_full_limit())
        self.assertTrue(axis.needs_rehome)

    def test_a_full_switch_exactly_at_the_tolerance_does_not_cry_wolf(self):
        # The rule is "differing by MORE than 5 %". Exactly 5 % must pass, or the
        # flag fires on well-built hardware and gets ignored when it is real.
        axis = BallastAxis(4000, 0.05)
        axis.mark_empty_limit()
        axis.steps = 4000 + 200
        self.assertFalse(axis.mark_full_limit())
        self.assertFalse(axis.needs_rehome)

    def test_the_switch_wins_and_the_counter_is_snapped_to_the_span(self):
        # The switch is real metal; the counter is bookkeeping. Bookkeeping loses.
        axis = BallastAxis(4000, 0.05)
        axis.mark_empty_limit()
        axis.steps = 3000
        with self.assertLogs("neptune.hw", level="WARNING"):
            axis.mark_full_limit()
        self.assertEqual(axis.steps, 4000)
        self.assertEqual(axis.level(), 1.0)

    def test_re_homing_against_real_metal_clears_the_flag(self):
        axis = BallastAxis(4000, 0.05)
        axis.needs_rehome = True
        axis.mark_empty_limit()
        self.assertFalse(axis.needs_rehome)

    def test_the_bench_reproduces_a_skipped_step_end_to_end(self):
        # The documented hook: the driver pulsed and the motor did not move. The
        # lie is undetectable until the FULL switch closes at the wrong count, so
        # the test drives all the way to the stop exactly as an operator would.
        hw = MockHardware()
        hw.ballast_home()
        hw._force_skipped_steps(int(0.10 * settings.ballast_span_steps))
        self.assertFalse(hw.ballast_needs_rehome(), "the lie is invisible until a switch closes")
        hw.ballast_pump("fill")
        with self.assertLogs("neptune.hw", level="WARNING"):
            advance(hw, 15.0)            # 4000 steps at 400 steps/s = 10 s of stroke
        hw.ballast_pump("hold")
        self.assertTrue(hw.ballast_needs_rehome())
        self.assertTrue(hw.ballast_homed())

    def test_a_clean_full_stroke_does_not_raise_the_flag(self):
        # The other half of the same test: if a normal fill trips needs-rehome,
        # the flag is noise and the operator learns to clear it without looking.
        hw = MockHardware()
        hw.ballast_home()
        hw.ballast_pump("fill")
        advance(hw, 15.0)
        hw.ballast_pump("hold")
        self.assertFalse(hw.ballast_needs_rehome())
        self.assertEqual(hw.get_ballast_level(), 1.0)

    def test_a_mismatch_inside_the_tolerance_does_not_raise_the_flag(self):
        hw = MockHardware()
        hw.ballast_home()
        hw._force_skipped_steps(100)     # 2.5 % of the default 4000-step span
        hw.ballast_pump("fill")
        advance(hw, 15.0)
        hw.ballast_pump("hold")
        self.assertFalse(hw.ballast_needs_rehome())

    def test_the_span_and_tolerance_are_the_documented_defaults(self):
        # docs/hardware.md's calibration procedure quotes both of these, and a
        # wrong span silently rescales the entire syringe UI with nothing to see.
        self.assertEqual(settings.ballast_span_steps, 4000)
        self.assertEqual(settings.ballast_span_tolerance, 0.05)
        self.assertEqual(settings.ballast_step_rate, 400.0)


# ---------------------------------------------------------------------------
# Paddlewheel
# ---------------------------------------------------------------------------
class PaddleWheelTest(unittest.TestCase):
    """"No pulses" is not "no speed" — the difference is the snag detector."""

    def wheel(self) -> PaddleWheel:
        return PaddleWheel(m_per_pulse=0.05, window_s=0.5, stale_s=2.0)

    def test_read_returns_a_speed_and_a_freshness_flag(self):
        # The tuple shape is load-bearing: a caller that unpacks only the float
        # gets a confident 0.0 for a wheel that is not fitted.
        got = self.wheel().read(0.0)
        self.assertIsInstance(got, tuple)
        self.assertEqual(len(got), 2)
        speed, fresh = got
        self.assertIsInstance(speed, float)
        self.assertIsInstance(fresh, bool)

    def test_a_wheel_that_has_never_turned_is_not_fresh(self):
        # No wheel fitted at all looks exactly like this, and 0.0 m/s would be a
        # measurement claim from a sensor that does not exist.
        speed, fresh = self.wheel().read(10.0)
        self.assertFalse(fresh)
        self.assertEqual(speed, 0.0)

    def test_silence_past_the_stale_window_stops_being_evidence(self):
        w = self.wheel()
        w.pulse(0.0)
        self.assertTrue(w.read(1.9)[1])
        self.assertFalse(w.read(2.5)[1], "no pulse for >2 s must read cannot-tell")

    def test_zero_pulses_inside_the_stale_window_is_a_measured_zero(self):
        # This is the reading the snag detector lives on: the wheel is fitted, it
        # is talking, and right now it is NOT TURNING. Suppressing it as "stale"
        # would blind the one detector the paddlewheel was bought for.
        w = self.wheel()
        w.pulse(0.0)
        speed, fresh = w.read(1.0)
        self.assertTrue(fresh)
        self.assertEqual(speed, 0.0)

    def test_speed_is_the_pulses_in_the_window_over_the_window(self):
        w = self.wheel()
        for i in range(10):
            w.pulse(0.5 + i * 0.01)      # ten pulses inside one 0.5 s window
        speed, fresh = w.read(0.6)
        self.assertTrue(fresh)
        self.assertAlmostEqual(speed, 10 * 0.05 / 0.5)

    def test_pulses_older_than_the_window_leave_the_average(self):
        # Otherwise speed only ever climbs, and a sub that stopped keeps reporting
        # way on — which is the exact reading that hides a snag.
        w = self.wheel()
        for i in range(10):
            w.pulse(i * 0.01)
        self.assertGreater(w.read(0.2)[0], 0.0)
        w.pulse(1.0)
        speed, fresh = w.read(1.1)
        self.assertTrue(fresh)
        self.assertAlmostEqual(speed, 1 * 0.05 / 0.5)

    def test_the_smallest_reportable_speed_is_the_stall_speed(self):
        # Quantisation is coarse by construction: one pulse in a 0.5 s window at
        # 0.05 m/pulse IS 0.1 m/s, the speed the wheel stalls at. The KF widens R
        # to match; a test pins the arithmetic so that comment stays true.
        w = self.wheel()
        w.pulse(0.0)
        self.assertAlmostEqual(w.read(0.1)[0], 0.1)

    def test_the_calibration_constants_are_navigations_and_not_reinvented(self):
        # metres-per-pulse is a CALIBRATION, measured on a canal run and written
        # into NAV_M_PER_PULSE. Two copies of it drift, and the drift shows up as
        # a dive track that is quietly the wrong length.
        self.assertEqual(nav_settings.m_per_pulse, 0.05)
        self.assertEqual(nav_settings.paddle_window_s, 0.5)
        self.assertEqual(nav_settings.paddle_stale_s, 2.0)


class MockPaddleWheelTest(unittest.TestCase):
    def setUp(self):
        self.hw = MockHardware()
        self.hw.set_armed(True)

    def test_a_sub_below_the_stall_speed_reports_cannot_tell(self):
        # Bearing friction beats the water below ~0.1 m/s. The wheel goes silent,
        # and silence at idle throttle is a stopped sub, not a broken sensor.
        self.hw.set_thrusters(0.05, 0.05)
        advance(self.hw, 3.0)
        speed, fresh = self.hw.read_water_speed()
        self.assertFalse(fresh)
        self.assertEqual(speed, 0.0)

    def test_a_sub_under_way_reports_a_fresh_measured_speed(self):
        self.hw.set_thrusters(1.0, 1.0)
        advance(self.hw, 3.0)
        speed, fresh = self.hw.read_water_speed()
        self.assertTrue(fresh)
        # One pulse of quantisation either side of the modelled 1.0 m/s.
        self.assertGreater(speed, 0.85)
        self.assertLess(speed, 1.15)

    def test_a_jammed_wheel_under_full_thrust_goes_stale(self):
        # The sub is pinned on a shopping trolley: thrusters roaring, wheel still.
        # This is the input the snag detector reads, so the mock must be able to
        # produce it or that detector is never exercised before the canal.
        self.hw.set_thrusters(1.0, 1.0)
        advance(self.hw, 3.0)
        self.assertTrue(self.hw.read_water_speed()[1])
        self.hw._jam_paddle(True)
        advance(self.hw, 3.0)
        speed, fresh = self.hw.read_water_speed()
        self.assertFalse(fresh, "a jammed wheel must not keep reporting the last speed")
        self.assertEqual(speed, 0.0)


# ---------------------------------------------------------------------------
# Thrusters and the spool encoder
# ---------------------------------------------------------------------------
class ThrusterTest(unittest.TestCase):
    def test_a_command_inside_the_deadband_coasts_the_bridge(self):
        # A few percent of duty cannot turn a prop but it does make the H-bridge
        # sing, and a whining idle sounds exactly like a fault to whoever is
        # holding the tether.
        self.assertEqual(thruster_duty(0.04, 0.05), (0, 0, 0.0))
        self.assertEqual(thruster_duty(-0.04, 0.05), (0, 0, 0.0))

    def test_sign_picks_the_direction_pins_and_magnitude_the_duty(self):
        self.assertEqual(thruster_duty(0.5, 0.05), (1, 0, 0.5))
        self.assertEqual(thruster_duty(-0.5, 0.05), (0, 1, 0.5))

    def test_an_out_of_range_command_is_clamped_not_passed_through(self):
        self.assertEqual(thruster_duty(2.0, 0.05), (1, 0, 1.0))
        self.assertEqual(thruster_duty(-2.0, 0.05), (0, 1, 1.0))

    def test_the_deadband_is_the_configured_value(self):
        self.assertEqual(settings.thruster_deadband, 0.05)

    def test_a_disarmed_vehicle_refuses_thrust(self):
        # The mock gates on armed exactly as the real bridges do. Without this the
        # bench sub keeps moving after a disarm and the map quietly disagrees with
        # the vehicle — the failure that makes a simulator worthless.
        hw = MockHardware()
        hw.set_thrusters(1.0, 1.0)
        advance(hw, 2.0)
        self.assertEqual(hw.read_water_speed(), (0.0, False))
        hw.set_armed(True)
        hw.set_thrusters(1.0, 1.0)
        advance(hw, 2.0)
        self.assertTrue(hw.read_water_speed()[1])
        hw.set_armed(False)
        self.assertEqual((hw._left, hw._right), (0.0, 0.0))


class SpoolEncoderTest(unittest.TestCase):
    """Payout is an upper bound on range (§5.5) — a bound that can tighten."""

    def test_paying_out_counts_up_and_rewinding_counts_down(self):
        # No monotonic maximum anywhere: latching the high-water mark would leave
        # a sub that has been hauled halfway home still claiming it might be at
        # the far end of the tether, which is the opposite of what a bound is for.
        q = QuadratureDecoder()
        # One full Gray-code cycle. Which way round is "out" depends on how the
        # encoder ends up mounted on the drum, so the test pins the MAGNITUDE and
        # the reversibility, not a sign the wiring gets to choose.
        cycle = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
        for a, b in cycle:
            q.update(bool(a), bool(b))
        self.assertEqual(abs(q.ticks), 4)
        for a, b in reversed(cycle[:-1]):
            q.update(bool(a), bool(b))
        self.assertEqual(q.ticks, 0, "winding the spool back must undo the payout")

    def test_a_double_transition_is_counted_as_missed_rather_than_guessed(self):
        # Two edges arrived between reads and the direction is genuinely
        # unknowable. Inventing ±2 there fabricates tether length.
        q = QuadratureDecoder()
        q.update(False, False)
        self.assertEqual(q.update(True, True), 0)
        self.assertEqual(q.missed, 1)
        self.assertEqual(q.ticks, 0)

    def test_an_unchanged_reading_is_not_a_tick(self):
        q = QuadratureDecoder()
        q.update(False, True)
        self.assertEqual(q.update(False, True), 0)
        self.assertEqual(q.ticks, 0)


# ---------------------------------------------------------------------------
# The pack is 2S
# ---------------------------------------------------------------------------
class BatteryScaleTest(unittest.TestCase):
    """The 24 V scale described a vehicle that was never built."""

    def test_the_bench_pack_boots_on_the_2s_scale(self):
        v = MockHardware().read_voltage()
        self.assertLessEqual(v, settings.battery_full_v)
        self.assertGreaterEqual(v, settings.battery_warn_v)

    def test_the_bench_pack_sags_to_the_documented_floor_and_stops(self):
        # 6.0 V is 3.0 V/cell: below it the cells are damaged, not merely flat.
        # The old mock sagged to 20.0 V, which on this pack is not a voltage.
        hw = MockHardware()
        hw.set_armed(True)
        hw.set_thrusters(1.0, 1.0)
        advance(hw, 4000.0, dt=1.0)
        self.assertAlmostEqual(hw.read_voltage(), settings.battery_floor_v, places=6)

    def test_the_pack_current_is_reported_and_rises_with_thrust(self):
        # Free from the same INA219 as the voltage, and the number that turns "the
        # pack is sagging" into "because both thrusters are wide open".
        hw = MockHardware()
        hw.set_armed(True)
        idle = hw.read_current_a()
        self.assertIsNotNone(idle)
        hw.set_thrusters(1.0, 1.0)
        self.assertGreater(hw.read_current_a(), idle)


# ---------------------------------------------------------------------------
# Backend selection — the honesty mechanism
# ---------------------------------------------------------------------------
class BackendSelectionTest(unittest.TestCase):
    """NEPTUNE_HW=auto must land on a backend that flags itself."""

    @staticmethod
    def _with_backend(name: str):
        # settings is a frozen dataclass read at call time, so a replaced copy is
        # the whole override. No env mutation, so tests cannot leak into each other.
        return mock.patch.object(hardware, "settings",
                                 dataclasses.replace(settings, hardware_backend=name))

    def test_auto_lands_on_the_bench_mock_with_no_gpio_wired(self):
        # And it must SAY so. A silent downgrade to simulation is how a bench run
        # gets mistaken for a dive; the warning is part of the honesty mechanism,
        # so it is asserted rather than merely tolerated.
        with self._with_backend("auto"):
            with self.assertLogs("neptune.hw", level="WARNING") as caught:
                hw = get_hardware()
        self.assertIsInstance(hw, MockHardware)
        self.assertTrue(hw.is_mock)
        self.assertIn("MockHardware", "".join(caught.output))

    def test_the_mock_flags_itself_as_a_simulation(self):
        # Everything downstream (telemetry `mock`, the SIM badge, the dive log)
        # hangs off this one flag. A simulation that does not say so is the single
        # worst failure in the whole system.
        self.assertTrue(MockHardware().is_mock)
        self.assertFalse(HardwareBase.is_mock)

    def test_real_hardware_refuses_to_construct_until_a_human_says_it_is_wired(self):
        # gpiozero installs fine on a Pi with nothing attached, so the import check
        # alone would come up reporting mock=False over a loom that does not exist.
        # One flag, flipped by the person who put the wires in the holes.
        self.assertFalse(RealHardware._gpio_available())
        with self.assertRaises(RuntimeError):
            RealHardware()

    def test_forcing_real_raises_instead_of_silently_simulating(self):
        # "real" is an explicit demand for the vehicle. Falling back to the mock
        # there would hand back a simulation to someone who asked for hardware.
        with self._with_backend("real"):
            with self.assertRaises(RuntimeError):
                get_hardware()

    def test_an_unrecognised_backend_name_falls_back_to_the_mock(self):
        with self._with_backend("banana"):
            with self.assertLogs("neptune.hw", level="WARNING"):
                hw = get_hardware()
        self.assertIsInstance(hw, MockHardware)

    def test_mock_is_the_default_on_this_machine(self):
        # The end-to-end §7 check: whatever NEPTUNE_HW says by default, a laptop
        # with no GPIO must end up simulated and flagged. Not assertLogs, because
        # an operator who has exported NEPTUNE_HW=mock gets the same answer with
        # nothing to warn about — and this test is about the answer.
        with quiet_hw_log():
            hw = get_hardware()
        self.assertTrue(hw.is_mock)


if __name__ == "__main__":
    unittest.main()

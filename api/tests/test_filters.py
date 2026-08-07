"""Estimator unit tests (spec §7) for nav/filters.py and nav/estimator.py.

Standard-library `unittest` on purpose: the repo has no python test framework and
this is not the change that should introduce one. Run from the api/ directory,
which is the python root:

    python -m unittest tests.test_filters -v

WHAT THESE TESTS ARE FOR. The estimator is the one part of NEPTUNE that cannot be
checked by looking at it. A heading filter that is subtly wrong does not crash and
does not look wrong on the dashboard — it draws a smooth, confident, plausible
track through water the sub was never in, and the operator finds out when the
tether runs out somewhere unexpected. Every test below therefore pins a specific
way the maths can lie quietly, and its docstring names that failure rather than
restating the assertion.

Each test asserts the SPECIFICATION, not the code. Where the two disagree the
spec wins and the disagreement is called out in the test, with one exception,
documented here because it is the sort of thing a future reader will otherwise
"fix" back into a bug:

    THE GYRO BIAS SIGN. The brief writes step 5 of §4a as
    `b <- clamp(b + k_b*e*dt, ±3)`, but §7 requires that "gyro bias converges
    toward an injected constant bias" — and with the brief's own definitions of
    the other steps those two requirements contradict each other. Predict
    SUBTRACTS b, and e = wrap180(mag - h), so writing ε for the heading error
    (h - true) and β̃ for the bias error (b - true bias):

        ε̇ = -ε/τ - β̃          (predict + the first-order correction)
        β̃̇ = -k_b·ε           (the brief's literal step 5, since e = -ε)

    The system matrix [[-1/τ, -1], [-k_b, 0]] has determinant -k_b < 0, i.e. one
    positive eigenvalue: it is a saddle, and the bias runs away from the truth
    until it pins at its own clamp. Flipping the sign gives determinant +k_b > 0
    with a negative trace — stable, and the bias converges.

    So the CONVERGENCE requirement (§7) is what these tests assert, because it is
    the observable behaviour the system needs and the formula is only a means to
    it. filters.py already implements the corrected sign and says so in a comment.
    Reported as a brief defect, not as a code defect.

The bulk of the file only needs plain floats. The last test case exercises the two
estimator wrappers end to end, which pulls in pydantic through nav/models.py — it
skips with a loud reason rather than taking the whole module down when pydantic is
not installed, because the pure filter maths is exactly what must stay checkable on
a bench machine with nothing installed on it.
"""
from __future__ import annotations

import unittest

from nav.filters import (
    HeadingFilter,
    SnagDetector,
    SpeedKF,
    heading_delta,
    thrust_level,
    wrap180,
    wrap360,
)

try:
    from nav.estimator import DeadReckonEstimator, FilteredEstimator
    from nav.models import Origin, SensorSample
    _NO_ESTIMATOR: str | None = None
except Exception as exc:                                        # noqa: BLE001
    _NO_ESTIMATOR = f"nav.estimator not importable here ({type(exc).__name__}: {exc})"


# 10 Hz — the NAV_DR_HZ default, so the numbers below are the ones that actually run.
DT = 0.1


def _tick(i: int) -> float:
    """Timestamp of sample i at 10 Hz, built by division rather than by accumulating
    0.1 sixty times over. Repeated += 0.1 drifts, and 2.0 s of drifted ticks arrives as
    2.0000000000000004 — which silently turns every "sustained for > 2 s" boundary test
    into an off-by-one-tick test of floating point instead of a test of the rule."""
    return i / 10.0


class TestWrap180(unittest.TestCase):
    """§4a: "every subtraction of two headings MUST go through wrap180"."""

    def test_folds_into_the_half_open_range(self) -> None:
        """Guards against wrap180 returning the raw angle for values already in range,
        or drifting off by 360 for multi-turn inputs — a filter fed 725° of innovation
        would slew for two minutes to correct an error of 5°."""
        self.assertAlmostEqual(wrap180(0.0), 0.0)
        self.assertAlmostEqual(wrap180(1.0), 1.0)
        self.assertAlmostEqual(wrap180(-1.0), -1.0)
        self.assertAlmostEqual(wrap180(179.0), 179.0)
        # The brief's formula ((d + 180) mod 360) - 180 puts the half-turn at -180.
        # Either sign is geometrically correct; pinning it stops a future rewrite from
        # flipping which way the sub is believed to have spun at the exact half turn.
        self.assertAlmostEqual(wrap180(180.0), -180.0)
        self.assertAlmostEqual(wrap180(-180.0), -180.0)
        self.assertAlmostEqual(wrap180(725.0), 5.0)
        self.assertAlmostEqual(wrap180(-725.0), -5.0)

    def test_the_359_to_1_crossing(self) -> None:
        """THE classic bug, called out by name in §4a. Crossing north is a 2° turn; a
        raw subtraction calls it 358° the wrong way round. That does not look like a
        bug on the map — it looks like the sub briefly span, and the estimator then
        steers the whole track sideways to explain a spin that never happened."""
        # The difference itself, folded.
        self.assertAlmostEqual(wrap180(1.0 - 359.0), 2.0)
        self.assertAlmostEqual(wrap180(359.0 - 1.0), -2.0)
        # And through the helper every caller is supposed to use.
        self.assertAlmostEqual(heading_delta(1.0, 359.0), 2.0)
        self.assertAlmostEqual(heading_delta(359.0, 1.0), -2.0)
        # The naive answer, stated explicitly so this test fails loudly if someone
        # "simplifies" heading_delta back to a subtraction.
        self.assertNotAlmostEqual(heading_delta(1.0, 359.0), 1.0 - 359.0)
        # Neighbours of north, both sides.
        self.assertAlmostEqual(heading_delta(0.0, 360.0), 0.0)
        self.assertAlmostEqual(heading_delta(350.0, 10.0), -20.0)
        self.assertAlmostEqual(heading_delta(10.0, 350.0), 20.0)

    def test_wrap360_keeps_absolute_headings_on_the_compass(self) -> None:
        """A heading of -1° or 361° is not wrong, it is unrenderable: the HUD prints it
        raw and the map rotates by it, so a filter that leaks one out shows the operator
        a compass that reads minus one degree north."""
        self.assertAlmostEqual(wrap360(-1.0), 359.0)
        self.assertAlmostEqual(wrap360(361.0), 1.0)
        self.assertAlmostEqual(wrap360(0.0), 0.0)
        self.assertAlmostEqual(wrap360(720.5), 0.5)


class TestHeadingTrustGate(unittest.TestCase):
    """§4a step 2: trusted ⟺ mag_cal >= 2 AND thrust_level < 0.5."""

    def test_gyro_only_when_mag_cal_is_below_two(self) -> None:
        """A magnetometer that has not seen enough motion to calibrate reports a heading
        with the same confident precision as a good one (§5.6). If the filter believes
        it, the gyro — the one instrument that is right — gets corrected towards a lie
        every tick, and the operator has no way to tell."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=1)
        self.assertTrue(f.gyro_only, "mag_cal=1 must not be trusted")
        f.update(DT, 100.0, 0.0, mag_cal=0)
        self.assertTrue(f.gyro_only, "mag_cal=0 must not be trusted")
        f.update(2 * DT, 100.0, 0.0, mag_cal=2)
        self.assertFalse(f.gyro_only, "mag_cal=2 is the documented trust threshold")
        f.update(3 * DT, 100.0, 0.0, mag_cal=3)
        self.assertFalse(f.gyro_only, "mag_cal=3 must be trusted")

    def test_gyro_only_when_thrust_reaches_half(self) -> None:
        """The BNO085 sits inside the same hull as two brushed motors; the sim models 22°
        of magnetic error at full throttle. Under thrust the compass is not noisy, it is
        wrong in a direction that correlates with exactly the manoeuvre being flown, so
        averaging does not save it. The gate is a hard cut, not a weighting."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, 3, left=0.0, right=0.0)
        self.assertFalse(f.gyro_only)
        f.update(DT, 100.0, 0.0, 3, left=0.49, right=0.0)
        self.assertFalse(f.gyro_only, "just under half thrust is still trusted")
        # 0.5 exactly is NOT trusted: the spec says trusted ⟺ thrust_level < 0.5.
        f.update(2 * DT, 100.0, 0.0, 3, left=0.5, right=0.0)
        self.assertTrue(f.gyro_only, "thrust_level == 0.5 must fail the trust gate")
        f.update(3 * DT, 100.0, 0.0, 3, left=0.0, right=-0.8)
        self.assertTrue(f.gyro_only, "a reversing thruster magnetises just as well")
        f.update(4 * DT, 100.0, 0.0, 3, left=0.0, right=0.0)
        self.assertFalse(f.gyro_only, "trust must come back when the motors stop")

    def test_thrust_level_is_the_actual_output(self) -> None:
        """§4a is explicit: "actual output, not commanded — disarmed must read as zero
        thrust". A disarmed sub has a joystick at full deflection and no current in the
        motors; gating on the command would throw away the compass on the surface, which
        is the one moment it is clean and the only chance to learn the gyro's bias."""
        self.assertAlmostEqual(thrust_level(0.0, 0.0), 0.0)
        self.assertAlmostEqual(thrust_level(0.8, 0.1), 0.8)
        self.assertAlmostEqual(thrust_level(0.1, -0.9), 0.9, msg="magnitude, not sign")
        # The filter takes left/right and has no access to the throttle at all, which is
        # the structural half of this guarantee: the command cannot reach the gate.
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, 3, left=0.0, right=0.0)
        self.assertFalse(f.gyro_only)


class TestHeadingSlewCap(unittest.TestCase):
    """§4a step 4: corrections are capped at ±5 °/s, "even a large accumulated error
    walks back smoothly"."""

    def _coast_then_trust(self) -> tuple[HeadingFilter, float]:
        """Build a deliberately large error: 10 s of gyro-only coast at +10 °/s while the
        compass sits still at 0°. That is a 100° disagreement, which is the state a real
        dive reaches after a long run down a canal on the thrusters."""
        f = HeadingFilter()
        f.update(0.0, 0.0, 0.0, mag_cal=0)
        for i in range(1, 101):
            f.update(_tick(i), 0.0, 10.0, mag_cal=0)
        self.assertTrue(f.gyro_only, "the coast must actually have been gyro-only")
        return f, wrap180(0.0 - f.h)

    def test_the_coast_really_accumulates_a_large_error(self) -> None:
        """A slew-cap test that starts from a 2° error proves nothing. This asserts the
        fixture itself, so the real test below cannot quietly become a no-op."""
        _, e = self._coast_then_trust()
        self.assertGreater(abs(e), 90.0, "setup failed to build a large error")

    def test_regaining_trust_never_steps_the_heading(self) -> None:
        """The no-step rule. An estimator that teleports the heading 100° when the
        compass recovers draws a track with a kink in it, and an operator who has seen
        one kink stops believing the map — which costs more than the lag ever does.

        The cap applies to the CORRECTION, so each tick is measured against what pure
        prediction would have produced, rather than against the raw heading change
        (which also carries the gyro and the learned bias)."""
        f, _ = self._coast_then_trust()
        cap = HeadingFilter.SLEW_DPS * DT
        worst = 0.0
        for i in range(101, 601):                    # 50 s of trusted, gyro silent
            h_before, b_before = f.h, f.b
            h_after = f.update(_tick(i), 0.0, 0.0, mag_cal=3)
            predicted = h_before + (0.0 - b_before) * DT
            worst = max(worst, abs(wrap180(h_after - predicted)))
        self.assertLessEqual(worst, cap + 1e-9,
                             f"correction of {worst:.4f}° in one tick exceeds {cap:.4f}°")

    def test_the_first_trusted_tick_does_not_snap(self) -> None:
        """The single tick that matters most, checked on its own: the instant trust
        returns. Nothing is learned yet and the gyro is quiet, so the entire heading
        change on this tick IS the correction — no arithmetic to hide behind."""
        f, e = self._coast_then_trust()
        h_before = f.h
        h_after = f.update(_tick(101), 0.0, 0.0, mag_cal=3)
        moved = abs(wrap180(h_after - h_before))
        self.assertLessEqual(moved, HeadingFilter.SLEW_DPS * DT + 1e-9)
        # It must move the SHORT way, towards the compass, not merely move slowly.
        self.assertGreater(abs(e) - abs(wrap180(0.0 - h_after)), 0.0)

    def test_the_cap_still_converges(self) -> None:
        """The other half of the rule: capping must produce a slow walk home, not a
        permanent offset. A heading that lags forever is just a wrong heading."""
        f, _ = self._coast_then_trust()
        for i in range(101, 501):                    # 40 s; 100° at 5 °/s needs 20 s
            f.update(_tick(i), 0.0, 0.0, mag_cal=3)
        self.assertLess(abs(wrap180(0.0 - f.h)), 2.0)


class TestHeadingBias(unittest.TestCase):
    """§4a step 5, read against §7's requirement that the bias converges. See the module
    docstring for why the brief's literal sign cannot satisfy its own requirement."""

    def test_bias_converges_toward_an_injected_constant_bias(self) -> None:
        """A MEMS gyro's zero is not zero. Left unlearned, a +1.5 °/s offset rotates the
        whole track by a degree and a half every second the compass is distrusted — and
        the compass is distrusted precisely while the thrusters are running, i.e. while
        the sub is actually going somewhere. This is the test that says the filter can
        coast at all."""
        true_bias = 1.5                      # °/s of pure offset on a stationary sub
        f = HeadingFilter()
        f.update(0.0, 100.0, true_bias, mag_cal=3)
        for i in range(1, 4001):             # 400 s; the slow mode's time constant ≈ 48 s
            f.update(_tick(i), 100.0, true_bias, mag_cal=3)
            self.assertLessEqual(abs(f.b), HeadingFilter.B_MAX_DPS + 1e-12,
                                 "the ±3 °/s clamp is an invariant, not a final state")
        self.assertAlmostEqual(f.b, true_bias, delta=0.05)
        # And the point of learning it: no standing heading error left behind.
        self.assertLess(abs(wrap180(100.0 - f.h)), 0.5)

    def test_bias_converges_on_a_negative_offset_too(self) -> None:
        """Guards against a sign that happens to work in one direction — which is exactly
        what a wrong-signed feedback loop looks like on a lucky test case."""
        f = HeadingFilter()
        f.update(0.0, 40.0, -0.8, mag_cal=3)
        for i in range(1, 4001):
            f.update(_tick(i), 40.0, -0.8, mag_cal=3)
        self.assertAlmostEqual(f.b, -0.8, delta=0.05)

    def test_bias_is_not_learned_while_untrusted(self) -> None:
        """§4a: "only while trusted". While the thrusters are poisoning the compass the
        innovation is a measure of the magnetic disturbance, not of the gyro. Learning
        from it teaches the gyro the thrusters' error and then coasts on it — the filter
        would carry the disturbance forward into the exact interval it exists to
        protect."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 1.5, mag_cal=1)
        for i in range(1, 601):
            f.update(_tick(i), 100.0, 1.5, mag_cal=1)
        self.assertEqual(f.b, 0.0, "bias moved while the magnetometer was distrusted")

    def test_bias_is_not_learned_on_a_large_innovation(self) -> None:
        """§4a: "only ... AND |e| < 10 deg". A 30° innovation is a magnetic disturbance
        or a bad mounting offset, never a gyro bias — a gyro drifting 30° in a tick would
        be a hardware failure. Feeding it to the estimator converts a one-off disturbance
        into a permanent lie the filter then coasts on."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)         # seeds h = 100
        for i in range(1, 11):                        # compass jumps to 130 and stays
            f.update(_tick(i), 130.0, 0.0, mag_cal=3)
        self.assertGreaterEqual(abs(wrap180(130.0 - f.h)), 10.0,
                                "setup failed: the innovation shrank below the gate")
        self.assertEqual(f.b, 0.0, "bias learned from an out-of-gate innovation")

    def test_bias_clamps_rather_than_absorbing_a_fault(self) -> None:
        """A compass rotating steadily while the gyro insists nothing is turning is a
        broken gyro, not a bias. Without the ±3 °/s clamp the estimator quietly absorbs
        an unbounded fault into a "calibration" — and then flies on it the moment it
        goes gyro-only, which is the worst possible time to be carrying it."""
        f = HeadingFilter()
        f.update(0.0, 0.0, 0.0, mag_cal=3)
        for i in range(1, 3001):                      # 300 s, compass turning at 4.9 °/s
            t = _tick(i)
            f.update(t, wrap360(4.9 * t), 0.0, mag_cal=3)
            self.assertLessEqual(abs(f.b), HeadingFilter.B_MAX_DPS + 1e-12)
        self.assertAlmostEqual(abs(f.b), HeadingFilter.B_MAX_DPS, delta=1e-9,
                               msg="setup failed: the clamp was never reached")

    def test_the_359_to_1_crossing_inside_the_filter(self) -> None:
        """wrap180 being right is not the same as it being USED. With h at 359° and the
        compass at 1°, the filter must nudge 0.1° clockwise through north — not run 358°
        the other way, which at the slew cap would be a seventy-second lie."""
        f = HeadingFilter()
        f.update(0.0, 359.0, 0.0, mag_cal=3)
        h = f.update(DT, 1.0, 0.0, mag_cal=3)
        step = wrap180(h - 359.0)
        self.assertGreater(step, 0.0, "corrected away from the compass, the long way")
        self.assertLess(step, HeadingFilter.SLEW_DPS * DT + 1e-9)
        self.assertLess(abs(wrap180(1.0 - h)), 2.0, "the filter should now be nearer 1°")


class TestHeadingDtRules(unittest.TestCase):
    """§4a: "skip update if dt <= 0; if dt > 0.5 s treat as a gap"."""

    def test_dt_of_zero_is_skipped(self) -> None:
        """The nav loop and the replay harness can both hand the same sample twice.
        Re-running a tick on dt = 0 integrates nothing but DOES re-apply the correction,
        so a stalled clock would drag the heading onto the compass at whatever rate the
        loop happens to spin — the exact snap the slew cap exists to prevent."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        h1 = f.update(1.0, 100.0, 30.0, mag_cal=3)
        h2 = f.update(1.0, 100.0, 30.0, mag_cal=3)
        self.assertEqual(h2, h1, "a duplicated timestamp changed the heading")

    def test_negative_dt_is_skipped(self) -> None:
        """The Pi 3B+ has no RTC, so its clock steps when NTP finally lands mid-dive.
        Integrating a negative dt runs the gyro backwards and unwinds real turns."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        h1 = f.update(1.0, 100.0, 30.0, mag_cal=3)
        h2 = f.update(0.5, 100.0, 30.0, mag_cal=3)
        self.assertEqual(h2, h1, "time going backwards moved the heading")

    def test_a_clock_step_does_not_wedge_the_filter(self) -> None:
        """NOT SPECIFIED BY THE BRIEF — pinned here so the resolution stays deliberate.
        If a sample from the future is remembered as the previous timestamp, every later
        sample has dt <= 0 and the filter is frozen until the clock catches up: on a Pi
        with no RTC that can be the whole dive, and it fails silently."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        f.update(9.0, 100.0, 0.0, mag_cal=3)         # a sample from the future
        f.update(1.0, 100.0, 0.0, mag_cal=3)         # skipped, but must re-seat the clock
        before = f.h
        after = f.update(1.1, 100.0, 30.0, mag_cal=0)
        self.assertNotAlmostEqual(after, before, msg="filter stayed wedged after a step")

    def test_a_gap_reseeds_from_the_compass_when_trusted(self) -> None:
        """§4a: a gap longer than 0.5 s must not be integrated across. The one thing
        known about the missing seconds is that nothing is known about them, and a gyro
        rate held across a two-second hole is an invented turn."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        h = f.update(2.0, 200.0, 30.0, mag_cal=3)
        self.assertAlmostEqual(h, 200.0, msg="a trusted gap must re-seed from the compass")

    def test_a_gap_holds_when_the_compass_is_not_trusted(self) -> None:
        """The other branch of the same rule. With nothing worth believing on either
        side, holding is the only honest answer — inventing 60° of gyro turn or snapping
        onto a distrusted compass are both claims the filter cannot support."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=0)
        h = f.update(2.0, 200.0, 30.0, mag_cal=0)
        self.assertAlmostEqual(h, 100.0, msg="an untrusted gap must hold, not integrate")

    def test_half_a_second_is_still_a_normal_tick(self) -> None:
        """The boundary: the rule is dt > 0.5 s, so 0.5 s exactly still integrates. Off by
        one on this turns every slow loop iteration into a compass snap."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        h = f.update(0.5, 100.0, 30.0, mag_cal=3)
        # Predict 100 + 30*0.5 = 115, then a correction capped at 5 °/s * 0.5 s = 2.5°.
        self.assertAlmostEqual(h, 112.5, places=6)

    def test_a_gap_does_not_leave_the_filter_stuck(self) -> None:
        """After the gap the filter has to resume normally; a gap handler that forgets to
        advance its clock turns one dropped frame into a permanently frozen heading."""
        f = HeadingFilter()
        f.update(0.0, 100.0, 0.0, mag_cal=3)
        f.update(2.0, 100.0, 0.0, mag_cal=3)
        h = f.update(2.1, 100.0, 30.0, mag_cal=0)   # untrusted: pure gyro integration
        self.assertAlmostEqual(h, 103.0, places=6)


class TestSpeedKF(unittest.TestCase):
    """§4b — the 1-D Kalman filter over [v, b_a]."""

    LUT_LIE = 0.20          # what the speed model claims; deliberately wrong
    TRUTH = 0.60            # what the paddlewheel measures

    def _converged(self) -> SpeedKF:
        """A filter that has settled on a measured 0.60 m/s, for the tests that need to
        start from a confident state."""
        kf = SpeedKF(m_per_pulse=0.05, window_s=0.5)
        for _ in range(50):
            kf.update(DT, 0.0, 0.6, self.TRUTH, self.LUT_LIE)
        return kf

    def test_converges_to_paddlewheel_truth(self) -> None:
        """The whole reason the paddlewheel was bought. The LUT is an open-loop model
        that cannot know about a headwind, a fouled prop or a half-metre of weed on the
        nose; when a real measurement of the water is available it must win, and win
        outright rather than being averaged into the model's opinion."""
        kf = self._converged()
        self.assertAlmostEqual(kf.v, self.TRUTH, delta=0.02)
        self.assertEqual(kf.source, "kf-paddle")
        # Emphatically not a blend with the LUT's claim.
        self.assertGreater(abs(kf.v - self.LUT_LIE), 0.3)

    def test_the_wheel_is_directionless_so_the_sign_comes_from_the_throttle(self) -> None:
        """§2/§4b: the hall sensor counts magnets going past and has no idea which way
        the wheel turned. Reversing out of a culvert with a positive speed on the map
        drives the track forward into the wall the sub is backing away from."""
        kf = SpeedKF(m_per_pulse=0.05, window_s=0.5)
        for _ in range(50):
            kf.update(DT, 0.0, -0.6, self.TRUTH, -self.LUT_LIE)   # magnitude only
        self.assertAlmostEqual(kf.v, -self.TRUTH, delta=0.02)

    def test_the_lut_is_a_weak_measurement_when_the_wheel_goes_stale(self) -> None:
        """§4b: "the LUT is a weak prior, never a crisp measurement". If a stale wheel let
        the model snap the estimate to itself, the filter would give back exactly the
        number it exists to check, at exactly the moment — no pulses under thrust — that
        the number is most likely to be a lie about a snagged sub."""
        on_lut = self._converged()
        on_paddle = self._converged()
        start = on_lut.v
        claim = 1.0                                   # the LUT suddenly claims 1 m/s

        v_lut = on_lut.update(DT, 0.0, 0.6, None, claim)
        v_paddle = on_paddle.update(DT, 0.0, 0.6, claim, claim)
        self.assertEqual(on_lut.source, "kf-lut")
        self.assertEqual(on_paddle.source, "kf-paddle")

        step_lut = abs(v_lut - start)
        step_paddle = abs(v_paddle - start)
        self.assertLess(step_lut, 0.05, "the estimate snapped towards the LUT")
        self.assertLess(step_lut * 5.0, step_paddle,
                        "the LUT moved the estimate as hard as a real measurement")

        # A full second later it must still be nearer what was measured than what is
        # modelled — the prior leans, it does not overrule.
        for _ in range(9):
            v_lut = on_lut.update(DT, 0.0, 0.6, None, claim)
        self.assertLess(abs(v_lut - self.TRUTH), abs(v_lut - claim))

    def test_the_lut_prior_is_weak_but_not_ignored(self) -> None:
        """The opposite failure: a prior weighted into irrelevance is a filter coasting on
        an accelerometer, which is §2.2's forbidden double integration wearing a hat.
        Given long enough with no wheel, the model is all there is and it must be used."""
        kf = self._converged()
        claim = 1.0
        for _ in range(200):                          # 20 s with a silent wheel
            kf.update(DT, 0.0, 0.6, None, claim)
        self.assertAlmostEqual(kf.v, claim, delta=0.15)

    def test_zero_locks_at_rest(self) -> None:
        """§4b: "a stopped, unpowered sub is genuinely stopped — this kills accel-bias
        drift at rest". The accelerometer is injected with a steady +0.3 m/s² of pure
        bias here; without the zero-lock the filter integrates it into an imaginary
        3 m/s over ten seconds of sitting still, and the map wanders off on its own
        while the operator is fiddling with the tether."""
        kf = self._converged()
        self.assertGreater(kf.v, 0.5, "setup failed: nothing to bring back to rest")
        for _ in range(300):                          # 30 s stopped, wheel stalled
            kf.update(DT, 0.30, 0.0, None, 0.0)
        self.assertLess(abs(kf.v), 0.02, "the estimate drifted while the sub sat still")
        # And the reason it held: the bias was identified rather than integrated.
        self.assertAlmostEqual(kf.b_a, 0.30, delta=0.05)

    def test_rest_is_believed_more_firmly_than_the_model(self) -> None:
        """The rest branch is not just "z = 0", it is "z = 0 with R = 0.05²" — four times
        more confident than the weakest the model prior can ever be. That difference is
        the whole mechanism: a sub sitting still is the only moment the filter gets a
        measurement it can lean on hard enough to strip the accelerometer's bias out.

        Both filters below are handed the SAME pseudo-measurement, z = 0, so the only
        thing that can move them differently is R."""
        at_rest = self._converged()
        on_model = self._converged()
        start = at_rest.v

        at_rest.update(DT, 0.0, 0.0, None, 0.0)       # |throttle| < 0.1 -> R = 0.05²
        on_model.update(DT, 0.0, 0.5, None, 0.0)      # thrusting -> R = (0.3·0 + 0.1)²
        self.assertGreater(start - at_rest.v, 2.0 * (start - on_model.v),
                           "rest was believed no harder than the speed model")

    def test_rest_needs_the_throttle_below_a_tenth(self) -> None:
        """The boundary, |throttle| < 0.1. Above it a stale wheel means "too slow for the
        wheel to see", not "stopped" — the wheel stalls below ~0.1 m/s, so zero-locking a
        sub that is genuinely creeping forward would erase real, slow progress."""
        kf = self._converged()
        for _ in range(100):                          # throttle exactly at the boundary
            kf.update(DT, 0.0, 0.1, None, 0.11)
        self.assertGreater(kf.v, 0.05, "throttle == 0.1 must not count as at rest")
        self.assertAlmostEqual(kf.v, 0.11, delta=0.05)

        creeping = self._converged()
        for _ in range(100):                          # just inside: this IS rest
            creeping.update(DT, 0.0, 0.09, None, 0.10)
        self.assertLess(abs(creeping.v), 0.05)

    def test_non_positive_dt_is_skipped(self) -> None:
        """Re-applying a measurement at an unchanged timestamp shrinks the covariance for
        free, and a filter that has convinced itself it is certain stops listening to the
        wheel — which is a very quiet way to lose the only real speed measurement. The
        negative case is the replay harness seeking, and the Pi's clock stepping when NTP
        lands mid-dive; a negative dt runs the accelerometer backwards."""
        kf = self._converged()
        v_before, p_before = kf.v, kf.p00
        kf.update(0.0, 5.0, 1.0, 3.0, 3.0)
        self.assertEqual(kf.v, v_before, "a duplicated timestamp moved the estimate")
        self.assertEqual(kf.p00, p_before, "a duplicated timestamp shrank the covariance")
        kf.update(-0.5, 5.0, 1.0, 3.0, 3.0)
        self.assertEqual(kf.v, v_before, "time going backwards moved the estimate")
        self.assertEqual(kf.p00, p_before)


class TestSnagDetector(unittest.TestCase):
    """§4c — thrust_level > 0.5 sustained > 2 s with no measured motion."""

    def test_fires_on_sustained_thrust_with_no_measured_speed(self) -> None:
        """"The map marches forward while the sub is pinned on a shopping trolley." The
        dead reckoner integrates a speed model that has no idea the sub has stopped, so
        without this detector the operator drives a phantom for as long as their patience
        lasts and then goes looking for it in the wrong hundred metres of canal."""
        d = SnagDetector()
        self.assertFalse(d.update(_tick(0), 0.8, 0.8, 0.0))
        for i in range(1, 21):                        # up to and including t = 2.0 s
            self.assertFalse(d.update(_tick(i), 0.8, 0.8, 0.0),
                             f"fired early, at t = {_tick(i)} s")
        self.assertTrue(d.update(_tick(21), 0.8, 0.8, 0.0), "did not fire past 2 s")

    def test_does_not_fire_on_a_brief_transient(self) -> None:
        """A kick off a lock wall, a strand of weed, a moment of ballast shifting: the sub
        stops for a second and then goes. An alarm that cries snag every time that happens
        is an alarm the operator learns to ignore, and then it is worth nothing on the day
        the sub really is caught."""
        d = SnagDetector()
        for i in range(0, 16):                        # 1.5 s stuck — under the threshold
            self.assertFalse(d.update(_tick(i), 0.8, 0.8, 0.0))
        for i in range(16, 46):                       # then the water starts moving again
            self.assertFalse(d.update(_tick(i), 0.8, 0.8, 0.7),
                             "a transient latched into a snag")

    def test_clears_once_the_sub_moves_again(self) -> None:
        """Snag is a live state, not an event log. If it latched, the operator would have
        no way to tell a sub that has broken free from one still stuck, and would surface
        a working sub — or worse, learn to disbelieve the flag."""
        d = SnagDetector()
        for i in range(0, 30):
            d.update(_tick(i), 0.8, 0.8, 0.0)
        self.assertTrue(d.snagged, "setup failed: never snagged")
        self.assertFalse(d.update(_tick(30), 0.8, 0.8, 0.4), "stayed snagged after moving")

    def test_thrust_must_exceed_half(self) -> None:
        """§4c says thrust_level > 0.5. Below that a stationary sub is just a sub being
        driven gently against a current — no evidence of anything being caught."""
        d = SnagDetector()
        for i in range(0, 60):                        # 6 s at exactly the threshold
            self.assertFalse(d.update(_tick(i), 0.5, 0.5, 0.0))

    def test_a_measured_creep_is_not_a_snag(self) -> None:
        """The stopped threshold is 0.05 m/s. A sub inching forward is making progress,
        badly; calling that a snag would mask the real thing when it happens."""
        d = SnagDetector()
        for i in range(0, 60):
            self.assertFalse(d.update(_tick(i), 0.8, 0.8, 0.06))

    def test_a_silent_wheel_under_thrust_is_the_snag_signature(self) -> None:
        """§2: "no pulses + high throttle = the snag signal". This is also where the LUT is
        excluded — the detector's signature has no place to put a modelled speed, so the
        one number that would confidently say "0.9 m/s, all fine" for a sub bolted to a
        trolley cannot reach it. Only a measurement, or its absence, is admissible."""
        d = SnagDetector()
        d.update(_tick(0), 0.0, 0.0, 0.6)             # the wheel proves it exists
        fired_at = None
        for i in range(1, 41):
            if d.update(_tick(i), 0.8, 0.8, None) and fired_at is None:
                fired_at = _tick(i)
        self.assertIsNotNone(fired_at, "a stale wheel under full thrust never snagged")
        self.assertGreater(fired_at, 2.0)
        self.assertLess(fired_at, 2.5)

    def test_a_wheel_that_has_never_turned_does_not_raise_an_alarm(self) -> None:
        """NOT SPECIFIED BY THE BRIEF, pinned so the choice stays deliberate. §4c defines
        the rule for a fitted wheel that has stopped reporting; a hull built with no
        paddlewheel at all reports None forever and is outside it. Firing there would put
        a snag alarm on every normal run of every wheel-less hull until the operator
        learned to ignore snag alarms — which is a worse failure than the stated cost of
        not firing: a sub already stuck before the wheel has ever turned is missed until
        it moves once."""
        d = SnagDetector()
        for i in range(0, 60):                        # 6 s of full thrust, no wheel fitted
            self.assertFalse(d.update(_tick(i), 0.8, 0.8, None))


@unittest.skipIf(_NO_ESTIMATOR is not None, _NO_ESTIMATOR or "")
class TestEstimatorWiring(unittest.TestCase):
    """§4 end to end: the filters are only worth anything if the estimator hands them the
    right numbers and carries their verdicts out on the NavState the dashboard reads."""

    def _origin(self) -> Origin:
        return Origin(lat=52.0, lon=-1.0, accuracy=5.0, heading_deg=0.0, source="manual")

    def _sample(self, i: int, **kw: float | int | None) -> SensorSample:
        # gyro_z_dps is stated EXPLICITLY, as a live reading of "not turning". It used to
        # be left to the field default, which was 0.0 — the same number, but meaning the
        # opposite thing once the default became None for cannot-tell: no gyro at all.
        # The filter only reports gyro_only when it HAS a gyro to coast on, so every test
        # in here about the trust gate was quietly asserting against a vehicle with no
        # IMU fitted. A fixture that means "healthy sub" has to say so.
        f = {"heading_deg": 0.0, "depth_m": 1.0, "throttle": 0.0, "gyro_z_dps": 0.0,
             "left": 0.0, "right": 0.0, "mag_cal": 3, "armed": True}
        f.update(kw)
        return SensorSample(t=_tick(i), **f)

    def test_a_disarmed_sub_reads_as_zero_thrust(self) -> None:
        """The "actual output, not commanded" rule, checked where it can actually go
        wrong: at the wiring. A joystick held at full deflection with the sub disarmed
        must not throw away the compass, because sitting disarmed on the surface is
        exactly when the magnetometer is clean and the bias is learnable."""
        est = FilteredEstimator(self._origin())
        ns = None
        for i in range(20):
            ns = est.update(self._sample(i, throttle=1.0, left=0.0, right=0.0, armed=False))
        self.assertFalse(ns.gyro_only, "a full command with dead motors distrusted the compass")
        for i in range(20, 40):
            ns = est.update(self._sample(i, throttle=1.0, left=0.9, right=0.9))
        self.assertTrue(ns.gyro_only, "real thrust must reach the trust gate")

    def test_no_gyro_is_not_reported_as_coasting_on_the_gyro(self) -> None:
        """GYRO ONLY means "the compass is being ignored on purpose, and the spin sensor
        is carrying the bearing". A hull whose IMU is not answering has no spin sensor to
        carry anything, so claiming that state would be the reassuring reading of the two:
        it says a deliberate, working fallback is in hand when nothing is. The compass —
        distrusted or not — is all there is, and mag_cal is what carries the doubt."""
        est = FilteredEstimator(self._origin())
        ns = None
        for i in range(40):                       # thrusting hard, so the compass is distrusted
            ns = est.update(self._sample(i, throttle=1.0, left=0.9, right=0.9,
                                         gyro_z_dps=None))
        self.assertFalse(ns.gyro_only, "no gyro cannot be a gyro-only coast")
        self.assertIsNotNone(ns.heading_deg,
                             "a live compass is still a bearing, even while distrusted")

    def test_the_snag_detector_runs_in_the_dead_reckoning_backend(self) -> None:
        """§4c: "runs in BOTH estimator modes — it is a safety signal, not an estimator
        feature". Which backend NAV_FILTER happens to name must never decide whether the
        operator is told the sub is pinned, and "dr" is the DEFAULT — so if the detector
        only ran in "filtered" it would, in practice, not run at all."""
        est = DeadReckonEstimator(self._origin())
        ns = None
        for i in range(10):
            ns = est.update(self._sample(i, throttle=0.2, left=0.2, right=0.2,
                                         speed_ms_measured=0.25))
        self.assertFalse(ns.snagged)
        for i in range(10, 45):
            ns = est.update(self._sample(i, throttle=0.9, left=0.9, right=0.9,
                                         speed_ms_measured=0.0))
        self.assertTrue(ns.snagged, "the default backend missed a snag")
        self.assertLessEqual(ns.confidence, 0.4, "a snagged track must not read as healthy")

    def test_a_lut_backed_speed_is_not_evidence_of_movement(self) -> None:
        """§4c: "LUT does NOT count — it would lie". With the wheel silent the speed KF is
        pulled towards the model, so the filtered speed itself starts reporting a healthy
        metre per second. If that number were fed back in as evidence, the detector would
        be blind in the one mode it should be strongest, on the one failure it exists for."""
        est = FilteredEstimator(self._origin())
        ns = None
        for i in range(10):                           # a healthy run, wheel reporting
            ns = est.update(self._sample(i, throttle=0.5, left=0.5, right=0.5,
                                         speed_ms_measured=0.55))
        self.assertEqual(ns.speed_src, "kf-paddle")
        self.assertFalse(ns.snagged)
        for i in range(10, 55):                       # pinned: full thrust, wheel silent
            ns = est.update(self._sample(i, throttle=0.9, left=0.9, right=0.9))
        self.assertEqual(ns.speed_src, "kf-lut")
        self.assertGreater(ns.speed_ms, 0.3,
                           "setup failed: the model was not claiming healthy motion")
        self.assertTrue(ns.snagged, "the model's own speed suppressed the snag alarm")
        self.assertLessEqual(ns.confidence, 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Estimator maths (spec §4a–§4c) — heading complementary filter, speed KF, snag detector.

Deliberately pure: no pydantic, no config, no hardware, no logging. Everything here
takes and returns plain floats, so every rule below can be exercised from a bare
interpreter with three lines and no fixtures. `estimator.py` owns the translation
from SensorSample/NavState into these scalars; nothing in this module knows a
SensorSample exists.

The constants are not taste. They came out of the design discussion with reasons
attached (tau = 2 s because a canal sub's real turn is slower than the thruster
magnetic disturbance; ±5 °/s because a heading that STEPS makes the operator
distrust the map more than a heading that lags). Tune them against a replay of a
real dive, with `python -m nav.cli replay --filter both`, or not at all.

EXPLICITLY OUT OF SCOPE (§4d) — written here so a future pass does not "improve" it:

    NO position-domain EKF, NO online current estimation, NO surface-refix fusion
    in this pass. Reason: with only heading+speed+payout there is not enough
    observability to learn a current vector, and there are no real dive logs yet
    to validate against — the replay harness is what will justify (or kill) that
    work later, with data.

That is a scope decision, not an oversight. The next person to want a position EKF
should arrive carrying dive logs and a replay score, not an intuition.
"""
from __future__ import annotations


def wrap180(d: float) -> float:
    """Fold an angle DIFFERENCE into -180..180.

    EVERY subtraction of two headings in this system goes through this. Without it
    the 359°→1° crossing reads as a 358° turn instead of 2°, which does not look
    like a bug — it looks like the sub briefly span round, and the estimator will
    happily steer the whole track sideways to explain it.
    """
    return ((d + 180.0) % 360.0) - 180.0


def wrap360(d: float) -> float:
    """Fold an absolute heading into 0..360 (compass convention: 0 = N, 90 = E)."""
    return d % 360.0


def heading_delta(a: float, b: float) -> float:
    """a - b, the short way round. Use this instead of `a - b`, always."""
    return wrap180(a - b)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def thrust_level(left: float, right: float) -> float:
    """How hard the thrusters are ACTUALLY pushing, 0..1.

    Deliberately the actual outputs and not the commanded throttle: a disarmed sub
    has full-scale commands flying about and zero current in the motors, and the two
    things this number gates — "is the magnetometer being poisoned?" and "should a
    stationary sub be considered snagged?" — are both questions about the motors,
    not about the joystick.
    """
    return max(abs(left), abs(right))


def _sign(x: float) -> float:
    """+1 for zero-or-positive, -1 for negative — the same convention SpeedLUT uses,
    so a zero-throttle sample resolves the same way in both places."""
    return 1.0 if x >= 0.0 else -1.0


class HeadingFilter:
    """§4a — gyro integrated short-term, magnetic heading blended in only when it is
    worth believing, and the output NEVER stepped.

    The failure this exists for: the BNO085's fused yaw is pulled around by the
    thrusters' own magnetic fields — the simulator models 22° of error at full
    throttle, which is not a subtle wobble, it is a heading that lies while the sub
    is doing the one thing that moves it. The gyro cannot be magnetised but it
    drifts. So: coast on the gyro whenever the magnetometer is suspect, correct
    gently back when it is trustworthy again, and learn the gyro's bias only in
    the quiet moments when there is something honest to learn it from.

    The no-step rule (the slew cap) matters as much as the maths. An estimator that
    silently teleports the heading by 20° when the compass recovers produces a track
    with a kink in it, and an operator who has watched one kink stops trusting the
    map entirely. A lag is forgivable; a jump is not.

    A COAST IS AN INTEGRATION, SO IT NEEDS SOMETHING TO INTEGRATE. Both the fused
    yaw and the yaw rate come off the SAME BNO085, so the interesting failure is not
    "the compass is poisoned" — it is "the chip is gone", and then there is no gyro
    to coast on either. This filter used to be handed a 0.0 rate in that case, which
    it read as "measured: running dead straight" and integrated into a bearing that
    never moved while the sub turned underneath it. Every branch below therefore
    asks whether each input EXISTS before asking what it says, and when neither does
    the filter returns None rather than its last opinion.
    """

    TAU_S = 2.0                     # first-order blend time constant
    SLEW_DPS = 5.0                  # hard cap on how fast a correction may walk the heading
    K_B = 0.01                      # gyro bias learning rate, per second
    B_MAX_DPS = 3.0                 # a "bias" larger than this is a fault, not a bias
    MIN_MAG_CAL = 2                 # BNO085 calibration status 0..3; <2 = suspect (§5.6)
    MAX_TRUSTED_THRUST = 0.5        # above this the thrusters own the magnetometer
    MAX_BIAS_INNOVATION_DEG = 10.0  # a big error is a disturbance, never evidence about bias
    GAP_S = 0.5                     # longer than this between samples is a gap, not a tick

    def __init__(self) -> None:
        self.h: float = 0.0                 # filtered heading, degrees, 0..360
        self.b: float = 0.0                 # estimated gyro bias, deg/s
        self.gyro_only: bool = False        # the compass is being ignored ON PURPOSE
        # No heading could be produced at all this tick — no compass AND no gyro.
        # Distinct from gyro_only, which is a working estimate running on one
        # instrument; this is the absence of an estimate.
        self.unknown: bool = False
        self.initialised: bool = False
        self._prev_t: float | None = None

    def update(
        self,
        t: float,
        heading_deg: float | None,
        gyro_z_dps: float | None,
        mag_cal: int | None,
        left: float = 0.0,
        right: float = 0.0,
    ) -> float | None:
        """One tick. Returns the filtered heading in degrees (0..360), or None when
        nothing aboard can say which way the sub is pointing."""
        # None is not a calibration status, it is the absence of one — "no IMU
        # answered" — and it can never satisfy a trust gate. Compared with >= it
        # would raise; defaulted to 0 it would merely be untrusted, which is too
        # generous, because untrusted is a state the filter knows how to fly in.
        mag_ok = mag_cal is not None and mag_cal >= self.MIN_MAG_CAL
        have_mag = heading_deg is not None
        have_gyro = gyro_z_dps is not None
        trusted = have_mag and mag_ok and thrust_level(left, right) < self.MAX_TRUSTED_THRUST
        # The coast is gyro-only in the literal sense: it is the GYRO carrying the
        # heading while the compass is ignored. Setting this without a live gyro
        # told the operator the filter was coasting on an instrument that was not
        # there, and put a "deliberate, under control" badge on a dead chip.
        self.gyro_only = have_gyro and not trusted
        self.unknown = not have_mag and not have_gyro
        if self.unknown:
            # Nothing measured the heading and nothing measured the turn rate. h is
            # NOT returned: it is the bearing from whenever the IMU last spoke, and
            # handing that out is the frozen-bearing failure — worse than a blank
            # one, because the radar is heading-up and the whole map turns with it.
            #
            # The filter is also marked uninitialised, which is the half that is easy
            # to miss. This is not a gyro-only coast that will be corrected back; it
            # is a hole, and afterwards there is no continuous estimate to protect.
            # Resuming from the old h would walk the recovered track home from a
            # bearing taken before a blackout of unknown length, at the slew cap, for
            # twenty seconds — the sub turned during the hole and nothing watched it.
            # Re-seeding from the first compass reading that comes back is the same
            # answer the gap branch gives, for the same reason.
            #
            # The clock still advances or the first tick after a recovery arrives as
            # an unintegrable gap on top of everything else.
            self._prev_t = t
            self.initialised = False
            return None

        # Initialise from the FIRST heading whether or not it is trustworthy. A wrong
        # start walks itself back within a few seconds of trusted samples; an unset
        # start is a NaN that poisons the whole track and every dive log written from it.
        #
        # With no compass there is nothing to initialise FROM: a gyro measures change,
        # not bearing, so a dive that starts with a dead magnetometer has no origin
        # for the integration and must stay uninitialised (and answer None) until one
        # arrives. Seeding from 0.0 there would start every such dive pointing north.
        # The same holds for a gyro that comes back before its magnetometer does.
        if not self.initialised:
            if not have_mag:
                self._prev_t = t
                return None
            self.h = wrap360(heading_deg)
            # b is deliberately NOT reset. It is 0.0 on a fresh filter already, and
            # this branch is also the recovery path from a dead IMU — the gyro's zero
            # offset is a property of the chip, not of the run, so a reseated
            # connector should not cost the several minutes of quiet, trusted samples
            # it took to learn it.
            self.initialised = True
            self._prev_t = t
            return self.h

        dt = t - (self._prev_t if self._prev_t is not None else t)
        if dt <= 0.0:
            # No time passed (a duplicated sample) or time went backwards (a clock step,
            # or a replay seeking). Integrate nothing. _prev_t still moves, because
            # holding on to a timestamp from the future wedges the filter shut until
            # the clock catches up — which on a Pi with no RTC can be a whole dive.
            self._prev_t = t
            return self.h

        if dt > self.GAP_S:
            # A gap: the loop stalled, or the log skips. Integrating a gyro rate across
            # a hole assumes the sub held that rate the whole time, and the one thing
            # known about the missing seconds is that nothing was known about them.
            # So: take the magnetometer's word if it is worth taking, otherwise hold.
            # With no gyro there is nothing that could have carried the heading across
            # the hole either, so a compass reading — trusted or not — is the only
            # thing on the far side of the gap that is about the present.
            self._prev_t = t
            if trusted or (have_mag and not have_gyro):
                self.h = wrap360(heading_deg)
            return self.h
        self._prev_t = t

        # 1. Predict on the bias-corrected gyro rate — WHEN THERE IS A GYRO TO READ.
        #    A dead gyro is not a zero rate. Skipping the term says "no rotation
        #    information this tick", which is the truth; running it on a substituted
        #    0.0 would walk the heading by the learned bias every tick on the
        #    authority of a chip that is not answering.
        if have_gyro:
            self.h = wrap360(self.h + (gyro_z_dps - self.b) * dt)

        if not have_mag:
            # THE COAST, and the only branch that is allowed to be one: the gyro just
            # carried the heading and there is no compass to correct it against.
            # Nothing to learn a bias from either — the innovation needs a
            # magnetometer to be an innovation about.
            return self.h

        # 3. Innovation — through wrap180, or 359 vs 1 becomes a 358° panic.
        e = heading_delta(heading_deg, self.h)

        # WHETHER TO CORRECT AT ALL. Normally only while trusted, so the thrusters'
        # magnetic error cannot walk the estimate around. That refusal is only safe
        # because the GYRO is carrying the heading in the meantime — take the gyro
        # away and "do not correct" means the bearing never changes again, which is
        # the frozen-bearing failure this round exists to close. So a hull whose gyro
        # is not answering tracks its compass even while it distrusts it: a bearing
        # 22° out under full throttle is a poor measurement, and a poor measurement
        # still beats a number nothing is measuring. Nothing is hidden by this —
        # mag_cal rides on the frame and the dead reckoner floors confidence on it.
        if trusted or not have_gyro:
            # 4. First-order blend, then SLEW-CAPPED. On re-entering trust after a long
            # gyro-only coast e can be tens of degrees; the cap is what turns that into
            # a smooth walk back instead of a jump. No special case for "just regained
            # trust" — the cap already is the special case.
            alpha = dt / (self.TAU_S + dt)
            cap = self.SLEW_DPS * dt
            self.h = wrap360(self.h + clamp(alpha * e, -cap, cap))

            # 5. Bias learning, only while trusted and only on small errors: a 30°
            # innovation is a magnetic disturbance or a bad calibration, and feeding it
            # to the bias estimator teaches the gyro a lie it then coasts on.
            #
            # SIGN — THE BRIEF SAYS `b + k_b*e*dt` AND THE BRIEF IS WRONG. Do not
            # "restore" it. The minus below is deliberate, it has been re-derived and
            # re-measured, and putting the plus back turns a converging filter into a
            # diverging one. Only the sign departs from §4a step 5; every magnitude
            # (K_B, B_MAX_DPS, MAX_BIAS_INNOVATION_DEG) is the brief's, unchanged.
            #
            # DERIVATION. The predict step above is `h += (gyro - b)*dt` — b is
            # SUBTRACTED — so b has to converge to the gyro's own offset. Sub stationary
            # on a bench at a true 100°, gyro reading a steady +2 °/s of pure bias, b
            # starting at 0: the prediction walks h clockwise to 100.2 while the compass
            # still says 100, so e = wrap180(100 - 100.2) = -0.2. The filter is reading
            # HIGH and the bias it needs to learn is POSITIVE, so a negative innovation
            # must push b UP: the correction runs AGAINST e.
            #
            # Linearise it and the same thing falls out. With x = heading error and
            # d = b - b_true, xdot = -d - x/TAU and d_dot = ∓K_B·x, so A = [[-1/τ, -1],
            # [∓K_B, 0]] and det A = ±K_B. Minus (this code) gives det > 0 with a
            # negative trace — stable. Plus (the brief) gives det < 0, a saddle.
            #
            # MEASURED on that bench case, noise-free, dt = 0.1 s, both rules run through
            # this exact arithmetic: this code settles to b = +2.0000 with zero heading
            # error (b = +1.77 by t = 100 s, +2.00 by t = 300 s). The brief's rule runs
            # the wrong way — unstable eigenvalue +0.0202 /s, a ~50 s growth time
            # constant — and it does NOT end at the B_MAX_DPS clamp: it stalls at
            # b = -2.96 once |e| grows past MAX_BIAS_INNOVATION_DEG and this very gate
            # switches the bias estimator off. That is the nastier outcome, and worth
            # knowing: the guard below does not save the filter, it just freezes it at a
            # standing ~9.9° heading error which then never walks back, because the only
            # machinery that could correct it has gated itself out.
            #
            # `trusted and have_gyro`, not just `abs(e)`: a bias is a property of a
            # gyro, so there is nothing to learn one about when no gyro is answering,
            # and writing into b then would corrupt the value the filter coasts on
            # the moment the chip comes back.
            if trusted and have_gyro and abs(e) < self.MAX_BIAS_INNOVATION_DEG:
                self.b = clamp(self.b - self.K_B * e * dt, -self.B_MAX_DPS, self.B_MAX_DPS)
        # When not trusted (and the gyro is alive): no correction at all. This is the
        # gyro-only coast, and it is surfaced (gyro_only) rather than inferred,
        # because the operator has to be able to tell "ignoring the compass on
        # purpose" from "compass broken".

        return self.h


class SpeedKF:
    """§4b — 1-D Kalman filter over [v, b_a]: water-relative forward speed and the
    forward accelerometer's bias. The ONLY Kalman filter in this pass.

    Why a filter at all: the throttle→speed LUT is an open-loop model that cannot
    know about a headwind, a fouled prop, or a shopping trolley. The paddlewheel can,
    but it is coarse (a handful of pulses per window), directionless, and stalls below
    ~0.1 m/s. Neither is trustworthy alone; the filter is where they are allowed to
    argue, with the accelerometer carrying the estimate between measurements.

    §2.2 COMPLIANCE — the boundary, and do not cross it. Acceleration is integrated
    ONCE here, into velocity, inside a loop that is continuously corrected by a
    measurement. Position still only ever integrates a velocity, once. Nothing in
    this file may integrate acceleration into a position; that is the double
    integration that turns a small bias into a kilometre of imaginary track.
    """

    SIGMA_A = 0.15          # accelerometer noise, m/s² — drives Q's velocity term
    # What the velocity's uncertainty grows on when NO accelerometer is answering.
    # The predict step then carries v forward unchanged, and "unchanged" is not a
    # measurement of steady speed — it is the absence of one, so the process noise
    # has to open up to the hull's whole dynamic range (about 1 m/s²; MockHardware
    # clamps its own model there for the same reason) instead of to a sensor's noise
    # figure. Without this the filter would grow CONFIDENT while blind, and stop
    # listening to the one instrument still talking to it, the paddlewheel.
    SIGMA_A_NO_ACCEL = 1.0
    Q_B = 1e-4              # bias random walk per second
    P0_V = 0.25             # initial variance on v (m/s)² — 0.5 m/s of "no idea"
    P0_B = 0.01             # initial variance on b_a
    R_PADDLE_FLOOR = 0.03   # the wheel is never better than this, whatever the maths says
    R_AT_REST = 0.05        # how firmly a stopped, unpowered sub is believed to be stopped
    REST_THROTTLE = 0.1     # below this the throttle is not asking for motion
    B_A_MAX = 0.5           # m/s²; beyond this it is a broken accelerometer, not a bias
    SIGN_TOL_MS = 0.05      # how far v may sit on the wrong side of the throttle's sign

    def __init__(self, m_per_pulse: float = 0.05, window_s: float = 0.5) -> None:
        # Defaults mirror NAV_M_PER_PULSE / NAV_PADDLE_WINDOW_S so this class stays
        # config-free and testable; the estimator injects the configured values.
        self.v: float = 0.0
        self.b_a: float = 0.0
        self.p00, self.p01 = self.P0_V, 0.0
        self.p10, self.p11 = 0.0, self.P0_B
        # One pulse per window is the smallest speed the wheel can report, so it is
        # also the size of its quantisation step. At canal speeds that is comparable
        # to the speed itself, and a filter that does not know its measurement is
        # coarse will chase the staircase instead of the water.
        self.quantization: float = (m_per_pulse / window_s) if window_s > 0 else self.R_PADDLE_FLOOR
        self.source: str = "kf-lut"     # "kf-paddle" once a real measurement lands

    def update(
        self,
        dt: float,
        accel_fwd_ms2: float | None,
        throttle: float,
        speed_ms_measured: float | None,
        lut_speed_ms: float,
    ) -> float:
        """One tick. `speed_ms_measured` is None when the paddlewheel is stale or absent,
        `accel_fwd_ms2` is None when no accelerometer is answering, and `lut_speed_ms` is
        the speed model's signed answer for this throttle. Returns v."""
        if dt <= 0.0:
            # Same rule as the heading filter: no time passed means no prediction, and
            # re-applying a measurement at an unchanged timestamp would shrink P for free.
            return self.v

        # ---- predict: v carried forward on the bias-corrected accelerometer ----
        # A missing accelerometer is not 0.0 m/s². 0.0 is the measurement "coasting"
        # and would let the filter claim it had watched the speed hold steady; the
        # absence of a reading says nothing about the speed at all, so the term is
        # dropped and the uncertainty opens up instead (SIGMA_A_NO_ACCEL). The
        # accelerometer dies with the compass — one BNO085 — so this branch is the
        # same failure as the heading filter's coast, seen from the speed side.
        have_accel = accel_fwd_ms2 is not None
        if have_accel:
            self.v += (accel_fwd_ms2 - self.b_a) * dt
        sigma_a = self.SIGMA_A if have_accel else self.SIGMA_A_NO_ACCEL
        p00, p01, p10, p11 = self.p00, self.p01, self.p10, self.p11
        # F = [[1, -dt], [0, 1]];  P <- F P Fᵀ + Q,  Q = diag(σ_a²·dt², q_b·dt)
        n00 = p00 - dt * (p01 + p10) + dt * dt * p11 + (sigma_a * dt) ** 2
        n01 = p01 - dt * p11
        n10 = p10 - dt * p11
        n11 = p11 + self.Q_B * dt

        # ---- choose exactly ONE measurement for this tick ----
        if speed_ms_measured is not None:
            # The wheel spun: a real measurement of the water. abs() because the wheel
            # is mechanically incapable of knowing direction — only the throttle knows
            # which way the sub was asked to go, and it is the only vote available.
            z = _sign(throttle) * abs(speed_ms_measured)
            r = max(self.R_PADDLE_FLOOR, self.quantization) ** 2
            self.source = "kf-paddle"
        elif abs(throttle) < self.REST_THROTTLE:
            # Stale wheel AND nobody is asking for motion: the sub really is stopped.
            # This branch exists to kill accelerometer bias drift at rest — without a
            # zero-lock, b_a wanders during every pause and the next burst of thrust
            # starts from a velocity that was never real.
            z = 0.0
            r = self.R_AT_REST ** 2
            self.source = "kf-lut"
        else:
            # Stale wheel while thrusting: fall back to the speed model, but as a WEAK
            # measurement, never a crisp one. The LUT is exactly what cannot notice a
            # snagged sub, so it is given a variance that grows with its own claim.
            z = lut_speed_ms
            r = (0.3 * abs(z) + 0.1) ** 2
            self.source = "kf-lut"

        # ---- standard scalar update, H = [1, 0] ----
        # k1 is the gain that pushes the residual into the ACCELEROMETER's bias. With
        # no accelerometer answering there is no bias to attribute anything to, and
        # doing it anyway would quietly rewrite b_a from paddlewheel noise — a
        # "calibration" learned about a chip that is not there, which the filter then
        # subtracts from the first real reading after the connector is pushed back on.
        y = z - self.v
        s_cov = n00 + r
        k0 = n00 / s_cov
        k1 = (n10 / s_cov) if have_accel else 0.0
        self.v += k0 * y
        self.b_a += k1 * y
        self.p00 = (1.0 - k0) * n00
        self.p01 = (1.0 - k0) * n01
        self.p10 = n10 - k1 * n00
        self.p11 = n11 - k1 * n01

        # ---- clamps ----
        # The wheel is directionless, so nothing in the measurement can contradict the
        # throttle's sign; a v that disagrees with it came from integrated accelerometer
        # noise. A small tolerance is allowed so a sub decelerating through zero is not
        # snapped. Zero throttle asserts nothing — a coasting sub genuinely is moving.
        if throttle > 0.0 and self.v < -self.SIGN_TOL_MS:
            self.v = -self.SIGN_TOL_MS
        elif throttle < 0.0 and self.v > self.SIGN_TOL_MS:
            self.v = self.SIGN_TOL_MS
        self.b_a = clamp(self.b_a, -self.B_A_MAX, self.B_A_MAX)
        return self.v


class SnagDetector:
    """§4c — high thrust, sustained, going nowhere.

    This is the detector the paddlewheel was bought for: without it a sub pinned on a
    shopping trolley keeps "moving" across the map at whatever the throttle is worth,
    and the operator drives a phantom for as long as their patience lasts. It runs in
    BOTH estimator backends, because it is a safety signal and not an estimator
    feature — which backend is configured must not decide whether the operator is told
    the sub is stuck.

    LUT-derived speed is not admissible evidence here. The speed model is a function of
    throttle alone, so under exactly the conditions this detector cares about it
    reports a healthy speed with total confidence. Only a measurement (or a filter
    estimate currently corrected by one) counts.
    """

    THRUST = 0.5            # above this the sub should be visibly moving
    STOPPED_MS = 0.05       # below this it is not
    SUSTAIN_S = 2.0         # long enough that a kick off a wall or a weed does not count

    def __init__(self) -> None:
        self.snagged: bool = False
        self._since: float | None = None
        self._ever_measured: bool = False

    def update(self, t: float, left: float, right: float, measured_speed_ms: float | None) -> bool:
        """`measured_speed_ms` is the paddle-backed speed, or None when nothing measured
        it this tick (stale wheel, or no wheel fitted). Returns the latest snagged state."""
        if measured_speed_ms is not None:
            self._ever_measured = True

        pushing = thrust_level(left, right) > self.THRUST
        if measured_speed_ms is None:
            # A silent wheel while thrusting IS the snag signature — but only once the
            # wheel has proved it exists. A hull built without a paddlewheel reports None
            # forever, and a detector that fired on that would raise a snag alarm on every
            # normal run until the operator learned to ignore snag alarms entirely. Not
            # knowing must read as cannot-tell, even when the alarm would be the safe
            # guess. Cost of this rule, stated honestly: a sub that is already snagged
            # before the wheel has ever turned is not caught until it moves once.
            stopped = self._ever_measured
        else:
            stopped = abs(measured_speed_ms) < self.STOPPED_MS

        if pushing and stopped:
            if self._since is None or t < self._since:
                self._since = t          # also re-seeds if time went backwards (replay seek)
            self.snagged = (t - self._since) > self.SUSTAIN_S
        else:
            self._since = None
            self.snagged = False
        return self.snagged

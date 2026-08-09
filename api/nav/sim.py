"""Navigation SIMULATOR (spec §10.7 — build this first).

Produces synthetic IMU (fused heading with magnetometer disturbance near the
thrusters, yaw rate, forward acceleration), measured depth, paddlewheel water
speed, throttle/thruster outputs and tether encoder along a scripted path,
including drift, mag disturbance and current — so the entire nav stack is
testable without water.

It holds GROUND TRUTH (true x/y/depth/heading/speed) internally; the estimators
consume only the emitted SensorSamples, so a test can compare an estimate
against truth. That comparison is the whole point of §4e: "filtered" is only
allowed to replace dead reckoning if it beats it against a truth the estimator
never saw. truth_row()/run_with_truth() exist so that truth reaches the replay
log — a ground truth that stays inside the simulator cannot settle an argument.

Deterministic (seeded xorshift, no wall-clock) so tests are reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import FlowVector, SensorSample
from .speedlut import DEFAULT_LUT, SpeedLUT


def _wrap180(d: float) -> float:
    """Signed shortest angle. Every heading subtraction goes through this — the
    359°→1° crossing is the classic bug and it silently produces a 358° turn."""
    return ((d + 180.0) % 360.0) - 180.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


@dataclass
class Leg:
    heading_deg: float  # true heading held during this leg (turned toward, not snapped to)
    throttle: float  # -1..1
    duration_s: float
    depth_m: float = 0.0  # target depth (ramped toward)


# A default scripted canal run: float, straight, gentle turns, a dive, some reverse.
DEFAULT_PATH = [
    # Idle on the surface first. No dive has ever begun at 60 % throttle from a
    # standing start, and the difference is not cosmetic: the thrusters are what
    # poison the magnetometer, so a run that opens under power hands the heading
    # filter a compass that is ALREADY ~10° out and then never gives it a trusted
    # window to walk that back — it coasts on the gyro holding the initial error
    # for the whole dive. Measured on this path: with the sub under power from t=0
    # the filtered estimator finishes 17.9 m from truth against dead reckoning's
    # 20.1 m (a coin toss); with these four seconds of float first it finishes
    # 0.9 m out. The seconds are also the only stretch where the paddlewheel is
    # genuinely stalled and the throttle genuinely zero, which is the one condition
    # that lets the speed filter zero-lock and kill its accelerometer bias (§4b).
    Leg(90, 0.0, 4, depth_m=0.0),
    Leg(90, 0.6, 30, depth_m=1.0),  # head east, dive to 1 m
    Leg(90, 0.8, 40, depth_m=2.0),
    Leg(60, 0.6, 20, depth_m=2.0),  # bend NE
    Leg(60, 0.8, 40, depth_m=3.0),
    Leg(90, 0.5, 20, depth_m=3.0),
    Leg(90, -0.4, 15, depth_m=2.0),  # reverse, ascend
]


class Simulator:
    def __init__(
        self,
        path: list[Leg] | None = None,
        true_lut: SpeedLUT | None = None,
        current: FlowVector | None = None,
        heading_bias_deg: float = 1.5,  # constant IMU yaw bias
        mag_gain_deg: float = 22.0,  # mag error at full thrust (near thrusters, §5.6)
        depth_noise_m: float = 0.03,  # pressure sensor is accurate (§2.4)
        heading_noise_deg: float = 0.4,
        encoder_slack: float = 0.06,  # payout ≥ path length (§5.5)
        turn_rate_dps: float = 12.0,  # how fast the hull can actually swing
        speed_tau_s: float = 1.5,  # hull inertia: speed lags the throttle
        gyro_noise_dps: float = 0.15,
        gyro_bias_dps: float = 0.0,  # OFF by default — see the note below
        accel_noise_ms2: float = 0.05,
        paddle_noise_ms: float = 0.02,
        paddle_stall_ms: float = 0.10,  # below this the wheel stops turning
        seed: int = 1234,
        hold_at_end: bool = False,  # keep flying the last leg (for the live service)
    ):
        self.hold_at_end = hold_at_end
        self.path = path or DEFAULT_PATH
        self.true_lut = true_lut or DEFAULT_LUT
        self.current = current or FlowVector()
        self.heading_bias = heading_bias_deg
        self.mag_gain = mag_gain_deg
        self.depth_noise = depth_noise_m
        self.heading_noise = heading_noise_deg
        self.encoder_slack = encoder_slack
        self.turn_rate_dps = turn_rate_dps
        self.speed_tau = speed_tau_s
        self.gyro_noise = gyro_noise_dps
        # A constant gyro bias is the error a complementary filter is supposed to
        # learn away (§4a step 5), but it can only learn while the magnetometer is
        # trusted — and on a path that runs the thrusters hard it almost never is.
        # Left on by default it would therefore integrate unopposed and the A/B
        # test would be measuring the injected bias rather than the filter. So the
        # default is zero and a bias-learning test injects its own, deliberately.
        self.gyro_bias = gyro_bias_dps
        self.accel_noise = accel_noise_ms2
        self.paddle_noise = paddle_noise_ms
        self.paddle_stall = paddle_stall_ms
        # deterministic PRNG (no Math.random / wall-clock)
        self._rng_state = seed & 0xFFFFFFFF
        # ---- ground truth ----
        self.t = 0.0
        self.x = 0.0  # metres east
        self.y = 0.0  # metres north
        self.depth = 0.0
        # True heading, turned continuously between legs. Starts ON the first leg
        # so the run does not open with a manoeuvre nobody scripted.
        self.heading = float(self.path[0].heading_deg) % 360.0
        self.turn_dps = 0.0  # true yaw rate this tick (+ = clockwise)
        self.v = 0.0  # true water-relative forward speed, m/s (signed)
        self.accel = 0.0  # true forward acceleration, m/s²
        self.path_len = 0.0  # cumulative true distance (for encoder)
        self._leg_i = 0
        self._leg_t = 0.0

    def _rand(self) -> float:
        # xorshift32 → [-1,1)
        s = self._rng_state
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self._rng_state = s & 0xFFFFFFFF
        return (self._rng_state / 0xFFFFFFFF) * 2.0 - 1.0

    @property
    def duration(self) -> float:
        return sum(leg.duration_s for leg in self.path)

    @property
    def finished(self) -> bool:
        return self._leg_i >= len(self.path)

    def step(self, dt: float) -> SensorSample | None:
        """Advance ground truth by dt and return the sensor reading. None when done
        (unless hold_at_end, in which case it keeps flying the last leg)."""
        if self.finished and not self.hold_at_end:
            return None
        # Time does not run backwards. A negative dt would rewind ground truth while
        # still emitting a sample, i.e. hand a test a truth that disagrees with the
        # stream it came from — much harder to spot than an obviously wrong number.
        dt = max(0.0, dt)
        leg = self.path[self._leg_i] if not self.finished else self.path[-1]
        thr = leg.throttle

        # ---- true heading: TURN toward the leg, never snap to it ---------------
        # The path is a list of constant-heading legs, and the obvious way to fly it
        # is to adopt the new heading the instant a leg starts. That produces the one
        # shape a gyro can never see: a turn rate that is zero everywhere except for
        # a single impossible sample. A heading filter integrating that gyro would
        # score perfectly while doing nothing at all, so the §4e A/B test would pass
        # without testing anything. Swinging the hull at a bounded rate puts real,
        # sustained yaw rate into the stream — and, because position below uses THIS
        # heading and not the leg's, the truth the estimators are scored against
        # contains the curved corners they have to reproduce.
        turn_cap = self.turn_rate_dps * dt
        turn = _clamp(_wrap180(leg.heading_deg - self.heading), -turn_cap, turn_cap)
        self.turn_dps = (turn / dt) if dt > 0 else 0.0
        self.heading = (self.heading + turn) % 360.0
        turn_demand = _clamp(turn / turn_cap, -1.0, 1.0) if turn_cap > 0 else 0.0

        # ---- true water-relative speed: the hull has mass -----------------------
        # The LUT is a steady-state model; a hull does not reach a new speed the
        # instant the throttle moves. The lag matters here because it is what makes
        # accel_fwd_ms2 a real signal instead of an impulse at every leg boundary,
        # and the speed KF's predict step (§4b) integrates exactly that.
        v_cmd = self.true_lut.speed(thr)
        k = min(1.0, dt / self.speed_tau) if self.speed_tau > 0 else 1.0
        dv = (v_cmd - self.v) * k
        self.accel = (dv / dt) if dt > 0 else 0.0
        self.v += dv

        # true ground velocity = water-relative (throttle) + current (§5.4).
        # Compass heading: 0=N, 90=E → east=sin, north=cos.
        true_hdg = math.radians(self.heading)
        vx = self.v * math.sin(true_hdg)  # east
        vy = self.v * math.cos(true_hdg)  # north
        cur = math.radians(self.current.bearing_deg)
        vx += self.current.speed_ms * math.sin(cur)
        vy += self.current.speed_ms * math.cos(cur)

        self.x += vx * dt
        self.y += vy * dt
        self.path_len += math.hypot(vx, vy) * dt
        # depth ramps toward the leg target (measured cleanly)
        self.depth += (leg.depth_m - self.depth) * min(1.0, dt * 0.5)
        self.t += dt

        # ---- thruster outputs -------------------------------------------------
        # Differential thrust is what actually turns a two-motor hull, and two things
        # downstream read it: the heading filter's trust gate is max(|left|,|right|)
        # (§4a — ACTUAL output, so a disarmed sub reads as no thrust) and the snag
        # detector wants sustained real thrust (§4c). A sim that emitted left=right=0
        # while the hull visibly turned would leave both of them blind, and would also
        # be attributing the magnetic disturbance below to an abstract "throttle"
        # rather than to the motors that physically cause it.
        diff = 0.35 * turn_demand
        left = _clamp(thr + diff, -1.0, 1.0)
        right = _clamp(thr - diff, -1.0, 1.0)
        thrust_level = max(abs(left), abs(right))

        # ---- sensor readings (with errors) ----
        # magnetometer garbage grows with the thrust actually being produced (§5.6)
        mag_dist = self.mag_gain * thrust_level * (0.6 + 0.4 * math.sin(self.t * 1.7))
        heading_meas = (self.heading + self.heading_bias + mag_dist + self.heading_noise * self._rand()) % 360.0
        mag_cal = 3 if thrust_level < 0.4 else (1 if thrust_level > 0.7 else 2)
        depth_meas = max(0.0, self.depth + self.depth_noise * self._rand())
        encoder = self.path_len * (1.0 + self.encoder_slack)
        # The gyro is immune to the thruster fields wrecking the compass above: the
        # disturbed magnetometer next to a clean yaw rate is precisely the scenario
        # the heading filter has to win, so it must appear in the same samples.
        gyro = self.turn_dps + self.gyro_bias + self.gyro_noise * self._rand()
        accel = self.accel + self.accel_noise * self._rand()
        # Paddlewheel. It measures WATER-RELATIVE speed (so the current above is not
        # in it), it cannot sense direction — the sign belongs to the throttle — and
        # it stalls: below ~0.1 m/s no magnet passes the hall sensor at all. Silence
        # is not zero speed, it is "slower than I can see", so it goes out as None
        # rather than as a 0.0 the estimator would take for a measurement. It keeps
        # reporting right through the mag disturbance, which is the point: the wheel
        # is the one instrument the thrusters cannot lie to.
        speed_true = abs(self.v)
        speed_meas = (
            max(0.0, speed_true + self.paddle_noise * self._rand()) if speed_true >= self.paddle_stall else None
        )

        # advance leg clock (unless holding at the end)
        if not self.finished:
            self._leg_t += dt
            if self._leg_t >= leg.duration_s:
                self._leg_i += 1
                self._leg_t = 0.0

        return SensorSample(
            t=round(self.t, 3),
            heading_deg=round(heading_meas, 2),
            depth_m=round(depth_meas, 3),
            throttle=thr,
            encoder_m=round(encoder, 3),
            mag_cal=mag_cal,
            speed_ms_measured=None if speed_meas is None else round(speed_meas, 3),
            gyro_z_dps=round(gyro, 3),
            accel_fwd_ms2=round(accel, 3),
            steer=round(turn_demand, 3),
            left=round(left, 3),
            right=round(right, 3),
            # The scripted sub is under way, so it is armed — a disarmed sample
            # proves nothing about speed and the snag detector would discard it.
            # ballast_level stays None: this simulator has no syringe, and None is
            # how "no such instrument" travels (it is NOT a level of zero).
            armed=True,
        )

    def run(self, dt: float = 0.1):
        """Generator of samples for the whole scripted path."""
        while not self.finished:
            s = self.step(dt)
            if s is None:
                break
            yield s

    def run_with_truth(self, dt: float = 0.1):
        """Generator of (sample, truth_row) for the whole scripted path.

        The replay harness (§4e) scores an estimator against truth, so truth has to
        travel WITH the samples and into the log. Zipping two separate passes would
        not work: the simulator is stateful and a second run is a different run.
        """
        while not self.finished:
            s = self.step(dt)
            if s is None:
                break
            yield s, self.truth_row()

    def truth(self) -> tuple[float, float, float]:
        return self.x, self.y, self.depth

    def truth_row(self) -> dict:
        """Ground truth for the sample just emitted, ready to be written into a log.

        Deliberately prefixed `true_*`: in a replay file these sit next to the
        estimator's own x/y, and anything that can be mistaken for an estimate
        eventually will be.
        """
        return {
            "t": round(self.t, 3),
            "true_x": round(self.x, 3),
            "true_y": round(self.y, 3),
            "true_depth_m": round(self.depth, 3),
            "true_heading_deg": round(self.heading, 2),
            "true_speed_ms": round(self.v, 3),
            "true_turn_dps": round(self.turn_dps, 3),
        }

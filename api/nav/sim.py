"""Navigation SIMULATOR (spec §10.7 — build this first).

Produces synthetic IMU (heading + magnetometer disturbance near thrusters),
measured depth, throttle, and tether-encoder streams along a scripted path,
including drift, mag disturbance, and current — so the entire nav stack is
testable without water.

It holds GROUND TRUTH (true x/y/depth) internally; dead-reckoning consumes only
the emitted SensorSamples, so a test can compare DR output against truth to
verify the ~5-15% linear error target.

Deterministic (seeded, no wall-clock) so tests are reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import FlowVector, SensorSample
from .speedlut import DEFAULT_LUT, SpeedLUT


@dataclass
class Leg:
    heading_deg: float      # true heading held during this leg
    throttle: float         # -1..1
    duration_s: float
    depth_m: float = 0.0     # target depth (ramped toward)


# A default scripted canal run: straight, gentle turns, a dive, some reverse.
DEFAULT_PATH = [
    Leg(90, 0.6, 30, depth_m=1.0),    # head east, dive to 1 m
    Leg(90, 0.8, 40, depth_m=2.0),
    Leg(60, 0.6, 20, depth_m=2.0),    # bend NE
    Leg(60, 0.8, 40, depth_m=3.0),
    Leg(90, 0.5, 20, depth_m=3.0),
    Leg(90, -0.4, 15, depth_m=2.0),   # reverse, ascend
]


class Simulator:
    def __init__(
        self,
        path: list[Leg] | None = None,
        true_lut: SpeedLUT | None = None,
        current: FlowVector | None = None,
        heading_bias_deg: float = 1.5,        # constant IMU yaw bias
        mag_gain_deg: float = 22.0,           # mag error at full throttle (near thrusters, §5.6)
        depth_noise_m: float = 0.03,          # pressure sensor is accurate (§2.4)
        heading_noise_deg: float = 0.4,
        encoder_slack: float = 0.06,          # payout ≥ path length (§5.5)
        seed: int = 1234,
        hold_at_end: bool = False,            # keep flying the last leg (for the live service)
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
        # deterministic PRNG (no Math.random / wall-clock)
        self._rng_state = seed & 0xFFFFFFFF
        # ground truth
        self.t = 0.0
        self.x = 0.0            # metres east
        self.y = 0.0            # metres north
        self.depth = 0.0
        self.path_len = 0.0     # cumulative true distance (for encoder)
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
        return sum(l.duration_s for l in self.path)

    @property
    def finished(self) -> bool:
        return self._leg_i >= len(self.path)

    def step(self, dt: float) -> SensorSample | None:
        """Advance ground truth by dt and return the sensor reading. None when done
        (unless hold_at_end, in which case it keeps flying the last leg)."""
        if self.finished and not self.hold_at_end:
            return None
        leg = self.path[self._leg_i] if not self.finished else self.path[-1]
        thr = leg.throttle
        true_hdg = math.radians(leg.heading_deg)

        # true ground velocity = water-relative (throttle) + current (§5.4).
        # Compass heading: 0=N, 90=E → east=sin, north=cos.
        v = self.true_lut.speed(thr)
        vx = v * math.sin(true_hdg)          # east
        vy = v * math.cos(true_hdg)          # north
        cur = math.radians(self.current.bearing_deg)
        vx += self.current.speed_ms * math.sin(cur)
        vy += self.current.speed_ms * math.cos(cur)

        self.x += vx * dt
        self.y += vy * dt
        self.path_len += math.hypot(vx, vy) * dt
        # depth ramps toward the leg target (measured cleanly)
        self.depth += (leg.depth_m - self.depth) * min(1.0, dt * 0.5)
        self.t += dt

        # ---- sensor readings (with errors) ----
        # magnetometer garbage grows with |throttle| (thruster proximity, §5.6)
        mag_dist = self.mag_gain * abs(thr) * (0.6 + 0.4 * math.sin(self.t * 1.7))
        heading_meas = (leg.heading_deg + self.heading_bias + mag_dist
                        + self.heading_noise * self._rand()) % 360.0
        mag_cal = 3 if abs(thr) < 0.4 else (1 if abs(thr) > 0.7 else 2)
        depth_meas = max(0.0, self.depth + self.depth_noise * self._rand())
        encoder = self.path_len * (1.0 + self.encoder_slack)

        # advance leg clock (unless holding at the end)
        if not self.finished:
            self._leg_t += dt
            if self._leg_t >= leg.duration_s:
                self._leg_i += 1
                self._leg_t = 0.0

        return SensorSample(
            t=round(self.t, 3), heading_deg=round(heading_meas, 2),
            depth_m=round(depth_meas, 3), throttle=thr,
            encoder_m=round(encoder, 3), mag_cal=mag_cal,
        )

    def run(self, dt: float = 0.1):
        """Generator of samples for the whole scripted path."""
        while not self.finished:
            s = self.step(dt)
            if s is None:
                break
            yield s

    def truth(self) -> tuple[float, float, float]:
        return self.x, self.y, self.depth

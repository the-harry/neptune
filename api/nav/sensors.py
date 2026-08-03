"""Navigation sensor source — sim (default) or real hardware (BNO085 / MS5837 /
tether encoder, spec §5.1). The service reads SensorSamples from here at dr_hz.
"""
from __future__ import annotations

import logging

from .config import settings
from .models import SensorSample
from .sim import Simulator

log = logging.getLogger("neptune.nav.sensors")


class SensorSource:
    is_sim = False
    def read(self, dt: float) -> SensorSample | None: ...
    def reset(self) -> None: ...


class SimSensorSource(SensorSource):
    is_sim = True

    def __init__(self) -> None:
        self._clock = 0.0
        self._sim = Simulator(hold_at_end=True)   # never stops; flies the last leg
        log.info("nav sensors: SIMULATOR (scripted path, drift + mag + current)")

    def read(self, dt: float) -> SensorSample | None:
        self._clock += dt
        s = self._sim.step(dt)
        if s is not None:
            s.t = round(self._clock, 3)           # continuous clock across the run
        return s

    def reset(self) -> None:
        self._clock = 0.0
        self._sim = Simulator(hold_at_end=True)


class RealSensorSource(SensorSource):
    is_sim = False

    def __init__(self) -> None:
        # TODO(hardware): open BNO085 (I2C, fused yaw + cal status), MS5837 (I2C,
        # depth), and the tether rotary encoder. Keep handles on self.
        self._t = 0.0
        log.info("nav sensors: REAL (GPIO/I2C) — wire in hardware")

    def read(self, dt: float) -> SensorSample | None:
        self._t += dt
        # TODO(hardware): read heading_deg + mag_cal from BNO085, depth from MS5837,
        # throttle from the control plane, encoder_m from the spool encoder.
        return SensorSample(t=round(self._t, 3), heading_deg=0.0, depth_m=0.0,
                            throttle=0.0, encoder_m=0.0, mag_cal=3)

    def reset(self) -> None:
        self._t = 0.0


def get_sensor_source() -> SensorSource:
    choice = settings.sensor_backend.lower()
    if choice == "real":
        try:
            return RealSensorSource()
        except Exception as exc:  # noqa: BLE001
            log.warning("RealSensorSource init failed (%s); using SIM", exc)
            return SimSensorSource()
    return SimSensorSource()

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


class VehicleSensorSource(SensorSource):
    """Sensors read from the LIVE vehicle instead of a script.

    Until the IMU/depth/encoder hardware is wired, the best available truth about
    where the sub is pointing and how hard it is being driven is the ROV control
    plane itself - heading from the hardware layer, depth from the pressure
    reading, throttle from what the operator is actually commanding.

    This exists because the alternative was worse than useless: NAV_SENSORS
    defaulted to the scripted simulator, so the map traced a canned route with
    preset heading legs and IGNORED the operator entirely. Steering the vehicle
    changed nothing on the map, which reads exactly as "I can only go straight".

    is_sim stays True while the underlying hardware is mocked, so the dashboard
    keeps flagging it honestly rather than presenting a simulation as ground truth.
    """

    def __init__(self, get_rov):
        self._get_rov = get_rov
        self._t = 0.0
        self._payout = 0.0
        log.info("nav sensors: VEHICLE (heading/depth/throttle from the live ROV state)")

    @property
    def is_sim(self) -> bool:
        rov = self._get_rov()
        hw = getattr(rov, "hw", None)
        return bool(getattr(hw, "is_mock", True))

    def read(self, dt: float):
        rov = self._get_rov()
        if rov is None:
            return None
        self._t += dt
        hw = rov.hw
        try:
            heading = float(hw.read_heading()) % 360.0
        except Exception:  # noqa: BLE001
            heading = 0.0
        try:
            pressure = float(hw.read_pressure())
            depth = max(0.0, (pressure - settings_surface_psi()) / settings_psi_per_m())
        except Exception:  # noqa: BLE001
            depth = 0.0
        # Use the ACTUAL thruster output, not the commanded throttle.
        #
        # Heading comes from the hardware, which only turns when the thrusters really
        # run - so it is zero while disarmed or in failsafe. Taking forward speed from
        # the *commanded* throttle instead produced the exact reported symptom: the sub
        # advanced but never turned, i.e. "I can only go straight". Averaging left/right
        # keeps both quantities on the same footing: disarmed means neither.
        left = float(getattr(rov, "left", 0.0))
        right = float(getattr(rov, "right", 0.0))
        throttle = (left + right) / 2.0
        # No spool encoder yet. Payout is used only as an UPPER BOUND on range, so
        # integrating commanded speed over time is a safe over-estimate: it can only
        # loosen the clamp, never fake precision the vehicle does not have.
        self._payout += abs(throttle) * dt * 1.2
        return SensorSample(t=round(self._t, 3), heading_deg=round(heading, 2),
                            depth_m=round(depth, 3), throttle=round(throttle, 3),
                            encoder_m=round(self._payout, 3), mag_cal=3)

    def reset(self) -> None:
        self._t = 0.0
        self._payout = 0.0


def settings_surface_psi() -> float:
    try:
        from config import settings as rov_settings
        return float(rov_settings.surface_pressure_psi)
    except Exception:  # noqa: BLE001
        return 14.7


def settings_psi_per_m() -> float:
    try:
        from config import settings as rov_settings
        return float(rov_settings.psi_per_meter) or 1.42
    except Exception:  # noqa: BLE001
        return 1.42


def get_sensor_source(get_rov=None) -> SensorSource:
    choice = settings.sensor_backend.lower()
    if choice == "sim":
        return SimSensorSource()                 # scripted demo path, ignores the operator
    if choice == "real":
        try:
            return RealSensorSource()
        except Exception as exc:  # noqa: BLE001
            log.warning("RealSensorSource init failed (%s); using SIM", exc)
            return SimSensorSource()
    # default ("vehicle"/"auto"): follow the actual vehicle when one is bound,
    # falling back to the scripted sim only when nav runs standalone.
    if get_rov is not None:
        return VehicleSensorSource(get_rov)
    return SimSensorSource()

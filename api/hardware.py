"""Hardware abstraction layer — GPIO / serial / I2C live behind this interface.

Two backends:
  * MockHardware — a self-contained bench simulator. Fully working with no
    hardware attached, so the whole server (and the client) can be exercised on
    a laptop. `is_mock` is True → telemetry carries `mock: true`.
  * RealHardware — the real Pi backend. Structure + call sites are in place;
    the actual actuator/sensor wiring is marked `TODO(hardware)` for you to fill
    with your GPIO/serial/I2C code (pins live in one place at the top).

`get_hardware()` selects one based on settings.hardware_backend ("auto" tries
real, falls back to mock).

All methods MUST be fast and non-blocking — they are called from the asyncio
event loop. Real GPIO writes are effectively instant; anything slow (a bus
transaction) should be cached and refreshed off the hot path.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from config import settings

log = logging.getLogger("neptune.hw")

Which = str  # "green" | "white"
BallastDir = str  # "fill" | "empty" | "hold"


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
    @abstractmethod
    def get_ballast_level(self) -> float: ...  # 0..1
    @abstractmethod
    def read_pressure(self) -> float: ...      # PSI
    @abstractmethod
    def read_heading(self) -> float: ...       # degrees 0..360
    @abstractmethod
    def read_leak(self) -> str: ...            # "NORMAL" | "WARN" | "FLOOD"
    @abstractmethod
    def read_voltage(self) -> float: ...       # volts
    @abstractmethod
    def link_quality(self) -> int: ...         # 0..4 bars, -1 = tether down

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
    is_mock = True

    def __init__(self) -> None:
        self._armed = False
        self._left = 0.0
        self._right = 0.0
        self._pan = 0.0
        self._tilt = 0.0
        self._ballast = 0.40          # 0..1
        self._ballast_dir: BallastDir = "hold"
        self._magnet = False
        self._lights = {"green": (True, 0.8), "white": (False, 0.2)}
        self._voltage = 24.8
        self._heading = 284.0
        self._dropped = False
        self._leak = "NORMAL"
        log.info("MockHardware active (bench simulation)")

    # actuators
    def set_armed(self, on: bool) -> None:
        self._armed = on
        if not on:
            self._left = self._right = 0.0

    def set_thrusters(self, left: float, right: float) -> None:
        self._left, self._right = left, right

    def set_camera(self, pan: float, tilt: float) -> None:
        self._pan, self._tilt = pan, tilt

    def ballast_pump(self, direction: BallastDir) -> None:
        self._ballast_dir = direction

    def ballast_home(self) -> None:
        self._ballast_dir = "hold"
        self._ballast = 0.5

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
        log.warning("MOCK: drop-weight released (irreversible)")

    # readbacks
    def get_magnet(self) -> bool:
        return self._magnet

    def get_light(self, which: Which) -> tuple[bool, float]:
        return self._lights[which]

    def get_ballast_level(self) -> float:
        return self._ballast

    def read_pressure(self) -> float:
        # Fake a depth from ballast fill so the gauge moves plausibly.
        depth_m = self._ballast * 9.0
        return settings.surface_pressure_psi + depth_m * settings.psi_per_meter

    def read_heading(self) -> float:
        return self._heading % 360.0

    def read_leak(self) -> str:
        return self._leak

    def read_voltage(self) -> float:
        return self._voltage

    def link_quality(self) -> int:
        return 4

    # sim advance
    def update(self, dt: float) -> None:
        rate = 0.25  # tank fraction per second
        if self._ballast_dir == "fill":
            self._ballast = min(1.0, self._ballast + rate * dt)
        elif self._ballast_dir == "empty":
            self._ballast = max(0.0, self._ballast - rate * dt)
        # gentle heading drift when thrusters differ; cosmetic battery sag
        self._heading = (self._heading + (self._left - self._right) * 20.0 * dt) % 360.0
        self._voltage = max(20.0, self._voltage - 0.0004 * dt)

    # test hook: toggle a simulated leak from the console/tests
    def _set_leak(self, state: str) -> None:
        self._leak = state


# ---------------------------------------------------------------------------
# Real Pi backend — wire your GPIO / serial / I2C here.
# ---------------------------------------------------------------------------
class RealHardware(HardwareBase):
    is_mock = False

    # ---- pin / bus map (single source of truth) -------------------------
    # TODO(hardware): set to your wiring.
    PIN_THRUSTER_L = 12   # PWM
    PIN_THRUSTER_R = 13   # PWM
    PIN_CAM_PAN = 18      # servo PWM
    PIN_CAM_TILT = 19     # servo PWM
    PIN_BALLAST_FILL = 5
    PIN_BALLAST_EMPTY = 6
    PIN_MAGNET = 16
    PIN_DROPWEIGHT = 26
    PIN_LIGHT_GREEN = 20  # PWM (dimmable)
    PIN_LIGHT_WHITE = 21  # PWM (dimmable)

    def __init__(self) -> None:
        # TODO(hardware): init gpiozero / lgpio / pigpio, I2C (pressure, IMU),
        # ADC (voltage). Keep handles on self. Raise on failure so get_hardware()
        # can fall back to the mock in "auto" mode.
        self._magnet = False
        self._lights = {"green": (False, 0.0), "white": (False, 0.0)}
        log.info("RealHardware active (GPIO)")

    def set_armed(self, on: bool) -> None:
        # TODO(hardware): enable/disable ESC arming signal.
        if not on:
            self.set_thrusters(0.0, 0.0)

    def set_thrusters(self, left: float, right: float) -> None:
        # TODO(hardware): map -1..1 to ESC PWM on PIN_THRUSTER_L/R.
        ...

    def set_camera(self, pan: float, tilt: float) -> None:
        # TODO(hardware): map -1..1 to servo pulse widths.
        ...

    def ballast_pump(self, direction: BallastDir) -> None:
        # TODO(hardware): drive fill/empty pump/valve; "hold" = both off.
        ...

    def ballast_home(self) -> None:
        # TODO(hardware): run pump toward the neutral position.
        ...

    def set_magnet(self, on: bool) -> None:
        # TODO(hardware): drive the electromagnet MOSFET.
        self._magnet = on

    def set_light(self, which: Which, on: bool) -> None:
        _, lvl = self._lights[which]
        self._lights[which] = (on, lvl)
        # TODO(hardware): PWM duty = lvl if on else 0.

    def set_light_level(self, which: Which, level: float) -> None:
        on, _ = self._lights[which]
        self._lights[which] = (on, max(0.0, min(1.0, level)))
        # TODO(hardware): update PWM duty.

    def release_dropweight(self) -> None:
        # TODO(hardware): fire the drop-weight release (servo/burn-wire). Irreversible.
        log.warning("drop-weight release commanded")

    def get_magnet(self) -> bool:
        return self._magnet

    def get_light(self, which: Which) -> tuple[bool, float]:
        return self._lights[which]

    def get_ballast_level(self) -> float:
        # TODO(hardware): read the ballast position sensor (0..1).
        return 0.0

    def read_pressure(self) -> float:
        # TODO(hardware): read the depth/pressure sensor (e.g. MS5837 over I2C).
        return settings.surface_pressure_psi

    def read_heading(self) -> float:
        # TODO(hardware): read the IMU/compass heading.
        return 0.0

    def read_leak(self) -> str:
        # TODO(hardware): read leak probes -> "NORMAL" | "WARN" | "FLOOD".
        return "NORMAL"

    def read_voltage(self) -> float:
        # TODO(hardware): read pack voltage via ADC / INA219.
        return 0.0

    def link_quality(self) -> int:
        # TODO(hardware): tether/RF link quality 0..4, -1 if down.
        return 4

    def close(self) -> None:
        # TODO(hardware): release GPIO/bus handles.
        ...


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

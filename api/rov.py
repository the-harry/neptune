"""RovState — the authoritative, server-side model of the vehicle.

The client sends *inputs*; this owns the *truth*. It applies control/commands to
the hardware, runs the safety watchdog (zero thrusters if control frames stop),
advances the mock sim, and produces telemetry snapshots.

Single-threaded: all methods run on the asyncio event loop, so no locking.
"""
from __future__ import annotations

import logging
import time

from config import settings
from hardware import HardwareBase
from protocol import (
    BallastMsg,
    CameraMsg,
    CommandMsg,
    COMMAND_NAMES,
    ControlMsg,
    Telemetry,
)

log = logging.getLogger("neptune.rov")

_CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def cardinal(deg: float) -> str:
    return _CARDINALS[round((deg % 360) / 45) % 8]


class RovState:
    def __init__(self, hw: HardwareBase) -> None:
        self.hw = hw
        self.armed = False
        self.throttle = 0.0
        self.steer = 0.0
        self.left = 0.0
        self.right = 0.0
        self.pan = 0.0
        self.tilt = 0.0
        self.ballast_dir = "hold"
        self.ballast_target = hw.get_ballast_level()
        self.last_control_ts = 0.0
        self._failsafe = False
        self._prev_leak = False
        hw.set_armed(False)

    # ---- inbound application --------------------------------------------
    def apply_control(self, m: ControlMsg) -> None:
        self.throttle = _clamp(m.throttle)
        self.steer = _clamp(m.steer)
        self.last_control_ts = time.monotonic()
        if self._failsafe:
            self._failsafe = False
            log.info("control resumed — watchdog cleared")
        self._drive_thrusters()

    def apply_camera(self, m: CameraMsg) -> None:
        self.pan = _clamp(m.pan)
        self.tilt = _clamp(m.tilt)
        self.hw.set_camera(self.pan, self.tilt)

    def apply_ballast(self, m: BallastMsg) -> None:
        self.ballast_dir = m.cmd
        if m.cmd == "fill":
            self.ballast_target = 1.0
        elif m.cmd == "empty":
            self.ballast_target = 0.0
        else:  # hold
            self.ballast_target = self.hw.get_ballast_level()
        self.hw.ballast_pump(m.cmd)

    def apply_command(self, m: CommandMsg) -> None:
        name = m.name
        if name not in COMMAND_NAMES:
            log.warning("unknown command %r ignored", name)
            return
        val = m.value
        if name == "arm":
            self._set_armed(True)
        elif name == "disarm":
            self._set_armed(False)
        elif name == "stop":  # E-STOP
            self._set_armed(False)
            self.ballast_dir = "hold"
            self.hw.ballast_pump("hold")
            log.warning("E-STOP")
        elif name == "surface":
            self.ballast_dir = "empty"
            self.ballast_target = 0.0
            self.hw.ballast_pump("empty")
        elif name == "ballast_home":
            self.hw.ballast_home()
            self.ballast_dir = "hold"
        elif name == "magnet":
            self.hw.set_magnet(bool(val))
        elif name in ("light_green", "light_white"):
            self.hw.set_light("green" if name.endswith("green") else "white", bool(val))
        elif name in ("light_green_level", "light_white_level"):
            which = "green" if "green" in name else "white"
            self.hw.set_light_level(which, float(val or 0.0))
        elif name == "dropweight":
            if val == "release":
                self.hw.release_dropweight()
            else:
                log.warning("dropweight command without 'release' value ignored")

    # ---- driving / watchdog ---------------------------------------------
    def _set_armed(self, on: bool) -> None:
        self.armed = on
        self.hw.set_armed(on)
        if not on:
            self.throttle = self.steer = 0.0
            self.left = self.right = 0.0
            self.hw.set_thrusters(0.0, 0.0)

    def _drive_thrusters(self) -> None:
        if self.armed and not self._failsafe:
            self.left = _clamp(self.throttle + self.steer)
            self.right = _clamp(self.throttle - self.steer)
        else:
            self.left = self.right = 0.0
        self.hw.set_thrusters(self.left, self.right)

    def watchdog(self, now: float) -> None:
        """Fail-safe: if control frames stopped, zero the thrusters."""
        if not self.armed:
            return
        stale = (now - self.last_control_ts) > settings.watchdog_timeout_s
        if stale and not self._failsafe:
            self._failsafe = True
            self.left = self.right = 0.0
            self.hw.set_thrusters(0.0, 0.0)
            log.warning("watchdog: no control for >%.2fs — thrusters zeroed",
                        settings.watchdog_timeout_s)

    def update(self, dt: float) -> None:
        self.hw.update(dt)

    # ---- telemetry -------------------------------------------------------
    def leak_alarm_edge(self, leaking: bool) -> bool:
        edge = leaking and not self._prev_leak
        self._prev_leak = leaking
        return edge

    def telemetry(self, metrics: dict, link_ms: int | None = None) -> Telemetry:
        heading = self.hw.read_heading()
        pressure = round(self.hw.read_pressure(), 1)
        depth = max(0.0, round((pressure - settings.surface_pressure_psi) / settings.psi_per_meter, 2))
        leak_state = self.hw.read_leak()
        leaking = leak_state != "NORMAL"
        g_on, g_lvl = self.hw.get_light("green")
        w_on, w_lvl = self.hw.get_light("white")
        return Telemetry(
            armed=self.armed,
            left=round(self.left, 3),
            right=round(self.right, 3),
            ballast_level=round(self.hw.get_ballast_level(), 3),
            ballast_target=round(self.ballast_target, 3),
            depth=depth,
            pressure=pressure,
            heading=round(heading, 1),
            heading_card=cardinal(heading),
            magnet=self.hw.get_magnet(),
            light_green=g_on,
            light_white=w_on,
            light_green_level=round(g_lvl, 2),
            light_white_level=round(w_lvl, 2),
            leak=leaking,
            leak_state=leak_state,
            battery_v=round(self.hw.read_voltage(), 1),
            signal=self.hw.link_quality(),
            link_ms=link_ms,
            # Pi system health passes through verbatim — including None, which means
            # "probe unavailable". Never coerce a missing reading into a plausible 0.
            cpu_c=metrics.get("cpu_c"),
            cpu_pct=metrics.get("cpu_pct"),
            ram_pct=metrics.get("ram_pct"),
            disk_gb=metrics.get("disk_gb"),
            uptime_s=metrics.get("uptime_s"),
            net_tether_up=metrics.get("net_tether_up"),
            net_tether_mbps=metrics.get("net_tether_mbps"),
            net_cam_up=metrics.get("net_cam_up"),
            net_cam_signal=metrics.get("net_cam_signal"),
            mock=self.hw.is_mock,
        )

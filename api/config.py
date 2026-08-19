"""Server configuration — all tunables in one place, overridable via env vars.

Mirrors the spirit of the client's config.js: one file to tune. Every value can
be overridden with an environment variable (see the `env=` names below), so the
same code runs on a dev laptop and on the Pi without edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ[env])
    except (KeyError, ValueError):
        return default


def _i(env: str, default: int) -> int:
    try:
        return int(os.environ[env])
    except (KeyError, ValueError):
        return default


def _s(env: str, default: str) -> str:
    return os.environ.get(env, default)


def _b(env: str, default: bool) -> bool:
    v = os.environ.get(env)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# Repo root (…/sub) and the built client the server serves as static files.
_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # --- network ---
    host: str = field(default_factory=lambda: _s("NEPTUNE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _i("NEPTUNE_PORT", 8000))
    client_dir: Path = field(default_factory=lambda: Path(_s("NEPTUNE_CLIENT_DIR", str(_ROOT / "client"))))

    # --- loop rates / timing (seconds unless noted) ---
    telemetry_hz: float = field(default_factory=lambda: _f("NEPTUNE_TELEMETRY_HZ", 15.0))
    control_hz: float = field(default_factory=lambda: _f("NEPTUNE_CONTROL_HZ", 30.0))
    metrics_period_s: float = field(default_factory=lambda: _f("NEPTUNE_METRICS_PERIOD_S", 1.0))
    # Fail-safe: if no `control` frame arrives within this window, thrusters are
    # zeroed. MUST be a few control periods, not less. Critical safety knob.
    watchdog_timeout_s: float = field(default_factory=lambda: _f("NEPTUNE_WATCHDOG_S", 0.5))

    # --- vehicle model ---
    surface_pressure_psi: float = field(default_factory=lambda: _f("NEPTUNE_SURFACE_PSI", 14.7))
    psi_per_meter: float = field(default_factory=lambda: _f("NEPTUNE_PSI_PER_M", 1.42))
    leak_warn_at: str = field(default_factory=lambda: _s("NEPTUNE_LEAK_WARN", "WARN"))

    # --- battery: 3S Li-ion (12.6 V full, 11.1 V nominal) --------------------
    # THE PACK ACTUALLY FITTED (2026-08-18): 3S3P INR18650, docs/hardware.md §7.
    # The 2S scale is obsolete the same way the 24 V scale was before it — a
    # threshold that describes a different vehicle does not fail loudly, it
    # reads "full" forever. Same per-cell judgements, three cells now:
    #
    #   >= battery_warn_v (10.5)  green   — dive on            (3.5 V/cell)
    #   <  battery_warn_v (10.5)  amber   — finish the pass    (below 3.5)
    #   <  battery_crit_v (9.9)   red     — SURFACE prompt     (3.3 V/cell)
    #      battery_floor_v (9.0)  the documented hard floor (3.0 V/cell). Below
    #                             it Li-ion cells are damaged, not merely flat.
    #                             Nothing in software enforces it — it is the
    #                             number the operator must never reach, which is
    #                             why it is written down instead of left to
    #                             folklore. Confirm the warn margin against the
    #                             recovered pack's sag at the bathtub ceremony.
    battery_full_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_FULL", 12.6))
    battery_warn_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_WARN", 10.5))
    battery_crit_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_CRIT", 9.9))
    battery_floor_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_FLOOR", 9.0))

    # --- hardware tunables (RealHardware; mirrored in docs/hardware.md) -------
    # Ballast is an open-loop NEMA 17 through an A4988: there is no position
    # sensor, so level = steps / span. The span is MEASURED once during the
    # calibration run (home, drive to the FULL switch, record the count) and
    # lives here rather than in code, because a wrong span silently rescales the
    # entire syringe UI without anything looking broken.
    ballast_span_steps: int = field(default_factory=lambda: _i("NEPTUNE_BALLAST_SPAN_STEPS", 4000))
    # Bounded step rate. Faster is tempting and loses steps under load, and a lost
    # step on an open-loop axis is not a glitch — it is the reported level quietly
    # drifting away from where the plunger actually is.
    ballast_step_rate: float = field(default_factory=lambda: _f("NEPTUNE_BALLAST_STEP_RATE", 400.0))
    # If the FULL limit switch closes more than this fraction of the span away
    # from the expected count, steps were skipped (or the span is stale): flag
    # needs-rehome instead of continuing to publish a level derived from a
    # counter we now know is wrong.
    ballast_span_tolerance: float = field(default_factory=lambda: _f("NEPTUNE_BALLAST_SPAN_TOL", 0.05))
    # Thrusters: two DRV8871 pairs on GPIO 23/24 and 5/6 — the Pi's ONLY GPIO
    # (docs/hardware.md §8). No pin shares a PWM channel with another, so the
    # old channel-sharing trap is gone; PWM rides the direction pin itself.
    # ~2 kHz is above audible whine for a brushed motor and well inside what
    # the drivers switch cleanly. pigpio's DMA timing remains the jitter
    # upgrade and needs no rewiring.
    thruster_pwm_hz: float = field(default_factory=lambda: _f("NEPTUNE_THRUSTER_PWM_HZ", 2000.0))
    # The lamp's PWM lives on the ESP32 now (LEDC, 8 kHz — above camera
    # banding; firmware/brainstem). This constant survives for the MOCK only,
    # which still models the old Pi-driven lamp pins.
    light_pwm_hz: float = field(default_factory=lambda: _f("NEPTUNE_LIGHT_PWM_HZ", 200.0))
    # Below this magnitude the H-bridge gets duty 0 instead of a trickle: a tiny
    # commanded value cannot turn a prop but does make the bridge sing, and a
    # whining idle sounds exactly like a fault to whoever is holding the tether.
    thruster_deadband: float = field(default_factory=lambda: _f("NEPTUNE_THRUSTER_DEADBAND", 0.05))
    # A leak probe must read wet for this many consecutive 10 Hz samples (~0.5 s)
    # before its state latches. Condensation, a splash on launch, and a droplet
    # running down the hull all touch a probe briefly; a real ingress does not
    # stop. Debouncing is what keeps the FLOOD alarm worth believing.
    leak_debounce_samples: int = field(default_factory=lambda: _i("NEPTUNE_LEAK_DEBOUNCE", 5))

    # --- camera (MJPEG) ---
    cam_width: int = field(default_factory=lambda: _i("NEPTUNE_CAM_W", 1280))
    cam_height: int = field(default_factory=lambda: _i("NEPTUNE_CAM_H", 720))
    cam_fps: int = field(default_factory=lambda: _i("NEPTUNE_CAM_FPS", 24))
    cam_jpeg_quality: int = field(default_factory=lambda: _i("NEPTUNE_CAM_Q", 80))

    # --- brainstem (the ESP32 on USB serial — docs/hardware.md §8) -----------
    # Empty port = autodetect: /dev/ttyESP (the udev rule the Pi installs),
    # then the USB-serial patterns a devkit enumerates as. Pin it explicitly
    # when two serial devices share the machine.
    brainstem_port: str = field(default_factory=lambda: _s("NEPTUNE_BRAINSTEM_PORT", ""))
    brainstem_baud: int = field(default_factory=lambda: _i("NEPTUNE_BRAINSTEM_BAUD", 115200))
    # The ballast bag's working swing in millilitres — what ballast_ml=full
    # means as a level of 1.0. The 500 ml flask runs part-filled (±250 g of
    # authority, docs/hardware.md §6); the bathtub ceremony confirms the figure.
    ballast_capacity_ml: float = field(default_factory=lambda: _f("NEPTUNE_BALLAST_ML", 250.0))

    # --- hardware backend ---
    # "auto" picks the real backend when either half exists (a GPIO stack for
    # the thrusters, or a brainstem answering on serial), else the bench mock.
    # Force with "mock" or "real".
    hardware_backend: str = field(default_factory=lambda: _s("NEPTUNE_HW", "auto"))

    # IS THE HARNESS BUILT? This used to be a `wired = False` literal inside
    # RealHardware._gpio_available, on the reasoning that no software can find out
    # whether the wires are in the holes, so a human has to say so. That reasoning
    # still holds — this is the human saying so, in a place that does not need the
    # file edited to change it, and it is still the ONE assertion that separates
    # "mock, and honest about it" from "real, and answering for real pins".
    #
    # It does NOT claim every sensor is fitted. RealHardware now brings up each
    # group on its own and names the ones that are not there, so the vehicle can be
    # built up a sensor at a time with the console telling the truth at every step.
    # gpiozero still has to import, so a bench machine falls back to the mock
    # whatever this says.
    hardware_wired: bool = field(default_factory=lambda: _b("NEPTUNE_HW_WIRED", True))


settings = Settings()

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

    # --- battery: 2S Li-ion (8.4 V full, 7.4 V nominal) ----------------------
    # THE OLD 24 V SCALE IS OBSOLETE. It was a placeholder from before the pack
    # existed; anything still comparing against 20-25 V is describing a different
    # vehicle and will read "full" forever on this one.
    #
    # Bands — one colour, one meaning, and the colour comes ONLY from here:
    #   >= battery_warn_v (7.0)   green   — dive on
    #   <  battery_warn_v (7.0)   amber   — finish the pass and head back
    #   <  battery_crit_v (6.6)   red     — SURFACE prompt
    #      battery_floor_v (6.0)  the documented hard floor (3.0 V/cell). Below it
    #                             Li-ion cells are damaged, not merely flat. Nothing
    #                             in software enforces it — it is the number the
    #                             operator must never reach, which is why it is
    #                             written down instead of left to folklore.
    battery_full_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_FULL", 8.4))
    battery_warn_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_WARN", 7.0))
    battery_crit_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_CRIT", 6.6))
    battery_floor_v: float = field(default_factory=lambda: _f("NEPTUNE_BATT_FLOOR", 6.0))

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
    # Thrusters own the Pi's two hardware PWM channels (GPIO12/13). ~2 kHz is
    # above audible whine for a brushed motor and well inside what the H-bridges
    # switch cleanly.
    thruster_pwm_hz: float = field(default_factory=lambda: _f("NEPTUNE_THRUSTER_PWM_HZ", 2000.0))
    # Lights run SOFTWARE PWM — GPIO12/18 share hardware channel 0 and GPIO13/19
    # share channel 1, so there are only two hardware channels and the thrusters
    # need both. ~200 Hz is flicker-free for LEDs and cheap enough in software.
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

    # --- hardware backend ---
    # "auto" picks real GPIO if the libs import, else the bench mock.
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

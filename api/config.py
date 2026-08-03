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

    # --- camera (MJPEG) ---
    cam_width: int = field(default_factory=lambda: _i("NEPTUNE_CAM_W", 1280))
    cam_height: int = field(default_factory=lambda: _i("NEPTUNE_CAM_H", 720))
    cam_fps: int = field(default_factory=lambda: _i("NEPTUNE_CAM_FPS", 24))
    cam_jpeg_quality: int = field(default_factory=lambda: _i("NEPTUNE_CAM_Q", 80))

    # --- hardware backend ---
    # "auto" picks real GPIO if the libs import, else the bench mock.
    # Force with "mock" or "real".
    hardware_backend: str = field(default_factory=lambda: _s("NEPTUNE_HW", "auto"))


settings = Settings()

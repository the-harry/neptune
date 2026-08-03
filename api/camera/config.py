"""Camera-service configuration. All values from the build spec / HAR captures.
Env-overridable so tests can point the CGI client at the mock.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _s(env: str, default: str) -> str:
    return os.environ.get(env, default)


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ[env])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class CamSettings:
    # --- camera network identity (spec §2) ---
    camera_ip: str = field(default_factory=lambda: _s("WOLFANG_IP", "192.72.1.1"))
    # Base HTTP URL for CGI + file/thumb serving. Point at the mock in dev.
    base_url: str = field(default_factory=lambda: _s("WOLFANG_BASE", "http://192.72.1.1"))
    rtsp_url: str = field(default_factory=lambda: _s("WOLFANG_RTSP", "rtsp://192.72.1.1/liveRTSP/av4"))
    wlan_iface: str = field(default_factory=lambda: _s("WOLFANG_WLAN", "wlan0"))

    # --- timeouts (spec §3.3b/c) ---
    timeout_fast_s: float = field(default_factory=lambda: _f("WOLFANG_T_FAST", 3.0))
    timeout_slow_s: float = field(default_factory=lambda: _f("WOLFANG_T_SLOW", 6.0))
    settle_after_slow_s: float = field(default_factory=lambda: _f("WOLFANG_SETTLE", 1.5))

    # --- circuit breaker (spec §7.2) ---
    breaker_cooldown_s: float = field(default_factory=lambda: _f("WOLFANG_BREAKER", 5.0))

    # --- telemetry cadence (spec §7.2 — 15s; faster stutters RTSP) ---
    telemetry_period_s: float = field(default_factory=lambda: _f("WOLFANG_TELEMETRY_S", 15.0))

    # --- record-toggle confirmation poll ---
    record_poll_timeout_s: float = field(default_factory=lambda: _f("WOLFANG_REC_POLL_T", 5.0))
    record_poll_interval_s: float = field(default_factory=lambda: _f("WOLFANG_REC_POLL_I", 0.4))

    # --- battery warning threshold (spec §7.3.11) ---
    battery_warn_pct: int = 40

    # --- download offload ---
    download_dir: str = field(default_factory=lambda: _s("WOLFANG_DL_DIR", "/tmp/neptune-offload"))
    download_chunk_bytes: int = 1 << 20  # 1 MiB; sequential, resumable

    # --- go2rtc (video plane health check) ---
    go2rtc_api: str = field(default_factory=lambda: _s("GO2RTC_API", "http://127.0.0.1:1984"))
    go2rtc_stream: str = field(default_factory=lambda: _s("GO2RTC_STREAM", "sub"))


# Property names are ASYMMETRIC (spec §4.1): you WRITE one name, READ another.
# Map both directions explicitly.
WRITE_TO_READ = {
    "Videores": "Camera.Menu.VideoRes",
    "Imageres": "Camera.Menu.ImageRes",
    "AWB": "Camera.Menu.AWB",
}
READ_TO_WRITE = {v: k for k, v in WRITE_TO_READ.items()}

# Operations that block the server for ~seconds (spec §3.3c) → slow timeout + settle.
SLOW_PROPERTIES = frozenset({"Video", "Camera.Menu.UIMode", "Playback", "SD0", "Videores", "Imageres"})

cam_settings = CamSettings()

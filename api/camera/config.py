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

    # --- defaults enforcement (see defaults.py) ---
    # The factory PowerSaving=5MIN powers the camera off mid-dive, so the defaults
    # are applied on connect and re-asserted afterwards. Set 0 to leave the camera
    # exactly as found (bench work against a camera someone else is configuring).
    apply_defaults: bool = field(default_factory=lambda: _s("WOLFANG_APPLY_DEFAULTS", "1") not in ("0", "no", "false"))
    # How often the guard re-reads the menu. Doubles as the camera KEEPALIVE: the
    # 15 s telemetry poll only runs while a dashboard is subscribed, so with nobody
    # watching there is no CGI traffic at all and an idle timer we failed to disable
    # has nothing to reset. Must stay well inside the factory 5MIN.
    defaults_recheck_s: float = field(default_factory=lambda: _f("WOLFANG_DEFAULTS_RECHECK_S", 60.0))
    # Physical mounting — only asserted when set, because the right value depends on
    # how the camera is bolted into the hull. Typical: "Normal" or "UpsideDown".
    upside_down: str = field(default_factory=lambda: _s("WOLFANG_UPSIDE_DOWN", ""))

    # --- download offload ---
    download_dir: str = field(default_factory=lambda: _s("WOLFANG_DL_DIR", "/tmp/neptune-offload"))
    download_chunk_bytes: int = 1 << 20  # 1 MiB; sequential, resumable

    # --- go2rtc (video plane health check) ---
    go2rtc_api: str = field(default_factory=lambda: _s("GO2RTC_API", "http://127.0.0.1:1984"))
    go2rtc_stream: str = field(default_factory=lambda: _s("GO2RTC_STREAM", "sub"))


# Property names are ASYMMETRIC (spec §4.1): you WRITE one name, READ another.
# Map both directions explicitly.
#
# Getting this wrong does not raise — it silently reads a property that does not
# exist, returns None, and makes every verification vacuous. That is precisely how
# `preflight()` reported "PowerSaving=OFF (critical) OK" on a camera that then
# powered itself off mid-dive. Anything written anywhere must have an entry here.
WRITE_TO_READ = {
    "Videores": "Camera.Menu.VideoRes",
    "Imageres": "Camera.Menu.ImageRes",
    "AWB": "Camera.Menu.AWB",
}
READ_TO_WRITE = {v: k for k, v in WRITE_TO_READ.items()}


def _extend_name_map() -> None:
    """Fold in every name the defaults table knows about.

    Derived rather than duplicated: a hand-maintained second copy of this mapping
    is how the two lists drift apart, and the drift is invisible until a dive fails.
    """
    from . import defaults as _defaults  # local import: defaults.py imports nothing from here

    for s in _defaults.SETTINGS:
        for w in s.writes:
            WRITE_TO_READ.setdefault(w, s.read)
        # The SHORT write name is the useful reverse mapping ("Camera.Menu.PowerSaving"
        # -> "PowerSaving"), so set it from the first candidate and do not let the
        # dotted alias overwrite it.
        READ_TO_WRITE.setdefault(s.read, s.writes[0])


_extend_name_map()


def read_name_for(write_prop: str) -> str:
    """Best guess at the READ name for a write name we have no mapping for.

    The properties most in need of this are precisely the ones missing from the map:
    anything being probed is by definition not yet understood. Every observed menu
    property reads back as `Camera.Menu.<Name>`, so fall back to that rather than
    reading a property that cannot exist and then reporting a confident `None`.
    """
    if write_prop in WRITE_TO_READ:
        return WRITE_TO_READ[write_prop]
    if "." in write_prop:                 # already dotted (Camera.Preview.H264.w)
        return write_prop
    return f"Camera.Menu.{write_prop}"

# Operations that block the server for ~seconds (spec §3.3c) → slow timeout + settle.
SLOW_PROPERTIES = frozenset({"Video", "Camera.Menu.UIMode", "Playback", "SD0", "Videores", "Imageres"})

cam_settings = CamSettings()

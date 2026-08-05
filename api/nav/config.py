"""Navigation config. Env-overridable. Isolated-segment friendly (no hostnames)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent   # …/sub


def _f(env, d):
    try: return float(os.environ[env])
    except (KeyError, ValueError): return d

def _i(env, d):
    try: return int(os.environ[env])
    except (KeyError, ValueError): return d

def _s(env, d):
    return os.environ.get(env, d)

def _b(env, d):
    v = os.environ.get(env)
    if v is None:
        return d
    return v.strip().lower() in ("1", "true", "yes", "on")


# WGS84 for the flat-earth approximation (spec §5.2). Exact enough at pond/canal scale.
EARTH_R = 6378137.0


@dataclass(frozen=True)
class NavSettings:
    # --- loop rates ---
    dr_hz: float = field(default_factory=lambda: _f("NAV_DR_HZ", 10.0))       # dead-reckoning (§5.2)
    broadcast_hz: float = field(default_factory=lambda: _f("NAV_BCAST_HZ", 10.0))  # WS push / map redraw cap (§7.5)

    # --- origin gating (§4.2) ---
    max_origin_accuracy_m: float = field(default_factory=lambda: _f("NAV_MAX_ORIGIN_ACC", 15.0))

    # --- snapping (§5.7) ---
    snap_max_dist_m: float = field(default_factory=lambda: _f("NAV_SNAP_MAX_M", 25.0))  # beyond this, don't snap
    snapping_enabled: bool = field(default_factory=lambda: _s("NAV_SNAP", "auto") != "off")

    # --- sensor backend: "sim" | "real" | "auto" ---
    # "vehicle" (default) = heading/depth/throttle from the live ROV, so steering the
    # sub actually moves the map. "sim" = the scripted demo path, which ignores the
    # operator entirely. "real" = the (unwired) IMU/depth/encoder stubs.
    sensor_backend: str = field(default_factory=lambda: _s("NAV_SENSORS", "vehicle"))

    # --- storage ---
    data_dir: Path = field(default_factory=lambda: Path(_s("NAV_DATA_DIR", str(_ROOT / "data"))))
    areas_dir: Path = field(default_factory=lambda: Path(_s("NAV_AREAS_DIR", str(_ROOT / "data" / "areas"))))
    dives_dir: Path = field(default_factory=lambda: Path(_s("NAV_DIVES_DIR", str(_ROOT / "data" / "dives"))))
    # SAFETY: log every session automatically. A navigation record is not an opt-in
    # feature - the dive you forgot to start recording is the one you needed. Set
    # NAV_AUTOLOG=0 only for bench work where the noise is unwanted.
    autolog: bool = field(default_factory=lambda: _b("NAV_AUTOLOG", True))
    speed_lut_dir: Path = field(default_factory=lambda: Path(_s("NAV_LUT_DIR", str(_ROOT / "data" / "speed_luts"))))

    # --- pmtiles extractor (legacy vector path §6.1) — a separate binary; may be absent ---
    pmtiles_bin: str = field(default_factory=lambda: _s("NAV_PMTILES_BIN", "pmtiles"))
    pmtiles_source: str = field(default_factory=lambda: _s("NAV_PMTILES_SRC", ""))  # bootstrap-only world build URL
    area_size_cap_mb: float = field(default_factory=lambda: _f("NAV_AREA_CAP_MB", 200.0))

    # --- satellite basemap downloader (§3/§4) — raster tiles → MBTiles ---
    # {z}/{y}/{x} for Esri World Imagery (y BEFORE x). Provider-configurable (§3.1).
    sat_tile_url: str = field(default_factory=lambda: _s(
        "NAV_SAT_URL",
        "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"))
    sat_attribution: str = field(default_factory=lambda: _s("NAV_SAT_ATTR", "Imagery © Esri"))
    sat_user_agent: str = field(default_factory=lambda: _s(
        "NAV_SAT_UA", "NeptuneROV/1.0 (canal survey; offline tile cache)"))
    sat_min_zoom: int = field(default_factory=lambda: _i("NAV_SAT_ZMIN", 16))
    sat_max_zoom: int = field(default_factory=lambda: _i("NAV_SAT_ZMAX", 18))     # 'High' detail adds z19 (§4)
    sat_rate_per_s: float = field(default_factory=lambda: _f("NAV_SAT_RATE", 6.0))  # polite throttle (§3.2)
    sat_tile_cap: int = field(default_factory=lambda: _i("NAV_SAT_TILE_CAP", 8000))
    sat_avg_kb: float = field(default_factory=lambda: _f("NAV_SAT_AVG_KB", 20.0))  # for size estimates (§3.3)
    overpass_url: str = field(default_factory=lambda: _s("NAV_OVERPASS", "https://overpass-api.de/api/interpreter"))
    nominatim_url: str = field(default_factory=lambda: _s("NAV_NOMINATIM", "https://nominatim.openstreetmap.org"))

    # --- live track decimation (§7.5) — cap the polyline ---
    max_live_points: int = field(default_factory=lambda: _i("NAV_MAX_POINTS", 4000))


settings = NavSettings()

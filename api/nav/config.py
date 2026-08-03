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
    sensor_backend: str = field(default_factory=lambda: _s("NAV_SENSORS", "sim"))

    # --- storage ---
    data_dir: Path = field(default_factory=lambda: Path(_s("NAV_DATA_DIR", str(_ROOT / "data"))))
    areas_dir: Path = field(default_factory=lambda: Path(_s("NAV_AREAS_DIR", str(_ROOT / "data" / "areas"))))
    dives_dir: Path = field(default_factory=lambda: Path(_s("NAV_DIVES_DIR", str(_ROOT / "data" / "dives"))))
    speed_lut_dir: Path = field(default_factory=lambda: Path(_s("NAV_LUT_DIR", str(_ROOT / "data" / "speed_luts"))))

    # --- pmtiles extractor (§6.1) — a separate binary; may be absent in isolated phase ---
    pmtiles_bin: str = field(default_factory=lambda: _s("NAV_PMTILES_BIN", "pmtiles"))
    pmtiles_source: str = field(default_factory=lambda: _s("NAV_PMTILES_SRC", ""))  # bootstrap-only world build URL
    area_size_cap_mb: float = field(default_factory=lambda: _f("NAV_AREA_CAP_MB", 200.0))

    # --- live track decimation (§7.5) — cap the polyline ---
    max_live_points: int = field(default_factory=lambda: _i("NAV_MAX_POINTS", 4000))


settings = NavSettings()

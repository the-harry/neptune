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

def _csv(env, d):
    """Comma-separated env override → tuple. Empty string means an EMPTY list, not the
    default: `NAV_CRT_EXTRA=` is how you say "fetch nothing but the Hub", and silently
    handing back the seven hardcoded services would be answering a different question."""
    v = os.environ.get(env)
    if v is None:
        return tuple(d)
    return tuple(p.strip() for p in v.split(",") if p.strip())


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

    # --- estimator backend (§4) ---
    # "dr" (DEFAULT) = the existing dead reckoner, behaviour untouched.
    # "filtered" = the same position integration, tether clamp, snapping and
    # confidence logic, fed by a gyro/mag complementary heading and a speed KF.
    # Selection is config-only and the default stays "dr" on purpose: promoting
    # the filter is a decision to be made against real dive data with
    # `python -m nav.cli replay --filter both`, not a decision to be made by taste.
    filter_backend: str = field(default_factory=lambda: _s("NAV_FILTER", "dr"))

    # --- sensor calibration constants (§2) ---
    # EVERY DEFAULT BELOW IS A PLACEHOLDER. They exist so the code runs on the
    # bench, not because anyone measured them; shipping them unchanged means the
    # map is confidently wrong, which is worse than an obvious failure. Each one
    # has a procedure in docs/hardware.md and each must be replaced by the number
    # that procedure produces.
    #
    # Paddlewheel metres-per-pulse. Depends on the printed wheel's diameter, how
    # many magnets ended up in the paddles and how the flow reaches it — a guess
    # scales EVERY measured speed, and therefore every distance, by a constant
    # error. Measure: a timed run along a known length of canal wall at fixed
    # throttle, distance / pulses.
    m_per_pulse: float = field(default_factory=lambda: _f("NAV_M_PER_PULSE", 0.05))
    # Spool metres-per-tick for a ~600 PPR quadrature encoder: 0.0005 assumes a
    # ~0.3 m circumference drum, which is arithmetic, not measurement — and the
    # effective circumference grows as cable layers build on the drum anyway.
    # Measure: pay out a marked length and divide by the ticks counted.
    m_per_spool_tick: float = field(default_factory=lambda: _f("NAV_M_PER_SPOOL_TICK", 0.0005))
    # How the BNO085 is bolted into the hull, in degrees, added after the ENU→
    # compass conversion. 0.0 asserts the board's X axis points dead ahead, which
    # it almost certainly does not once it is epoxied in. An uncorrected mounting
    # offset does not look like a bug — the track just leans consistently off true.
    # Measure: point the sub along a known bearing and record the difference.
    imu_yaw_offset_deg: float = field(default_factory=lambda: _f("NAV_IMU_YAW_OFFSET_DEG", 0.0))
    # Rolling window the paddlewheel pulse rate is averaged over. Short enough to
    # respond to a real throttle change, long enough that at canal speeds the
    # window still contains several pulses — quantisation at low pulse counts is
    # why the speed KF widens R instead of trusting a one-pulse window.
    paddle_window_s: float = field(default_factory=lambda: _f("NAV_PADDLE_WINDOW_S", 0.5))
    # No pulse for this long = STALE, reported as None rather than 0.0. The wheel
    # stalls below ~0.1 m/s, so silence means "slower than I can see" — which is
    # not the same claim as "stopped", and only the throttle can tell them apart.
    paddle_stale_s: float = field(default_factory=lambda: _f("NAV_PADDLE_STALE_S", 2.0))

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

    # --- Canal & River Trust hazard layers (nav/crt.py) — BOOTSTRAP-ONLY DOWNLOAD ---
    # Every hostname below is reached exactly once, by `python -m nav.crt`, while there is
    # still internet. Nothing in the serving path may resolve any of them: canal-side there
    # is no DNS, and a lookup that hangs is worse than a layer that says ABSENT.
    #
    # The ArcGIS Hub search endpoint. Enumerates the ~20 datasets CRT publishes as open
    # data; each item's properties.url is a FeatureServer root.
    crt_hub_search_url: str = field(default_factory=lambda: _s(
        "NAV_CRT_HUB",
        "https://data-canalrivertrust.opendata.arcgis.com/api/search/v1/collections/dataset/items?limit=100"))
    # The org's own service root. The Hub is a WINDOW onto 204 services and the best hazard
    # layers are not in that window — sluices, safety gates, stop-plank grooves and outfalls
    # are all reachable here and none of them is on the Hub.
    crt_org_service_root: str = field(default_factory=lambda: _s(
        "NAV_CRT_ORG", "https://services.arcgis.com/DknzyjEEie5tEW0u/arcgis/rest/services"))
    # Item metadata (licence text) for a service, looked up by its serviceItemId. A
    # FeatureServer root carries copyrightText — an ATTRIBUTION — and no licence at all, so
    # the terms have to be read from the item or they are being assumed.
    crt_item_lookup_url: str = field(default_factory=lambda: _s(
        "NAV_CRT_ITEMS", "https://www.arcgis.com/sharing/rest/content/items"))
    # Hardcoded because they are NOT discoverable from the Hub, verified 2026-08-07 against
    # the live org (national feature counts in nav/crt.py's _EXPECTED_FEATURES). Names are
    # deliberately the Hub-curated *_View / *_View_Public family: the org also carries older
    # near-duplicates — five separate Sluices services answering 886, 892, 893 and 937 —
    # and picking one by its name being shorter is how a survey gets 44 sluices it will
    # never be told about.
    crt_extra_services: tuple = field(default_factory=lambda: _csv("NAV_CRT_EXTRA", (
        "Canal_And_River_Trust_Sluices_View",
        "Safety_Gates_View_Public",
        "Stop_Plank_Grooves_View_Public",
        "Outfall_Discharge_Points_View_Public",
        "Towpath_Access_Points_2022",
        "Canal_And_River_Trust_Moorings_All_View",
        "Canal_And_River_Trust_Feeders_View",
    )))
    # Requests per second, sequential. Same reasoning as sat_rate_per_s: a full run is a few
    # hundred requests against somebody's free ArcGIS quota, and being blocked mid-bootstrap
    # leaves half an area on disk.
    crt_rate_per_s: float = field(default_factory=lambda: _f("NAV_CRT_RATE", 4.0))
    # data/crt/<area>/<layer>.geojson + <layer>.prov.json + provenance.json. Deliberately
    # NOT inside areas_dir: areas.list_areas() globs areas/*.json and reads every hit as an
    # area, so a provenance file landing there would invent areas that do not exist.
    crt_dir: Path = field(default_factory=lambda: Path(_s("NAV_CRT_DIR", str(_ROOT / "data" / "crt"))))
    # Skip a layer whose NATIONAL count is below this — Boat_Lifts has 1 feature and
    # Flow_Control_Structures has 3, and a toggle that can only ever be empty teaches the
    # pilot that empty means broken. Judged on the national count, never on the clipped one:
    # a bridges layer with nothing in this area is the true and useful claim "no bridges
    # here", and that file gets written.
    crt_min_features: int = field(default_factory=lambda: _i("NAV_CRT_MIN_FEATURES", 5))
    # What to do with a service whose licence text forbids reuse. Two of the seven hardcoded
    # services read "Internal use only" (Towpath_Access_Points_2022, Moorings_All_View)
    # despite being served publicly. "flag" (default) fetches them — this is one operator's
    # own safety copy, not a republication — and marks them redistributable=false in every
    # provenance record. Set "skip" to leave the tree clean enough to publish.
    crt_restricted: str = field(default_factory=lambda: _s("NAV_CRT_RESTRICTED", "flag"))
    crt_user_agent: str = field(default_factory=lambda: _s(
        "NAV_CRT_UA", "NeptuneROV/1.0 (canal survey; offline hazard cache)"))

    # --- live track decimation (§7.5) — cap the polyline ---
    max_live_points: int = field(default_factory=lambda: _i("NAV_MAX_POINTS", 4000))


settings = NavSettings()

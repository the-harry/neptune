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

    # --- auto-created offline areas (nav/areas.py create_area) -------------------
    # WHY THESE EXIST. data/areas/ was empty on every card ever built, because
    # nothing in the repo could create an area: areas.py listed them, satellite.py
    # filled in one that already had a name and a bbox, crt.py refused to fetch for
    # an area that did not exist. Setting the launch point is the first moment the
    # system knows WHERE it will be, so that is what now makes one — and these are
    # the numbers that decide how big "here" is.
    #
    # 1200 m RADIUS = a 2.4 km square around the launch point. Three reasons for
    # that number and not a rounder one: it is the size of the only hand-made area
    # this repo has ever had (data/crt/gas-street's bbox is 2.4 x 2.2 km, drawn by
    # an operator who knew the canal); a tethered ROV works a pound, not a county,
    # and 1.2 km reaches the lock at each end of most of them; and at the z16-z18
    # imagery this console downloads it costs ~970 tiles, ~19 MB and ~2.7 minutes
    # at the polite 6 req/s — done before the tether is rigged. An area that takes
    # longer to fetch than the dive takes to run would be abandoned half-finished,
    # which is the one outcome worse than no area at all.
    area_radius_m: float = field(default_factory=lambda: _f("NAV_AREA_RADIUS_M", 1200.0))
    # HARD CAP on the radius. 3000 m = a 6 km square = ~5700 tiles, ~110 MB, ~16
    # minutes: still inside sat_tile_cap and area_size_cap_mb, so this cap bites
    # FIRST and refuses with a sentence about the launch point instead of one about
    # a tile budget. An operator who taps a map must never silently start a 2 GB
    # download over a phone hotspot at the water's edge.
    area_max_radius_m: float = field(default_factory=lambda: _f("NAV_AREA_MAX_RADIUS_M", 3000.0))
    # IDEMPOTENCE. An existing area is REUSED when the new launch point sits inside
    # its bbox with at least this much coverage on every side; re-tapping the same
    # spot, or moving 50 m along the towpath, must not make a second area or
    # re-download a tile. 250 m is a launch point's own working distance — closer
    # to the edge than that and the map runs out just as the sub gets going, so
    # that case grows the existing area rather than reusing it. Another city is
    # inside nobody's bbox and gets its own area.
    area_reuse_margin_m: float = field(default_factory=lambda: _f("NAV_AREA_REUSE_M", 250.0))
    # A download that says "downloading" and has said nothing for this long is
    # reported as FAILED, not as still running. The console must be able to tell a
    # fetch in progress from a fetch whose process died — an MBTiles file appears
    # on disk with the first tile, so "the file exists" reads as a finished area
    # when it is 3% of one, and a map that looks complete and is not is the whole
    # failure this state field exists to prevent. 180 s is many times the polite
    # rate limit's worst case per tile.
    area_state_stale_s: float = field(default_factory=lambda: _f("NAV_AREA_STATE_STALE_S", 180.0))
    # Master switch for the whole automatic path — creating the area AND fetching
    # into it. Default on: nothing being automatic is the bug this round exists to
    # fix. Set it to 0 for bench work, or for an operator who would rather draw
    # every box by hand.
    #
    # TWO NAMES, ONE SWITCH, AND THAT IS DELIBERATE. service.py's fetch driver
    # already tells the operator "automatic fetching is switched off
    # (NAV_AUTOFETCH=0)" in its own reply, so that spelling is in an operator's
    # hands whether this file likes it or not. A half-honoured kill switch — one
    # that stops the download but still writes areas, or the reverse — is worse
    # than either name on its own, so both are read here and NAV_AREA_AUTO wins
    # when both are set.
    area_auto: bool = field(default_factory=lambda: _b("NAV_AREA_AUTO", _b("NAV_AUTOFETCH", True)))

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
    # ---- THE NATIONAL SET: data/crt/national/, fetched ONCE and never per-area ----
    #
    # WHY THE WHOLE COUNTRY IS ON THE HANDHELD. A marker that is not held cannot be
    # got at the waterside — there is no internet there, and the Trust's vectors are
    # how this thing is navigated in every mode: real sub, simulator, or a bench at
    # home planning next weekend. Clipping them to an area made the maps a
    # consequence of having already chosen where to go, which is backwards.
    #
    # ~150 MB nationally, dominated by two polygon layers (planning buffer 1296
    # features at ~83 kB each, canals-by-navigation 193 at ~46 kB each; every point
    # layer is 270-350 B a feature). That is a one-time download onto a handheld with
    # hundreds of gigabytes free, so NOTHING is excluded for size and nothing is
    # excluded for tier. Satellite IMAGERY stays per-area: it is tiles, it is far
    # bigger, and it is genuinely bounded by where you are going.
    #
    # The directory name is RESERVED — crt.safe_area_name refuses it, so no operator
    # area can ever be created on top of the national card.
    crt_national_name: str = field(default_factory=lambda: _s("NAV_CRT_NATIONAL", "national"))
    # WHAT "CURRENT" MEANS, and it is two things, both recorded in the provenance.
    #
    # COUNT: the layer file holds exactly as many features as the service says the
    # layer has nationally, right now. That is the test that catches the Trust adding
    # twelve culverts, and it is free — the count query is one request and discovery
    # already makes it.
    # AGE: and it was fetched within this many days. A count can agree while the
    # geometry underneath has been re-surveyed, and a card that has been right for a
    # year has not been CHECKED for a year. 90 days is a season: long enough that a
    # weekly bootstrap re-downloads nothing, short enough that a card carried through
    # a winter is refreshed before the spring.
    #
    # Both must hold, or the layer is re-fetched — from where it got to, never from
    # scratch. Neither can be tested with no internet, and with no internet nothing is
    # re-fetched anyway: what is on the card is served, dated, exactly as it is.
    crt_national_max_age_days: float = field(
        default_factory=lambda: _f("NAV_CRT_NATIONAL_MAX_AGE_DAYS", 90.0))
    # Fetch the national set in the background when the map backend starts, if it is
    # missing or incomplete. Off (0) for a bench that must touch no network at all;
    # the fetch is still one CLI command away, and the console can still ask for it.
    crt_national_auto: bool = field(default_factory=lambda: _b("NAV_CRT_NATIONAL_AUTO", True))
    # HOW BIG ONE HTTP RESPONSE IS ALLOWED TO GET. The servers advertise
    # maxRecordCount 2000, and 2000 planning-buffer polygons at ~83 kB each is a
    # 160 MB JSON document that has to be parsed whole — fine on the Ally, fatal on a
    # Pi 3B+ with a gigabyte of RAM, and a request that size times out on a hotspot
    # long before it arrives. So the page size is derived from this budget and the
    # bytes-per-feature actually measured on the first page, never from the record
    # count alone. resultOffset is a FEATURE offset, so a smaller page changes only
    # how many requests it takes and nothing about what lands.
    crt_page_bytes: float = field(default_factory=lambda: _f("NAV_CRT_PAGE_BYTES", 8e6))
    # HOW BIG A LAYER MAY BE BEFORE THE SERVING SIDE STOPS RE-PARSING IT TO CHECK IT.
    #
    # The rule in nav/service.py is that a layer is PRESENT WHEN IT HAS BEEN READ, not
    # when it has a size — a download killed part-way leaves a file with a size and no
    # closing brace, and a stat() certifies it. That rule was written when the biggest
    # file on a card was 2 MB. The national planning-buffer layer is over 100 MB, the
    # readiness check is POLLED, and re-parsing that on a Pi 3B+ would be a new fault
    # rather than a fix for an old one.
    #
    # So: under this ceiling a layer is parsed, exactly as before. Over it, the check is
    # the recorded byte count against the size on disk PLUS the closing bracket at the
    # end of the file — which is precisely what a truncated download loses, and what a
    # partial write cannot fake. Every answer says which of the two it used, because a
    # weaker check reported as a stronger one is the thing this whole file is against.
    crt_parse_max_mb: float = field(default_factory=lambda: _f("NAV_CRT_PARSE_MAX_MB", 8.0))
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

"""LIDAR launch-bank layer: THE CLASSIFICATION AND THE PAINT.

WHAT THIS HALF DOES. Reads the one float32 elevation grid nav/lidar.py left on the card
for an offline area and turns it into a picture: the corridor traced from the Trust's
centreline, a water level detected PER POUND, a BINARY amber/brown classification of
every bank pixel against the water beside it, a hillshade through both colours, faint
contours, and an XYZ tile pyramid in the same scheme and the same MBTiles container the
satellite imagery already uses. It touches no network, ever — the acquisition half did
that at bootstrap, and this half runs identically on a canal bank with no DNS.

WHY THE LAYER EXISTS. On the satellite basemap a canal is a dark blue blob between two
identical strips of green, and the only question that matters standing at the car with
12 kg of vehicle and a tether drum is which of those strips you can walk down. Imagery
cannot answer it: a two-metre brick wall and a shelving grass bank photograph the same
from above. A 1 m terrain model can.

WHAT AMBER MEANS, AND IT IS THE WHOLE HONESTY PROBLEM IN ONE SENTENCE
    Amber means "this ground stands less than 2 m above the water next to it". That is a
    GEOMETRIC FACT and it is NOT "you can launch here". The recon run that proved this
    layer amber-classified a railway cutting: low, flat, beautifully accessible, and
    behind a fence on somebody's operational railway. It will equally happily paint a
    private garden, a reed bed, a factory yard and the inside of a lock chamber. Every
    title and aria-label this module writes says so in plain words, because the paint is
    persuasive and the operator is in a hurry.

WATER IS NEVER PAINTED, AND IT IS STRUCTURAL
    LIDAR cannot see through water, so this layer knows nothing whatever about the
    channel — and a wash of colour over it would be read as knowing something. So water
    is a CLASS (BANK_CLASS_WATER) whose only palette entry is fully transparent. There is
    no code path in which a water pixel is handed a colour, at any zoom, in any tile. The
    satellite shows through it, unaltered.

PER-POUND DATUMS — THE PART THAT EARNS THE LAYER
    "2 m above the water" is meaningless against a global constant, because a canal is
    not level: it is a staircase of pounds, each held between locks, and a flight can
    drop thirty metres in a few hundred. The same absolute height is a wadeable step in
    one pound and a two-storey wall in the next, so measured against one number a lock
    flight comes out wrong at one end whatever number is chosen.

    So the water level is DETECTED from the terrain itself — near-flat pixels close to
    the centreline, histogrammed, the prominent well-separated modes being the pounds —
    and every bank pixel is measured against its NEAREST flat-water pixel. The
    amber/brown split then walks down a lock flight on its own, with no list of locks
    anywhere in this file. On the Regent's Canal at Camden it reads
    29.0 -> 27.6 -> 25.2 -> 22.6 m OD: Hampstead Road, Hawley and Kentish Town.

    A DETECTED LEVEL IS NOT A SURVEYED DATUM. The composite is mosaicked from surveys
    flown in different years, and where two meet mid-pound the water sheet steps by a
    few tens of centimetres with no lock under it. Those appear as extra levels and are
    reported as what they are — the height of a flat sheet of water in 2022 — never as
    "the pound datum".

THE CORRIDOR IS BUFFERED FROM THE VECTOR, NEVER GROWN OUT OF THE FLAT WATER
    The obvious way to decide which pixels are beside the canal is to find the flat water
    and grow outwards. It fails at exactly the places that matter: the survey records the
    top of a BRIDGE, not the water under it, so a corridor detected from the sheet has a
    ten-metre hole in it at every bridge, lock chamber and building over the cut — and the
    console then draws no bank at the one spot an operator is most likely to be standing
    on. Buffering the vector makes the corridor continuous BY CONSTRUCTION, and it does
    not know the bridge is there.

    The price is stated rather than hidden: an arm, a basin or a marina absent from the
    Trust's centreline is absent from the corridor, and unpainted ground has NOT been
    surveyed and found high — it has not been looked at.

NODATA IS NOT AN ELEVATION, AND IT REACHES FURTHER THAN ITS OWN HOLE
    The acquisition half masks the service's sentinel to NaN at the door, so nothing here
    ever sees -9999 or -3.4e38. That is not enough on its own. MASKING THE OUTPUT IS NOT
    MASKING THE INPUT: a derivative taken across a hole whose cells were filled with any
    constant computes, at every SURVEYED cell touching that hole, a slope of tens of
    metres in one pixel — and those cells are valid, so no `where(valid, ...)` afterwards
    can rescue them. The hillshade saturates and the console draws a hard rim around
    every gap in the Environment Agency's coverage that an operator reads as a retaining
    wall. So the holes are filled from their NEAREST SURVEYED NEIGHBOUR before any
    gradient is taken; see the fill in `classify`.

DOWNSAMPLING IS DONE ON THE CLASSIFICATION, NEVER ON THE PIXELS
    Zooming out re-renders from the class raster: each output pixel COUNTS the grid
    pixels under it, takes the majority of the painted ones, and sets its alpha to how
    much of itself is painted at all. Averaging the finished RGBA is the obvious thing
    and it manufactures amber, because half-covered brown beside transparent water is a
    lighter brown and a lighter brown reads as amber — a launch bank invented by a filter
    kernel, which is the worst single failure this layer has. Counting cannot do it: a
    pixel with no amber under it has an amber count of zero, at every zoom.

ABSENT AND PARTIAL ARE REPORTED, NEVER FALLEN BACK FROM
    A missing or partial area says so, with a sentence. It never drops quietly to bare
    satellite, because bare satellite over a canal looks exactly like "there are no low
    banks here" — and an operator who reads a hole in the survey as a surveyed result
    walks a bank nobody ever looked at.

WHERE THE NUMBERS LIVE. The classification constants — the 2 m rule, the two colours,
the hillshade angles, the contour interval, the 12 m and 22 m corridor buffers, the
pound-detection bins — are in nav/config.py beside the acquisition half's, NOT duplicated
here, because two halves of one layer with two copies of a constant is a layer that
drifts apart on the first tuning pass. What this file defines is what only the renderer
has an opinion about: the tile scheme, the zoom range, and how solid the paint sits.

DEPENDENCIES. numpy, scipy and Pillow, imported INSIDE the functions that need them and
never at module scope. A handheld that has not installed them must still start the API
and still serve every other layer — the same rule that makes a missing depth sensor
report itself rather than kill the console. `library_state()` is the one call that
decides whether this half can run, and it answers with a sentence an operator can act on;
`BankUnavailable` is the one named exception an endpoint can turn into that sentence.
These live on the HANDHELD: the Pi 3B+ never runs a line of this file.

RUN IT
    python -m nav.bank <area>            build the pyramid from the stored grid
    python -m nav.bank --status <area>   what is on the card — no network, no numpy
    python -m nav.bank --levels <area>   detect and print the water levels only
    python -m nav.bank --list            every area with a bank layer

CPU-BOUND AND SYNCHRONOUS ON PURPOSE. There is no I/O to await, only arithmetic over a
few million pixels. An async caller runs `render_area` through asyncio.to_thread so the
event loop keeps serving telemetry while the map is built.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import lidar
from .config import settings

log = logging.getLogger("neptune.nav.bank")


def _f(env: str, d: float) -> float:
    try:
        return float(os.environ[env])
    except (KeyError, ValueError):
        return d


def _i(env: str, d: int) -> int:
    try:
        return int(os.environ[env])
    except (KeyError, ValueError):
        return d


# ================================================================================
# WHAT ONLY THE RENDERER HAS AN OPINION ABOUT
# Everything else — colours, the 2 m rule, buffers, hillshade, contours, pound bins —
# comes from settings.lidar_* so the two halves of this layer cannot drift apart.
# ================================================================================
BANK_LAYER_KEY = "bank"

#: 256 px tiles in the slippy-map scheme, identical to nav/satellite.py, so the overlay
#: lands on the imagery pixel for pixel and the client draws it with the code it has.
BANK_TILE_SIZE = 256
#: zmax follows the imagery — an overlay finer than the basemap under it is detail with
#: nothing to sit on. zmin goes well BELOW the imagery's floor (16) on purpose: pulling
#: back to see where you are is exactly when the corridor should still carry its paint,
#: and z13 is a whole 2.4 km area in about two tiles.
BANK_ZMAX = _i("NAV_BANK_ZMAX", settings.sat_max_zoom)
BANK_ZMIN = _i("NAV_BANK_ZMIN", 13)

#: How solid the paint sits over the imagery. Not 1.0: the satellite's own texture — a
#: parked car, a fence line, a moored boat, a gate — is worth keeping under the
#: classification and the operator reads both at once. Not much lower either, or the
#: binary split stops being legible against bright grass in sunlight.
BANK_PAINT_ALPHA = _f("NAV_BANK_PAINT_ALPHA", 0.80)

#: How far a pixel may sit from the water beside it and still BE water. Well under a
#: towpath's height (0.5-1.5 m) so the towpath stays paintable, and well over the noise
#: in a LIDAR return over water. Used to widen the water class beyond the centreline
#: buffer, so a basin, a winding hole or a widened lock approach keeps the imagery
#: showing through instead of being painted as "bank at 0 m above the water".
BANK_WATER_TOLERANCE_M = _f("NAV_BANK_WATER_TOL_M", 0.25)
BANK_WATER_REACH_M = _f("NAV_BANK_WATER_REACH_M", 40.0)

#: A detected mode has to be a real sheet of water, not a flat roof that happened to
#: fall inside the sampling band. 500 px at 1 m is 500 m2, and the band is 16 m wide, so
#: that is about 30 m of canal — shorter than any pound on the network.
BANK_POUND_MIN_PIXELS = _i("NAV_BANK_POUND_MIN_PX", 500)

#: The hillshade is a MULTIPLIER on the class colour, normalised so flat ground comes out
#: at exactly 1.0 and is therefore exactly the specified colour. Raw hillshade at 45
#: degrees is cos(45) = 0.707 on the flat, and shipping that as a multiplier would darken
#: the whole validated palette by 30%.
#:
#: THE CLAMPS ARE NOT TASTE. Measured against this exact palette, amber multiplied by
#: less than 0.655 is nearer brown in RGB than it is to amber, and brown multiplied by
#: more than 2.11 is nearer amber — i.e. beyond those the relief moves a pixel into the
#: other class and a wall reads as wadeable. These sit inside that band with room for the
#: white contour blend on top.
BANK_HILLSHADE_MIN = _f("NAV_BANK_HS_MIN", 0.72)
BANK_HILLSHADE_MAX = _f("NAV_BANK_HS_MAX", 1.60)

# ---- classification codes -------------------------------------------------------
# WATER HAS ITS OWN CODE AND ITS ONLY PALETTE ENTRY IS TRANSPARENT. That is what makes
# "water is never painted" structural instead of a rule somebody has to remember.
BANK_CLASS_OUTSIDE = 0   # outside the corridor, or no survey here — draw nothing
BANK_CLASS_WATER = 1     # water — draw nothing, ever, at any zoom
BANK_CLASS_LOW = 2       # amber: under the launch height above the water beside it
BANK_CLASS_HIGH = 3      # brown: higher bank, wall, urban fabric

#: Files, all inside the directory nav/lidar.py already made for this area. The extension
#: is on the DIRECTORY (data/areas/<name>.lidar/), which matches no glob in areas.py — a
#: file called "<name>.bank.json" beside the imagery would be picked up by
#: areas.list_areas() as a phantom area called "<name>.bank" that nothing can fly and
#: nothing can delete.
_TILES_NAME = "bank.mbtiles"
_PROV_NAME = "bank.json"        # OURS. lidar.py owns provenance.json in the same folder.
_POUNDS_NAME = "pounds.geojson"


# ================================================================================
# THE DEPENDENCY GATE
# ================================================================================
class BankUnavailable(RuntimeError):
    """One named exception for "this machine cannot do it, and here is why".

    An endpoint has to turn a missing library into a sentence for the panel. A bare
    ImportError out of the middle of a pipeline gets caught as `except Exception` and
    reported as "server error", which tells an operator nothing about a pip command.
    """


def library_state() -> dict:
    """Can the RENDER half run here, and if not, exactly what is missing. NEVER raises.

    Built ON the acquisition half's answer rather than beside it: that half needs numpy
    and Pillow, this one needs those and scipy as well, and an operator told to install
    numpy when scipy is what is missing runs the command, sees no change, and concludes
    the layer is broken.

    THE SENTENCE IS THE POINT, and `why` is written to be shown verbatim.
    """
    base = lidar.library_state()
    missing = list(base.get("missing") or [])
    detail = dict(base.get("libraries") or {})
    needed = ("scipy.ndimage — the corridor buffer, the gradients, and the distance "
              "transform that measures a bank against its nearest water")
    try:
        import scipy  # noqa: F401
        detail["scipy"] = {"present": True, "error": None, "needed_for": needed}
    except Exception as exc:  # noqa: BLE001 — a half-installed wheel is an absence too
        detail["scipy"] = {"present": False, "needed_for": needed,
                           "error": f"{type(exc).__name__}: {exc}"}
        missing.append("scipy")
    install = "pip install numpy scipy Pillow"
    if not missing:
        return {"ok": True, "missing": [], "install": None, "libraries": detail,
                "why": ("numpy, scipy and Pillow are installed, so this machine can "
                        "build and read the LIDAR launch-bank overlay."),
                "title": ("LAUNCH-BANK RENDERER AVAILABLE — numpy, scipy and Pillow are "
                          "all installed on this handheld."),
                "aria_label": "The launch bank renderer is available on this machine."}
    # PIL installs as `Pillow`, and an install line reading "pip install PIL" sends the
    # reader to a package that has not existed since 2011.
    pretty = ["Pillow" if m == "PIL" else m for m in missing]
    # "numpy and Pillow and scipy" is what a naive join gives for three; an operator
    # reading this at midnight deserves a sentence rather than a chain.
    names = (pretty[0] if len(pretty) == 1
             else " and ".join([", ".join(pretty[:-1]), pretty[-1]]))
    why = (f"The LIDAR launch-bank overlay cannot be built on this machine because "
           f"{names} {'is' if len(pretty) == 1 else 'are'} not installed. Install with: "
           f"{install}  (handheld only — the Pi 3B+ must not carry these). Tiles already "
           f"on the card are still served, and every other map layer is unaffected.")
    return {"ok": False, "missing": missing, "install": install, "libraries": detail,
            "why": why, "title": f"LAUNCH-BANK RENDERER UNAVAILABLE — {why}",
            "aria_label": f"The launch bank renderer is unavailable. {why}"}


def _deps():
    """Import numpy/scipy/Pillow HERE, never at module scope.

    A missing optional library must not take the API down, for exactly the reason a
    missing depth sensor does not: the console's job is to say what it has, and a server
    that will not import says nothing at all.
    """
    state = library_state()
    if not state["ok"]:
        raise BankUnavailable(state["why"])
    import numpy as np
    from scipy import ndimage, signal
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None   # a 2 km square of 1 m LIDAR trips the decompression
    # -bomb guard, which guards against hostile uploads and not against our own survey.
    return np, ndimage, signal, Image


# ================================================================================
# WHERE THINGS LIVE — beside the grid the acquisition half stored
# ================================================================================
def tiles_path(name: str) -> Path:
    return lidar.area_lidar_dir(name) / _TILES_NAME


def render_provenance_path(name: str) -> Path:
    return lidar.area_lidar_dir(name) / _PROV_NAME


def pounds_path(name: str) -> Path:
    return lidar.area_lidar_dir(name) / _POUNDS_NAME


def _write_atomic(path: Path, text: str) -> None:
    """Whole file or no file. A truncated record is unparseable, and an unparseable
    record makes an area that is entirely present on the card report itself ABSENT."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # BaseException deliberately: Ctrl-C mid-write is the case this exists for, and
        # it must not leave the half-written file where the whole one was.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ================================================================================
# THE GRID
# ================================================================================
def _metres_per_degree(lat_deg: float) -> tuple[float, float]:
    """(metres per degree of longitude, metres per degree of latitude).

    The series form rather than a spherical R*cos(lat), because these same numbers set
    the 12 m and 22 m corridor buffers and the 8 m sampling band, and those are metres an
    operator paces out on a towpath.
    """
    phi = math.radians(lat_deg)
    m_lat = (111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
             - 0.0023 * math.cos(6 * phi))
    m_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
             + 0.118 * math.cos(5 * phi))
    return m_lon, m_lat


@dataclass
class Grid:
    """One area's stored elevation grid plus the lattice it sits on.

    `z` is metres above Ordnance Datum with NaN wherever there is no measurement — the
    acquisition half masked the service's sentinel out once, at the door, so nothing here
    has to know what it was. NaN rather than a number because every arithmetic path below
    then propagates the absence instead of quietly averaging a fill value 340 undecillion
    metres below the canal into a hillshade.
    """
    z: object                # numpy float32 (H, W)
    valid: object            # numpy bool (H, W)
    west: float              # OUTER edge of column 0
    north: float             # OUTER edge of row 0
    px_lon: float            # degrees per column
    px_lat: float            # degrees per row (rows run north to south)
    px_x_m: float            # one column, in metres, at this area's centre latitude
    px_y_m: float            # one row, in metres
    provenance: dict = field(default_factory=dict)

    @property
    def shape(self):
        return self.z.shape

    @property
    def bbox(self) -> list[float]:
        h, w = self.z.shape
        return [self.west, self.north - self.px_lat * h,
                self.west + self.px_lon * w, self.north]

    def lonlat(self, row: float, col: float) -> tuple[float, float]:
        """Centre of pixel (row, col). The half pixel matters: half of 1 m is 0.5 m,
        which is half the width of the vehicle — small, and exactly the kind of small
        that never gets noticed and never stops being wrong."""
        return (self.west + (col + 0.5) * self.px_lon,
                self.north - (row + 0.5) * self.px_lat)


def load_grid(name: str) -> Grid:
    """Read one area's stored grid through nav/lidar.py's door, or say why not."""
    np, _, _, _ = _deps()
    got = lidar.read_dtm(name, mmap=True)
    if not got.get("ok"):
        raise BankUnavailable(got.get("why") or
                              f"no LIDAR grid is stored for the area '{name}'")
    geo = got.get("geo") or {}
    for k in ("west", "north", "px_lon", "px_lat"):
        if geo.get(k) is None:
            raise BankUnavailable(
                f"the stored grid for '{name}' has no {k} in its record, so it cannot be "
                f"placed on the earth and nothing can be painted from it. Re-download "
                f"the area.")
    # COPIED OFF THE MEMMAP DELIBERATELY. Every pass below — the gradient, the distance
    # transforms, the classification — touches the whole array several times, and doing
    # that through a memory map on an eMMC card turns half a second of arithmetic into
    # minutes of page faults.
    z = np.array(got["grid"], dtype=np.float32)
    px_lat = float(geo["px_lat"])
    m_lon, m_lat = _metres_per_degree(float(geo["north"]) - px_lat * z.shape[0] / 2.0)
    return Grid(z=z, valid=np.isfinite(z), west=float(geo["west"]),
                north=float(geo["north"]), px_lon=float(geo["px_lon"]), px_lat=px_lat,
                px_x_m=float(geo["px_lon"]) * m_lon, px_y_m=px_lat * m_lat,
                provenance=got.get("provenance") or {})


# ================================================================================
# THE PICTURE
# ================================================================================
@dataclass
class BankRaster:
    """Everything the tiler needs, all on the grid, all the same shape."""
    grid: Grid
    classes: object          # uint8 (H, W) — one of the BANK_CLASS_* codes
    shade: object            # float32 (H, W) — multiplier, 1.0 on flat ground
    contour: object          # float32 (H, W) — 0..1 coverage of a contour line
    levels: list = field(default_factory=list)   # detected sheet levels, high first
    pounds: list = field(default_factory=list)   # label points, one per sheet component
    stats: dict = field(default_factory=dict)


def _rasterise_centreline(np, grid: Grid, lines) -> object:
    """Burn the centreline vector onto the grid as a connected 1 px line.

    Stepped at half a pixel so the line has NO GAPS. A sampled polyline with gaps gives
    the distance transform holes to grow through, and a corridor with holes in it is
    exactly the discontinuous mess that buffering the vector — rather than detecting flat
    sheets — was chosen to avoid.
    """
    h, w = grid.shape
    mask = np.zeros((h, w), dtype=bool)
    for coords in lines or ():
        c = np.asarray(coords, dtype=np.float64)
        if c.ndim != 2 or c.shape[0] < 2:
            continue
        cx = (c[:, 0] - grid.west) / grid.px_lon - 0.5
        cy = (grid.north - c[:, 1]) / grid.px_lat - 0.5
        for i in range(len(cx) - 1):
            steps = int(max(abs(cx[i + 1] - cx[i]), abs(cy[i + 1] - cy[i])) * 2) + 2
            xs = np.rint(np.linspace(cx[i], cx[i + 1], steps)).astype(np.int64)
            ys = np.rint(np.linspace(cy[i], cy[i + 1], steps)).astype(np.int64)
            ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            if ok.any():
                mask[ys[ok], xs[ok]] = True
    return mask


def detect_pound_levels(np, signal, values) -> list[dict]:
    """The pound levels: prominent, well-separated modes of the flat-water elevations.

    Bin width, separation and what counts as flat all come from settings.lidar_pound_*,
    beside the acquisition half's constants, so both halves describe the same map.

    THE HISTOGRAM IS PADDED WITH AN EMPTY BIN AT EACH END, and that is not tidiness.
    find_peaks cannot return index 0 — a boundary bin has no left neighbour to stand out
    from — so the LOWEST pound in an area, which is very often the flattest and the one
    the most bank is measured against, lands in bin 0 and is never reported at all. One
    zero bin either side gives it a neighbour and costs nothing.

    The reported level is the MEDIAN of the values in the winning bin and its immediate
    neighbours, not the bin's centre: a bin centre quantises every level to the nearest
    10 cm and the phase of that quantisation is an accident of where the histogram
    happened to start. Against the Camden flight the median form reproduces the recon's
    29.0 / 27.6 / 25.2 / 22.6 to a centimetre; bin centres are half a bin out.
    """
    bin_m = float(settings.lidar_pound_bin_m)
    sep_m = float(settings.lidar_pound_separation_m)
    values = values[np.isfinite(values)]
    if values.size < BANK_POUND_MIN_PIXELS:
        return []
    lo = math.floor(float(values.min()) / bin_m) * bin_m
    hi = math.ceil(float(values.max()) / bin_m) * bin_m
    edges = np.arange(lo, hi + bin_m, bin_m)
    if edges.size < 3:
        return []
    counts, edges = np.histogram(values, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2.0
    padded = np.concatenate(([0], counts, [0]))
    peaks, _ = signal.find_peaks(
        padded, distance=max(1.0, sep_m / bin_m),
        # PROMINENT, not merely tall: a mode has to stand out of its own neighbourhood.
        # 5% of the biggest bin still catches a short pound beside a long one; the floor
        # stops a nearly-empty area finding structure in a dozen stray pixels.
        prominence=max(counts.max() * 0.05, BANK_POUND_MIN_PIXELS / 10.0))

    found = []
    for p in peaks:
        i = int(p) - 1                      # undo the pad
        if not 0 <= i < centres.size:
            continue
        near = np.abs(values - centres[i]) <= 1.5 * bin_m
        n = int(near.sum())
        if n < BANK_POUND_MIN_PIXELS:
            continue        # a flat roof inside the sampling band, not a sheet of water
        found.append({"level_m_od": round(float(np.median(values[near])), 2),
                      "bin_pixels": int(counts[i]), "sheet_pixels": n})

    # "MORE THAN 0.6 m APART" is enforced on the REFINED levels, not on the bin indices:
    # two bins four apart can refine to 0.55 m apart, and reporting those as two pounds
    # would put two labels and a phantom lock on one flat stretch of water. The
    # better-supported one wins, because the bigger sheet is the one more of the map is
    # measured against.
    found.sort(key=lambda r: -r["sheet_pixels"])
    keep: list[dict] = []
    for rec in found:
        if all(abs(rec["level_m_od"] - k["level_m_od"]) > sep_m for k in keep):
            keep.append(rec)
    keep.sort(key=lambda r: -r["level_m_od"])
    return keep


def classify(grid: Grid, lines=None) -> BankRaster:
    """Corridor, per-pound datums, binary class, hillshade, contours — the whole picture.

    `lines` is the centreline as lists of [lon, lat]; when it is None the national card is
    read for this grid's own bbox through the acquisition half's reader.
    """
    np, ndimage, signal, _ = _deps()
    t0 = time.time()
    if lines is None:
        got = lidar.centreline_in(grid.bbox)
        lines = got.get("lines") or []
        centre_why = got.get("why") or ""
    else:
        centre_why = f"{len(lines)} centreline part(s) supplied by the caller"

    h, w = grid.shape
    z, valid = grid.z, grid.valid
    sampling = (grid.px_y_m, grid.px_x_m)     # EDT distances come out in METRES

    line = _rasterise_centreline(np, grid, lines)
    line_px = int(line.sum())
    if line_px == 0:
        # A true and useful claim, and NOT the same claim as "no low banks here".
        return BankRaster(
            grid=grid, classes=np.zeros((h, w), np.uint8),
            shade=np.ones((h, w), np.float32), contour=np.zeros((h, w), np.float32),
            stats={"centreline_pixels": 0, "corridor_pixels": 0,
                   "centreline_why": centre_why,
                   "why_empty": (
                       "no Canal & River Trust canal centreline crosses this area, so "
                       "there is no corridor to trace and nothing has been painted. "
                       "Arms, basins and private cuts the Trust holds no centreline for "
                       "are absent from this layer too — unpainted ground has not been "
                       "surveyed and found high, it has not been looked at.")})

    dist = ndimage.distance_transform_edt(~line, sampling=sampling)
    water_buf = float(settings.lidar_water_buffer_m)
    corridor = dist <= water_buf + float(settings.lidar_band_buffer_m)

    # ---- FILL THE HOLES BEFORE TAKING ANY DERIVATIVE ----------------------------
    # See the module docstring: masking the OUTPUT is not masking the INPUT. Filling each
    # hole from its NEAREST SURVEYED NEIGHBOUR makes the hole flat and continuous with
    # the ground it is cut into, so the shading at its rim is the shading of that ground
    # rather than a cliff. The hole is still masked out of every answer afterwards — this
    # fill exists only so the arithmetic at its EDGE is right.
    if valid.all():
        zfill = z
    elif valid.any():
        _, (fy, fx) = ndimage.distance_transform_edt(
            ~valid, sampling=sampling, return_indices=True)
        zfill = z[fy, fx]
        del fy, fx
    else:
        zfill = np.zeros((h, w), dtype=np.float32)

    gy, gx = np.gradient(zfill.astype(np.float32), grid.px_y_m, grid.px_x_m)
    grad = np.hypot(gx, gy)
    flat = valid & (grad < float(settings.lidar_flat_gradient))

    # ---- the flat-water set, and the pound levels in it --------------------------
    # On a 1 m composite the water surface is the flattest thing in the scene by a wide
    # margin — inside one sheet the 10th and 90th percentiles come out identical to the
    # centimetre — so "flat, and close to the centreline" is a good water detector and a
    # cheap one. A bridge deck is the case it has to get right, and does: a deck is
    # cambered, so it fails the flatness test and never becomes its own datum.
    water_datum = flat & (dist <= float(settings.lidar_water_sample_m))
    levels = detect_pound_levels(np, signal, z[water_datum])

    # ---- the datum field: nearest flat-water pixel, per bank pixel ---------------
    # THIS is what makes the amber/brown split walk down a lock flight with no list of
    # locks anywhere in this file: every pixel is measured against the water beside IT.
    #
    # The nearest water pixel's OWN elevation is used, not the pound level it was
    # assigned to. That matters where the composite is mosaicked from two surveys flown
    # in different years: their water sheets can meet mid-pound with a 0.4 m step, which
    # is INSIDE the 0.6 m rule that decides two modes are one pound — so the two report as
    # a single level, and a bank measured against that single level is out by the size of
    # the seam along whichever sheet lost. Measuring against the actual pixel beside it
    # cannot go wrong that way, and costs nothing. The detected levels are still what gets
    # LABELLED, because a label describes a pound and a datum measures against water.
    #
    # THE TRANSIENT COST, the biggest allocation in this file: the index form of the
    # transform returns two whole-grid index arrays, 16 bytes a pixel together. A standard
    # 2.4 km area is 5.8 M pixels and costs about 92 MB for the length of one statement.
    # They are dropped the moment the datum is read out of them.
    if water_datum.any():
        _, (iy, ix) = ndimage.distance_transform_edt(
            ~water_datum, sampling=sampling, return_indices=True)
        datum = z[iy, ix]
        del iy, ix
    else:
        datum = np.full((h, w), np.float32("nan"), dtype=np.float32)
    height = z - datum

    # ---- what is water ----------------------------------------------------------
    # Everything inside the water buffer — a lock chamber, a bridge hole and a stretch the
    # LIDAR simply missed are all still the middle of the cut, and paint in the middle of
    # the cut is the one thing this layer may never produce. PLUS any flat ground further
    # out sitting at the level of the water beside it, which is how a basin, a winding
    # hole or a widened lock approach keeps the imagery showing through.
    #
    # `valid &` IS LOad-BEARING: a hole in the middle of the channel is unsurveyed, not
    # water. Called water it would be reported as a fact about the cut; left unclassified
    # it is reported as the absence it is.
    wide = (flat & (dist <= BANK_WATER_REACH_M)
            & np.isfinite(height) & (np.abs(height) <= BANK_WATER_TOLERANCE_M))
    water = valid & ((dist <= water_buf) | wide)

    paintable = corridor & valid & ~water & np.isfinite(datum)
    low = paintable & (height < float(settings.lidar_launch_max_height_m))
    high = paintable & ~low

    classes = np.zeros((h, w), dtype=np.uint8)
    classes[water & corridor] = BANK_CLASS_WATER
    classes[low] = BANK_CLASS_LOW
    classes[high] = BANK_CLASS_HIGH

    # ---- hillshade --------------------------------------------------------------
    az = math.radians(360.0 - float(settings.lidar_hillshade_azimuth_deg) + 90.0)
    zenith = math.radians(90.0 - float(settings.lidar_hillshade_altitude_deg))
    zf = float(settings.lidar_hillshade_z_factor)
    zx, zy = gx * zf, gy * zf
    slope = np.arctan(np.hypot(zx, zy))
    # gy is d/d(row) and rows run NORTH TO SOUTH, so the northward derivative is -gy. Get
    # this sign wrong and the scene is lit from the south-east: every embankment reads as
    # a ditch and every ditch as an embankment — the inverted-relief illusion, which here
    # would make a wall look like the way down to the water.
    aspect = np.arctan2(-zy, -zx)
    shade = (math.cos(zenith) * np.cos(slope)
             + math.sin(zenith) * np.sin(slope) * np.cos(az - aspect))
    # Normalised so FLAT GROUND IS EXACTLY 1.0 — see BANK_HILLSHADE_MIN.
    shade = np.clip(shade / math.cos(zenith), BANK_HILLSHADE_MIN, BANK_HILLSHADE_MAX)
    shade = np.where(valid, shade, 1.0).astype(np.float32)

    # ---- contours ---------------------------------------------------------------
    # Distance to the nearest contour, converted from metres of elevation into PIXELS
    # across the ground by the local gradient, so the line keeps a constant width on
    # screen instead of being a fat blob on flat ground and invisible on a wall.
    interval = float(settings.lidar_contour_interval_m)
    width_px = float(settings.lidar_contour_width_px)
    g_per_px = np.hypot(gx * grid.px_x_m, gy * grid.px_y_m)
    with np.errstate(invalid="ignore", divide="ignore"):
        to_line = np.abs(z / interval - np.round(z / interval)) * interval
        cov = np.clip(width_px / 2.0 + 0.5 - to_line / np.maximum(g_per_px, 1e-6),
                      0.0, 1.0)
    # Where the ground climbs a whole interval or more per pixel the contours are closer
    # together than the pixels are and cannot be drawn as lines at all; the honest render
    # is the fraction of ground they cover, which is the line width itself.
    cov = np.where(g_per_px > interval, width_px, cov)
    contour = np.where(paintable, cov, 0.0).astype(np.float32)   # never on the water

    pounds = _pound_labels(np, ndimage, grid, levels, water_datum)

    corridor_px = int(corridor.sum())
    amber_px, brown_px = int(low.sum()), int(high.sum())
    painted = amber_px + brown_px
    covered = int((corridor & valid).sum())
    stats = {
        "centreline_pixels": line_px,
        "centreline_why": centre_why,
        "corridor_pixels": corridor_px,
        "corridor_covered_pixels": covered,
        # PARTIAL is decided on this: how much of the corridor the survey reaches. A hole
        # in the LIDAR is not a hole in the bank.
        "corridor_coverage": round(covered / corridor_px, 4) if corridor_px else 0.0,
        "water_pixels": int((water & corridor).sum()),
        "painted_pixels": painted,
        "amber_pixels": amber_px,
        "brown_pixels": brown_px,
        "amber_fraction_of_painted": round(amber_px / painted, 4) if painted else 0.0,
        "amber_fraction_of_corridor": round(amber_px / corridor_px, 4) if corridor_px else 0.0,
        "levels_detected": len(levels),
        "px_total": int(z.size),
        "valid_px": int(valid.sum()),
        "classify_seconds": round(time.time() - t0, 2),
    }
    return BankRaster(grid=grid, classes=classes, shade=shade, contour=contour,
                      levels=levels, pounds=pounds, stats=stats)


def _pound_labels(np, ndimage, grid: Grid, levels, water_datum) -> list[dict]:
    """One label per connected sheet of water, placed ON that sheet.

    Per COMPONENT and not per level: the same pound appears twice in one box whenever a
    bridge or a building splits it, and a single label at the centroid of both halves
    lands on the building. Each label is then snapped to a pixel genuinely inside its own
    component, because a level written over the bank is a number about the bank as far as
    anyone reading the map is concerned.
    """
    out: list[dict] = []
    if not levels or not water_datum.any():
        return out
    z = grid.z
    vintage = settings.lidar_survey_vintage
    min_px = max(BANK_POUND_MIN_PIXELS // 2, 1)
    for rec in levels:
        lv = rec["level_m_od"]
        sheet = water_datum & (np.abs(z - np.float32(lv)) <= BANK_WATER_TOLERANCE_M)
        lab, n = ndimage.label(sheet)
        if n == 0:
            continue
        sizes = ndimage.sum_labels(sheet, lab, index=np.arange(1, n + 1))
        boxes = ndimage.find_objects(lab)
        for i, size in enumerate(sizes):
            if size < min_px:
                continue          # a puddle of noise, not a pound worth naming
            sl = boxes[i]
            ys, xs = np.nonzero(lab[sl] == (i + 1))
            cy, cx = ys.mean(), xs.mean()
            # Snap to the component pixel nearest that centroid: on a bend the centroid of
            # a curved sheet of water sits on the towpath outside it.
            k = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
            lon, lat = grid.lonlat(int(ys[k]) + sl[0].start, int(xs[k]) + sl[1].start)
            area = float(size) * grid.px_x_m * grid.px_y_m
            out.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
                "properties": {
                    "level_m_od": lv,
                    "label": f"{lv:.1f} m OD",
                    "sheet_area_m2": round(area, 1),
                    "sheet_pixels": int(size),
                    "survey_vintage": vintage,
                    "title": (
                        f"WATER LEVEL {lv:.1f} m above Ordnance Datum — the height of "
                        f"this flat sheet of water as the {vintage} LIDAR survey found "
                        f"it, over about {area:,.0f} square metres. The amber and brown "
                        f"paint on the banks beside it is measured against THIS level "
                        f"and not against one taken elsewhere on the canal. It is a "
                        f"detected water surface, not a surveyed pound datum, and the "
                        f"water was this height in {vintage}."),
                    "aria_label": (
                        f"Detected water level {lv:.1f} metres above Ordnance Datum, "
                        f"over about {area:,.0f} square metres of water. Bank heights "
                        f"nearby are measured against this level. From the {vintage} "
                        f"LIDAR survey."),
                },
            })
    out.sort(key=lambda f: -f["properties"]["level_m_od"])
    return out


# ================================================================================
# THE PYRAMID
# ================================================================================
def deg2num(lat: float, lon: float, z: int) -> tuple[int, int]:
    """Slippy-map tile containing (lat, lon). Identical maths to nav/satellite.py so the
    overlay lands on the imagery pixel for pixel."""
    lat_r = math.radians(lat)
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = str(s).lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _init_mbtiles(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE TABLE IF NOT EXISTS metadata (name text, value text);"
        "CREATE TABLE IF NOT EXISTS tiles (zoom_level integer, tile_column integer,"
        " tile_row integer, tile_data blob);"
        "CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles"
        " (zoom_level, tile_column, tile_row);"
    )


def read_tile(name: str, z: int, x: int, y: int) -> bytes | None:
    """One overlay PNG back out, or None.

    PURE STDLIB, and that is the point: this is the SERVING path and it has to work on a
    machine that cannot BUILD tiles, so that "the tiles are on this card" and "this
    machine could make them" stay separate facts. MBTiles rows are TMS-flipped (y from
    the south) exactly as in nav/satellite.py; the flip lives here rather than in the
    caller so the two archives are addressed identically.
    """
    path = tiles_path(name)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, (1 << z) - 1 - y)).fetchone()
        return bytes(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _tiles_held(path: Path) -> tuple[int | None, str]:
    """How many tiles the archive ACTUALLY holds — counted, not read off the record.

    A .mbtiles that EXISTS is not a .mbtiles with tiles in it. A build killed partway,
    a card pulled out of the handheld mid-write, a copy onto a full disk: all three
    leave a file of the right name and the right shape that opens and serves nothing.
    The build record beside it still says what the build INTENDED to write, so trusting
    that record means reporting PRESENT — "the LIDAR covers this whole corridor and
    every pixel of it has been classified" — over an archive serving zero tiles. That is
    the bare-satellite lie this whole layer exists to prevent, and told this way it is
    worse than a missing file: a missing file at least reads as missing.

    PURE STDLIB, like read_tile beside it and for the same reason — this answers for the
    SERVING path and has to work on a machine that could never have built the tiles.

    Returns (count, "") or (None, why). None is not zero. An archive that will not answer
    and an archive that answers "none" are two different claims about this card.
    """
    con = None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = con.execute("SELECT count(*) FROM tiles").fetchone()
        return int(row[0]), ""
    except sqlite3.Error as exc:
        return None, str(exc)
    except (TypeError, ValueError, IndexError) as exc:   # a tiles table of another shape
        return None, f"the tile count came back unreadable ({exc})"
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


def _tile_pixel_lonlat(np, z: int, x: int, y: int):
    """Longitudes of a tile's 256 columns and latitudes of its 256 rows.

    SEPARABLE, which is the whole reason the tiler is quick: in Web Mercator a pixel's
    longitude depends only on its column and its latitude only on its row, so two
    256-long vectors describe all 65,536 pixels.
    """
    n = float(1 << z) * BANK_TILE_SIZE
    p = np.arange(BANK_TILE_SIZE, dtype=np.float64) + 0.5
    lon = (x * BANK_TILE_SIZE + p) / n * 360.0 - 180.0
    wy = (y * BANK_TILE_SIZE + p) / n
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * wy))))
    return lon, lat


def _tile_rgba(np, raster: BankRaster, z: int, x: int, y: int):
    """One tile's RGBA, or None when nothing in it is painted.

    TWO REGIMES, chosen per zoom by comparing the tile's ground resolution with the
    grid's:

      UPSAMPLING (tile pixels finer than the grid, roughly z18 and in) — NEAREST
      NEIGHBOUR on the class raster. Nearest cannot invent a class that is not there;
      interpolating between class codes 2 and 3 would produce a 2.5 that means nothing.
      Alpha up there is either nothing or the layer's one declared opacity.

      DOWNSAMPLING (tile pixels coarser than the grid) — COUNT the grid pixels under each
      tile pixel. The colour is the majority of the painted ones and the alpha is how much
      of the pixel is painted at all, so a tile pixel that is half water comes out half
      transparent and the imagery shows through the half that is water. A pixel with no
      amber under it counts zero amber and can never come out amber — the guarantee that
      resampling a finished image cannot make.
    """
    grid = raster.grid
    h, w = grid.shape
    lon, lat = _tile_pixel_lonlat(np, z, x, y)

    col = (lon - grid.west) / grid.px_lon - 0.5
    row = (grid.north - lat) / grid.px_lat - 0.5
    if col[-1] < -0.5 or col[0] > w - 0.5 or row[-1] < -0.5 or row[0] > h - 0.5:
        return None                                     # tile is off the survey entirely

    amber = np.array(_hex_rgb(settings.lidar_colour_low), dtype=np.float32)
    brown = np.array(_hex_rgb(settings.lidar_colour_high), dtype=np.float32)

    if 360.0 / (float(1 << z) * BANK_TILE_SIZE) <= abs(grid.px_lon):
        ci = np.clip(np.rint(col).astype(np.int64), 0, w - 1)
        ri = np.clip(np.rint(row).astype(np.int64), 0, h - 1)
        inside = (((col >= -0.5) & (col <= w - 0.5))[None, :]
                  & ((row >= -0.5) & (row <= h - 0.5))[:, None])
        sel = np.ix_(ri, ci)
        cls = raster.classes[sel]
        is_low = (cls == BANK_CLASS_LOW) & inside
        painted = is_low | ((cls == BANK_CLASS_HIGH) & inside)
        if not painted.any():
            return None
        f_paint = painted.astype(np.float32)
        shade = raster.shade[sel]
        cont = raster.contour[sel]
    else:
        c0, c1 = max(0, int(math.floor(col[0]))), min(w, int(math.ceil(col[-1])) + 1)
        r0, r1 = max(0, int(math.floor(row[0]))), min(h, int(math.ceil(row[-1])) + 1)
        if c1 <= c0 or r1 <= r0:
            return None
        # Forward Web Mercator for every grid pixel in the window — separable again.
        n = float(1 << z) * BANK_TILE_SIZE
        glons = grid.west + grid.px_lon * (np.arange(c0, c1, dtype=np.float64) + 0.5)
        glats = grid.north - grid.px_lat * (np.arange(r0, r1, dtype=np.float64) + 0.5)
        tx = np.floor((glons + 180.0) / 360.0 * n).astype(np.int64) - x * BANK_TILE_SIZE
        lat_r = np.radians(np.clip(glats, -85.05112878, 85.05112878))
        ty = np.floor((1.0 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2.0 * n
                      ).astype(np.int64) - y * BANK_TILE_SIZE
        okx, oky = (tx >= 0) & (tx < BANK_TILE_SIZE), (ty >= 0) & (ty < BANK_TILE_SIZE)
        if not okx.any() or not oky.any():
            return None
        sub = np.ix_(oky, okx)
        cls = raster.classes[r0:r1, c0:c1][sub]
        idx = (ty[oky][:, None] * BANK_TILE_SIZE + tx[okx][None, :]).ravel()
        nb = BANK_TILE_SIZE * BANK_TILE_SIZE
        shape = (BANK_TILE_SIZE, BANK_TILE_SIZE)
        low_m = (cls == BANK_CLASS_LOW).ravel()
        high_m = (cls == BANK_CLASS_HIGH).ravel()
        paint_m = low_m | high_m
        if not paint_m.any():
            return None
        n_all = np.bincount(idx, minlength=nb).astype(np.float32)
        n_low = np.bincount(idx[low_m], minlength=nb).astype(np.float32)
        n_high = np.bincount(idx[high_m], minlength=nb).astype(np.float32)
        s_shade = np.bincount(idx[paint_m], weights=raster.shade[r0:r1, c0:c1][sub].ravel()[paint_m],
                              minlength=nb).astype(np.float32)
        s_cont = np.bincount(idx[paint_m], weights=raster.contour[r0:r1, c0:c1][sub].ravel()[paint_m],
                             minlength=nb).astype(np.float32)
        n_paint = n_low + n_high
        f_paint = (n_paint / np.maximum(n_all, 1.0)).reshape(shape)
        # TIES AND MIXTURES GO BROWN. Amber is the colour that says "this might be a way
        # down"; brown says nothing more than "not that". Deciding a 50/50 pixel in
        # amber's favour would let a filter kernel promote half a wall into a launch
        # zone, and the asymmetry between those two mistakes is why the rule exists.
        is_low = (n_low > n_high).reshape(shape)
        # Neutral where nothing is painted: those pixels get alpha 0, and 1.0 / 0.0 keep
        # them out of the arithmetic as neutral values rather than as NaN.
        shade = np.where(n_paint > 0, s_shade / np.maximum(n_paint, 1.0), 1.0).reshape(shape)
        cont = np.where(n_paint > 0, s_cont / np.maximum(n_paint, 1.0), 0.0).reshape(shape)

    base = np.where(np.asarray(is_low, dtype=bool)[..., None],
                    amber[None, None, :], brown[None, None, :])
    rgb = base * shade[..., None]
    # White contours blended in AFTER the shade, so they read as lines drawn on the relief
    # rather than being darkened into it.
    a_c = (float(settings.lidar_contour_alpha) * cont)[..., None]
    rgb = rgb * (1.0 - a_c) + 255.0 * a_c
    alpha = np.clip(f_paint * BANK_PAINT_ALPHA, 0.0, 1.0) * 255.0

    out = np.zeros((BANK_TILE_SIZE, BANK_TILE_SIZE, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.rint(alpha).astype(np.uint8)
    # NOTHING UNPAINTED CARRIES A COLOUR. Belt and braces over the alpha above: this is
    # the last line before the tile leaves the module, and it is the one place a future
    # edit could put a colour on the channel.
    out[out[..., 3] == 0] = 0
    return out if out[..., 3].any() else None


def write_pyramid(name: str, raster: BankRaster, zmin: int | None = None,
                  zmax: int | None = None, progress=None) -> dict:
    """Render the class raster into an XYZ pyramid → <area>.lidar/bank.mbtiles.

    Fully transparent tiles are NOT written. A tile that would paint nothing is a tile the
    client skips, and writing thousands of blank PNGs would cost a lot of card to say
    nothing at all. What that absence MEANS is settled by the area's record and its state
    — never by a missing tile — which is exactly why a partial survey is reported as
    PARTIAL instead of being left to look like clear water.
    """
    np, _, _, Image = _deps()
    zmin = BANK_ZMIN if zmin is None else int(zmin)
    zmax = BANK_ZMAX if zmax is None else int(zmax)
    if zmin > zmax:
        # Left alone, range(18, 14) is empty, the archive is written with no tiles in it
        # and the area reports ABSENT — a typo in a zoom argument wearing the clothes of
        # "nobody has surveyed this", which is the one confusion this layer exists to stop.
        raise BankUnavailable(
            f"zmin {zmin} is above zmax {zmax}, so no zoom level would be rendered at "
            f"all. An area built from that would report itself ABSENT and look exactly "
            f"like a stretch of canal nobody has surveyed.")
    path = tiles_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A rebuild REPLACES the pyramid rather than merging into it. A stale tile from a
    # previous survey sitting beside a fresh one is a map that is half two surveys, and
    # nothing on it would say which half you were looking at.
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    _init_mbtiles(con)

    bbox = raster.grid.bbox
    written = blank = 0
    per_zoom: dict[str, int] = {}
    t0 = time.time()
    try:
        for z in range(zmin, zmax + 1):
            xa, ya = deg2num(bbox[1], bbox[0], z)
            xb, yb = deg2num(bbox[3], bbox[2], z)
            zw = 0
            for tx in range(min(xa, xb), max(xa, xb) + 1):
                for ty in range(min(ya, yb), max(ya, yb) + 1):
                    rgba = _tile_rgba(np, raster, z, tx, ty)
                    if rgba is None:
                        blank += 1
                        continue
                    buf = io.BytesIO()
                    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
                    con.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)",
                                (z, tx, (1 << z) - 1 - ty, buf.getvalue()))
                    written += 1
                    zw += 1
            con.commit()          # per zoom: a run killed part-way keeps whole levels
            per_zoom[str(z)] = zw
            if progress:
                progress({"name": name, "state": "running", "zoom": z,
                          "tiles": written, "blank": blank})
        for k, v in {
                "name": f"{name} launch banks", "format": "png", "type": "overlay",
                "minzoom": str(zmin), "maxzoom": str(zmax),
                "bounds": ",".join(f"{b:.6f}" for b in bbox),
                "attribution": settings.lidar_attribution,
                "description": (
                    f"Launch-bank classification over the canal corridor. Amber is bank "
                    f"less than {settings.lidar_launch_max_height_m:g} m above the water "
                    f"beside it — a measured height, not permission to launch. Brown is "
                    f"higher bank and urban fabric. Water is never painted.")}.items():
            con.execute("INSERT INTO metadata VALUES (?,?)", (k, v))
        con.commit()
    finally:
        con.close()
    return {"tiles": written, "blank": blank, "zmin": zmin, "zmax": zmax,
            "per_zoom": per_zoom, "tile_seconds": round(time.time() - t0, 2),
            "bytes": path.stat().st_size if path.exists() else 0,
            "bbox": [round(b, 7) for b in bbox],
            "scheme": "XYZ 256 px PNG, MBTiles (TMS rows), same as the satellite archive"}


# ================================================================================
# THE JOB, AND WHAT IT LEAVES BEHIND
# ================================================================================
def render_area(name: str, *, zmin: int | None = None, zmax: int | None = None,
                progress=None) -> dict:
    """Classify the stored grid for one area and write its pyramid, labels and record.

    Synchronous and CPU-bound; an async caller wraps it in asyncio.to_thread. Returns the
    record it wrote, which is what `card()` reads back.
    """
    t0 = time.time()
    grid = load_grid(name)
    raster = classify(grid)
    tiles = write_pyramid(name, raster, zmin=zmin, zmax=zmax, progress=progress)
    vintage = settings.lidar_survey_vintage
    src = grid.provenance or {}

    _write_atomic(pounds_path(name), json.dumps({
        "type": "FeatureCollection",
        "layer": BANK_LAYER_KEY, "area": name,
        "survey_vintage": vintage,
        "attribution": settings.lidar_attribution,
        "title": ("Detected water levels, one per flat sheet of water the LIDAR found "
                  "along this corridor. Each is the height the banks beside it are "
                  "measured against — a detected water surface, not a surveyed datum."),
        "aria_label": (f"{len(raster.pounds)} detected water level labels for area "
                       f"{name}, from the {vintage} LIDAR survey."),
        "features": raster.pounds,
    }, indent=1))

    state, why = _state_of(raster.stats, tiles["tiles"])
    prov = {
        "layer": BANK_LAYER_KEY,
        "area": name,
        "state": state,
        "why": why,
        "vintage": vintage,
        "survey_vintage": vintage,
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bbox": [round(v, 7) for v in grid.bbox],
        "attribution": settings.lidar_attribution,
        "grid": {"width": int(grid.shape[1]), "height": int(grid.shape[0]),
                 "px_m": [round(grid.px_x_m, 4), round(grid.px_y_m, 4)],
                 "nodata_fraction": round(1.0 - float(grid.valid.mean()), 4)},
        # Carried forward from the acquisition half so ONE record answers "when was this
        # flown, when was it fetched, and when was it painted".
        "source": {"fetched": src.get("fetched"),
                   "survey_vintage": src.get("survey_vintage", vintage),
                   "download_state": src.get("state"),
                   "download_why": src.get("why")},
        "corridor": {"water_buffer_m": settings.lidar_water_buffer_m,
                     "band_buffer_m": settings.lidar_band_buffer_m,
                     "total_half_width_m": (float(settings.lidar_water_buffer_m)
                                            + float(settings.lidar_band_buffer_m)),
                     "centreline_layer": settings.lidar_centreline_layer,
                     "centreline_why": raster.stats.get("centreline_why")},
        "classification": {"launch_max_height_m": settings.lidar_launch_max_height_m,
                           "colour_low": settings.lidar_colour_low,
                           "colour_high": settings.lidar_colour_high,
                           "paint_alpha": BANK_PAINT_ALPHA},
        "hillshade": {"azimuth_deg": settings.lidar_hillshade_azimuth_deg,
                      "altitude_deg": settings.lidar_hillshade_altitude_deg,
                      "z_factor": settings.lidar_hillshade_z_factor},
        "contours": {"interval_m": settings.lidar_contour_interval_m,
                     "width_px": settings.lidar_contour_width_px,
                     "alpha": settings.lidar_contour_alpha},
        "pound_detection": {"bin_m": settings.lidar_pound_bin_m,
                            "separation_m": settings.lidar_pound_separation_m,
                            "flat_gradient": settings.lidar_flat_gradient,
                            "sample_m": settings.lidar_water_sample_m,
                            "min_sheet_pixels": BANK_POUND_MIN_PIXELS},
        "levels": raster.levels,
        # TWO COUNTS, AND THEY ARE DIFFERENT NUMBERS. `pounds` is how many distinct water
        # levels were detected — the one nav/service.py reads to say "N water level(s)
        # detected". `pound_labels` is how many LABEL POINTS were placed, which is higher
        # whenever a bridge or a building splits one pound into two visible sheets, and
        # reporting that as the number of pounds would invent locks that are not there.
        "pounds": len(raster.levels),
        "pound_labels": len(raster.pounds),
        "stats": raster.stats,
        "tiles": tiles,
        "seconds": round(time.time() - t0, 2),
        "title": _title(name, raster, state, why),
        "aria_label": _aria(name, raster, state),
        "survey": (
            f"Every height in this layer comes from the Environment Agency's {vintage} "
            f"1 m LIDAR composite terrain model."),
        # THE DATE ON ITS OWN IS NOT A WARNING. "2022" in a field is a number a hurried
        # operator reads straight past; what has to travel with it is what a four-year-old
        # survey of a bank actually means, in a sentence, in the same voice every other
        # absence on this console is reported in.
        "vintage_warning": _vintage_warning(vintage),
        "what_amber_means": _WHAT_AMBER_MEANS,
        "water_is_never_painted": _WATER_RULE,
        "what_is_missing": _WHAT_IS_MISSING,
    }
    _write_atomic(render_provenance_path(name), json.dumps(prov, indent=1))
    return prov


_WHAT_AMBER_MEANS = (
    "AMBER means the ground stands less than 2 m above the water beside it. That is a "
    "geometric fact measured from a LIDAR terrain model and it is NOT a statement that "
    "you can launch there. It knows nothing about fences, gates, private land, live "
    "railway, reed beds, mud, or whether anything can be carried to it. The recon run "
    "that proved this layer painted a railway cutting amber.")

_WATER_RULE = (
    "LIDAR cannot see through water, so this layer knows nothing about the channel and "
    "paints none of it. Every water pixel is fully transparent and the satellite imagery "
    "shows through it unaltered. Nothing here says anything whatever about depth.")

_WHAT_IS_MISSING = (
    "The corridor is buffered from the Canal & River Trust canal centreline, so an arm, "
    "a basin or a marina that is not in that centreline is not in this layer at all. "
    "Ground with no paint on it has NOT been surveyed and found high — it has not been "
    "looked at.")


def _vintage_warning(vintage: str) -> str:
    """The survey year with the sentence that makes it mean something.

    The year is taken from the settings constant and NEVER from the clock:
    `time.gmtime().tm_year` is the wrong kind of right — it agrees with the data in 2022
    and lies in every year this vehicle will actually fly.
    """
    return (f"The terrain behind this layer is the {vintage} LIDAR survey, and nothing "
            f"in it has been checked since {vintage}. Banks change: piling is driven, "
            f"moorings are built, edges collapse, wharves are filled in and slipways are "
            f"fenced off. Treat every amber pixel as a {vintage} observation and not as "
            f"a description of what is there today.")


def _state_of(stats: dict, tiles: int) -> tuple[str, str]:
    """PRESENT / PARTIAL / ABSENT, and the sentence that goes with it.

    Never a silent fallback to bare satellite. Bare satellite over a canal looks exactly
    like "there are no low banks here", and an operator who reads a hole in the survey as
    a surveyed result walks a bank that was never looked at.
    """
    if stats.get("why_empty"):
        return "absent", stats["why_empty"]
    if not stats.get("corridor_pixels"):
        return "absent", ("no canal centreline crosses this area, so there is no corridor "
                          "and nothing has been classified here.")
    if tiles == 0:
        return "absent", (
            "the corridor was traced but no tile carries any paint. Treat this area as "
            "UNSURVEYED for bank height — the imagery under it says nothing about "
            "whether a bank is low or a wall.")
    cov = stats.get("corridor_coverage", 0.0)
    if cov < float(settings.lidar_min_coverage):
        return "partial", (
            f"the LIDAR reaches only {cov * 100:.1f}% of this corridor — below the "
            f"{float(settings.lidar_min_coverage) * 100:.0f}% this layer treats as "
            f"usable. Most of this area is drawn as nothing, which is NOT the same as "
            f"bank that was measured and found high.")
    if cov < 0.995:
        return "partial", (
            f"the LIDAR reaches {cov * 100:.1f}% of this corridor. The rest was not flown "
            f"or not delivered and is drawn as nothing, which is NOT the same as bank "
            f"that was measured and found high.")
    return "present", ("the LIDAR covers this whole corridor and every pixel of it has "
                       "been classified.")


def _title(name: str, raster: BankRaster, state: str, why: str) -> str:
    s = raster.stats
    lv = ", ".join(f"{r['level_m_od']:.1f}" for r in raster.levels) or "none"
    return (f"LAUNCH BANKS, {name}: {state.upper()}. {why} "
            f"Amber is bank under {settings.lidar_launch_max_height_m:g} m above the "
            f"water beside it and is {s.get('amber_fraction_of_painted', 0) * 100:.0f}% "
            f"of the paint; brown is everything higher. Water carries no paint at all, "
            f"because nothing here knows the depth. Water levels detected, metres above "
            f"Ordnance Datum: {lv}. Environment Agency LIDAR Composite DTM 1 m, "
            f"{settings.lidar_survey_vintage} survey.")


def _aria(name: str, raster: BankRaster, state: str) -> str:
    s = raster.stats
    return (f"Launch bank layer for area {name}. Status {state}. "
            f"{s.get('amber_fraction_of_painted', 0) * 100:.0f} percent of the painted "
            f"corridor is amber, meaning bank less than "
            f"{settings.lidar_launch_max_height_m:g} metres above the water beside it — "
            f"a measured height, not permission to launch. {len(raster.levels)} water "
            f"levels detected. From the {settings.lidar_survey_vintage} LIDAR survey.")


def card(name: str) -> dict:
    """What PAINT this area holds, off the disk, with no network and no numpy.

    Mirrors lidar.card() on purpose — the console asks both halves the same question and
    shows both answers. Callable on a machine with none of the libraries installed,
    because the console has to be able to say WHY the layer is missing, and a status call
    that needed the missing library to answer would be silent in exactly the case it
    exists for.
    """
    tiles = tiles_path(name)
    libs = library_state()
    try:
        prov = json.loads(render_provenance_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        held = lidar.card(name)
        return {"area": name, "layer": BANK_LAYER_KEY, "state": "absent",
                "painted": False, "tiles_bytes": 0, "libraries": libs,
                "survey_vintage": settings.lidar_survey_vintage,
                "why": (f"no launch-bank paint has been built for the area '{name}'. "
                        f"{held.get('why', '')} The satellite imagery is drawn unpainted, "
                        f"and unpainted means NOT SURVEYED here — it does not mean there "
                        f"are no low banks."),
                "title": (f"LAUNCH BANKS, {name}: ABSENT — this handheld holds no bank "
                          f"classification for this area."),
                "aria_label": (f"Launch bank layer for area {name} is absent. No bank "
                               f"classification is held.")}
    except Exception as exc:  # noqa: BLE001
        # A record that will not parse describes an area nobody can vouch for: nothing on
        # it can be dated or attributed, so it is not served as a survey.
        return {"area": name, "layer": BANK_LAYER_KEY, "state": "absent",
                "painted": False, "tiles_bytes": 0, "libraries": libs,
                "why": (f"the record beside this area's bank tiles is unreadable ({exc}), "
                        f"so nothing here can be dated or attributed. Rebuild the layer "
                        f"before trusting the paint."),
                "title": f"LAUNCH BANKS, {name}: ABSENT — unreadable record.",
                "aria_label": f"Launch bank layer for area {name} has an unreadable record."}
    prov["painted"] = tiles.exists()
    prov["tiles_bytes"] = tiles.stat().st_size if tiles.exists() else 0
    prov["libraries"] = libs
    if not tiles.exists() and prov.get("state") != "absent":
        # The mirror of the empty-layer lie: everything says the area is here and the
        # pixels are gone.
        prov["state"] = "absent"
        prov["why"] = ("the record for this area's paint is on the card but the tile "
                       "archive beside it is not, so there is nothing to draw. Rebuild "
                       "the layer.")
    elif tiles.exists() and prov.get("state") != "absent":
        # AND THE SAME LIE ONE STEP QUIETER: the archive is present, so the check above
        # is satisfied, and it is EMPTY or SHORT. The record describes the build; only
        # the archive describes what will actually reach the glass, so the archive is
        # what gets counted before this card claims anything.
        held, why_not = _tiles_held(tiles)
        claimed = int((prov.get("tiles") or {}).get("tiles") or 0)
        prov["tiles_held"] = held
        prov["tiles_claimed"] = claimed
        if held is None:
            prov["state"] = "unreadable"
            prov["painted"] = False
            prov["why"] = (
                f"the tile archive for this area is on the card but will not be read "
                f"({why_not}). Its record claims {claimed} tiles; none of them can be "
                f"served, so this area is drawn as nothing. Unpainted means NOT "
                f"SURVEYED — it does not mean there are no low banks. Rebuild the layer.")
            prov["title"] = (f"LAUNCH BANKS, {name}: UNREADABLE — the tile archive is "
                             f"there and will not open.")
            prov["aria_label"] = (f"Launch bank layer for area {name} is unreadable. The "
                                  f"tile archive will not open and no paint can be drawn.")
        elif held == 0:
            prov["state"] = "unreadable"
            prov["painted"] = False
            prov["why"] = (
                f"the tile archive for this area opens and is EMPTY: its record claims "
                f"{claimed} tiles and it holds none. This is a broken card and not an "
                f"unsurveyed area — nothing will be drawn, and unpainted means NOT "
                f"SURVEYED, not measured and found high. Rebuild the layer.")
            prov["title"] = (f"LAUNCH BANKS, {name}: UNREADABLE — the tile archive is "
                             f"empty, {claimed} tiles expected.")
            prov["aria_label"] = (f"Launch bank layer for area {name} is unreadable. The "
                                  f"tile archive holds no tiles of {claimed} expected.")
        elif claimed and held < claimed:
            prov["state"] = "partial"
            prov["why"] = (
                f"the tile archive holds {held} of the {claimed} tiles its record claims "
                f"were built, so part of this corridor will draw as nothing that was in "
                f"fact classified. Ground with no paint here has NOT been surveyed and "
                f"found high — some of it was surveyed and lost. Rebuild the layer.")
            prov["title"] = (f"LAUNCH BANKS, {name}: PARTIAL — {held} of {claimed} tiles "
                             f"survive on this card.")
            prov["aria_label"] = (f"Launch bank layer for area {name} is partial. The tile "
                                  f"archive holds {held} of {claimed} tiles.")
    return prov


def pound_labels(name: str) -> dict | None:
    """The detected water-level labels for an area, as GeoJSON. Pure stdlib."""
    try:
        return json.loads(pounds_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("unreadable pound labels for %s: %s", name, exc)
        return None


def list_painted() -> list[dict]:
    """Every area on this card that has a bank layer, with its state."""
    d = settings.areas_dir
    if not d.is_dir():
        return []
    suffix = settings.lidar_dir_suffix
    return [card(p.name[: -len(suffix)]) for p in sorted(d.iterdir())
            if p.is_dir() and p.name.endswith(suffix) and (p / _PROV_NAME).exists()]


# ================================================================================
# CLI — bootstrap and bench. Reads files, writes files, touches no network.
# ================================================================================
def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m nav.bank",
        description="Paint the LIDAR launch-bank overlay for an offline area. Reads the "
                    "grid nav/lidar.py downloaded; touches no network.")
    ap.add_argument("area", nargs="?", help="area name (as used by nav.lidar)")
    ap.add_argument("--status", metavar="AREA", help="what paint is on the card")
    ap.add_argument("--list", action="store_true", help="every area with a bank layer")
    ap.add_argument("--levels", metavar="AREA", help="detect and print water levels only")
    ap.add_argument("--zmin", type=int, default=None)
    ap.add_argument("--zmax", type=int, default=None)
    args = ap.parse_args(argv)

    if args.status:
        print(json.dumps(card(args.status), indent=1))
        return 0
    if args.list:
        print(json.dumps(list_painted(), indent=1))
        return 0
    libs = library_state()
    if not libs["ok"]:
        print(libs["why"])
        return 2
    try:
        if args.levels:
            grid = load_grid(args.levels)
            raster = classify(grid)
            print(f"{args.levels}: {grid.shape[1]}x{grid.shape[0]} px, "
                  f"{grid.px_x_m:.3f} x {grid.px_y_m:.3f} m")
            if not raster.levels:
                print("no water levels detected — " + raster.stats.get(
                    "why_empty", "no flat sheet of water was found in the sampling band."))
            for r in raster.levels:
                print(f"  {r['level_m_od']:8.2f} m OD   "
                      f"{r['sheet_pixels']:>7,} px of flat water")
            print(json.dumps(raster.stats, indent=1))
            return 0
        if not args.area:
            ap.print_help()
            return 1

        def show(ev):
            print(f"  z{ev['zoom']:>2}: {ev['tiles']:>5} tiles written, "
                  f"{ev['blank']:>5} empty skipped")

        prov = render_area(args.area, zmin=args.zmin, zmax=args.zmax, progress=show)
        print()
        print(prov["title"])
        print(json.dumps({k: prov[k] for k in ("state", "levels", "stats", "tiles")},
                         indent=1))
        return 0 if prov["state"] != "absent" else 3
    except BankUnavailable as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))

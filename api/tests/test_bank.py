"""The launch-bank layer (nav/bank.py, and nav/lidar.py's decoder) driven against a
synthetic canal whose every height is known.

Run:  cd api && python -m unittest tests.test_bank -v
      python api/tests/run.py bank

WHAT THIS LAYER CLAIMS, because every check below is one way of failing to claim it:

    "The ground on this side of the cut stands less than 2 m above the water you
     would be floating on."

Three nouns, three separate opportunities to be confidently wrong.

  THE GROUND comes off a float32 LIDAR GeoTIFF, and the value that means "no survey
  here" is a NUMBER in that file — -9999, or the composite's own -3.4e38. It is a
  perfectly valid float. Put through a histogram it drags the range down by ten
  kilometres; put through np.gradient it is a cliff of 10,000 m across one pixel,
  and the hillshade of that saturates into a hard wall drawn around every hole in
  the survey. Neither failure raises. Both draw a confident picture.

  THE WATER YOU WOULD BE FLOATING ON is not one level. A canal is a staircase of
  pounds, each flat, each a lock's drop below the last, and "2 m above the water"
  against a single global constant paints every bank below the top lock brown and
  every bank above the bottom lock amber. The fixture is built so that NO SINGLE
  GLOBAL DATUM CAN CLASSIFY BOTH OF ITS PROBES CORRECTLY. That is arithmetic, not
  taste, and _no_global_datum() writes it out in the failure message.

  LESS THAN 2 m is a rule with an edge, and `<` and `<=` are one character apart.
  A bank standing at exactly 2.0000 m has to land on the declared side of it. The
  ladder in the fixture steps 1.9375 / 2.0 / 2.0625 through that edge in values
  that are EXACT in float32, so what the check measures is the comparison and not a
  rounding artefact.

AND TWO THAT ARE ABOUT THE DRAWING RATHER THAN THE DATA:

  WATER IS NEVER PAINTED. LIDAR cannot see through water, so this layer knows
  nothing whatever about the channel, and a wash of colour over it would be read as
  knowing something. Every water pixel is asserted transparent — the WHOLE region,
  not a sample, and on the real PNG tiles rather than on the class array — because
  the failure worth catching is a fringe: one pixel of bank colour bleeding over
  the water's edge is invisible in a spot check and is exactly what a dilate-then-
  classify pipeline leaves behind.

  A LOW-ZOOM PIXEL MAY NOT INVENT A CLASS. Averaging RGBA to zoom out manufactures
  amber out of brown-beside-transparent, because half-covered brown is a lighter
  brown. So the fixture contains a WHOLE POUND WITH NO AMBER IN IT ANYWHERE — brown
  bank running straight into transparent water for 128 m — and any amber over that
  pound at any zoom came from a filter kernel and not from the survey. A launch
  bank invented by a resampler is the worst single failure this layer has.

WHY THE CORRIDOR IS BUFFERED FROM THE VECTOR. The obvious way to decide "which
pixels are beside the canal" is to find the flat water and grow outwards. The
fixture has EIGHTY METRES OF COVERED CUT in it — a building over the water, which is
what the terrain model records instead of the water — so a corridor grown out of the
sheet has an eighty-metre hole in it, over the one stretch an operator is most likely
to be walking. Buffered from the Trust's centreline it cannot.

EIGHTY AND NOT TEN, and that number is a finding rather than a detail. The first
version of this fixture used a ten-metre bridge, and the check passed against a
deliberately broken build that detected the corridor from the sheet: the buffer is
34 m either side, so it simply closes a short gap from both ends and no hole ever
appears. A gap has to be longer than the buffer can reach across from either side
before the difference between the two designs is visible at all.

THE DEPENDENCIES ARE OPTIONAL AND THE HONESTY RULE APPLIES TO THEM TOO. numpy,
scipy and Pillow do this work and they live on the HANDHELD; the Pi 3B+ never
carries them, and a bench that has not run `bootstrap.py --dev` has not got them
either. On both of those machines the api must still start and the layer must say,
in a sentence, what is absent and the command that installs it. The last class does
not TRUST that the imports are lazy — `from nav import bank` at the top of this file
succeeded in an interpreter that HAS numpy, which proves nothing about one that does
not. It starts child interpreters with each library genuinely un-importable and asks
them what happened.

NO NETWORK, EVER. This layer reads files and does arithmetic. setUpClass makes every
socket in the process raise, so a stray fetch fails naming this suite instead of
quietly reaching the Environment Agency's coverage service over somebody's hotspot;
the child interpreters install the same guard for themselves.

WHAT IS DELIBERATELY NOT TESTED HERE. The acquisition half's network path — planning,
sub-requests, retries — belongs with nav/lidar.py's own suite. This file takes one
decoded grid and one centreline and asks what the console ends up drawing.
"""
from __future__ import annotations

import dataclasses
import importlib
import io
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

API = Path(__file__).resolve().parent.parent

# The three optional libraries, by IMPORT name and by PIP name — they differ for the
# one that matters most: `import PIL` installs as `Pillow`, and an install line reading
# "pip install PIL" sends the reader to a package that has not existed since 2011.
OPTIONAL = (("numpy", ("numpy",)), ("scipy", ("scipy",)), ("PIL", ("pillow", "pil")))

_MISSING = []
for _imp, _pip in OPTIONAL:
    try:
        importlib.import_module(_imp)
    except Exception:                      # noqa: BLE001 — any import failure counts
        _MISSING.append(_imp)
NEED = ("No module named '%s'" % _MISSING[0]) if _MISSING else None

try:
    from nav import bank, lidar
    from nav.config import settings
except Exception as exc:                   # noqa: BLE001
    bank = lidar = settings = None
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    IMPORT_ERROR = None


# ===========================================================================
# THE FIXTURE
#
# 256 x 160 m of canal on a 1 m EPSG:4326 lattice, with every height decided here
# rather than measured. Stated in PIXELS, because a fixture whose geometry is only
# expressible in decimal degrees is a fixture nobody can check by reading.
#
#   row   0 +--------------------------------------------------------------+
#           |  beyond the corridor: real, high ground, NEVER classified     |
#      46   +--------------------------------------------------------------+
#   |dy|24-33   FAR bench                             [nodata hole in the ]
#   |dy|13-22   NEAR bench       |lock|               [ flat plateau      ]
#   |dy| 0-12   THE WATER        | at |         [-- covered 80 m --]
#   |dy|13-22   NEAR bench       |col |
#   |dy|24-33   FAR bench        |128 |
#     114   +--------------------------------------------------------------+
#           |  beyond the corridor                                          |
#     159   +--------------------------------------------------------------+
#            pound A, 30.125 m OD  |  pound B, 27.125 m OD
#
# THE TWO PROBES, and why they are these numbers:
#
#   PROBE A  cols 60-79, near bench.  31.375 m OD = 30.125 + 1.250  -> AMBER
#   PROBE B  cols 136-169, near bench. 30.625 m OD = 27.125 + 3.500 -> BROWN
#
#   For PROBE A to be amber against a single global datum d: 31.375 - d <  2 => d > 29.375
#   For PROBE B to be brown against that same d:             30.625 - d >= 2 => d <= 28.625
#
# There is no such d. PROBE A is three quarters of a metre HIGHER above Ordnance
# Datum than PROBE B and is the safer of the two, because the water beside it is
# higher still — which is the whole layer in one sentence.
#
# Every elevation is exact in binary (x.125, x.0625, x.5), so the ladder either side
# of the 2 m rule tests a comparison rather than a rounding.
# ===========================================================================
W, H = 340, 160
LAT_C, LON_W = 52.4800, -1.9200          # a plausible bit of the Birmingham cut
CENTRE_ROW = 80

LEVEL_A, LEVEL_B = 30.125, 27.125        # the two pounds, three metres apart
LOCK_COL = 128

WATER_DY = 12                            # the flat sheet runs to |dy| = 12
NEAR_LO, NEAR_HI = 13, 22                # the near bench
FAR_LO, FAR_HI = 24, 33                  # the far bench, still inside the 34 m corridor

# THE COVERED SECTION — eighty metres of cut with a building over it, which is what the
# terrain model records instead of the water. EIGHTY AND NOT TEN, and the number is the
# whole point: the corridor is buffered 34 m either side of the centreline, so a ten
# metre gap in the seed is closed from both ends by the buffer itself and proves
# nothing. A hole only appears in a corridor DETECTED from the sheet when the gap is
# longer than the buffer can reach across, and canals run under wharves, warehouses and
# tunnel mouths for a great deal more than eighty metres.
COVERED = (180, 260)                     # [180, 260)
COVER_BASE = LEVEL_B + 3.5               # where the roof starts, at the near end
COVER_CAMBER = 0.06                      # m per row across the cut
COVER_RAMP = 0.06                        # m per column along it

PLATEAU = (272, 308)                     # near bench and far bench at the SAME height,
                                         # so the ground round the hole has no slope in
                                         # it and the hillshade there has one answer
HOLE_ROWS = (96, 107)                    # the nodata block, inside the plateau
HOLE_COLS = (282, 298)
WET_HOLE_ROWS = (78, 83)                 # a second hole, in the middle of the cut
WET_HOLE_COLS = (44, 57)
NODATA = -9999.0

PROBE_A = (60, 80)
PROBE_B = (136, 170)
PROBE_A_OD = LEVEL_A + 1.25              # 31.375
PROBE_B_OD = LEVEL_B + 3.50              # 30.625

# The ladder through the rule, in columns. 1.9375 and 2.0625 are exact float32.
LADDER = ((80, 90, 1.9375), (90, 100, 2.0), (100, 110, 2.0625), (110, 120, 1.75))

# THE TWO TILTED STRETCHES. detect_pound_levels() histograms the flat near-centreline
# elevations and asks scipy.signal.find_peaks for the modes — and find_peaks cannot
# return index 0, because a boundary bin has no left neighbour to stand out from. A
# perfectly flat lowest pound therefore lands entirely in bin 0 and is never reported.
# Real water is not perfectly flat: a pound's LIDAR surface drifts a few centimetres
# across a strip, which is smooth enough to stay inside the flatness test and wide
# enough to put samples either side of the mode. These two stretches carry that drift,
# and the bank on top of them is offset from the SAME tilted level, so every height in
# them is still exact.
TILT = ((0, 40, 0.15), (308, 340, 0.15))


def _tilt(col: int) -> float:
    for c0, c1, amp in TILT:
        if c0 <= col < c1:
            span = c1 - 1 - c0
            return -amp + 2.0 * amp * ((col - c0) / span if span else 0.0)
    return 0.0


def level_of(col: int) -> float:
    """The water level in this column: the pound, plus the local drift."""
    return (LEVEL_A if col < LOCK_COL else LEVEL_B) + _tilt(col)


def near_h(col: int) -> float:
    """Height of the near bench above the water beside it."""
    if col < 40:   return 0.80 + (col / 39.0) * 0.70      # 0.80..1.50  amber, wide margin
    if col < 60:   return 1.25
    if col < 80:   return 1.25                            # PROBE A
    for c0, c1, h in LADDER:
        if c0 <= col < c1:
            return h
    if col < 128:  return 1.25
    if col < 170:  return 3.50                            # PROBE B
    if col < 180:  return 2.60 + (col - 170) * 0.03       # 2.60..2.87
    if col < 272:  return 3.50                            # the roof sits over this
    if col < 308:  return 3.50                            # the flat plateau
    return 2.60 + (col - 308) * 0.012                     # 2.60..2.98


def far_h(col: int) -> float:
    if col < 40:   return 2.60 + (col / 39.0) * 0.80      # 2.60..3.40  brown
    if col < 128:  return 3.50
    if col < 272:  return 5.00
    if col < 308:  return 3.50                            # the plateau: near == far
    return 5.00 + (col - 308) * 0.015


def _bench_h(col: int, dy: int) -> float:
    """Height above the local water, for any row. The seam at |dy| = 23 is a straight
    interpolation and is left out of every strict check."""
    if dy <= WATER_DY:
        return 0.0
    if dy <= NEAR_HI:
        return near_h(col)
    if dy < FAR_LO:
        return (near_h(col) + far_h(col)) / 2.0
    return far_h(col)


def elevation():
    """The synthetic terrain, float32, nodata already stamped in."""
    import numpy as np

    z = np.zeros((H, W), dtype=np.float64)
    for col in range(W):
        lv = level_of(col)
        for row in range(H):
            z[row, col] = lv + _bench_h(col, abs(row - CENTRE_ROW))

    # THE COVERED SECTION. The survey records the roof, so the water sheet simply stops
    # for eighty metres — the gap the corridor has to be continuous across.
    #
    # SLOPED IN BOTH DIRECTIONS, and that is load-bearing rather than realism. A
    # perfectly flat slab is flat ground, and flat ground beside the cut at the level of
    # the water beside it is what this layer calls WATER — so a flat roof would be
    # admitted as its own datum and the fixture would have no gap in the sheet at all.
    # Sloped, every pixel of it fails the flatness test, which is what makes it a real
    # eighty-metre hole in the flat water for anything that tries to grow a corridor out
    # of the sheet instead of buffering it from the vector.
    for col in range(*COVERED):
        for row in range(H):
            z[row, col] = (COVER_BASE + COVER_CAMBER * abs(row - CENTRE_ROW)
                           + COVER_RAMP * (col - COVERED[0]))

    z = z.astype(np.float32)
    z[HOLE_ROWS[0]:HOLE_ROWS[1], HOLE_COLS[0]:HOLE_COLS[1]] = NODATA
    z[WET_HOLE_ROWS[0]:WET_HOLE_ROWS[1], WET_HOLE_COLS[0]:WET_HOLE_COLS[1]] = NODATA
    return z


def grid_z():
    """The same terrain as the ACQUISITION HALF stores it: NaN where the survey has
    nothing, because nav/lidar.py's decoder recognises the sentinel once, at the door,
    and everything downstream reads NaN and propagates the absence. Handing the raw
    -9999 to bank.classify would be testing a pipeline nobody has."""
    import numpy as np
    z = elevation()
    z[z <= NODATA + 1.0] = np.float32("nan")
    return z


def truth():
    """Every region this suite reasons about, as boolean arrays. Ground truth BY
    CONSTRUCTION — not one of these is read back out of the module under test."""
    import numpy as np

    rows = np.arange(H)[:, None]
    cols = np.arange(W)[None, :]
    dy = np.abs(rows - CENTRE_ROW) + 0 * cols
    col_i = np.broadcast_to(cols, (H, W))

    hole = np.zeros((H, W), dtype=bool)
    hole[HOLE_ROWS[0]:HOLE_ROWS[1], HOLE_COLS[0]:HOLE_COLS[1]] = True
    wet_hole = np.zeros((H, W), dtype=bool)
    wet_hole[WET_HOLE_ROWS[0]:WET_HOLE_ROWS[1], WET_HOLE_COLS[0]:WET_HOLE_COLS[1]] = True
    deck = (col_i >= COVERED[0]) & (col_i < COVERED[1])

    # COLUMNS LEFT OUT OF THE PER-PIXEL COMPARISON, each for a stated reason and none
    # of them because the answer there is inconvenient:
    #   124-131  either side of the lock, where the nearest water to a bank pixel is
    #            diagonally across the step;
    #   178-263  the covered section and a couple of columns either side of it, where
    #            the roof is what the survey saw and the nearest water is round the end
    #            of it rather than straight across the cut;
    quiet = ((col_i >= 124) & (col_i < 132)) | ((col_i >= 178) & (col_i < 264))

    return {
        "dy": dy,
        "hole": hole, "wet_hole": wet_hole, "nodata": hole | wet_hole,
        "deck": deck, "quiet": quiet,
        # The flat sheet, and it is asserted WHOLE. |dy| <= 11 rather than 12 leaves one
        # pixel for the distance transform to disagree about at the buffer's edge.
        "water": (dy <= 11) & ~deck & ~wet_hole,
        # Bench INTERIORS, one pixel clear of every seam.
        "near_in": (dy >= NEAR_LO + 1) & (dy <= NEAR_HI - 1) & ~deck & ~hole & ~quiet,
        "far_in": (dy >= FAR_LO + 1) & (dy <= FAR_HI - 1) & ~deck & ~hole & ~quiet,
        # Two metres clear of the 34 m corridor edge, so no rounding of the buffer can
        # put these on the wrong side of it.
        "outside": dy >= 36,
    }


def expected_class(z, t):
    """What every pixel must come out as, from the fixture's OWN known water levels.
    Ground truth and not a second implementation: the level of a column is something
    this file DECIDED."""
    import numpy as np

    lv = np.array([level_of(c) for c in range(W)], dtype=np.float32)[None, :]
    above = np.asarray(z, dtype=np.float64) - np.asarray(lv, dtype=np.float64)
    out = np.zeros((H, W), dtype=np.uint8)
    out[t["water"]] = bank.BANK_CLASS_WATER
    land = (t["dy"] >= NEAR_LO) & (t["dy"] <= FAR_HI) & ~t["nodata"]
    thr = float(settings.lidar_launch_max_height_m)
    out[land & (above < thr)] = bank.BANK_CLASS_LOW
    out[land & (above >= thr)] = bank.BANK_CLASS_HIGH
    return out


def make_grid(z):
    """The fixture as a bank.Grid — exactly what load_grid() hands classify().

    The lattice is chosen so one column and one row are ONE METRE at this latitude,
    using the same WGS84 series the module uses. That makes every distance in the
    corridor buffer readable as a pixel count, which is what lets the checks below
    name a row rather than a decimal degree."""
    import numpy as np

    phi = math.radians(LAT_C)
    m_lat = (111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
             - 0.0023 * math.cos(6 * phi))
    m_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
             + 0.118 * math.cos(5 * phi))
    px_lon, px_lat = 1.0 / m_lon, 1.0 / m_lat
    north = LAT_C + px_lat * (H / 2.0)
    return bank.Grid(z=z, valid=np.isfinite(z), west=LON_W, north=north,
                     px_lon=px_lon, px_lat=px_lat,
                     px_x_m=px_lon * m_lon, px_y_m=px_lat * m_lat,
                     provenance={"fetched": "2026-08-01T00:00:00Z",
                                 "survey_vintage": settings.lidar_survey_vintage,
                                 "state": "present", "why": "the synthetic fixture"})


def centreline(grid):
    """The Trust's centreline, as classify() spells one: lists of [lon, lat].

    Deliberately runs PAST BOTH ENDS of the grid. A real centreline does, and a fixture
    whose line stopped at the first and last column would let a rounded end cap clip the
    outermost columns and call it geometry."""
    lat = grid.north - (CENTRE_ROW + 0.5) * grid.px_lat
    return [[[grid.west - 20 * grid.px_lon, lat],
             [grid.west + (W + 20) * grid.px_lon, lat]]]


def geotiff_bytes(z, nodata=NODATA):
    """The same terrain as a real float32 GeoTIFF, for nav/lidar.py's decoder.

    Written with Pillow, uncompressed, mode "F", carrying GDAL_NODATA and the
    ModelPixelScale + ModelTiepoint pair — because the DECODE is where the sentinel has
    to be caught, and a fixture that handed over a bare array would skip it."""
    from PIL import Image, TiffImagePlugin

    phi = math.radians(LAT_C)
    m_lat = 111132.92 - 559.82 * math.cos(2 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[42113] = str(nodata); info.tagtype[42113] = 2            # GDAL_NODATA
    info[33550] = (1.0 / m_lon, 1.0 / m_lat, 0.0); info.tagtype[33550] = 12
    info[33922] = (0.0, 0.0, 0.0, LON_W, LAT_C + H / (2.0 * m_lat), 0.0)
    info.tagtype[33922] = 12
    info[34735] = (1, 1, 0, 1, 2048, 0, 1, 4326); info.tagtype[34735] = 3
    buf = io.BytesIO()
    Image.fromarray(z).save(buf, format="TIFF", tiffinfo=info, compression=None)
    return buf.getvalue()


def _no_global_datum() -> str:
    hi = PROBE_A_OD - float(settings.lidar_launch_max_height_m)
    lo = PROBE_B_OD - float(settings.lidar_launch_max_height_m)
    return (f"PROBE A ({PROBE_A_OD:.3f} m OD) is amber only for a datum above "
            f"{hi:.3f} m; PROBE B ({PROBE_B_OD:.3f} m OD) is brown only for a datum at "
            f"or below {lo:.3f} m. No single number satisfies both, so a global water "
            f"level cannot produce this map however it is chosen.")


# ===========================================================================
#  ONE WORLD, BUILT ONCE
# ===========================================================================
_WORLD = None
AREA = "test-cut"


def world():
    """The fixture, classified, tiled, and readable — built once for the whole file.

    Two rasters. `raster` is the shipping one. `plain` is the same classification with
    the relief and the contour lines turned off through the module's own settings, so a
    painted tile pixel is EXACTLY the declared colour and "is this pixel amber?" is a
    question with an answer. Every check that is about the classification uses `plain`;
    every check that is about the picture as shipped uses `raster`.
    """
    global _WORLD
    if _WORLD is not None:
        return _WORLD

    tmp = Path(tempfile.mkdtemp(prefix="neptune-bank-"))
    z = grid_z()
    grid = make_grid(z)
    lines = centreline(grid)
    raster = bank.classify(grid, lines)

    # The plain render. Settings is a FROZEN dataclass, so the flat copy is made with
    # dataclasses.replace and swapped into bank's own module namespace for exactly as
    # long as it is needed — never mutated in place, because every other module in the
    # api holds the same object and a suite that left it re-tuned would change the
    # layer for every check after it.
    flat_settings = dataclasses.replace(
        settings, lidar_hillshade_z_factor=0.0,        # shade == 1.0 everywhere
        lidar_contour_alpha=0.0, lidar_contour_width_px=0.0)

    real_dir, real_settings = lidar.area_lidar_dir, bank.settings
    lidar.area_lidar_dir = lambda name: tmp / f"{name}.lidar"
    try:
        tiles = bank.write_pyramid(AREA, raster, zmin=ZMIN, zmax=ZMAX)
        shipped = _read_pyramid(AREA)
        bank.settings = flat_settings
        plain = bank.classify(grid, lines)
        plain_tiles = bank.write_pyramid(AREA + "-plain", plain, zmin=ZMIN, zmax=ZMAX)
        flat = _read_pyramid(AREA + "-plain")
    finally:
        lidar.area_lidar_dir, bank.settings = real_dir, real_settings

    _WORLD = {"tmp": tmp, "z": z, "grid": grid, "lines": lines,
              "raster": raster, "plain": plain, "t": truth(),
              "want": expected_class(z, truth()),
              "tiles": tiles, "plain_tiles": plain_tiles,
              "shipped": shipped, "flat": flat, "dir": lidar.area_lidar_dir}
    return _WORLD


ZMIN, ZMAX = 13, 18
UPSAMPLE_Z = 18          # asserted below to be an upsampling zoom, never assumed


def zoom_split(grid):
    """(upsampling zooms, downsampling zooms) for this grid.

    DERIVED, not typed. The tiler picks its regime per zoom by comparing the tile's
    ground resolution with the grid's, and the two regimes have different right answers
    — nearest-neighbour cannot invent a class, counting cannot invent one either, but a
    check written for one of them passes vacuously against the other. A change to the
    fixture's pixel size moves this line instead of silently invalidating half the
    pyramid checks."""
    up, down = [], []
    for z in range(ZMIN, ZMAX + 1):
        (up if 360.0 / (float(1 << z) * bank.BANK_TILE_SIZE) <= abs(grid.px_lon)
         else down).append(z)
    return up, down


def _read_pyramid(name):
    """Every tile of an archive, decoded, keyed (z, x, y) -> 256x256x4 uint8.

    Read out of the MBTiles rather than walked over a tile range, because the archive
    is the authority on what it holds: write_pyramid deliberately stores nothing for a
    tile that would paint nothing, and a reader that enumerated the range would have to
    invent a rule for the gaps — which is the same rule the checks are testing."""
    import sqlite3

    import numpy as np
    from PIL import Image

    out = {}
    p = bank.tiles_path(name)
    if not p.exists():
        return out
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        for z, tx, tms_y, blob in con.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
            y = (1 << z) - 1 - tms_y
            out[(int(z), int(tx), int(y))] = np.asarray(
                Image.open(io.BytesIO(bytes(blob))).convert("RGBA"))
    finally:
        con.close()
    return out


def tile_pixel_of(grid, row, col, z):
    """Which (z, x, y, py, px) a grid pixel's CENTRE lands in.

    Uses the tile scheme's own definition — the slippy-map formula, which is also what
    bank.deg2num implements at whole-tile granularity. deg2num at z+8 IS the pixel
    index, because a 256 px tile at z is exactly the tile grid at z+8, and the checks
    below prove that identity against bank.deg2num rather than assuming it."""
    lon, lat = grid.lonlat(row, col)
    n = 1 << (z + 8)
    tx = int((lon + 180.0) / 360.0 * n)
    ty = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    tx = max(0, min(n - 1, tx)); ty = max(0, min(n - 1, ty))
    return z, tx >> 8, ty >> 8, ty & 255, tx & 255


def grid_pixel_of(grid, z, x, y, py, px):
    """The grid pixel a TILE pixel's centre lands in — the inverse of tile_pixel_of.

    Needed because the two regimes have to be checked in opposite directions. Zoomed
    IN, one grid pixel covers several tile pixels and only one of them holds its
    centre, so walking the grid misses the rest; the honest question there is "what is
    under THIS tile pixel", which is this function. Zoomed OUT the mapping is many
    grid pixels to one tile pixel and the forward walk is the right one."""
    n = float(1 << (z + 8))
    lon = ((x * 256 + px + 0.5) / n) * 360.0 - 180.0
    wy = (y * 256 + py + 0.5) / n
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * wy))))
    return (int(round((grid.north - lat) / grid.px_lat - 0.5)),
            int(round((lon - grid.west) / grid.px_lon - 0.5)))


def alpha_at(tiles, grid, row, col, z):
    """The alpha the console draws over one grid pixel, 0 for a tile that is not in the
    archive at all — write_pyramid does not store a tile that would paint nothing."""
    zz, x, y, py, px = tile_pixel_of(grid, row, col, z)
    img = tiles.get((zz, x, y))
    return 0 if img is None else int(img[py, px, 3])


def rgb_at(tiles, grid, row, col, z):
    zz, x, y, py, px = tile_pixel_of(grid, row, col, z)
    img = tiles.get((zz, x, y))
    return None if img is None else tuple(int(v) for v in img[py, px, :3])


# ===========================================================================
#  BASE
# ===========================================================================
class BankTestCase(unittest.TestCase):
    _connect = _create = None
    fail_reason = None

    @classmethod
    def setUpClass(cls):
        # THE GUARD IS ON connect(), NOT ON THE socket CLASS. Replacing socket.socket
        # itself with a function breaks the import of anything that subclasses it, and
        # the failure that produces ("function() argument 'code' must be code") names
        # neither sockets nor this file. Stopping the connection is what is wanted
        # anyway: a socket that is never connected reaches nothing.
        cls._connect, cls._create = socket.socket.connect, socket.create_connection

        def blocked(*_a, **_k):
            raise AssertionError(
                "tests/test_bank.py reached the network. This half of the layer reads a "
                "grid off the disk and does arithmetic — nothing in it may resolve a "
                "hostname, and no check here may either.")

        socket.socket.connect = blocked
        socket.create_connection = blocked
        cls.fail_reason = None
        if NEED or bank is None:
            return
        try:
            cls.w = world()
        except Exception as exc:            # noqa: BLE001 — reported per check below
            # Caught rather than let out of setUpClass: unittest folds a setUpClass
            # explosion into ONE error for the whole class, and a round that verified
            # nothing would read as a single failure instead of as the absence it is.
            import traceback
            cls.fail_reason = (f"the fixture could not be built or classified: "
                               f"{type(exc).__name__}: {exc}\n"
                               + "".join(traceback.format_tb(exc.__traceback__)[-3:]))

    @classmethod
    def tearDownClass(cls):
        if cls._connect is not None:
            socket.socket.connect = cls._connect
        if cls._create is not None:
            socket.create_connection = cls._create

    def setUp(self):
        # A MISSING THIRD-PARTY PACKAGE IS A SKIP. A MISSING nav/bank.py IS A FAILURE.
        # api/tests/run.py draws exactly this line: a top-level name that is not part of
        # the api's own tree gets blamed on pip, and anything under nav/ does not.
        # Reporting "No module named 'nav.bank'" as "install something" would send the
        # next person to pip for a round that did not land.
        if NEED:
            self.skipTest(NEED)
        if IMPORT_ERROR:
            self.fail(f"nav/bank.py or nav/lidar.py could not be imported: "
                      f"{IMPORT_ERROR}. That is not a missing package, it is a missing "
                      f"module of this api, and nothing about the layer was exercised.")
        if self.fail_reason:
            self.fail(self.fail_reason)

    # -- shorthands --------------------------------------------------------
    @property
    def cls_(self):
        return self.w["raster"].classes

    @property
    def t(self):
        return self.w["t"]

    @property
    def grid(self):
        return self.w["grid"]

    def first(self, mask, bad):
        import numpy as np
        rs, cs = np.nonzero(mask & bad)
        return [(int(r), int(c)) for r, c in zip(rs[:8], cs[:8])] or "(none)"

    def cols(self, lo, hi):
        import numpy as np
        m = np.zeros((H, W), dtype=bool)
        m[:, lo:hi] = True
        return m


# ===========================================================================
#  THE FIXTURE ITSELF — asserted before anything is concluded from it
# ===========================================================================
class TheFixtureIsWhatItClaims(BankTestCase):
    """A fixture that quietly stopped containing its adversarial cases would make every
    check in this file pass for no reason at all. These run first and are cheap."""

    def test_the_grid_is_one_metre_pixels(self):
        g = self.grid
        self.assertAlmostEqual(g.px_x_m, 1.0, places=6, msg=f"px_x_m {g.px_x_m}")
        self.assertAlmostEqual(g.px_y_m, 1.0, places=6, msg=f"px_y_m {g.px_y_m}")

    def test_the_centreline_burns_onto_the_row_it_was_aimed_at(self):
        import numpy as np
        line = bank._rasterise_centreline(np, self.grid, self.w["lines"])
        rows = sorted({int(r) for r in np.nonzero(line)[0]})
        self.assertEqual(rows, [CENTRE_ROW],
                         f"the centreline rasterised onto rows {rows}, not row "
                         f"{CENTRE_ROW}; every distance in this file is measured from "
                         f"it and would be off by that many metres")
        self.assertEqual(int(line.sum()), W,
                         f"{int(line.sum())} centreline pixels over {W} columns — a "
                         f"gap in the burnt line gives the distance transform a hole "
                         f"to grow through")

    def test_the_second_pound_holds_no_amber_at_all(self):
        """THE CONSTRUCTED CASE the downsampling checks rely on: 128 m of brown bank
        running straight into transparent water, with no amber anywhere in it."""
        rhs = self.w["plain"].classes[:, LOCK_COL:]
        n = int((rhs == bank.BANK_CLASS_LOW).sum())
        self.assertEqual(n, 0,
                         f"{n} amber pixels were classified over pound B at native "
                         f"resolution. Every bank there is 2.6 m or more above its own "
                         f"water; if amber is appearing, the downsampling checks below "
                         f"are testing nothing.")
        self.assertGreater(int((rhs == bank.BANK_CLASS_HIGH).sum()), 1500,
                           "and there has to be plenty of brown there for a blend to "
                           "have anything to blend")

    def test_the_first_pound_holds_plenty_of_amber(self):
        lhs = self.w["plain"].classes[:, :LOCK_COL]
        self.assertGreater(int((lhs == bank.BANK_CLASS_LOW).sum()), 1000,
                           "pound A has to hold real amber or 'no amber anywhere' is "
                           "the trivially true answer")

    def test_the_tile_pixel_arithmetic_agrees_with_the_modules_own(self):
        """This suite indexes tile pixels with deg2num at z+8. If that identity does not
        hold, every pixel assertion below is reading the wrong pixel and passing."""
        g = self.grid
        for row, col, z in ((0, 0, 18), (80, 128, 18), (159, 255, 17), (40, 60, 13)):
            lon, lat = g.lonlat(row, col)
            zz, x, y, py, px = tile_pixel_of(g, row, col, z)
            self.assertEqual((x, y), bank.deg2num(lat, lon, z),
                             f"tile of grid ({row},{col}) at z{z}")
            fx, fy = bank.deg2num(lat, lon, z + 8)
            self.assertEqual((fx >> 8, fy >> 8, fy & 255, fx & 255), (x, y, py, px),
                             f"pixel of grid ({row},{col}) at z{z}")

    def test_the_zoom_this_suite_reads_pixels_at_really_is_the_nearest_one(self):
        """Several checks read one tile pixel per grid pixel at z18 and expect the
        class of that grid pixel back. That is only true where the tiler is in its
        NEAREST regime; at a downsampling zoom the same read is a coverage average and
        the checks would be asking a question with no answer."""
        up, down = zoom_split(self.grid)
        self.assertIn(UPSAMPLE_Z, up,
                      f"z{UPSAMPLE_Z} downsamples this grid (upsampling zooms: {up}); "
                      f"every per-pixel read in this file is at that zoom")
        self.assertTrue(down, f"nothing in z{ZMIN}..{ZMAX} downsamples this grid, so "
                              f"the whole pyramid class is testing one regime")

    def test_the_pyramid_was_actually_written(self):
        got = self.w["tiles"]
        self.assertGreater(got["tiles"], 0,
                           f"write_pyramid stored no tiles at all: {got}")
        self.assertTrue(self.w["shipped"],
                        "no tile came back out of the archive; every pixel check below "
                        "would pass on an empty dictionary")


# ===========================================================================
#  THE 2 m RULE
# ===========================================================================
class TheTwoMetreRule(BankTestCase):

    def test_a_bank_just_under_the_rule_is_amber(self):
        band = self.t["near_in"] & self.cols(80, 90)     # 1.9375 m, exact in float32
        bad = band & (self.cls_ != bank.BANK_CLASS_LOW)
        self.assertFalse(bad.any(),
                         f"1.9375 m above its own water is under the "
                         f"{settings.lidar_launch_max_height_m:g} m rule and must be "
                         f"AMBER; {int(bad.sum())} of {int(band.sum())} pixels were "
                         f"not, first at {self.first(band, bad)}")

    def test_a_bank_exactly_on_the_rule_is_brown_and_the_edge_is_declared(self):
        """`<` and `<=` are one character apart and make two different maps. The rule
        the console explains is "LESS THAN 2 m is amber", so exactly 2.00 is brown.
        The ladder either side of the edge passes under both readings; this is the
        check that does not."""
        band = self.t["near_in"] & self.cols(90, 100)    # exactly 2.0, exact in float32
        amber = band & (self.cls_ == bank.BANK_CLASS_LOW)
        self.assertFalse(
            amber.any(),
            f"a bank standing at EXACTLY {settings.lidar_launch_max_height_m:g} m above "
            f"its own water came back AMBER at {int(amber.sum())} of {int(band.sum())} "
            f"pixels ({self.first(band, amber)}). settings.lidar_launch_max_height_m is "
            f"the height amber stops at, and `height <= threshold` puts the edge on the "
            f"wrong side of itself.")
        bad = band & (self.cls_ != bank.BANK_CLASS_HIGH)
        self.assertFalse(bad.any(),
                         f"exactly on the rule must be BROWN; {int(bad.sum())} pixels "
                         f"were neither, at {self.first(band, bad)}")

    def test_a_bank_just_over_the_rule_is_brown(self):
        band = self.t["near_in"] & self.cols(100, 110)   # 2.0625 m, exact in float32
        bad = band & (self.cls_ != bank.BANK_CLASS_HIGH)
        self.assertFalse(bad.any(),
                         f"2.0625 m is over the rule and must be BROWN; "
                         f"{int(bad.sum())} of {int(band.sum())} pixels were not, "
                         f"first at {self.first(band, bad)}")

    def test_the_split_lands_on_the_rule_everywhere_and_not_only_at_the_edge(self):
        """The whole classified corridor at once, against the fixture's own truth. A
        layer that got the edge right and the middle wrong passes all three checks
        above and fails here."""
        import numpy as np
        got, want = self.cls_, self.w["want"]
        band = self.t["near_in"] | self.t["far_in"]
        bad = band & (got != want)
        if bad.any():
            rs, cs = np.nonzero(bad)
            sample = "; ".join(
                f"({int(r)},{int(c)}) z={float(self.w['z'][r, c]):.4f} "
                f"water={level_of(int(c)):.4f} want={int(want[r, c])} got={int(got[r, c])}"
                for r, c in list(zip(rs, cs))[:6])
            self.fail(f"{int(bad.sum())} of {int(band.sum())} classified pixels "
                      f"disagree with the rule applied to their own water: {sample}")

    def test_ground_outside_the_corridor_is_never_classified(self):
        """There is real, high, perfectly good ground out there. It is not beside this
        canal, so this layer has nothing to say about it, and painting it would turn
        'the bank is 4 m high' into a claim about a field."""
        out = self.t["outside"]
        bad = out & (self.cls_ != bank.BANK_CLASS_OUTSIDE)
        self.assertFalse(bad.any(),
                         f"{int(bad.sum())} of {int(out.sum())} pixels more than 36 m "
                         f"from the centreline were classified anyway (the corridor is "
                         f"{settings.lidar_water_buffer_m + settings.lidar_band_buffer_m:g} m), "
                         f"first at {self.first(out, bad)}")


# ===========================================================================
#  THE POUNDS
# ===========================================================================
class EveryPoundIsItsOwnDatum(BankTestCase):
    """The lock-step fixture, which is what the whole file is built round."""

    def test_two_separate_water_levels_are_detected_and_not_one_average(self):
        levels = sorted(round(float(r["level_m_od"]), 2)
                        for r in self.w["raster"].levels)
        self.assertEqual(
            len(levels), 2,
            f"the fixture holds two pounds, {LEVEL_A:.3f} m and {LEVEL_B:.3f} m OD, "
            f"with a lock between them; detect_pound_levels reported {levels}. One "
            f"level means the staircase was flattened. Three or more means something "
            f"that is not water was read as a pound — and the only other structure "
            f"here is a ROOF over the cut, which does not change the level of the "
            f"water either side of it.")
        self.assertAlmostEqual(levels[0], LEVEL_B, places=1, msg=f"levels {levels}")
        self.assertAlmostEqual(levels[1], LEVEL_A, places=1, msg=f"levels {levels}")

    def test_the_levels_are_labelled_on_their_own_sheets(self):
        pounds = self.w["raster"].pounds
        self.assertTrue(pounds, "no pound label was placed at all")
        for p in pounds:
            geom = (p.get("geometry") or {}).get("coordinates")
            self.assertTrue(geom, f"a pound label carries no position: {p}")

    def test_a_bank_is_measured_against_its_own_water_and_not_a_constant(self):
        """PROBE A must be amber and PROBE B must be brown. No single global datum can
        produce both — which is what makes this a test of the per-pound datum rather
        than of two thresholds that happen to be right."""
        A = self.t["near_in"] & self.cols(*PROBE_A)
        B = self.t["near_in"] & self.cols(*PROBE_B)
        note = _no_global_datum()
        badA = A & (self.cls_ != bank.BANK_CLASS_LOW)
        badB = B & (self.cls_ != bank.BANK_CLASS_HIGH)
        self.assertFalse(
            badA.any(),
            f"PROBE A stands at {PROBE_A_OD:.3f} m OD, {PROBE_A_OD - LEVEL_A:.3f} m "
            f"above pound A, and must be AMBER; {int(badA.sum())} of {int(A.sum())} "
            f"pixels were not, first at {self.first(A, badA)}. {note}")
        self.assertFalse(
            badB.any(),
            f"PROBE B stands at {PROBE_B_OD:.3f} m OD, {PROBE_B_OD - LEVEL_B:.3f} m "
            f"above pound B, and must be BROWN; {int(badB.sum())} of {int(B.sum())} "
            f"pixels were not, first at {self.first(B, badB)}. {note}")

    def test_the_higher_ground_is_the_amber_one_which_is_the_whole_point(self):
        """Stated on its own because it is the sentence that sounds wrong until you
        remember the staircase: PROBE A is 0.75 m higher above Ordnance Datum than
        PROBE B and is the safer of the two."""
        import numpy as np
        got = self.cls_
        ra, ca = np.nonzero(self.t["near_in"] & self.cols(*PROBE_A))
        rb, cb = np.nonzero(self.t["near_in"] & self.cols(*PROBE_B))
        a, b = int(got[ra[0], ca[0]]), int(got[rb[0], cb[0]])
        self.assertGreater(PROBE_A_OD, PROBE_B_OD, "the fixture itself is wrong")
        self.assertEqual(
            (a, b), (int(bank.BANK_CLASS_LOW), int(bank.BANK_CLASS_HIGH)),
            f"the higher ground ({PROBE_A_OD:.3f} m OD) came back class {a} and the "
            f"lower ({PROBE_B_OD:.3f} m OD) came back class {b}. Sorted by height "
            f"above Ordnance Datum that reads backwards, which is exactly right: "
            f"height above OD says nothing at all here. {_no_global_datum()}")


# ===========================================================================
#  THE WATER
# ===========================================================================
class WaterIsNeverPainted(BankTestCase):
    """LIDAR cannot see through water, so this layer knows nothing about the channel.
    Every one of these runs over the WHOLE region, on the real tiles."""

    def test_every_pixel_of_the_sheet_is_classed_as_water(self):
        water = self.t["water"]
        bad = water & (self.cls_ != bank.BANK_CLASS_WATER)
        self.assertFalse(bad.any(),
                         f"{int(bad.sum())} of {int(water.sum())} water pixels were "
                         f"classed as something else, first at {self.first(water, bad)}")

    def test_no_tile_paints_a_single_pixel_of_water(self):
        """On the PNGs the console draws, at the zoom where one tile pixel is finer
        than one grid pixel, over every water pixel there is. The whole region rather
        than a sample, because the failure worth catching is a one-pixel rim at the
        water's edge — invisible in a spot check, and exactly what dilating a bank
        mask over the channel leaves behind."""
        import numpy as np
        g, tiles = self.grid, self.w["shipped"]
        water = self.t["water"]
        rs, cs = np.nonzero(water)
        worst, where, n_bad = 0, None, 0
        for r, c in zip(rs.tolist(), cs.tolist()):
            a = alpha_at(tiles, g, r, c, UPSAMPLE_Z)
            if a:
                n_bad += 1
                if a > worst:
                    worst, where = a, (r, c)
        self.assertEqual(
            n_bad, 0,
            f"{n_bad} of {int(water.sum())} water pixels carry paint at z{UPSAMPLE_Z} "
            f"— worst alpha {worst}/255 at grid pixel {where}. Paint in the middle of "
            f"the cut is a claim about the channel, and nothing here has ever measured "
            f"the channel.")

    def test_a_hole_in_the_survey_in_the_middle_of_the_cut_is_not_a_bank(self):
        """Where the LIDAR simply missed the water — a specular dropout, which is what
        water usually gives a laser — the answer is still 'the middle of the cut', not
        'ground at 0 m above the water'."""
        wet = self.t["wet_hole"]
        painted = wet & ((self.cls_ == bank.BANK_CLASS_LOW) |
                         (self.cls_ == bank.BANK_CLASS_HIGH))
        self.assertFalse(painted.any(),
                         f"{int(painted.sum())} of {int(wet.sum())} unsurveyed pixels "
                         f"in the middle of the channel were classified as bank, first "
                         f"at {self.first(wet, painted)}")

    def test_nothing_outside_the_two_painted_classes_reaches_a_tile(self):
        """The upsampling branch is nearest-neighbour on the class raster, so every
        painted tile pixel must be a whole one: alpha is either nothing or the layer's
        one declared opacity. A partial alpha up there would mean something was blended
        at a zoom where nothing may be."""
        import numpy as np
        full = int(round(bank.BANK_PAINT_ALPHA * 255))
        seen = set()
        for (z, _x, _y), img in self.w["shipped"].items():
            if z != UPSAMPLE_Z:
                continue
            seen.update(int(v) for v in np.unique(img[:, :, 3]))
        self.assertTrue(seen, f"no z{UPSAMPLE_Z} tile in the archive")
        self.assertLessEqual(
            seen, {0, full},
            f"z{UPSAMPLE_Z} tiles carry alphas {sorted(seen)}; at that zoom one tile "
            f"pixel is finer than one grid pixel and the only honest answers are 0 and "
            f"{full} (BANK_PAINT_ALPHA {bank.BANK_PAINT_ALPHA})")


# ===========================================================================
#  THE CORRIDOR
# ===========================================================================
class TheCorridorComesFromTheVector(BankTestCase):
    """Buffered from the Trust's centreline, never grown out of the flat water — and
    the covered section is what tells the two apart."""

    def test_the_corridor_is_as_wide_as_it_says_and_no_wider(self):
        import numpy as np
        inside = self.t["dy"] <= 32
        got = self.cls_ != bank.BANK_CLASS_OUTSIDE
        miss = inside & ~got & ~self.t["nodata"]
        self.assertFalse(miss.any(),
                         f"{int(miss.sum())} surveyed pixels within 32 m of the "
                         f"centreline are outside the corridor, first at "
                         f"{self.first(inside, ~got & ~self.t['nodata'])}")

    def test_not_one_column_of_the_canal_loses_its_bank(self):
        """A corridor detected from the flat sheet has a ten-metre hole under the
        covered section. A corridor buffered from the vector does not know the roof
        is there."""
        import numpy as np
        painted = ((self.cls_ == bank.BANK_CLASS_LOW) |
                   (self.cls_ == bank.BANK_CLASS_HIGH))
        per_col = painted.sum(axis=0)
        thin = np.nonzero(per_col < 20)[0]
        self.assertEqual(
            thin.size, 0,
            f"{thin.size} of {W} columns carry fewer than 20 painted pixels — "
            f"narrowest is column {int(np.argmin(per_col))} with "
            f"{int(per_col.min())}. Columns {COVERED[0]}..{COVERED[1] - 1} are the "
            f"covered section; a hole there is a corridor grown out of the water "
            f"sheet rather "
            f"than buffered from the centreline. Thin columns: {thin[:16].tolist()}")

    def test_the_bank_beside_the_covered_section_is_drawn_on_the_actual_tiles(self):
        """Continuity of a mask is not the deliverable — continuity of the PICTURE is.
        An operator walking a covered stretch is precisely who needs to know what the bank
        does on either side of it."""
        import numpy as np
        g, tiles = self.grid, self.w["shipped"]
        painted = ((self.cls_ == bank.BANK_CLASS_LOW) |
                   (self.cls_ == bank.BANK_CLASS_HIGH))
        blank = []
        for c in range(COVERED[0], COVERED[1]):
            rows = np.nonzero(painted[:, c])[0]
            drawn = sum(1 for r in rows.tolist()
                        if alpha_at(tiles, g, int(r), c, UPSAMPLE_Z) > 0)
            if drawn == 0:
                blank.append((c, int(rows.size)))
        self.assertFalse(blank,
                         f"columns under the roof with classified bank and NO paint "
                         f"on any tile: {blank}. The map goes blank exactly where "
                         f"somebody is standing.")

    def test_the_water_under_the_roof_is_still_the_middle_of_the_cut(self):
        """The survey sees the deck, not the water. The channel does not stop being
        the channel because something was built over it, and the layer must not start
        painting the middle of the cut there."""
        mid = (self.t["dy"] <= 11) & self.t["deck"]
        bad = mid & ((self.cls_ == bank.BANK_CLASS_LOW) |
                     (self.cls_ == bank.BANK_CLASS_HIGH))
        self.assertFalse(bad.any(),
                         f"{int(bad.sum())} of {int(mid.sum())} pixels in the channel "
                         f"under the roof were painted as bank, first at "
                         f"{self.first(mid, bad)}")


# ===========================================================================
#  NODATA
# ===========================================================================
class NodataNeverReachesTheArithmetic(BankTestCase):
    """-9999 is a valid float. Nothing raises on it; everything downstream just
    quietly becomes wrong."""

    def test_the_decoder_masks_the_sentinel_at_the_door(self):
        """nav/lidar.py owns the decode, and it is the ONLY place the sentinel may be
        recognised — everything after it reads NaN and propagates the absence."""
        import numpy as np
        got = lidar.decode_dtm(geotiff_bytes(elevation()))
        self.assertTrue(got.get("ok"), f"the fixture GeoTIFF was refused: {got.get('why')}")
        grid = got["grid"]
        nod = self.t["nodata"]
        self.assertTrue(np.isnan(grid[nod]).all(),
                        f"{int((~np.isnan(grid[nod])).sum())} of {int(nod.sum())} "
                        f"nodata cells came through as numbers")
        self.assertFalse(np.isnan(grid[~nod]).any(),
                         f"{int(np.isnan(grid[~nod]).sum())} surveyed cells were masked "
                         f"out — masking too much is the same defect wearing the other "
                         f"sign, and it reads on the console as PARTIAL coverage")
        self.assertEqual(int(got["nodata_pixels"]), int(nod.sum()),
                         f"nodata_pixels {got['nodata_pixels']} against "
                         f"{int(nod.sum())} in the fixture")

    def test_the_sentinel_never_reaches_the_elevation_range(self):
        import numpy as np
        got = lidar.decode_dtm(geotiff_bytes(elevation()))
        real = elevation()[~self.t["nodata"]]
        self.assertAlmostEqual(
            float(got["elev_min"]), float(real.min()), places=2,
            msg=f"elev_min came back {got['elev_min']}; the lowest SURVEYED cell is "
                f"{float(real.min()):.3f} m, and anything near {NODATA} is the "
                f"sentinel arriving in the statistics")
        self.assertAlmostEqual(float(got["elev_max"]), float(real.max()), places=2,
                               msg=f"elev_max came back {got['elev_max']}")

    def test_the_sentinel_never_reaches_the_pound_histogram(self):
        """detect_pound_levels histograms the flat near-centreline elevations. One
        -9999 in that set puts every real height into a single bin at the top of a
        ten-kilometre range, and every level taken off it is then noise."""
        levels = [float(r["level_m_od"]) for r in self.w["raster"].levels]
        self.assertTrue(levels, "no water level was detected at all")
        self.assertTrue(
            all(l > 0.0 for l in levels),
            f"a detected water level is at or below zero: {levels}. There is a nodata "
            f"hole in the middle of this fixture's channel and its value is {NODATA}.")

    def test_the_hole_is_not_classified_and_not_painted(self):
        import numpy as np
        g, tiles = self.grid, self.w["shipped"]
        hole = self.t["hole"]
        bad = hole & (self.cls_ != bank.BANK_CLASS_OUTSIDE)
        self.assertFalse(bad.any(),
                         f"{int(bad.sum())} of {int(hole.sum())} unsurveyed cells were "
                         f"classified, first at {self.first(hole, bad)}")
        rs, cs = np.nonzero(hole)
        lit = [(int(r), int(c)) for r, c in zip(rs.tolist(), cs.tolist())
               if alpha_at(tiles, g, int(r), int(c), UPSAMPLE_Z) > 0]
        self.assertFalse(lit, f"{len(lit)} unsurveyed cells carry paint on a tile: "
                              f"{lit[:8]}")

    def test_the_hillshade_is_finite_everywhere_it_is_defined(self):
        import numpy as np
        hs = np.asarray(self.w["raster"].shade, dtype=np.float64)
        valid = np.asarray(self.grid.valid, dtype=bool)
        bad = valid & ~np.isfinite(hs)
        self.assertFalse(bad.any(),
                         f"{int(bad.sum())} surveyed cells have a non-finite hillshade, "
                         f"first at {self.first(valid, ~np.isfinite(hs))}")

    def test_the_hole_casts_no_wall_around_itself(self):
        """THE CHECK THIS CLASS EXISTS FOR, and the one that is easiest to get wrong
        while believing it is handled.

        Masking the OUTPUT is not masking the INPUT. np.gradient over an array whose
        holes have been filled with a constant computes, at every SURVEYED cell touching
        a hole, a slope of thirty metres in one pixel — and those cells are valid, so no
        `where(valid, ...)` afterwards can rescue them. The hillshade saturates and the
        console draws a hard rim around every hole in the Environment Agency's coverage,
        which an operator reads as a retaining wall.

        The hole here is cut into a deliberately FLAT plateau — near bench and far bench
        at the same height for thirty-six columns — so every cell touching it is on
        ground with no slope at all and there is exactly one right answer."""
        import numpy as np
        hs = np.asarray(self.w["raster"].shade, dtype=np.float64)
        valid = np.asarray(self.grid.valid, dtype=bool)

        r0, r1 = HOLE_ROWS
        c0, c1 = HOLE_COLS
        ring = np.zeros((H, W), dtype=bool)
        ring[r0 - 1:r1 + 1, c0 - 1:c1 + 1] = True
        ring[r0:r1, c0:c1] = False                     # the hole itself
        ring &= valid

        ref = np.zeros((H, W), dtype=bool)             # flat plateau, well clear of it
        ref[r0:r1, PLATEAU[0] + 3:PLATEAU[0] + 8] = True

        spread = float(hs[valid].max() - hs[valid].min())
        self.assertGreater(spread, 0.05,
                           "the hillshade is flat everywhere — there is real relief in "
                           "this fixture and none of it arrived")
        base = float(hs[ref].mean())
        dev = np.abs(hs - base)
        worst = float(dev[ring].max())
        tol = 0.02 * spread
        rs, cs = np.nonzero(ring & (dev > tol))
        self.assertLessEqual(
            worst, tol,
            f"the ring of SURVEYED cells around the nodata hole differs from the flat "
            f"plateau it is cut into by up to {worst:.4g}, against {tol:.4g} (2% of the "
            f"hillshade's own full spread, {spread:.4g}). Flat plateau reads "
            f"{base:.4g}; {int((ring & (dev > tol)).sum())} of {int(ring.sum())} ring "
            f"cells are outside, first at "
            f"{[(int(r), int(c)) for r, c in zip(rs[:6], cs[:6])]}. Every one of those "
            f"cells is on ground with no slope. A rim there is the {NODATA} hole "
            f"reaching np.gradient through whatever the array was filled with.")


# ===========================================================================
#  THE PYRAMID
# ===========================================================================
class DownsamplingInventsNothing(BankTestCase):
    """Zooming out by averaging RGBA manufactures amber out of brown-beside-transparent,
    because half-covered brown is a lighter brown — a launch bank invented by a filter
    kernel. The fixture's second pound has no amber in it anywhere, so any amber over it
    at any zoom came from the arithmetic and not from the survey.

    These run against the PLAIN render — the same classification with the relief and
    contours turned off — because with the hillshade on, brown at 1.6x and amber at
    0.3x overlap in colour and "is this pixel amber?" stops having an answer. The
    hillshade is applied AFTER the class is chosen, so nothing about the choice is
    hidden by turning it off, and TheFixtureIsWhatItClaims asserts that the plain
    render classifies identically."""

    def setUp(self):
        super().setUp()
        self.amber = bank._hex_rgb(settings.lidar_colour_low)
        self.brown = bank._hex_rgb(settings.lidar_colour_high)

    def test_the_two_colours_can_be_told_apart_by_somebody_in_sunlight(self):
        d = math.dist(self.amber, self.brown)
        self.assertGreater(
            d, 60.0,
            f"amber {self.amber} and brown {self.brown} are {d:.1f} apart in RGB. They "
            f"carry the only distinction this layer makes, on a handheld held in "
            f"sunlight, over satellite imagery.")

    def test_the_plain_render_classifies_exactly_as_the_shipped_one_does(self):
        import numpy as np
        a, b = self.w["raster"].classes, self.w["plain"].classes
        self.assertTrue(
            np.array_equal(a, b),
            f"{int((a != b).sum())} pixels are classified differently with the relief "
            f"turned off. The hillshade is a multiplier applied after the class is "
            f"chosen; if it can change the class, the whole colour argument below is "
            f"testing a different map from the one that ships.")

    def test_every_painted_pixel_is_exactly_one_of_the_two_colours(self):
        """No blend, at any zoom. A pixel of some third colour is two classes averaged,
        and a colour between amber and brown means neither of the two things this layer
        says."""
        import numpy as np
        bad = {}
        for (z, x, y), img in self.w["flat"].items():
            lit = img[:, :, 3] > 0
            if not lit.any():
                continue
            rgb = img[:, :, :3][lit]
            uniq = {tuple(int(v) for v in row) for row in np.unique(rgb, axis=0)}
            stray = uniq - {self.amber, self.brown}
            if stray:
                bad.setdefault(z, set()).update(list(stray)[:4])
        self.assertFalse(
            bad,
            f"colours that are neither amber {self.amber} nor brown {self.brown} "
            f"reached a tile: {dict(sorted(bad.items()))}. Every one of them is a "
            f"class that does not exist.")

    def _amber_pixels(self, z):
        """Every (x, y, py, px) painted the amber colour on a level-z tile."""
        import numpy as np
        want = np.array(self.amber, dtype=np.uint8)
        out = []
        for (tz, x, y), img in self.w["flat"].items():
            if tz != z:
                continue
            lit = img[:, :, 3] > 0
            is_amber = lit & np.all(img[:, :, :3] == want[None, None, :], axis=2)
            out += [(x, y, int(py), int(px)) for py, px in zip(*np.nonzero(is_amber))]
        return out

    def test_no_zoomed_out_pixel_is_amber_unless_amber_exists_underneath_it(self):
        """THE RULE, over every tile pixel of every DOWNSAMPLING level: amber on the
        screen requires amber in the survey under it. Averaging RGBA is what breaks it —
        half-covered brown is a lighter brown, and a lighter brown is amber."""
        import numpy as np
        g = self.grid
        _up, down = zoom_split(g)
        self.assertTrue(down, "no zoom in the pyramid actually downsamples the grid")
        rs, cs = np.nonzero(self.w["plain"].classes == bank.BANK_CLASS_LOW)
        for z in down:
            has_amber = {tile_pixel_of(g, r, c, z)[1:]
                         for r, c in zip(rs.tolist(), cs.tolist())}
            bad = [p for p in self._amber_pixels(z) if p not in has_amber]
            self.assertFalse(
                bad,
                f"z{z}: {len(bad)} tile pixel(s) came back AMBER with no amber-classed "
                f"grid pixel underneath them. First few (x,y,row,col): {bad[:8]}. An "
                f"aggregate that invents a class invents the one sentence this layer "
                f"is for.")

    def test_no_zoomed_in_pixel_is_amber_unless_the_grid_pixel_it_sits_on_is(self):
        """The other regime, and the other direction. Zoomed in, a tile pixel takes the
        class of the grid pixel it lands on; anything else is an interpolation between
        class codes, and there is no such thing as class 2.5."""
        g = self.grid
        up, _down = zoom_split(g)
        self.assertTrue(up, "no zoom in the pyramid actually upsamples the grid")
        cls = self.w["plain"].classes
        for z in up:
            bad = []
            for x, y, py, px in self._amber_pixels(z):
                r, c = grid_pixel_of(g, z, x, y, py, px)
                if not (0 <= r < H and 0 <= c < W) or \
                        cls[r, c] != bank.BANK_CLASS_LOW:
                    bad.append((x, y, py, px, r, c,
                                int(cls[r, c]) if 0 <= r < H and 0 <= c < W else "off"))
            self.assertFalse(
                bad,
                f"z{z}: {len(bad)} tile pixel(s) are amber over a grid pixel that is "
                f"not. First few (x,y,tile row,tile col,grid row,grid col,class): "
                f"{bad[:8]}")

    def test_the_second_pound_is_never_amber_at_any_zoom(self):
        """THE CONSTRUCTED CASE, stated separately from the general rule so that a
        failure says which half of the fixture produced it. Brown bank runs straight
        into transparent water for the whole of pound B; averaging the two is what
        makes a lighter brown that reads as amber."""
        g = self.grid
        # The WEST edge of the tile pixel against the WEST edge of the lock column, so
        # that only tile pixels lying WHOLLY east of the lock are counted. A tile pixel
        # straddling the lock legitimately has amber under half of it.
        lock_lon = g.west + LOCK_COL * g.px_lon
        bad = []
        for z in range(ZMIN, ZMAX + 1):
            n = float(1 << (z + 8))
            for x, y, py, px in self._amber_pixels(z):
                lon = ((x * 256 + px) / n) * 360.0 - 180.0
                if lon >= lock_lon:
                    bad.append((z, x, y, py, px, round(lon, 7)))
        self.assertFalse(
            bad,
            f"{len(bad)} tile pixel(s) lying wholly east of the lock came back amber; "
            f"the first few are {bad[:6]}. There is no amber anywhere in that pound at "
            f"native resolution — brown bank runs straight into transparent water, and "
            f"averaging the two is exactly what makes a lighter brown that reads amber.")

    def test_a_zoomed_out_pixel_with_nothing_painted_under_it_stays_transparent(self):
        """Alpha is coverage when zoomed out — a tile pixel that is half water comes
        out half transparent, which is honest. Zero painted grid pixels under it means
        zero alpha, which is the part that has to be exact."""
        import numpy as np
        g = self.grid
        _up, down = zoom_split(g)
        painted = ((self.w["plain"].classes == bank.BANK_CLASS_LOW) |
                   (self.w["plain"].classes == bank.BANK_CLASS_HIGH))
        rs, cs = np.nonzero(painted)
        for z in down:
            covered = {tile_pixel_of(g, r, c, z)[1:]
                       for r, c in zip(rs.tolist(), cs.tolist())}
            bad = []
            for (tz, x, y), img in self.w["flat"].items():
                if tz != z:
                    continue
                for py, px in zip(*np.nonzero(img[:, :, 3] > 0)):
                    if (x, y, int(py), int(px)) not in covered:
                        bad.append((x, y, int(py), int(px), int(img[py, px, 3])))
            self.assertFalse(
                bad,
                f"z{z}: {len(bad)} tile pixel(s) are painted with no painted grid pixel "
                f"under them at all: {bad[:8]}")


# ===========================================================================
#  PROVENANCE
# ===========================================================================
class Provenance(BankTestCase):
    """Where this came from and, above all, WHEN. A bank moves: piling is driven,
    edges collapse, wharves are filled in. A 2022 survey shown without its date is a
    claim about today."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.fail_reason or NEED or bank is None:
            return
        w = world()
        cls.tmp = Path(tempfile.mkdtemp(prefix="neptune-bank-prov-"))
        real_dir, real_grid = lidar.area_lidar_dir, bank.load_grid
        lidar.area_lidar_dir = lambda name: cls.tmp / f"{name}.lidar"
        bank.load_grid = lambda name: w["grid"]
        try:
            # THE REAL DOOR IN. render_area is what bootstrap and the CLI call; a check
            # that assembled the provenance dict itself would test a dict.
            cls.prov = bank.render_area("prov-cut", zmin=ZMIN, zmax=ZMIN + 1)
            cls.card = bank.card("prov-cut")
            cls.pounds = bank.pound_labels("prov-cut")
        finally:
            lidar.area_lidar_dir, bank.load_grid = real_dir, real_grid

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)
        super().tearDownClass()

    def test_the_survey_vintage_is_recorded_as_2022(self):
        v = str(((self.prov.get("source") or {}).get("survey_vintage")) or "")
        self.assertEqual(v, "2022",
                         f"the provenance records survey_vintage {v!r}; the terrain "
                         f"model behind this layer is the 2022 one and the console "
                         f"quotes it to the operator")
        self.assertEqual(str(settings.lidar_survey_vintage), "2022",
                         f"settings.lidar_survey_vintage is "
                         f"{settings.lidar_survey_vintage!r}")

    def test_the_vintage_is_a_fact_about_the_survey_and_not_a_clock(self):
        """`time.gmtime().tm_year` is the wrong kind of right: it agrees with the
        survey in 2022 and lies every year after, which is every year this vehicle
        will fly."""
        now = time.gmtime().tm_year
        self.assertNotEqual(now, 2022,
                            "this check can only bite outside 2022; if the bench clock "
                            "really says 2022, read the vintage by hand")
        blob = json.dumps(self.prov, default=str)
        self.assertIn("2022", blob,
                      f"nothing in the provenance record mentions 2022 at all")
        self.assertIn("2022", str(self.prov.get("vintage_warning") or ""),
                      f"the vintage warning does not name the year: "
                      f"{self.prov.get('vintage_warning')!r}")

    def test_the_record_says_in_words_that_the_bank_may_have_moved_since(self):
        s = str(self.prov.get("vintage_warning") or "")
        self.assertGreater(len(s), 60,
                           f"vintage_warning is {s!r} — a date with no sentence beside "
                           f"it is a number nobody reads as a warning")

    def test_the_record_says_what_amber_does_not_mean(self):
        """The one sentence that stops this layer being read as permission. It is a
        geometric fact about height and it knows nothing about fences, gates, private
        land or whether anything can be carried to it."""
        s = str(self.prov.get("what_amber_means") or "").lower()
        self.assertTrue(s, "the provenance carries no explanation of amber at all")
        self.assertIn("not", s, f"what_amber_means never denies anything: {s[:200]!r}")
        for word in ("launch",):
            self.assertIn(word, s, f"what_amber_means does not mention {word!r}")

    def test_the_record_says_that_unpainted_ground_was_not_looked_at(self):
        s = str(self.prov.get("what_is_missing") or "").lower()
        self.assertTrue(s, "the provenance does not say what is NOT in this layer")
        self.assertIn("centreline", s,
                      f"the corridor is buffered from the Trust's centreline and the "
                      f"record has to say so, because everything off it is unpainted "
                      f"and unpainted is not a survey result: {s[:200]!r}")

    def test_the_card_reads_back_what_was_written(self):
        self.assertEqual(self.card.get("state"), self.prov.get("state"),
                         f"card() says {self.card.get('state')!r}, the record says "
                         f"{self.prov.get('state')!r}")
        self.assertTrue(self.card.get("painted"),
                        f"tiles were written and card() reports painted="
                        f"{self.card.get('painted')!r}")

    def test_the_pound_labels_carry_the_vintage_too(self):
        self.assertIsNotNone(self.pounds, "no pound label file was written")
        self.assertEqual(str(self.pounds.get("survey_vintage")), "2022",
                         f"the labels record vintage "
                         f"{self.pounds.get('survey_vintage')!r}")

    def test_an_area_with_no_layer_reports_absent_and_says_why(self):
        """ABSENT and EMPTY are opposite claims. Bare satellite over a canal looks
        exactly like 'there are no low banks here'."""
        real = lidar.area_lidar_dir
        lidar.area_lidar_dir = lambda name: self.tmp / f"{name}.lidar"
        try:
            got = bank.card("never-built")
        finally:
            lidar.area_lidar_dir = real
        self.assertEqual(got.get("state"), "absent", f"card() returned {got!r}")
        why = str(got.get("why") or "").lower()
        self.assertTrue(len(why) > 40 and ("not" in why),
                        f"the absent card gives no sentence an operator can act on: "
                        f"{got.get('why')!r}")


# ===========================================================================
#  A BROKEN CARD IS NOT A SURVEY RESULT
# ===========================================================================
class ABrokenArchiveDoesNotClaimPaint(BankTestCase):
    """The record beside the tiles says what the build MEANT to write. Only the archive
    says what will reach the glass, and the two come apart in ordinary ways: a build
    killed partway, a card pulled out of the handheld mid-write, a copy onto a disk that
    filled. All three leave a file of the right name that opens and serves nothing.

    card() used to check only that the file EXISTED, so all three reported PRESENT — "the
    LIDAR covers this whole corridor and every pixel of it has been classified" — over an
    archive serving nothing. That is worse than a missing file, because a missing file at
    least reads as missing: the operator gets bare satellite under a row stating the banks
    are painted, which is the precise confusion this layer exists to prevent.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.fail_reason or NEED or bank is None:
            return
        w = world()
        cls.tmp = Path(tempfile.mkdtemp(prefix="neptune-bank-broken-"))
        real_dir, real_grid = lidar.area_lidar_dir, bank.load_grid
        real_classify = bank.classify
        lidar.area_lidar_dir = lambda name: cls.tmp / f"{name}.lidar"
        bank.load_grid = lambda name: w["grid"]
        # render_area() calls classify(grid) with NO centreline, so left alone it would
        # go and fetch one — and this file blocks the network. It would not raise; it
        # would classify an empty corridor, write a pyramid of nothing, and hand back a
        # perfectly well-formed ABSENT record. A class about broken archives would then
        # be measuring a broken fixture, and every mutation below would "pass" because
        # the card was never on PRESENT to begin with. So it is handed the fixture's own
        # classified raster — the same one every other check in this file measures.
        bank.classify = lambda grid, lines=None: w["raster"]
        try:
            cls.prov = bank.render_area("broken-cut", zmin=ZMIN, zmax=ZMIN + 1)
            cls.tiles = bank.tiles_path("broken-cut")
            cls.control = bank.card("broken-cut")
        finally:
            lidar.area_lidar_dir, bank.load_grid = real_dir, real_grid
            bank.classify = real_classify
        cls.gold = cls.tiles.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)
        super().tearDownClass()

    def _card_after(self, mutate):
        """Break the archive this one way, ask card(), then put it back.

        Every mutation starts from the intact bytes and the intact bytes are restored
        afterwards, so the checks below cannot contaminate each other — a half-deleted
        archive that stayed half-deleted would make the emptied case pass for the wrong
        reason.
        """
        self.tiles.write_bytes(self.gold)
        mutate(self.tiles)
        real = lidar.area_lidar_dir
        lidar.area_lidar_dir = lambda name: self.tmp / f"{name}.lidar"
        try:
            return bank.card("broken-cut")
        finally:
            lidar.area_lidar_dir = real
            self.tiles.write_bytes(self.gold)

    @staticmethod
    def _count(path) -> int:
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT count(*) FROM tiles").fetchone()[0])
        finally:
            con.close()

    def test_the_control_is_an_archive_holding_every_tile_its_record_claims(self):
        """FIRST, THE FIXTURE. Mutations that move a card off a healthy state prove
        nothing if the unmutated card was never in one: they would pass just as well
        against a build that had failed outright.

        The fixture reports PARTIAL rather than PRESENT and that is correct — it is
        built with nodata holes and a covered section, so the LIDAR genuinely does not
        reach all of its corridor. That is partial-by-COVERAGE, which is a fact about
        the survey. The checks below are about partial-by-LOST-TILES, which is a fact
        about this card. The two must not be confused, so what is asserted here is the
        thing that actually has to hold: every tile the record claims is in the archive.
        """
        n = self._count(self.tiles)
        self.assertGreater(n, 0, "the fixture archive holds no tiles, so nothing below "
                                 "can distinguish a broken card from this one")
        self.assertIn(self.control.get("state"), ("present", "partial"),
                      f"the intact fixture card reports "
                      f"{self.control.get('state')!r} — it is already in a state the "
                      f"mutations below are supposed to produce")
        self.assertTrue(self.control.get("painted"),
                        "the intact fixture card reports painted=False")
        self.assertEqual(self.control.get("tiles_held"), n,
                         f"card() counts {self.control.get('tiles_held')!r} tiles in an "
                         f"archive holding {n}")
        self.assertEqual(self.control.get("tiles_held"),
                         self.control.get("tiles_claimed"),
                         f"the INTACT fixture is already short of its own record "
                         f"({self.control.get('tiles_held')!r} held, "
                         f"{self.control.get('tiles_claimed')!r} claimed), so a check "
                         f"that a short archive gets reported cannot bite here")

    def test_an_emptied_archive_is_not_reported_as_present(self):
        """The defect as found: the record claims every tile, the archive serves none."""
        def empty(path):
            import sqlite3
            con = sqlite3.connect(path)
            con.execute("DELETE FROM tiles")
            con.commit()
            con.close()

        got = self._card_after(empty)
        self.assertEqual(got.get("state"), "unreadable",
                         f"an archive holding ZERO tiles reports "
                         f"{got.get('state')!r}. The operator is shown bare satellite "
                         f"under a row claiming the banks are painted: "
                         f"{str(got.get('why'))[:200]!r}")
        self.assertFalse(got.get("painted"),
                         "an archive holding zero tiles still reports painted=True")
        self.assertEqual(got.get("tiles_held"), 0,
                         f"card() reports tiles_held={got.get('tiles_held')!r} for an "
                         f"emptied archive")
        why = str(got.get("why") or "").lower()
        self.assertNotIn("every pixel", why,
                         f"the broken card still carries the whole-corridor sentence: "
                         f"{got.get('why')!r}")
        self.assertTrue(len(why) > 60,
                        f"the broken card gives no sentence to act on: {why!r}")

    def test_a_short_archive_says_how_short(self):
        """Half the tiles is not none of them and not all of them. Reported as PRESENT
        it hides a hole; reported as ABSENT it throws away paint that is really there."""
        keep = max(1, self._count(self.tiles) // 2)

        def truncate(path):
            import sqlite3
            con = sqlite3.connect(path)
            con.execute("DELETE FROM tiles WHERE rowid > ?", (keep,))
            con.commit()
            con.close()

        got = self._card_after(truncate)
        claimed = self.control.get("tiles_claimed")
        self.assertEqual(got.get("state"), "partial",
                         f"an archive holding {keep} of {claimed} tiles reports "
                         f"{got.get('state')!r}")
        self.assertEqual(got.get("tiles_held"), keep,
                         f"card() reports tiles_held={got.get('tiles_held')!r} for an "
                         f"archive holding {keep}")
        # NOT just "the state moved": this fixture is ALREADY partial on coverage
        # grounds, so a state check alone would pass without card() having noticed the
        # missing tiles at all. The sentence has to carry both numbers, and it has to be
        # a different sentence from the one the intact card gives.
        why = str(got.get("why") or "")
        self.assertIn(str(keep), why,
                      f"the partial card does not say how many tiles survive, so there "
                      f"is no telling a nearly-whole card from an almost-empty one: "
                      f"{why!r}")
        self.assertIn(str(claimed), why,
                      f"the partial card does not say how many tiles were expected, so "
                      f"{keep} is a number with nothing to compare it against: {why!r}")
        self.assertNotEqual(why, str(self.control.get("why") or ""),
                            "a short archive gives the same sentence as a whole one, so "
                            "the lost tiles are not being reported at all")

    def test_an_archive_that_is_not_a_database_is_not_reported_as_present(self):
        """The file is the right name and the right place and is not a database at all.
        card() has to answer this WITHOUT numpy, because it is the call the console makes
        to find out why the layer is missing."""
        got = self._card_after(
            lambda path: path.write_bytes(b"not a database, just bytes of the right name"))
        self.assertEqual(got.get("state"), "unreadable",
                         f"an archive that will not open reports "
                         f"{got.get('state')!r}: {str(got.get('why'))[:200]!r}")
        self.assertFalse(got.get("painted"),
                         "an unopenable archive still reports painted=True")
        self.assertIsNone(got.get("tiles_held"),
                          f"an archive that will not answer reports "
                          f"tiles_held={got.get('tiles_held')!r}. None and 0 are "
                          f"different claims: one is a broken file, the other is a file "
                          f"that answered honestly that it holds nothing")

    def test_the_intact_archive_survives_the_mutations(self):
        """The class restores the archive between checks; if it did not, whichever
        check ran last would decide the state of every check after it."""
        real = lidar.area_lidar_dir
        lidar.area_lidar_dir = lambda name: self.tmp / f"{name}.lidar"
        try:
            got = bank.card("broken-cut")
        finally:
            lidar.area_lidar_dir = real
        self.assertEqual(got.get("state"), self.control.get("state"),
                         f"after mutation the intact archive reports "
                         f"{got.get('state')!r}, was {self.control.get('state')!r}")
        self.assertEqual(self.tiles.read_bytes(), self.gold,
                         "the archive bytes were not restored")


# ===========================================================================
#  THE DEPENDENCY RULE
# ===========================================================================
class AMissingLibraryIsNotADeadConsole(unittest.TestCase):
    """numpy, scipy and Pillow are OPTIONAL and they live on the handheld. The Pi 3B+
    never carries them and a bench that has not run `bootstrap.py --dev` has not got
    them either — so on both of those machines the api must still start, and the layer
    must say what is missing and how to get it.

    THIS DOES NOT TRUST THAT THE IMPORTS ARE LAZY. `from nav import bank` at the top of
    this file already succeeded in an interpreter that HAS numpy, which proves exactly
    nothing about one that has not. Each check starts a CHILD interpreter with one
    library made genuinely un-importable — a sys.meta_path finder that refuses it before
    any real finder gets a look — and asks the child what happened. A module-scope
    `import numpy` kills the child at its import line and the failure names the library
    that did it.

    Nothing here touches the fixture, because the machine this is really about has none
    of the libraries needed to build one."""

    CHILD = textwrap.dedent(r'''
        import json, socket, sys

        BLOCK = sys.argv[1]

        class Refuse:
            def find_spec(self, name, path=None, target=None):
                if name == BLOCK or name.startswith(BLOCK + "."):
                    raise ModuleNotFoundError("No module named %r" % name, name=name)
                return None

        for name in list(sys.modules):
            if name == BLOCK or name.startswith(BLOCK + "."):
                del sys.modules[name]
        sys.meta_path.insert(0, Refuse())

        # On connect(), not on the class: replacing socket.socket outright breaks the
        # import of anything that subclasses it, and this child is here to find out
        # whether an IMPORT survives.
        def _blocked(*a, **k):
            raise AssertionError("the bank layer reached the network")
        socket.socket.connect = _blocked
        socket.create_connection = _blocked

        out = {"block": BLOCK}
        try:
            from nav import bank, lidar
        except BaseException as exc:
            out["import"] = "%s: %s" % (type(exc).__name__, exc)
            print(json.dumps(out)); raise SystemExit(0)
        out["import"] = "ok"

        for half, mod in (("bank", bank), ("lidar", lidar)):
            try:
                st = mod.library_state()
                out[half] = {"ok": bool(st.get("ok")),
                             "missing": [str(x) for x in (st.get("missing") or [])],
                             "why": str(st.get("why") or ""),
                             "install": str(st.get("install") or "")}
            except BaseException as exc:
                out[half + "_raised"] = "%s: %s" % (type(exc).__name__, exc)

        # card() is the SERVING call: the console asks it to find out why the layer is
        # missing, so it is the one call that must answer on the machine that is
        # missing everything.
        try:
            c = bank.card("no-such-area")
            out["card_state"] = str(c.get("state"))
            out["card_why"] = str(c.get("why") or "")
        except BaseException as exc:
            out["card_raised"] = "%s: %s" % (type(exc).__name__, exc)

        try:
            bank.classify(object())
            out["classify"] = "returned"
        except BaseException as exc:
            out["classify"] = type(exc).__name__
            out["classify_msg"] = str(exc)
            out["classify_is_bank_unavailable"] = isinstance(
                exc, getattr(bank, "BankUnavailable", ()))
        print(json.dumps(out))
    ''')

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="neptune-bank-dep-"))
        cls.script = cls.tmp / "child.py"
        cls.script.write_text(cls.CHILD, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)

    def run_without(self, module: str, script=None) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(API) + os.pathsep + env.get("PYTHONPATH", "")
        p = subprocess.run([sys.executable, str(script or self.script), module],
                           cwd=str(API), env=env, capture_output=True, text=True,
                           timeout=180)
        lines = (p.stdout or "").strip().splitlines()
        try:
            return json.loads(lines[-1])
        except (ValueError, IndexError):
            self.fail(f"the child interpreter with {module} blocked printed nothing "
                      f"readable.\nexit {p.returncode}\nstdout: {p.stdout[-2000:]}\n"
                      f"stderr: {p.stderr[-2000:]}")

    def test_the_blocker_really_blocks(self):
        """A control. If sys.meta_path can be got round, every check in this class is
        green because it tested an interpreter that had numpy after all."""
        probe = self.tmp / "probe.py"
        probe.write_text(
            self.CHILD.replace("from nav import bank, lidar", "import numpy as bank"),
            encoding="utf-8")
        got = self.run_without("numpy", probe)
        self.assertNotEqual(
            got.get("import"), "ok",
            "numpy imported in a child where it was supposed to be blocked — the "
            "simulation is not simulating anything")

    def _check(self, imp: str, pips: tuple) -> None:
        got = self.run_without(imp)
        self.assertEqual(
            got.get("import"), "ok",
            f"`from nav import bank, lidar` RAISED in an interpreter with {imp} absent: "
            f"{got.get('import')}. That is a missing optional library taking the console "
            f"down — the same failure as a module that dies because a sensor is "
            f"unplugged. Import numpy/scipy/Pillow inside the functions that need them, "
            f"where the absence can be caught and reported.")
        self.assertNotIn("bank_raised", got,
                         f"bank.library_state() raised with {imp} absent: "
                         f"{got.get('bank_raised')} — it is the one call whose entire "
                         f"job is to survive this")
        st = got.get("bank") or {}
        self.assertIs(st.get("ok"), False,
                      f"with {imp} absent, bank.library_state() reports ok="
                      f"{st.get('ok')!r}")

        named = " ".join(st.get("missing") or []).lower()
        self.assertTrue(
            any(p in named for p in pips),
            f"library_state()['missing'] is {st.get('missing')!r} with {imp} absent; it "
            f"has to name the package — one of {list(pips)} — because 'a library is "
            f"missing' sends nobody anywhere")

        why = (st.get("why") or "")
        self.assertGreater(
            len(why), 40,
            f"'why' is {why!r} — the rule is a SENTENCE naming exactly what is absent, "
            f"in the same voice every other absence on this console is reported in")
        self.assertTrue(
            any(p in why.lower() for p in pips),
            f"the sentence does not name what is absent: {why!r}")
        install = (st.get("install") or "") + " " + why
        self.assertIn(
            "pip install", install.lower(),
            f"nothing in the report says how to fix it: install={st.get('install')!r}. "
            f"'Unavailable' with no remedy is a dead end on a handheld at a canal.")
        self.assertTrue(
            any(p in install.lower() for p in pips),
            f"the install line does not name the package to install: "
            f"{st.get('install')!r}"
            + ("  (PIL installs as `Pillow`; `pip install PIL` fetches a package that "
               "has not existed since 2011)" if imp == "PIL" else ""))

    def test_the_layer_reports_itself_unavailable_without_numpy(self):
        self._check("numpy", ("numpy",))

    def test_the_layer_reports_itself_unavailable_without_scipy(self):
        self._check("scipy", ("scipy",))

    def test_the_layer_reports_itself_unavailable_without_pillow(self):
        self._check("PIL", ("pillow", "pil"))

    def test_the_console_can_still_ask_what_this_card_holds(self):
        """card() is the SERVING call. On a handheld with the tiles already built and
        no numpy, "what have I got" still has to answer — and on one with nothing at
        all it has to be the thing that says why."""
        for imp in ("numpy", "scipy", "PIL"):
            got = self.run_without(imp)
            self.assertNotIn("card_raised", got,
                             f"bank.card() raised with {imp} absent: "
                             f"{got.get('card_raised')}. The console calls it to find "
                             f"out WHY the layer is missing; a status call that needs "
                             f"the missing library is silent in exactly the case it "
                             f"exists for.")
            self.assertEqual(got.get("card_state"), "absent",
                             f"with {imp} absent, card() for an area that was never "
                             f"built says {got.get('card_state')!r}")

    def test_classifying_without_a_library_raises_something_the_api_can_catch(self):
        """An endpoint has to turn this into a sentence for the panel. A bare
        ModuleNotFoundError out of the middle of a pipeline gets caught by an
        `except Exception` somewhere and reported as "server error", which tells the
        operator nothing about a pip command."""
        for imp in ("numpy", "scipy", "PIL"):
            got = self.run_without(imp)
            self.assertNotEqual(
                got.get("classify"), "returned",
                f"classify() returned a raster with {imp} absent — whatever it built, "
                f"it did not build it with {imp}")
            self.assertTrue(
                got.get("classify_is_bank_unavailable"),
                f"with {imp} absent, classify() raised {got.get('classify')} "
                f"({got.get('classify_msg')!r}) rather than bank.BankUnavailable. The "
                f"api needs one named exception it can turn into the panel's sentence.")
            self.assertTrue(
                any(p in (got.get("classify_msg") or "").lower()
                    for p in ({"PIL": ("pillow", "pil")}.get(imp, (imp.lower(),)))),
                f"the exception message does not name {imp}: "
                f"{got.get('classify_msg')!r}")

    def test_with_everything_present_both_halves_say_so(self):
        if NEED or bank is None:
            self.skipTest(NEED or IMPORT_ERROR)
        for half, mod in (("bank", bank), ("lidar", lidar)):
            st = mod.library_state()
            self.assertIs(st.get("ok"), True,
                          f"every library is installed in this interpreter and "
                          f"{half}.library_state() still says {st!r}")
            self.assertFalse(list(st.get("missing") or []),
                             f"nothing is missing, but {half} lists "
                             f"{st.get('missing')!r}")


def tearDownModule():
    w = _WORLD
    if w:
        shutil.rmtree(w["tmp"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

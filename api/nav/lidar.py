"""LIDAR launch-bank layer: THE ACQUISITION AND THE DECODE.

WHAT THIS HALF DOES. Fetches the Environment Agency's 1 m composite DTM for an offline
area, decodes it, masks it, and leaves ONE float32 elevation grid on the card with a
provenance record beside it. It does not classify, hillshade, contour or tile — the
render half reads what this writes and does all of that offline afterwards.

WHERE IT RUNS. On the HANDHELD, at bootstrap, with internet. Never on the Pi: the
vehicle is not a chart server, carries neither numpy nor scipy, and has no business
decoding a 23 MB raster on a gigabyte of RAM. Nothing in the serving path calls the
network functions here.

WHY THE LAYER EXISTS AT ALL. Painted over the satellite basemap this turns the map from
a dark blue blob into an operational picture: which bank you could get down with kit,
and which is a wall. What it can never be is a promise. The classification the render
half applies to this grid is the geometric fact "this ground is less than 2 m above the
water beside it" — the recon's own output amber-classified a railway cutting — so every
sentence this module writes about its own output says elevation, not permission.

WHAT IS HONEST HERE, and the whole module is built round it:

  * WATER CARRIES NO CLAIM. This half stores elevations only; nothing it writes says
    anything about depth, and the render half paints no water pixel. LIDAR cannot see
    through water and this file must never let anything downstream imply that it can.
  * NODATA IS NOT AN ELEVATION. The service fills unsurveyed ground with about
    -3.4e38. Left in, that single value dominates every histogram, drags every
    hillshade it touches to a cliff, and turns the 2 m test into noise. It is masked
    to NaN here, once, before anything else ever sees the grid.
  * ABSENT AND PARTIAL ARE REPORTED, NEVER FALLED BACK FROM. An area with no LIDAR, or
    with holes in it, says so in the provenance. A silent drop to bare satellite would
    read to an operator as "no low banks here", which is a lie with a boat in it.
  * THE SURVEY IS FROM 2022. It is recorded per area and per sub-request, because banks
    change: a wall gets built, a bank collapses, a wharf is filled in.
  * THE CORRIDOR IS THE TRUST'S CENTRELINE AND NOTHING ELSE. Arms, basins and private
    cuts that are not in data/crt/national/canals-by-km-length-1.geojson are not in
    this download either, and the provenance says that in words.

DEPENDENCIES. numpy and Pillow are imported INSIDE the functions that need them, never
at module scope. A handheld that has not installed them must still start the API and
still serve every other layer — the same rule that makes a missing depth sensor report
itself instead of killing the console. library_state() is the one place that decides
whether this layer can run, and it answers with the sentence an operator can act on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import crt
from .config import settings

log = logging.getLogger("neptune.nav.lidar")


# ---------------------------------------------------------------------------
# OPTIONAL LIBRARIES
# ---------------------------------------------------------------------------
# Imported here and nowhere near module scope. `import numpy` at the top of this file
# would make a handheld without it fail to import nav.lidar, which fails to import
# whatever imports nav.lidar, which takes the whole API down — and the operator would
# be told "the console will not start" when the truth is "one optional map layer needs
# one pip command". The console must survive every absence it can describe.


def _try_import(module: str):
    try:
        return __import__(module), None
    except Exception as exc:  # noqa: BLE001 — a half-installed wheel is an absence too
        return None, f"{type(exc).__name__}: {exc}"


def library_state() -> dict:
    """Can this layer run on this machine, and if not, exactly what is missing.

    THE SENTENCE IS THE POINT. "Bank layer unavailable" teaches nobody anything; what
    an operator at a kitchen table the night before a dive needs is the name of the
    library and the command that installs it. `why` is written to be shown verbatim.
    """
    need = (
        ("numpy", "the elevation grid itself — every array, mask and statistic"),
        ("PIL", "Pillow, which decodes the float32 GeoTIFF the service returns"),
    )
    missing, detail = [], {}
    for mod, what in need:
        obj, err = _try_import(mod)
        detail[mod] = {"present": obj is not None, "needed_for": what, "error": err}
        if obj is None:
            missing.append(mod)
    if not missing:
        return {
            "ok": True,
            "missing": [],
            "install": None,
            "why": "numpy and Pillow are installed, so the LIDAR launch-bank layer "
            "can be downloaded and decoded on this machine.",
            "libraries": detail,
        }
    names = " and ".join("Pillow" if m == "PIL" else m for m in missing)
    cmd = "pip install numpy scipy Pillow"
    return {
        "ok": False,
        "missing": missing,
        "install": cmd,
        "why": (
            f"The LIDAR launch-bank layer is unavailable on this machine because "
            f"{names} {'is' if len(missing) == 1 else 'are'} not installed. "
            f"Install with: {cmd}  (handheld only — the Pi must not carry these). "
            f"Every other map layer is unaffected."
        ),
        "libraries": detail,
    }


# ---------------------------------------------------------------------------
# HTTP — one chokepoint, monkeypatched in tests, exactly satellite.py's shape
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: float | None = None) -> bytes:
    """The only outbound call in this file. Tests replace THIS name.

    Kept deliberately dumb — build request, read body — so that a test which
    monkeypatches it gets the whole network surface of the module in one substitution,
    and so that nothing in the retry/backoff logic above it can be bypassed by a second
    quiet urlopen somewhere further down the file.
    """
    req = urllib.request.Request(url, headers={"User-Agent": settings.lidar_user_agent})
    with urllib.request.urlopen(req, timeout=timeout or settings.lidar_timeout_s) as r:  # noqa: S310
        return r.read()


async def _fetch_retry(url: str, tries: int | None = None) -> tuple[bytes | None, str]:
    """(body, why) with exponential backoff. RETURNS on failure; never raises.

    Returning rather than raising is the same decision satellite.py made and for the
    same reason: one sub-request failing out of nine is a PARTIAL area, which is a true
    and useful thing to record, whereas an exception unwinds the whole download and
    throws away eight good tiles that are already paid for.

    `why` travels with the None so the provenance can say what went wrong months later,
    when the hotspot that dropped is long forgotten.
    """
    tries = tries or settings.lidar_tries
    last = "no attempt was made"
    for attempt in range(tries):
        try:
            return await asyncio.to_thread(_http_get, url), ""
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt == tries - 1:
                log.warning("lidar fetch failed (%s): %s", url.split("?")[0], last)
                return None, last
            await asyncio.sleep(1.0 * (2**attempt))
    return None, last


def coverage_url(bbox: list[float]) -> str:
    """WCS 2.0.1 GetCoverage for one sub-request box, bbox = [W, S, E, N] in degrees.

    subsettingCrs IS NOT OPTIONAL, and that is a measured correction to the endpoint
    this was specified with. The coverage's native CRS is EPSG:27700 with axis labels
    "E N" (DescribeCoverage, checked live 2026-08-08), so `subset=Long(...)` names an
    axis the coverage does not have. Without subsettingCrs the service does not answer
    "unknown axis" — it answers

        HTTP 500  {"message":"Internal server error","statusCode":500,"code":"internal_error"}

    which reads like the service being down, and would have had somebody waiting for a
    dead server to come back. With subsettingCrs=EPSG/0/4326 the identical request
    returns 200 and a 2.5 MB GeoTIFF.

    NO COMPRESSION IS REQUESTED, and that is also measured rather than assumed. The
    service honours `&compression=DEFLATE` and it is tempting — the same Camden box
    drops from 2,523,587 bytes to 337,867, four and a half times less over a hotspot.
    It is not used because PILLOW DECODES IT WRONG. Hand-unpacking the deflate tiles
    with zlib reproduces the uncompressed grid exactly, so the server's bytes are
    correct, but PIL 12.3.0 hands back plausible nonsense: a pixel whose true elevation
    is 31.73 m OD arrives as 2.08e-32. Nothing about that value looks wrong until it
    reaches a histogram. decode_dtm() refuses a compressed TIFF outright for the same
    reason — see the guard there.
    """
    w, s, e, n = bbox
    q = (
        f"service=WCS&version=2.0.1&request=GetCoverage"
        f"&coverageId={urllib.parse.quote(settings.lidar_coverage_id)}"
        f"&subset=Long({w:.7f},{e:.7f})&subset=Lat({s:.7f},{n:.7f})"
        f"&format={urllib.parse.quote(settings.lidar_format)}"
        f"&subsettingCrs={urllib.parse.quote(settings.lidar_subset_crs, safe='')}"
    )
    return f"{settings.lidar_wcs_url}?{q}"


# ---------------------------------------------------------------------------
# THE DECODE
# ---------------------------------------------------------------------------
# Every failure mode below was reachable from the live service on the day this was
# written. The rule throughout: produce a refusal with a reason, never a grid that
# happens to be wrong. A refused sub-request leaves a hole the provenance names; a
# wrong one paints amber over a wall.

_GEOKEY_GEOGRAPHIC = 2048  # GeographicTypeGeoKey
_GEOKEY_PROJECTED = 3072  # ProjectedCSTypeGeoKey
_TAG_PIXEL_SCALE = 33550  # ModelPixelScaleTag
_TAG_TIEPOINT = 33922  # ModelTiepointTag
_TAG_TRANSFORM = 34264  # ModelTransformationTag (what this service sends)
_TAG_GEOKEYS = 34735
_TAG_GDAL_NODATA = 42113
_TAG_COMPRESSION = 259
_TAG_SAMPLEFORMAT = 339  # 3 = IEEE float
_TAG_BITS = 258


def _fail(why: str, **extra) -> dict:
    return {"ok": False, "why": why, "grid": None, **extra}


def _geokey(tags, key: int):
    """One GeoKey out of the GeoKeyDirectory, or None.

    The directory is a flat tuple: four shorts of header, then (key, location, count,
    value) quadruplets. Only keys stored inline (location 0) are read — the ones that
    point off into tags 34736/34737 are strings and doubles this module has no use for.
    """
    d = tags.get(_TAG_GEOKEYS)
    if not d or len(d) < 4:
        return None
    for i in range(4, len(d) - 3, 4):
        if d[i] == key and d[i + 1] == 0:
            return d[i + 3]
    return None


def _transform(tags, width: int, height: int) -> dict | None:
    """Pixel grid → lon/lat, as an affine with NO rotation, or None if unreadable.

    Two spellings are accepted because GeoTIFF has two: this service sends
    ModelTransformation (34264), but ModelPixelScale + ModelTiepoint is the commoner
    pair and costs four lines to support. A rotated or sheared transform is REFUSED
    rather than approximated — the mosaic below indexes by arithmetic, and quietly
    ignoring a rotation term would slide the whole grid sideways by an amount nobody
    would ever see in a number.

    `west`/`north` are the OUTER edge of the first pixel, not its centre: GeoTIFF's
    RasterPixelIsArea, which this service confirms with GeoKey 1025 = 1.
    """
    t = tags.get(_TAG_TRANSFORM)
    if t and len(t) >= 8:
        sx, r1, _, x0, r2, sy, _, y0 = (float(v) for v in t[:8])
        if abs(r1) > 1e-15 or abs(r2) > 1e-15:
            return None
        px_lon, px_lat = sx, -sy  # sy is negative: north-up rasters count down
    else:
        scale, tie = tags.get(_TAG_PIXEL_SCALE), tags.get(_TAG_TIEPOINT)
        if not scale or not tie or len(scale) < 2 or len(tie) < 6:
            return None
        px_lon, px_lat = float(scale[0]), float(scale[1])
        # The tiepoint maps raster (i,j) to model (x,y); anything but the origin pixel
        # would need the offset applied, so do that rather than assume i=j=0.
        x0 = float(tie[3]) - float(tie[0]) * px_lon
        y0 = float(tie[4]) + float(tie[1]) * px_lat
    if not (px_lon > 0 and px_lat > 0):
        return None
    return {
        "west": x0,
        "north": y0,
        "px_lon": px_lon,
        "px_lat": px_lat,
        "east": x0 + px_lon * width,
        "south": y0 - px_lat * height,
        "width": int(width),
        "height": int(height),
    }


def _nodata_value(tags) -> float | None:
    """GDAL_NODATA (42113) is an ASCII string in the file, e.g. "-3.4028234663852886E38"."""
    raw = tags.get(_TAG_GDAL_NODATA)
    if raw is None:
        return None
    try:
        return float(str(raw).strip().strip("\x00"))
    except (TypeError, ValueError):
        return None


def decode_dtm(raw: bytes) -> dict:
    """bytes → {"ok", "why", "grid" (float32, NaN where there is no measurement), ...}.

    NEVER RAISES on bad input, and never returns a grid it is not sure of. The service
    can and does answer with all of the following, every one of them HTTP 200 or a body
    worth quoting:

      * a JSON or XML error document instead of a TIFF (Pillow refuses to open it);
      * a TIFF that is not float32 (would be read as elevations and be wrong by orders
        of magnitude);
      * a COMPRESSED TIFF, which Pillow decodes to garbage that looks like data — see
        coverage_url() for the measurement. This is the dangerous one, so it is
        checked before anything reads a pixel;
      * a grid of zeros for a box outside the 2022 survey, carrying NO GDAL_NODATA tag
        at all (measured over the North Sea: every value 0.0 or a denormal around
        6.9e-41). "Sea level everywhere" is exactly the shape of a launchable bank, so
        a sheet like that is rejected as fill, not stored as terrain.
    """
    libs = library_state()
    if not libs["ok"]:
        return _fail(libs["why"], missing=libs["missing"])
    import io

    import numpy as np
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as exc:  # noqa: BLE001
        head = raw[:200].decode("utf-8", "replace").strip() if raw else "(empty response)"
        return _fail(
            f"the service did not return a readable TIFF ({type(exc).__name__}: " f"{exc}); the body began: {head!r}"
        )

    tags = getattr(im, "tag_v2", {}) or {}
    compression = tags.get(_TAG_COMPRESSION)
    if compression not in (None, 1):
        # REFUSED, not decoded-and-hoped. Pillow 12.3.0 silently mis-decodes tiled
        # float32 deflate (32946) from this service; a wrong elevation is worse than a
        # missing one because nothing downstream can tell it is wrong.
        return _fail(
            f"the TIFF is compressed (compression tag {compression}) and this "
            f"decoder only trusts uncompressed float32; ask for the "
            f"uncompressed form rather than decoding this one"
        )
    if im.mode != "F" or tags.get(_TAG_SAMPLEFORMAT, (3,))[0] != 3 or tags.get(_TAG_BITS, (32,))[0] != 32:
        return _fail(
            f"the TIFF is not 32-bit float elevation (PIL mode {im.mode!r}, "
            f"SampleFormat {tags.get(_TAG_SAMPLEFORMAT)}, "
            f"BitsPerSample {tags.get(_TAG_BITS)})"
        )

    epsg = _geokey(tags, _GEOKEY_GEOGRAPHIC) or _geokey(tags, _GEOKEY_PROJECTED)
    if epsg is not None and int(epsg) != 4326:
        # The mosaic places pixels by lon/lat arithmetic. A grid in any other CRS would
        # land in the wrong place on the map with no symptom other than a canal that is
        # not where the satellite says it is.
        return _fail(
            f"the grid came back in EPSG:{int(epsg)}, not the EPSG:4326 that "
            f"was asked for; it cannot be placed on the map by this code"
        )

    arr = np.asarray(im)
    if arr.dtype != np.float32 or arr.ndim != 2 or arr.size == 0:
        return _fail(f"the decoded array is not a 2-D float32 grid " f"(dtype {arr.dtype}, shape {arr.shape})")
    geo = _transform(tags, arr.shape[1], arr.shape[0])
    if geo is None:
        return _fail(
            "the TIFF carries no readable north-up georeference "
            "(ModelTransformation / ModelPixelScale + ModelTiepoint)"
        )

    nodata = _nodata_value(tags)
    grid = np.array(arr, dtype=np.float32, copy=True)

    # --- the mask, counted by CAUSE ------------------------------------------
    # The counts are kept apart rather than OR-ed into one boolean because the sentence
    # written when nothing survives has to name the right failure. "Every pixel is
    # nodata, this ground is outside the survey" is a true and useful thing to record;
    # said over a response that was actually 900 km of nonsense it is a lie that closes
    # the case, and the next run would not retry a box it should retry.
    unsurveyed = ~np.isfinite(grid)  # NaN / inf
    if nodata is not None:
        # Compared with a tolerance, not ==: the tag is decimal ASCII and the pixels are
        # float32, so an exact equality test is one rounding away from leaving the
        # sentinel in the grid, and the sentinel is 3.4e38.
        unsurveyed |= np.isclose(grid, np.float32(nodata), rtol=1e-6, atol=0.0)
    unsurveyed |= grid < settings.lidar_sentinel_below  # the ~-3.4e38 fill, tag or not
    implausible = (~unsurveyed) & ((grid < settings.lidar_elev_min_m) | (grid > settings.lidar_elev_max_m))
    # Denormals: 6.9e-41 is not a height above Newlyn, it is uninitialised memory.
    implausible |= (~unsurveyed) & (grid != 0) & (np.abs(grid) < 1e-30)
    bad = unsurveyed | implausible
    grid[bad] = np.nan

    total = int(grid.size)
    n_unsurveyed = int(np.count_nonzero(unsurveyed))
    n_implausible = int(np.count_nonzero(implausible))
    valid = int(total - n_unsurveyed - n_implausible)
    out = {
        "ok": True,
        "why": "",
        "grid": grid,
        "geo": geo,
        "nodata_value": nodata,
        "nodata_tag_present": nodata is not None,
        "pixels": total,
        "valid_pixels": valid,
        "nodata_pixels": n_unsurveyed,
        "implausible_pixels": n_implausible,
        "nodata_fraction": round(1.0 - valid / total, 6) if total else 1.0,
        "compression": int(compression) if compression else 1,
        "elev_min": None,
        "elev_max": None,
        "fill": False,
    }

    if valid == 0:
        out["ok"] = False
        out["why"] = (
            f"every pixel in this box is nodata — the {settings.lidar_survey_vintage} "
            f"LIDAR composite does not cover this ground"
            if n_implausible == 0
            else f"nothing in this box is a plausible elevation: {n_implausible} of {total} "
            f"pixels fall outside {settings.lidar_elev_min_m:g} to "
            f"{settings.lidar_elev_max_m:g} m OD and the rest is nodata. This is not "
            f"terrain and has not been stored"
        )
        return out

    lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
    out["elev_min"], out["elev_max"] = round(lo, 3), round(hi, 3)
    # THE FILL-SHEET TEST. A box outside the survey comes back as a flat sheet of zeros
    # with no nodata tag to say so. Flat AND at zero is the signature; a genuinely flat
    # real pound is at 20-something metres OD and passes, and a genuine 0.00 m OD reach
    # is not flat to a centimetre across a whole square kilometre.
    if (hi - lo) < settings.lidar_fill_span_m and abs(hi) < settings.lidar_fill_span_m:
        out["ok"] = False
        out["fill"] = True
        out["why"] = (
            f"the grid is a flat sheet at {lo:.3f}-{hi:.3f} m with no relief, "
            f"which is how this service answers a box outside the survey — "
            f"it is fill, not terrain, and has not been stored"
        )
    return out


# ---------------------------------------------------------------------------
# THE AREA GRID, AND THE SUB-REQUESTS THAT FILL IT
# ---------------------------------------------------------------------------
def area_grid(bbox: list[float], px_m: float | None = None) -> dict:
    """The ONE lattice an area's DTM is stored on: [W,S,E,N] → origin, pixel size, size.

    Defined here, before anything is fetched, because every sub-request is then a
    whole-pixel window of it and the mosaic has no seams by construction. The
    alternative — take whatever grid each response happens to arrive on — was measured
    and is worse than it sounds: three adjacent boxes at Camden came back with pixel
    sizes 1.487370815486e-05, 1.487593061110e-05 and 1.487604150357e-05 degrees and
    origins snapped outward by different amounts, so consecutive tiles share neither a
    lattice nor an edge.

    Pixels are square IN METRES at the area's centre latitude, which is what the 2 m
    height test, the gradient threshold and the hillshade all actually want. A degree
    of longitude is 0.62 of a degree of latitude at Camden; a grid that ignored that
    would light the hillshade from the wrong direction.
    """
    w, s, e, n = (float(v) for v in bbox)
    px_m = float(px_m or settings.lidar_px_m)
    lat_mid = (s + n) / 2.0
    px_lat = px_m / 111132.0
    px_lon = px_m / max(1.0, 111320.0 * math.cos(math.radians(lat_mid)))
    width = max(1, int(math.ceil((e - w) / px_lon)))
    height = max(1, int(math.ceil((n - s) / px_lat)))
    return {
        "west": w,
        "north": s + height * px_lat,
        "px_lon": px_lon,
        "px_lat": px_lat,
        "east": w + width * px_lon,
        "south": s,
        "width": width,
        "height": height,
        "px_m": px_m,
    }


def subrequest_boxes(grid: dict) -> list[dict]:
    """Chop the area grid into whole-pixel windows, each about lidar_subrequest_m square.

    WHY 1000 m, AND THE NUMBERS IT WAS CHOSEN ON (Camden, live, 2026-08-08):

        350 m box   →   2,523,587 bytes    0.9 s     3.9x the raw float32
       1000 m box   →   9,437,675 bytes    2.8 s     2.2x
       2400 m box   →  32,588,323 bytes   62.0 s     1.3x

    The overhead is the service's internal tiling padding the edges, so bigger requests
    are more efficient per byte — and that is not the constraint. 62 seconds for one
    request is: a hotspot at the water's edge with a whole minute to drop in, one
    all-or-nothing unit of work, and no progress to show while it runs. 1000 m is under
    three seconds, gives a resumable unit worth about 9 MB, and divides the standard
    2.4 km area into nine of which the corridor filter usually keeps four or five.
    """
    step = max(1, int(round(settings.lidar_subrequest_m / grid["px_m"])))
    out = []
    for row, j0 in enumerate(range(0, grid["height"], step)):
        for col, i0 in enumerate(range(0, grid["width"], step)):
            i1 = min(grid["width"], i0 + step)
            j1 = min(grid["height"], j0 + step)
            out.append(
                {
                    "row": row,
                    "col": col,
                    "key": f"r{row:02d}c{col:02d}",
                    "i0": i0,
                    "j0": j0,
                    "i1": i1,
                    "j1": j1,
                    "bbox": [
                        grid["west"] + i0 * grid["px_lon"],
                        grid["north"] - j1 * grid["px_lat"],
                        grid["west"] + i1 * grid["px_lon"],
                        grid["north"] - j0 * grid["px_lat"],
                    ],
                }
            )
    return out


# ---------------------------------------------------------------------------
# THE CORRIDOR: THE TRUST'S CENTRELINE, ALREADY ON THIS CARD
# ---------------------------------------------------------------------------
# data/crt/national/canals-by-km-length-1.geojson is 9.8 MB and 3,173 features, fetched
# nationally by `python -m nav.cli crt-fetch`. It is NOT fetched again here: it is the
# same network, it is already verified against the live service, and a second copy
# would be a second thing to keep current.
#
# The read goes through crt.py's byte-offset index (<layer>.index.json: [offset, length,
# W, S, E, N] per feature), so windowing a national file down to one area is a small
# JSON load and a handful of seeks rather than parsing 9.8 MB.


def _segment_hits_box(x1: float, y1: float, x2: float, y2: float, w: float, s: float, e: float, n: float) -> bool:
    """Liang-Barsky: does the segment touch the axis-aligned box at all.

    Endpoint-in-box would have been shorter and is wrong: a centreline vertex every
    20 m and a 150 m margin makes it *almost* always right, and "almost" here means a
    sub-request silently skipped and a hole in the bank layer at the one bridge where
    the survey vertices happened to be sparse.
    """
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - w), (dx, e - x1), (-dy, y1 - s), (dy, n - y1)):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return True


def _walk_lines(geom, out: list) -> None:
    """Every LineString in a geometry, whatever the nesting, as a list of vertices."""
    if not isinstance(geom, dict):
        return
    kind, coords = geom.get("type"), geom.get("coordinates")
    if kind == "LineString" and coords:
        out.append(coords)
    elif kind in ("MultiLineString", "Polygon") and coords:
        out.extend(c for c in coords if c)
    elif kind == "MultiPolygon" and coords:
        for poly in coords:
            out.extend(c for c in poly if c)
    elif kind == "GeometryCollection":
        for g in geom.get("geometries", []) or []:
            _walk_lines(g, out)


def centreline_in(bbox: list[float]) -> dict:
    """The Trust's canal centreline inside `bbox`, off the card. No network, ever.

    Returns {"ok", "why", "lines": [[[lon,lat], ...], ...], "features": int}. `ok` is
    False when the national card has not been fetched — and that is a message with a
    command in it, not a shrug, because without the centreline this layer cannot know
    where the canal is and would otherwise download a square kilometre of housing
    estate.
    """
    key = settings.lidar_centreline_layer
    path = crt.national_dir() / f"{key}.geojson"
    idx_path = crt.national_index_path(key)
    if not path.exists():
        return {
            "ok": False,
            "lines": [],
            "features": 0,
            "why": (
                f"the Canal & River Trust centreline is not on this handheld "
                f"({path}). The LIDAR bank layer traces its corridor from that "
                f"file and cannot be planned without it. Fetch it once, with "
                f"internet: python -m nav.cli crt-fetch"
            ),
        }
    w, s, e, n = (float(v) for v in bbox)
    lines: list = []
    features = 0
    try:
        entries = json.loads(idx_path.read_text(encoding="utf-8"))["entries"]
    except Exception as exc:  # noqa: BLE001
        # No index: read the whole 9.8 MB file rather than window it. Slower, never
        # wrong. Refusing here would make a card that HAS the data behave like one that
        # does not, on the strength of a missing optimisation.
        log.info("no window index beside %s (%s) — reading the whole layer", path.name, exc)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc2:  # noqa: BLE001
            return {
                "ok": False,
                "lines": [],
                "features": 0,
                "why": f"the centreline file {path.name} could not be read ({exc2})",
            }
        for feat in doc.get("features", []):
            got: list = []
            _walk_lines(feat.get("geometry"), got)
            if got:
                features += 1
                lines.extend(got)
    else:
        picks = [
            (int(r[0]), int(r[1]))
            for r in entries
            # A feature with no recorded box could not be placed, so it is never
            # windowed OUT — the same rule the map backend applies. Excluding it
            # would be this file deciding what to skip on the strength of not
            # understanding it.
            if len(r) < 6 or not (r[4] < w or r[2] > e or r[5] < s or r[3] > n)
        ]
        with open(path, "rb") as fh:
            for off, ln in picks:
                fh.seek(off)
                try:
                    feat = json.loads(fh.read(ln).decode("utf-8"))
                except Exception:  # noqa: BLE001 — one unreadable feature is not a failure
                    continue
                got: list = []
                _walk_lines(feat.get("geometry"), got)
                if got:
                    features += 1
                    lines.extend(got)
    if not lines:
        return {
            "ok": True,
            "lines": [],
            "features": 0,
            "why": (
                "no Canal & River Trust canal centreline passes through this "
                "area. Arms, basins and private cuts that the Trust does not "
                "hold a centreline for are absent from this layer too, and "
                "that is a gap in the source, not in the ground"
            ),
        }
    return {
        "ok": True,
        "lines": lines,
        "features": features,
        "why": f"{features} Trust centreline feature(s) cross this area",
    }


def _on_corridor(box: list[float], lines: list, margin_deg: tuple[float, float]) -> bool:
    dw, dh = margin_deg
    w, s, e, n = box[0] - dw, box[1] - dh, box[2] + dw, box[3] + dh
    for line in lines:
        prev = None
        for pt in line:
            if prev is not None and _segment_hits_box(prev[0], prev[1], pt[0], pt[1], w, s, e, n):
                return True
            prev = pt
    return False


# ---------------------------------------------------------------------------
# STORAGE
# ---------------------------------------------------------------------------
# data/areas/<name>.lidar/  — a DIRECTORY, and the extension is on the directory on
# purpose. areas.list_areas() globs areas/*.json and reads every hit as an area, so a
# file called "<name>.dtm.json" beside the imagery would invent an area named
# "<name>.dtm" that nothing can fly and nothing can delete. A directory matches no glob
# in that module. It is the same trap crt.py sidestepped by living outside areas_dir
# altogether; this layer has to sit beside the imagery it is painted over, so it sits
# beside it as a folder.


def area_lidar_dir(name: str) -> Path:
    return settings.areas_dir / f"{name}{settings.lidar_dir_suffix}"


def dtm_path(name: str) -> Path:
    return area_lidar_dir(name) / "dtm.npy"


def provenance_path(name: str) -> Path:
    return area_lidar_dir(name) / "provenance.json"


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_atomic_json(path: Path, data: dict) -> None:
    """Write beside, then replace. areas.py's lesson, and it applies harder here.

    This file is the ONLY record of which sub-requests landed. Truncate it and a
    23 MB grid on the card becomes a grid nothing can say anything about — not which
    parts are real, not whether the holes are sea or a dropped hotspot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # BaseException on purpose: Ctrl-C at the bank is the case this exists for.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_json_quiet(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# PLANNING — what a fetch would cost, with no network touched
# ---------------------------------------------------------------------------
def plan(bbox: list[float], px_m: float | None = None) -> dict:
    """What downloading this area's DTM would involve. Reads the card, calls nothing.

    Exists so the console can say "9 requests, about 47 MB, roughly 40 seconds" BEFORE
    an operator on a metered hotspot at the water's edge commits to it — the same
    courtesy areas.plan_area() extends for imagery, and for the same reason.
    """
    grid = area_grid(bbox, px_m)
    boxes = subrequest_boxes(grid)
    centre = centreline_in(bbox)
    margin = (
        settings.lidar_fetch_margin_m / max(1.0, 111320.0 * math.cos(math.radians((bbox[1] + bbox[3]) / 2.0))),
        settings.lidar_fetch_margin_m / 111132.0,
    )
    wanted, skipped = [], []
    for b in boxes:
        (wanted if (centre["lines"] and _on_corridor(b["bbox"], centre["lines"], margin)) else skipped).append(b)
    mb = round(len(wanted) * settings.lidar_avg_mb_per_request, 1)
    over = None
    if len(wanted) > settings.lidar_max_requests:
        over = f"{len(wanted)} sub-requests exceeds the cap of " f"{settings.lidar_max_requests} — shrink the area"
    if grid["width"] * grid["height"] > settings.lidar_max_pixels:
        over = (
            f"{grid['width']}x{grid['height']} pixels exceeds the cap of "
            f"{settings.lidar_max_pixels:,} — shrink the area or raise "
            f"NAV_LIDAR_PX_M"
        )
    return {
        "grid": grid,
        "requests": len(wanted),
        "skipped_off_corridor": len(skipped),
        "sub_boxes": wanted,
        "est_mb": mb,
        "est_seconds": round(
            len(wanted) * (settings.lidar_avg_seconds_per_request + 1.0 / max(0.05, settings.lidar_rate_per_s))
        ),
        "centreline": {"ok": centre["ok"], "features": centre["features"], "why": centre["why"]},
        "over_cap": over,
        "libraries": library_state(),
    }


# ---------------------------------------------------------------------------
# THE DOWNLOAD
# ---------------------------------------------------------------------------
async def download_dtm(
    name: str, bbox: list[float], progress=None, refresh: bool = False, px_m: float | None = None
) -> dict:
    """Fetch, decode and mosaic the 1 m DTM for one offline area. Returns; never raises
    for a network or data failure — an area that got seven of nine sub-requests is a
    PARTIAL area and says so.

    RESUMES BY DEFAULT, and the ledger is per sub-request. The grid is written straight
    into a memory-mapped .npy as each response is decoded, so a run killed at the bank
    leaves every sub-request that landed already on the card and the next run asks only
    for the ones that did not. Re-requesting 9 MB that is already on disk over a phone
    hotspot is the cost of getting this wrong.
    """

    async def emit(msg: dict) -> None:
        if progress:
            await progress({"name": name, "layer": "lidar", **msg})

    libs = library_state()
    if not libs["ok"]:
        await emit({"state": "unavailable", "why": libs["why"]})
        return {
            "name": name,
            "state": "unavailable",
            "why": libs["why"],
            "missing": libs["missing"],
            "install": libs["install"],
        }

    import numpy as np

    p = plan(bbox, px_m)
    if p["over_cap"]:
        await emit({"state": "refused", "why": p["over_cap"]})
        return {"name": name, "state": "refused", "why": p["over_cap"]}
    if not p["centreline"]["ok"] or not p["sub_boxes"]:
        why = (
            p["centreline"]["why"]
            if not p["centreline"]["ok"]
            else (
                "no Canal & River Trust centreline passes within "
                f"{settings.lidar_fetch_margin_m:.0f} m of this area, so there is no "
                "corridor to survey. Arms and basins the Trust holds no centreline for are "
                "absent from this layer, and that is a gap in the source, not in the ground"
            )
        )
        await emit({"state": "absent", "why": why})
        record = _provenance(name, bbox, p, [], why, "absent")
        _write_atomic_json(provenance_path(name), record)
        return {"name": name, "state": "absent", "why": why, "provenance": record}

    grid = p["grid"]
    d = area_lidar_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    prev = _read_json_quiet(provenance_path(name)) or {}
    done: dict[str, dict] = {}
    if not refresh and dtm_path(name).exists():
        # The ledger is only believed if it describes THIS grid. An area re-planned at a
        # different pixel size or a grown bbox is a different lattice, and pouring new
        # sub-requests into the old array would offset them by whatever the two grids
        # differ by — a shift with no symptom except a bank in the wrong place.
        if (
            (prev.get("grid") or {}).get("width") == grid["width"]
            and (prev.get("grid") or {}).get("height") == grid["height"]
            and abs((prev.get("grid") or {}).get("west", 1e9) - grid["west"]) < 1e-12
        ):
            # Only sub-requests THIS plan still wants are carried forward. A wider fetch
            # margin, or a Trust centreline that has moved, changes which boxes are on
            # the corridor — and a ledger still listing yesterday's boxes would make
            # _state_of count "7 of 9 landed" against a plan that only ever wanted 7.
            planned = {b["key"] for b in p["sub_boxes"]}
            done = {
                k: v
                for k, v in (prev.get("parts") or {}).items()
                if k in planned and v.get("state") in ("present", "absent", "fill")
            }
        else:
            log.info("lidar: %s was stored on a different grid — refetching all", name)

    mm = None
    try:
        if done and dtm_path(name).exists():
            mm = np.lib.format.open_memmap(dtm_path(name), mode="r+")
            if mm.shape != (grid["height"], grid["width"]):
                mm = None
                done = {}
        if mm is None:
            mm = np.lib.format.open_memmap(
                dtm_path(name), mode="w+", dtype=np.float32, shape=(grid["height"], grid["width"])
            )
            # NaN, not zero. An unfetched pixel that reads 0.0 is "sea level here",
            # which is the exact shape of a launchable bank; NaN is the only initial
            # value that cannot be mistaken for a measurement.
            mm[:] = np.nan
            done = {}
    except Exception as exc:  # noqa: BLE001
        why = f"could not create the elevation grid at {dtm_path(name)}: {exc}"
        await emit({"state": "failed", "why": why})
        return {"name": name, "state": "failed", "why": why}

    parts: dict[str, dict] = dict(done)
    await emit(
        {
            "state": "starting",
            "requests": p["requests"],
            "already": len(done),
            "est_mb": p["est_mb"],
            "skipped_off_corridor": p["skipped_off_corridor"],
        }
    )

    delay = 1.0 / max(0.05, settings.lidar_rate_per_s)
    wire_bytes = 0
    for i, box in enumerate(p["sub_boxes"]):
        if box["key"] in done:
            continue
        url = coverage_url(box["bbox"])
        raw, why = await _fetch_retry(url)
        if raw is None:
            parts[box["key"]] = {"state": "failed", "why": why, "bbox": box["bbox"], "fetched": _iso()}
        else:
            wire_bytes += len(raw)
            dec = decode_dtm(raw)
            rec = {
                "bbox": box["bbox"],
                "bytes": len(raw),
                "fetched": _iso(),
                "survey_vintage": settings.lidar_survey_vintage,
            }
            if dec["ok"]:
                placed = _place(np, mm, grid, box, dec)
                rec.update(
                    {
                        "state": "present",
                        "why": "",
                        "pixels": dec["pixels"],
                        "valid_pixels": dec["valid_pixels"],
                        "nodata_fraction": dec["nodata_fraction"],
                        "nodata_value": dec["nodata_value"],
                        "nodata_tag_present": dec["nodata_tag_present"],
                        "elev_min_m": dec["elev_min"],
                        "elev_max_m": dec["elev_max"],
                        "source_px_lon_deg": dec["geo"]["px_lon"],
                        "source_px_lat_deg": dec["geo"]["px_lat"],
                        "source_size": [dec["geo"]["width"], dec["geo"]["height"]],
                        "placed_pixels": placed,
                    }
                )
            else:
                # An honest empty: the box was asked for, answered, and holds no survey.
                # Recorded as its own state so a later run does not keep asking, and so
                # the provenance can tell "outside the survey" from "the link dropped".
                rec.update(
                    {
                        "state": "fill" if dec.get("fill") else "absent",
                        "why": dec["why"],
                        "nodata_fraction": dec.get("nodata_fraction"),
                        "elev_min_m": dec.get("elev_min"),
                        "elev_max_m": dec.get("elev_max"),
                    }
                )
            parts[box["key"]] = rec
        # THE LEDGER IS FLUSHED PER SUB-REQUEST, and the memmap with it. Nine minutes of
        # download must not be undone by a lid closing on the tenth: satellite.py learned
        # the same lesson with a 25-tile commit that rolled back four tiles out of five.
        try:
            mm.flush()
        except Exception:  # noqa: BLE001
            pass
        _write_atomic_json(
            provenance_path(name), _provenance(name, bbox, p, parts, "", "downloading", wire_bytes=wire_bytes)
        )
        await emit(
            {
                "state": "running",
                "done": i + 1,
                "total": p["requests"],
                "key": box["key"],
                "part_state": parts[box["key"]]["state"],
            }
        )
        await asyncio.sleep(delay)

    stats = _grid_stats(np, mm, p["sub_boxes"])
    try:
        mm.flush()
    except Exception:  # noqa: BLE001
        pass
    del mm

    state, why = _state_of(parts, stats)
    record = _provenance(name, bbox, p, parts, why, state, wire_bytes=wire_bytes, stats=stats)
    _write_atomic_json(provenance_path(name), record)
    await emit({"state": state, "why": why, "coverage": stats["coverage_fraction"]})
    return {"name": name, "state": state, "why": why, "provenance": record, "dtm": str(dtm_path(name))}


def _place(np, mm, grid: dict, box: dict, dec: dict) -> int:
    """Copy one decoded sub-request into the area grid, nearest source pixel.

    NEAREST, NOT INTERPOLATED, and that is the honesty rule applied to arithmetic. The
    service snaps every request outward onto its own grid — the Camden box asked for
    W=-0.1500 came back starting at -0.150166611, eleven metres out — so the response
    never lands exactly on the lattice this area is stored on. Interpolating would
    invent elevations between two measurements; nearest picks a real one and is wrong
    by at most half a pixel, which at 1 m is half a metre of horizontal placement and
    is recorded as such in the provenance.

    A destination pixel with no source pixel is left NaN rather than filled from the
    edge, because "the survey does not reach here" and "the nearest measurement is 3 m
    away" are different claims.
    """
    geo = dec["geo"]
    src = dec["grid"]
    i0, i1, j0, j1 = box["i0"], box["i1"], box["j0"], box["j1"]
    # Destination pixel CENTRES in lon/lat, then back into source pixel indices. The
    # 0.5s are the RasterPixelIsArea convention on both sides; dropping them would shift
    # the whole layer by half a pixel in each axis.
    lon = grid["west"] + (np.arange(i0, i1, dtype=np.float64) + 0.5) * grid["px_lon"]
    lat = grid["north"] - (np.arange(j0, j1, dtype=np.float64) + 0.5) * grid["px_lat"]
    si = np.rint((lon - geo["west"]) / geo["px_lon"] - 0.5).astype(np.int64)
    sj = np.rint((geo["north"] - lat) / geo["px_lat"] - 0.5).astype(np.int64)
    ok_i = (si >= 0) & (si < geo["width"])
    ok_j = (sj >= 0) & (sj < geo["height"])
    if not ok_i.any() or not ok_j.any():
        return 0
    block = src[np.ix_(np.clip(sj, 0, geo["height"] - 1), np.clip(si, 0, geo["width"] - 1))]
    block = np.where(ok_j[:, None] & ok_i[None, :], block, np.float32(np.nan))
    mm[j0:j1, i0:i1] = block
    return int(np.count_nonzero(np.isfinite(block)))


def _grid_stats(np, mm, boxes: list[dict]) -> dict:
    """Coverage and elevation range off the stored grid — measured TWICE, on purpose.

    COVERAGE IS OVER THE CORRIDOR, NOT OVER THE RECTANGLE, and getting that wrong was a
    real bug in this file. The area grid is a bbox; the download is the sub-requests the
    Trust's centreline passes through. On an area where the canal runs corner to corner
    the corridor is barely half the rectangle, so measuring "surveyed pixels / all
    pixels" reported 51.4% for a download in which every single sub-request succeeded —
    a number that reads as a half-broken area and, one percentage point lower, would
    have labelled a complete download PARTIAL and sent an operator back to a hotspot to
    re-fetch nothing.

    So `coverage_fraction` answers "of the ground I asked for, how much came back", which
    is the question the state field acts on. `area_fraction` answers "how much of the
    bbox is painted at all", which is what an operator sees on the map. They are
    different questions and the difference is the corridor, so both are written down.

    Read from the ARRAY either way, never accumulated from the parts ledger: the ledger
    records what each response claimed, and this records what survived being placed.
    """
    total = int(mm.size)
    finite = corridor = 0
    lo, hi = math.inf, -math.inf
    # Row-blocked so a 144 MB grid never has a second full-size boolean beside it.
    step = max(1, 4_000_000 // max(1, mm.shape[1]))
    for j in range(0, mm.shape[0], step):
        chunk = np.asarray(mm[j : j + step])
        good = np.isfinite(chunk)
        c = int(np.count_nonzero(good))
        if c:
            finite += c
            vals = chunk[good]
            lo = min(lo, float(vals.min()))
            hi = max(hi, float(vals.max()))
    # The requested windows tile the corridor and never overlap (subrequest_boxes cuts
    # the grid into disjoint whole-pixel blocks), so this is a sum and not a union.
    asked = 0
    for b in boxes:
        block = np.asarray(mm[b["j0"] : b["j1"], b["i0"] : b["i1"]])
        asked += block.size
        corridor += int(np.count_nonzero(np.isfinite(block)))
    return {
        "pixels": total,
        "valid_pixels": finite,
        "corridor_pixels": asked,
        "corridor_valid_pixels": corridor,
        "coverage_fraction": round(corridor / asked, 6) if asked else 0.0,
        "area_fraction": round(finite / total, 6) if total else 0.0,
        "elev_min_m": None if finite == 0 else round(lo, 3),
        "elev_max_m": None if finite == 0 else round(hi, 3),
    }


def _state_of(parts: dict, stats: dict) -> tuple[str, str]:
    """PRESENT / PARTIAL / ABSENT for the area, and the sentence that goes with it.

    THREE STATES AND NO FOURTH, because the render half has to be able to say which one
    it is drawing. "Absent" and "partial" both mean unpainted ground on the map, and an
    operator must be told which — unpainted because the survey stops there is a fact
    about the country, unpainted because a hotspot dropped is a fact about the download
    and is fixable by running it again.
    """
    failed = [k for k, v in parts.items() if v.get("state") == "failed"]
    got = [k for k, v in parts.items() if v.get("state") == "present"]
    cov = stats["coverage_fraction"]
    vintage = settings.lidar_survey_vintage
    if not got:
        return "absent", (
            "No LIDAR was stored for this area. "
            + (
                f"{len(failed)} sub-request(s) failed and can be retried."
                if failed
                else f"Every sub-request answered, and none of them held any of the {vintage} "
                f"survey — this ground is outside it."
            )
            + " The map draws the satellite imagery unpainted here, which means "
            "'not surveyed', NOT 'no low banks'."
        )
    # Said the same way in all three sentences below, because an operator reading
    # "94% surveyed" over a map that is half bare has been told two things that only
    # reconcile if somebody explains the corridor.
    outside = (
        ""
        if stats["area_fraction"] >= 0.995
        else f" Ground outside the canal corridor was never requested, which is why "
        f"only {stats['area_fraction'] * 100:.0f}% of the rectangle is painted "
        f"at all."
    )
    if failed:
        return "partial", (
            f"{len(got)} of {len(parts)} sub-request(s) landed and {len(failed)} failed, "
            f"so this area's LIDAR is INCOMPLETE: {cov * 100:.1f}% of the corridor that "
            f"was asked for carries a {vintage} elevation. Unpainted ground on the canal "
            f"here may be unsurveyed or may simply be undownloaded — run the fetch again "
            f"with internet to close the gap." + outside
        )
    if cov < settings.lidar_min_coverage:
        return "partial", (
            f"Every sub-request answered, but only {cov * 100:.1f}% of the corridor "
            f"carries a {vintage} elevation; the rest is nodata in the survey itself. "
            f"Nothing further will be downloaded — the gaps are in the source." + outside
        )
    return "present", (
        f"{cov * 100:.1f}% of the canal corridor in this area carries a {vintage} "
        f"Environment Agency 1 m LIDAR elevation, from {len(got)} sub-request(s). "
        f"Elevations run {stats['elev_min_m']} to {stats['elev_max_m']} m above "
        f"Ordnance Datum. Bank height is a measurement; it is not a statement that a "
        f"launch is possible." + outside
    )


def _provenance(
    name: str, bbox: list[float], p: dict, parts, why: str, state: str, wire_bytes: int = 0, stats: dict | None = None
) -> dict:
    """Everything a reader needs to judge this grid a year from now, in one file.

    WHY EACH FIELD IS HERE. `fetched` because a card that has been right for a year has
    not been CHECKED for a year. `survey_vintage` because the DTM is a 2022 survey and
    banks change — a wall built in 2024 is not in it and nothing downstream can know
    that unless this says so. `nodata_value` because the sentinel found in the file is
    the one thing that proves the mask was applied to what actually arrived rather than
    to what was expected. `placement_error_m` because a mosaic is arithmetic and the
    arithmetic has a bound, and a bound nobody wrote down is a bound nobody can check.
    """
    parts = parts if isinstance(parts, dict) else {}
    nod = [v.get("nodata_value") for v in parts.values() if v.get("nodata_value") is not None]
    return {
        "layer": "lidar-dtm",
        "area": name,
        "state": state,
        "why": why,
        "bbox": [float(v) for v in bbox],
        "fetched": _iso(),
        "survey_vintage": settings.lidar_survey_vintage,
        "source": {
            "service": settings.lidar_wcs_url,
            "coverage_id": settings.lidar_coverage_id,
            "attribution": settings.lidar_attribution,
            "subsetting_crs": settings.lidar_subset_crs,
            "format": settings.lidar_format,
            "note": (
                "subsettingCrs is required: the coverage is natively EPSG:27700 "
                "and the service answers HTTP 500 without it. Compression is not "
                "requested because Pillow mis-decodes the compressed form."
            ),
        },
        "grid": {
            **p["grid"],
            "crs": "EPSG:4326",
            "pixel_size_m_nominal": p["grid"]["px_m"],
            "dtype": "float32",
            "nodata": "NaN",
            "file": dtm_path(name).name,
            "note": (
                "north-up; west/north are the OUTER edge of pixel (0,0). "
                "Elevations are metres above Ordnance Datum Newlyn. Pixels "
                "with no measurement are NaN, never a sentinel and never 0."
            ),
        },
        "placement_error_m": round(p["grid"]["px_m"] / 2.0, 3),
        "placement_note": (
            "sub-requests are placed by nearest source pixel, so a "
            "stored elevation is at most half a pixel from where it was "
            "measured; nothing is interpolated and no value is invented"
        ),
        "corridor": {
            "centreline_layer": settings.lidar_centreline_layer,
            "centreline_source": str(crt.national_dir() / f"{settings.lidar_centreline_layer}.geojson"),
            "centreline_features": p["centreline"]["features"],
            "fetch_margin_m": settings.lidar_fetch_margin_m,
            "sub_requests_off_corridor": p["skipped_off_corridor"],
            "note": (
                "only ground within the fetch margin of a Canal & River Trust "
                "canal centreline was downloaded. Arms, basins and private cuts "
                "the Trust holds no centreline for are absent from this layer — a "
                "gap in the source, not in the ground"
            ),
        },
        "requests": {
            "planned": p["requests"],
            "sub_request_m": settings.lidar_subrequest_m,
            "wire_bytes": wire_bytes,
            "rate_per_s": settings.lidar_rate_per_s,
        },
        "nodata_values_seen": sorted({float(v) for v in nod}) if nod else [],
        "coverage": stats or {},
        "coverage_note": (
            "coverage is measured on the STORED grid; a part's nodata_fraction is "
            "measured on the RESPONSE, and the two differ on purpose. The service "
            "snaps each request outward onto its own grid and pads the reprojected "
            "corners with nodata, so a response is a little larger than the window "
            "asked for and carries a nodata rim it did not have to survey — measured "
            "at Camden, parts read 4.8% nodata while the area they filled came out "
            "99.998% covered. Reading a part figure as the area's holes would be "
            "reading the padding as missing ground."
        ),
        "parts": parts,
    }


# ---------------------------------------------------------------------------
# READING IT BACK — the render half's door into all of the above
# ---------------------------------------------------------------------------
def card(name: str) -> dict:
    """What LIDAR this area holds, off the disk, with no network and no numpy.

    Callable on a machine with neither library installed, because the console has to be
    able to say WHY the layer is missing, and a status call that needs the missing
    library to answer would be silent in exactly the case it exists for.
    """
    prov = _read_json_quiet(provenance_path(name))
    libs = library_state()
    if prov is None:
        return {
            "area": name,
            "state": "absent",
            "held": False,
            "libraries": libs,
            "why": (
                f"No LIDAR has been downloaded for the area '{name}'. The "
                f"satellite imagery is drawn unpainted, which means 'not "
                f"surveyed here', not 'no low banks here'." + ("" if libs["ok"] else " " + libs["why"])
            ),
        }
    path = dtm_path(name)
    return {
        "area": name,
        "state": prov.get("state", "absent"),
        "held": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "why": prov.get("why", ""),
        "fetched": prov.get("fetched"),
        "survey_vintage": prov.get("survey_vintage"),
        "coverage": prov.get("coverage", {}),
        "bbox": prov.get("bbox"),
        "grid": prov.get("grid", {}),
        "attribution": (prov.get("source") or {}).get("attribution"),
        "libraries": libs,
        "provenance": str(provenance_path(name)),
    }


def read_dtm(name: str, mmap: bool = True) -> dict:
    """The stored grid for `name`: {"ok", "why", "grid", "geo", "provenance"}.

    THIS IS THE INTERFACE THE RENDER HALF USES. `grid` is a 2-D float32 numpy array,
    metres above Ordnance Datum, NaN wherever there is no measurement — already masked,
    so no consumer ever has to know what the service's sentinel was. `geo` is the
    lattice from area_grid(): west/north outer edges, px_lon/px_lat in degrees.

    Memory-mapped by default: a 6 km area is 144 MB and the classification only ever
    reads windows of it.
    """
    libs = library_state()
    if not libs["ok"]:
        return {"ok": False, "why": libs["why"], "grid": None, "geo": None, "provenance": None}
    import numpy as np

    prov = _read_json_quiet(provenance_path(name))
    path = dtm_path(name)
    if prov is None or not path.exists():
        return {
            "ok": False,
            "grid": None,
            "geo": None,
            "provenance": prov,
            "why": (f"no LIDAR grid is stored for the area '{name}' " f"({path} is not on this card)"),
        }
    try:
        grid = np.load(path, mmap_mode="r" if mmap else None)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "grid": None,
            "geo": None,
            "provenance": prov,
            "why": f"the stored elevation grid could not be read ({exc})",
        }
    return {
        "ok": True,
        "why": prov.get("why", ""),
        "grid": grid,
        "geo": prov.get("grid", {}),
        "provenance": prov,
        "state": prov.get("state", "absent"),
    }

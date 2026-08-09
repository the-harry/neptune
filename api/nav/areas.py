"""Offline area management (spec §6.4) + the `pmtiles extract` job runner (§6.1).

Extraction is a BOOTSTRAP-time operation (needs internet + the pmtiles binary).
In the isolated segment it's unavailable — this reports that cleanly instead of
hanging. Areas live as areas/<name>.pmtiles + areas/<name>.json (metadata) +
optional areas/<name>.geojson (waterway centreline).

CREATION — THE MISSING LINK. Until this round nothing in the repo could make an
area. This file listed and read them; satellite.py filled in one that already had
a name and a bbox; crt.py's `crt-fetch <area>` refused to run without an area to
clip to. So data/areas/ was empty on every card ever built and the console's "no
chart data is downloaded" was true, permanent and nobody's bug. create_area()
below writes exactly the metadata list_areas() already reads, so an area made
automatically from a launch point is indistinguishable downstream from one an
operator drew by hand.

STILL NO NETWORK IN THIS FILE. Creating an area is path arithmetic and one small
JSON write; it is safe on the bank with no DNS. What it produces is a PLAN with a
size on it — the fetch that fills it in is somebody else's job, needs internet,
and is the same bootstrap-time act it always was.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import math
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

from . import satellite as satmod  # tile arithmetic only (count_tiles/estimate) — importing
from .config import EARTH_R, settings

# it touches no network; every URL in it is used lazily

log = logging.getLogger("neptune.nav.areas")

# Metres per degree of latitude. The same flat-earth approximation geo.py uses, and
# exact enough for deciding whether a launch point is 50 m or 5 km from an edge.
_M_PER_DEG_LAT = math.radians(1.0) * EARTH_R

# The four states an area can be in, and the whole reason this file grew a `state`
# field: "the archive exists" was the only signal there was, and an MBTiles file
# appears on disk the moment the FIRST tile lands. A half-downloaded area therefore
# read as present, and a map that looks complete and is not is worse than a map
# that says it is empty.
STATES = ("absent", "downloading", "present", "failed")


def _meta_path(name: str) -> Path:
    return settings.areas_dir / f"{name}.json"


def estimate_size_mb(bbox: list[float], maxzoom: int) -> float:
    """Rough estimate (§6.4: show size before download). Each zoom ~doubles size."""
    minlon, minlat, maxlon, maxlat = bbox
    import math

    span = abs(maxlon - minlon) * abs(maxlat - minlat) * math.cos(math.radians((minlat + maxlat) / 2))
    # empirical-ish: ~0.5 MB per deg² at z14, doubling per level above 14
    base = span * 0.5 * (2 ** max(0, maxzoom - 14))
    return round(max(0.2, base), 1)


def _now_iso() -> str:
    """UTC, in satellite.py's exact format — the two writers share one `created` field."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _age_s(stamp) -> float | None:
    """Seconds since an ISO stamp this file wrote, or None if it cannot be read."""
    if not isinstance(stamp, str):
        return None
    try:
        return max(0.0, time.time() - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def _derive_state(d: dict, archive: Path | None) -> tuple[str, str]:
    """(state, why) for one area — DISK TRUTH FIRST, the record only for what disk
    cannot show.

    Present and absent are facts about the card and are read off it: a metadata file
    claiming "present" after somebody deleted the .mbtiles is not evidence, it is a
    stale note. Downloading and failed are the two the filesystem genuinely cannot
    tell you, so they come from the record — and a "downloading" that has gone quiet
    is reported as FAILED rather than trusted forever, because the process that
    wrote it can be killed by a dying hotspot, a Ctrl-C or a flat battery, and the
    operator who is told a download is still running does not start it again.
    """
    recorded = d.get("state")
    if recorded == "downloading":
        age = _age_s(d.get("state_at"))
        if age is not None and age <= settings.area_state_stale_s:
            return "downloading", d.get("state_why") or "a download is running for this area"
        stalled = f"has reported nothing for {int(age)}s" if age is not None else "carries no progress timestamp"
        return "failed", (
            f"a download was started for this area and {stalled} — it stopped "
            f"without finishing, so whatever is on the card is PARTIAL. Run it "
            f"again while there is internet"
        )
    if recorded == "failed":
        return "failed", d.get("state_why") or "the last download for this area failed"
    if archive is None:
        return "absent", (
            d.get("state_why")
            or "this area has been defined but nothing has been downloaded into it "
            "yet — it needs internet once, before the water"
        )
    if recorded == "absent":
        # TILES ON THE CARD AND THE RECORD STILL SAYS ABSENT. That is not a
        # contradiction, it is an area that GREW: create_area() unions a wider bbox
        # into an existing area when a new launch point sits at its edge, and the
        # imagery for the new margin has not been fetched. Disk truth wins for
        # present-versus-gone, but it cannot see the shape of what is missing, and
        # an area whose box outruns its imagery must never read as complete —
        # `present` and `size` still travel beside this, so nothing is hidden either.
        return "absent", (d.get("state_why") or "this area holds imagery for part of its box only")
    return "present", d.get("state_why") or "the tile archive is on the card"


def list_areas() -> list[dict]:
    settings.areas_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for meta in sorted(settings.areas_dir.glob("*.json")):
        try:
            d = json.loads(meta.read_text())
        except Exception:  # noqa: BLE001
            continue
        name = meta.stem
        mb = settings.areas_dir / f"{name}.mbtiles"  # satellite raster (§3, default)
        pm = settings.areas_dir / f"{name}.pmtiles"  # legacy vector
        archive = mb if mb.exists() else (pm if pm.exists() else None)
        d["name"] = name
        d["size"] = archive.stat().st_size if archive else 0
        d["present"] = archive is not None
        d["format"] = "mbtiles" if mb.exists() else ("pmtiles" if pm.exists() else d.get("format", "?"))
        d["has_centreline"] = (settings.areas_dir / f"{name}.geojson").exists()
        # `present` is a fact about a FILE and it always was; `state` is the answer to
        # the question the operator is actually asking, which is whether this area is
        # ready to fly on. They disagree exactly when it matters — mid-download, and
        # after a download that died — so both travel.
        d["state"], d["state_why"] = _derive_state(d, archive)
        out.append(d)
    return out


def delete_area(name: str) -> None:
    for ext in (".mbtiles", ".pmtiles", ".json", ".geojson"):
        p = settings.areas_dir / f"{name}{ext}"
        if p.exists():
            p.unlink()


def pmtiles_available() -> bool:
    return shutil.which(settings.pmtiles_bin) is not None and bool(settings.pmtiles_source)


async def extract_area(name: str, bbox: list[float], maxzoom: int, progress) -> dict:
    """Run `pmtiles extract` → areas/<name>.pmtiles, streaming progress via the
    async `progress(dict)` callback. Enforces the size cap (§6.4)."""
    settings.areas_dir.mkdir(parents=True, exist_ok=True)
    est = estimate_size_mb(bbox, maxzoom)
    if est > settings.area_size_cap_mb:
        raise ValueError(f"estimated {est} MB exceeds cap {settings.area_size_cap_mb} MB")
    if not pmtiles_available():
        raise RuntimeError(
            "pmtiles unavailable (bootstrap-only: needs the pmtiles binary + a source URL "
            "+ internet). Run area extraction before going isolated."
        )

    out = settings.areas_dir / f"{name}.pmtiles"
    minlon, minlat, maxlon, maxlat = bbox
    cmd = [
        settings.pmtiles_bin,
        "extract",
        settings.pmtiles_source,
        str(out),
        f"--bbox={minlon},{minlat},{maxlon},{maxlat}",
        f"--maxzoom={maxzoom}",
    ]
    await progress({"name": name, "state": "starting", "cmd": " ".join(cmd), "est_mb": est})
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").strip()
        if line:
            await progress({"name": name, "state": "running", "line": line})
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"pmtiles extract exited {rc}")
    meta = {"bbox": bbox, "maxzoom": maxzoom}
    _meta_path(name).write_text(json.dumps(meta))
    await progress({"name": name, "state": "done", "size": out.stat().st_size})
    return {"name": name, **meta, "size": out.stat().st_size}


# =============================================================================
# CREATING AN AREA — from a launch point, or from a box drawn by hand.
#
# Everything below is offline. It decides WHAT to download and writes the small
# JSON that says so; it never opens a socket. The rule the whole subsystem is
# built on survives untouched: downloading is a bootstrap-time act, and the
# canal-side runtime reads a card.
# =============================================================================


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write beside and rename, the lesson crt.py learned the hard way.

    A metadata file is small enough to feel safe and is not: a truncated one is
    unparseable, list_areas() skips it with `continue`, and the area DISAPPEARS
    from the console while its tiles and its hazard layers sit on the card. Write
    a sibling temporary in the SAME directory (a cross-filesystem rename is a
    copy, and a copy is not atomic) and os.replace it into place — atomic on
    POSIX and Windows alike, and this repo runs on both.

    The suffix goes on the END of the full name so a fragment matches neither
    *.json nor *.geojson: nothing globbing areas/ can pick one up as an area.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # BaseException on purpose: KeyboardInterrupt at the bank is the case this
        # exists for, and it must not leave the half-written file where the whole one was.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # nothing to add to whatever is already raising
            pass
        raise


def _read_meta(name: str) -> dict | None:
    p = _meta_path(name)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable is the caller's problem to report
        return None
    return d if isinstance(d, dict) else None


def _merge_meta(name: str, fields: dict) -> dict | None:
    """Read-modify-write ONE area's metadata, keeping every key already in it.

    Never a wholesale rewrite. The bbox is the load-bearing field — crt.py clips
    to it, service.py's readiness gate asks whether it covers the launch point,
    cli.py's dive-locate finds an area by it — and a status update that dropped it
    would silently unmake the area while appearing to succeed.
    """
    cur = _read_meta(name)
    if cur is None:
        return None
    cur.update(fields)
    _atomic_write_json(_meta_path(name), cur)
    return cur


def set_area_state(name: str, state: str, *, why: str | None = None, **extra) -> dict | None:
    """Record what is happening to this area. Returns the new metadata, or None if
    there is no such area (a status update must NEVER call an area into being — an
    area with no bbox is one the tile walk, the hazard clip and the readiness gate
    all trip over).

    CALL IT WHILE THE DOWNLOAD RUNS, not only at each end. `state_at` is a
    heartbeat: list_areas() reports a "downloading" older than
    settings.area_state_stale_s as FAILED, because a fetch killed by a flat
    battery leaves the word "downloading" on the card forever, and an operator who
    is told it is still running does not start it again.

    NOTE FOR THE FETCH DRIVER: satellite.download_area() rewrites <name>.json from
    scratch when it finishes, so `label`, `origin` and `created_by` do not survive
    it. Re-apply them here afterwards — pass them as **extra alongside
    state="present". The bbox DOES survive (satellite.py writes it), which is why
    reuse and idempotence are keyed on the bbox and not on those fields.
    """
    if state not in STATES:
        raise ValueError(f"state {state!r} is not one of {STATES}")
    fields = {"state": state, "state_at": _now_iso(), **extra}
    if why is not None:
        fields["state_why"] = why
    elif "state_why" not in extra:
        # A reason left over from the previous state is worse than none: "the last
        # download failed" sitting on a finished area is a lie with a timestamp.
        fields["state_why"] = None
    return _merge_meta(name, fields)


def zooms_for(detail: str = "standard") -> tuple[int, int]:
    """(zmin, zmax) for a detail level — the same mapping service.py's _zooms() uses.
    'high' adds one level (§4), which roughly quadruples the tile count."""
    return settings.sat_min_zoom, settings.sat_max_zoom + (1 if detail == "high" else 0)


# ---- geometry ---------------------------------------------------------------
def _valid_bbox(b) -> bool:
    """[W,S,E,N], west of east and south of north. The same test crt.py applies before
    it will clip to a bbox; repeated here rather than imported because the serving side
    imports this module and must not be made to import the bootstrap one."""
    try:
        w, s, e, n = (float(v) for v in b)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        return False
    return -180 <= w < e <= 180 and -85.06 <= s < n <= 85.06


def bbox_for_point(lat: float, lon: float, radius_m: float) -> list[float]:
    """The square of half-width `radius_m` centred on a launch point → [W,S,E,N].

    Flat-earth, like geo.py: at canal scale the error is centimetres, and the
    number this feeds is a tile count.
    """
    try:
        lat, lon, radius_m = float(lat), float(lon), float(radius_m)
    except (TypeError, ValueError):
        raise ValueError("launch point and radius must be numbers")
    if not all(math.isfinite(v) for v in (lat, lon, radius_m)):
        raise ValueError("launch point and radius must be finite numbers")
    if radius_m <= 0:
        raise ValueError("radius must be greater than zero")
    if not (-85.0 <= lat <= 85.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"launch point {lat},{lon} is not a position on this planet")
    if abs(lat) < 1e-7 and abs(lon) < 1e-7:
        # A GNSS receiver with no fix, a phone that denied the permission and a
        # zero-initialised struct all report 0,0 — which is open ocean in the Gulf
        # of Guinea. Creating an area there costs a thousand tiles of empty sea and,
        # worse, hands the console a launch point it will happily draw a track from.
        raise ValueError(
            "launch point 0,0 is the null fix, not a place — set a real " "position before creating an area"
        )

    dlat = radius_m / _M_PER_DEG_LAT
    # cos() of a canal latitude is never near zero; the floor only stops a nonsense
    # high latitude from turning into a division by zero before the check below.
    dlon = radius_m / (_M_PER_DEG_LAT * max(0.02, math.cos(math.radians(lat))))
    w, s = lon - dlon, max(-85.06, lat - dlat)
    e, n = lon + dlon, min(85.06, lat + dlat)
    if w < -180.0 or e > 180.0:
        # Crossing the antimeridian needs two boxes and nothing downstream can hold
        # one: tiles_for_bbox() walks x from min to max and crt.py's validator demands
        # w < e, so half the area would silently never be fetched.
        raise ValueError(
            "an area that crosses the antimeridian cannot be expressed as one "
            "bbox; nothing downstream would download the far half"
        )
    return [round(w, 7), round(s, 7), round(e, 7), round(n, 7)]


def _coverage_m(bbox: list[float], lat: float, lon: float) -> float:
    """Metres from a point to the NEAREST edge of a bbox; negative when outside.

    This is the honest measure of "how much map is there around me", which is what
    the reuse rule is really asking — not how close the point is to the middle of
    somebody's rectangle.
    """
    w, s, e, n = bbox
    coslat = math.cos(math.radians(lat))
    return min(
        (lat - s) * _M_PER_DEG_LAT,
        (n - lat) * _M_PER_DEG_LAT,
        (lon - w) * _M_PER_DEG_LAT * coslat,
        (e - lon) * _M_PER_DEG_LAT * coslat,
    )


def _contains_bbox(outer: list[float], inner: list[float]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _union_bbox(a: list[float], b: list[float]) -> list[float]:
    return [round(min(a[0], b[0]), 7), round(min(a[1], b[1]), 7), round(max(a[2], b[2]), 7), round(max(a[3], b[3]), 7)]


def _centre(bbox: list[float]) -> tuple[float, float]:
    """(lat, lon) of a bbox centre — lat first, matching every other pair in nav/."""
    return (bbox[1] + bbox[3]) / 2.0, (bbox[0] + bbox[2]) / 2.0


# ---- naming -----------------------------------------------------------------
# Windows reserves these as device names at every path level, and the dev handheld is
# a Windows box: data/areas/con.json cannot be created there and the failure is a bare
# OSError with nothing in it about names. A canal called "Aux" is unlikely; a
# bootstrap that dies on one is not worth the cost of finding out.
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"] + [f"com{i}" for i in range(1, 10)] + [f"lpt{i}" for i in range(1, 10)]
)


def slugify(text: str, limit: int = 40) -> str:
    """A place name → a filesystem-safe, URL-safe area name, or "" if nothing survives.

    Deliberately STRICTER than crt.py's safe_area_name(), which permits spaces and
    dots: this name becomes a filename on a FAT-formatted SD card, a path segment in
    /api/areas/{name}/tiles/… and a directory under data/crt/. Lowercase ASCII plus
    hyphens is the intersection of all three. Accents are FOLDED rather than dropped,
    so "Pontcysyllte" survives instead of losing letters to a naive ASCII filter.
    """
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:limit].strip("-")
    if s in _RESERVED:
        s = f"{s}-area"
    return s


def _name_taken(name: str) -> bool:
    """Is anything on the card already using this name?

    Every extension, not just the metadata: an operator who deleted a .json to
    "start again" leaves the .mbtiles and the hazard directory behind, and a new area
    reusing that name would silently inherit another place's tiles and another
    place's sluices. data/crt/<name>/ counts for exactly the same reason.
    """
    for ext in (".json", ".mbtiles", ".pmtiles", ".geojson"):
        if (settings.areas_dir / f"{name}{ext}").exists():
            return True
    return (settings.crt_dir / name).exists()


def _unique_name(base: str) -> str:
    """`base`, or the first free `base-2`, `base-3`, … — never a name already in use.

    Two launch points that reverse-geocode to the same street ("bridge-street" is on
    half the network) must not land on top of each other's data.
    """
    if not _name_taken(base):
        return base
    for i in range(2, 100):
        cand = f"{base}-{i}"
        if not _name_taken(cand):
            return cand
    # A hundred areas of one name is not a case worth a clever answer; a stamp ends it.
    return f"{base}-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}"


def default_area_name(label: str | None = None) -> str:
    """The base name for a new area, before collision handling.

    A PLACE NAME WHEN THERE IS ONE. With six areas on a handheld the operator
    searches for where they were — "gas-street", "hatton", "kings-norton" — so a
    reverse-geocoded name (satellite.reverse_geocode, online) is passed in as `label`
    and used verbatim.

    THE DATE WHEN THERE IS NOT. That geocode needs internet, and the case with no
    internet is precisely the case where the area is created empty and filled in
    later, at home, from a list. What identifies it then is WHEN they were there,
    which is also what sorts the list; the exact position and any label that arrives
    later sit in the metadata beside it, so the console can show
    "launch-2026-08-08 · 52.4790, -1.9080". LOCAL date, not UTC: a launch at half past
    midnight BST belongs to the night the operator remembers, not to the day before.

    THE NAME IS PERMANENT once written. The .mbtiles, the .geojson, data/crt/<name>/
    and every dive journal that names an area all hang off it, so a geocode that
    arrives later fills in the `label` field and renames nothing.
    """
    slug = slugify(label) if label else ""
    return slug or f"launch-{time.strftime('%Y-%m-%d', time.localtime())}"


# ---- which existing area, if any, already covers this launch point ----------
def _existing() -> list[dict]:
    """Every area on the card that has a usable bbox. One with a broken bbox is not a
    candidate for reuse OR for extension — it is left strictly alone, because the
    thing it needs is an operator, not a merge."""
    out = []
    for a in list_areas():
        bb = a.get("bbox")
        if _valid_bbox(bb):
            out.append({**a, "bbox": [float(v) for v in bb]})
    return out


def area_for_point(lat: float, lon: float, margin_m: float | None = None) -> dict | None:
    """The area already covering this launch point, or None.

    THE IDEMPOTENCE RULE, in one place so it can be stated in one sentence: an area
    covers a launch point when the point lies inside its bbox with at least
    settings.area_reuse_margin_m of map on EVERY side. Tap the same spot twice and
    the second tap finds the first area. Walk 50 m up the towpath and it still does.
    Stand at the very edge of an old area and it does not — there the map runs out
    just as the sub gets going, and that case grows the area instead of pretending it
    is covered. Another city is inside nobody's bbox and gets its own.

    Ties go to the nearest centre, deterministically. cli.py's dive-locate REFUSES a
    launch point that falls inside two areas rather than guessing which one's hazards
    a dive belongs to, so this must never be the thing that invents a second one.
    """
    margin = settings.area_reuse_margin_m if margin_m is None else float(margin_m)
    covering = [a for a in _existing() if _coverage_m(a["bbox"], lat, lon) >= margin]
    if not covering:
        return None

    def _dist2(a):
        clat, clon = _centre(a["bbox"])
        return (clat - lat) ** 2 + ((clon - lon) * math.cos(math.radians(lat))) ** 2

    return sorted(covering, key=lambda a: (_dist2(a), a["name"]))[0]


# ---- plan, then commit -------------------------------------------------------
def _over_cap(est: dict, vector_mb: float, caps: dict) -> str | None:
    """Which cap this box breaks, phrased for a human, or None.

    TWO ESTIMATES BECAUSE THERE ARE TWO ARCHIVE FORMATS, each with a ceiling that
    already exists in this repo: satellite.estimate() counts raster tiles (the
    default path, sat_tile_cap) and estimate_size_mb() sizes a pmtiles vector
    extract (area_size_cap_mb, the number extract_area enforces already). Whichever
    bites first is the answer. Inventing a third budget here would only produce a
    number nothing else in the repo respects.
    """
    if est["tiles"] > caps["tiles"]:
        return (
            f"{est['tiles']} tiles, past the {caps['tiles']} the polite downloader will "
            f"fetch in one go (about {est['mb']} MB)"
        )
    if est["mb"] > caps["mb"]:
        return f"about {est['mb']} MB of imagery, past the {caps['mb']:.0f} MB cap"
    if vector_mb > caps["mb"]:
        return f"about {vector_mb} MB as a vector extract, past the {caps['mb']:.0f} MB cap"
    return None


def plan_area(
    lat: float | None = None,
    lon: float | None = None,
    *,
    radius_m: float | None = None,
    bbox: list[float] | None = None,
    name: str | None = None,
    label: str | None = None,
    detail: str = "standard",
    zmin: int | None = None,
    zmax: int | None = None,
    reuse: bool = True,
) -> dict:
    """What create_area() WOULD do, with a size on it and nothing written.

    This is the "say what the cap is and make it visible" half: the console can put
    the tile count and the megabytes in front of the operator before anything starts,
    and a refusal comes back as a sentence to read rather than an exception to catch.

    `action` is one of:
      create  — no area covers this point; a new one will be written
      reuse   — an existing area already covers it; NOTHING is written or fetched
      extend  — an existing area reaches the point but only just, so its bbox grows
                to include the new coverage. Its tiles, centreline and hazard layers
                are untouched: extending only ever adds
      refuse  — it would be too big. Nothing is written, and any existing area is
                left exactly as it was

    reuse=False turns off BOTH attaching outcomes — no reuse and no extend — and
    always plans a fresh area under its own name. That is the hand-drawn path: an
    operator who has drawn a box on the map asked for that box, and silently folding
    it into a neighbouring area would hand back something they did not draw. The
    automatic launch-point path leaves it True, which is what keeps a second origin
    in the same pound from making a second overlapping area.
    """
    if bbox is None and (lat is None or lon is None):
        raise ValueError("give a launch point (lat, lon) or an explicit bbox")

    zmin = settings.sat_min_zoom if zmin is None else int(zmin)
    zmax = zooms_for(detail)[1] if zmax is None else int(zmax)
    radius = settings.area_radius_m if radius_m is None else float(radius_m)
    caps = {"radius_m": settings.area_max_radius_m, "tiles": settings.sat_tile_cap, "mb": settings.area_size_cap_mb}

    def _result(action, **kw):
        return {
            "action": action,
            "ok": action != "refuse",
            "zmin": zmin,
            "zmax": zmax,
            "caps": caps,
            "label": label,
            **kw,
        }

    # ---- the box being asked for
    if bbox is not None:
        if not _valid_bbox(bbox):
            raise ValueError(f"{bbox!r} is not a [west, south, east, north] box")
        requested = [round(float(v), 7) for v in bbox]
        point = None
    else:
        if radius > settings.area_max_radius_m:
            # REFUSE, DO NOT CLAMP. Quietly downloading something smaller than what was
            # asked for is how an operator ends up with a map that stops mid-pound, with
            # nothing on screen to say where it will stop.
            return _result(
                "refuse",
                name=None,
                bbox=None,
                requested_bbox=None,
                est_tiles=0,
                est_mb=0.0,
                why=(
                    f"a {radius:.0f} m radius is past the {settings.area_max_radius_m:.0f} m "
                    f"cap on an automatically created area — raise NAV_AREA_MAX_RADIUS_M "
                    f"if you mean it, or draw the box by hand"
                ),
            )
        requested = bbox_for_point(lat, lon, radius)
        point = (float(lat), float(lon))

    existing = _existing()
    # The name the caller asked for, if it asked for one. It is not just the name of a
    # new area: when several existing areas reach this point it says which of them the
    # operator meant, and picking a different one by alphabet would grow the wrong area.
    wanted = slugify(name) if name else None
    if name and not wanted:
        raise ValueError(f"{name!r} leaves nothing usable as an area name")

    # ---- is it already on the card?
    if reuse:
        if point is not None:
            hit = area_for_point(point[0], point[1])
        else:
            # An explicit box is reused only when an existing area holds ALL of it.
            # A margin rule would be guesswork about a box somebody drew on purpose.
            inner = [a for a in existing if _contains_bbox(a["bbox"], requested)]
            hit = sorted(inner, key=lambda a: (a["name"] != wanted, a["name"]))[0] if inner else None
        if hit is not None:
            why = (
                f"{hit['name']} already covers this launch point with "
                f"{_coverage_m(hit['bbox'], point[0], point[1]):.0f} m of map around it"
                if point is not None
                else f"{hit['name']} already contains this box"
            )
            return _result(
                "reuse",
                name=hit["name"],
                bbox=hit["bbox"],
                requested_bbox=requested,
                state=hit.get("state"),
                present=hit.get("present"),
                est_tiles=0,
                est_mb=0.0,
                why=why,
            )

    # ---- does one reach it without covering it? then GROW that one, never overlap it
    #
    # cli.py's dive-locate refuses outright when a dive's launch point falls inside two
    # areas, because it cannot know whose hazards and soundings that dive belongs to.
    # A second overlapping area here would manufacture exactly that ambiguity every
    # time an operator launched 100 m from where they launched last week.
    #
    # WHICH ONE, when more than one reaches the point: the one the caller named if it
    # named one, then the one with the most map around the point — the deepest cover is
    # the one that needs to grow least — and the name last, only so that two equal
    # candidates always resolve the same way twice running.
    probe = point if point is not None else _centre(requested)
    touching = [a for a in existing if _coverage_m(a["bbox"], probe[0], probe[1]) >= 0] if reuse else []
    target = (
        sorted(touching, key=lambda a: (a["name"] != wanted, -_coverage_m(a["bbox"], probe[0], probe[1]), a["name"]))[0]
        if touching
        else None
    )

    if target is not None:
        final = _union_bbox(target["bbox"], requested)
        est = satmod.estimate(final, zmin, zmax)
        vec = estimate_size_mb(final, zmax)
        over = _over_cap(est, vec, caps)
        if over:
            return _result(
                "refuse",
                name=target["name"],
                bbox=target["bbox"],
                requested_bbox=requested,
                est_tiles=est["tiles"],
                est_mb=est["mb"],
                why=(
                    f"extending {target['name']} to reach this launch point would make "
                    f"it {over} — {target['name']} is untouched and still holds "
                    f"everything it held"
                ),
            )
        return _result(
            "extend",
            name=target["name"],
            bbox=final,
            previous_bbox=target["bbox"],
            requested_bbox=requested,
            est_tiles=est["tiles"],
            est_mb=est["mb"],
            vector_est_mb=vec,
            state=target.get("state"),
            why=(
                f"{target['name']} reaches this launch point but only just, so it grows "
                f"to {est['mb']} MB of imagery; nothing it already holds is removed"
            ),
        )

    # ---- a new one
    est = satmod.estimate(requested, zmin, zmax)
    vec = estimate_size_mb(requested, zmax)
    chosen = wanted or default_area_name(label)
    over = _over_cap(est, vec, caps)
    if over:
        return _result(
            "refuse",
            name=None,
            bbox=None,
            requested_bbox=requested,
            est_tiles=est["tiles"],
            est_mb=est["mb"],
            why=f"this area would be {over} — nothing was created",
        )
    return _result(
        "create",
        name=_unique_name(chosen),
        bbox=requested,
        requested_bbox=requested,
        est_tiles=est["tiles"],
        est_mb=est["mb"],
        vector_est_mb=vec,
        origin=({"lat": point[0], "lon": point[1], "radius_m": radius} if point is not None else None),
        why=(
            f"{est['tiles']} tiles, about {est['mb']} MB at zoom {zmin}-{zmax} "
            f"(cap {caps['tiles']} tiles / {caps['mb']:.0f} MB)"
        ),
    )


def create_area(
    lat: float | None = None,
    lon: float | None = None,
    *,
    radius_m: float | None = None,
    bbox: list[float] | None = None,
    name: str | None = None,
    label: str | None = None,
    detail: str = "standard",
    zmin: int | None = None,
    zmax: int | None = None,
    reuse: bool = True,
) -> dict:
    """Define an offline area from a launch point (+ radius) or from an explicit bbox.

    Writes areas/<name>.json in exactly the shape list_areas() reads and satellite.py
    writes, so an area created here is indistinguishable downstream from a hand-made
    one: crt.py will clip hazards to its bbox, cli.py's dive-locate will find dives
    inside it, the readiness gate will ask whether it covers the launch point. NO
    TILES ARE FETCHED HERE and nothing is opened but a file — the area starts in
    state "absent", which is the honest description of a plan.

    NOTHING IS EVER DESTROYED. The only three outcomes that touch the card are: write
    a new metadata file under a name nothing else uses; merge a WIDER bbox into an
    existing one, leaving its tiles, centreline and hazard layers where they are; or
    write nothing at all. No path through this function deletes, truncates or
    rewrites another area's data — the CRT sweep learned that lesson by wiping a
    complete 26-layer hazard card and reporting success.

    Raises ValueError when the input is not a place, or when the result would be too
    big; call plan_area() first if you would rather show the refusal than raise it.
    """
    plan = plan_area(
        lat, lon, radius_m=radius_m, bbox=bbox, name=name, label=label, detail=detail, zmin=zmin, zmax=zmax, reuse=reuse
    )
    if plan["action"] == "refuse":
        raise ValueError(plan["why"])

    settings.areas_dir.mkdir(parents=True, exist_ok=True)
    now = _now_iso()

    if plan["action"] == "reuse":
        # THE IDEMPOTENT PATH, and it does not so much as touch an mtime. Re-setting
        # the origin where it already was has to be free — not cheap, free — because
        # the console does it on every phone fix, and a card rewritten each time is an
        # SD card worn out for no new information.
        log.info("area %s already covers %s,%s — reusing it", plan["name"], lat, lon)
        return {**plan, "created": False}

    if plan["action"] == "extend":
        merged = _merge_meta(
            plan["name"],
            {
                "bbox": plan["bbox"],
                "extended_at": now,
                "extended_from": plan["previous_bbox"],
                # A GROWN AREA IS AN INCOMPLETE AREA and must say so, even when it was
                # complete a moment ago. Its box now reaches water its imagery does not,
                # and leaving "present" on it is precisely the "looks finished, is not"
                # failure this field was added to end — the pre-dive gate would go green
                # over a map that stops short of where the sub is going in. The tiles it
                # already holds are untouched and `present`/`size` still report them; what
                # changes is the claim about completeness, which is now false.
                "state": "absent",
                "state_at": now,
                "state_why": (
                    f"this area was grown to reach a new launch point and holds imagery "
                    f"for the previous box {plan['previous_bbox']} only — the new margin "
                    f"has not been downloaded"
                ),
            },
        )
        if merged is None:
            raise ValueError(f"area {plan['name']} vanished between planning and writing")
        log.info("area %s extended %s -> %s", plan["name"], plan["previous_bbox"], plan["bbox"])
        return {**plan, "created": False, "meta": merged}

    # ---- create
    meta = {
        "bbox": plan["bbox"],
        "minzoom": plan["zmin"],
        "maxzoom": plan["zmax"],
        "format": "mbtiles",  # the default archive (§3); satellite.py fills it in
        "attribution": settings.sat_attribution,
        "created": now,
        "created_by": "launch-point" if plan.get("origin") else "bbox",
        # The estimate, under names that cannot be mistaken for a measurement.
        # satellite.py writes tiles_ok/tiles_total once it has actually counted; a
        # tiles_total written HERE would be a forecast wearing a result's clothes.
        "est_tiles": plan["est_tiles"],
        "est_mb": plan["est_mb"],
        "state": "absent",
        "state_at": now,
        "state_why": "defined from a launch point; no imagery has been downloaded into it yet",
    }
    if plan.get("origin"):
        meta["origin"] = plan["origin"]  # where the operator actually stood
    if label:
        meta["label"] = label  # human title; the name never changes, this may
    target = _meta_path(plan["name"])
    if target.exists():
        # _unique_name() said this was free, so something wrote it in between. The file
        # on disk wins: an area is somebody's downloaded data and this is a plan.
        raise ValueError(f"{target.name} appeared while it was being planned")
    _atomic_write_json(target, meta)
    log.info("area %s created %s (%s tiles, ~%s MB)", plan["name"], plan["bbox"], plan["est_tiles"], plan["est_mb"])
    return {**plan, "created": True, "meta": meta}

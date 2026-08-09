"""Satellite raster-tile area downloader (spec §3/§4).

Walks the XYZ tile pyramid for a bbox, fetches imagery (Esri World Imagery by
default — note the {z}/{y}/{x} order), and writes an MBTiles (SQLite) archive the
Pi serves back to the radar. Rate-limited with backoff + a real User-Agent so a
naive parallel fetch of a thousand tiles doesn't get us blocked (§3.2).

Also (best-effort, online only): a waterway centreline via Overpass (§3.5, the
snapping target) and a reverse-geocoded area name via Nominatim (§4).

Pure stdlib (urllib + sqlite3) — no third-party deps. All network calls go
through the module-level _http_get, which tests monkeypatch to run offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import settings

log = logging.getLogger("neptune.nav.satellite")


# ---- tile math (Web Mercator / slippy map) ---------------------------------
def deg2num(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_for_bbox(bbox: list[float], zmin: int, zmax: int):
    """Yield (z, x, y) for every tile covering bbox=[minlon,minlat,maxlon,maxlat]."""
    minlon, minlat, maxlon, maxlat = bbox
    for z in range(zmin, zmax + 1):
        xa, ya = deg2num(minlat, minlon, z)  # bottom-left: x small, y large
        xb, yb = deg2num(maxlat, maxlon, z)  # top-right:   x large, y small
        for x in range(min(xa, xb), max(xa, xb) + 1):
            for y in range(min(ya, yb), max(ya, yb) + 1):
                yield z, x, y


def count_tiles(bbox: list[float], zmin: int, zmax: int) -> int:
    minlon, minlat, maxlon, maxlat = bbox
    total = 0
    for z in range(zmin, zmax + 1):
        xa, ya = deg2num(minlat, minlon, z)
        xb, yb = deg2num(maxlat, maxlon, z)
        total += (abs(xb - xa) + 1) * (abs(yb - ya) + 1)
    return total


def estimate(bbox: list[float], zmin: int, zmax: int) -> dict:
    n = count_tiles(bbox, zmin, zmax)
    mb = round(n * settings.sat_avg_kb / 1024.0, 1)
    return {"tiles": n, "mb": mb, "zmin": zmin, "zmax": zmax}


# ---- HTTP (stdlib; monkeypatched in tests) ---------------------------------
def _http_get(url: str, timeout: float = 20.0) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": settings.sat_user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted config URL)
        return r.read()


async def _fetch_retry(url: str, tries: int = 3) -> bytes | None:
    """Fetch with exponential backoff. Returns None on final failure (the tile is
    left missing — the client overzooms from a parent rather than showing a hole)."""
    for attempt in range(tries):
        try:
            return await asyncio.to_thread(_http_get, url)
        except Exception as exc:  # noqa: BLE001
            if attempt == tries - 1:
                log.warning("tile fetch failed (%s): %s", url, exc)
                return None
            await asyncio.sleep(0.5 * (2**attempt))
    return None


def _tile_url(z: int, x: int, y: int) -> str:
    return settings.sat_tile_url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))


# ---- MBTiles ----------------------------------------------------------------
def _mbtiles_path(name: str) -> Path:
    return settings.areas_dir / f"{name}.mbtiles"


def _init_mbtiles(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE TABLE IF NOT EXISTS metadata (name text, value text);"
        "CREATE TABLE IF NOT EXISTS tiles (zoom_level integer, tile_column integer,"
        " tile_row integer, tile_data blob);"
        "CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles"
        " (zoom_level, tile_column, tile_row);"
    )


def read_tile(name: str, z: int, x: int, y: int) -> bytes | None:
    """Read one tile back. MBTiles stores rows TMS-flipped (y from the bottom)."""
    path = _mbtiles_path(name)
    if not path.exists():
        return None
    tms_y = (1 << z) - 1 - y
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone()
        return bytes(row[0]) if row else None
    finally:
        con.close()


# ---- online extras (best-effort) -------------------------------------------
async def reverse_geocode(lat: float, lon: float) -> str | None:
    """Human-readable area name (§4). Online only; None on any failure."""
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16})
    url = f"{settings.nominatim_url}/reverse?{q}"
    raw = await _fetch_retry(url, tries=1)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        a = d.get("address", {})
        parts = [a.get(k) for k in ("waterway", "road", "suburb", "village", "town", "city")]
        parts = [p for p in parts if p]
        return (parts[0] if parts else d.get("name") or d.get("display_name", "").split(",")[0]) or None
    except Exception:  # noqa: BLE001
        return None


async def fetch_centreline(bbox: list[float]) -> dict | None:
    """Waterway centreline as GeoJSON via Overpass (§3.5). Online only; None on failure."""
    minlon, minlat, maxlon, maxlat = bbox
    ql = (
        f"[out:json][timeout:25];"
        f'(way["waterway"~"canal|river|stream|ditch|drain"]'
        f"({minlat},{minlon},{maxlat},{maxlon}););out geom;"
    )
    url = f"{settings.overpass_url}?{urllib.parse.urlencode({'data': ql})}"
    raw = await _fetch_retry(url, tries=1)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    feats = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if el.get("type") == "way" and geom:
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"waterway": el.get("tags", {}).get("waterway")},
                    "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in geom]},
                }
            )
    return {"type": "FeatureCollection", "features": feats} if feats else None


# ---- the download job -------------------------------------------------------
async def download_area(name: str, bbox: list[float], zmin: int, zmax: int, progress, refresh: bool = False) -> dict:
    """Fetch the imagery pyramid → areas/<name>.mbtiles, streaming progress via the
    async progress(dict) callback. Enforces the tile cap and rate limit (§3.2).

    RESUMES BY DEFAULT. A tile already in the archive is not asked for again, because
    the situation this runs in is a hotspot at a canal that has already dropped once:
    the retry used to start at tile one and re-request the lot — measured at 981
    requests for an area that already had tiles on the card — which is slow, rude to a
    free public service, and on a metered connection expensive. `refresh=True` is the
    way to deliberately re-fetch imagery that has gone stale."""
    settings.areas_dir.mkdir(parents=True, exist_ok=True)
    est = estimate(bbox, zmin, zmax)
    if est["tiles"] > settings.sat_tile_cap:
        raise ValueError(f"{est['tiles']} tiles exceeds cap {settings.sat_tile_cap} — shrink the area or lower detail")

    path = _mbtiles_path(name)
    con = sqlite3.connect(path)
    _init_mbtiles(con)
    tiles = list(tiles_for_bbox(bbox, zmin, zmax))
    total = len(tiles)
    # WHAT IS ALREADY HERE, read once. Asked per tile this would be a query per
    # request; asked not at all — which is what it was — every resume starts from the
    # beginning. mbtiles rows are keyed on the TMS row, so the comparison happens in
    # that coordinate system rather than converting the whole archive back.
    have: set[tuple[int, int, int]] = set()
    if not refresh:
        try:
            have = {(z, x, r) for z, x, r in con.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")}
        except Exception as exc:  # noqa: BLE001 — a resume that cannot read is a full fetch
            log.warning("could not read the existing tiles for %s (%s); fetching all", name, exc)
    await progress({"name": name, "state": "starting", "total": total, "est_mb": est["mb"], "already": len(have)})

    delay = 1.0 / max(0.5, settings.sat_rate_per_s)
    ok = skipped = 0
    # EVERY TILE THAT ARRIVES IS KEPT, even if the next one kills the download. The
    # commit used to happen only every 25 tiles, so a hotspot that dropped after five
    # left FOUR of them in an uncommitted transaction that sqlite then rolled back —
    # the archive kept one tile out of five, and the resume this whole path exists for
    # had almost nothing to resume from. Committing per tile on a local eMMC costs
    # microseconds against a network fetch that costs hundreds of milliseconds; the
    # rate limit dwarfs it either way.
    try:
        for i, (z, x, y) in enumerate(tiles):
            tms_y = (1 << z) - 1 - y
            if (z, x, tms_y) in have:
                skipped += 1
                ok += 1  # it IS on the card; that is what ok counts
                continue  # no request, and no rate-limit sleep either
            data = await _fetch_retry(_tile_url(z, x, y))
            if data:
                con.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", (z, x, tms_y, data))
                con.commit()  # this tile is now survivable
                ok += 1
            if i % 25 == 0:
                await progress(
                    {"name": name, "state": "running", "done": i + 1, "total": total, "ok": ok, "skipped": skipped}
                )
            await asyncio.sleep(delay)
    finally:
        # Whatever happened — a dropped hotspot, a cancel, a cap — what arrived is on
        # the card before this returns or raises.
        try:
            con.commit()
        except Exception:  # noqa: BLE001
            pass

    # waterway centreline (§3.5) + auto-name (§4) — best-effort, don't fail the download
    centre = await fetch_centreline(bbox)
    if centre:
        (settings.areas_dir / f"{name}.geojson").write_text(json.dumps(centre))

    size = path.stat().st_size
    for k, v in {
        "name": name,
        "format": "jpg",
        "type": "baselayer",
        "minzoom": str(zmin),
        "maxzoom": str(zmax),
        "bounds": ",".join(str(b) for b in bbox),
        "attribution": settings.sat_attribution,
    }.items():
        con.execute("INSERT INTO metadata VALUES (?,?)", (k, v))
    con.commit()
    con.close()

    meta = {
        "bbox": bbox,
        "minzoom": zmin,
        "maxzoom": zmax,
        "format": "mbtiles",
        "tiles_ok": ok,
        "tiles_total": total,
        "has_centreline": bool(centre),
        "attribution": settings.sat_attribution,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (settings.areas_dir / f"{name}.json").write_text(json.dumps(meta))
    await progress({"name": name, "state": "done", "size": size, "ok": ok, "total": total})
    return {"name": name, "size": size, **meta}

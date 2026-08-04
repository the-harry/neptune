"""Offline area management (spec §6.4) + the `pmtiles extract` job runner (§6.1).

Extraction is a BOOTSTRAP-time operation (needs internet + the pmtiles binary).
In the isolated segment it's unavailable — this reports that cleanly instead of
hanging. Areas live as areas/<name>.pmtiles + areas/<name>.json (metadata) +
optional areas/<name>.geojson (waterway centreline).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from .config import settings

log = logging.getLogger("neptune.nav.areas")


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


def list_areas() -> list[dict]:
    settings.areas_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for meta in sorted(settings.areas_dir.glob("*.json")):
        try:
            d = json.loads(meta.read_text())
        except Exception:  # noqa: BLE001
            continue
        name = meta.stem
        mb = settings.areas_dir / f"{name}.mbtiles"       # satellite raster (§3, default)
        pm = settings.areas_dir / f"{name}.pmtiles"       # legacy vector
        archive = mb if mb.exists() else (pm if pm.exists() else None)
        d["name"] = name
        d["size"] = archive.stat().st_size if archive else 0
        d["present"] = archive is not None
        d["format"] = "mbtiles" if mb.exists() else ("pmtiles" if pm.exists() else d.get("format", "?"))
        d["has_centreline"] = (settings.areas_dir / f"{name}.geojson").exists()
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
        raise RuntimeError("pmtiles unavailable (bootstrap-only: needs the pmtiles binary + a source URL "
                           "+ internet). Run area extraction before going isolated.")

    out = settings.areas_dir / f"{name}.pmtiles"
    minlon, minlat, maxlon, maxlat = bbox
    cmd = [settings.pmtiles_bin, "extract", settings.pmtiles_source, str(out),
           f"--bbox={minlon},{minlat},{maxlon},{maxlat}", f"--maxzoom={maxzoom}"]
    await progress({"name": name, "state": "starting", "cmd": " ".join(cmd), "est_mb": est})
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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

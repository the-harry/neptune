"""Navigation service (spec §10.1) — sensor ingest, dead reckoning, snapping,
dive logging — over REST + a nav WebSocket. Plus the area manager (§10.2) and the
readiness check (§9). Mounts into the existing FastAPI app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlencode as _urlencode

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import areas as areamod
from . import satellite as satmod
from .config import settings
from .deadreckoning import DeadReckoner
from .divelog import DiveLog
from .models import Adjustment, FlowVector, NavState, Origin, ReadinessItem, ReadinessResult
from .sensors import get_sensor_source
from .speedlut import DEFAULT_LUT, SpeedLUT

log = logging.getLogger("neptune.nav")


class NavService:
    def __init__(self, get_rov=None) -> None:
        # get_rov lets navigation read the LIVE vehicle (heading/depth/throttle) instead
        # of a scripted path. Without it - e.g. nav running standalone - it falls back
        # to the simulator, which is fine there and wrong when a real vehicle exists.
        self._get_rov = get_rov
        self.sensors = get_sensor_source(get_rov)
        self.origin: Origin | None = None
        self.flow = FlowVector()
        self.speed_lut: SpeedLUT = DEFAULT_LUT
        self.dr: DeadReckoner | None = None
        self.dive: DiveLog | None = None
        self.last_state: NavState | None = None
        self.last_sample = None            # latest raw sensor sample (heading0/IMU cal, even with no dive)
        self.active_area: str | None = None
        self.centreline: list[tuple[float, float]] | None = None   # [lon,lat]
        self._subs: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        for d in (settings.data_dir, settings.areas_dir, settings.dives_dir, settings.speed_lut_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._recover_orphans()
        self._task = asyncio.create_task(self._loop())
        log.info("nav service started (sensors=%s, autolog=%s)",
                 "sim" if self.sensors.is_sim else "real", settings.autolog)

    def _recover_orphans(self) -> None:
        """Turn journals with no finished GeoJSON into readable dives.

        A .jsonl with no matching .geojson means the process died mid-dive - a crash,
        a power cut, a pulled plug. That is precisely the dive worth keeping, so it is
        rebuilt on the next start rather than left as an unreadable fragment.
        """
        try:
            for jf in sorted(settings.dives_dir.glob("dive-*.jsonl")):
                gf = jf.with_suffix(".geojson")
                if gf.exists():
                    continue
                try:
                    feat = _feature_from_journal(jf)
                    if feat is None:
                        continue
                    gf.write_text(json.dumps(feat, indent=2))
                    log.warning("recovered an unfinished dive from %s (%d samples)",
                                jf.name, len(feat.get("samples", [])))
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not recover %s: %s", jf.name, exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("dive recovery scan failed: %s", exc)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- dead-reckoning loop ---------------------------------------------
    async def _loop(self) -> None:
        dt = 1.0 / settings.dr_hz
        bcast_every = max(1, round(settings.dr_hz / settings.broadcast_hz))
        i = 0
        while True:
            # SAFETY: a navigation log is not something to remember to switch on. The
            # moment an origin exists there is a position to record, so record it -
            # unasked, every session. A dive nobody logged is a dive nobody can review.
            if (settings.autolog and self.dive is None and self.origin is not None):
                try:
                    self.start_dive(auto=True)
                except Exception as exc:  # noqa: BLE001 — never let logging stop navigation
                    log.warning("auto dive log could not start: %s", exc)
            s = self.sensors.read(dt)
            if s is not None:
                self.last_sample = s          # IMU heading/cal available even without a dive
            if s is not None and self.dr is not None:
                ns = self.dr.update(s)
                self.last_state = ns
                if self.dive is not None:
                    self.dive.add(ns)
                if i % bcast_every == 0 and self._subs:               # decouple redraw from DR rate (§7.5)
                    await self._broadcast(json.dumps({"type": "nav", **ns.model_dump()}))
            i += 1
            await asyncio.sleep(dt)

    async def _broadcast(self, text: str) -> None:
        for ws in list(self._subs):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                self._subs.discard(ws)

    # ---- origin / dive ----------------------------------------------------
    def set_origin(self, o: Origin) -> None:
        self.origin = o

    def start_dive(self, auto: bool = False) -> str:
        if not self.origin:
            raise ValueError("no origin set")
        if self.dive is not None:                 # an explicit start supersedes the auto log
            self.stop_dive()
        dive_id = "dive-" + time.strftime("%Y%m%d-%H%M%S")
        self.sensors.reset()
        self.dr = DeadReckoner(self.origin, self.speed_lut, self.flow,
                               centreline_lonlat=self.centreline)
        self.dive = DiveLog(dive_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            self.origin, self.speed_lut.id, self.flow,
                            directory=settings.dives_dir, auto=auto)
        log.info("dive started: %s%s", dive_id, " (automatic)" if auto else "")
        return dive_id

    def stop_dive(self):
        if not self.dive:
            return None
        path = self.dive.save(settings.dives_dir)
        feat = self.dive.to_feature()
        log.info("dive saved: %s (%d samples)", path.name, self.dive.count)
        self.dive = None
        self.dr = None
        return {"file": str(path), "feature": feat}

    def activate_area(self, name: str) -> None:
        self.active_area = name
        geo = settings.areas_dir / f"{name}.geojson"
        self.centreline = None
        if geo.exists():
            try:
                self.centreline = _centreline_from_geojson(json.loads(geo.read_text()))
            except Exception as exc:  # noqa: BLE001
                log.warning("centreline parse failed for %s: %s", name, exc)

    # ---- readiness (§9) ---------------------------------------------------
    def readiness(self) -> ReadinessResult:
        items: list[ReadinessItem] = []

        def add(step, ok, detail=""):
            items.append(ReadinessItem(step=step, ok=ok, detail=detail))

        area_meta = next((a for a in areamod.list_areas() if a["name"] == self.active_area), None)
        # 1 basemap present + covers launch point
        covers = False
        if area_meta and area_meta.get("present"):
            bb = area_meta.get("bbox")
            if bb and self.origin:
                covers = bb[0] <= self.origin.lon <= bb[2] and bb[1] <= self.origin.lat <= bb[3]
        add("basemap present + covers launch", bool(area_meta and area_meta.get("present") and covers),
            f"area={self.active_area}")
        # 2 centreline cached or snapping off
        add("waterway centreline cached (or snapping off)",
            bool(self.centreline) or not settings.snapping_enabled,
            "centreline loaded" if self.centreline else "snapping disabled")
        # 3 origin + accuracy
        add("origin set within accuracy threshold",
            bool(self.origin) and (self.origin.accuracy <= settings.max_origin_accuracy_m if self.origin else False),
            f"accuracy={self.origin.accuracy}m ≤ {settings.max_origin_accuracy_m}m" if self.origin else "no origin")
        # 4 heading0 + IMU cal
        mag_cal = self.last_sample.mag_cal if self.last_sample else None
        add("heading0 captured + IMU cal good", bool(self.origin) and (mag_cal or 0) >= 2,
            f"mag_cal={mag_cal}")
        # 5 clock sane (RTC or bootstrap-set)
        add("system clock sane", time.time() > 1_700_000_000, time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
        # 6 speed LUT
        add("speed LUT loaded", self.speed_lut is not None, f"lut={self.speed_lut.id}")
        # 9 tether encoder zeroed
        add("tether encoder zeroed at launch",
            self.last_state is None or self.last_state.payout_m < 1.0,
            f"payout={self.last_state.payout_m if self.last_state else 0}m")
        # 7,8 camera preflight + video — cross-subsystem, checked by the camera plane; noted here
        add("camera pre-flight + video (see camera plane)", True, "run /api/preflight separately")
        passed = all(x.ok for x in items)
        return ReadinessResult(passed=passed, items=items)


def _centreline_from_geojson(gj: dict) -> list[tuple[float, float]]:
    """Flatten waterway LineString/MultiLineString coords into one [lon,lat] polyline."""
    coords: list[tuple[float, float]] = []

    def walk(g):
        t = g.get("type")
        if t == "LineString":
            coords.extend((c[0], c[1]) for c in g["coordinates"])
        elif t == "MultiLineString":
            for line in g["coordinates"]:
                coords.extend((c[0], c[1]) for c in line)
        elif t == "Feature":
            walk(g["geometry"])
        elif t == "FeatureCollection":
            for f in g["features"]:
                walk(f)

    walk(gj)
    return coords


# ==========================================================================
def _feature_from_journal(path):
    """Rebuild a dive Feature from an append-only .jsonl journal.

    Tolerant on purpose: the last line of a journal from a crashed process is very
    often truncated mid-write, and that must not cost the whole dive. Bad lines are
    skipped, everything readable is kept.
    """
    from .geo import to_latlon
    header, samples = None, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001 — truncated tail; keep what we have
                continue
            kind = rec.get("type")
            if kind == "header":
                header = rec
            elif kind == "s":
                samples.append(rec)
    if header is None or not samples:
        return None
    o = header.get("origin") or {}
    olat, olon = o.get("lat"), o.get("lon")
    coords, out = [], []
    for smp in samples:
        if olat is not None and olon is not None:
            lat, lon = to_latlon(smp.get("x", 0.0), smp.get("y", 0.0), olat, olon)
            coords.append([round(lon, 7), round(lat, 7)])
        out.append({"t": smp.get("t"), "depth_m": smp.get("depth_m"),
                    "heading_deg": smp.get("heading_deg"),
                    "snapped": smp.get("snapped"), "confidence": smp.get("confidence")})
    return {
        "type": "Feature",
        "properties": {
            "dive_id": header.get("dive_id"), "started_at": header.get("started_at"),
            "speed_lut_id": header.get("speed_lut_id"), "auto": header.get("auto", False),
            "recovered": True,          # this dive never got a clean stop
            "samples": len(out),
        },
        "geometry": {"type": "LineString", "coordinates": coords},
        "samples": out,
    }

def build_router(svc: NavService) -> APIRouter:
    r = APIRouter()

    @r.post("/api/origin")
    async def set_origin(o: Origin, override: bool = False):
        if o.accuracy > settings.max_origin_accuracy_m and not override:
            raise HTTPException(422, f"origin accuracy {o.accuracy}m exceeds {settings.max_origin_accuracy_m}m "
                                     f"— re-fix or pass ?override=true")
        # heading0 is the sub's IMU yaw at this instant (§4.4) — authoritative over any posted value
        if svc.last_sample is not None:
            o.heading_deg = round(svc.last_sample.heading_deg, 1)
        svc.set_origin(o)
        return {"ok": True, "origin": o.model_dump()}

    @r.get("/api/origin")
    async def get_origin():
        return svc.origin.model_dump() if svc.origin else JSONResponse({"set": False})

    @r.get("/api/nav/state")
    async def nav_state():
        if not svc.last_state:
            return JSONResponse({"has_state": False, "has_origin": bool(svc.origin)})
        return svc.last_state.model_dump()

    @r.post("/api/nav/flow")
    async def set_flow(f: FlowVector):
        svc.flow = f
        if svc.dr:
            svc.dr.current = f
        return {"ok": True, "flow": f.model_dump()}

    @r.post("/api/nav/dive/start")
    async def dive_start():
        try:
            return {"dive_id": svc.start_dive()}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/api/nav/dive/stop")
    async def dive_stop():
        res = svc.stop_dive()
        if not res:
            raise HTTPException(400, "no active dive")
        return res

    @r.get("/api/nav/dive/current")
    async def dive_current():
        if not svc.dive:
            raise HTTPException(404, "no active dive")
        return svc.dive.to_feature()

    @r.post("/api/nav/dive/current/adjust")
    async def adjust_current(a: Adjustment):
        if not svc.dive:
            raise HTTPException(404, "no active dive")
        svc.dive.set_adjustment(a)          # applied to output only; raw untouched (§4.5)
        return {"ok": True, "adjustment": a.model_dump()}

    @r.get("/api/nav/dives")
    async def list_dives():
        return sorted(p.name for p in settings.dives_dir.glob("*.geojson"))

    # ---- areas (§3/§4): satellite raster download → MBTiles ----
    def _zooms(detail: str) -> tuple[int, int]:
        # 'standard' → zmin..zmax (z18); 'high' adds one level (z19) (§4)
        zmax = settings.sat_max_zoom + (1 if detail == "high" else 0)
        return settings.sat_min_zoom, zmax

    def _safe_name(s: str) -> str:
        s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (s or "").strip()).strip("-")
        return s[:48] or "area"

    @r.get("/api/areas")
    async def get_areas():
        return {"areas": areamod.list_areas(), "extractor_available": areamod.pmtiles_available()}

    @r.post("/api/areas/estimate")
    async def area_estimate(payload: dict = Body(...)):
        zmin, zmax = _zooms(payload.get("detail", "standard"))
        return satmod.estimate(payload["bbox"], payload.get("zmin", zmin), payload.get("zmax", zmax))

    @r.post("/api/areas")
    async def area_create(payload: dict = Body(...)):
        bbox = payload["bbox"]
        zmin, zmax = _zooms(payload.get("detail", "standard"))
        name = payload.get("name")
        if not name:                                    # §4 — auto-name (reverse geocode, else coords)
            gc = await satmod.reverse_geocode((bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2)
            name = gc or f"{(bbox[1]+bbox[3])/2:.4f}_{(bbox[0]+bbox[2])/2:.4f}"
        name = _safe_name(name)

        async def progress(p):
            await svc._broadcast(json.dumps({"type": "area_progress", **p}))
        try:
            return await satmod.download_area(name, bbox, zmin, zmax, progress)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))

    @r.get("/api/areas/{name}/tiles/{z}/{x}/{y}.jpg")
    async def area_tile(name: str, z: int, x: int, y: int):
        data = satmod.read_tile(name, z, x, y)
        if data is None:
            raise HTTPException(404, "tile not in area")   # client overzooms from a parent (§3.4)
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})

    @r.get("/api/areas/{name}/thumb")
    async def area_thumb(name: str):
        meta = next((a for a in areamod.list_areas() if a["name"] == name), None)
        if not meta or not meta.get("bbox"):
            raise HTTPException(404, "no area")
        bb, z = meta["bbox"], int(meta.get("minzoom", settings.sat_min_zoom))
        cx, cy = satmod.deg2num((bb[1] + bb[3]) / 2, (bb[0] + bb[2]) / 2, z)
        data = satmod.read_tile(name, z, cx, cy)
        if data is None:
            raise HTTPException(404, "no thumbnail")
        return Response(content=data, media_type="image/jpeg")

    @r.get("/api/areas/{name}/centreline")
    async def area_centreline(name: str):
        p = settings.areas_dir / f"{name}.geojson"
        if not p.exists():
            return {"type": "FeatureCollection", "features": []}
        return json.loads(p.read_text())

    @r.delete("/api/areas/{name}")
    async def area_delete(name: str):
        areamod.delete_area(name)
        if svc.active_area == name:
            svc.active_area = None
            svc.centreline = None
        return {"deleted": name}

    # ---- geocoding (§4) — online only, best-effort ----
    @r.get("/api/geocode/reverse")
    async def geocode_reverse(lat: float, lon: float):
        return {"name": await satmod.reverse_geocode(lat, lon)}

    @r.get("/api/geocode/search")
    async def geocode_search(q: str):
        raw = await satmod._fetch_retry(
            f"{settings.nominatim_url}/search?" + _urlencode({"q": q, "format": "jsonv2", "limit": 5}), tries=1)
        if not raw:
            return {"results": []}
        try:
            items = json.loads(raw)
            return {"results": [{"name": it.get("display_name"),
                                 "lat": float(it["lat"]), "lon": float(it["lon"])} for it in items]}
        except Exception:  # noqa: BLE001
            return {"results": []}

    @r.post("/api/areas/{name}/activate")
    async def area_activate(name: str):
        svc.activate_area(name)
        return {"active": name, "has_centreline": bool(svc.centreline)}

    @r.get("/api/readiness")
    async def readiness():
        return svc.readiness().model_dump()

    @r.websocket("/ws/nav")
    async def ws_nav(ws: WebSocket):
        await ws.accept()
        svc._subs.add(ws)
        try:
            if svc.last_state:
                await ws.send_text(json.dumps({"type": "nav", **svc.last_state.model_dump()}))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            svc._subs.discard(ws)

    return r

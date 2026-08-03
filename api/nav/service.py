"""Navigation service (spec §10.1) — sensor ingest, dead reckoning, snapping,
dive logging — over REST + a nav WebSocket. Plus the area manager (§10.2) and the
readiness check (§9). Mounts into the existing FastAPI app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import areas as areamod
from .config import settings
from .deadreckoning import DeadReckoner
from .divelog import DiveLog
from .models import Adjustment, FlowVector, NavState, Origin, ReadinessItem, ReadinessResult
from .sensors import get_sensor_source
from .speedlut import DEFAULT_LUT, SpeedLUT

log = logging.getLogger("neptune.nav")


class NavService:
    def __init__(self) -> None:
        self.sensors = get_sensor_source()
        self.origin: Origin | None = None
        self.flow = FlowVector()
        self.speed_lut: SpeedLUT = DEFAULT_LUT
        self.dr: DeadReckoner | None = None
        self.dive: DiveLog | None = None
        self.last_state: NavState | None = None
        self.active_area: str | None = None
        self.centreline: list[tuple[float, float]] | None = None   # [lon,lat]
        self._subs: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        for d in (settings.data_dir, settings.areas_dir, settings.dives_dir, settings.speed_lut_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())
        log.info("nav service started (sensors=%s)", "sim" if self.sensors.is_sim else "real")

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
            s = self.sensors.read(dt)
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

    def start_dive(self) -> str:
        if not self.origin:
            raise ValueError("no origin set")
        dive_id = "dive-" + time.strftime("%Y%m%d-%H%M%S")
        self.sensors.reset()
        self.dr = DeadReckoner(self.origin, self.speed_lut, self.flow,
                               centreline_lonlat=self.centreline)
        self.dive = DiveLog(dive_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            self.origin, self.speed_lut.id, self.flow)
        log.info("dive started: %s", dive_id)
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
        mag_ok = (self.last_state.mag_cal >= 2) if self.last_state else False
        add("heading0 captured + IMU cal good", bool(self.origin) and mag_ok,
            f"mag_cal={self.last_state.mag_cal if self.last_state else '?'}")
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
def build_router(svc: NavService) -> APIRouter:
    r = APIRouter()

    @r.post("/api/origin")
    async def set_origin(o: Origin, override: bool = False):
        if o.accuracy > settings.max_origin_accuracy_m and not override:
            raise HTTPException(422, f"origin accuracy {o.accuracy}m exceeds {settings.max_origin_accuracy_m}m "
                                     f"— re-fix or pass ?override=true")
        svc.set_origin(o)
        return {"ok": True, "origin": o.model_dump()}

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

    # ---- areas (§6.4) ----
    @r.get("/api/areas")
    async def get_areas():
        return {"areas": areamod.list_areas(), "extractor_available": areamod.pmtiles_available()}

    @r.post("/api/areas/estimate")
    async def area_estimate(payload: dict = Body(...)):
        return {"est_mb": areamod.estimate_size_mb(payload["bbox"], payload.get("maxzoom", 16))}

    @r.post("/api/areas")
    async def area_create(payload: dict = Body(...)):
        name, bbox, mz = payload["name"], payload["bbox"], payload.get("maxzoom", 16)
        async def progress(p):
            await svc._broadcast(json.dumps({"type": "area_progress", **p}))
        try:
            return await areamod.extract_area(name, bbox, mz, progress)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))

    @r.delete("/api/areas/{name}")
    async def area_delete(name: str):
        areamod.delete_area(name)
        if svc.active_area == name:
            svc.active_area = None
            svc.centreline = None
        return {"deleted": name}

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

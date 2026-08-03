"""NEPTUNE Sub API — FastAPI app.

Serves the static client, the MJPEG camera feed, and the real-time control
WebSocket. One authoritative RovState; a single background loop advances it,
runs the safety watchdog, and broadcasts telemetry to every connected client.

Run:  cd api && uvicorn main:app --host 0.0.0.0 --port 8000
      (or: python main.py)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import metrics as sysmetrics
from rov_camera import get_camera, mjpeg_stream
from camera.app import create_camera_service
from camera.service import build_router as build_camera_router
from nav.app import create_nav_service
from nav.service import build_router as build_nav_router
from config import settings
from hardware import get_hardware
from protocol import Alarm, Pong, parse_inbound
from rov import RovState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("neptune")


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.info("client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.info("client disconnected (%d total)", len(self._clients))

    async def broadcast(self, text: str) -> None:
        if not self._clients:
            return
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 — drop broken sockets
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._clients)


async def _control_loop(app: FastAPI) -> None:
    """Advance sim, run the watchdog, refresh metrics, broadcast telemetry."""
    rov: RovState = app.state.rov
    mgr: ConnectionManager = app.state.manager
    period = 1.0 / max(1.0, settings.telemetry_hz)
    metrics_cache = sysmetrics.snapshot()
    last_metrics = time.monotonic()
    last = time.monotonic()
    log.info("control loop @ %.0f Hz (watchdog %.2fs)", settings.telemetry_hz, settings.watchdog_timeout_s)
    while True:
        now = time.monotonic()
        dt = now - last
        last = now

        rov.update(dt)
        rov.watchdog(now)

        if now - last_metrics >= settings.metrics_period_s:
            metrics_cache = sysmetrics.snapshot()
            last_metrics = now

        if mgr.count:
            tel = rov.telemetry(metrics_cache)
            await mgr.broadcast(tel.model_dump_json())
            if rov.leak_alarm_edge(tel.leak):
                await mgr.broadcast(Alarm(name="leak").model_dump_json())

        await asyncio.sleep(period)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.hw = get_hardware()
    app.state.rov = RovState(app.state.hw)
    app.state.manager = ConnectionManager()
    app.state.camera = get_camera()
    app.state.loop_task = asyncio.create_task(_control_loop(app))
    await app.state.camera_svc.start()   # WOLFANG control plane (degrades if no camera)
    await app.state.nav_svc.start()      # navigation: dead reckoning + dive logging
    log.info("NEPTUNE API up — hardware=%s camera=%s + WOLFANG control plane",
             "mock" if app.state.hw.is_mock else "real", app.state.camera.kind)
    try:
        yield
    finally:
        app.state.loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.loop_task
        with contextlib.suppress(Exception):
            await app.state.camera_svc.stop()
        with contextlib.suppress(Exception):
            await app.state.nav_svc.stop()
        with contextlib.suppress(Exception):
            app.state.camera.stop()
        with contextlib.suppress(Exception):
            app.state.hw.safe()
            app.state.hw.close()
        log.info("NEPTUNE API down — vehicle safed")


app = FastAPI(title="NEPTUNE Sub API", lifespan=lifespan)

# WOLFANG camera control plane: /api/* + /ws/telemetry (mounted before the static
# client mount below so the API routes win). started/stopped in the lifespan.
app.state.camera_svc = create_camera_service()
app.include_router(build_camera_router(app.state.camera_svc))
app.state.nav_svc = create_nav_service()
app.include_router(build_nav_router(app.state.nav_svc))

# file:// client reports Origin "null"; "*" lets disk-mode reach the REST/health
# endpoints. (Browser WebSockets aren't subject to CORS.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "hardware": "mock" if app.state.hw.is_mock else "real",
        "camera": app.state.camera.kind,
        "clients": app.state.manager.count,
    })


@app.get("/stream.mjpg")
def stream() -> StreamingResponse:
    return StreamingResponse(
        mjpeg_stream(app.state.camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, private", "Pragma": "no-cache"},
    )


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket) -> None:
    mgr: ConnectionManager = app.state.manager
    rov: RovState = app.state.rov
    await mgr.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            msg = parse_inbound(raw)
            if msg is None:
                continue
            t = msg.type
            if t == "control":
                rov.apply_control(msg)
            elif t == "camera":
                rov.apply_camera(msg)
            elif t == "ballast":
                rov.apply_ballast(msg)
            elif t == "command":
                rov.apply_command(msg)
            elif t == "ping":
                await ws.send_text(Pong().model_dump_json())
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 — never let one socket take down the app
        log.warning("ws error: %s", exc)
    finally:
        mgr.disconnect(ws)


# Static client LAST so the API routes above win. html=True serves index.html at /.
if settings.client_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(settings.client_dir), html=True), name="client")
else:
    log.warning("client dir %s not found — static UI not served", settings.client_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)

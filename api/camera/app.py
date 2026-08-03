"""Camera control app.

Two ways to run:
  * Standalone:  uvicorn camera.app:app   (control plane only, for camera dev)
  * Embedded:    the ROV `main.py` calls create_camera_service() + build_router()
                 and drives start()/stop() from its own lifespan, so both control
                 planes share one :8000 origin (spec §7 topology).
"""
from __future__ import annotations

import contextlib

from fastapi import FastAPI

from .service import CameraService, build_router


def create_camera_service() -> CameraService:
    return CameraService()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    svc: CameraService = app.state.camera_svc
    await svc.start()
    try:
        yield
    finally:
        await svc.stop()


def make_app() -> FastAPI:
    svc = create_camera_service()
    app = FastAPI(title="NEPTUNE Camera API", lifespan=_lifespan)
    app.state.camera_svc = svc
    app.include_router(build_router(svc))
    return app


app = make_app()

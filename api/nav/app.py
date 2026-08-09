"""Navigation app — standalone (uvicorn nav.app:app) or embedded via the ROV
main app (create_nav_service() + build_router(); main drives start()/stop())."""

from __future__ import annotations

import contextlib

from fastapi import FastAPI

from .service import NavService, build_router


def create_nav_service(get_rov=None) -> NavService:
    """get_rov: callable returning the live RovState, so the map follows the vehicle
    rather than a scripted route. Omitted when nav runs standalone."""
    return NavService(get_rov)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    svc: NavService = app.state.nav_svc
    await svc.start()
    try:
        yield
    finally:
        await svc.stop()


def make_app() -> FastAPI:
    svc = create_nav_service()
    app = FastAPI(title="NEPTUNE Nav API", lifespan=_lifespan)
    app.state.nav_svc = svc
    app.include_router(build_router(svc))
    return app


app = make_app()

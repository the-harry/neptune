"""Blackbox HTTP surface (spec §1/§5): session handshake + client-log upload."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException

from .recorder import BlackBox

log = logging.getLogger("neptune.blackbox")

MAX_RECORDS_PER_POST = 5000


def build_router(bb: BlackBox) -> APIRouter:
    r = APIRouter()

    @r.get("/api/session")
    async def get_session():
        """The client adopts this session on connect (§1). pi_boot_id lets it detect
        a Pi reboot and start a fresh client file; a reconnect keeps the same one."""
        return bb.session_info()

    @r.post("/api/clientlog")
    async def client_log(payload: dict = Body(...)):
        """Append uploaded client records verbatim to client_<session>.jsonl (§5).
        The client deletes from its IndexedDB ring only after this confirms."""
        session_id = payload.get("session_id")
        records = payload.get("records")
        if not isinstance(records, list):
            raise HTTPException(422, "records must be a list")
        if len(records) > MAX_RECORDS_PER_POST:
            raise HTTPException(413, f"too many records (>{MAX_RECORDS_PER_POST})")
        written = bb.client_append(session_id or bb.session_id, records)
        return {"ok": True, "written": written, "session_id": session_id or bb.session_id}

    return r

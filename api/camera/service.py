"""Camera control service — the FastAPI surface from spec §7.1.

Owns the CGI client, the cached Camera.Menu.* snapshot, the record-state poller,
the sequential download queue, the telemetry loop, and pre-flight. Exposes an
APIRouter (mounted under /api + /ws/telemetry by app.py / the ROV main app).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .cgi import PRIORITY_TELEMETRY, PRIORITY_USER, CgiClient
from .config import WRITE_TO_READ, cam_settings
from .models import (
    CameraUnavailable,
    CgiError,
    FileEntry,
    MenuOption,
    PreflightCheck,
    PreflightResult,
    RecordState,
    Status,
)

log = logging.getLogger("neptune.cam.svc")


class CameraService:
    def __init__(self) -> None:
        self.cgi = CgiClient()
        self.menu_cache: dict[str, str] = {}     # Camera.Menu.* — read once, updated on write
        self.menu_options: list[MenuOption] = []  # parsed cammenu.xml
        self._subs: set[WebSocket] = set()
        self._dl_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self.downloads: dict[str, dict] = {}     # name -> {state, received, total}

    async def start(self) -> None:
        await self.cgi.start()
        # Cache Camera.Menu.* + cammenu.xml on connect (§7.2). Best-effort: if the
        # camera isn't reachable (e.g. laptop dev), the breaker trips and we carry
        # on with an empty cache until /api/preflight refreshes it.
        try:
            await self.refresh_menu()
            await self.load_cammenu()
        except Exception as exc:  # noqa: BLE001
            log.info("initial menu cache skipped (camera not ready): %s", exc)
        self._tasks = [
            asyncio.create_task(self._telemetry_loop()),
            asyncio.create_task(self._download_worker()),
        ]
        log.info("camera service started (base=%s)", cam_settings.base_url)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await self.cgi.aclose()

    # ---- helpers ---------------------------------------------------------
    async def refresh_menu(self, priority: int = PRIORITY_USER) -> dict[str, str]:
        resp = await self.cgi.get("Camera.Menu.*", priority=priority)
        self.menu_cache = dict(resp.data)
        return self.menu_cache

    async def load_cammenu(self) -> list[MenuOption]:
        url = f"{cam_settings.base_url.rstrip('/')}/cammenu.xml"
        async with httpx.AsyncClient(headers={"Connection": "close"}) as c:
            r = await c.get(url, timeout=cam_settings.timeout_fast_s)
        opts: list[MenuOption] = []
        try:
            root = ET.fromstring(r.text)
            for item in root.findall(".//item"):
                prop = item.get("property", "")
                options = [o.text or "" for o in item.findall("option")]
                read_prop = WRITE_TO_READ.get(prop, prop)
                opts.append(MenuOption(property=prop, read_property=read_prop,
                                       options=options, current=self.menu_cache.get(read_prop)))
        except ET.ParseError as exc:
            log.warning("cammenu.xml parse failed: %s", exc)
        self.menu_options = opts
        return opts

    async def status(self, priority: int = PRIORITY_USER) -> Status:
        if self.cgi.degraded:
            return Status(degraded=True)
        try:
            preview = (await self.cgi.get("Camera.Preview.*", priority=priority)).data
            batt = (await self.cgi.get("Camera.Battery.Level", priority=priority)).data
            rem = (await self.cgi.get("Camera.Capture.Remaining", priority=priority)).data
        except CameraUnavailable:
            return Status(degraded=True)
        rec_raw = preview.get("Camera.Preview.MJPEG.status.record", "")
        return Status(
            battery=_to_int(batt.get("Camera.Battery.Level")),
            recording=bool(rec_raw) and rec_raw != "Standby",
            record_raw=rec_raw,
            mode=self.menu_cache.get("Camera.Menu.UIMode", ""),
            sd=self.menu_cache.get("Camera.Menu.SD0", ""),
            warning=preview.get("Camera.Preview.MJPEG.WarningMSG", ""),
            remaining=_to_int(rem.get("Camera.Capture.Remaining")),
            is_streaming=self.menu_cache.get("Camera.Menu.IsStreaming", ""),
            video_res=self.menu_cache.get("Camera.Menu.VideoRes", ""),
            awb=self.menu_cache.get("Camera.Menu.AWB", ""),
            image_res=self.menu_cache.get("Camera.Menu.ImageRes", ""),
            ev=self.menu_cache.get("Camera.Menu.EV", ""),
            degraded=False,
        )

    async def set_property(self, prop: str, value: str) -> str | None:
        """Set + re-read + return the actual value (best-effort; spec §4.4)."""
        await self.cgi.set(prop, value)
        actual = await self.cgi.read_value(prop)
        read_name = WRITE_TO_READ.get(prop, prop)
        if actual is not None:
            self.menu_cache[read_name] = actual
        return actual

    async def record_toggle(self) -> RecordState:
        # spec §4.3: UIMode=VIDEO first, then Video=record (a TOGGLE), then POLL
        # Camera.Preview.MJPEG.status.record until it flips — never optimistic.
        before = (await self.cgi.get("Camera.Preview.MJPEG.status.record")).data.get(
            "Camera.Preview.MJPEG.status.record", "")
        await self.cgi.set("Camera.Menu.UIMode", "VIDEO")
        self.menu_cache["Camera.Menu.UIMode"] = "VIDEO"
        await self.cgi.set("Video", "record")
        deadline = time.monotonic() + cam_settings.record_poll_timeout_s
        raw = before
        changed = False
        while time.monotonic() < deadline:
            raw = (await self.cgi.get("Camera.Preview.MJPEG.status.record")).data.get(
                "Camera.Preview.MJPEG.status.record", "")
            if raw != before:
                changed = True
                break
            await asyncio.sleep(cam_settings.record_poll_interval_s)
        return RecordState(recording=bool(raw) and raw != "Standby", record_raw=raw, changed=changed)

    async def capture(self) -> FileEntry | None:
        await self.cgi.set("Camera.Menu.UIMode", "CAMERA")
        self.menu_cache["Camera.Menu.UIMode"] = "CAMERA"
        await self.cgi.set("Video", "capture")
        files = await self.list_files("photo", 0, 1)
        return files[0] if files else None

    async def list_files(self, kind: str, frm: int, count: int) -> list[FileEntry]:
        prop = "Normal" if kind == "video" else "Photo"
        # Some firmware needs playback mode to browse; wrap and always exit after.
        await self.cgi.set("Playback", "enter")
        try:
            xml = await self.cgi.dir(prop, frm, count)
        finally:
            await self.cgi.set("Playback", "exit")
        return _parse_dir(xml, kind)

    async def delete_file(self, name: str) -> None:
        dollar = name.replace("/", "$")            # listing uses '/', del uses '$'
        await self.cgi.delete(dollar)

    async def preflight(self) -> PreflightResult:
        checks: list[PreflightCheck] = []

        def add(step, ok, detail=""):
            checks.append(PreflightCheck(step=step, ok=ok, detail=detail))

        # 1) route pinning
        ok, detail = _check_route()
        add("route: 192.72.1.1 via wlan0", ok, detail)
        try:
            # 2) exit playback
            await self.cgi.set("Playback", "exit"); add("Playback=exit", True)
            # 3) time
            await self.cgi.set("TimeSettings", time.strftime("%Y$%m$%d$%H$%M$%S")); add("TimeSettings", True)
            # 4) menu cache
            await self.refresh_menu(); add("cache Camera.Menu.*", bool(self.menu_cache),
                                          f"{len(self.menu_cache)} props")
            # 5) cammenu.xml
            opts = await self.load_cammenu(); add("parse cammenu.xml", bool(opts), f"{len(opts)} items")
            # 6) power-saving OFF (critical)
            ps = await self.set_property("PowerSaving", "OFF")
            add("PowerSaving=OFF (critical)", ps == "OFF" or ps is None, f"actual={ps}")
            await self.set_property("LCDPower", "OFF")
            # 7) warning message => fail if non-empty
            prev = (await self.cgi.get("Camera.Preview.MJPEG.WarningMSG")).data.get(
                "Camera.Preview.MJPEG.WarningMSG", "")
            add("WarningMSG empty", prev == "", f"msg={prev!r}")
            # 8) SD ready + remaining
            sd = self.menu_cache.get("Camera.Menu.SD0", "")
            rem = _to_int((await self.cgi.get("Camera.Capture.Remaining")).data.get("Camera.Capture.Remaining"))
            add("SD READY", sd == "READY", f"SD0={sd}")
            add("capacity remaining", (rem or 0) > 10, f"remaining={rem}")
            # 9) preview bump (best effort)
            await self.cgi.set("Camera.Preview.H264.w", "1280")
            await self.cgi.set("Camera.Preview.H264.h", "720")
            w = (await self.cgi.get("Camera.Preview.H264.w")).data.get("Camera.Preview.H264.w")
            add("preview 1280x720 (best effort)", True, f"actual w={w}")
            # 10) go2rtc stream health
            gok, gdetail = await self._go2rtc_ok()
            add("go2rtc stream healthy", gok, gdetail)
            # 11) battery
            batt = _to_int((await self.cgi.get("Camera.Battery.Level")).data.get("Camera.Battery.Level"))
            add(f"battery >= {cam_settings.battery_warn_pct}%",
                (batt or 0) >= cam_settings.battery_warn_pct, f"battery={batt}%")
        except (CgiError, CameraUnavailable) as exc:
            add("camera reachable", False, str(exc))
        passed = all(c.ok for c in checks)
        return PreflightResult(passed=passed, checks=checks)

    async def _go2rtc_ok(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{cam_settings.go2rtc_api}/api/streams", timeout=2.0)
            if r.status_code == 200 and cam_settings.go2rtc_stream in r.text:
                return True, "stream present"
            return False, f"stream {cam_settings.go2rtc_stream!r} not reported"
        except Exception as exc:  # noqa: BLE001
            return False, f"go2rtc unreachable: {exc}"

    # ---- telemetry (spec §7.2: every 15s, pushed over WS) ----------------
    async def _telemetry_loop(self) -> None:
        while True:
            try:
                if self._subs:
                    st = await self.status(priority=PRIORITY_TELEMETRY)
                    await self._broadcast(st.model_dump_json())
            except Exception as exc:  # noqa: BLE001
                log.debug("telemetry poll skipped: %s", exc)
            await asyncio.sleep(cam_settings.telemetry_period_s)

    async def _broadcast(self, text: str) -> None:
        for ws in list(self._subs):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                self._subs.discard(ws)

    # ---- download worker (sequential, Range-resumable) -------------------
    async def enqueue_download(self, name: str) -> None:
        self.downloads[name] = {"state": "queued", "received": 0, "total": None}
        await self._dl_queue.put(name)

    async def _download_worker(self) -> None:
        os.makedirs(cam_settings.download_dir, exist_ok=True)
        while True:
            name = await self._dl_queue.get()
            try:
                await self._offload(name)
            except Exception as exc:  # noqa: BLE001
                self.downloads[name]["state"] = f"error: {exc}"
                log.warning("download %s failed: %s", name, exc)
            finally:
                self._dl_queue.task_done()

    async def _offload(self, name: str) -> None:
        # ONE transfer at a time (single-threaded server); Range-resume on failure.
        url = f"{cam_settings.base_url.rstrip('/')}{name}"
        dest = os.path.join(cam_settings.download_dir, os.path.basename(name))
        received = os.path.getsize(dest) if os.path.exists(dest) else 0
        self.downloads[name].update(state="downloading", received=received)
        attempts = 0
        while True:
            attempts += 1
            headers = {"Connection": "close"}
            if received:
                headers["Range"] = f"bytes={received}-"
            try:
                async with httpx.AsyncClient(headers=headers) as c:
                    async with c.stream("GET", url, timeout=None) as r:
                        if r.status_code not in (200, 206):
                            raise RuntimeError(f"HTTP {r.status_code}")
                        total = _content_total(r, received)
                        self.downloads[name]["total"] = total
                        mode = "ab" if (received and r.status_code == 206) else "wb"
                        if mode == "wb":
                            received = 0
                        with open(dest, mode) as fh:
                            async for chunk in r.aiter_bytes(cam_settings.download_chunk_bytes):
                                fh.write(chunk)
                                received += len(chunk)
                                self.downloads[name]["received"] = received
                if self.downloads[name]["total"] and received >= self.downloads[name]["total"]:
                    self.downloads[name]["state"] = "done"
                    log.info("download %s complete (%d bytes)", name, received)
                    return
                self.downloads[name]["state"] = "done"
                return
            except (httpx.TransportError, httpx.TimeoutException, RuntimeError) as exc:
                if attempts >= 5:
                    raise
                log.warning("download %s interrupted at %d, resuming (%s)", name, received, exc)
                await asyncio.sleep(1.0)


# ---- module-level helpers ------------------------------------------------
def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _content_total(resp: httpx.Response, received: int) -> int | None:
    cr = resp.headers.get("content-range")
    if cr and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            pass
    cl = resp.headers.get("content-length")
    if cl:
        return received + int(cl) if resp.status_code == 206 else int(cl)
    return None


def _parse_dir(xml: str, kind: str) -> list[FileEntry]:
    out: list[FileEntry] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for f in root.findall("file"):
        name = (f.findtext("name") or "").strip()
        size = _to_int(f.findtext("size")) or 0
        fmt = f.find("format")
        res = fmt.get("size", "") if fmt is not None else ""
        fps = fmt.get("fps") if fmt is not None else None
        dur = fmt.get("time") if fmt is not None else None
        out.append(FileEntry(
            name=name, kind=kind, size=size, resolution=res,
            fps=float(fps) if fps else None, duration=float(dur) if dur else None,
            time=(f.findtext("time") or "").strip(),
        ))
    return out


def _check_route() -> tuple[bool, str]:
    """Spec §1: `ip route get 192.72.1.1` must resolve via wlan0; pin if not."""
    try:
        out = subprocess.run(["ip", "route", "get", cam_settings.camera_ip],
                             capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return False, f"ip route unavailable ({exc}) — skip on non-Linux dev host"
    txt = out.stdout
    if re.search(rf"\bdev\s+{re.escape(cam_settings.wlan_iface)}\b", txt):
        return True, txt.strip().splitlines()[0] if txt.strip() else "ok"
    # attempt to pin (needs privileges)
    try:
        subprocess.run(["ip", "route", "add", f"{cam_settings.camera_ip}/32", "dev", cam_settings.wlan_iface],
                       capture_output=True, text=True, timeout=2)
        return _check_route()[0], "pinned route to wlan0"
    except Exception as exc:  # noqa: BLE001
        return False, f"MISROUTED — not via {cam_settings.wlan_iface}; pin failed: {exc}"


# ==========================================================================
# Router
# ==========================================================================
def build_router(svc: CameraService) -> APIRouter:
    r = APIRouter()

    @r.get("/api/status", response_model=Status)
    async def get_status():
        return await svc.status()

    @r.get("/api/config")
    async def get_config():
        return JSONResponse(svc.menu_cache)

    @r.put("/api/config/{prop}")
    async def put_config(prop: str, value: str = Query(...)):
        try:
            actual = await svc.set_property(prop, value)
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))
        return {"property": prop, "requested": value, "actual": actual, "took": actual == value}

    @r.get("/api/menu")
    async def get_menu():
        if not svc.menu_options:
            await svc.load_cammenu()
        return [m.model_dump() for m in svc.menu_options]

    @r.post("/api/record/toggle", response_model=RecordState)
    async def record_toggle():
        try:
            return await svc.record_toggle()
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))

    @r.post("/api/capture")
    async def capture():
        try:
            f = await svc.capture()
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))
        return f.model_dump() if f else JSONResponse({"file": None})

    @r.get("/api/files")
    async def files(type: str = Query("video", pattern="^(video|photo)$"),
                    from_: int = Query(0, alias="from"), count: int = 100):
        try:
            entries = await svc.list_files(type, from_, count)
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))
        return [e.model_dump() for e in entries]

    @r.get("/api/files/{name:path}/thumb")
    async def thumb(name: str):
        # thumbnails live under /thumb/Video/... — strip the /SD/ prefix (§5.2)
        tail = name.split("/SD/", 1)[-1] if "/SD/" in name else name.lstrip("/")
        url = f"{cam_settings.base_url.rstrip('/')}/thumb/{tail}"
        async with httpx.AsyncClient(headers={"Connection": "close"}) as c:
            resp = await c.get(url, timeout=cam_settings.timeout_fast_s)
        return Response(resp.content, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})

    @r.post("/api/files/{name:path}/download")
    async def download(name: str):
        await svc.enqueue_download("/" + name.lstrip("/"))
        return {"queued": name, "status": svc.downloads.get("/" + name.lstrip("/"))}

    @r.get("/api/downloads")
    async def downloads():
        return svc.downloads

    @r.delete("/api/files/{name:path}")
    async def delete(name: str, confirm: bool = Query(False)):
        if not confirm:                        # destructive: needs explicit confirm (§7.2)
            raise HTTPException(400, "refused: destructive delete requires ?confirm=true")
        try:
            await svc.delete_file("/" + name.lstrip("/"))
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))
        return {"deleted": name}

    @r.post("/api/config/format-sd")
    async def format_sd(confirm: bool = Query(False)):
        if not confirm:                        # DESTRUCTIVE: wipes the card
            raise HTTPException(400, "refused: SD format requires ?confirm=true")
        await svc.cgi.set("SD0", "format")
        return {"formatting": True}

    @r.post("/api/preflight", response_model=PreflightResult)
    async def preflight():
        return await svc.preflight()

    @r.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket):
        await ws.accept()
        svc._subs.add(ws)
        try:
            st = await svc.status(priority=PRIORITY_TELEMETRY)  # push one immediately
            await ws.send_text(st.model_dump_json())
            while True:
                await ws.receive_text()   # ignore client input; keep the socket open
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            svc._subs.discard(ws)

    return r

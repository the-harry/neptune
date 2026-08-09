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
from .config import WRITE_TO_READ, cam_settings, read_name_for
from .defaults import (
    CRITICAL,
    HULL,
    NOT_SET,
    REPORT_ONLY,
    SETTINGS,
    Capabilities,
    Setting,
    awb_setting,
    load_caps,
)
from .defaults import same as _same
from .defaults import (
    save_caps,
)
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

# Cap on set+read pairs spent discovering one unknown property. Bounds a cold probe
# of the whole table to a few seconds of background CGI traffic; after that the
# capability cache makes it near-free.
_MAX_PROBE_ATTEMPTS = 6


def _log_defaults(rep: dict) -> None:
    if not rep.get("ok"):
        log.warning(
            "camera defaults (%s%s): CRITICAL UNMET %s — counts=%s",
            rep.get("reason"),
            " audit" if rep.get("audit_only") else "",
            rep.get("critical_unmet"),
            rep.get("counts"),
        )
    else:
        log.info(
            "camera defaults (%s%s): %s in %sms",
            rep.get("reason"),
            " audit" if rep.get("audit_only") else "",
            rep.get("counts"),
            rep.get("took_ms"),
        )
    for r in rep.get("results", []):
        if r["outcome"] == "set":
            log.info("  %s: %s -> %s (%s)", r["property"], r["before"], r["after"], r["detail"])
        elif r["outcome"] in ("ignored", "rejected", "unresolved", "unreachable", "unmet"):
            level = log.warning if r["tier"] == CRITICAL else log.info
            level("  %s: %s (%s) — %s", r["property"], r["outcome"], r["desired"], r["detail"])


class CameraService:
    def __init__(self, get_rov=None) -> None:
        self.cgi = CgiClient()
        self.menu_cache: dict[str, str] = {}  # Camera.Menu.* — read once, updated on write
        self.menu_options: list[MenuOption] = []  # parsed cammenu.xml
        self._subs: set[WebSocket] = set()
        self._dl_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self.downloads: dict[str, dict] = {}  # name -> {state, received, total}
        # Optional accessor for the live vehicle. Only used to read the white-light
        # state, which decides AWB — see defaults.awb_setting(). None on the bench.
        self.get_rov = get_rov
        self.caps = Capabilities()
        self.defaults_report: dict = {"state": "not applied yet"}
        self._cam_online = False

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
            asyncio.create_task(self._defaults_loop()),
        ]
        log.info(
            "camera service started (base=%s, apply_defaults=%s)", cam_settings.base_url, cam_settings.apply_defaults
        )

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
                opts.append(
                    MenuOption(
                        property=prop, read_property=read_prop, options=options, current=self.menu_cache.get(read_prop)
                    )
                )
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
        read_name = read_name_for(prop)
        actual = (await self.cgi.get(read_name)).data.get(read_name)
        if actual is not None:
            self.menu_cache[read_name] = actual
        return actual

    # ---- defaults: applied on connect, re-asserted for the rest of the dive --
    async def connect_sequence(self, priority: int = PRIORITY_USER) -> None:
        """Spec §5.5 steps 1–4: leave playback, set the clock, cache the menu.

        The camera has NO RTC and burns its clock into the image, so a camera that
        just rebooted is stamping footage with a wrong time until this runs — which
        breaks the one thing the timestamp exists for, correlating footage against
        the blackbox log.
        """
        await self.cgi.set("Playback", "exit", priority=priority)
        await self.cgi.set("TimeSettings", time.strftime("%Y$%m$%d$%H$%M$%S"), priority=priority)
        await self.refresh_menu(priority=priority)
        if not self.menu_options:
            try:
                await self.load_cammenu()
            except Exception as exc:  # noqa: BLE001
                log.info("cammenu.xml unavailable: %s", exc)

    def _white_lights_on(self) -> bool:
        """The white LEDs decide AWB. Absent a vehicle (bench), assume no lamps —
        that is the darker assumption and the warmer preset, which is recoverable;
        the reverse leaves everything blue-green."""
        try:
            rov = self.get_rov() if self.get_rov else None
            hw = getattr(rov, "hw", None)
            if hw is None:
                return False
            on, _level = hw.get_light("white")
            return bool(on)
        except Exception:  # noqa: BLE001
            return False

    def _plan(self) -> list[Setting]:
        plan = list(SETTINGS)
        plan.append(awb_setting(self._white_lights_on()))
        if cam_settings.upside_down:
            plan.append(
                Setting(
                    "upside_down",
                    "Camera.Menu.UpsideDown",
                    ("UpsideDown", "Camera.Menu.UpsideDown"),
                    (cam_settings.upside_down,),
                    HULL,
                    "Physical mounting, asserted because WOLFANG_UPSIDE_DOWN is set. Getting it "
                    "right in the sensor beats rotating in post.",
                )
            )
        return plan

    async def _apply_one(self, s: Setting, priority: int) -> tuple[str, str | None, str]:
        """Probe one setting. Returns (outcome, actual, detail).

        Neither the write name nor the valid value set is known for most of these
        (spec §7), and the two failure modes look different on the wire:

          * `722` means the server parsed the property and refused the VALUE — so
            the NAME is probably right, and the next value is worth trying.
          * `0 OK` followed by an unchanged read-back is a SILENT NO-OP, which
            usually means the name is wrong. Move to the next name rather than
            burning attempts on values.
        """
        writes, values = self.caps.preferred(s)
        attempts, note, refused = 0, "", False
        for wname in writes:
            for vi, value in enumerate(values):
                if attempts >= _MAX_PROBE_ATTEMPTS:
                    return "unresolved", None, f"gave up after {attempts} attempts; {note}"
                attempts += 1
                try:
                    await self.cgi.set(wname, value, priority=priority)
                except CgiError as exc:
                    refused = True
                    note = f"set {wname}={value} refused ({exc})"
                    continue  # name looks real, value is not
                actual = (await self.cgi.get(s.read, priority=priority)).data.get(s.read)
                if _same(actual, value):
                    self.caps.write_names[s.key] = wname
                    self.caps.values[s.key] = value
                    for lst in (self.caps.ignored, self.caps.rejected):
                        if s.key in lst:
                            lst.remove(s.key)
                    return "set", actual, f"via {wname}"
                note = f"set {wname}={value} returned OK but {s.read} is still {actual!r}"
                if vi == 0 and len(writes) > 1:
                    break  # silent no-op on the first value: suspect the NAME
        if refused:
            if s.key not in self.caps.rejected:
                self.caps.rejected.append(s.key)
            return "rejected", None, note
        if s.key not in self.caps.ignored:
            self.caps.ignored.append(s.key)
        return "ignored", None, note

    async def apply_defaults(
        self, *, include_cold: bool = True, reason: str = "manual", reprobe: bool = False, priority: int = PRIORITY_USER
    ) -> dict:
        """Bring the camera to the defaults in `defaults.py`, verifying every write.

        Never writes a setting that is already at an acceptable value — that keeps a
        steady-state pass down to two reads, and avoids pointless wear on the
        camera's settings flash.
        """
        t0 = time.monotonic()
        # Enforcement off still AUDITS. Returning an empty report here meant preflight
        # emitted no critical checks at all, so a camera sitting at the factory
        # PowerSaving=5MIN produced a clean bill of health — the same "reports OK
        # because it looked at nothing" failure this whole change exists to remove.
        audit_only = not cam_settings.apply_defaults and not reprobe
        try:
            state = dict(await self.refresh_menu(priority=priority))
            try:
                state.update((await self.cgi.get("Camera.Preview.*", priority=priority)).data)
            except CgiError as exc:  # preview block is optional for the menu settings
                log.info("preview state unreadable (%s) — preview defaults skipped", exc)
        except (CgiError, CameraUnavailable) as exc:
            return {"reason": reason, "ok": False, "error": str(exc), "results": [], "counts": {}}

        fw = state.get("Camera.Menu.FWversion", "")
        if not self.caps.write_names or self.caps.fw != fw:
            self.caps = load_caps(fw)
        rec_raw = state.get("Camera.Preview.MJPEG.status.record", "")
        recording = bool(rec_raw) and rec_raw != "Standby"

        results: list[dict] = []

        def row(s: Setting, before, after, outcome, detail=""):
            results.append(
                {
                    "key": s.key,
                    "tier": s.tier,
                    "property": s.read,
                    "desired": s.values[0],
                    "before": before,
                    "after": after,
                    "outcome": outcome,
                    "detail": detail,
                    "why": s.why,
                }
            )

        for s in self._plan():
            before = state.get(s.read)
            if any(_same(before, v) for v in s.values):
                row(s, before, before, "already")
                continue
            if audit_only:
                row(s, before, before, "unmet", "WOLFANG_APPLY_DEFAULTS=0 — reported, not corrected")
                continue
            if not s.hot:
                # Slow and/or blanks the picture: every UIMode-class op stalls the
                # camera's single-threaded server for ~1.1s and RTSP shares it, so
                # this is a second of blind piloting. Connect-time only, never
                # while the card is rolling.
                if not include_cold:
                    row(s, before, before, "deferred", "slow/blanks the feed — connect-time only")
                    continue
                if recording:
                    row(s, before, before, "deferred", "camera is recording")
                    continue
            if not reprobe and s.key in self.caps.ignored:
                row(s, before, before, "ignored", "cached: this firmware accepts but ignores it")
                continue
            if not reprobe and s.key in self.caps.rejected:
                row(s, before, before, "rejected", "cached: this firmware refused every candidate")
                continue
            try:
                outcome, actual, detail = await self._apply_one(s, priority)
            except CameraUnavailable as exc:
                row(s, before, None, "unreachable", str(exc))
                break  # the camera is gone; stop hammering it
            row(s, before, actual, outcome, detail)
            if actual is not None:
                state[s.read] = actual
                if s.read.startswith("Camera.Menu."):
                    self.menu_cache[s.read] = actual

        self.caps.fw = fw or self.caps.fw
        saved = False if audit_only else save_caps(self.caps)

        counts: dict[str, int] = {}
        for r in results:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        critical_bad = [r["key"] for r in results if r["tier"] == CRITICAL and r["outcome"] not in ("already", "set")]
        report = {
            "reason": reason,
            "ok": not critical_bad,
            "fw": fw,
            "audit_only": audit_only,
            "white_lights": self._white_lights_on(),
            "recording": recording,
            "critical_unmet": critical_bad,
            "counts": counts,
            "took_ms": round((time.monotonic() - t0) * 1000),
            "caps_saved": saved,
            "results": results,
            # Reported, never written — see defaults.NOT_SET.
            "observed": {p: state.get(p) for p in REPORT_ONLY if p in state},
            "not_set": [{"property": p, "why": w} for p, w in NOT_SET],
        }
        self.defaults_report = report
        return report

    async def probe_property(
        self, write: str, values: list[str], *, read: str | None = None, dwell_s: float = 0.0, restore: bool = True
    ) -> dict:
        """Deliberate, operator-driven discovery for a property whose value set or
        semantics are unknown (spec §7) — e.g. whether `LCDPower=OFF` means the
        screen never blanks or the screen stays dark.

        Restores the original value by default, so probing does not silently leave
        the camera somewhere unintended.
        """
        read_name = read or read_name_for(write)
        original = (await self.cgi.get(read_name)).data.get(read_name)
        if original is None:
            # Reading nothing means the read name is wrong, not that the property is
            # empty. Probing on regardless would produce a page of took=false that
            # says nothing about the camera and everything about our guess.
            return {
                "write": write,
                "read": read_name,
                "original": None,
                "tried": [],
                "restored": None,
                "error": f"{read_name} does not read back — pass an explicit `read` name. "
                f"Dump the full property list with GET /api/config first.",
            }
        tried = []
        for v in values:
            entry = {"value": v}
            try:
                await self.cgi.set(write, v)
                if dwell_s:
                    await asyncio.sleep(dwell_s)  # give the operator time to watch the camera
                actual = (await self.cgi.get(read_name)).data.get(read_name)
                entry.update(accepted=True, read_back=actual, took=_same(actual, v))
            except CgiError as exc:
                entry.update(accepted=False, error=str(exc))
            except CameraUnavailable as exc:
                entry.update(accepted=False, error=str(exc))
                tried.append(entry)
                break
            tried.append(entry)
        restored = None
        if restore and original is not None:
            try:
                await self.cgi.set(write, original)
                restored = (await self.cgi.get(read_name)).data.get(read_name)
            except (CgiError, CameraUnavailable) as exc:
                restored = f"restore failed: {exc}"
        return {"write": write, "read": read_name, "original": original, "tried": tried, "restored": restored}

    async def _defaults_loop(self) -> None:
        """Keep the camera awake, and keep the critical settings actually asserted.

        Three jobs, all of which must happen with NO dashboard connected — the 15 s
        telemetry poll only runs while someone is subscribed, so with nobody watching
        there is no CGI traffic at all:

          * **keepalive** — one `get` per tick. If a power-saving timer could not be
            disabled, this resets it. The tick must stay well inside the factory 5MIN.
          * **reconnect** — a camera that rebooted (battery swap, sleep, AP drop)
            comes back with a wrong clock and possibly factory settings. A
            False→True transition re-runs the whole connect sequence.
          * **drift** — anything that put a critical setting back gets it undone.
        """
        await asyncio.sleep(1.0)  # let start()'s own menu read land first
        while True:
            try:
                menu = await self.refresh_menu(priority=PRIORITY_TELEMETRY)
                was_online, self._cam_online = self._cam_online, True
                if not was_online:
                    log.info("camera online — running the connect sequence")
                    await self.connect_sequence(priority=PRIORITY_TELEMETRY)
                    rep = await self.apply_defaults(
                        reason="camera-online", include_cold=True, priority=PRIORITY_TELEMETRY
                    )
                    _log_defaults(rep)
                elif cam_settings.apply_defaults:
                    # Drift check is free: it reads the menu we just fetched. Cold
                    # settings are excluded because re-asserting them blanks the
                    # picture, and preview properties are not in this dump.
                    drifted = [
                        s.key
                        for s in self._plan()
                        if s.hot
                        and s.read.startswith("Camera.Menu.")
                        and s.read in menu
                        and not any(_same(menu.get(s.read), v) for v in s.values)
                        and s.key not in self.caps.ignored
                        and s.key not in self.caps.rejected
                    ]
                    if drifted:
                        log.warning("camera settings drifted (%s) — re-asserting", ", ".join(drifted))
                        rep = await self.apply_defaults(reason="drift", include_cold=False, priority=PRIORITY_TELEMETRY)
                        _log_defaults(rep)
            except (CgiError, CameraUnavailable) as exc:
                if self._cam_online:
                    log.warning("camera unreachable (%s) — defaults will be re-applied when it returns", exc)
                self._cam_online = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("defaults guard tick failed: %s", exc)
            await asyncio.sleep(cam_settings.defaults_recheck_s)

    async def record_toggle(self) -> RecordState:
        # spec §4.3: UIMode=VIDEO first, then Video=record (a TOGGLE), then POLL
        # Camera.Preview.MJPEG.status.record until it flips — never optimistic.
        before = (await self.cgi.get("Camera.Preview.MJPEG.status.record")).data.get(
            "Camera.Preview.MJPEG.status.record", ""
        )
        await self.cgi.set("Camera.Menu.UIMode", "VIDEO")
        self.menu_cache["Camera.Menu.UIMode"] = "VIDEO"
        await self.cgi.set("Video", "record")
        deadline = time.monotonic() + cam_settings.record_poll_timeout_s
        raw = before
        changed = False
        while time.monotonic() < deadline:
            raw = (await self.cgi.get("Camera.Preview.MJPEG.status.record")).data.get(
                "Camera.Preview.MJPEG.status.record", ""
            )
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
        dollar = name.replace("/", "$")  # listing uses '/', del uses '$'
        await self.cgi.delete(dollar)

    async def preflight(self) -> PreflightResult:
        checks: list[PreflightCheck] = []

        def add(step, ok, detail=""):
            checks.append(PreflightCheck(step=step, ok=ok, detail=detail))

        # 1) route pinning
        ok, detail = _check_route()
        add("route: 192.72.1.1 via wlan0", ok, detail)
        try:
            # 2) exit playback  3) time  4) menu cache  5) cammenu.xml
            await self.connect_sequence()
            add("Playback=exit + TimeSettings", True)
            add("cache Camera.Menu.*", bool(self.menu_cache), f"{len(self.menu_cache)} props")
            add("parse cammenu.xml", bool(self.menu_options), f"{len(self.menu_options)} items")
            # 6) the defaults, each verified by re-read (§5.1–5.3, defaults.py).
            #
            #    This step used to be two blind writes whose check could not fail:
            #    it wrote `PowerSaving`, read back a property of that name rather
            #    than `Camera.Menu.PowerSaving`, got None, and scored None as a
            #    pass. It reported OK for months on a camera that powered itself
            #    off mid-dive. The report below distinguishes set / already /
            #    ignored / rejected, and only `already` and `set` count.
            rep = await self.apply_defaults(reason="preflight")
            _log_defaults(rep)
            for r in rep.get("results", []):
                if r["tier"] != CRITICAL:
                    continue
                actual = r["after"] if r["after"] is not None else r["before"]
                add(
                    f"{r['property']} (critical)",
                    r["outcome"] in ("already", "set"),
                    f"{r['outcome']}: {actual!r}"
                    + ("" if _same(actual, r["desired"]) else f" (preferred {r['desired']!r})")
                    + (f" — {r['detail']}" if r["detail"] else ""),
                )
            non_critical_bad = [
                r["property"]
                for r in rep.get("results", [])
                if r["tier"] != CRITICAL and r["outcome"] in ("ignored", "rejected", "unresolved")
            ]
            add(
                "non-critical defaults applied",
                True,
                "all applied" if not non_critical_bad else f"firmware would not take: {', '.join(non_critical_bad)}",
            )
            # 7) warning message => fail if non-empty
            prev = (await self.cgi.get("Camera.Preview.MJPEG.WarningMSG")).data.get(
                "Camera.Preview.MJPEG.WarningMSG", ""
            )
            add("WarningMSG empty", prev == "", f"msg={prev!r}")
            # 8) SD ready + remaining
            sd = self.menu_cache.get("Camera.Menu.SD0", "")
            rem = _to_int((await self.cgi.get("Camera.Capture.Remaining")).data.get("Camera.Capture.Remaining"))
            add("SD READY", sd == "READY", f"SD0={sd}")
            add("capacity remaining", (rem or 0) > 10, f"remaining={rem}")
            # 9) the pilot's substream — attempted as part of the defaults above,
            #    reported here because it is what the operator actually flies on.
            pv = {r["key"]: r for r in rep.get("results", [])}
            pw, ph = pv.get("preview_w", {}), pv.get("preview_h", {})
            add(
                "preview 1280x720 (best effort)",
                True,
                f"w={pw.get('after') or pw.get('before')} ({pw.get('outcome')}), "
                f"h={ph.get('after') or ph.get('before')} ({ph.get('outcome')})",
            )
            # 10) go2rtc stream health
            gok, gdetail = await self._go2rtc_ok()
            add("go2rtc stream healthy", gok, gdetail)
            # 11) battery
            batt = _to_int((await self.cgi.get("Camera.Battery.Level")).data.get("Camera.Battery.Level"))
            add(
                f"battery >= {cam_settings.battery_warn_pct}%",
                (batt or 0) >= cam_settings.battery_warn_pct,
                f"battery={batt}%",
            )
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
        out.append(
            FileEntry(
                name=name,
                kind=kind,
                size=size,
                resolution=res,
                fps=float(fps) if fps else None,
                duration=float(dur) if dur else None,
                time=(f.findtext("time") or "").strip(),
            )
        )
    return out


def _check_route() -> tuple[bool, str]:
    """Spec §1: `ip route get 192.72.1.1` must resolve via wlan0; pin if not."""
    try:
        out = subprocess.run(["ip", "route", "get", cam_settings.camera_ip], capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return False, f"ip route unavailable ({exc}) — skip on non-Linux dev host"
    txt = out.stdout
    if re.search(rf"\bdev\s+{re.escape(cam_settings.wlan_iface)}\b", txt):
        return True, txt.strip().splitlines()[0] if txt.strip() else "ok"
    # attempt to pin (needs privileges)
    try:
        subprocess.run(
            ["ip", "route", "add", f"{cam_settings.camera_ip}/32", "dev", cam_settings.wlan_iface],
            capture_output=True,
            text=True,
            timeout=2,
        )
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
    async def files(
        type: str = Query("video", pattern="^(video|photo)$"), from_: int = Query(0, alias="from"), count: int = 100
    ):
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
        return Response(resp.content, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    @r.post("/api/files/{name:path}/download")
    async def download(name: str):
        await svc.enqueue_download("/" + name.lstrip("/"))
        return {"queued": name, "status": svc.downloads.get("/" + name.lstrip("/"))}

    @r.get("/api/downloads")
    async def downloads():
        return svc.downloads

    @r.delete("/api/files/{name:path}")
    async def delete(name: str, confirm: bool = Query(False)):
        if not confirm:  # destructive: needs explicit confirm (§7.2)
            raise HTTPException(400, "refused: destructive delete requires ?confirm=true")
        try:
            await svc.delete_file("/" + name.lstrip("/"))
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))
        return {"deleted": name}

    @r.post("/api/config/format-sd")
    async def format_sd(confirm: bool = Query(False)):
        if not confirm:  # DESTRUCTIVE: wipes the card
            raise HTTPException(400, "refused: SD format requires ?confirm=true")
        await svc.cgi.set("SD0", "format")
        return {"formatting": True}

    @r.get("/api/camera/defaults")
    async def get_defaults():
        """What the last enforcement pass actually achieved, per setting, with the
        reason each setting exists and what is deliberately left alone."""
        return svc.defaults_report

    @r.post("/api/camera/defaults")
    async def post_defaults(include_cold: bool = Query(True), reprobe: bool = Query(False)):
        """Re-apply now. `reprobe=true` ignores the cached 'this firmware ignores it'
        verdicts and probes every candidate again — worth doing once after a
        firmware change."""
        try:
            return await svc.apply_defaults(reason="manual", include_cold=include_cold, reprobe=reprobe)
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))

    @r.post("/api/camera/probe")
    async def probe(
        prop: str = Query(...),
        values: str = Query(...),
        read: str | None = Query(None),
        dwell: float = Query(0.0),
        restore: bool = Query(True),
    ):
        """Discovery for a property whose valid values or semantics are unknown
        (spec §7) — e.g. whether LCDPower=OFF blanks the screen or stops it blanking.
        Use `dwell` to hold each value long enough to watch the physical camera."""
        vals = [v.strip() for v in values.split(",") if v.strip()]
        if not vals:
            raise HTTPException(400, "values must be a non-empty comma-separated list")
        if prop == "SD0":
            # Returns success immediately and wipes the card in the background, so
            # it would not look like a mistake until the footage was already gone.
            raise HTTPException(400, "refused: SD0 is destructive — use /api/config/format-sd")
        try:
            return await svc.probe_property(prop, vals, read=read, dwell_s=min(dwell, 30.0), restore=restore)
        except (CgiError, CameraUnavailable) as e:
            raise HTTPException(502, str(e))

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
                await ws.receive_text()  # ignore client input; keep the socket open
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            svc._subs.discard(ws)

    return r

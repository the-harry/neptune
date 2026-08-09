"""Async CGI client for the WOLFANG camera.

Design forced by the server's quirks (spec §3.3):
  * single-threaded server → ALL calls run through one worker, serialized. A
    priority queue lets user commands jump ahead of telemetry polls.
  * says `Connection: close` but mishandles keep-alive → keep-alive disabled,
    every request has an explicit timeout.
  * some ops block for seconds → slow ops get the long timeout + a settle sleep.
  * circuit breaker → after a timeout, stop calling for a cooldown and report
    degraded, so requests don't pile onto a stalled camera.
Every call is logged with duration for re-validating the timing table.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from .config import READ_TO_WRITE, SLOW_PROPERTIES, WRITE_TO_READ, cam_settings

log = logging.getLogger("neptune.cam.cgi")

# A QUEUED REQUEST MAY WAIT FOR OTHERS AHEAD OF IT, so the outer budget is a
# multiple of the single-request timeout rather than equal to it. Generous enough
# that a busy queue is never mistaken for a dead one; finite so a dead one is
# always eventually named.
QUEUE_WAIT_FACTOR = 4.0

PRIORITY_USER = 0
PRIORITY_TELEMETRY = 10


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    thunk: object = field(compare=False)
    future: asyncio.Future = field(compare=False)


class CgiResponse:
    def __init__(self, code: int, status: str, data: dict[str, str]):
        self.code = code
        self.status = status
        self.data = data

    @property
    def ok(self) -> bool:
        return self.code == 0


def parse_cgi(text: str) -> CgiResponse:
    """Tolerant parser: first line=code, second=status, rest k=v (split on FIRST
    '='). Body lines without '=' are appended to the previous value so a
    multi-line WarningMSG survives; blank lines never crash it."""
    # Strip the body's trailing newline(s) so the final pair's value isn't
    # polluted; internal blank lines (multiline WarningMSG) are still preserved.
    lines = text.rstrip("\r\n").split("\n")
    try:
        code = int(lines[0].strip()) if lines else -1
    except ValueError:
        code = -1
    status = lines[1].strip() if len(lines) > 1 else ""
    data: dict[str, str] = {}
    last_key: str | None = None
    for line in lines[2:]:
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            data[k] = v.rstrip("\r")
            last_key = k
        elif last_key is not None:
            data[last_key] += "\n" + line.rstrip("\r")   # continuation
    return CgiResponse(code, status, data)


from .models import CameraUnavailable, CgiError  # noqa: E402  (avoid cycle at top)


class CgiClient:
    def __init__(self) -> None:
        self._base = cam_settings.base_url.rstrip("/")
        # keep-alive OFF (§3.3b): no pooled connections, explicit Connection: close.
        self._client = httpx.AsyncClient(
            headers={"Connection": "close", "User-Agent": "okhttp/3.11.0"},
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=1),
        )
        self._queue: asyncio.PriorityQueue[_Job] = asyncio.PriorityQueue()
        self._seq = 0
        self._worker: asyncio.Task | None = None
        # circuit breaker
        self._breaker_open_until = 0.0

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            # CLEAR IT, or this client can never be started again. start() is guarded
            # on `self._worker is None`, so a cancelled-but-still-referenced task made
            # the guard see a worker that would never run: a second start() in one
            # process created nothing, and _enqueue() then parked a job on a queue with
            # no reader. CameraService.start() calls refresh_menu() on the way up, so
            # the app's lifespan never completed and the server never came up at all.
            # Intermittent, because _enqueue short-circuits while the breaker is still
            # open — a second boot seconds later raises and carries on, one minutes
            # later hangs forever.
            self._worker = None
        await self._client.aclose()

    # ---- public verbs -----------------------------------------------------
    async def get(self, prop: str, *, priority: int = PRIORITY_USER) -> CgiResponse:
        return await self._enqueue(priority, {"action": "get", "property": prop}, prop)

    async def set(self, prop: str, value: str, *, priority: int = PRIORITY_USER) -> CgiResponse:
        return await self._enqueue(priority, {"action": "set", "property": prop, "value": value}, prop)

    async def delete(self, dollar_path: str, *, priority: int = PRIORITY_USER) -> CgiResponse:
        return await self._enqueue(priority, {"action": "del", "property": dollar_path}, "del")

    async def dir(self, prop: str, frm: int = 0, count: int = 100, *, priority: int = PRIORITY_USER) -> str:
        params = {"action": "dir", "property": prop, "format": "all",
                  "from": str(frm), "count": str(count), "backward": ""}
        return await self._enqueue(priority, params, prop, raw=True)

    async def read_value(self, write_prop: str, *, priority: int = PRIORITY_USER) -> str | None:
        """Read a setting back using its READ name when it differs from WRITE."""
        read_prop = WRITE_TO_READ.get(write_prop, write_prop)
        resp = await self.get(read_prop, priority=priority)
        return resp.data.get(read_prop)

    # ---- serializer -------------------------------------------------------
    async def _enqueue(self, priority: int, params: dict, prop_for_timing: str, raw: bool = False):
        now = time.monotonic()
        if now < self._breaker_open_until:
            raise CameraUnavailable(f"circuit breaker open ({self._breaker_open_until - now:.1f}s left)")
        self._seq += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        job = _Job(priority, self._seq, (params, prop_for_timing, raw), fut)
        await self._queue.put(job)
        # BOUNDED, because an unbounded await here trusts the worker to exist. It did
        # not always: a queue with no reader parked every caller forever, and the first
        # caller is CameraService.start()'s refresh_menu(), so the whole app's lifespan
        # hung and the server never came up. The worker's own per-request timeouts are
        # in _do(); this is the outer guarantee that a caller ALWAYS gets an answer,
        # including the answer "nothing is draining this queue".
        budget = (cam_settings.timeout_slow_s if self._is_slow(params)
                  else cam_settings.timeout_fast_s) * QUEUE_WAIT_FACTOR
        try:
            return await asyncio.wait_for(fut, timeout=budget)
        except asyncio.TimeoutError:
            fut.cancel()
            raise CameraUnavailable(
                f"the camera queue did not answer {prop_for_timing!r} within {budget:.0f}s — "
                f"nothing is draining it") from None

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            params, prop, raw = job.thunk
            try:
                result = await self._do(params, prop, raw)
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # noqa: BLE001
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def _is_slow(self, params: dict) -> bool:
        prop = params.get("property", "")
        return prop in SLOW_PROPERTIES

    async def _do(self, params: dict, prop: str, raw: bool):
        slow = self._is_slow(params)
        timeout = cam_settings.timeout_slow_s if slow else cam_settings.timeout_fast_s
        url = f"{self._base}/cgi-bin/Config.cgi?{urlencode(params)}"
        t0 = time.monotonic()
        try:
            r = await self._client.get(url, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self._breaker_open_until = time.monotonic() + cam_settings.breaker_cooldown_s
            log.warning("CGI %s %s FAILED in %.0fms (%s) — breaker open %.0fs",
                        params.get("action"), prop, (time.monotonic() - t0) * 1000, exc,
                        cam_settings.breaker_cooldown_s)
            raise CameraUnavailable(str(exc)) from exc
        dt_ms = (time.monotonic() - t0) * 1000

        if raw:
            log.info("CGI dir %s -> %d in %.0fms", prop, r.status_code, dt_ms)
            body = r.text
            if slow:
                await asyncio.sleep(cam_settings.settle_after_slow_s)
            return body

        resp = parse_cgi(r.text)
        log.info("CGI %s %s -> code=%d in %.0fms%s",
                 params.get("action"), prop, resp.code, dt_ms, " [slow]" if slow else "")
        if slow:
            await asyncio.sleep(cam_settings.settle_after_slow_s)  # let the camera settle
        if not resp.ok:
            raise CgiError(resp.code, resp.status)
        return resp

    @property
    def degraded(self) -> bool:
        return time.monotonic() < self._breaker_open_until

"""Navigation service (spec §10.1) — sensor ingest, dead reckoning, snapping,
dive logging — over REST + a nav WebSocket. Plus the area manager (§10.2) and the
readiness check (§9). Mounts into the existing FastAPI app.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode as _urlencode

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)

from . import areas as areamod
from . import nominal as nominalmod
from . import satellite as satmod
from .config import settings
from .divelog import DiveLog
from .estimator import make_estimator
from .models import Adjustment, FlowVector, NavState, Origin, ReadinessItem, ReadinessResult
from .sensors import SimSensorSource, get_sensor_source
from .speedlut import DEFAULT_LUT, SpeedLUT

if TYPE_CHECKING:                     # which class this is depends on NAV_FILTER at runtime;
    from .estimator import Estimator  # only the update(SensorSample)->NavState shape matters here

log = logging.getLogger("neptune.nav")

# How many dead-reckoning periods a NavState may age before it stops counting as
# live (see NavService.fresh_state). Three, because one missed tick is ordinary —
# a slow I2C read, a GC pause, a sensor source that returned None for a moment —
# and blanking the operator's speed readout on every hiccup would train them to
# ignore the blank. Three is still short: at the default 10 Hz a loop that has
# actually stopped is caught in 0.3 s, long before anyone can act on the number.
_STATE_MAX_AGE_TICKS = 3.0

# How often a repeating tick fault may be logged at ERROR. A dead I2C bus is dead
# at dr_hz, and ten identical tracebacks a second is a log nobody can read the
# failure out of — the first one is the finding, the next six hundred are noise.
_FAULT_LOG_GAP_S = 10.0


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
        # Still called `dr`: it IS the dead reckoner under the default backend, and
        # the flow endpoint and the client both reach it by that name. WHICH estimator
        # it is, though, is a config decision (NAV_FILTER), so nothing in this file
        # constructs one directly — every path goes through make_estimator.
        self.dr: Estimator | None = None
        self.dive: DiveLog | None = None
        self.last_state: NavState | None = None
        # WHEN last_state was produced, on the monotonic clock. A NavState carries
        # its own `t` (seconds since dive start), which cannot answer "is this
        # current?" — it stops advancing the instant the loop does, and after a
        # dive it is a number about a dive that ended. Freshness needs a clock that
        # keeps running when navigation does not.
        self.last_state_ts: float | None = None
        # Guard the divide: a hand-set NAV_DR_HZ of 0 would otherwise turn the
        # freshness window into an exception at the worst possible moment.
        self.state_max_age_s = _STATE_MAX_AGE_TICKS / max(0.1, settings.dr_hz)
        # WHEN THE LOOP LAST TURNED, which is a different question from when it
        # last produced a NavState. A loop that is alive and failing every tick
        # (dead sensor bus) and a loop that is gone (task ended) both stop
        # publishing states, and they are fixed at opposite ends of the tether —
        # one is a wiring job at the sub, the other is a bug up here. Nothing could
        # tell them apart before, so both were reported as "no state", i.e. as
        # nothing at all.
        self.last_tick_ts: float | None = None
        self.tick_faults = 0                    # ticks that raised, cumulative
        self.last_fault: str | None = None      # and what the most recent one said
        self.last_fault_ts: float | None = None
        self._fault_logged_at: float | None = None
        # Why the loop task ended, once it has. None while it is still running —
        # and still None if it was never started, which loop_state() separates.
        self.loop_end_reason: str | None = None
        self.last_sample = None            # latest raw sensor sample (heading0/IMU cal, even with no dive)
        self.active_area: str | None = None
        self.centreline: list[tuple[float, float]] | None = None   # [lon,lat]
        self._subs: set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        # ---- the bootstrap fetch (§3), which is NOT part of the runtime path ----
        # ONE job at a time, deliberately: every source it drives is rate-limited
        # against somebody's free public service, and two jobs racing would double
        # the request rate while halving the chance of either finishing. It lives
        # on the service rather than in a module global so a test can build a
        # second NavService without inheriting the first one's download.
        self.fetch: AreaFetch | None = None
        self._fetch_task: asyncio.Task | None = None
        self._autofetch_task: asyncio.Task | None = None
        self.last_fetch: dict | None = None     # the last FINISHED job's snapshot
        # ---- and the NATIONAL fetch, which belongs to no area at all ----
        # Its own job, on its own task, because it is not per-area and must not wait
        # for one: the whole Trust network is fetched ONCE on launch and every area
        # afterwards draws from it. One handle, so the launch hook, the console's
        # button and an area fetch that finds the set incomplete all drive the SAME
        # download rather than three racing each other at a rate limit built for one.
        self.national: NationalFetch | None = None
        self._national_task: asyncio.Task | None = None   # the DOWNLOAD
        self._national_boot: asyncio.Task | None = None   # the launch-time decision
        self.last_national: dict | None = None
        # Until when each area is left alone after a no-internet verdict. The
        # console re-POSTs the stored origin on every page load (navui.js does it in
        # autoRequestOrigin, and again from the location watch), so without this a
        # handheld sitting on a bank would buy a four-second DNS timeout per fix.
        self._offline_until: dict[str, float] = {}

    async def start(self) -> None:
        for d in (settings.data_dir, settings.areas_dir, settings.dives_dir, settings.speed_lut_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._recover_orphans()
        self._task = asyncio.create_task(self._loop())
        # THE LOOP IS NOT ALLOWED TO END QUIETLY. asyncio hands a task's exception
        # to whoever awaits it, and nobody awaits this one, so a navigation loop
        # that died took the reason to the grave — the service object stayed
        # standing with every attribute holding its final value, which reads
        # exactly like a healthy service. The callback is the only place that
        # notices, so it both shouts and records.
        self._task.add_done_callback(self._loop_ended)
        # Log the SOURCE and whether it is simulated separately. Collapsing them into
        # one word printed "sensors=sim" while VehicleSensorSource was actually in use
        # (is_sim is true whenever the vehicle hardware is mocked), which is exactly the
        # confusion that hid the scripted-path bug for so long. The estimator backend
        # goes on the same line for the same reason: "dr" and "filtered" draw
        # DIFFERENT tracks from byte-identical samples, so a log that does not name
        # the one that ran leaves nothing to argue with when the track looks wrong.
        log.info("nav service started (source=%s, simulated=%s, filter=%s, autolog=%s)",
                 type(self.sensors).__name__, self.sensors.is_sim,
                 settings.filter_backend, settings.autolog)
        # ---- THE WHOLE CANAL & RIVER TRUST NETWORK, FETCHED ON LAUNCH ----
        # This line is the decision. The maps are how this thing is navigated — real
        # sub, simulator, or a bench at home planning a run — so they are not something
        # to go and get once a destination has been chosen. They are simply present, and
        # the moment to make that true is when the map backend starts.
        #
        # IT DOES NOT BLOCK ANYTHING. A task, created after the dead-reckoning loop is
        # already running, that reads the disk first and touches the network only if
        # something is actually missing. Every request inside crt.py goes through
        # asyncio.to_thread and sleeps between calls, so the 10 Hz tick keeps its slot
        # for the whole 140 MB — and the download resumes across launches, so being
        # killed half way costs nothing but the page that was in flight.
        if settings.crt_national_auto:
            # Held on the service and not dropped on the floor: asyncio keeps only a
            # weak reference to a task, and a fire-and-forget one can be collected
            # mid-await — which would look exactly like a bootstrap that decided to do
            # nothing and said nothing about why.
            self._national_boot = asyncio.create_task(self._national_bootstrap())
        else:
            log.info("the national Canal & River Trust fetch is switched off "
                     "(NAV_CRT_NATIONAL_AUTO=0) — what is on this card is what there is")

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
        # The fetch goes first. It is the only task here that holds a half-written
        # area on disk, and AreaFetch.run records "cancelled" into the area's own
        # metadata on the way out — so a Pi shut down mid-download says so next
        # boot instead of leaving a record that reads as a download still running.
        await self.cancel_fetch()
        # The national fetch goes next, and it is CANCELLED rather than waited for: it
        # is resumable by construction, so a shutdown costs the page in flight and
        # nothing else. crt.download_national stamps its index "interrupted" on the way
        # out so the next launch says so instead of showing a download that is not
        # running.
        for t in (self._national_boot, self._national_task):
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
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
            # ONE BAD TICK IS NOT THE END OF NAVIGATION. Everything inside _tick
            # can raise on a real dive — a sensor source hitting a dead I2C bus, an
            # estimator meeting a NaN, a dive journal on a full SD card, a socket
            # that dies mid-broadcast — and until now any one of them ended the
            # task outright. Nobody awaits this task, so it ended in silence, and
            # the whole service went on LOOKING alive: last_state is a plain
            # attribute and keeps its final value forever, so a map frozen at the
            # instant of the fault kept being read as the sub's position.
            #
            # A tick that raises publishes nothing, which is the honest outcome:
            # fresh_state() ages out three periods later and every reader gets
            # cannot-tell. What must NOT happen is the loop leaving with it.
            try:
                await self._tick(dt, i, bcast_every)
            except asyncio.CancelledError:      # shutdown, not a fault — let it through
                raise
            except Exception as exc:  # noqa: BLE001 — see _note_fault
                self._note_fault(exc)
            # Stamped whether the tick worked or not, and deliberately outside the
            # guard: this answers "is the loop still turning", which is a different
            # question from "did it produce anything". Alive-and-failing is a
            # sensor fault at the sub; gone is a software fault up here.
            self.last_tick_ts = time.monotonic()
            i += 1
            await asyncio.sleep(dt)

    async def _tick(self, dt: float, i: int, bcast_every: int) -> None:
        """One dead-reckoning period. Anything in here may raise; see _loop."""
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
            # Stamped from the same clock fresh_state() reads, on the same line
            # that publishes the state: any gap between the two is a window in
            # which a stale state looks current to everything downstream.
            self.last_state_ts = time.monotonic()
            if self.dive is not None:
                self.dive.add(ns, s)   # `s` = the raw sample: calibration needs it
            if i % bcast_every == 0 and self._subs:               # decouple redraw from DR rate (§7.5)
                await self._broadcast(json.dumps(self.nav_frame(ns)))

    def _note_fault(self, exc: BaseException) -> None:
        """Record a tick that raised, and say so out loud the first time.

        Kept as a count and a message rather than only a log line, because the
        thing that has to reach the operator is not this exception — it is that
        navigation is no longer answering about their sub. health() carries that
        topside; the log carries the traceback to whoever fixes it afterwards.
        """
        now = time.monotonic()
        self.tick_faults += 1
        self.last_fault = f"{type(exc).__name__}: {exc}"
        self.last_fault_ts = now
        if self._fault_logged_at is None or (now - self._fault_logged_at) >= _FAULT_LOG_GAP_S:
            log.error("navigation tick failed (%d so far) — the loop continues, but it is "
                      "publishing nothing: %s", self.tick_faults, self.last_fault, exc_info=True)
            self._fault_logged_at = now

    def _loop_ended(self, task: asyncio.Task) -> None:
        """The navigation loop has stopped. Nothing may find that out by inference.

        _loop is written not to end, but "written not to" is not a guarantee: a
        MemoryError, a bug in the fault handler itself, or a cancel from anywhere
        still finishes the task. An asyncio task nobody awaits swallows whatever
        killed it, and a NavService whose loop is gone is indistinguishable — from
        the outside — from one that is simply between dives. That is the shape of
        every fault in this round: the failure removes the signal instead of
        raising one.
        """
        if task.cancelled():
            self.loop_end_reason = "cancelled"
            log.info("navigation loop cancelled (shutdown)")
            return
        exc = task.exception()
        if exc is None:
            self.loop_end_reason = "returned"
            log.error("navigation loop RETURNED — it is an infinite loop, so this is a bug; "
                      "the map and every nav field topside now report cannot-tell")
            return
        self.loop_end_reason = f"{type(exc).__name__}: {exc}"
        log.error("navigation loop DIED: %s — no position, speed, snag or heading estimate "
                  "will be produced until the service is restarted", self.loop_end_reason,
                  exc_info=exc)

    # ---- what navigation can currently be asked --------------------------
    @property
    def reads_vehicle(self) -> bool:
        """Do the samples come off THIS hull, or out of a script?

        Not the same question as `sensors.is_sim`, and confusing the two is what
        this exists to stop. is_sim asks "are these numbers invented" — it is true
        for the scripted simulator AND for a live source pointed at MockHardware,
        and it is what the SIM badge hangs on. This asks something narrower: is the
        estimate ABOUT the vehicle whose telemetry frame we are filling in. A
        VehicleSensorSource reading a mocked hull says yes — the estimate and the
        frame describe the same (pretend) sub, and they agree. The scripted
        simulator says no whatever the hull is: its heading is a canned leg, and
        stamping that onto a real hull's frame with mock=false is a simulation
        presented as a measurement.
        """
        return not isinstance(self.sensors, SimSensorSource)

    def loop_state(self) -> str:
        """"never-started" | "running" | "stalled" | "stopped".

        Four states because they mean four different things to whoever is holding
        the controller. never-started: nav is not part of this process. running:
        navigation is turning (it may still have nothing to say — no origin, no
        dive — which fresh_state() answers separately). stalled: the task is alive
        but has not completed a period recently, i.e. something in the loop is
        blocking. stopped: the task has ended and nothing will be produced again.
        """
        if self._task is None:
            return "never-started"
        if self._task.done():
            return "stopped"
        if self.last_tick_ts is None:
            return "running"        # created, first period not finished yet
        return "running" if (time.monotonic() - self.last_tick_ts) <= self.state_max_age_s else "stalled"

    def health(self) -> dict:
        """Navigation's account of ITSELF, for anything that reports nav topside.

        The rule this serves: a signal whose source is absent shows cannot-tell,
        never a plausible number — and "absent" includes "was here and stopped".
        A caller that only ever asks fresh_state() cannot obey it, because None
        from there covers "no origin yet", "between dives", "the sensor bus is
        down" and "the loop is dead" with one silence. Those are four different
        situations and only one of them is normal.
        """
        try:
            # is_sim is a PROPERTY on the live sources and reaches through the
            # vehicle handle to ask the hardware. Cheap, but not guaranteed not to
            # raise — and this is called from the control loop, which must never be
            # stopped by anything navigation does. Unknown provenance is treated as
            # simulated, because the direction to be wrong in is the one that keeps
            # the SIM badge on.
            simulated = bool(self.sensors.is_sim)
        except Exception:  # noqa: BLE001
            simulated = True
        return {
            "loop": self.loop_state(),
            "answering": self.fresh_state() is not None,
            "source": type(self.sensors).__name__,
            "simulated": simulated,
            "reads_vehicle": self.reads_vehicle,
            "tick_faults": self.tick_faults,
            "last_fault": self.last_fault,
            "loop_end_reason": self.loop_end_reason,
        }

    def nav_frame(self, ns: NavState) -> dict:
        """One /ws/nav frame: the estimate, plus what the estimate is worth.

        `simulated` rides on EVERY frame rather than being announced once at
        connect. The map is heading-up and draws this track as the sub's own, so a
        frame that does not carry its own provenance is a frame the map has to
        guess about — and a scripted path drawn over a real dive is the one lie
        this project refuses to allow. `reads_vehicle` is the sharper of the two:
        it is false exactly when the track belongs to no hull at all.

        ONE VEHICLE MUST NOT DESCRIBE ITSELF TWO DIFFERENT WAYS ON TWO SOCKETS. The
        map and the HUD write the same state slots (client/js/map.js says so in as
        many words), so whichever frame lands last wins — and with the BNO085 killed
        mid-dive the two disagreed. Measured on a live server, same instant:

            /ws/control  heading=None mag_cal=None gyro_only=None snagged=False
            /ws/nav      heading_deg=None mag_cal=None gyro_only=False snagged=False

        api/main.py nulls gyro_only the moment no bearing survives, for the reason
        stated there: the flag qualifies a heading, a coast is an integration, and
        both the fused yaw and the yaw rate come off the one chip — so there is
        neither a bearing to qualify nor a gyro to coast on. This frame carried the
        estimator's raw False instead, which the console reads as "the filter looked
        and it is using the compass". A reassuring answer put into the mouth of a
        subsystem that is saying nothing.

        snagged is deliberately NOT nulled here: it agrees on both sockets (False
        above) because the snag detector reads thrust against the paddlewheel and
        never touches the compass. Nulling it would invent the very contradiction
        this is fixing, in the other direction.

        THE CHEAPEST WAY TO OBEY THAT RULE IS NOT TO SHARE THE FACT AT ALL, which
        is why the IMU's raw channels — gyro_z_dps, accel_fwd_ms2, pitch_deg,
        roll_deg — are not in this frame even though every one of them arrives on
        the SensorSample that produced the state. They travel on /ws/control, off
        the hardware handle, with heading and mag_cal (see protocol.py and
        models.NavState). Nothing here has to keep them consistent with anything,
        because there is only one copy. A reading this service publishes would also
        inherit the fresh_state() gate below, and that gate answers "is the
        ESTIMATE current" — it would blank a live attitude for want of an origin.
        """
        f = {"type": "nav", **ns.model_dump(),
             "simulated": bool(self.sensors.is_sim),
             "reads_vehicle": self.reads_vehicle}
        if ns.heading_deg is None:
            f["gyro_only"] = None
        return f

    def fresh_state(self) -> NavState | None:
        """last_state, but ONLY if the loop that produced it is still running.

        `last_state` on its own cannot answer "is this current?" — it is a plain
        attribute that holds its final value forever. A dive that ended, or a
        _loop that died on an exception, leaves behind a snapshot indistinguishable
        from a live one, and every consumer that reads the attribute directly then
        presents a frozen speed and a latched `snagged` as present-tense readings.
        That is the worst kind of wrong: it looks like a measurement.

        So anything that shows nav's answers to an operator asks HERE, not there.
        None means "navigation is not telling us right now" — the honest answer,
        and the one that travels topside as null / cannot-tell rather than as a
        plausible number.
        """
        ns, ts = self.last_state, self.last_state_ts
        if ns is None or ts is None:
            return None
        # monotonic at both ends on purpose: the wall clock steps on this vehicle
        # (no RTC on some builds, so the clock is set from the topside handshake
        # after boot) and a backwards step would pin a dead state at "fresh".
        return ns if (time.monotonic() - ts) <= self.state_max_age_s else None

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
        self.dr = make_estimator(self.origin, self.speed_lut, self.flow,
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
        # The estimator is gone, so its last answer is no longer about anything.
        # Left standing it OUTLIVES the dive: telemetry kept stamping that final
        # speed / speed_src / snagged / gyro_only into every frame, so a dive that
        # ended snagged reported snagged=true until the process restarted. The
        # timestamp goes with it so nothing can be fooled by a state with no clock.
        #
        # last_sample deliberately does NOT go: it is refreshed every tick whether
        # or not a dive is running, and /api/origin (heading0) and the readiness
        # check (mag_cal) are exactly the things you use BETWEEN dives.
        self.last_state = None
        self.last_state_ts = None
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

    # ---- the bootstrap fetch, driven from here ----------------------------
    #
    # WHY ALL OF THIS IS A TASK AND NONE OF IT IS AN AWAIT ON A REQUEST. The one
    # thing this console may never do is stop flying the sub. A satellite pyramid
    # is a thousand rate-limited HTTP requests and a full CRT run is a few hundred
    # more; at six a second that is minutes, and any endpoint that awaited it would
    # hold a worker for the whole of it while the operator's finger is on the
    # throttle. So every entry point below returns as soon as it has WRITTEN DOWN
    # what it is going to do, and the doing happens on a task that shares the loop
    # with the dead reckoner exactly the way _loop does — every network call inside
    # satellite.py and crt.py already goes through asyncio.to_thread, and both
    # sleep between requests, so the 10 Hz tick keeps its slot throughout.

    def fetch_state(self) -> dict:
        """What the fetch job is doing, or what the last one did. Never None-shaped.

        One document whether or not anything is running, because the console binds
        a panel to this and a missing key is a panel that renders blank — which
        looks like "nothing is wrong" and is indistinguishable from "nobody asked".
        """
        if self.fetch is not None:
            return {**self.fetch.snapshot(), "running": self.fetch.is_running}
        if self.last_fetch is not None:
            return {**self.last_fetch, "running": False}
        return {
            "scope": "area",
            "state": "idle", "running": False, "area": None, "sources": {},
            "title": ("No offline-data fetch has run in this session. That does not mean "
                      "the card is empty and it does not mean it is full — ask "
                      "/api/areas/<name>/complete which of the three sources are "
                      "actually on it."),
            "aria_label": ("No download job has run since this service started. The state "
                           "of the card is a separate question, answered by the area "
                           "completeness endpoint."),
        }

    async def _fetch_changed(self, snap: dict) -> None:
        """One progress step: to the card, then to every console watching.

        The card first, on purpose. A broadcast reaches whoever happens to be
        connected right now; the area's own metadata is what the NEXT process
        reads, and an interrupted download that left no trace on disk is one the
        operator finds out about by noticing a hole in the map.
        """
        try:
            _record_fetch(snap["area"], snap)
        except Exception as exc:  # noqa: BLE001 — a fetch must not die of its own bookkeeping
            log.warning("could not record fetch progress for %s: %s", snap.get("area"), exc)
        await self._broadcast(json.dumps({"type": "area_fetch", **snap}))

    async def start_fetch(self, area: str, bbox: list[float], zmin: int, zmax: int,
                          *, refresh: bool = False, reason: str = "",
                          radius_m: float | None = None,
                          net: tuple[bool, str] | None = None) -> dict:
        """Begin one background fetch. Returns immediately, with what it started."""
        if self.fetch is not None and self.fetch.is_running:
            # NOT an error, and not a queue either. A second request for the SAME
            # area while the first is still running is what a double-tap looks
            # like, and the honest answer to it is the job that is already going.
            return {**self.fetch.snapshot(), "running": True, "started": False,
                    "why": f"a fetch for {self.fetch.area!r} is already running — "
                           f"this one was not started, because these are rate-limited "
                           f"public services and two jobs would halve the rate each"}
        job = AreaFetch(area, bbox, zmin, zmax, refresh=refresh, reason=reason,
                        radius_m=radius_m, on_change=self._fetch_changed)
        self.fetch = job
        self._fetch_task = asyncio.create_task(job.run(net=net))
        self._fetch_task.add_done_callback(self._fetch_ended)
        return {**job.snapshot(), "running": True, "started": True}

    def _fetch_ended(self, task: asyncio.Task) -> None:
        """The download task is over, however it ended.

        Same reasoning as _loop_ended: nobody awaits this task, so an exception in
        it would be swallowed and the job would sit at "running" forever — a
        progress bar that never moves and never says why, which is the exact shape
        of failure this whole subsystem refuses. AreaFetch.run is written not to
        raise; this is what catches it being wrong about that.
        """
        job = self.fetch
        if job is None:
            return
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                job.crash(exc)
                log.error("area fetch for %s DIED: %s", job.area, exc, exc_info=exc)
        self.last_fetch = job.snapshot()
        try:
            _record_fetch(job.area, self.last_fetch)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record the finished fetch for %s: %s", job.area, exc)
        # Re-read the card. The centreline this job may have just downloaded is the
        # estimator's snapping target, and activate_area is the only thing that
        # loads it — without this the operator has to restart the service to use
        # data that is already sitting on the disk.
        if self.active_area == job.area:
            self.activate_area(job.area)

    # ---- the national fetch: one job, three ways in -------------------------
    #
    # WHO STARTS IT. The launch hook (start(), above), the console's own button
    # (POST /api/crt/fetch), and an area fetch that finds the national set incomplete.
    # All three go through ensure_national(), which starts one job or hands back the
    # one already running, because these are rate-limited public services and two jobs
    # would halve the rate each while doubling the chance neither finishes.

    def national_state(self) -> dict:
        """What the national fetch is doing, or what is on the card. Never None-shaped.

        One document whether or not anything is running, for the same reason
        fetch_state() is: the console binds a panel to this, and a missing key is a
        panel that renders blank — which looks like "nothing is wrong" and is
        indistinguishable from "nobody asked".
        """
        crt = _crt_mod()
        card = _national_layers()
        live = self.national.snapshot() if self.national is not None else None
        if live is not None and self.national.is_running:
            return {**live, "running": True, "card": _national_summary(card)}
        base = live or self.last_national
        if base is not None:
            return {**base, "running": False, "card": _national_summary(card)}
        stale, why = (crt.national_is_stale() if crt else
                      (True, "api/nav/crt.py is not in this build"))
        return {
            "scope": "national", "state": "idle", "running": False, "area": None,
            "sources": {}, "order": [],
            "stale": stale, "why": why,
            "card": _national_summary(card),
            "title": (f"No national download has run in this session. "
                      f"{'The whole Trust network is on this handheld. ' if not stale else ''}"
                      f"{why}"),
            "aria_label": (f"No national Canal and River Trust download has run since "
                           f"this service started. {why}"),
        }

    async def _national_changed(self, snap: dict) -> None:
        """One progress step, out on the channel the area fetch already uses.

        THE SAME FRAME TYPE ON PURPOSE — {"type": "area_fetch", …} — so the panel the
        console already has renders this without a second mechanism being invented for
        it. `scope` is what tells the two apart: "national" here, "area" on the
        per-area job. Nothing is written into an area's metadata, because this download
        belongs to no area; crt.py rewrites the national index after every layer, which
        is what the NEXT process reads.
        """
        await self._broadcast(json.dumps({"type": "area_fetch", **snap}))

    async def ensure_national(self, *, reason: str = "", refresh: bool = False,
                              net: tuple[bool, str] | None = None) -> dict:
        """Start the national fetch, or hand back the one already running."""
        if self.national is not None and self.national.is_running:
            return {**self.national.snapshot(), "running": True, "started": False,
                    "why": ("the national fetch is already running — it is one download "
                            "for the whole country and every area draws from it")}
        job = NationalFetch(reason=reason, refresh=refresh, on_change=self._national_changed)
        self.national = job
        self._national_task = asyncio.create_task(job.run(net=net))
        self._national_task.add_done_callback(self._national_ended)
        return {**job.snapshot(), "running": True, "started": True}

    async def await_national(self) -> dict | None:
        """Wait for the running national fetch, whoever started it.

        This is how an area fetch gets its hazard layers without starting a second
        download: it joins the one in flight rather than racing it.
        """
        task = self._national_task
        if task is None:
            return None
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(task)
        return self.national.snapshot() if self.national else None

    def _national_ended(self, task: asyncio.Task) -> None:
        """The national download is over, however it ended. Same reasoning as
        _fetch_ended: nobody awaits this task, so an exception in it would be swallowed
        and the job would sit at "running" for the life of the process."""
        job = self.national
        if job is None:
            return
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                job.crash(exc)
                log.error("the national CRT fetch DIED: %s", exc, exc_info=exc)
        elif job.is_running:
            job.stopped()
        self.last_national = job.snapshot()

    async def _national_bootstrap(self) -> None:
        """Is the whole Trust network on this handheld? If not, get it. On launch.

        DISK FIRST, ALWAYS. The steady state — a complete card — must cost nothing at
        all: not a socket, not a DNS lookup, not the four seconds a dead resolver takes
        to admit it. Only a card that is actually missing something buys the probe.

        AND IT KEEPS ASKING, because a handheld is switched on in a car park with no
        bars far more often than it is switched on at a desk. One probe at startup would
        mean the country stays unfetched for the whole of a session that walked into
        signal ten minutes later, and the operator would find that out at the water.
        A TCP connect every ten minutes is nothing; the alternative is NOT DOWNLOADED
        being what you see because nothing has gone right yet.
        """
        crt = _crt_mod()
        if crt is None:
            return
        said: str | None = None
        while True:
            try:
                stale, why = crt.national_is_stale()
                if not stale:
                    log.info("national CRT layers: %s", why)
                    return
                if why != said:
                    log.info("national CRT layers need fetching: %s", why)
                    said = why
                ok, net_why = await internet_available()
                if ok:
                    await self.ensure_national(
                        reason="the map backend started and the national layers "
                               "were incomplete",
                        net=(ok, net_why))
                    snap = await self.await_national()
                    if (snap or {}).get("state") == "done":
                        return
                else:
                    # THE NORMAL CANAL-SIDE OUTCOME AND NOT A FAILURE. Nothing was
                    # attempted, so nothing failed. What IS on the card is drawn exactly
                    # as it is, dated; what is missing stays missing until there is a
                    # connection, and this comes back to ask again.
                    rec = _national_offline_record(why, net_why)
                    if self.last_national is None or not self.national:
                        self.last_national = rec
                    await self._broadcast(json.dumps({"type": "area_fetch", **rec}))
                    log.info("national CRT fetch not started: %s", net_why)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a service start must never fail here
                log.warning("the national CRT bootstrap failed: %s", exc, exc_info=True)
            await asyncio.sleep(_NATIONAL_RETRY_GAP_S)

    async def cancel_fetch(self) -> dict | None:
        """Stop the running job and wait for it to write down that it was stopped."""
        task, job = self._fetch_task, self.fetch
        if task is None or task.done():
            return None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — _fetch_ended has already recorded it
            pass
        return job.snapshot() if job else None

    def autofetch(self, origin: Origin) -> dict:
        """A launch point was set. Make sure this water's offline data exists.

        THIS IS THE WHOLE POINT OF THE ROUND, so it is worth saying what it is not:
        it is not a download. It is a decision, taken on a background task, about
        whether a download is needed — and on a canal bank the answer is almost
        always no, because the area is already complete or there is no internet to
        change that with. The origin is the trigger because it is the first moment
        anything on this vehicle knows WHERE it is going to be, which is the one
        fact an offline area needs and the reason data/areas/ has been empty since
        the day this console was written: nothing ever created one.
        """
        if not settings.area_auto:
            return {"scheduled": False,
                    "why": "automatic areas and fetching are switched off "
                           "(NAV_AREA_AUTO=0 / NAV_AUTOFETCH=0)"}
        # ---- 1. THE AREA, MADE HERE AND NOW, WITH NO NETWORK INVOLVED --------
        # Defining an area is writing down a plan: a box, a name and a state of
        # ABSENT. It needs a launch point and nothing else, which is exactly why it
        # must not wait behind an internet probe — the case with no signal is the
        # case where the area is created empty at the water and filled in later at
        # home, from a list, and an area that could not exist without a hotspot
        # would be missing from that list precisely when it was needed.
        # areas.create_area also decides REUSE: the same launch point twice is one
        # area, which matters because the console re-POSTs its stored origin on
        # every page load.
        try:
            plan = areamod.create_area(origin.lat, origin.lon)
        except ValueError as exc:      # not a place, or the box would be too big
            log.info("no area for %s,%s: %s", origin.lat, origin.lon, exc)
            return {"scheduled": False, "why": str(exc)}
        except Exception as exc:  # noqa: BLE001 — setting a datum must never fail here
            log.warning("area creation failed: %s", exc, exc_info=True)
            return {"scheduled": False, "why": f"the area could not be written: {exc}"}
        name = plan["name"]
        if self.active_area != name:
            self.activate_area(name)

        # ---- 2. IS ANYTHING ACTUALLY MISSING? Disk only. ---------------------
        # The canal-side steady state, and it has to be free: a console at the
        # water with a complete area must not spend one socket finding that out.
        comp = area_completeness(name)
        if comp["complete"]:
            return {"scheduled": False, "area": name, "action": plan["action"],
                    "why": (f"{name} is already complete — imagery, hazard charts and "
                            f"centreline are all on this card, so nothing was fetched "
                            f"and nothing was asked of the network"),
                    "complete": True}
        until = self._offline_until.get(name)
        if until is not None and time.monotonic() < until:
            return {"scheduled": False, "area": name, "action": plan["action"],
                    "why": (f"there was no internet {int(_OFFLINE_RETRY_GAP_S)}s ago and "
                            f"{name} is not being re-probed yet. No signal is the normal "
                            f"state here, and retrying it on every origin fix is how a "
                            f"console spends a dive on DNS timeouts")}

        # ---- 3. SAY DOWNLOADING BEFORE RETURNING -----------------------------
        # DOWNLOADING IS ITS OWN STATE and the operator has to be able to watch it
        # start. The probe that decides whether there is anything to download takes
        # up to four seconds, and this endpoint has a controller on the other end of
        # it, so the state is written here and the checking happens behind it. If
        # the probe then says there is no signal the job puts the area back to
        # ABSENT within those few seconds, with the reason on it — and areas.py's
        # own staleness rule catches the case where this process dies in between.
        try:
            areamod.set_area_state(
                name, "downloading",
                why=("checking for internet, then downloading this area's imagery, "
                     "hazard charts and centreline"))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not mark %s downloading: %s", name, exc)
        try:
            # Kept on the service, not dropped on the floor: asyncio holds only a
            # weak reference to a task, and a fire-and-forget one can be collected
            # mid-await — which would look exactly like a fetch that decided to do
            # nothing and said nothing about why.
            self._autofetch_task = asyncio.create_task(self._autofetch(origin, name))
        except RuntimeError:            # no running loop — nav embedded in a sync test
            areamod.set_area_state(name, "absent",
                                   why="no event loop was running to fetch on")
            return {"scheduled": False, "area": name,
                    "why": "no event loop is running to schedule it on"}
        return {"scheduled": True, "area": name, "action": plan["action"],
                "missing": comp["missing"], "bbox": plan.get("bbox"),
                "est_tiles": plan.get("est_tiles"), "est_mb": plan.get("est_mb"),
                "why": (f"{name} is missing {', '.join(comp['missing'])}. A background "
                        f"fetch was scheduled; it checks for internet first and does "
                        f"nothing at all if there is none"),
                "watch": "/api/areas/fetch"}

    async def _autofetch(self, origin: Origin, name: str) -> None:
        """The download decision, off the request path. Network only if needed."""
        try:
            if self.fetch is not None and self.fetch.is_running:
                return
            ok, why = await internet_available()
            if not ok:
                # THE NORMAL CANAL-SIDE OUTCOME, AND IT IS NOT A FAILURE. Nothing
                # was attempted, so nothing failed and nothing is in progress: the
                # area goes back to ABSENT, which is the true and useful claim that
                # there is a plan on this card and no data behind it yet. Reporting
                # it as a failed download would send the operator to try again;
                # what they actually need is to be somewhere with signal.
                self._offline_until[name] = time.monotonic() + _OFFLINE_RETRY_GAP_S
                rec = _offline_record(name, why, reason="a launch point was set")
                self.last_fetch = rec
                areamod.set_area_state(
                    name, "absent",
                    why=(f"nothing has been downloaded into this area yet, and there is "
                         f"no internet here to do it with: {why}"), fetch=rec)
                await self._broadcast(json.dumps({"type": "area_fetch", **rec}))
                log.info("auto-fetch not started for %s: %s", name, why)
                return
            self._offline_until.pop(name, None)
            meta = _area_meta(name) or {}
            bbox = meta.get("bbox")
            if not bbox:
                log.warning("area %s has no bbox — nothing to fetch for it", name)
                return
            zmin = int(meta.get("minzoom") or settings.sat_min_zoom)
            zmax = int(meta.get("maxzoom") or settings.sat_max_zoom)
            await self.start_fetch(name, bbox, zmin, zmax,
                                   radius_m=(meta.get("origin") or {}).get("radius_m"),
                                   reason="a launch point was set", net=(ok, why))
        except asyncio.CancelledError:
            # THE DECISION WAS KILLED BEFORE IT DECIDED, and the area is sitting
            # there saying DOWNLOADING because autofetch() said so before handing
            # over. A process shut down in this window — or an event loop closed
            # under it — would leave that word on the card with nothing behind it,
            # and areas.py would go on believing it for the whole of its staleness
            # window. Nothing was attempted, so the honest state is ABSENT: a plan
            # on the card with no data behind it yet.
            with contextlib.suppress(Exception):
                areamod.set_area_state(
                    name, "absent",
                    why=("the download was stopped before it started, so nothing has "
                         "been downloaded into this area yet"))
            raise
        except Exception as exc:  # noqa: BLE001 — setting an origin must never fail here
            log.warning("auto-fetch decision failed: %s", exc, exc_info=True)
            with contextlib.suppress(Exception):
                areamod.set_area_state(name, "failed",
                                       why=f"the fetch could not be started: {exc}")

    # ---- readiness (§9) ---------------------------------------------------
    def _hw(self):
        """The live vehicle's hardware layer, or None when nav runs standalone.

        Readiness has to ask the hardware things the sensor stream cannot answer —
        is the leak probe actually alive, has the ballast ever been homed — and nav
        is deliberately runnable with no vehicle bound, so "no answer" is a state
        every caller must handle. It is reached exactly the way the sensor source
        reaches it (rov.hw), because there is only one vehicle and one way to it.
        """
        try:
            rov = self._get_rov() if self._get_rov else None
            return getattr(rov, "hw", None)
        except Exception:  # noqa: BLE001 — a readiness check must never be the thing that crashes
            return None

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
        # 2b CRT hazard layers cached for this area (§3 bootstrap → §9 pre-dive)
        #
        # WHY THIS IS A GO/NO-GO ITEM AND NOT A NICETY. A canal is full of things
        # that will stop this vehicle and are invisible from the surface: sluice
        # intakes that pull, stop-plank grooves that eat a tether, culvert mouths,
        # weirs, safety gates. All of them are published, none of them is
        # reachable from the bank, and the console draws whatever is on the card.
        # An operator who has not fetched them sees a map with no hazards on it,
        # which is indistinguishable from a map of water with no hazards in it —
        # and the moment to find that out is before the water, because afterwards
        # there is no internet to fix it with.
        #
        # A DELIBERATE SKIP DOES NOT FAIL THIS. crt.py leaves out layers it judged
        # empty-by-design or licence-refused, on purpose, and those are decisions.
        # A layer whose fetch FAILED is different: no file was written (correctly —
        # a truncated hazard layer reads as an empty canal), so the map is quietly
        # missing a whole class of obstruction. That fails.
        #
        # AND A LAYER THAT IS ON THE CARD AND WILL NOT PARSE FAILS TOO. This check
        # used to ask the provenance whether any fetch had failed and the filesystem
        # whether the files existed, and never asked whether a single one of them
        # could be READ — so the one question that gets asked before a sub goes in the
        # water was answered by a stat(). A card pulled mid-write, a fetch killed by a
        # dying hotspot: the file has a size, the check went green, and the console
        # drew a clear channel. _crt_layers now parses each layer (once per version of
        # the file — see the cache above it) and reports the three states apart, and
        # this reads all three.
        # NO AREA IS REQUIRED TO ANSWER THIS ANY MORE. The Trust's layers are national
        # and held once, so "are the hazards on this handheld" is a question with an
        # answer whether or not a launch point has ever been set. It used to fail with
        # "no area is activated", which taught the operator that the gate was about
        # paperwork rather than about sluices.
        crt_block = _crt_layers(self.active_area or "")
        failed = crt_block.get("failed") or []
        corrupt = crt_block.get("unreadable") or []
        part = [p.get("layer_key") for p in crt_block.get("partial") or []]
        if crt_block["status"] != "present":
            hz_ok, hz_why = False, (crt_block.get("why", "not fetched")
                                    + " — an absent hazard layer is NOT a clear channel")
        elif part:
            # STILL COMING IS NOT A PASS AND IT IS NOT A FAILURE EITHER. It is "not
            # yet", and diving on a half-downloaded card is exactly what this gate is
            # written against: what has not landed draws as clear water.
            hz_ok = False
            hz_why = (f"{len(part)} layer(s) are still downloading "
                      f"({', '.join(part[:4])}) — the map draws what has landed and "
                      f"blanks the rest, which is missing data and not empty water")
        elif failed or corrupt:
            # BOTH sentences, when both apply. They send the operator to two different
            # jobs — one to the internet, one to the card — and a gate that reports
            # only the first leaves the second to be found in the water.
            hz_ok = False
            parts = []
            if failed:
                parts.append(f"{len(failed)} layer(s) did not download and no file was "
                             f"written for them ({', '.join(failed[:4])}) — nothing is "
                             f"known about those hazards here")
            if corrupt:
                parts.append(f"{len(corrupt)} layer(s) are on the card and CANNOT BE READ "
                             f"({', '.join(corrupt[:4])}) — a half-written hazard layer "
                             f"draws as an empty canal; delete those files and re-fetch "
                             f"while there is still internet")
            hz_why = "; ".join(parts)
        else:
            n = len(crt_block.get("layers") or [])
            hz_ok = True
            hz_why = (f"{n} layer(s) held "
                      f"{'NATIONALLY' if crt_block.get('scope') == 'national' else 'for this area'} "
                      f"and certified (not merely present on disk), fetched "
                      f"{crt_block.get('fetched')}; "
                      f"{len(crt_block.get('skipped') or [])} skipped on purpose")
        add("CRT hazard layers held AND readable (absent is not 'no hazards', "
            "and neither is corrupt)", hz_ok, hz_why)
        # 2c IS THIS AREA ACTUALLY FINISHED? The three items above each answer about
        # ONE source, and an operator reading three greens still has to work out
        # whether that is all of them — which is the question they actually have at
        # the water's edge, because everything above is downloaded at bootstrap and
        # NONE of it can be fixed once the hotspot is gone. This is the roll-up:
        # imagery, hazard charts and the waterway centreline, each present or not,
        # in one line with the missing ones named.
        #
        # A FETCH STILL RUNNING IS NOT A PASS AND IS NOT A FAILURE EITHER — it is
        # "not yet", and it fails this gate on purpose. Diving on a half-downloaded
        # card is exactly the thing the honesty doctrine above is written against:
        # the map draws what landed, and what has not landed yet draws as clear
        # water. area_completeness() reports downloading, interrupted and cancelled
        # as their own states so the sentence can say which.
        #
        # A SOURCE THIS MACHINE CANNOT BUILD AT ALL DOES NOT FAIL THIS, and it is
        # still named in the detail every time. That is the same judgement as the
        # deliberate-skip rule above it: this gate is for things somebody could have
        # done and did not, because those are fixable while there is still internet.
        # The vehicle is never given numpy, so the launch-bank overlay is permanently
        # unbuildable there — failing on it would leave a red line no dive could ever
        # clear, and a gate that is always red is a gate that gets waved through on
        # the day it is red about a sluice. area_completeness keeps the two apart, and
        # keeps a third case apart as well: a bank layer that is BUILT with holes in
        # the survey behind it, which is the ordinary outcome of a good build and is
        # reported in this line's detail rather than failing it.
        comp = area_completeness(self.active_area or "")
        add("offline area COMPLETE — imagery, hazard charts, the launch-bank overlay "
            "and the centreline all on this card and nothing still downloading",
            bool(comp["complete"]), comp["detail"])
        # 3 origin + accuracy
        add("origin set within accuracy threshold",
            bool(self.origin) and (self.origin.accuracy <= settings.max_origin_accuracy_m if self.origin else False),
            f"accuracy={self.origin.accuracy}m ≤ {settings.max_origin_accuracy_m}m" if self.origin else "no origin")
        # 4 heading0 + IMU cal
        mag_cal = self.last_sample.mag_cal if self.last_sample else None
        # The detail has to name the half that FAILED. Reporting "mag_cal=3" against a
        # red check because no origin was set sent the operator to re-calibrate a
        # compass that was already good — a check whose explanation points at the wrong
        # subsystem costs more than no explanation at all. mag_cal None is its own
        # answer: no IMU is reporting, which is not the same as one reporting badly.
        has_origin = bool(self.origin)
        cal_ok = mag_cal is not None and mag_cal >= 2
        if not has_origin and not cal_ok:
            why = f"no origin set, and mag_cal={'no IMU reporting' if mag_cal is None else mag_cal}"
        elif not has_origin:
            why = f"no origin set (the compass is fine, mag_cal={mag_cal})"
        elif not cal_ok:
            why = ("no IMU is reporting a calibration" if mag_cal is None
                   else f"mag_cal={mag_cal}, needs 2 or better")
        else:
            why = f"origin set, mag_cal={mag_cal}"
        add("heading0 captured + IMU cal good", has_origin and cal_ok, why)
        # 5 clock sane (RTC or bootstrap-set)
        add("system clock sane", time.time() > 1_700_000_000, time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
        # 6 speed LUT
        add("speed LUT loaded", self.speed_lut is not None, f"lut={self.speed_lut.id}")
        # 9 tether encoder zeroed
        add("tether encoder zeroed at launch",
            self.last_state is None or self.last_state.payout_m < 1.0,
            f"payout={self.last_state.payout_m if self.last_state else 0}m")
        hw = self._hw()
        # §5 leak probes — the one failure the two-probe design otherwise hides.
        # Both probes are pull-ups that only read wet when water bridges them, so a
        # cut lead or a shorted pair reads DRY FOREVER and the sub floods behind a
        # calm green panel. Nothing during the dive can distinguish that from a dry
        # hull; the only moment the difference is observable is before the water.
        probe_ok, probe_detail = False, "no vehicle bound — probes not readable"
        if hw is not None:
            try:
                fault = hw.leak_probe_fault()
                probe_ok = fault is None
                probe_detail = "both probes sane" if probe_ok else f"faulty probe: {fault}"
            except Exception as exc:  # noqa: BLE001
                probe_detail = f"probe read failed: {exc}"
        add("leak probes sane (a dead probe reads dry forever)", probe_ok, probe_detail)
        # §5 ballast homed. The syringe is an open-loop stepper with no position
        # sensor, so before homing the level is UNKNOWN, not empty — it could be
        # anywhere including full, which changes the sub's buoyancy and every depth
        # decision that follows. get_ballast_level() says None and this says so too.
        homed_ok, homed_detail = False, "no vehicle bound — ballast state unknown"
        rehome, rehome_detail = False, "no vehicle bound"
        if hw is not None:
            try:
                homed_ok = bool(hw.ballast_homed())
                level = hw.get_ballast_level()
                homed_detail = (f"level={level:.2f} of stroke" if homed_ok and level is not None
                                else "never homed — run ballast home before launch")
            except Exception as exc:  # noqa: BLE001
                homed_ok, homed_detail = False, f"ballast read failed: {exc}"
            try:
                rehome = bool(hw.ballast_needs_rehome())
                rehome_detail = ("re-home required — step count disagrees with the span"
                                 if rehome else "step count agrees with the span")
            except Exception as exc:  # noqa: BLE001
                rehome, rehome_detail = True, f"rehome flag unreadable: {exc}"
        add("ballast homed (position known)", homed_ok, homed_detail)
        # Its own line on purpose: homed-but-drifted is a different failure from
        # never-homed, and a skipped-step event that only reaches the log is an
        # event nobody sees at the water's edge.
        add("ballast step count trusted (no skipped steps)",
            hw is not None and not rehome, rehome_detail)
        # 7,8 camera preflight + video — cross-subsystem, checked by the camera plane; noted here
        add("camera pre-flight + video (see camera plane)", True, "run /api/preflight separately")
        passed = all(x.ok for x in items)
        return ReadinessResult(passed=passed, items=items)


# ==========================================================================
# Overlay layers — serving what BOOTSTRAP put on the card, and saying plainly
# when it put nothing there
# ==========================================================================
#
# THE ONE RULE EVERYTHING BELOW EXISTS FOR. A layer whose file is not on this card
# must never be answered with an empty FeatureCollection. "No sluices in this
# area" is a survey result somebody's fetch established; "the sluices never
# downloaded" is the absence of one — and they are opposite claims about the water
# the vehicle is about to go into. `features: []` says the first. Only the first
# is safe to fly on, and a renderer handed it cannot tell which it was given, so
# it draws clear water either way.
#
# So an absent layer comes back as a document that is NOT GeoJSON at all —
# `type: "AbsentLayer"` — carrying why it is absent, what its absence means in a
# full sentence, and the command that would fix it. A renderer that hands that to
# MapLibre gets an error, which is the correct outcome: nothing is drawn and
# nothing is claimed. THREE states, not two: `unreadable` is its own answer,
# because a half-written file is not an empty canal either.
#
# WHY THIS FILE READS DIRECTORIES AND NEVER HOSTNAMES. Everything served here was
# downloaded at bootstrap by nav/crt.py, which is the module that owns the network
# and is not on this path. Canal-side there is no DNS, and a lookup does not fail
# so much as hang — so the runtime answer to "is the hazard layer here" is a stat
# call, always, and it is instant whether the answer is yes or no.

_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,80}$")

# Endpoint URLs quoted back to the client so it does not have to build them, and
# to the operator so the fix is one copy-paste rather than a hunt through a README.
_FETCH_CMD = "python -m nav.cli crt-fetch {area}"
_SOUND_CMD = "python -m nav.cli soundings <dive.jsonl> --area {area}"


def _crt_mod():
    """nav/crt.py, imported lazily and only for its path arithmetic.

    Lazy on purpose. That module's own docstring says it "is never imported by the
    runtime path", and it is right to: it is the half of this subsystem that talks
    to the network. Nothing here calls anything in it but `safe_area_name`,
    `area_dir` and `provenance_path` — the three pure functions its `area_dir`
    docstring explicitly offers to the serving side — and none of them resolves a
    hostname. Returning None rather than raising keeps a card with no downloader
    on it serving everything else.
    """
    try:
        from . import crt
        return crt
    except ImportError:      # noqa: BLE001 — a build without the downloader still serves
        return None


def _soundings_mod():
    """nav/soundings.py, for its store path and its three explanatory constants.

    Also lazy, for a different reason: that module is where the sentences live
    that say what a sounding IS (a lower bound on bed depth, not a measurement of
    it) and what an absent cell means. Copying those strings into this file would
    put the same claim in two places, and the day they drift is the day the map
    and the store disagree about what the number under the sub means.
    """
    try:
        from . import soundings
        return soundings
    except ImportError:      # noqa: BLE001
        return None


# What went wrong the last time each optional module was reached for, keyed by module
# name, or None once one has imported. A module that raises on import is a different
# fact from a module that is not in the build, and the operator asking why the
# launch-bank overlay is missing is entitled to the difference: one is a checkout with a
# feature left out, the other is a library that did not install.
_BANK_IMPORT: dict[str, str | None] = {}


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001 — a half-installed package reports absent, not raises
        return False


def _import_optional(name: str):
    """One of nav/'s optional modules, or None, with the reason kept.

    LAZY FOR THE REASON crt.py IS, AND FOR A STRICTER ONE ON TOP. The reason it
    shares: these two own the download and the heavy raster work, and the serving path
    must not import a downloader. The reason that is their own: nav/lidar.py and
    nav/bank.py are the only modules under nav/ whose processing needs numpy, scipy and
    Pillow, and the vehicle is deliberately never given any of the three — a Pi 3B+ has
    neither the RAM nor the hours to build scipy, and it has no reason to, because the
    maps live on the handheld. An import at module scope would take the WHOLE API down
    on the machine that flies the sub, over libraries that machine is never meant to
    have. A missing optional library is exactly like a missing sensor: it is reported,
    in a sentence, by something that is still running.

    ASKED BEFORE IT IS IMPORTED, so the two absences can be told apart. Without the
    find_spec, a checkout with no bank.py in it and a bank.py that died on its own
    imports both come back as the same ImportError, and the sentence the operator reads
    would send them to install a library when the file is simply not in this build.

    EXCEPTION, NOT ImportError. _crt_mod catches the narrow one because crt.py imports
    nothing exotic; these must survive whatever a module doing float32 raster work can
    raise at import time, including a numpy that is present and broken. Anything at all
    here means "this layer cannot be asked", and the endpoint whose job is to REPORT
    that must not be the thing that dies of it.
    """
    if not _has_module(f"{__package__}.{name}"):
        # KEYED BY MODULE, because two of them are reached for on one request path and
        # one shared slot would answer about bank.py with the reason lidar.py gave.
        _BANK_IMPORT[name] = f"api/nav/{name}.py is not in this build"
        return None
    try:
        mod = importlib.import_module(f".{name}", __package__)
    except Exception as exc:  # noqa: BLE001 — a broken optional module still serves the rest
        _BANK_IMPORT[name] = f"api/nav/{name}.py could not be imported ({exc})"
        return None
    _BANK_IMPORT[name] = None
    return mod


def _bank_mod():
    """nav/bank.py — the classifier and the tile pyramid. See _import_optional."""
    return _import_optional("bank")


def _lidar_mod():
    """nav/lidar.py — the terrain acquisition half. See _import_optional."""
    return _import_optional("lidar")


# The three libraries the launch-bank layer's raster work needs, as (import name, pip
# name), and the command that installs them. NAMED HERE AS WELL AS IN nav/bank.py on
# purpose: the case this file most has to be able to describe is the one where bank.py
# could not be imported at all, and in that case there is nothing in that module left
# to ask. Keep it in step with the handheld section of api/requirements.txt.
_BANK_LIBS = (("numpy", "numpy"), ("scipy", "scipy"), ("PIL", "Pillow"))
_BANK_INSTALL = "pip install numpy scipy Pillow"
# Where they belong, said once so every sentence below can quote it. The Pi is not a
# chart server and never becomes one: this is the handheld's work, done once, at home.
_BANK_WHERE = ("the launch-bank layer is built on the HANDHELD, where the maps live and "
               "where this is one-time work — the vehicle is deliberately never given "
               "these libraries")


def _bank_libraries() -> dict:
    """Which of numpy / scipy / Pillow this interpreter has, and the sentence for it.

    nav/bank.py's own library_state() is asked FIRST, because the list of what it needs
    is its to change and its `why` is written to be shown verbatim. This file's own
    probe is the fallback for the case where that module could not be imported — which
    is exactly the case where the answer matters most and cannot come from there.

    find_spec in the fallback and not an import, for the reason bootstrap.py gives for
    the same probe: this is answered on request paths, and importing numpy to find out
    whether numpy is there costs a fifth of a second and a lot of address space on a
    machine that is flying a sub.
    """
    bank = _bank_mod()
    fn = getattr(bank, "library_state", None) if bank is not None else None
    if callable(fn):
        try:
            doc = fn()
            if isinstance(doc, dict) and isinstance(doc.get("missing"), (list, tuple)):
                missing = [str(m) for m in doc["missing"]]
                return {"ok": not missing, "missing": missing,
                        "install": doc.get("install") or _BANK_INSTALL,
                        "why": doc.get("why") or _bank_lib_why(missing, _BANK_INSTALL),
                        "libraries": doc.get("libraries") or {}}
        except Exception as exc:  # noqa: BLE001 — its answer failing is not this file's
            log.warning("nav/bank.py library_state() raised (%s) — probing directly", exc)
    missing = [pkg for mod, pkg in _BANK_LIBS if not _has_module(mod)]
    return {"ok": not missing, "missing": missing, "install": _BANK_INSTALL,
            "why": _bank_lib_why(missing, _BANK_INSTALL), "libraries": {}}


def _bank_lib_why(missing: list[str], install: str) -> str:
    if not missing:
        return ("numpy, scipy and Pillow are installed for this python, so this machine "
                "can build the launch-bank overlay.")
    names = ", ".join("Pillow" if m == "PIL" else m for m in missing)
    return (f"The launch-bank overlay cannot be built on this machine because {names} "
            f"{'is' if len(missing) == 1 else 'are'} not installed for the python "
            f"running this service. Install with: {install}  ({_BANK_WHERE}). Nothing "
            f"else is affected — the API, the map, the hazard layers and the vehicle "
            f"all run exactly as they did, and tiles already on this card are still "
            f"served.")


# The five answers the launch-bank overlay can give. nav/bank.py's card() says
# present / partial / absent, which are facts about a card; this file adds two.
#
# UNAVAILABLE is different in kind: it means this MACHINE cannot build the layer at all
# — no nav/bank.py in the build, or none of numpy/scipy/Pillow on this interpreter —
# which is not a fact about the water and not something a download would fix.
#
# WHY IT IS NOT FOLDED INTO "absent". On the vehicle it would be permanent: the Pi is
# never given numpy, so ABSENT there would be a red line that can never go green, on the
# one machine where the pre-dive gate is read. A gate that is always red is a gate that
# stops being read, and then the sluice layer's red goes past with it. On a bench with a
# fresh checkout it would be just as wrong in the other direction — it would send
# somebody looking for internet to download something no amount of internet can supply.
#
# UNREADABLE is the file-is-there-and-will-not-parse answer the rest of this module
# already draws for every other layer, kept here so a bank card that raises is not
# rounded up into "nothing has been built".
#
# AND IT IS ONLY UNAVAILABLE WHEN THERE IS NOTHING TO SERVE. Tiles already on the card
# are read out of an MBTiles archive with sqlite and nothing else, so a Pi handed a card
# built on the handheld serves this layer perfectly — which is the whole point of
# building it at home. The libraries gate the BUILD, never the map.
_BANK_STATES = ("present", "partial", "absent", "unreadable", "unavailable")


def _bank_block(area: str) -> dict:
    """What the launch-bank overlay is for this area — off the disk, in one shape.

    EVERY KEY THE MODULE SUPPLIED IS KEPT and only the ones this file must be able to
    rely on are imposed on top: `status` (this file's five-word vocabulary, from
    bank.py's three), plus a title and an aria-label that are never empty. nav/bank.py
    and nav/lidar.py landed in the same round as this wiring, exactly as
    nav/soundings.py did, and the lesson from that round is written into
    _surveyed_collection above: a rename over there must cost a duller sentence here,
    never a 500 out of the endpoint an operator checks before a dive.

    BOTH HALVES ARE REPORTED, because they fail differently and are fixed differently.
    `lidar` is the download — a hotspot that died leaves it partial and re-running helps.
    The bank card is the paint — it is computed here, from what the download left, and
    re-running the network changes nothing about it.
    """
    libs = _bank_libraries()
    bank = _bank_mod()
    lidar = _lidar_mod()
    held = None
    if lidar is not None:
        try:
            held = lidar.card(area)
        except Exception as exc:  # noqa: BLE001 — a card that will not answer is an answer
            log.warning("nav/lidar.py card(%s) raised: %s", area, exc)
    base = {"area": area, "layer": "bank", "url": "/api/bank",
            "libraries": libs, "lidar": held}
    if bank is None:
        why = _BANK_IMPORT.get("bank") or "api/nav/bank.py could not be loaded"
        return {
            **base, "status": "unavailable", "why": why,
            "means": ("nothing on this machine can build or read a launch-bank layer, so "
                      "nothing is known about which bank could be got down with the sub "
                      "and the cable. That is a missing capability, not a survey result"),
            "remedy": (libs["install"] if not libs["ok"] else
                       "install a build of this repo that includes api/nav/bank.py"),
            "title": (f"LAUNCH BANKS: UNAVAILABLE on this machine — {why}. Nothing here "
                      f"claims a bank is low and nothing claims it is high."),
            "aria_label": (f"The launch bank layer is unavailable for area {area} "
                           f"because the module that builds it could not be loaded."),
        }
    try:
        card = bank.card(area)
    except Exception as exc:  # noqa: BLE001 — an unreadable card is an answer, never a 500
        log.warning("nav/bank.py card(%s) raised: %s", area, exc)
        return {
            **base, "status": "unreadable", "why": f"{type(exc).__name__}: {exc}",
            "means": _UNREADABLE_MEANS, "remedy": _UNREADABLE_REMEDY,
            "title": (f"LAUNCH BANKS, {area}: the record beside this area's bank tiles "
                      f"could not be read ({exc}). Nothing is claimed about these banks "
                      f"either way."),
            "aria_label": f"The launch bank layer for area {area} could not be read.",
        }
    if not isinstance(card, dict):
        card = {}
    status = card.get("state")
    painted = bool(card.get("painted"))
    if not painted and not libs["ok"]:
        # THE ONLY PLACE THE LIBRARIES DECIDE ANYTHING. Nothing is painted and this
        # machine cannot paint it, so the honest word is not "absent" — nobody left
        # this undone, and no fetch will produce it here. Once tiles ARE on the card
        # the libraries stop mattering entirely and this branch is not taken.
        return {
            **base, **card, "status": "unavailable", "why": libs["why"],
            "means": ("the launch-bank layer is classified from a LIDAR ground model and "
                      "the arithmetic that does it needs these libraries. Without them "
                      "nothing is known about where the bank is low — which is not the "
                      "same as knowing there is no low bank here"),
            "remedy": libs["install"],
            "title": f"LAUNCH BANKS, {area}: UNAVAILABLE — {libs['why']}",
            "aria_label": (f"The launch bank layer is unavailable on this machine. "
                           f"{libs['why']}"),
        }
    if status not in _BANK_STATES:
        # NOT UNDERSTOOD IS NOT FINE. Reported as PARTIAL, which is the conservative end
        # of this vocabulary: it never claims the layer is whole, and unlike "unreadable"
        # it does not send anybody off to delete files over a word this file simply has
        # not been taught yet.
        log.warning("nav/bank.py reported state %r for %s, which is not one of %s",
                    status, area, list(_BANK_STATES))
        card = {**card, "why": (f"the bank module answered {status!r}, which this "
                                f"service does not know how to read")}
        status = "partial"
    n = card.get("pounds")
    tiles = ((card.get("tiles") or {}).get("tiles")
             if isinstance(card.get("tiles"), dict) else card.get("tiles"))
    default_titles = {
        "present": (f"LAUNCH BANKS, {area}: on this card"
                    + (f", {tiles} overlay tile(s)" if isinstance(tiles, int) else "")
                    + (f", {n} water level(s) detected" if isinstance(n, int) else "")
                    + ". Amber is bank measured under the launch height above the water "
                      "beside it, which is a geometric fact and not permission to launch; "
                      "unpainted ground has NOT been surveyed and found high."),
        "partial": (f"LAUNCH BANKS, {area}: PARTIAL. Part of this corridor has no "
                    f"terrain behind it and is drawn as nothing — which is NOT bank that "
                    f"was measured and found high."),
        "absent": (f"LAUNCH BANKS, {area}: ABSENT. No bank classification has been built "
                   f"for this area, so nothing here knows which side could be got down "
                   f"with the sub and the cable. Bare imagery is not a high bank."),
        "unreadable": (f"LAUNCH BANKS, {area}: the layer is on the card and cannot be "
                       f"read, so nothing is claimed about these banks."),
        "unavailable": f"LAUNCH BANKS: UNAVAILABLE on this machine — {_BANK_WHERE}.",
    }
    default_aria = {
        "present": f"The launch bank layer for area {area} is built and on this card.",
        "partial": f"The launch bank layer for area {area} is only partly built.",
        "absent": f"No launch bank layer has been built for area {area}.",
        "unreadable": f"The launch bank layer for area {area} cannot be read.",
        "unavailable": "The launch bank layer cannot be built on this machine.",
    }
    return {
        **base, **card,
        "status": status, "area": area, "layer": "bank",
        "title": card.get("title") or default_titles[status],
        "aria_label": card.get("aria_label") or default_aria[status],
    }


# Every painted area's card, cached against the FILES rather than against a clock —
# exactly like _layer_cache, _nominal_cache and _pyramid_cache above, and for a sharper
# reason than any of them. This list is read on the TILE path: a map view is forty
# overlay tiles, each of which has to find which area holds it, and bank.list_painted()
# is a directory scan plus a JSON parse per area. Uncached that is forty scans and a
# hundred and sixty parses to paint one screen, on a handheld that is also flying a sub.
#
# A SIGNATURE AND NOT A TIMER, so the answer can never be stale: the moment a build
# replaces a provenance file its mtime moves and the next question is answered off the
# disk. The stat is what is repeated per request; the parse is what is not.
#
# MEASURED ON THE ALLY, two painted areas with 4.4 kB records: 0.160 ms per call
# through the cache against 0.417 ms straight to list_painted(), so a forty-tile screen
# pays 6 ms instead of 17. The gap is per AREA and per byte of record, so it widens on
# the handheld that has been used all season, which is the one that matters.
_bank_cards_cache: dict[str, tuple] = {}


def _bank_cards_sig(bank) -> tuple:
    """(dir, mtime, size) for every area's bank record. Cheap, and it never parses."""
    suffix = getattr(settings, "lidar_dir_suffix", ".lidar")
    prov = getattr(bank, "render_provenance_path", None)
    out: list[tuple] = []
    try:
        entries = sorted(settings.areas_dir.iterdir())
    except OSError:
        return ()
    for p in entries:
        if not (p.is_dir() and p.name.endswith(suffix)):
            continue
        name = p.name[: -len(suffix)] if suffix else p.name
        paths = [p] + ([prov(name)] if callable(prov) else [])
        for q in paths:
            try:
                st = q.stat()
                out.append((str(q), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(q), 0, -1))
    return tuple(out)


def bank_cards() -> list[dict]:
    """Every area on this handheld that has launch-bank paint, with its state.

    The list the area-less /api/bank index and the tile lookup are both built on. No
    network, no numpy: a scan and a JSON read per area, and only when one of them has
    changed since the last question.
    """
    bank = _bank_mod()
    fn = getattr(bank, "list_painted", None) if bank is not None else None
    if not callable(fn):
        return []
    sig = _bank_cards_sig(bank)
    hit = _bank_cards_cache.get("cards")
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        cards = [c for c in (fn() or []) if isinstance(c, dict)]
    except Exception as exc:  # noqa: BLE001 — an unreadable card is an answer, never a 500
        log.warning("nav/bank.py list_painted() raised: %s", exc)
        return []
    _bank_cards_cache["cards"] = (sig, cards)
    return cards


# IS THERE ANYTHING MORE THIS CARD COULD BE GIVEN? That, and not the word "present", is
# what the pre-dive gate is actually asking of each source — see area_completeness.
#
# THE BANK LAYER IS THE ONE SOURCE WHERE THOSE TWO QUESTIONS COME APART. Its PARTIAL is
# not a download that stopped half way: nav/lidar.py says in as many words that where
# the survey has holes "nothing further will be downloaded — the gaps are in the
# source", and nav/bank.py calls a corridor partial at anything under 99.5% coverage,
# which is the ordinary outcome of a perfectly good build. Counted as missing, every
# successful build on most of the network would leave the gate red for ever, and a gate
# that is always red is one that gets waved through on the day it is red about a sluice.
# Counted as held, the word PARTIAL and its sentence still travel on the source, in the
# roll-up's detail, in the console's own layer row and out of the CLI — nothing is
# hidden, and the operator is told exactly which ground was never looked at.
#
# ABSENT still fails, because that is a card with no bank layer on it at all.
def _source_held(key: str, status: str) -> bool:
    return status == "present" or (key == "bank" and status == "partial")


def _unsurveyed_sentence(snd) -> str:
    """What an absent sounding means, taken from the module that owns the claim.

    getattr rather than a direct read: nav/soundings.py is landing in the same
    round as this file and its constant names are its own to change. A rename must
    cost a duller sentence, not a 500 from the endpoint an operator checks before
    a dive. The fallback says the same thing in fewer words and is deliberately
    short, so it is obvious in a diff which one is being shown.
    """
    return getattr(snd, "UNSURVEYED", None) or (
        "no dive has left bottom evidence here, so the bed is UNSURVEYED. Absent is "
        "not shallow and it is not zero.")


def _surveyed_collection(area: str, store: dict, snd) -> dict:
    """A sounding store → the FeatureCollection a map can draw.

    EVERY KEY IS READ OUT OF THE STORE RATHER THAN NAMED HERE. The store already
    carries the name of its own quantity (`quantity`), because the number is
    meaningless without it — that is nav/soundings.py's rule, and hardcoding
    `lower_bound_m` in this file would break the day that module renames it and,
    far worse, would go on serving numbers under a name nothing had checked. The
    rename has already happened once during this round.

    THE CLAIM TRAVELS WITH THE NUMBER, on every feature and not only on the
    collection: `bound: "lower"` says the bed is at LEAST this deep, `measured`
    says a hull was there, `is_survey` says what kind of layer this is. A cell
    that loses those on the way to a renderer is a depth reading, and it is not
    one — it is the deepest this vehicle got without grounding.
    """
    quantity = store.get("quantity") or "lower_bound_m"
    cell_m = store.get("cell_length_m")
    feats = []
    for cell in store.get("cells") or []:
        depth = cell.get(quantity)
        geom = cell.get("geom")
        if geom and len(geom) >= 2:
            geometry = {"type": "LineString", "coordinates": geom}
        elif cell.get("lon") is not None and cell.get("lat") is not None:
            # A renderer that meets a point draws a cell_m square around it — which
            # is why cell_m goes on the properties. It is the store's own bin width
            # and not a guess made here.
            geometry = {"type": "Point", "coordinates": [cell["lon"], cell["lat"]]}
        else:
            continue
        d = f"{depth:.2f}" if isinstance(depth, (int, float)) else "?"
        feats.append({
            "type": "Feature", "geometry": geometry,
            "properties": {
                "layer": "depth-surveyed",
                # `depth_m` is the name every depth renderer on this console looks
                # for first; the store's own name for the quantity is carried
                # beside it, unchanged, so nothing has to trust this translation.
                "depth_m": depth,
                quantity: depth,
                "quantity": quantity,
                "bound": cell.get("bound", "lower"),
                "measured": True, "nominal": False, "is_survey": True,
                "cell_m": cell_m,
                "line": cell.get("line"), "cell": cell.get("cell"),
                "from_m": cell.get("from_m"), "to_m": cell.get("to_m"),
                "samples": cell.get("samples"), "contacts": cell.get("contacts"),
                "dives": cell.get("dives"),
                "confidence_min": cell.get("confidence_min"),
                "confidence_mean": cell.get("confidence_mean"),
                "offset_m_max": cell.get("offset_m_max"),
                "deepest_from": cell.get("deepest_from"),
                "title": (f"MEASURED: the bed here is at least {d} m below the surface "
                          f"of the day. This is a LOWER BOUND, not a depth — it is the "
                          f"deepest this sub got while the journal showed it resting on "
                          f"something solid, and the pressure port sits above the keel, "
                          f"so there may be more water under it. There is no vertical "
                          f"datum: canal levels move with rain and lock use."),
                "aria_label": (f"Measured lower bound on bed depth, at least {d} metres, "
                               f"from {len(cell.get('dives') or [])} dive(s). The bed is "
                               f"at least this deep and may be deeper."),
            },
        })
    n = len(feats)
    return {
        "type": "FeatureCollection",
        "features": feats,
        "status": "present",
        "layer": "depth-surveyed",
        "area": area,
        "measured": True, "nominal": False, "is_survey": True,
        "quantity": quantity,
        # `cell_m` ON THE WIRE, both here and on every feature above. The store calls
        # its bin width `cell_length_m` and this collection used to publish that name
        # at the top level while the features underneath carried `cell_m` — one
        # document, one quantity, two names, and client/js/crt.js reads the
        # collection-level one to size a cell it cannot size from a point. It found
        # nothing there and fell back to a hardcoded 5 m, so a survey binned in tens
        # drew at half size and the sounded water looked like water somebody had not
        # coloured in. The store's own name is kept BESIDE it, assigned from the same
        # local on the next line so the two cannot drift, for the same reason
        # `quantity` rides beside `depth_m`: nothing downstream has to trust this
        # file's translation.
        "cell_m": cell_m,
        "cell_length_m": cell_m,
        "schema": store.get("schema"),
        "updated_at": store.get("updated_at"),
        "dives": sorted(store.get("dives") or {}),
        "centreline": store.get("centreline"),
        # The three sentences that say what this layer means, taken from the module
        # that owns them rather than restated here. `unsurveyed` is the important
        # one: it is what a renderer must draw for every cell NOT in this list.
        "means": getattr(snd, "MEANS", None),
        "unsurveyed": _unsurveyed_sentence(snd),
        "datum": getattr(snd, "DATUM", None),
        "title": (f"{n} surveyed cell(s) for {area}, from "
                  f"{len(store.get('dives') or {})} dive(s). Each is a LOWER BOUND on "
                  f"bed depth: the bed is at least this deep and may be deeper. "
                  f"Anywhere not drawn is UNSURVEYED, which is not shallow and not "
                  f"zero."),
        "aria_label": (f"Measured soundings for area {area}: {n} cells, each a lower "
                       f"bound on the depth of the bed. Anywhere not listed has never "
                       f"been surveyed by this vehicle."),
    }


def _absent(area: str, layer: str, why: str, means: str, remedy: str) -> dict:
    """The answer for a layer that is not on this card. Deliberately not GeoJSON."""
    return {
        "type": "AbsentLayer",          # NOT "FeatureCollection". See the note above.
        "status": "absent",
        "area": area,
        "layer": layer,
        "why": why,
        "means": means,
        "remedy": remedy,
        "title": f"{layer}: ABSENT for {area}. {why} {means}",
        "aria_label": (f"The {layer} layer is absent for area {area}. {why} {means} "
                       f"This is missing data, not an empty result."),
    }


# What "unreadable" MEANS, in one place. The per-layer document below and the index
# rows in _crt_layers both have to say it, and a sentence that exists twice is a
# sentence that will one day disagree with itself about whether a corrupt hazard
# layer is a missing one.
_UNREADABLE_MEANS = ("this layer's file is on the card and could not be parsed — almost "
                     "always a download killed part-way or a card that was pulled while "
                     "writing. It is NOT an empty layer and it is NOT a missing one: "
                     "something is there and nothing can be read out of it, so nothing "
                     "is claimed about this water")
_UNREADABLE_REMEDY = "delete the file and re-run the fetch while there is still internet"


def _unreadable(area: str, layer: str, path, exc: Exception) -> dict:
    """A file that is there and cannot be parsed. Its own answer, on purpose.

    A truncated download has exactly the shape of "nothing here" and this is the
    only place that can still tell the difference — the file's existence says a
    fetch ran, and its failure to parse says the fetch did not finish. Folded into
    "absent" it would send an operator to re-run a download that already ran;
    folded into "present" with zero features it would be the lie this whole file
    is built to refuse.
    """
    return {
        "type": "UnreadableLayer",
        "status": "unreadable",
        "area": area,
        "layer": layer,
        "file": str(path),
        "error": str(exc),
        "means": _UNREADABLE_MEANS,
        "remedy": _UNREADABLE_REMEDY,
        "title": (f"{layer}: UNREADABLE for {area}. The file exists and cannot be "
                  f"parsed, so nothing is claimed about this water."),
        "aria_label": (f"The {layer} layer for area {area} is present on disk and "
                       f"cannot be read. No claim is made about this water."),
    }


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _crt_index(area: str) -> dict | None:
    """The CRT fetch's own provenance index for an area, or None if it never ran."""
    crt = _crt_mod()
    if crt is None:
        return None
    name = crt.safe_area_name(area)
    if not name:
        return None
    p = crt.provenance_path(name)
    if not p.exists():
        return None
    try:
        return _read_json(p)
    except Exception as exc:  # noqa: BLE001 — a corrupt index is reported, not raised
        log.warning("CRT provenance for %s could not be read: %s", area, exc)
        return {"_unreadable": str(exc)}


# A skip that was a DECISION and a skip that was a FAILURE are different facts
# about the water, and nav/crt.py already separates them in its own records. The
# ones here are decisions: the layer was left off on purpose and the operator has
# lost nothing. Everything else — "fetch-failed" above all — means a hazard layer
# that SHOULD be on this card is not, and that is what the readiness check gates
# on. crt.py writes no file for a partial fetch precisely so it cannot read as
# empty; this is the other end of that decision.
_DELIBERATE_SKIPS = frozenset({"licence", "near-empty", "no-geometry"})


# WHAT IS ACTUALLY IN A CACHED LAYER FILE — remembered per file, so nobody pays for
# the answer twice.
#
# WHY THIS EXISTS. Everything below used to call a hazard layer "present" because
# stat() answered, and a stat cannot tell 400 kB of GeoJSON from 400 kB OF a
# GeoJSON. A download killed part-way leaves a file with a size and no closing
# brace, and both readers of this block then certified it: the pre-dive gate went
# green, and the index published status "present" with a feature count copied out of
# the fetch's own provenance — a number describing a file nobody had opened. The
# operator's console then drew clear water over a culvert mouth. A layer is PRESENT
# WHEN IT HAS BEEN READ, not when it has a size.
#
# WHAT IS CHEAP ENOUGH TO DO PER REQUEST, because parsing two dozen GeoJSON files
# (one of them a couple of megabytes) on every readiness poll would be a new problem
# on a Pi 3B+: the parse happens once per (mtime_ns, size) of each file, and what is
# kept is the VERDICT — state, error, feature count, byte count. Steady state is one
# stat() per layer per request, which is one syscall FEWER than the exists()+stat()
# pair it replaces. The parse is paid the first time a file is seen and again
# whenever it changes on disk, which is exactly when the answer could have changed.
# A signature rather than a timer, for the reason nominal_layer gives: a timer has to
# choose between re-reading files nobody has touched and certifying the card as it
# was ten minutes ago.
#
# THE PARSED DOCUMENT IS DELIBERATELY NOT KEPT. Holding two dozen decoded
# FeatureCollections would park the whole hazard fetch in this process's RSS for the
# life of the session, on a board with a gigabyte of it, to answer a yes/no question.
_layer_cache: dict[str, tuple[tuple, str, str | None, int | None, int | None]] = {}

# A JSON file is not a hazard layer. crt.py writes nothing but FeatureCollections, so
# anything else under a .geojson name got there by accident — a service error body
# saved as a layer is the common one, and it parses perfectly and contains no
# features. Answering "0 features" for it is the empty-canal lie arriving by the one
# route a JSON parse cannot catch.
_NOT_A_LAYER = ("the file is valid JSON and is not a GeoJSON FeatureCollection, so "
                "nothing can be read out of it")


def _is_layer_doc(doc) -> bool:
    return (isinstance(doc, dict) and doc.get("type") == "FeatureCollection"
            and isinstance(doc.get("features"), list))


def _read_layer(path: Path) -> tuple[str, str | None, int | None, int | None]:
    """(state, error, features, bytes) for one hazard layer file on the card.

    state is "present" | "absent" | "unreadable", and the third one is the entire
    point: a file that is there and will not parse is neither an empty canal nor a
    missing download, and the remedies are opposite — re-fetch versus delete-then-
    fetch. `features` is counted out of the FILE. The fetch's record of how many it
    wrote is a claim about a file that may no longer be the one on this card.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return "absent", None, None, None
    except OSError as exc:
        # NOT absent. Something is there and this process cannot look at it — a card
        # going bad, a permission, a directory half-written. Cannot-tell, and the
        # difference matters because "you never downloaded this" sends the operator
        # to the internet and a failing card does not care.
        return "unreadable", f"{type(exc).__name__}: {exc}", None, None
    sig = (st.st_mtime_ns, st.st_size)
    key = str(path)
    hit = _layer_cache.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2], hit[3], hit[4]
    state, err, n = "present", None, None
    try:
        doc = _read_json(path)
        if _is_layer_doc(doc):
            n = len(doc["features"])
        else:
            state, err = "unreadable", _NOT_A_LAYER
    except Exception as exc:  # noqa: BLE001 — a corrupt layer is an answer, never a 500
        state, err = "unreadable", f"{type(exc).__name__}: {exc}"
    if state == "unreadable":
        # Logged HERE and not at the call site: this runs once per version of the
        # file, so the operator's log gets one line per corrupt layer rather than one
        # per readiness poll — and a warning nobody can read is a warning nobody
        # reads.
        log.warning("CRT hazard layer %s is on the card and could not be read: %s — it is "
                    "NOT being served as an empty layer", path, err)
    _layer_cache[key] = (sig, state, err, n, st.st_size)
    return state, err, n, st.st_size


# ==========================================================================
# THE NATIONAL CARD, SERVED — one copy, every area, no area required
# ==========================================================================
#
# WHAT CHANGED AND WHY EVERY READER BELOW HAS TO KNOW IT. The Trust's vectors used to
# arrive clipped to an area, which meant the console could only ever show hazards for
# water somebody had already chosen. So a fresh console showed NOT DOWNLOADED as a
# matter of course — it was the everyday state rather than the last resort — and the
# operator was told to go and fetch something for a place they had not been to yet.
# Now the whole network is on the handheld, fetched once on launch, and an AREA is an
# optimisation for what to DRAW. Nothing here may require one to exist.
#
# THE ORDER OF PREFERENCE, and it is deliberate: the national card first, the area's
# clipped copy second. The national file is the whole layer and cannot be short; the
# clip is a convenience for a renderer that would rather draw 40 features than 7,691,
# and it is offered BESIDE each row (`clipped`) rather than instead of it.
_NATIONAL_CMD = "python -m nav.cli crt-fetch --national"

# WHAT A NATIONAL LAYER FILE IS, verified. Cached against (mtime, size) exactly like
# _layer_cache and for the same reason — this is on the readiness poll — with one
# difference stated in config.crt_parse_max_mb: over the ceiling the file is checked by
# its recorded size and its closing bracket rather than by a full parse, because
# re-decoding 100 MB of polygons on every poll is a fault of its own. Every answer
# carries `verified` saying which check it got.
_national_cache: dict[str, tuple[tuple, dict]] = {}

# And the CARD itself, which is 27 small provenance reads. One readiness poll asks for
# it three times over (the gate, the completeness roll-up inside it, and the index the
# console renders beside them), and the readiness endpoint is polled — that is eighty
# file reads a second on a board with an SD card for a disk.
#
# A SIGNATURE, NEVER A TIMER, for the reason _layer_cache gives: a timer has to choose
# between re-reading files nobody has touched and certifying the card as it was ten
# minutes ago. The index is rewritten atomically after EVERY layer of a fetch, and
# os.replace moves the directory's own mtime, so any change to this card moves one of
# the two numbers below before the next question is asked.
_national_card_cache: tuple[tuple, dict] | None = None


def _national_card_sig(crt) -> tuple:
    def stamp(p: Path):
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
    d = crt.national_dir()
    return (stamp(d), stamp(d / "provenance.json"))


def _national_card_cached(crt) -> dict:
    global _national_card_cache
    sig = _national_card_sig(crt)
    if _national_card_cache is not None and _national_card_cache[0] == sig:
        return _national_card_cache[1]
    card = crt.national_card()
    _national_card_cache = (sig, card)
    return card


def _verify_national(path: Path, rec: dict) -> dict:
    """{status, features, bytes, check, verified, error} for one national layer file.

    `check` is a bare token and `verified` is the sentence. Both, because the token
    goes in an HTTP header and a header is latin-1: the first version put the sentence
    there and every request for a layer answered HTTP 500 on the em-dash in it, which is
    a 140 MB card serving nothing at all over a punctuation mark.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"status": "absent", "features": None, "bytes": None,
                "check": "absent", "verified": "not on the card", "error": None}
    except OSError as exc:
        return {"status": "unreadable", "features": None, "bytes": None,
                "check": "unopenable", "verified": "could not be opened",
                "error": f"{type(exc).__name__}: {exc}"}
    sig = (st.st_mtime_ns, st.st_size, rec.get("bytes"), rec.get("features"))
    hit = _national_cache.get(str(path))
    if hit is not None and hit[0] == sig:
        return hit[1]
    claimed_bytes, claimed_n = rec.get("bytes"), rec.get("features")
    out: dict
    if claimed_bytes is not None and st.st_size != claimed_bytes:
        out = {"status": "unreadable", "features": None, "bytes": st.st_size,
               "check": "size", "verified": "size against the fetch's own record",
               "error": (f"the file is {st.st_size} bytes and the fetch recorded writing "
                         f"{claimed_bytes} — it is not the file that was downloaded")}
    elif st.st_size <= settings.crt_parse_max_mb * 1e6:
        state, err, n, size = _read_layer(path)
        out = {"status": state, "features": n, "bytes": size,
               "check": "parsed", "verified": "parsed in full", "error": err}
        if state == "present" and claimed_n is not None and n != claimed_n:
            out = {"status": "unreadable", "features": n, "bytes": size,
                   "check": "parsed", "verified": "parsed in full",
                   "error": (f"this file holds {n} feature(s) and the fetch recorded "
                             f"writing {claimed_n}")}
    else:
        # THE TAIL IS THE CHECK A TRUNCATED DOWNLOAD CANNOT PASS. crt.py closes every
        # collection with "]}" through an atomic rename, so a file that is the recorded
        # size AND ends in those two characters is the file that was written. It is a
        # weaker claim than a parse and it is reported as one.
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, st.st_size - 64))
                tail = fh.read().strip()
            ok = tail.endswith(b"]}")
        except OSError as exc:
            ok, tail = False, str(exc).encode()
        out = {"status": "present" if ok else "unreadable",
               "features": claimed_n, "bytes": st.st_size,
               "check": "size+terminator",
               "verified": (f"recorded size and closing bracket — {st.st_size / 1e6:.0f} "
                            f"MB is too large to re-parse on every check "
                            f"(NAV_CRT_PARSE_MAX_MB={settings.crt_parse_max_mb:.0f})"),
               "error": None if ok else ("the file does not end in a closing bracket, "
                                         "which is exactly what a download killed "
                                         "part-way loses")}
    if out["status"] == "unreadable":
        log.warning("national CRT layer %s could not be certified: %s — it is NOT being "
                    "served as an empty layer", path, out["error"])
    _national_cache[str(path)] = (sig, out)
    return out


def _national_layers(area: str | None = None) -> dict:
    """The national card, in the same shape _crt_layers returns for an area.

    One shape, because the console binds one table to it and a second document with
    different key names is the way the two halves of this feature have already been
    broken twice. `scope` is what tells them apart, never the shape.
    """
    crt = _crt_mod()
    if crt is None:
        return {"status": "absent", "scope": "national", "layers": [], "skipped": [],
                "warnings": [], "failed": [], "unreadable": [], "complete": False,
                "why": "api/nav/crt.py is not in this build",
                "means": ("nothing on this handheld can have downloaded the Trust's "
                          "layers, so no claim whatsoever is made about obstructions "
                          "anywhere"),
                "remedy": _NATIONAL_CMD}
    try:
        card = _national_card_cached(crt)
    except Exception as exc:  # noqa: BLE001 — an unreadable card is an answer, never a 500
        log.warning("the national CRT card could not be read: %s", exc)
        return {"status": "unreadable", "scope": "national", "layers": [], "skipped": [],
                "warnings": [], "failed": [], "unreadable": [], "complete": False,
                "why": f"the national card could not be read ({exc})",
                "means": "what is on this handheld cannot be accounted for",
                "remedy": _NATIONAL_CMD}
    if not card["layers"]:
        return {"status": "absent", "scope": "national", "layers": [], "skipped": [],
                "warnings": card.get("warnings") or [], "failed": [], "unreadable": [],
                "complete": False, "dir": card["dir"],
                "partial": card.get("partial") or [],
                "why": ("the Canal & River Trust's layers have never been downloaded on "
                        "this handheld"),
                "means": ("nothing has been downloaded about sluices, weirs, culverts, "
                          "stop-plank grooves, outfalls or safety gates ANYWHERE. That "
                          "is NOT a clear channel — it is no information at all, and "
                          "the two look identical on a map that draws an absent layer "
                          "as an empty one"),
                "remedy": _NATIONAL_CMD}
    d = Path(card["dir"])
    area_index = _crt_index(area) if area else None
    area_rows = {r.get("layer_key"): r for r in (area_index or {}).get("layers") or []}
    rows, missing, corrupt = [], [], []
    for rec in card["layers"]:
        key = rec.get("layer_key")
        seen = _verify_national(d / f"{key}.geojson", rec)
        row = {"layer": key, "title": rec.get("title"),
               "scope": "national",
               "features": seen["features"],
               "features_recorded": rec.get("features"),
               "national_features": rec.get("national_features"),
               "geometry_type": rec.get("geometry_type"),
               "attribution": rec.get("attribution"),
               "licence": rec.get("licence"), "licence_class": rec.get("licence_class"),
               "redistributable": rec.get("redistributable"),
               "count_check": rec.get("count_check"), "fetched": rec.get("fetched"),
               "bytes": seen["bytes"], "verified": seen["verified"],
               "check": seen["check"],
               "currency": rec.get("currency"),
               "url": f"/api/crt/{key}"}
        if seen["status"] == "present":
            row["status"] = "present"
        elif seen["status"] == "unreadable":
            row.update(status="unreadable", error=seen["error"],
                       why="the file is on this handheld and could not be certified",
                       means=_UNREADABLE_MEANS, remedy=_UNREADABLE_REMEDY)
            corrupt.append(key)
        else:
            row.update(status="absent",
                       why="the fetch recorded writing this layer and the file is gone",
                       means=("this layer's file has been removed since the fetch. "
                              "Nothing is known about its hazards, anywhere"),
                       remedy=_NATIONAL_CMD)
            missing.append(key)
        # THE AREA IS AN OPTIMISATION, OFFERED BESIDE THE DATA AND NEVER INSTEAD OF IT.
        # A clipped copy is a smaller thing for a renderer to draw over one pound of
        # canal; the national file above is the answer to "do we have this layer".
        clip = area_rows.get(key)
        if clip is not None and area:
            row["clipped"] = {"area": area, "features": clip.get("features"),
                              "url": f"/api/areas/{area}/crt/{key}",
                              "fetched": clip.get("fetched"),
                              "means": ("the same layer cut to this area — fewer "
                                        "features to draw, and not a different claim "
                                        "about the water")}
        rows.append(row)
    partial = card.get("partial") or []
    return {
        "status": "present",
        "scope": "national",
        "complete": bool(card.get("complete")) and not missing and not corrupt,
        "fetched": card.get("finished"),
        "state": card.get("state"),
        "bbox": None,
        "clip_rule": ("none — these are the whole national layers. An area clips a copy "
                      "for drawing and is never a precondition for having the data"),
        "attribution": card.get("attribution"),
        "dir": card["dir"],
        "layers": rows,
        "skipped": [{"layer": s.get("layer_key"), "title": s.get("title"),
                     "status": "absent", "skipped": s.get("skipped"),
                     "why": s.get("why"),
                     "deliberate": s.get("skipped") in _DELIBERATE_SKIPS,
                     "scope": "national",
                     "means": ("left out on purpose — nothing was lost"
                               if s.get("skipped") in _DELIBERATE_SKIPS else
                               "THIS LAYER IS MISSING AND SHOULD NOT BE. The fetch could "
                               "not complete it and wrote no file rather than a partial "
                               "one, because a truncated hazard layer reads exactly like "
                               "an empty canal"),
                     "remedy": _NATIONAL_CMD}
                    for s in card.get("skipped") or []],
        "failed": [s.get("layer_key") for s in card.get("skipped") or []
                   if s.get("skipped") not in _DELIBERATE_SKIPS] + missing,
        "unreadable": corrupt,
        "partial": partial,
        "expected_layers": card.get("expected_layers"),
        "warnings": card.get("warnings") or [],
        "features": sum(r.get("features") or 0 for r in rows),
        "bytes": card.get("bytes"),
    }


def _crt_layers(area: str) -> dict:
    """What Canal & River Trust data this console holds for `area` — and what it does not.

    THE NATIONAL CARD ANSWERS FIRST, and an area is not required to ask. Every caller
    below — the readiness gate, the completeness roll-up, the console's layer index —
    used to get "not downloaded" for any area whose own clip had not been fetched, even
    with the whole country sitting on the handheld. That made NOT DOWNLOADED the
    everyday state instead of the last resort, which is the wrong way round: it should
    be what you see when something has gone wrong, not what you see because nothing has
    gone right yet.

    The per-area clip is still read, and still preferred when there is no national card
    — a handheld that fetched gas-street last month and has not run the national fetch
    yet must not be told it has nothing. `scope` says which of the two answered.
    """
    national = _national_layers(area)
    if national["status"] == "present":
        return national
    area_block = _area_crt_layers(area)
    area_block.setdefault("scope", "area")
    for row in (area_block.get("layers") or []) + (area_block.get("skipped") or []):
        row.setdefault("scope", "area")
    if area_block["status"] == "present":
        # The national set is not here yet and this area's clip is. Said out loud on
        # the block, because "we have the hazards for this pound" and "we have the
        # hazards" are different claims and only one of them survives moving the van.
        area_block["national"] = {"status": national["status"],
                                  "why": national.get("why"),
                                  "means": national.get("means"),
                                  "remedy": _NATIONAL_CMD}
        return area_block
    # Neither. Prefer the NATIONAL sentence: it is the one an operator can act on
    # anywhere, and it does not send them to fetch data for a place they have not
    # chosen yet.
    return national


def _area_crt_layers(area: str) -> dict:
    """Everything the CRT fetch did for this area's own clipped copy."""
    crt = _crt_mod()
    remedy = _FETCH_CMD.format(area=area)
    if crt is None:
        return {"status": "absent", "layers": [], "skipped": [], "warnings": [],
                "why": "api/nav/crt.py is not in this build",
                "means": ("nothing on this card can have downloaded CRT hazard data, so "
                          "no claim whatsoever is made about obstructions here"),
                "remedy": remedy}
    index = _crt_index(area)
    if index is None:
        return {"status": "absent", "layers": [], "skipped": [], "warnings": [],
                "why": "no CRT fetch has ever run for this area",
                "means": ("nothing has been downloaded about sluices, weirs, culverts, "
                          "stop-plank grooves, outfalls or safety gates on this water. "
                          "That is NOT a clear channel — it is no information at all, "
                          "and the two look identical on a map that draws an absent "
                          "layer as an empty one"),
                "remedy": remedy}
    if "_unreadable" in index:
        return {"status": "unreadable", "layers": [], "skipped": [], "warnings": [],
                "why": f"the fetch's provenance index could not be parsed "
                       f"({index['_unreadable']})",
                "means": ("a fetch ran and its own record of what it did is corrupt, so "
                          "what is on this card cannot be accounted for"),
                "remedy": remedy}

    # Unreachable today — _crt_index already answered None for an unusable name —
    # but the fallback that used to sit here was `or area`, which would have handed
    # an unchecked operator string to a path join. A name that walks out of the data
    # directory is not a name, and the check is cheaper than the argument.
    name = crt.safe_area_name(area)
    if name is None:
        return {"status": "absent", "layers": [], "skipped": [], "warnings": [],
                "why": f"{area!r} is not a usable area name",
                "means": "nothing can be looked up for it",
                "remedy": remedy}
    d = crt.area_dir(name)
    rows, missing, corrupt = [], [], []
    for rec in index.get("layers") or []:
        key = rec.get("layer_key")
        f = d / (rec.get("file") or f"{key}.geojson")
        # READ, not stat()ed. See _read_layer: "the file has a size" is not a claim
        # anybody can dive on.
        state, err, n, size = _read_layer(f)
        row = {"layer": key, "title": rec.get("title"),
               # COUNTED OUT OF THE FILE just now — null while it cannot be. The
               # fetch's own number rides along under its own name instead of
               # standing in for this one, so the two can be compared rather than
               # confused: crt.py sets rec["features"] = len(feats) in the same
               # breath as it writes the file, so the day they differ, the file on
               # this card is not the file that was downloaded.
               "features": n,
               "features_recorded": rec.get("features"),
               "geometry_type": rec.get("geometry_type"),
               "attribution": rec.get("attribution"),
               "licence": rec.get("licence"), "licence_class": rec.get("licence_class"),
               "redistributable": rec.get("redistributable"),
               "count_check": rec.get("count_check"), "fetched": rec.get("fetched"),
               "url": f"/api/areas/{area}/crt/{key}"}
        if state == "present":
            row.update(status="present", bytes=size)
            claimed = rec.get("features")
            if claimed is not None and n != claimed:
                row["count_disagrees"] = True
                row["means"] = (f"this file holds {n} feature(s) and the fetch recorded "
                                f"writing {claimed}. It has been edited or replaced since "
                                f"it was downloaded, so what would be drawn here is not "
                                f"what the Trust served")
            elif not n:
                # A layer that fetched cleanly and matched nothing is a RESULT, and it
                # is the one case where an empty feature list is the honest answer.
                # Said out loud so a client showing "0" knows which zero it has.
                row["means"] = ("this layer downloaded cleanly and there is nothing of "
                                "its kind inside this area. An empty result, not a "
                                "missing one")
        elif state == "unreadable":
            # The third state, and the reason this loop parses at all. The file is on
            # the card, so the fetch ran and re-running it is not the fix; nothing can
            # be read out of it, so no claim is made about this water either way.
            row.update(status="unreadable", bytes=size, error=err,
                       why="the file is on this card and could not be parsed",
                       means=_UNREADABLE_MEANS, remedy=_UNREADABLE_REMEDY)
            corrupt.append(key)
        else:
            # The index says it was written and it is not there. Somebody deleted it,
            # or the card is failing. Either way it is not an empty layer.
            row.update(status="absent",
                       why="the fetch recorded writing this layer and the file is gone",
                       means=("this layer's file has been removed since the fetch. "
                              "Nothing is known about its hazards here"),
                       remedy=remedy)
            missing.append(key)
        rows.append(row)

    skipped = []
    for rec in index.get("skipped") or []:
        kind = rec.get("skipped")
        deliberate = kind in _DELIBERATE_SKIPS
        skipped.append({
            "layer": rec.get("layer_key"), "title": rec.get("title"),
            "status": "absent", "skipped": kind, "why": rec.get("why"),
            "deliberate": deliberate,
            "means": ("left out on purpose — nothing was lost" if deliberate else
                      "THIS LAYER IS MISSING AND SHOULD NOT BE. The fetch could not "
                      "complete it and wrote no file rather than a partial one, "
                      "because a truncated hazard layer reads exactly like an empty "
                      "canal. Nothing is known about this kind of hazard here"),
            "remedy": remedy,
        })
    failed = [s["layer"] for s in skipped if not s["deliberate"]]
    return {
        "status": "present",
        "fetched": index.get("finished"),
        "bbox": index.get("bbox"),
        "clip_rule": index.get("clip_rule"),
        "attribution": index.get("attribution"),
        "dir": str(d),
        "layers": rows,
        "skipped": skipped,
        "failed": failed + missing,
        # KEPT OUT OF `failed` ON PURPOSE. Both are go/no-go, and both are quoted to
        # the operator, but the sentences attached to them are opposite: `failed`
        # says "no file was written for this, go and download it", and that sentence
        # over a corrupt file sends somebody to re-run a fetch that already ran and
        # will now be refused by the very card that broke it. Absent is "you never
        # downloaded this"; unreadable is "what you downloaded is not usable".
        "unreadable": corrupt,
        "warnings": index.get("warnings") or [],
    }


# The computed NOMINAL layer, cached against the files it is computed FROM.
#
# nominal.load() reads every CRT layer in the area's directory to find the one
# worth hanging depth guidance on — two dozen files on a live fetch, one of them
# 2 MB — and this is a Pi. Caching it against a signature of those files rather
# than on a timer means the answer can never be stale: the moment a fetch writes a
# new layer the directory's mtime moves and the next request rebuilds. A timer
# would have to choose between rebuilding work nobody asked for and serving a
# depth layer that describes the card as it was.
_nominal_cache: dict[str, tuple[tuple, dict | None, str | None]] = {}


def _nominal_signature(area: str) -> tuple:
    def stamp(p: Path):
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
    crt = _crt_mod()
    name = (crt.safe_area_name(area) if crt else None) or area
    sig = [stamp(settings.areas_dir / f"{area}.geojson")]
    if crt is not None:
        d = crt.area_dir(name)
        sig.append(stamp(d))
        try:
            sig.append(tuple(sorted((p.name, stamp(p)) for p in d.glob("*.geojson"))))
        except OSError:
            sig.append(None)
    return tuple(sig)


def nominal_layer(area: str) -> tuple[dict | None, str | None]:
    """(layer, error). Both None-able: None/None is ABSENT, and it is a real answer."""
    sig = _nominal_signature(area)
    hit = _nominal_cache.get(area)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2]
    try:
        layer, err = nominalmod.load(area), None
    except ValueError as exc:            # the waterway source is there and corrupt
        layer, err = None, str(exc)
    _nominal_cache[area] = (sig, layer, err)
    return layer, err


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

# ==========================================================================
# THE BOOTSTRAP FETCH — one background job that fills an area from the network
# ==========================================================================
#
# WHAT WAS WRONG. Every piece of this already worked and NONE of it was automatic.
# satellite.py could download an imagery pyramid, crt.py could download two dozen
# hazard layers, satellite.fetch_centreline could pull the waterway — and data/areas/
# was empty, because nothing in the repo ever created an area. `grep -rn create_area`
# found nothing. The console therefore opened on "no chart data is downloaded", which
# was true, was nobody's bug, and could only be fixed by an operator who already knew
# to run `nav.cli crt-fetch <area>` — a command that REQUIRES an area name, for an
# area that did not exist. The chart data sitting in data/crt/gas-street/ belonged to
# no area at all.
#
# WHAT THIS IS. A sequencer. It does not download anything itself: it calls
# satellite.download_area, satellite.fetch_centreline and crt.download_hazards, in
# order, one at a time, and keeps a per-source record of what each one did. The three
# modules keep their own rate limits, their own retries, their own atomic writes and
# their own refusal to write a partial file. Nothing here duplicates any of that.
#
# THE TWO-PHASE MODEL IS UNTOUCHED. This is the bootstrap half, and it is reached
# only from an explicit endpoint or from the origin trigger below. The runtime path —
# every endpoint that SERVES a layer, the readiness check, the dead-reckoning loop —
# still stats and reads files and resolves no hostname, exactly as before. A card with
# no internet behind it loses nothing and waits for nothing: internet_available() is
# asked once per job, before any of it starts, and its NO is a normal answer.

# The three sources, in the order they are fetched, with what each one costs and what
# its absence costs.
#
# THE ORDER IS SAFETY-FIRST AND NOT ALPHABETICAL. A hotspot at the water's edge dies
# mid-job as a matter of routine, so what an interrupted fetch leaves behind is a
# design decision and not an accident. The centreline is ONE request and it is what
# the estimator snaps to; the hazard layers are a few hundred small requests and they
# are what keeps the sub out of a culvert mouth; the low-bank overlay is one big raster
# and it is how the sub gets back OUT; the imagery is a thousand requests and it is a
# picture to look at. Losing the picture is a disappointment. Losing any of the other
# three is the dive, or the recovery afterwards.
#
# WHY THE BANK LAYER SITS ABOVE THE IMAGERY AND BELOW THE HAZARDS. It is heavier than
# either in wall-clock terms — one terrain download and then a decode, a hillshade and a
# tile pyramid — so putting it first would mean a hotspot that dies in the usual four
# minutes costs the centreline and the sluices to buy a picture of the towpath. It is
# above the imagery because a bank you cannot climb out onto is a recovery problem and a
# blank background is not.
FETCH_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("centreline", "the waterway centreline (OpenStreetMap, via Overpass)",
     "the estimator has nothing to snap the track to and the map has no water drawn "
     "on it — which is missing data, not a stretch with no canal in it"),
    ("charts", "the Canal & River Trust hazard layers",
     "nothing is known about sluices, weirs, culverts, stop-plank grooves, outfalls "
     "or safety gates on this water, and an absent hazard layer draws exactly like an "
     "empty one"),
    ("bank", "the launch-bank overlay (an Environment Agency LIDAR ground model, "
             "downloaded and then classified on this handheld)",
     "nothing is known about which side of this cut could be got down with the sub and "
     "the cable — and imagery with no bank paint on it looks exactly like imagery of a "
     "bank that was measured and found to be a wall"),
    ("imagery", "the satellite basemap tiles (Esri World Imagery)",
     "the map has no picture under the track — the track and the hazards still draw, "
     "on a blank background"),
)

# A source is in exactly one of these. "skipped" is the successful idempotent case —
# it was already on the card and nothing was re-downloaded — and it is deliberately
# NOT called "done", so a console can show the difference between what this run
# fetched and what this run found.
#
# "unavailable" IS NOT A FAILURE AND MUST NEVER BE COUNTED AS ONE. It means this
# machine cannot do that source at all: no module for it in this build, or none of the
# libraries it needs on this interpreter. The Pi is what forces the word to exist — the
# vehicle is deliberately never given numpy, so a bank source reported as FAILED there
# would turn every otherwise-perfect fetch red, permanently, over a library nobody is
# ever going to install on it. Red that can never go green is red that stops being read.
# It is not "skipped" either: skipped means it was already on the card.
_SRC_STATES = ("pending", "running", "done", "skipped", "failed", "unavailable")

# Job states. "offline" is separated from "failed" on purpose: a canal-side console
# with no signal has not suffered a fault, it is in the condition this whole
# subsystem was designed around, and reporting that as an error would train the
# operator to ignore the one word that matters.
_JOB_LIVE = ("queued", "checking", "running")

# WHAT MAKES AN AREA, AND HOW BIG IT MAY BE, IS NOT DECIDED HERE. nav/areas.py owns
# it: plan_area/create_area turn a launch point into a box, area_for_point decides
# whether one already covers it, and the radius, the reuse margin and both caps are
# settings.area_* with the reasoning written beside them in config.py. This module
# sequences the DOWNLOAD, and a second opinion about how big an area is would be a
# second cap — the console would show one number and the refusal would quote another.
#
# The four-state area model (absent / downloading / present / failed) is areas.py's
# too, and every state change below goes through areas.set_area_state so there is one
# writer. That call is also a HEARTBEAT: list_areas() reports a "downloading" that has
# said nothing for settings.area_state_stale_s as FAILED, which is how a fetch killed
# by a flat battery stops reading as one still running.

# How long after a no-internet verdict the SAME area is left alone by the automatic
# path. The console re-POSTs its stored origin on every page load and again from the
# location watch, and at the canal every one of those would otherwise buy another
# four-second DNS timeout. A minute is long enough that a console sitting on a bank
# is not probing, and short enough that walking back into signal is noticed on the
# next fix rather than on the next dive.
#
# NOTHING ELSE IS CACHED. An earlier version remembered the last probe globally for
# two minutes, which made the answer depend on what had happened before — a fetch
# asked for explicitly could be refused because an automatic one had failed while the
# operator was still in the car park. A probe is four seconds on a daemon thread; an
# explicit request pays it every time.
_OFFLINE_RETRY_GAP_S = 60.0

# How often the launch-time national fetch re-asks whether there is internet yet.
#
# LONGER THAN THE AREA GAP ABOVE, AND FOR A DIFFERENT REASON. That one is defensive: it
# stops a console re-probing on every origin POST the console makes, which is several a
# minute. This one is nobody's hot path — it is one background task, one TCP connect,
# and the thing it is waiting for is an operator driving from a car park to somewhere
# with a signal. Ten minutes is short enough that walking into coverage is noticed
# within a session and long enough that a handheld left on overnight with no bars costs
# 144 connects and nothing else.
_NATIONAL_RETRY_GAP_S = 600.0

# How long the imagery download may go without a single tile arriving before the
# link is declared gone. See AreaFetch._imagery for the measurement that set it.
_IMAGERY_STALL_S = 15.0

# WHAT AN IMAGERY DOWNLOAD DESTROYS, AND THIS JOB PUTS BACK. satellite.download_area
# writes areas/<name>.json from scratch when it finishes — bbox, zooms, tiles_ok,
# attribution and nothing else — so every field nav/areas.py's create_area put there
# is gone the moment the last tile lands. nav/areas.py's set_area_state docstring
# says so in as many words and tells the fetch driver to re-apply them; this is the
# list. `origin` is the load-bearing one: it is where the operator actually stood,
# it is what this file prints as the launch point and what a later geocode is asked
# about, and losing it makes an area that can never be told where it came from.
_PRESERVE_ACROSS_IMAGERY = ("label", "origin", "created_by", "est_tiles", "est_mb",
                            "extended_at", "extended_from")


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fetch_cap(bbox: list[float], zmin: int, zmax: int) -> dict:
    """What downloading this box would cost, and the ceilings it is measured against.

    ONE EXPRESSION, THREE CALLERS — the job carries it on every progress snapshot,
    the endpoint refuses on it, and the CLI prints it in its preflight. The two
    ceilings are settings.sat_tile_cap and settings.area_size_cap_mb, which are the
    same pair nav/areas.py's planner refuses on: a cap invented here would be a
    third number, and the console would show one while the refusal quoted another.
    The estimate itself is satellite.py's, from the tile walk that will actually run.
    """
    est = satmod.estimate(bbox, zmin, zmax)
    cap_tiles, cap_mb = int(settings.sat_tile_cap), float(settings.area_size_cap_mb)
    within = est["tiles"] <= cap_tiles and est["mb"] <= cap_mb
    return {
        "tiles": est["tiles"], "mb": est["mb"], "zmin": zmin, "zmax": zmax,
        "tile_cap": cap_tiles, "mb_cap": cap_mb, "within": within,
        "title": (f"This area is {est['tiles']} satellite tiles, about {est['mb']} MB at "
                  f"zoom {zmin}-{zmax}. The ceiling for one area is {cap_tiles} tiles / "
                  f"{cap_mb:.0f} MB, and this is "
                  + ("inside it." if within else "OVER it, so nothing will be downloaded "
                     "until the area is made smaller or the detail is lowered.")),
        "aria_label": (f"Estimated download: {est['tiles']} tiles, about {est['mb']} "
                       f"megabytes. The limit is {cap_tiles} tiles or {cap_mb:.0f} "
                       f"megabytes. This area is "
                       + ("within the limit." if within else "over the limit.")),
    }


def _area_meta(name: str) -> dict | None:
    """One area's metadata as areas.py reports it — including its derived state.

    Read through list_areas() rather than off the file, so this sees the same
    document the console does: `state` there is disk truth first (an area whose
    archive was deleted is absent whatever the record says) and the record only for
    the two things a filesystem cannot show.
    """
    if not name:
        return None
    try:
        return next((a for a in areamod.list_areas() if a.get("name") == name), None)
    except Exception as exc:  # noqa: BLE001 — an unreadable card is an answer
        log.warning("could not read area %s: %s", name, exc)
        return None


def _record_fetch(name: str, snap: dict) -> None:
    """Put a job's progress on the card, as the area's own state plus the detail.

    THE AREA STATE AND THE PER-SOURCE RECORD ARE WRITTEN TOGETHER, in one call, on
    purpose. They are two views of one fact and the failure mode of keeping them
    apart is precise: an area left saying "downloading" with a finished job beside
    it, or the reverse. set_area_state stamps state_at as it goes, which is the
    heartbeat list_areas() uses to call a dead download dead.

    `offline` maps to ABSENT and not to failed, because nothing was attempted —
    there is a plan on this card and no data behind it yet, which is a different
    thing from a download that died, and it sends the operator somewhere different.
    """
    state = {"done": "present", "offline": "absent",
             "failed": "failed", "cancelled": "failed"}.get(snap.get("state"), "downloading")
    try:
        areamod.set_area_state(name, state, why=snap.get("title"), fetch=snap)
    except Exception as exc:  # noqa: BLE001 — a fetch must not die of its own bookkeeping
        log.warning("could not record fetch state for %s: %s", name, exc)


# ---- is there actually any internet -----------------------------------------
async def internet_available() -> tuple[bool, str]:
    """(ok, why) — asked ONCE per job, never per request, and never on the hot path.

    THE PROBE IS NOT A THIRD NOTION OF "ONLINE". It is nav/cli.py's `_reachable`,
    which is the only place in this repo that does anything about the isolated
    segment having no resolver at all: a getaddrinfo with nobody to ask does not
    fail, it sits, and a socket timeout bounds the connect and not the lookup. That
    function runs the lookup on a daemon thread and enforces its own deadline. The
    console's half of the same question is the launcher's /__net (wifi.internet),
    which is about the HANDHELD's radios and is the right thing for the client to
    gate its buttons on; this is about the machine that will do the downloading.

    Imported lazily and run in a thread. Lazily because nav/cli.py is the terminal
    driver and pulls in the simulator and the calibrator, which the API process has
    no reason to carry; in a thread because _reachable blocks on a join and this is
    the loop that flies the sub.
    """
    try:
        from .cli import _reachable
    except Exception as exc:  # noqa: BLE001 — a build without the CLI still serves
        return False, (f"nav/cli.py is not importable ({exc}), so nothing here can tell "
                       f"whether there is internet — and a fetch that guessed would hang")
    return await asyncio.to_thread(_reachable, settings.crt_hub_search_url)


# ---- what is actually on the card, per source --------------------------------
#
# Cached against a signature of the files themselves, exactly like _layer_cache and
# _nominal_cache above and for the same reason: the readiness check is polled, and
# counting rows in a tile archive on every poll is work a Pi 3B+ does not have
# spare. A signature rather than a timer, so the answer can never be stale — the
# moment a download writes a tile the archive's mtime moves and the next question
# is answered from the disk.
_pyramid_cache: dict[str, tuple[tuple, int, int, str | None]] = {}


def _tiles_present(name: str, bbox, zmin: int, zmax: int) -> tuple[int, int, str | None]:
    """(have, want, error) for one area's imagery pyramid.

    Counted out of the archive, not out of the metadata. satellite.download_area
    records tiles_ok when it finishes, and that number describes the run rather than
    the card: a download killed at tile 700 of 900 writes no metadata at all, and one
    whose archive was later truncated still has its old number sitting there. The
    question before a dive is what is ON THIS CARD.
    """
    want = satmod.count_tiles(bbox, zmin, zmax)
    path = settings.areas_dir / f"{name}.mbtiles"
    try:
        st = path.stat()
    except FileNotFoundError:
        return 0, want, None
    except OSError as exc:
        return 0, want, f"{type(exc).__name__}: {exc}"
    sig = (st.st_mtime_ns, st.st_size, tuple(bbox), zmin, zmax)
    key = str(path)
    hit = _pyramid_cache.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2], hit[3]
    have, err = 0, None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            minlon, minlat, maxlon, maxlat = bbox
            for z in range(zmin, zmax + 1):
                xa, ya = satmod.deg2num(minlat, minlon, z)
                xb, yb = satmod.deg2num(maxlat, maxlon, z)
                x0, x1 = min(xa, xb), max(xa, xb)
                y0, y1 = min(ya, yb), max(ya, yb)
                # MBTiles rows are TMS-flipped — y counted from the bottom. The flip
                # is satellite.read_tile's, and reproducing it here rather than
                # importing it is deliberate: read_tile answers about one tile and
                # this needs a range, and the two must agree about which row is which
                # or a full archive reads as an empty one.
                r0, r1 = (1 << z) - 1 - y1, (1 << z) - 1 - y0
                have += con.execute(
                    "SELECT COUNT(*) FROM tiles WHERE zoom_level=? AND tile_column "
                    "BETWEEN ? AND ? AND tile_row BETWEEN ? AND ?",
                    (z, x0, x1, r0, r1)).fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — a corrupt archive is an answer, never a 500
        have, err = 0, f"{type(exc).__name__}: {exc}"
    _pyramid_cache[key] = (sig, have, want, err)
    return have, want, err


def _src(status: str, title: str, aria: str, **extra) -> dict:
    return {"status": status, "title": title, "aria_label": aria, **extra}


def area_completeness(name: str) -> dict:
    """Is this area's offline data ALL here — per source, and in one word.

    The three questions an operator has at the water's edge, answered off the disk
    with no network involved: is there a picture, are the hazards on the card, is
    there a channel to snap to. Everything below distinguishes "not downloaded"
    from "downloaded and unreadable", because they send somebody to two different
    places and only one of them is fixable at the bank.
    """
    if not name:
        return {"area": None, "complete": False, "sources": {}, "fetch": None,
                "detail": "no area is activated, so there is nothing to be complete",
                "title": ("No offline area is active. Nothing here claims this water is "
                          "charted or that it is not — there is simply no area to ask "
                          "about."),
                "aria_label": ("No offline area is active, so its completeness cannot be "
                               "reported.")}
    meta = _area_meta(name) or {}
    bbox = meta.get("bbox")
    zmin = int(meta.get("minzoom", settings.sat_min_zoom) or settings.sat_min_zoom)
    zmax = int(meta.get("maxzoom", settings.sat_max_zoom) or settings.sat_max_zoom)
    sources: dict[str, dict] = {}

    # --- imagery -------------------------------------------------------------
    if not bbox or len(bbox) != 4:
        sources["imagery"] = _src(
            "absent",
            f"No area metadata for {name}, so there is no box to have downloaded "
            f"imagery for and none can be checked.",
            f"Satellite imagery for area {name} cannot be checked: the area has no "
            f"stored bounding box.",
            why="areas/%s.json carries no bbox" % name)
    else:
        have, want, err = _tiles_present(name, bbox, zmin, zmax)
        if err is not None:
            sources["imagery"] = _src(
                "unreadable",
                f"The tile archive for {name} is on the card and cannot be read ({err}). "
                f"That is not an empty map and it is not a missing download — delete "
                f"areas/{name}.mbtiles and fetch again while there is internet.",
                f"The satellite tile archive for area {name} exists and cannot be read.",
                have=0, want=want, why=err)
        elif have >= want:
            sources["imagery"] = _src(
                "present",
                f"All {want} satellite tiles for {name} are on this card, zoom "
                f"{zmin} to {zmax}. Nothing about the imagery needs the internet again.",
                f"All {want} satellite tiles for area {name} are downloaded.",
                have=have, want=want)
        else:
            sources["imagery"] = _src(
                "partial" if have else "absent",
                f"{have} of {want} satellite tiles are on this card for {name}. The map "
                f"will draw the ones it has and blank the rest — which is missing "
                f"imagery, not water with nothing in it.",
                f"{have} of {want} satellite tiles are downloaded for area {name}.",
                have=have, want=want)

    # --- charts (CRT) --------------------------------------------------------
    # NO LONGER A PER-AREA QUESTION. The Trust's vectors are national and held once, so
    # what this asks is "does this handheld hold them", and the answer is the same for
    # every area on the card. `scope` says which card answered — an older handheld with
    # a clipped copy and no national fetch yet is still reported as having its charts.
    crt_block = _crt_layers(name)
    scope = crt_block.get("scope", "area")
    where = ("this handheld, nationally" if scope == "national"
             else f"this card, clipped to {name}")
    failed = list(crt_block.get("failed") or [])
    corrupt = list(crt_block.get("unreadable") or [])
    part = [p.get("layer_key") for p in crt_block.get("partial") or []]
    n_layers = len(crt_block.get("layers") or [])
    if crt_block["status"] != "present":
        sources["charts"] = _src(
            "absent",
            f"No Canal & River Trust layer has been downloaded onto this handheld. "
            f"{crt_block.get('means', '')}",
            "No hazard charts are downloaded. This is missing data, not a clear "
            "channel.",
            why=crt_block.get("why"), scope=scope, layers=0, failed=[], unreadable=[],
            remedy=crt_block.get("remedy"))
    elif failed or corrupt or part:
        sources["charts"] = _src(
            "partial",
            f"{n_layers} Trust layer(s) are on {where}; {len(failed)} did not download, "
            f"{len(corrupt)} cannot be read and {len(part)} are still coming. Nothing is "
            f"known about the hazards those would have shown.",
            f"The hazard charts are incomplete: {len(failed)} missing, {len(corrupt)} "
            f"unreadable, {len(part)} still downloading.",
            scope=scope, layers=n_layers, failed=failed, unreadable=corrupt,
            partial=part)
    else:
        sources["charts"] = _src(
            "present",
            f"{n_layers} Trust layer(s) are on {where}, {crt_block.get('features')} "
            f"feature(s), and every one of them was certified rather than merely found. "
            f"Fetched {crt_block.get('fetched')}.",
            f"All {n_layers} hazard chart layers are downloaded and readable.",
            scope=scope, layers=n_layers, failed=[], unreadable=[],
            features=crt_block.get("features"), fetched=crt_block.get("fetched"))

    # --- the launch banks ----------------------------------------------------
    # Straight through from the modules that own the claim, with this file adding
    # nothing to it: _bank_block already answers in this vocabulary and already
    # carries its own two sentences. The one thing to notice is that it can answer
    # UNAVAILABLE, which none of the other three can, and that word is handled where
    # `missing` is worked out below rather than translated away here.
    #
    # A CHOSEN FEW KEYS AND NOT THE WHOLE RECORD. _bank_block hands back everything
    # nav/bank.py wrote — the classification constants, the hillshade parameters, the
    # per-zoom tile table, the corridor stats — which is right for the layer's own
    # endpoint and wrong here: THIS document is polled, by the console and by the
    # readiness check, and 4 kB of provenance per poll is bytes and parse time spent on
    # a Pi 3B+ to tell somebody something they did not ask this endpoint for. What a
    # roll-up needs is the word, the sentence, and where to go next.
    bank = _bank_block(name)
    libs = bank.get("libraries") or {}
    sources["bank"] = _src(
        bank["status"], bank["title"], bank["aria_label"],
        why=bank.get("why"), means=bank.get("means"), remedy=bank.get("remedy"),
        url=bank.get("url"), layer="bank",
        tiles=(bank["tiles"].get("tiles") if isinstance(bank.get("tiles"), dict)
               else bank.get("tiles")),
        pounds=bank.get("pounds"),
        vintage=(bank.get("source") or {}).get("survey_vintage"),
        # The download half's own verdict, beside the paint's. They fail differently
        # and are fixed differently — one by a connection, one by nothing at all — and
        # a roll-up that carried only the second would send somebody to re-run a build
        # over terrain that never arrived.
        terrain=(bank.get("lidar") or {}).get("state"),
        terrain_why=(bank.get("lidar") or {}).get("why"),
        libraries={"ok": libs.get("ok"), "missing": libs.get("missing"),
                   "install": libs.get("install")})

    # --- centreline ----------------------------------------------------------
    cl = settings.areas_dir / f"{name}.geojson"
    state, err, feats, _bytes = _read_layer(cl)
    if state == "present":
        sources["centreline"] = _src(
            "present",
            f"The waterway centreline for {name} is on this card: {feats} way(s). The "
            f"estimator can snap to it and the map has water drawn on it.",
            f"The waterway centreline for area {name} is downloaded, with {feats} ways.",
            features=feats)
    elif state == "unreadable":
        sources["centreline"] = _src(
            "unreadable",
            f"The centreline file for {name} is on the card and will not parse ({err}). "
            f"Delete areas/{name}.geojson and fetch again while there is internet.",
            f"The centreline file for area {name} exists and cannot be read.", why=err)
    else:
        sources["centreline"] = _src(
            "absent",
            f"No waterway centreline has been downloaded for {name}. Snapping has "
            f"nothing to snap to and the published depth guidance has nothing to hang "
            f"on — that is missing data, not a stretch with no canal in it.",
            f"No waterway centreline is downloaded for area {name}.")

    # THE AREA'S OWN WORD FOR WHAT IT IS comes from nav/areas.py and is not
    # recomputed here. Its _derive_state() reads disk first and the record only for
    # what a filesystem cannot show, and it is the thing that turns a "downloading"
    # left behind by a dead process into FAILED once it has gone quiet. A second
    # opinion in this file would eventually disagree with the console's.
    state = meta.get("state") or ("absent" if meta else "no-such-area")
    rec = meta.get("fetch") if isinstance(meta.get("fetch"), dict) else None
    live = state == "downloading"
    # UNAVAILABLE IS REPORTED IN FULL AND IS NOT COUNTED AS MISSING, and the line
    # between the two is worth stating precisely because it is the one place in this
    # document where something is deliberately not held against the card.
    #
    # MISSING means: this card could hold it and does not. Somebody with a connection
    # can fix that, and until they do the map is short of something it is meant to
    # have — so it fails `complete`, and the pre-dive gate refuses.
    #
    # UNAVAILABLE means: this MACHINE cannot hold it at all. On the vehicle that is
    # permanent by design (no numpy on a Pi 3B+, ever) and no download would change
    # it. Counted as missing it would paint the gate red on every dive this system
    # ever flies, for a reason nobody can act on — and a gate that is always red is
    # one nobody reads, including on the day it is red about a sluice. So it travels
    # in its own list, in `detail`, in `title` and in `aria_label`: named every time,
    # never dressed up as a pass, and never counted as a fault.
    unavailable = [k for k, s in sources.items() if s["status"] == "unavailable"]
    missing = [k for k, s in sources.items()
               if not _source_held(k, s["status"]) and s["status"] != "unavailable"]
    # HELD BUT NOT WHOLE, named in its own right. _source_held lets the bank layer's
    # PARTIAL satisfy the gate — the reasoning is written beside it — and this is what
    # stops that from being a silence: the word and the module's own sentence travel in
    # the detail below, so a corridor the survey never flew is said out loud on the same
    # line that says the area is ready.
    incomplete = [k for k, s in sources.items()
                  if _source_held(k, s["status"]) and s["status"] != "present"]
    # A DOWNLOAD IN FLIGHT IS NOT COMPLETE EVEN IF EVERY SOURCE HAPPENS TO READ
    # PRESENT AT THIS INSTANT. The imagery archive gains rows as it goes and the
    # counts are only a snapshot; "still coming" is its own answer and the pre-dive
    # gate must refuse it rather than round it up to yes.
    complete = not missing and not live
    # The unavailable ones, in a clause that goes on the end of every sentence below —
    # including the one that says everything is here, because "everything is on this
    # card" over a machine that cannot build the bank layer is exactly the
    # looks-complete-and-is-not claim this document exists to refuse.
    cannot = ""
    aria_cannot = ""
    if unavailable:
        why1 = (sources[unavailable[0]].get("why")
                or "this machine cannot build it")
        cannot = (f" {', '.join(unavailable)} cannot be built on this machine at all "
                  f"({why1}) — that is not a download anybody is waiting for, and "
                  f"nothing here claims anything about it either way.")
        aria_cannot = (f" {', '.join(unavailable)} cannot be built on this machine, so "
                       f"nothing is known about it.")
    for k in incomplete:
        cannot += (f" {k} is HELD BUT NOT WHOLE: "
                   f"{sources[k].get('why') or sources[k].get('title')}")
        aria_cannot += f" {k} is held but incomplete."
    if live:
        detail = (f"area={name}: state=downloading — "
                  f"{', '.join(missing) if missing else 'finishing up'}. Not yet.{cannot}")
    elif missing:
        detail = (f"area={name}: {', '.join(missing)} not on this card (area state="
                  f"{state}"
                  + (f", {meta.get('state_why')}" if meta.get("state_why") else "") + ")"
                  + cannot)
    else:
        detail = (f"area={name}: "
                  + ", ".join(k for k in sources
                              if k not in unavailable and k not in incomplete)
                  + f" all present and readable (area state={state}).{cannot}")
    return {
        "area": name, "complete": complete, "missing": missing,
        # Its own key, beside `missing` and never inside it — the same separation
        # _crt_layers keeps between `failed` and `unreadable`, and for the same
        # reason: the two send somebody to two different places, and one of them is
        # nowhere at all.
        "unavailable": unavailable, "incomplete": incomplete,
        "state": state, "downloading": live, "sources": sources, "fetch": rec,
        "bbox": bbox, "zmin": zmin, "zmax": zmax, "detail": detail,
        "title": (f"Offline data for {name}: "
                  + ("everything this machine can hold is on this card." if complete else
                     f"{', '.join(missing) or 'a fetch'} still "
                     + ("downloading." if live else "missing."))
                  + cannot),
        "aria_label": (f"Area {name} is "
                       + ("complete: every source this machine can hold is downloaded "
                          "and readable."
                          if complete else
                          f"incomplete. Missing or unfinished: "
                          f"{', '.join(missing) or 'a download is in progress'}.")
                       + aria_cannot),
    }


async def _reverse_label(lat: float, lon: float) -> str | None:
    """A human title for a new area, best-effort. Only ever called WITH internet.

    A LABEL, NEVER A RENAME. nav/areas.py names an area the moment it is created,
    which is before this can be asked — the geocode needs a network and creating an
    area does not, and an area that could not be created without one would be
    useless at the canal. Its docstring says the name is permanent for good reason:
    the .mbtiles, the hazard directory and every dive journal hang off it. So what
    arrives late goes in `label`, which is what a console shows beside the name.
    """
    try:
        gc = await satmod.reverse_geocode(lat, lon)
    except Exception as exc:  # noqa: BLE001 — a nameless area still works
        log.info("reverse geocode failed (%s) — the area keeps its date-based name", exc)
        return None
    return gc or None


def _offline_record(area: str, why: str, reason: str = "") -> dict:
    """The record of a fetch that never started because there is no internet.

    A REAL ANSWER AND NOT AN ERROR. This is what the canal looks like: the whole
    subsystem exists because there is no signal at the water, so "did not start" is
    the expected outcome of every trigger that fires at the bank, and it has to read
    that way or the operator learns to dismiss it.
    """
    # A SOURCE THIS MACHINE CANNOT DO IS NOT "WAITING FOR INTERNET". Told that, an
    # operator drives home, finds a connection, re-runs the fetch and gets the same
    # empty layer with the same reassuring sentence beside it. See
    # AreaFetch._unavailable_sources — this is the same rule on the path where no job
    # was ever built to hold it.
    bank = _bank_block(area)
    sources = {}
    for k, label, _means in FETCH_SOURCES:
        if k == "bank" and bank.get("status") == "unavailable":
            sources[k] = _src("unavailable", bank["title"], bank["aria_label"],
                              why=bank.get("why"), remedy=bank.get("remedy"))
        else:
            sources[k] = _src("pending", f"Not started: {label} needs internet.",
                              f"{label} was not downloaded because there is no internet.",
                              why=why)
    return {
        "scope": "area",
        "area": area, "state": "offline", "reason": reason,
        "started": _iso(), "finished": _iso(), "pid": os.getpid(),
        "net": {"ok": False, "why": why},
        "sources": sources,
        "title": (f"Nothing was downloaded for {area}: there is no internet here. "
                  f"{why}. This is the normal state at the water's edge and not a "
                  f"fault — but anything missing from this card stays missing until "
                  f"you are back on a connection."),
        "aria_label": (f"No download was started for area {area} because there is no "
                       f"internet connection. {why}"),
    }


# ---- serving a WINDOW of a national layer ---------------------------------------
#
# WHY A WHOLE LAYER IS SOMETIMES THE WRONG ANSWER. The national planning-buffer layer
# is 100 MB of consultation-zone polygons. The console cannot parse that — JSON.parse
# stalls the browser's only thread for seconds and leaves hundreds of megabytes of heap
# behind, on the machine an operator is steering with — so client/js/crt.js asks for the
# part around where the map is looking (?bbox=W,S,E,N) and, for anything handed back
# whole and over its ceiling, reports the layer as HELD BUT NOT DRAWN. Held and not
# drawn is the failure this whole round is against, arriving by the back door: the data
# is on the handheld, correct and complete, and never once appears on the glass.
#
# So the window is served HERE, where the file is, using the byte-offset index crt.py
# wrote beside it. No parse of the layer at any size: a small JSON load, a seek and a
# copy. What comes back says what it is — `windowed`, the box it is a window ON, and the
# national total it is a window OF — because "12 features" out of 6,916 is a window and
# would otherwise read as a store that has lost nearly all of it.
#
# THIS IS PAGING, NOT PRUNING. Nothing is withheld: the index rows still report the
# whole layer, every layer stays switched on, and what is outside the window is outside
# the screen as well.


def _window_box(raw: str) -> list[float] | None:
    try:
        w, s, e, n = (float(v) for v in raw.replace(" ", "").split(","))
    except (TypeError, ValueError):
        return None
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        return None
    return [w, s, e, n]


def _point_in_box(feature, box: list[float]) -> bool:
    """Is this feature's first coordinate inside [W,S,E,N]?

    KEPT IN THE WINDOW WHEN IT CANNOT BE PLACED, which is the same rule
    _window_response applies to a feature with no bbox: excluding something because
    this file did not understand its geometry would be the map deciding not to show
    something on the strength of its own ignorance.
    """
    w, s, e, n = box
    geom = (feature or {}).get("geometry") or {}
    coords = geom.get("coordinates")
    while isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (list, tuple)):
        coords = coords[0]
    if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
        return True
    try:
        lon, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return True
    return w <= lon <= e and s <= lat <= n


def _window_response(crt, layer: str, path: Path, rec: dict, box: list[float],
                     seen: dict):
    """A FeatureCollection of just the features overlapping `box`, or None.

    None means "no index beside this layer", and the caller then hands back the whole
    file — which is the honest fallback: the console says HELD and explains that this
    backend did not window it, rather than either of us pretending a partial answer is
    the layer.
    """
    idx_path = crt.national_index_path(layer)
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        entries = idx["entries"]
    except Exception as exc:  # noqa: BLE001 — an older card has no index; say so once
        log.info("no window index beside %s (%s) — the whole layer will be served",
                 path.name, exc)
        return None
    w, s, e, n = box
    picks = [(int(row[0]), int(row[1])) for row in entries
             # A feature with no box could not be placed at all, so it can never be
             # windowed OUT: excluding it would be this file deciding not to show
             # something on the strength of not understanding it.
             if len(row) < 6 or not (row[4] < w or row[2] > e or row[5] < s or row[3] > n)]
    total = rec.get("features")
    head = {
        "type": "FeatureCollection",
        "attribution": rec.get("attribution"),
        "scope": "national", "status": "present", "layer": layer,
        "windowed": True, "window": box,
        "features_in_window": len(picks), "features_total": total,
        "fetched": rec.get("fetched"),
        "title": (f"{layer}: {len(picks)} of the {total} feature(s) this handheld holds "
                  f"nationally, being the ones inside the box the map is looking at. "
                  f"This is PAGING and not pruning — the whole layer is on this "
                  f"machine, and what is outside this box is outside the screen too."),
        "aria_label": (f"National layer {layer}, windowed: {len(picks)} of {total} "
                       f"features, being those inside the current map view. The whole "
                       f"layer is held on this handheld."),
    }
    prefix = (json.dumps(head)[:-1] + ',"features":[').encode("utf-8")

    def body():
        yield prefix
        try:
            with open(path, "rb") as fh:
                first = True
                for off, ln in picks:
                    fh.seek(off)
                    raw = fh.read(ln)
                    if not first:
                        yield b","
                    yield raw
                    first = False
        except OSError as exc:
            # THE FILE WENT AWAY MID-STREAM. Nothing can be un-sent, so the collection
            # is closed and the truncation is left to fail at the client's JSON parse —
            # which draws nothing and claims nothing. A silently short list would be the
            # empty-canal lie with a 200 in front of it.
            log.warning("windowed read of %s failed part-way: %s", path, exc)
        yield b"]}"

    return StreamingResponse(
        body(), media_type="application/geo+json",
        headers={"X-Neptune-Scope": "national",
                 "X-Neptune-Windowed": "true",
                 "X-Neptune-Features": f"{len(picks)}/{total}",
                 "X-Neptune-Verified": seen["check"],
                 "Cache-Control": "no-cache"})


def _national_document(svc, block: dict) -> dict:
    """GET /api/crt — what Trust data this handheld holds, and what it does not.

    THE SAME SHAPE AS THE PER-AREA INDEX, on purpose: one array of layers carrying the
    absent rows as well as the present ones, each with `present` and `count`, because
    the console binds one table to whatever the wire calls a layer and then needs to
    know, per layer, whether to draw it or to say ABSENT. Two documents with different
    key names is how the two halves of this feature have already been broken twice.
    """
    # `path` beside `url`, same value. The console reads `meta.path || meta.url`, and
    # publishing both costs a key and removes the whole class of bug where one half of
    # this feature spells an endpoint one way and the other half spells it another.
    rows = [{**row, "present": row.get("status") == "present",
             "count": row.get("features"), "path": row.get("url")}
            for row in block.get("layers") or []]
    rows += [{**row, "present": False, "count": None,
              "url": f"/api/crt/{row['layer']}", "path": f"/api/crt/{row['layer']}"}
             for row in block.get("skipped") or []]
    n_absent = ((1 if block["status"] != "present" else 0)
                + len(block.get("failed") or []))
    n_corrupt = len(block.get("unreadable") or [])
    n_part = len(block.get("partial") or [])
    bits = ([f"{n_absent} layer(s) are MISSING"] if n_absent else []) + \
           ([f"{n_corrupt} are on this handheld and UNREADABLE"] if n_corrupt else []) + \
           ([f"{n_part} are still downloading"] if n_part else [])
    summary = (" and ".join(bits) + " — absent is not empty, unreadable is not empty "
               "either, and nothing here claims any water is clear."
               if bits else
               f"Every layer the Canal & River Trust publishes is on this handheld: "
               f"{len(rows)} layer(s), {block.get('features')} feature(s). No area is "
               f"needed to have them and none was used to get them.")
    return {
        "scope": "national",
        "status": block["status"],
        "complete": bool(block.get("complete")),
        "fetched": block.get("fetched"),
        "state": block.get("state"),
        "clip_rule": block.get("clip_rule"),
        "attribution": block.get("attribution"),
        "dir": block.get("dir"),
        "features": block.get("features"),
        "bytes": block.get("bytes"),
        # How many layers the Trust offered the last time anybody could ask. The console
        # renders "N of TOTAL" while the first download is running, and without this it
        # has to fall back to a constant of its own that goes stale silently.
        "total": block.get("expected_layers") or len(rows),
        "layers": rows,
        "failed": block.get("failed") or [],
        "unreadable": block.get("unreadable") or [],
        "partial": block.get("partial") or [],
        "warnings": block.get("warnings") or [],
        "why": block.get("why"), "means": block.get("means"),
        "remedy": block.get("remedy") or _NATIONAL_CMD,
        # WHAT IS HAPPENING ABOUT IT, in the same document. An operator looking at a
        # missing layer's row has exactly one next question, and making them poll a
        # second endpoint for it is how a download in progress gets read as a failure.
        "fetch": svc.national_state() if svc is not None else None,
        # The active area, offered as what it now is: a hint for DRAWING. Null is a
        # perfectly good answer and nothing above depends on it.
        "area": svc.active_area if svc is not None else None,
        "title": f"Canal & River Trust data on this handheld. {summary}",
        "aria_label": (f"National Canal and River Trust layers. {summary}"),
    }


def _national_summary(block: dict) -> dict:
    """The national card in the handful of numbers a panel actually renders."""
    return {"status": block.get("status"), "complete": bool(block.get("complete")),
            "layers": len(block.get("layers") or []),
            "features": block.get("features"), "bytes": block.get("bytes"),
            "fetched": block.get("fetched"),
            "failed": block.get("failed") or [],
            "unreadable": block.get("unreadable") or [],
            "partial": block.get("partial") or [],
            "why": block.get("why"), "means": block.get("means"),
            "remedy": block.get("remedy")}


def _national_offline_record(stale_why: str, net_why: str) -> dict:
    """The record of a national fetch that never started because there is no internet.

    A REAL ANSWER AND NOT AN ERROR, and it is the same shape as an area fetch's so the
    one panel can render it. What is on the handheld is still on the handheld and is
    still drawn; what is missing stays missing until there is a connection.
    """
    return {
        "scope": "national", "area": None, "state": "offline",
        "started": _iso(), "finished": _iso(), "pid": os.getpid(),
        "net": {"ok": False, "why": net_why}, "sources": {}, "order": [],
        "title": (f"The Canal & River Trust's national layers are not complete on this "
                  f"handheld and there is no internet here to finish them: {net_why}. "
                  f"{stale_why}. What IS on the card is drawn exactly as it is — this "
                  f"is the normal state at the water's edge and not a fault."),
        "aria_label": (f"No national download was started because there is no internet "
                       f"connection. {net_why}"),
    }


class NationalFetch:
    """One background job that brings the WHOLE Trust network onto this handheld.

    NOT AN AREA JOB WITH A DIFFERENT NAME. It has no bbox, no imagery and no area: it
    is 27 layers, ~140 MB, fetched once and resumed across launches, and every area on
    this card draws from the one copy.

    IT REPORTS PER LAYER, on the channel the area fetch already broadcasts on, in the
    same snapshot shape — `sources` keyed by layer instead of by source kind, `order`
    saying what order they come in. A console that can render one can render the other,
    and `scope` says which it has.

    IT DOES NOT RAISE, for the reason AreaFetch does not: this runs beside a control
    loop that is flying a sub.
    """

    def __init__(self, *, reason: str = "", refresh: bool = False, on_change=None) -> None:
        self.reason = reason
        self.refresh = bool(refresh)
        self._on_change = on_change
        self.state = "queued"
        self.started: str | None = None
        self.finished: str | None = None
        self.error: str | None = None
        self.net: dict | None = None
        self.result: dict | None = None
        self.sources: dict[str, dict] = {}
        self.order: list[str] = []

    @property
    def is_running(self) -> bool:
        return self.state in _JOB_LIVE

    def _set(self, key: str, **fields) -> None:
        if key not in self.sources:
            self.sources[key] = {"status": "pending", "label": key, "done": 0,
                                 "total": None, "why": "", "detail": ""}
            self.order.append(key)
        status = fields.get("status")
        if status is not None and status not in _SRC_STATES:
            log.warning("national fetch layer %s given an unknown status %r (not one "
                        "of %s)", key, status, list(_SRC_STATES))
        self.sources[key].update(fields)

    def snapshot(self) -> dict:
        done = [k for k, s in self.sources.items() if s["status"] in ("done", "skipped")]
        failed = [k for k, s in self.sources.items() if s["status"] == "failed"]
        running = [k for k, s in self.sources.items() if s["status"] == "running"]
        return {
            # `area` is null and stays null. This download belongs to no area, and a
            # panel that showed one would be telling the operator that the country's
            # worth of layers is a property of the pound they happen to be on.
            "scope": "national", "area": None, "state": self.state,
            "reason": self.reason, "refresh": self.refresh,
            "started": self.started, "finished": self.finished,
            "error": self.error, "net": self.net, "pid": os.getpid(),
            "sources": {k: dict(v) for k, v in self.sources.items()},
            "order": list(self.order),
            # `done` AND `total` ARE NUMBERS HERE, and that is deliberate rather than
            # sloppy. This document is read by the console's national-download panel,
            # which renders "N of 27 layers" and needs two integers; the per-area job's
            # snapshot answers a different question with the same word (WHICH sources
            # finished) and keeps its list. The names are kept apart —`done_layers` is
            # the list — because one of them being quietly the other is precisely how
            # the two halves of this feature have been broken before.
            "done": len(done), "total": len(self.sources),
            "done_layers": done, "failed": failed,
            "layer": (running[0] if running else None),
            "why": (self.sources[running[0]]["detail"] if running else (self.error or "")),
            "features": self.result.get("features") if self.result else None,
            "bytes": self.result.get("bytes") if self.result else None,
            "title": self._title(done, failed, running),
            "aria_label": self._aria(done, failed),
        }

    def _title(self, done, failed, running) -> str:
        if self.state == "offline":
            return (f"Nothing was downloaded: there is no internet here. "
                    f"{(self.net or {}).get('why', '')}")
        if self.state == "cancelled":
            return ("The national download was stopped. Every layer that finished is on "
                    "this handheld and the part-downloaded one continues from where it "
                    "stopped the next time this starts.")
        if self.state in _JOB_LIVE:
            what = ", ".join(f"{k} ({self.sources[k]['detail']})" for k in running) \
                or "asking the Trust what it publishes"
            return (f"Downloading the Canal & River Trust's whole network: {what}. "
                    f"{len(done)} of {len(self.sources)} layer(s) finished. This is a "
                    f"one-time ~140 MB download, it resumes if it is interrupted, and "
                    f"the console stays flyable throughout.")
        if failed:
            return (f"The national layers: {len(done)} finished, {len(failed)} did not "
                    f"({', '.join(failed[:4])}). What failed was not written, so the map "
                    f"is missing it rather than drawing it as empty water — and the "
                    f"pages that did arrive are kept for the next run.")
        return (f"The Canal & River Trust's whole network is on this handheld: "
                f"{len(done)} layer(s). Nothing about it needs the internet again.")

    def _aria(self, done, failed) -> str:
        if self.state in _JOB_LIVE:
            return (f"Downloading the national Canal and River Trust layers. "
                    f"{len(done)} of {len(self.sources)} finished.")
        if self.state == "offline":
            return "No national download was started: there is no internet."
        if failed:
            return (f"The national download finished with failures. Completed: "
                    f"{len(done)}. Failed: {', '.join(failed)}.")
        return f"The national Canal and River Trust layers are complete: {len(done)}."

    def crash(self, exc: BaseException) -> None:
        self.state = "failed"
        self.error = f"{type(exc).__name__}: {exc}"
        self.finished = self.finished or _iso()
        for s in self.sources.values():
            if s["status"] in ("pending", "running"):
                s["status"] = "failed"
                s["why"] = f"the fetch job itself died: {self.error}"

    def stopped(self) -> None:
        """Cancelled — the process is going down, or the operator said stop."""
        self.state = "cancelled"
        self.finished = self.finished or _iso()
        for s in self.sources.values():
            if s["status"] in ("pending", "running"):
                s["status"] = "failed"
                s["why"] = ("the download was stopped before this layer finished; the "
                            "pages that arrived are kept and it continues from there")

    async def _emit(self) -> None:
        if self._on_change is None:
            return
        try:
            await self._on_change(self.snapshot())
        except Exception as exc:  # noqa: BLE001 — a watcher must not stop a download
            log.warning("national fetch progress callback failed: %s", exc)

    async def run(self, net: tuple[bool, str] | None = None) -> dict:
        crt = _crt_mod()
        self.started = _iso()
        try:
            if crt is None:
                self.state = "failed"
                self.error = ("api/nav/crt.py is not in this build, so nothing here can "
                              "download the Trust's layers at all")
                return self.snapshot()
            self.state = "checking"
            await self._emit()
            ok, why = net if net is not None else await internet_available()
            self.net = {"ok": ok, "why": why}
            if not ok:
                self.state = "offline"
                return self.snapshot()
            self.state = "running"
            await self._emit()
            res = await crt.download_national(progress=self._progress, refresh=self.refresh)
            self.result = res
            self.finished = _iso()
            if res.get("ok"):
                self.state = "done"
            else:
                self.state = "failed"
                self.error = res.get("error")
            await self._emit()
            return self.snapshot()
        except asyncio.CancelledError:
            self.stopped()
            raise
        except Exception:
            self.state = "failed"
            self.error = self.error or "the national fetch died"
            raise
        finally:
            self.finished = self.finished or _iso()

    async def _progress(self, msg: dict) -> None:
        """crt.py's per-layer messages → the per-layer table the console renders."""
        st = msg.get("state")
        key = msg.get("layer")
        if st == "layer" and key:
            self._set(key, status="running", done=0, total=msg.get("expect"),
                      label=msg.get("title") or key,
                      detail=f"layer {msg.get('n')} of {msg.get('of')}")
        elif st in ("paging", "resumed") and key:
            self._set(key, status="running", done=msg.get("features"),
                      total=msg.get("of"),
                      detail=(f"{msg.get('features')} of {msg.get('of')} feature(s)"
                              + (" — continuing where the last run stopped"
                                 if st == "resumed" else "")))
        elif st == "current" and key:
            # SKIPPED, NOT DONE, and the difference is the whole of "incremental": this
            # layer was already whole on the handheld and not one byte was re-requested.
            self._set(key, status="skipped", done=msg.get("features"),
                      total=msg.get("features"),
                      detail=f"already here, fetched {msg.get('fetched')}",
                      why=msg.get("why") or "")
        elif st == "wrote" and key:
            self._set(key, status="done", done=msg.get("features"),
                      total=msg.get("features"), why="",
                      detail=(f"{msg.get('features')} feature(s), "
                              f"{(msg.get('bytes') or 0) / 1e6:.1f} MB in "
                              f"{msg.get('seconds')}s"))
        elif st == "failed" and key:
            self._set(key, status="failed", done=msg.get("kept"),
                      why=(f"{msg.get('why')} — the {msg.get('kept', 0)} feature(s) that "
                           f"did arrive are kept and the next run continues from there"))
        await self._emit()


class AreaFetch:
    """One background job that brings ONE area's offline data up to date.

    Sequential by construction: one source after another, each driven by the
    module that owns it. It exists to be watched — snapshot() is a complete document
    at every instant, per source, so a console can render "charts done, imagery
    failed" instead of a single percentage that says nothing an operator can act on.

    IT DOES NOT RAISE. Same rule crt.download_hazards states for itself, for the same
    reason one layer up: a Trust server having a bad afternoon must not throw away an
    imagery pyramid that already succeeded. Every source failure is caught, recorded
    with its reason, and the next source is attempted.
    """

    def __init__(self, area: str, bbox: list[float], zmin: int, zmax: int, *,
                 refresh: bool = False, reason: str = "", radius_m: float | None = None,
                 on_change=None) -> None:
        self.area = area
        self.bbox = [float(v) for v in bbox]
        self.zmin, self.zmax = int(zmin), int(zmax)
        self.refresh = bool(refresh)
        self.reason = reason
        self.radius_m = radius_m
        self._on_change = on_change
        self.state = "queued"
        self.started: str | None = None
        self.finished: str | None = None
        self.error: str | None = None
        self.net: dict | None = None
        # WHAT THIS WILL COST, carried on every progress snapshot rather than
        # computed by whoever renders one: "say what the cap is and make it visible"
        # means visible before the first request, not explainable after the last.
        self.cap = fetch_cap(self.bbox, self.zmin, self.zmax)
        self.sources: dict[str, dict] = {
            key: {"status": "pending", "label": label, "absence_means": means,
                  "done": 0, "total": None, "why": "", "detail": ""}
            for key, label, means in FETCH_SOURCES
        }

    # ---- state ----------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self.state in _JOB_LIVE

    def snapshot(self) -> dict:
        done = [k for k, s in self.sources.items() if s["status"] in ("done", "skipped")]
        failed = [k for k, s in self.sources.items() if s["status"] == "failed"]
        # NOT DONE AND NOT FAILED. A source this machine cannot do at all is its own
        # list, because folding it into either of the other two tells a lie in a
        # different direction each way: into `done` and the panel says the bank layer
        # is on the card, into `failed` and it says a download went wrong that was
        # never possible and never attempted.
        unavailable = [k for k, s in self.sources.items() if s["status"] == "unavailable"]
        return {
            # `scope` ON EVERY SNAPSHOT, because two different jobs now broadcast on one
            # socket as {"type": "area_fetch", …} — this one and the national fetch —
            # and a console that cannot tell them apart renders a country-wide download
            # into the panel for one pound of canal. The word, not the shape, is what
            # separates them: the shape is deliberately identical so one panel renders
            # both.
            "scope": "area",
            "area": self.area, "state": self.state, "reason": self.reason,
            "started": self.started, "finished": self.finished,
            "error": self.error, "net": self.net, "pid": os.getpid(),
            "bbox": self.bbox, "zmin": self.zmin, "zmax": self.zmax,
            "radius_m": self.radius_m, "refresh": self.refresh, "cap": self.cap,
            "sources": {k: dict(v) for k, v in self.sources.items()},
            "order": [k for k, _, _ in FETCH_SOURCES],
            "done": done, "failed": failed, "unavailable": unavailable,
            "title": self._title(done, failed, unavailable),
            "aria_label": self._aria(done, failed, unavailable),
        }

    def _settled(self) -> dict:
        """Stamp the finish time and WRITE THE CARD, then hand back the snapshot.

        Every terminal path goes through here. `_fetch_ended` also records, and that
        is deliberately kept — it is the backstop for run() raising after all — but
        it is an add_done_callback, which the loop schedules for a LATER turn. So
        between a job finishing and its callback running there was a window in which
        the task was done, the state said "done", and the card on disk still said
        "downloading". Anything polling in that window read a finished download as a
        live one, which is the same shape of lie as a frozen sensor reading: the
        number is stale and nothing on screen says so. Recording synchronously here
        closes the window rather than making it small.
        """
        self.finished = self.finished or _iso()
        snap = self.snapshot()
        try:
            _record_fetch(self.area, snap)
        except Exception as exc:  # noqa: BLE001 — a fetch must not die of bookkeeping
            log.warning("could not record the settled fetch for %s: %s", self.area, exc)
        return snap

    def _title(self, done: list[str], failed: list[str],
               unavailable: list[str] | None = None) -> str:
        running = [k for k, s in self.sources.items() if s["status"] == "running"]
        # SAID IN EVERY SENTENCE THIS JOB PRODUCES, including the happy one. A run that
        # fetched everything it could still has to name what it could not do at all,
        # or "up to date" is a claim about a card that is missing a layer.
        cannot = ""
        for key in (unavailable or []):
            cannot += (f" {key} was NOT attempted on this machine: "
                       f"{self.sources[key].get('why') or 'it is unavailable here'}.")
        if self.state == "offline":
            return (f"Nothing was downloaded for {self.area}: there is no internet here. "
                    f"{(self.net or {}).get('why', '')}{cannot}")
        if self.state == "cancelled":
            return (f"The fetch for {self.area} was stopped. What had already landed is on "
                    f"the card; the rest is not, and the map will draw the difference as "
                    f"blank rather than as empty water.{cannot}")
        if self.state in _JOB_LIVE:
            what = ", ".join(f"{k} ({self.sources[k]['detail']})" for k in running) or "starting"
            return (f"Downloading offline data for {self.area}: {what}. "
                    f"{len(done)} of {len(self.sources)} source(s) finished. The console "
                    f"stays flyable throughout — this runs in the background.{cannot}")
        if failed:
            return (f"Offline data for {self.area}: {', '.join(done) or 'nothing'} finished, "
                    f"{', '.join(failed)} FAILED. What failed was not written, so the map is "
                    f"missing it rather than showing it as empty.{cannot}")
        return (f"Offline data for {self.area} is up to date: "
                f"{', '.join(done)} — nothing here needs the internet again.{cannot}")

    def _aria(self, done: list[str], failed: list[str],
              unavailable: list[str] | None = None) -> str:
        cannot = (f" {', '.join(unavailable)} could not be attempted on this machine."
                  if unavailable else "")
        if self.state in _JOB_LIVE:
            return (f"Downloading offline data for area {self.area}. {len(done)} of "
                    f"{len(self.sources)} sources finished.{cannot}")
        if self.state == "offline":
            return f"No download was started for area {self.area}: there is no internet."
        if failed:
            return (f"Download for area {self.area} finished with failures. Completed: "
                    f"{', '.join(done) or 'none'}. Failed: {', '.join(failed)}.{cannot}")
        return (f"Download for area {self.area} is complete. Sources: "
                f"{', '.join(done)}.{cannot}")

    def crash(self, exc: BaseException) -> None:
        """The job died of something it did not catch. Say so where it will be seen."""
        self.state = "failed"
        self.error = f"{type(exc).__name__}: {exc}"
        self.finished = self.finished or _iso()
        for s in self.sources.values():
            if s["status"] in ("pending", "running"):
                s["status"] = "failed"
                s["why"] = f"the fetch job itself died: {self.error}"

    # ---- progress -------------------------------------------------------
    def _set(self, key: str, **fields) -> None:
        status = fields.get("status")
        if status is not None and status not in _SRC_STATES:
            # Warned, not raised: a mistyped status is a bug in this file and it
            # must not be the thing that ends a download the operator is waiting
            # for. It is worth saying out loud, though — the console switches on
            # these words, and one it does not know renders as nothing at all,
            # which is the silent-blank failure this whole subsystem is against.
            log.warning("fetch source %s given an unknown status %r (not one of %s)",
                        key, status, list(_SRC_STATES))
        self.sources[key].update(fields)

    async def _emit(self) -> None:
        if self._on_change is None:
            return
        try:
            await self._on_change(self.snapshot())
        except Exception as exc:  # noqa: BLE001 — a watcher must not stop a download
            log.warning("fetch progress callback failed: %s", exc)

    # ---- the run --------------------------------------------------------
    async def run(self, net: tuple[bool, str] | None = None) -> dict:
        self.started = _iso()
        try:
            self.state = "checking"
            await self._emit()
            # GATED ONCE, HERE. Not per request and not per source: a job that
            # re-probed between layers would spend a canal-side afternoon on
            # timeouts, and a job that probed nothing would hand every one of a
            # thousand tile requests to a resolver that is not there.
            # WHAT THIS MACHINE CANNOT DO AT ALL, decided before the internet is even
            # asked about — because it is not a question the internet answers. See
            # _unavailable_sources: no signal changes when you drive somewhere, a
            # library that is not installed does not.
            for key, cannot in self._unavailable_sources().items():
                self._set(key, status="unavailable", why=cannot,
                          detail="not possible on this machine")
            ok, why = net if net is not None else await internet_available()
            self.net = {"ok": ok, "why": why}
            if not ok:
                self.state = "offline"
                for key, src in self.sources.items():
                    # An unavailable source is NOT re-labelled "waiting for internet".
                    # That sentence sends an operator back with a hotspot to fetch
                    # something that would not build even with one.
                    if src["status"] == "unavailable":
                        continue
                    self._set(key, status="pending",
                              why=f"not started — there is no internet: {why}")
                return self._settled()
            if not self.cap["within"]:
                self.state = "failed"
                self.error = self.cap["title"]
                for key, src in self.sources.items():
                    if src["status"] == "unavailable":
                        continue
                    self._set(key, status="failed", why=self.cap["title"])
                return self._settled()

            self.state = "running"
            await self._emit()
            await self._label()
            await self._centreline()
            await self._charts()
            await self._bank()
            await self._imagery()

            failed = [k for k, s in self.sources.items() if s["status"] == "failed"]
            self.state = "failed" if failed else "done"
            if failed:
                self.error = "; ".join(f"{k}: {self.sources[k]['why']}" for k in failed)
            return self._settled()
        except asyncio.CancelledError:
            raise
        except Exception:
            # run() is documented as not raising and _fetch_ended exists to catch it
            # being wrong about that — but the CARD must not be left saying
            # "downloading" in the turn before that callback gets to run. Record
            # first, then let the callback do its own accounting.
            self.state = "failed"
            self.error = self.error or "the fetch died"
            self._settled()
            raise
        except asyncio.CancelledError:
            # STOPPED, and it has to be written down without awaiting anything —
            # the loop is tearing this task down and an await here may never
            # resume. A synchronous merge is the last honest act available.
            self.state = "cancelled"
            self.finished = _iso()
            for s in self.sources.values():
                if s["status"] in ("pending", "running"):
                    s["status"] = "failed"
                    s["why"] = "the fetch was stopped before this source was finished"
            _record_fetch(self.area, self.snapshot())
            raise
        finally:
            self.finished = self.finished or _iso()

    def _restore(self, keep: dict) -> None:
        """Re-apply the metadata satellite.download_area wrote over. See
        _PRESERVE_ACROSS_IMAGERY. Never overwrites a field the download supplied."""
        if not keep:
            return
        now = _area_meta(self.area) or {}
        missing = {k: v for k, v in keep.items() if now.get(k) is None}
        if not missing:
            return
        try:
            areamod._merge_meta(self.area, missing)
        except Exception as exc:  # noqa: BLE001 — the tiles matter more than the labels
            log.warning("could not restore area metadata for %s: %s", self.area, exc)

    def _unavailable_sources(self) -> dict[str, str]:
        """Which sources this MACHINE cannot do at all, and why. Asked once per job.

        DELIBERATELY NOT THE SAME QUESTION AS "IS THERE INTERNET". No signal is a
        fact about here and now, and it changes the moment somebody drives to a
        car park with bars on the phone; this is a fact about the machine, and
        driving does not fix it. Reporting the second as the first is how an
        operator ends up going home, finding a connection, re-running the fetch and
        getting the same empty bank layer with the same reassuring "no internet"
        beside it.

        Only the bank overlay can answer yes today: it is the one source whose work
        needs libraries this repo does not put on the vehicle. The dict shape is so
        the next such source is a line rather than a rewrite.
        """
        out: dict[str, str] = {}
        try:
            block = _bank_block(self.area)
        except Exception as exc:  # noqa: BLE001 — a fetch must not die deciding what to skip
            log.warning("could not decide whether the bank layer is buildable: %s", exc)
            return out
        if block.get("status") == "unavailable":
            why = block.get("why") or "this machine cannot build it"
            remedy = block.get("remedy")
            out["bank"] = f"{why} — {remedy}" if remedy else why
        return out

    async def _label(self) -> None:
        """Give a date-named area a human title, once, while there is internet.

        nav/areas.py names an area before this job exists — it has to, because
        creating an area must work with no network — so a launch point with no
        geocode becomes "launch-2026-08-08". With six of those on a handheld the
        operator cannot tell which pound is which. This fills in `label`, which
        areas.py documents as the field a late geocode goes into, and renames
        nothing: the .mbtiles, data/crt/<name>/ and every dive journal that names
        an area all hang off the name.
        """
        meta = _area_meta(self.area) or {}
        if meta.get("label") or not meta.get("origin"):
            return
        o = meta["origin"]
        label = await _reverse_label(o.get("lat"), o.get("lon"))
        if label:
            try:
                areamod._merge_meta(self.area, {"label": label})
            except Exception as exc:  # noqa: BLE001 — a nameless area still works
                log.info("could not store the area label: %s", exc)

    # ---- source 1: the waterway centreline ------------------------------
    async def _centreline(self) -> None:
        key = "centreline"
        path = settings.areas_dir / f"{self.area}.geojson"
        state, err, feats, _b = _read_layer(path)
        if state == "present" and not self.refresh:
            # ALREADY ON THE CARD. Skipped, not re-fetched: Overpass is a free
            # public service run on donations and this file does not change between
            # two dives on the same canal.
            self._set(key, status="skipped", done=1, total=1,
                      detail=f"already on the card ({feats} ways)",
                      why="already downloaded — nothing was re-requested")
            await self._emit()
            return
        self._set(key, status="running", total=1, detail="asking Overpass for the channel")
        await self._emit()
        try:
            gj = await satmod.fetch_centreline(self.bbox)
        except Exception as exc:  # noqa: BLE001
            self._set(key, status="failed", why=f"{type(exc).__name__}: {exc}")
            await self._emit()
            return
        if not gj:
            # TWO DIFFERENT THINGS ARRIVE HERE AS None and satellite.py cannot tell
            # them apart: a query that failed, and a box with no mapped waterway in
            # it. Recorded as a failure with both readings named, because the one
            # thing that must not happen is an empty centreline file being written
            # — that would claim "there is no canal here", which is the exact lie
            # the rest of this module is built to refuse.
            self._set(key, status="failed",
                      why=("Overpass returned no waterway for this box. Either nothing "
                           "is mapped here or the query did not get through, and "
                           "nothing on this vehicle can tell which — so no file was "
                           "written, because an empty centreline would claim there is "
                           "no canal here"))
            await self._emit()
            return
        # Written through areas.py's atomic writer — the same one the metadata
        # uses. A centreline truncated by a Ctrl-C parses as nothing, and this
        # file's own /centreline endpoint would then report UNREADABLE for a
        # layer that was perfectly good a second earlier.
        areamod._atomic_write_json(path, gj)
        n = len(gj.get("features") or [])
        self._set(key, status="done", done=1, total=1,
                  detail=f"{n} way(s) written", why="")
        await self._emit()

    # ---- source 2: this area's CLIPPED COPY of the CRT layers ------------
    #
    # WHAT THIS SOURCE IS NOW, AND WHAT IT IS NOT. It is the drawing optimisation: the
    # same national layers cut to this area's box, so a renderer over one pound of canal
    # draws 40 bridges instead of 6,916. IT IS NOT WHERE THE DATA COMES FROM ANY MORE.
    # The whole network is fetched nationally when the map backend starts
    # (NavService._national_bootstrap) and again the moment a launch point is set with a
    # connection behind it, and the serving side answers out of that card whether or not
    # this clip ever lands — _crt_layers reads the national card first, so a failure
    # here no longer means the console has nothing to draw. It means it will draw the
    # national file.
    #
    # THE CLIP IS READ THROUGH _area_crt_layers, NOT _crt_layers, on purpose: _crt_layers
    # prefers the national card, and asking it here would report this area's copy as
    # already present because the country's is, and the clip would never be cut at all.
    async def _charts(self) -> None:
        key = "charts"
        crt = _crt_mod()
        if crt is None:
            self._set(key, status="failed",
                      why="api/nav/crt.py is not in this build, so nothing here can "
                          "download hazard data at all")
            await self._emit()
            return
        block = _area_crt_layers(self.area)
        if (block["status"] == "present" and not block.get("failed")
                and not block.get("unreadable") and not self.refresh):
            self._set(key, status="skipped", done=1, total=1,
                      detail=f"{len(block.get('layers') or [])} layer(s) already on the card",
                      why=(f"every layer the last fetch produced is present and parses "
                           f"(fetched {block.get('fetched')}) — nothing was re-requested. "
                           f"Pass refresh to fetch them again"))
            await self._emit()
            return
        self._set(key, status="running", detail="asking the Trust what it publishes")
        await self._emit()

        async def say(msg: dict) -> None:
            st = msg.get("state")
            if st == "layer":
                self._set(key, done=max(0, int(msg.get("n") or 1) - 1),
                          total=msg.get("of"), detail=f"layer {msg.get('layer')}")
                await self._emit()
            elif st == "wrote":
                self._set(key, detail=f"{msg.get('layer')}: {msg.get('features')} feature(s)")
                await self._emit()

        try:
            res = await crt.download_hazards(self.area, self.bbox, progress=say)
        except Exception as exc:  # noqa: BLE001 — documented not to raise; believe the disk
            self._set(key, status="failed", why=f"the hazard fetch raised: {exc}")
            await self._emit()
            return
        # BELIEVE THE DISK, NOT THE RETURN VALUE — the same rule nav/cli.py's
        # crt-fetch already follows. download_hazards reports failure by returning,
        # and a layer that did not land leaves no file on purpose.
        after = _area_crt_layers(self.area)
        n = len(after.get("layers") or [])
        bad = list(after.get("failed") or []) + list(after.get("unreadable") or [])
        held = _national_layers()
        # WHAT A FAILED CLIP COSTS, SAID IN THE SAME BREATH AS THE FAILURE. It used to
        # cost the operator every hazard in this water. With the national card complete
        # it costs a smaller file to draw, and the sentence has to say which — an alarm
        # that no longer means what it used to is an alarm people learn to ignore.
        fallback = (f" The whole national set IS on this handheld "
                    f"({len(held.get('layers') or [])} layer(s), "
                    f"{held.get('features')} feature(s)), so the console draws those "
                    f"instead; this clip is a smaller file and not a different claim."
                    if held.get("complete") else
                    f" The national set is NOT complete either ({held.get('why') or ''}) "
                    f"— so nothing is known about the hazards these would have shown.")
        if after["status"] != "present" or bad:
            self._set(key, status="failed", done=n, total=n + len(bad),
                      detail=f"{n} layer(s) clipped to this area",
                      why=((res.get("error") or after.get("why")
                            or f"{len(bad)} layer(s) missing or unreadable: "
                               f"{', '.join(bad[:6])}") + fallback))
        else:
            self._set(key, status="done", done=n, total=n,
                      detail=f"{n} layer(s), {res.get('features', '?')} feature(s)", why="")
        await self._emit()

    # ---- source 3: the LAUNCH-BANK OVERLAY, from a LIDAR ground model ------
    #
    # WHAT THIS SOURCE IS, AND WHY IT IS TWO MODULES. nav/lidar.py fetches the
    # Environment Agency's 1 m terrain model for this box, a handful of large
    # sub-requests, and mosaics it into one float32 grid on the card. nav/bank.py then
    # classifies that grid against the water level of each pound it detects and writes
    # the overlay pyramid. They are separate because they fail separately and are fixed
    # separately: a hotspot that died leaves the DOWNLOAD partial and re-running helps,
    # while a corridor the survey never flew leaves the PAINT partial and no amount of
    # re-running will change it. One source key, two steps, and the sentences say which.
    #
    # THE CLASSIFICATION IS SYNCHRONOUS AND CPU-BOUND AND MUST NOT RUN ON THIS LOOP.
    # bank.render_area's own docstring says so: "an async caller wraps it in
    # asyncio.to_thread". That caller is here. This event loop is also flying the sub at
    # 10 Hz, and a numpy pass over a 144 MB grid taken on it is seconds in which nothing
    # steers, no telemetry goes out and no leak alarm is read.
    #
    # A FAILURE HERE IS NOT A FAILURE OF THE DIVE, and it is not nothing either: what is
    # lost is the answer to "which side could I get down with the sub and the cable",
    # which is asked at the worst possible moment when it was not asked at home. So it
    # is recorded exactly as loudly as the others, and it never stops the imagery.
    async def _bank(self) -> None:
        key = "bank"
        if self.sources[key]["status"] == "unavailable":
            # Already decided in run(), before the internet was asked about. Nothing to
            # add and nothing to attempt.
            return
        before = _bank_block(self.area)
        if before["status"] == "unavailable":
            # ASKED AGAIN HERE, not only in run(). This method is also driven on its
            # own — `python -m nav.cli bank-fetch` runs exactly this and nothing else —
            # and a version that only checked in its caller would sail past a missing
            # numpy and hand the box to a builder that cannot build it.
            self._set(key, status="unavailable", detail="not possible on this machine",
                      why=" — ".join(s for s in (before.get("why"),
                                                 before.get("remedy")) if s))
            await self._emit()
            return
        if before["status"] in ("present", "partial") and not self.refresh:
            # ALREADY BUILT. Skipped rather than rebuilt: the terrain under a canal does
            # not move between two weekends, the download is somebody's public service,
            # and the classification is minutes of a handheld's battery for a
            # byte-identical answer. PARTIAL counts as built here for the reason
            # _source_held gives — where the survey has holes there is nothing more to
            # fetch, and re-running would spend the whole cost to produce the same holes.
            # `refresh` is how you say you meant it.
            self._set(key, status="skipped", detail=f"already on the card ({before['status']})",
                      why=(f"{before.get('why') or 'already built'} — nothing was "
                           f"downloaded and nothing was recomputed. Pass refresh to "
                           f"build it again"))
            await self._emit()
            return

        lidar = _lidar_mod()
        bank = _bank_mod()
        get = getattr(lidar, "download_dtm", None) if lidar is not None else None
        render = getattr(bank, "render_area", None) if bank is not None else None
        if not callable(get) or not callable(render):
            self._set(key, status="unavailable", detail="not possible on this machine",
                      why=("this build does not offer lidar.download_dtm(area, bbox, "
                           "progress=, refresh=) and bank.render_area(area, progress=), "
                           "so nothing here can build a launch-bank layer"))
            await self._emit()
            return

        # ---- step 1: the terrain ------------------------------------------
        self._set(key, status="running",
                  detail="asking the Environment Agency for the terrain model")
        await self._emit()

        async def say(msg: dict) -> None:
            # DEFENSIVE ON EVERY KEY. This is another module's progress dict and a
            # KeyError raised inside a progress callback would end a download that was
            # working perfectly.
            if not isinstance(msg, dict):
                return
            got, want = msg.get("done"), msg.get("of") or msg.get("total")
            bits = [str(msg.get("state") or "")]
            if msg.get("why"):
                bits.append(str(msg["why"]))
            self._set(key, done=got, total=want,
                      detail=(msg.get("detail") or " ".join(b for b in bits if b)
                              or "downloading terrain"))
            await self._emit()

        try:
            got = await get(self.area, self.bbox, progress=say, refresh=self.refresh)
        except Exception as exc:  # noqa: BLE001 — one source may never end the job
            self._set(key, status="failed", why=f"the terrain download raised: {exc}")
            await self._emit()
            return
        got = got if isinstance(got, dict) else {}
        state, why = got.get("state"), got.get("why") or ""
        if state == "unavailable":
            self._set(key, status="unavailable", detail="not possible on this machine",
                      why=why or "the terrain half reported itself unavailable")
            await self._emit()
            return
        if state == "refused":
            self._set(key, status="failed", why=why or "the terrain download was refused")
            await self._emit()
            return
        if state == "absent":
            # NOTHING TO FETCH, WHICH IS NOT THE SAME AS A FETCH THAT FAILED. Either no
            # Trust centreline crosses this box — so there is no corridor to survey — or
            # the survey does not reach this ground. Both are facts about the country
            # and neither is fixable by re-running with a better connection, so this is
            # a SKIP with the reason on it, exactly as crt.py's deliberate skips are.
            # Reported as a failure it would put a red mark on every fetch for a canal
            # outside the English LIDAR composite, for ever, and the operator would
            # learn to ignore the one row that also carries real failures.
            self._set(key, status="skipped",
                      detail="no terrain to fetch for this area",
                      why=why or ("no LIDAR terrain covers this area, so no bank layer "
                                  "can be built from it — the imagery here is drawn "
                                  "unpainted, which means NOT SURVEYED and not 'no low "
                                  "bank'"))
            await self._emit()
            return

        # ---- step 2: the classification, OFF THIS LOOP --------------------
        self._set(key, detail="classifying the terrain and writing overlay tiles")
        await self._emit()
        # The render's progress callback is SYNCHRONOUS — it is called from inside the
        # worker thread, where nothing can be awaited — so it only records, and this
        # loop publishes what it recorded while it waits. Handing an await across a
        # thread boundary is the other way to do this and it needs the loop's own
        # scheduler; polling once a second is enough for a step measured in tens of
        # seconds and it cannot deadlock.
        seen: dict = {}

        def tick(ev) -> None:
            if isinstance(ev, dict):
                seen.update(ev)

        task = asyncio.ensure_future(
            asyncio.to_thread(render, self.area, progress=tick))
        said = None
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1.0)
                if seen and seen != said:
                    said = dict(seen)
                    self._set(key, done=said.get("tiles"),
                              detail=(f"zoom {said.get('zoom')}: {said.get('tiles')} "
                                      f"tile(s) painted, {said.get('blank')} empty"))
                    await self._emit()
            task.result()
        except asyncio.CancelledError:
            # The whole JOB is being cancelled. The render is a plain thread and cannot
            # be interrupted; it writes through an atomic replace, so whatever it
            # finishes is either wholly there or not there at all. Let go of it rather
            # than leaving this coroutine waiting on a loop that is being torn down.
            raise
        except Exception as exc:  # noqa: BLE001 — including BankUnavailable
            self._set(key, status="failed",
                      why=(f"the terrain downloaded and the classification failed "
                           f"({type(exc).__name__}: {exc}). The grid is on the card, so "
                           f"a re-run does not download it again"))
            await self._emit()
            return

        # BELIEVE THE DISK, NOT THE RETURN VALUE — the same rule _charts follows, for
        # the same reason: a builder reports what it thinks it wrote, and the question
        # before a dive is what is on the card.
        after = _bank_block(self.area)
        tiles = after.get("tiles")
        n = tiles.get("tiles") if isinstance(tiles, dict) else tiles
        if after["status"] in ("present", "partial"):
            self._set(key, status="done", done=n, total=n,
                      detail=(f"{n} overlay tile(s)" if isinstance(n, int) else "built")
                             + (f", {after.get('pounds')} water level(s)"
                                if after.get("pounds") is not None else ""),
                      # PARTIAL IS REPORTED AS DONE-WITH-A-SENTENCE, not as a failure.
                      # See _source_held: at anything under 99.5% corridor coverage
                      # bank.py calls a perfectly good build partial, and a source that
                      # went red on the ordinary outcome would be a red nobody reads.
                      why=("" if after["status"] == "present"
                           else after.get("why") or "part of this corridor has no "
                                                    "terrain behind it"))
        else:
            self._set(key, status="failed", done=n,
                      why=((after.get("why") or "no overlay tile was written")
                           + " The imagery here draws unpainted, which means NOT "
                             "SURVEYED and never 'no low bank'."))
        await self._emit()

    # ---- source 4: the satellite imagery --------------------------------
    async def _imagery(self) -> None:
        key = "imagery"
        have, want, err = _tiles_present(self.area, self.bbox, self.zmin, self.zmax)
        if err is not None:
            self._set(key, status="failed", done=0, total=want,
                      why=(f"the tile archive is on the card and cannot be read ({err}) — "
                           f"delete areas/{self.area}.mbtiles and fetch again, because "
                           f"re-running over a broken archive will not repair it"))
            await self._emit()
            return
        if have >= want and not self.refresh:
            self._set(key, status="skipped", done=have, total=want,
                      detail=f"all {want} tiles already on the card",
                      why="every tile in this pyramid is already downloaded — Esri was "
                          "not asked for a single one of them")
            await self._emit()
            return
        self._set(key, status="running", done=have, total=want,
                  detail=f"{have} of {want} tiles on the card")
        await self._emit()

        # WHEN THE LINK DIES MID-PYRAMID, STOP. satellite._fetch_retry gives every
        # tile three attempts with 1.5 s of backoff and then leaves it missing —
        # correct for ONE bad tile, and catastrophic for a hotspot that has gone
        # away, because it then spends 1.5 s per tile on the whole remaining
        # pyramid. Measured on the default 1.2 km area: 970 tiles is twenty-four
        # minutes of a console reporting a download against a network that is not
        # there, and no state on the card moving the entire time. That is the
        # "never spend the afternoon retrying" rule at tile scale.
        #
        # SO: the last time a tile actually LANDED is watched, and the download is
        # cancelled when nothing has for _IMAGERY_STALL_S. Whatever committed stays
        # on the card and the next run continues from it — this gives up on the
        # network, never on the tiles. The window has to be longer than a healthy
        # progress interval (satellite.py reports every 25 tiles, which at the
        # polite 6 a second is about four seconds) and shorter than an operator's
        # patience, and it is derived from the rate so a slower configured rate does
        # not turn into false cancellations.
        stall = max(_IMAGERY_STALL_S, 60.0 / max(0.5, settings.sat_rate_per_s))
        seen = {"ok": have, "at": time.monotonic()}

        async def say(msg: dict) -> None:
            if msg.get("state") != "running":
                return
            got = int(msg.get("ok") or 0)
            if got > seen["ok"]:
                seen["ok"], seen["at"] = got, time.monotonic()
            self._set(key, done=int(msg.get("done") or 0), total=msg.get("total"),
                      detail=f"{got} tile(s) written")
            await self._emit()

        # satellite.download_area walks the whole pyramid and writes with INSERT OR
        # REPLACE, so an interrupted archive is completed by re-running it and
        # nothing is ever left half-written — sqlite commits or it does not. It also
        # fetches the centreline itself at the end, which is one extra Overpass
        # request on a run that downloads imagery; it is left alone rather than
        # worked around, because this module sequences the downloaders and does not
        # reimplement them.
        # Taken BEFORE the download, because the download is what removes them.
        keep = {k: v for k, v in (_area_meta(self.area) or {}).items()
                if k in _PRESERVE_ACROSS_IMAGERY}
        task = asyncio.ensure_future(
            satmod.download_area(self.area, self.bbox, self.zmin, self.zmax, say,
                                 refresh=self.refresh))
        stalled = False
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1.0)
                if not task.done() and (time.monotonic() - seen["at"]) > stall:
                    stalled = True
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    break
            if not stalled:
                res = task.result()
        except asyncio.CancelledError:
            # The whole JOB is being cancelled, not just this source. Take the
            # download with it rather than leaving it writing into an archive
            # nobody is watching any more.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise
        except Exception as exc:  # noqa: BLE001
            self._set(key, status="failed", why=f"{type(exc).__name__}: {exc}")
            self._restore(keep)
            await self._emit()
            return
        # PUT BACK WHAT THE DOWNLOAD REMOVED, on every path out of it — a run that
        # stalled rewrote the file just as thoroughly as one that finished.
        self._restore(keep)
        if stalled:
            have, want, _err = _tiles_present(self.area, self.bbox, self.zmin, self.zmax)
            self._set(key, status="failed", done=have, total=want,
                      detail=f"{have} of {want} tiles on the card",
                      why=(f"no tile has arrived for {stall:.0f}s, so the connection is "
                           f"treated as gone and the download was stopped rather than "
                           f"spending {want - have} more retries on it. The {have} tile(s) "
                           f"that landed are on the card and a re-run continues from them"))
            await self._emit()
            return
        have, want, err = _tiles_present(self.area, self.bbox, self.zmin, self.zmax)
        if err is not None or have < want:
            self._set(key, status="failed", done=have, total=want,
                      detail=f"{have} of {want} tiles on the card",
                      why=(err or f"{want - have} tile(s) did not download. The map draws "
                                  f"what is here and blanks the rest; re-run to fill the "
                                  f"gaps, which will not re-request what landed"))
        else:
            self._set(key, status="done", done=have, total=want,
                      detail=f"{want} tiles, {res.get('size', 0):,} bytes", why="")
        await self._emit()


# ==========================================================================
def build_router(svc: NavService) -> APIRouter:
    r = APIRouter()

    @r.post("/api/origin")
    async def set_origin(o: Origin, override: bool = False, fetch: bool = True):
        if o.accuracy > settings.max_origin_accuracy_m and not override:
            raise HTTPException(422, f"origin accuracy {o.accuracy}m exceeds {settings.max_origin_accuracy_m}m "
                                     f"— re-fix or pass ?override=true")
        # heading0 is the sub's IMU yaw at this instant (§4.4) — authoritative over any
        # posted value, BUT ONLY WHEN A COMPASS ACTUALLY ANSWERED. SensorSample.
        # heading_deg went Optional this round and this was a bare round() on it:
        # with the BNO085 killed mid-run, POST /api/origin answered HTTP 500
        # "TypeError: type NoneType doesn't define __round__ method", so a hull whose
        # compass has stopped could not set a datum AT ALL — no origin, hence no
        # auto-logged dive and no navigation for the rest of the session. Topside it
        # was worse than an outright failure: client/js/navui.js mirrors the origin
        # with .catch(()=>{}) and writes "origin set" either way, so the console drew
        # a client-side track for a dive the Pi had never begun.
        #
        # LAUNCHING WITH NO COMPASS IS A STATE TO RECORD, NOT AN ERROR. heading0 is
        # dive-log metadata here and not an input to the estimate: DeadReckoner seeds
        # self.heading from it and then overwrites it from the first sample, and it
        # already holds the track on a null bearing. Nothing about a silent compass
        # justifies refusing the datum.
        measured = svc.last_sample.heading_deg if svc.last_sample is not None else None
        if measured is not None:
            o.heading_deg = round(measured, 1)
        else:
            # NOTHING MEASURED A BEARING, so the origin records that rather than a
            # number. 0.0 is due north, and heading0 is what every track from this
            # origin is expressed against, so a fabricated one tilts the whole dive
            # permanently in a file that outlives it. Origin.heading_deg is Optional
            # for exactly this, so the null survives the journal header and comes
            # back through DiveLog.load() instead of raising on the way in.
            o.heading_deg = None
        svc.set_origin(o)
        # THE LAUNCH POINT IS THE TRIGGER, and this line is the whole fix. Until now
        # nothing in this repo ever created an offline area, so data/areas/ was empty
        # and the console's "no chart data is downloaded" was permanently, correctly
        # true — the operator had to know to run a CLI command naming an area that did
        # not exist. Setting an origin is the first instant anything here knows WHERE
        # the vehicle will be, which is the one fact an area needs.
        #
        # IT RETURNS BEFORE ANYTHING IS DOWNLOADED. autofetch() only schedules a
        # decision; that decision reads the card first and touches the network solely
        # when something is missing AND there is internet. This endpoint is on the
        # path an operator uses at the water's edge with a controller in their hands,
        # so it may not wait for a socket, a DNS lookup or a thousand tiles.
        sched = svc.autofetch(o) if fetch else {
            "scheduled": False, "why": "not requested (?fetch=false)"}
        return {"ok": True, "origin": o.model_dump(), "heading0_measured": measured is not None,
                "fetch": sched}

    @r.get("/api/origin")
    async def get_origin():
        return svc.origin.model_dump() if svc.origin else JSONResponse({"set": False})

    @r.get("/api/nav/state")
    async def nav_state():
        # fresh_state, not last_state: docs/hardware.md sends the operator here to
        # read heading_deg while turning the sub through the magnetometer
        # calibration, and a heading frozen by a dead nav loop reads as a compass
        # that has stopped responding to the vehicle — the exact fault the
        # procedure is trying to find. has_state:false already means cannot-tell.
        ns = svc.fresh_state()
        if not ns:
            # WHY there is no state travels with the fact that there is none. A
            # bare has_state:false reads as "between dives" — which is what it
            # usually is, and is exactly why a dead loop hid inside it for two
            # review rounds. health() names which of the four it actually is.
            return JSONResponse({"has_state": False, "has_origin": bool(svc.origin),
                                 "nav": svc.health()})
        return {**ns.model_dump(), "nav": svc.health()}

    @r.get("/api/nav/health")
    async def nav_health():
        """Is navigation answering, and if not, why not — with no dive required.

        /api/nav/state can only say "no state", which is the normal answer before
        an origin exists. This endpoint is the one to point a check at.
        """
        return svc.health()

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

    @r.get("/api/areas")
    async def get_areas():
        return {"areas": areamod.list_areas(), "extractor_available": areamod.pmtiles_available()}

    @r.post("/api/areas/estimate")
    async def area_estimate(payload: dict = Body(...)):
        zmin, zmax = _zooms(payload.get("detail", "standard"))
        return satmod.estimate(payload["bbox"], payload.get("zmin", zmin), payload.get("zmax", zmax))

    # ---- the bootstrap fetch: drive it, and watch it -------------------------
    #
    # THREE ENDPOINTS AND A WEBSOCKET FRAME, because the console has three different
    # questions and they must not be answered by one number. "What is happening right
    # now" is GET /api/areas/fetch. "Is this area actually finished" is GET
    # /api/areas/<name>/complete, and it is answered off the disk with no job running
    # and no network — it is the question that survives a reboot. "Do it" is POST.
    # Progress arrives unasked on /ws/nav as {"type":"area_fetch", …}, the same socket
    # POST /api/areas already streams area_progress on, so a console that is already
    # connected does not have to poll to watch a download it started.

    @r.get("/api/areas/fetch")
    async def fetch_status():
        """The running area job, or the last one, or a plainly-idle document.

        `national` rides along because the two downloads are watched from one panel and
        an operator asking "is anything downloading" means either of them. It is the
        same document GET /api/crt/fetch returns.
        """
        return {**svc.fetch_state(), "national": svc.national_state()}

    # ---- THE NATIONAL CARD: no area anywhere in these three paths -------------
    #
    # WHY THEY ARE NOT UNDER /api/areas/. Because an area is not a precondition for
    # having this data and never will be again. A console at a kitchen table with no
    # launch point set, no imagery and no idea where it is going still holds every lock,
    # sluice, culvert and stop-plank groove in England and Wales, and it must be able to
    # ask for them without inventing a place first.

    @r.get("/api/crt")
    async def national_index():
        """Every Trust layer on this handheld, INCLUDING the ones that are not here."""
        return _national_document(svc, _national_layers(svc.active_area))

    @r.get("/api/crt/fetch")
    async def national_fetch_status():
        """What the national download is doing, or what is on the card."""
        return svc.national_state()

    @r.post("/api/crt/fetch")
    async def national_fetch_start(payload: dict = Body(default={})):
        """Fetch, or finish fetching, the national set. Returns AT ONCE.

        DOES NO NETWORK WORK OF ITS OWN, not even a name lookup: this answers in
        milliseconds and the job it starts does the asking, so a POST at the canal comes
        back "checking" and settles to "offline" a few seconds later on the socket.
        """
        payload = payload or {}
        return await svc.ensure_national(
            reason=payload.get("reason") or "asked for by the console",
            refresh=bool(payload.get("refresh")))

    @r.get("/api/crt/{layer}")
    async def national_layer(layer: str, bbox: str | None = None):
        """One national Trust layer — whole, or the part around ?bbox=W,S,E,N.

        SERVED AS THE FILE ON DISK rather than parsed and re-emitted, which is what the
        per-area endpoint does. The biggest of these is 100 MB of planning-buffer
        polygons; decoding and re-encoding that per request would cost seconds and a
        gigabyte on a machine that is flying a sub. crt.py writes `status`, `layer`,
        `scope`, `attribution` and `bbox` INTO the collection when it writes it, exactly
        so this can be a file handed straight out — and a truncated file fails to parse
        at the client, which is the correct outcome: nothing drawn, nothing claimed.

        WITH A bbox, the answer is a WINDOW on that layer, assembled by seeking to the
        bytes of the features that overlap it (see _window_response). That exists because
        the console genuinely cannot hold the biggest layer in a browser heap: without a
        window it reports the layer HELD-but-not-drawn, which is a hazard layer this
        handheld owns and never puts on the glass. Paging, not pruning — the index rows
        still report the whole layer and the response says what it is a window of.
        """
        if not _NAME_OK.match(layer or ""):
            raise HTTPException(400, "bad layer name")
        crt = _crt_mod()
        if crt is None:
            return _absent("national", layer,
                           why="api/nav/crt.py is not in this build",
                           means="nothing here can have downloaded the Trust's layers",
                           remedy=_NATIONAL_CMD)
        path = crt.national_dir() / f"{layer}.geojson"
        rec = crt.national_layer_record(layer) or {}
        seen = _verify_national(path, rec)
        if seen["status"] == "absent":
            card = crt.national_card()
            skip = next((s for s in card.get("skipped") or []
                         if s.get("layer_key") == layer), None)
            part = next((p for p in card.get("partial") or []
                         if p.get("layer_key") == layer), None)
            if part is not None:
                return _absent(
                    "national", layer,
                    why=(f"this layer is part-downloaded — {part.get('features')} "
                         f"feature(s) have arrived and it is not finished"),
                    means=("no file is written until the whole layer is here, because a "
                           "truncated layer draws exactly like an empty one. The pages "
                           "that arrived are kept and the next run continues from them"),
                    remedy=_NATIONAL_CMD)
            if skip is not None:
                deliberate = skip.get("skipped") in _DELIBERATE_SKIPS
                return _absent(
                    "national", layer,
                    why=f"the fetch skipped it ({skip.get('skipped')}): {skip.get('why')}",
                    means=("left out on purpose, and nothing was lost" if deliberate else
                           "the fetch could not complete this layer and wrote no file "
                           "rather than a partial one. Nothing is known about this kind "
                           "of feature anywhere"),
                    remedy=_NATIONAL_CMD)
            return _absent(
                "national", layer,
                why=("this handheld has no national layer by that name — "
                     + ("the national fetch has never run" if not card.get("layers")
                        else "the fetch ran and produced no such layer")),
                means="nothing is known about features of this kind, anywhere",
                remedy=_NATIONAL_CMD)
        if seen["status"] != "present":
            return _unreadable("national", layer, path,
                               ValueError(seen["error"] or _UNREADABLE_MEANS))
        if bbox:
            box = _window_box(bbox)
            if box is None:
                raise HTTPException(400, "bbox must be W,S,E,N in degrees")
            windowed = _window_response(crt, layer, path, rec, box, seen)
            if windowed is not None:
                return windowed
        return FileResponse(
            path, media_type="application/geo+json",
            headers={
                # The provenance a renderer needs before it draws, without a second
                # request and without decoding 100 MB to find it. BARE TOKENS ONLY:
                # a header is latin-1, and the first version of this put the prose
                # sentence in X-Neptune-Verified and answered HTTP 500 on the em-dash
                # inside it — a complete national card serving nothing over a dash.
                "X-Neptune-Scope": "national",
                "X-Neptune-Features": str(rec.get("features")),
                "X-Neptune-Fetched": str(rec.get("fetched")),
                "X-Neptune-Verified": seen["check"],
                "Cache-Control": "no-cache",
            })

    @r.post("/api/areas/fetch")
    async def fetch_start(payload: dict = Body(default={})):
        """Start one background fetch. Returns AT ONCE, with what it started.

        DELIBERATELY DOES NO NETWORK WORK OF ITS OWN, not even a name lookup: this
        answers in milliseconds and the job it started does the asking. The internet
        gate lives inside the job, so a POST at the canal comes back "checking" and
        settles to "offline" a few seconds later on the socket — which is a state the
        operator can see, rather than a request that sat there.
        """
        payload = payload or {}
        name = payload.get("name") or payload.get("area")
        lat, lon = payload.get("lat"), payload.get("lon")
        if lat is None or lon is None:
            o = svc.origin
            if o is not None:
                lat, lon = o.lat, o.lon
        want_detail = payload.get("detail")
        meta = _area_meta(name) if name else None
        if meta is None:
            # NO AREA BY THAT NAME YET, so one is defined — by nav/areas.py, which
            # owns the radius, the reuse rule and both caps. A refusal comes back as
            # its sentence, quoting the number it is enforcing, rather than as a
            # silently smaller download.
            if lat is None or lon is None:
                raise HTTPException(400, "give an existing area name, or lat+lon, or set "
                                         "an origin first — an offline area needs to know "
                                         "where it is")
            try:
                plan = areamod.create_area(
                    float(lat), float(lon), radius_m=payload.get("radius_m"),
                    bbox=payload.get("bbox"), name=name,
                    detail=want_detail or "standard")
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            name = plan["name"]
            meta = _area_meta(name) or {}
        bbox = meta.get("bbox")
        if not bbox:
            raise HTTPException(400, f"area {name!r} has no usable bbox, so there is no "
                                     f"box to fetch — nothing here will guess one")
        if want_detail is None:
            # Nobody said how much detail they wanted, so keep the pyramid this area
            # already has. Mixing zoom ranges into one archive would leave the
            # completeness check counting tiles against a range no run ever fetched.
            zmin = int(meta.get("minzoom") or settings.sat_min_zoom)
            zmax = int(meta.get("maxzoom") or settings.sat_max_zoom)
        else:
            zmin, zmax = _zooms(want_detail)
        cap = fetch_cap(bbox, zmin, zmax)
        if not cap["within"]:
            # REFUSED WITH THE NUMBERS, before the first request is made. Silently
            # shrinking the area would be the worse failure: the operator would get
            # a smaller card than they asked for and nothing on screen would say so.
            raise HTTPException(400, cap["title"])
        if svc.active_area != name:
            svc.activate_area(name)
        return await svc.start_fetch(name, bbox, zmin, zmax,
                                     refresh=bool(payload.get("refresh")),
                                     radius_m=(meta.get("origin") or {}).get("radius_m"),
                                     reason=payload.get("reason") or "asked for by the console")

    @r.post("/api/areas/fetch/cancel")
    async def fetch_cancel():
        """Stop the running job. What already landed stays on the card."""
        snap = await svc.cancel_fetch()
        if snap is None:
            raise HTTPException(404, "no fetch is running")
        return snap

    @r.get("/api/areas/{name}/complete")
    async def area_complete(name: str):
        """Is this area's offline data all here? Off the disk, per source.

        The pre-dive question, and it must be answerable with the radios off — so
        nothing in this path resolves a hostname or asks a running job anything. A
        card that was filled last week and a card that is filling right now are both
        described by what is on the disk plus the fetch record the disk carries.
        """
        if not _NAME_OK.match(name or ""):
            raise HTTPException(400, "bad area name")
        return area_completeness(name)

    @r.post("/api/areas")
    async def area_create(payload: dict = Body(...)):
        bbox = payload["bbox"]
        zmin, zmax = _zooms(payload.get("detail", "standard"))
        name = payload.get("name")
        if not name:                                    # §4 — auto-name (reverse geocode, else date)
            name = await satmod.reverse_geocode((bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2)
        # ONE NAMING RULE FOR THE WHOLE SUBSYSTEM, and it is nav/areas.py's. This
        # used to have its own slug function and its own coordinate fallback, so a
        # box drawn by hand and the same water reached from a launch point could
        # land on two directory names — two half-full cards, and the console would
        # show whichever it happened to activate. slugify is stricter than the old
        # one on purpose (this becomes a FAT filename, a URL segment and a directory
        # under data/crt/), and default_area_name supplies the date-based fallback.
        name = areamod.slugify(name) or areamod.default_area_name()

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

    # ---- THE LAUNCH-BANK OVERLAY: no area anywhere in these three paths -------
    #
    # WHY THEY ARE NOT UNDER /api/areas/, and it is the same reason the national chart
    # card is not. client/js/crt.js states the contract in one object at the top of
    # itself — /api/bank, /api/bank/tiles/{z}/{x}/{y}.png, /api/bank/pounds — and says
    # why: "NO AREA IN ANY PATH — the areas are reported BY the index rather than being
    # a parameter of it, so a console with no launch point still learns what is held."
    # A console at a kitchen table with no origin set can therefore ask what bank data
    # this handheld holds, and the map can draw the paint for whatever it is looking at
    # without first deciding which area that is.
    #
    # THE DOCUMENT COMES FIRST, ALWAYS. A raster overlay has the hazard layers' failure
    # and a worse one: a tile that is not there is a transparent hole, and a transparent
    # hole over a canal bank reads as "no low bank here" — a survey result nobody
    # produced. So the console is not meant to discover this layer by asking for tiles
    # and seeing what comes back; it reads the index, which says what is held per area
    # and in one word, and only then paints.
    #
    # ALL THREE ARE SERVED OFF THE DISK AND NONE NEEDS numpy. A tile is an sqlite row
    # out of an MBTiles archive, the index is two small JSON files per area. Only
    # BUILDING needs the raster stack, so a Pi handed a card built on the handheld
    # serves this layer perfectly — which is the whole point of building it at home.

    @r.get("/api/bank")
    async def bank_index():
        """What launch-bank data this handheld holds, INCLUDING what it does not.

        The keys are client/js/crt.js's, which reads `status`, `tiles`, `minzoom`,
        `maxzoom`, `threshold_m`, `vintage`, `attribution`, `why` and an `areas` array
        of {area, status, bbox, fetched, vintage, tiles, why}. They are spelled here
        exactly as that file spells them, because two halves of one feature spelling a
        field two ways is how this project has silently lost a feature five times.

        NEVER 404, AND NEVER AN EMPTY SUCCESS EITHER. A 404 is read by the console as
        "the bootstrap processing has not run here", which is one of the true answers
        but not the only one — a handheld with no numpy and a handheld that simply has
        not been pointed at an area are different situations with different fixes. So
        this always answers, and `status` plus `why` say which it is.
        """
        bank = _bank_mod()
        libs = _bank_libraries()
        cards = bank_cards()
        rows = []
        for c in cards:
            src = c.get("source") or {}
            tiles = c.get("tiles")
            rows.append({
                "area": c.get("area"),
                "status": c.get("state", "absent"),
                "bbox": c.get("bbox"),
                "fetched": src.get("fetched") or c.get("built"),
                "built": c.get("built"),
                "vintage": src.get("survey_vintage"),
                # A COUNT, because that is what the console's row shows. bank.py keeps
                # the whole tile report under this name; the number inside it is the
                # one thing a person can read.
                "tiles": (tiles.get("tiles") if isinstance(tiles, dict) else tiles),
                "pounds": c.get("pounds"),
                "why": c.get("why"),
                "title": c.get("title"),
                "aria_label": c.get("aria_label"),
            })
        held = [r for r in rows if r["status"] != "absent"]
        zmin = min((c.get("tiles", {}).get("zmin") for c in cards
                    if isinstance(c.get("tiles"), dict)
                    and c["tiles"].get("zmin") is not None),
                   default=getattr(bank, "BANK_ZMIN", None))
        zmax = max((c.get("tiles", {}).get("zmax") for c in cards
                    if isinstance(c.get("tiles"), dict)
                    and c["tiles"].get("zmax") is not None),
                   default=getattr(bank, "BANK_ZMAX", None))
        vintage = next((r["vintage"] for r in held if r.get("vintage")),
                       getattr(settings, "lidar_survey_vintage", ""))
        if bank is None:
            why = (_BANK_IMPORT.get("bank") or "api/nav/bank.py could not be loaded")
        elif not held:
            why = ((libs["why"] + " ") if not libs["ok"] else "") + (
                "no launch-bank layer has been built on this handheld yet. The imagery "
                "draws unpainted, and unpainted means NOT SURVEYED — it does not mean "
                "there is no low bank here. Build one with: "
                "python -m nav.cli bank-fetch <area>")
        else:
            why = (f"{len(held)} area(s) painted from the {vintage} Environment Agency "
                   f"LIDAR survey. Ground with no paint on it has not been looked at.")
        return {
            "layer": getattr(bank, "BANK_LAYER_KEY", "bank"),
            # 'absent' is the word the console switches on to mean "held nothing",
            # and it decides coverage for itself from the area boxes below.
            "status": "present" if held else "absent",
            "tiles": "/api/bank/tiles/{z}/{x}/{y}.png",
            "pounds": "/api/bank/pounds",
            "minzoom": zmin, "maxzoom": zmax,
            "threshold_m": getattr(settings, "lidar_launch_max_height_m", None),
            "vintage": vintage,
            "attribution": getattr(settings, "lidar_attribution", ""),
            "libraries": libs,
            "areas": rows,
            "why": why,
            "title": (f"LAUNCH BANKS: {len(held)} area(s) on this handheld. {why}"),
            "aria_label": (f"The launch bank layer holds {len(held)} area(s) on this "
                           f"handheld. {why}"),
        }

    @r.get("/api/bank/tiles/{z}/{x}/{y}.png")
    async def bank_tile(z: int, x: int, y: int):
        """One overlay tile, from whichever painted area holds it.

        NO AREA IN THE PATH, so this looks through the painted areas for one whose
        archive has this tile. That is a handful of sqlite lookups against files that
        are open anyway; the alternative is a console that has to know which area it is
        over before it can draw, which is exactly the coupling the wire was written to
        avoid.

        A MISSING TILE IS A 404 AND NOTHING ELSE — never a blank PNG. The console draws
        a missing bank tile as nothing at all and refuses to upscale a neighbour,
        because upscaling a classification paints amber over ground nobody classified;
        it decides what the hole MEANS from the index above, which is why that document
        is the one carrying the sentences.
        """
        bank = _bank_mod()
        fn = getattr(bank, "read_tile", None) if bank is not None else None
        if not callable(fn):
            raise HTTPException(
                404, "this build cannot serve launch-bank tiles: api/nav/bank.py is not "
                     "here. Ask GET /api/bank, which says so in a sentence.")
        for card in bank_cards():
            name = card.get("area")
            if not name:
                continue
            try:
                data = fn(name, z, x, y)
            except Exception as exc:  # noqa: BLE001 — a bad archive is a 404, never a 500
                log.warning("nav/bank.py read_tile(%s,%s,%s,%s) raised: %s",
                            name, z, x, y, exc)
                continue
            if data:
                return Response(
                    content=data, media_type="image/png",
                    # BARE ASCII TOKENS ONLY. Headers are latin-1, and the national
                    # layer endpoint above answered HTTP 500 once over an em-dash in
                    # one — an area name is already restricted to this alphabet.
                    headers={"X-Neptune-Bank-Area": str(name),
                             "Cache-Control": "public, max-age=604800"})
        raise HTTPException(404, "no painted area on this handheld holds that tile")

    @r.get("/api/bank/pounds")
    async def bank_pounds(bbox: str | None = None):
        """The detected water levels, as GeoJSON, for every painted area.

        THESE ARE HEIGHTS OF THE WATER SURFACE AND NEVER DEPTHS OF IT, and the sentence
        travels on the collection because a number floating over a canal with no
        sentence attached is read as whatever the reader already expected. Each is the
        level every bank beside it was measured against.

        ?bbox=W,S,E,N windows it, the same way the national chart layers are windowed
        and for the same reason: the console asks for the part it is looking at.
        Windowing here is a coordinate comparison over a few dozen labels, not the
        byte-offset index the 100 MB layers need.
        """
        bank = _bank_mod()
        fn = getattr(bank, "pound_labels", None) if bank is not None else None
        box = _window_box(bbox) if bbox else None
        if bbox and box is None:
            raise HTTPException(400, "bbox must be W,S,E,N in degrees")
        feats, areas = [], []
        if callable(fn):
            for card in bank_cards():
                name = card.get("area")
                if not name:
                    continue
                try:
                    doc = fn(name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("nav/bank.py pound_labels(%s) raised: %s", name, exc)
                    continue
                if not isinstance(doc, dict):
                    continue
                areas.append(name)
                for f in doc.get("features") or []:
                    if box is not None and not _point_in_box(f, box):
                        continue
                    props = dict(f.get("properties") or {})
                    props.setdefault("area", name)
                    # THE NAME THE CONSOLE ASKS FOR, BESIDE THE ONE THE PRODUCER
                    # WROTE, and neither replaces the other. nav/bank.py calls this
                    # `level_m_od` — the right name, because the unit and the datum
                    # are half the meaning — and client/js/crt.js's _bankLevelOf
                    # looks for `level_m`, `water_level_m` or `level` and gives up on
                    # anything else, which would leave every pound on the map
                    # unlabelled with nothing anywhere saying why. This is the same
                    # translation _surveyed_collection makes for `depth_m`, made in
                    # the same place and for the same reason: the producer's own name
                    # travels unchanged, so nothing downstream has to trust it.
                    if "level_m" not in props and props.get("level_m_od") is not None:
                        props["level_m"] = props["level_m_od"]
                    feats.append({**f, "properties": props})
        return {
            "type": "FeatureCollection",
            "layer": "bank-pounds",
            "areas": areas,
            "windowed": box is not None,
            "bbox": box,
            "features": feats,
            "vintage": getattr(settings, "lidar_survey_vintage", ""),
            "attribution": getattr(settings, "lidar_attribution", ""),
            "title": ("Detected water levels, in metres above Ordnance Datum, one per "
                      "sheet of flat water the LIDAR found along the corridor. Each is "
                      "the height of the SURFACE and the height every bank beside it "
                      "was measured against. Nothing here has measured how much water "
                      "is under that surface."),
            "aria_label": (f"{len(feats)} detected water level labels"
                           + (" in the current view" if box is not None else "")
                           + ". These are heights of the water surface, not depths."),
        }

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
        # THIS USED TO RETURN AN EMPTY FeatureCollection FOR A MISSING FILE, which
        # is the exact failure the rest of this section exists to prevent, sitting
        # in the oldest endpoint: a client cannot tell "this area has no mapped
        # waterway" from "the centreline was never downloaded", and one of those is
        # the reason the snapping is off and the estimator is running raw. The
        # PRESENT shape is unchanged — it is still the file's own GeoJSON, so
        # client/js/map.js's walk() keeps working — with `status` added as a
        # foreign member (RFC 7946 §6.1, ignored by MapLibre) so both answers can
        # be told apart by one key.
        p = settings.areas_dir / f"{name}.geojson"
        if not p.exists():
            return _absent(
                name, "centreline",
                why="no waterway centreline has been downloaded for this area",
                means=("nothing here knows where the channel runs, so the estimator "
                       "cannot snap to it and the map has no water drawn on it. That "
                       "is missing data, not a stretch with no canal in it"),
                remedy="re-create the area (the download fetches the OSM centreline), "
                       f"or {_FETCH_CMD.format(area=name)}")
        try:
            gj = _read_json(p)
        except Exception as exc:  # noqa: BLE001
            return _unreadable(name, "centreline", p, exc)
        gj["status"] = "present"
        gj["layer"] = "centreline"
        gj["area"] = name
        return gj

    # ---- CRT hazard overlays + the two depth layers (this round) --------------
    #
    # THE PATHS ARE THE CONSOLE'S, NOT THIS FILE'S PREFERENCE. client/js/crt.js
    # names them in one object at the top of itself (`/api/areas/{area}/crt`,
    # `/api/areas/{area}/crt/{layer}`, `/api/areas/{area}/depth/nominal`,
    # `/api/areas/{area}/depth/surveyed`) and its comment says the index may hand
    # back its own `path` per layer so the server can move an endpoint without the
    # client being edited in the same breath — so `url` travels on every row below
    # and these four names are what the console asks for today.
    #
    # THE INDEX IS THE GATE, and it is the reason `layers` is a flat array carrying
    # the ABSENT rows as well as the present ones. crt.js will not report any layer
    # absent until the index has answered, because a per-layer 404 also happens on
    # a Pi with no chart service at all, and "the file is not on the disk" and "I
    # could not ask anybody" are different claims. So this endpoint answering is
    # what earns the console the right to say ABSENT — which means it has to say
    # what is missing, not only what is here.

    @r.get("/api/areas/{name}/crt")
    async def area_crt_index(name: str):
        """What overlay data this area has, INCLUDING what it has not.

        A listing of the files present is the easy half and the useless one: the
        question before a dive is "did the hazard fetch run?", and only a listing
        that names what is missing can answer it.
        """
        if not _NAME_OK.match(name or ""):
            raise HTTPException(400, "bad area name")
        crt_block = _crt_layers(name)

        # ONE ARRAY, BOTH ANSWERS. Present layers and absent ones sit in the same
        # list with a `present` boolean, because the console binds its table to
        # whatever the wire calls a layer and then needs to know, per layer,
        # whether to draw it or to say ABSENT. Two arrays would make absence
        # something a client has to go and look for, and the whole point is that
        # it arrives unasked.
        rows = []
        for row in crt_block.get("layers") or []:
            rows.append({**row, "present": row.get("status") == "present",
                         "count": row.get("features")})
        for row in crt_block.get("skipped") or []:
            rows.append({**row, "present": False, "count": None,
                         "url": f"/api/areas/{name}/crt/{row['layer']}"})

        layer, err = nominal_layer(name)
        if err is not None:
            nominal_block = {"status": "unreadable", "present": False, "why": err,
                             "url": f"/api/areas/{name}/depth/nominal"}
        elif layer is None:
            nominal_block = {"status": "absent", "present": False,
                             "url": f"/api/areas/{name}/depth/nominal",
                             "why": "no waterway geometry is cached for this area",
                             "means": ("there is nothing to hang published depth "
                                       "guidance on — not that the water here is "
                                       "unguided, that nobody has downloaded it"),
                             "remedy": _FETCH_CMD.format(area=name)}
        else:
            nominal_block = {
                "status": "present", "present": True,
                "url": f"/api/areas/{name}/depth/nominal",
                "nominal": True, "measured": False, "is_survey": False,
                "count": layer["sections"],
                "sections": layer["sections"],
                "sections_with_guidance": layer["sections_with_guidance"],
                "sections_without_guidance": layer["sections_without_guidance"],
                "built_from": layer["built_from"], "title": layer["title"],
            }

        snd = _soundings_mod()
        sp = snd.store_path_for(name) if snd else None
        surveyed_url = f"/api/areas/{name}/depth/surveyed"
        if snd is None:
            sound_block = {"status": "absent", "present": False, "url": surveyed_url,
                           "why": "api/nav/soundings.py is not in this build",
                           "means": "nothing on this card can have recorded a sounding"}
        elif sp is not None and sp.exists():
            sound_block = {"status": "present", "present": True, "url": surveyed_url,
                           "file": str(sp), "bytes": sp.stat().st_size,
                           "measured": True, "nominal": False, "is_survey": True,
                           "quantity": getattr(snd, "QUANTITY", None),
                           "means": getattr(snd, "MEANS", None),
                           "unsurveyed": getattr(snd, "UNSURVEYED", None),
                           "datum": getattr(snd, "DATUM", None)}
        else:
            sound_block = {"status": "absent", "present": False, "url": surveyed_url,
                           "why": "no dive has contributed a sounding to this area yet",
                           "means": _unsurveyed_sentence(snd),
                           "remedy": _SOUND_CMD.format(area=name)}

        cl = settings.areas_dir / f"{name}.geojson"
        bank_block = _bank_block(name)
        # THE SUMMARY MUST COUNT WHAT IT COULD NOT READ. It used to add up the absent
        # rows only, so an area whose files were all present-and-corrupt was headlined
        # "Every hazard layer the fetch produced is here" — a certification issued by
        # a function that had opened none of them. Unreadable is counted separately
        # rather than folded in, because the two sentences send an operator to two
        # different places.
        n_absent = ((1 if crt_block["status"] != "present" else 0)
                    + len(crt_block.get("failed") or []))
        n_corrupt = len(crt_block.get("unreadable") or [])
        bits = ([f"{n_absent} hazard layer(s) are MISSING"] if n_absent else []) + \
               ([f"{n_corrupt} are on this card and UNREADABLE"] if n_corrupt else [])
        # WHICH CARD ANSWERED. "Every hazard layer is here" means something different
        # when the layers are national — it means they are here for everywhere, whether
        # or not this area was ever downloaded — and an operator reading a green line
        # about an area that does not exist yet is entitled to know which of the two
        # they are being told.
        held = ("held NATIONALLY on this handheld, so they cover this area and every "
                "other" if crt_block.get("scope") == "national" else
                "clipped to this area by an earlier fetch")
        summary = (" and ".join(bits) + " — absent is not empty, unreadable is not empty "
                   "either, and nothing here claims this water is clear."
                   if bits else
                   f"Every hazard layer the Trust publishes is here and every one of "
                   f"them was certified: {held}.")
        aria_summary = (" and ".join(bits) + "; no claim is made about the hazards they "
                        "would have shown."
                        if bits else
                        "All downloaded hazard layers are present and readable.")
        return {
            "area": name,
            "status": crt_block["status"],
            "fetched": crt_block.get("fetched"),
            "bbox": crt_block.get("bbox"),
            "clip_rule": crt_block.get("clip_rule"),
            "attribution": crt_block.get("attribution"),
            # Read first by client/js/crt.js — see the note above.
            "layers": rows,
            "failed": crt_block.get("failed") or [],
            # Its own key, beside `failed` and never inside it. A console that shows
            # these as one list tells the operator to download something they already
            # have.
            "unreadable": crt_block.get("unreadable") or [],
            "warnings": crt_block.get("warnings") or [],
            "why": crt_block.get("why"),
            "means": crt_block.get("means"),
            "remedy": crt_block.get("remedy"),
            "depth": {"nominal": nominal_block, "surveyed": sound_block},
            # THE LAUNCH-BANK ROW RIDES IN THIS INDEX FOR THE REASON EVERY OTHER ROW
            # DOES: this document is what earns the console the right to say a layer
            # is not there. It is the PER-AREA view — the console's own bank row reads
            # the area-less /api/bank, which is where the paint and the tile template
            # come from — and it is here so that "what does this area hold" has one
            # answer with nothing left out of it. The block is _bank_block's, unedited,
            # with the same `present` boolean every other row in this document carries.
            "bank": {**bank_block, "url": "/api/bank",
                     "present": bank_block["status"] == "present"},
            "centreline": {"status": "present" if cl.exists() else "absent",
                           "present": cl.exists(),
                           "url": f"/api/areas/{name}/centreline"},
            "title": f"Overlay data on this card for {name}. {summary}",
            "aria_label": f"Overlay layers for area {name}. {aria_summary}",
        }

    @r.get("/api/areas/{name}/crt/{layer}")
    async def area_crt_layer(name: str, layer: str):
        """One downloaded CRT layer, or a distinguishable answer about why it is not here."""
        if not _NAME_OK.match(name or "") or not _NAME_OK.match(layer or ""):
            raise HTTPException(400, "bad area or layer name")
        crt = _crt_mod()
        safe = crt.safe_area_name(name) if crt else None
        remedy = _FETCH_CMD.format(area=name)
        if crt is None or safe is None:
            return _absent(name, layer,
                           why="no CRT layer directory exists for this area",
                           means="nothing has been downloaded about hazards here",
                           remedy=remedy)
        p = crt.area_dir(safe) / f"{layer}.geojson"
        if not p.exists():
            # NO CLIP FOR THIS AREA — WHICH IS NOT THE SAME AS NOT HAVING THE LAYER.
            # The Trust's vectors are national now and an area's cut-down copy is an
            # optimisation for drawing; if the whole layer is on this handheld, that is
            # the honest answer to "give me this layer", and it is redirected rather
            # than copied so there is one file and one set of bytes. An operator whose
            # console asked for a hazard layer must never be told ABSENT while it sits
            # in the national card three directories away.
            if (crt.national_dir() / f"{layer}.geojson").exists():
                return RedirectResponse(f"/api/crt/{layer}", status_code=307)
            # WHY it is not there, when the fetch's own record can say. A layer that
            # was skipped on purpose and a layer whose fetch failed part-way are
            # different facts, and the second is the one worth interrupting somebody
            # over: crt.py deliberately writes NO file for a partial fetch, so the
            # only trace it leaves is this record.
            index = _crt_index(name) or {}
            for rec in index.get("skipped") or []:
                if rec.get("layer_key") != layer:
                    continue
                deliberate = rec.get("skipped") in _DELIBERATE_SKIPS
                return _absent(
                    name, layer,
                    why=f"the fetch skipped it ({rec.get('skipped')}): {rec.get('why')}",
                    means=("left out on purpose, and nothing was lost" if deliberate else
                           "the fetch could not complete this layer and wrote no file "
                           "rather than a partial one, because a truncated hazard "
                           "layer is indistinguishable from an empty canal. Nothing "
                           "is known about this kind of hazard here"),
                    remedy=remedy)
            return _absent(
                name, layer,
                why=("this area has no layer by that name — "
                     + ("the fetch has never run here" if not index else
                        "the fetch ran and produced no such layer")),
                means="nothing is known about hazards of this kind in this area",
                remedy=remedy)
        try:
            gj = _read_json(p)
        except Exception as exc:  # noqa: BLE001
            return _unreadable(name, layer, p, exc)
        if not _is_layer_doc(gj):
            # THE SAME LIE, ONE LEVEL DOWN. A file that parses and is not a collection
            # fell through here with status "present" and a title reading "0
            # feature(s) … an empty result, the fetch ran cleanly and there is nothing
            # of this kind here" — over a service error body somebody's fetch saved
            # under a layer name. The index calls that unreadable (see _read_layer)
            # and the two must not disagree about the same file.
            return _unreadable(name, layer, p, ValueError(_NOT_A_LAYER))
        # Parsed and re-emitted rather than streamed as bytes so the status marker
        # rides in the body where the client already looks. crt.py has already put
        # `attribution` and `clip` on the collection; per-file provenance sits
        # beside it and is folded in here so one request answers "what is this and
        # where did it come from" — the alternative is a console that draws hazards
        # it cannot attribute.
        gj["status"] = "present"
        gj["layer"] = layer
        gj["area"] = name
        prov = p.with_suffix(".prov.json")
        if prov.exists():
            try:
                gj["provenance"] = _read_json(prov)
            except Exception:  # noqa: BLE001 — the layer still stands without it
                gj["provenance"] = None
        n = len(gj.get("features") or [])
        gj["title"] = (f"{layer} for {name}: {n} feature(s) downloaded from the Canal & "
                       f"River Trust and clipped to this area."
                       + (" An empty result — the fetch ran cleanly and there is nothing "
                          "of this kind here, which is a survey result and not a gap."
                          if n == 0 else ""))
        gj["aria_label"] = (f"CRT layer {layer} for area {name}, {n} features. "
                            f"Downloaded data, not a survey by this vehicle.")
        return gj

    @r.get("/api/areas/{name}/depth/nominal")
    async def area_depth_nominal(name: str):
        """Published depth GUIDANCE over the water. Never a survey, and says so five ways."""
        if not _NAME_OK.match(name or ""):
            raise HTTPException(400, "bad area name")
        layer, err = nominal_layer(name)
        if err is not None:
            return _unreadable(name, "depth/nominal",
                               settings.areas_dir / f"{name}.geojson", ValueError(err))
        if layer is None:
            return _absent(
                name, "depth/nominal",
                why="no waterway geometry is cached for this area at all",
                means=("there is nothing to hang published depth guidance on. This is "
                       "not a stretch of canal with no published depth — it is a "
                       "stretch nobody has downloaded"),
                remedy=_FETCH_CMD.format(area=name))
        return layer

    @r.get("/api/areas/{name}/depth/surveyed")
    async def area_depth_surveyed(name: str):
        """The depths this hull has actually stood on, as GeoJSON. LOWER BOUNDS.

        Served beside the nominal layer and never merged into it. One is what the
        Trust publishes about a class of canal; the other is where this vehicle
        came to rest. They are drawn separately for the same reason they are stored
        separately — an estimate must never dress as a measurement, and a
        measurement must not be diluted by an estimate averaged into it.

        THE STORE IS NOT GeoJSON and this is where it becomes some. That conversion
        lives here rather than in nav/soundings.py on purpose: the store's shape is
        an accumulator with a per-dive breakdown inside every cell, which is what
        makes re-running a dive idempotent, and flattening it for a renderer is a
        serving concern. What must NOT be lost on the way through is the claim
        attached to the number, so `bound`, `measured` and the store's own sentence
        about what an absent cell means ride out with it.
        """
        if not _NAME_OK.match(name or ""):
            raise HTTPException(400, "bad area name")
        snd = _soundings_mod()
        if snd is None:
            return _absent(name, "depth/surveyed",
                           why="api/nav/soundings.py is not in this build",
                           means="nothing on this card can have recorded a sounding",
                           remedy="")
        p = snd.store_path_for(name)
        if not p.exists():
            return _absent(
                name, "depth/surveyed",
                why="no dive has contributed a sounding to this area yet",
                means=_unsurveyed_sentence(snd),
                remedy=_SOUND_CMD.format(area=name))
        try:
            store = _read_json(p)
        except Exception as exc:  # noqa: BLE001
            return _unreadable(name, "depth/surveyed", p, exc)
        return _surveyed_collection(name, store, snd)

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
            # The catch-up push so a map that just connected is not blank until the
            # next tick. It uses the SAME frame shape as the live broadcasts, so a
            # stale one is drawn as a live fix and nothing on screen says otherwise;
            # if nav is not currently producing, send nothing and let the map stay
            # empty until it is.
            ns = svc.fresh_state()
            if ns:
                await ws.send_text(json.dumps(svc.nav_frame(ns)))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            svc._subs.discard(ws)

    return r

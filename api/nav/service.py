"""Navigation service (spec §10.1) — sensor ingest, dead reckoning, snapping,
dive logging — over REST + a nav WebSocket. Plus the area manager (§10.2) and the
readiness check (§9). Mounts into the existing FastAPI app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode as _urlencode

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

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
        crt_block = _crt_layers(self.active_area or "")
        failed = crt_block.get("failed") or []
        corrupt = crt_block.get("unreadable") or []
        if not self.active_area:
            hz_ok, hz_why = False, "no area is activated, so no hazard layer can be checked"
        elif crt_block["status"] != "present":
            hz_ok, hz_why = False, (crt_block.get("why", "not fetched")
                                    + " — an absent hazard layer is NOT a clear channel")
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
            hz_why = (f"{n} layer(s) cached and PARSED (not merely present on disk), "
                      f"fetched {crt_block.get('fetched')}; "
                      f"{len(crt_block.get('skipped') or [])} skipped on purpose")
        add("CRT hazard layers cached AND readable (absent is not 'no hazards', "
            "and neither is corrupt)", hz_ok, hz_why)
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


def _crt_layers(area: str) -> dict:
    """Everything the CRT fetch did for this area: what landed, and what did not."""
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

def build_router(svc: NavService) -> APIRouter:
    r = APIRouter()

    @r.post("/api/origin")
    async def set_origin(o: Origin, override: bool = False):
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
        # heading0_measured travels WITH the origin, because the number cannot say
        # whether anything took it: 0.0 is due north and 284.0 is a bearing, and both
        # are perfectly plausible readings for a compass that was never asked.
        return {"ok": True, "origin": o.model_dump(), "heading0_measured": measured is not None}

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
        summary = (" and ".join(bits) + " — absent is not empty, unreadable is not empty "
                   "either, and nothing here claims this water is clear."
                   if bits else
                   "Every hazard layer the fetch produced is here, and every one of them "
                   "was read.")
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

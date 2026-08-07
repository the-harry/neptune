"""Navigation service (spec §10.1) — sensor ingest, dead reckoning, snapping,
dive logging — over REST + a nav WebSocket. Plus the area manager (§10.2) and the
readiness check (§9). Mounts into the existing FastAPI app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode as _urlencode

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import areas as areamod
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
        p = settings.areas_dir / f"{name}.geojson"
        if not p.exists():
            return {"type": "FeatureCollection", "features": []}
        return json.loads(p.read_text())

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

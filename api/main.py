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

import sysinfo
from rov_camera import get_camera, mjpeg_stream
from camera.app import create_camera_service
from camera.service import build_router as build_camera_router
from nav.app import create_nav_service
from nav.service import build_router as build_nav_router
from config import settings
from hardware import get_hardware
from protocol import COMMAND_NAMES, Ack, Alarm, Pong, Telemetry, parse_inbound
from rov import RovState, cardinal
from blackbox import BlackBox, build_router as build_blackbox_router

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


def battery_band(v: float | None) -> str:
    """2S pack band — "ok" | "warn" | "critical" | "unknown". One colour, one meaning (§5).

    Recorded alongside the raw voltage rather than derived at analysis time: the
    thresholds live in config and can be retuned, and a dive log that only carried
    volts would be re-scored against whatever the thresholds happened to be years
    later. The band is what the operator was actually shown, so the band is logged.

    "unknown" IS A BAND, and adding it is what let battery_v become nullable. This
    runs on the control loop's own path — twice per journal record — with nothing
    above it catching anything, so `None < 6.6` here would have raised inside the
    journal and taken telemetry, the watchdog and the blackbox down together. That
    is why rov.py used to substitute 0.0 for a dead INA219, and 0.0 V bands as
    "critical": the console showed a red BATTERY 0.0V · SURFACE on a vehicle with a
    full pack, a critical alarm invented entirely by an absent sensor.

    So it is TOTAL: every input has an answer and no input has an exception. A
    reading that is not a finite number is not a voltage, and the honest band for a
    voltage nobody could take is the one that says so — never "ok" (which would
    certify a pack nothing is measuring) and never "critical" (which would cry wolf
    with the alarm that must never be distrusted). Everything it needs is a
    parameter or `settings`, deliberately: tests/test_telemetry.py compiles this
    one function out of the source to check the 2S thresholds against the shipped
    rule, and a reference to any other module-level name here would break that.
    """
    if v is None:
        return "unknown"
    try:
        if v != v:                      # NaN — arithmetic on a failed read, not a reading
            return "unknown"
        if v < settings.battery_crit_v:
            return "critical"
        if v < settings.battery_warn_v:
            return "warn"
    except TypeError:                   # not a number at all; still not a crash
        return "unknown"
    return "ok"


class TelemetryJournal:
    """Writes telemetry VALUES into the blackbox, not merely frame counts.

    The `tlm_tx` seq ranges prove a frame left the Pi; they say nothing whatever
    about what was in it, so until now a session could be audited for lost packets
    and for nothing else. A dive you cannot replay is a dive you cannot learn from.

    Two triggers, because the two kinds of question are different:
      * every change of a DISCRETE state — armed, leak stage, probe fault, ballast
        homed / needs-rehome, speed source, snagged, gyro-only, mag_cal, battery
        band, and WHICH CHIPS ARE NOT ANSWERING. The transitions ARE the story and
        must never be averaged away. A sensor dying is the sharpest transition the
        vehicle has: every reading behind it goes null in the same instant, and a
        log that recorded only the nulls would show a gauge that simply stopped —
        which is what the end of a dive looks like too. sensor_faults names the
        chip, so the record says "bno085 stopped at t=412 s" instead of "heading
        went blank around then".
      * a heartbeat every `period_s` for the continuous numbers, so a long steady
        stretch still has depth/heading/volts/amps to plot.

    Writing every field at telemetry_hz instead would be ~15 fat JSON lines per
    second: it fills the card, it burns flash write cycles, and it buries the six
    transitions that mattered under nine thousand identical ones.

    `min_gap_s` bounds a flapping signal (gyro_only hinges on thrust crossing 0.5,
    which an operator can hold right on the boundary). Changes inside the gap are
    coalesced, not dropped silently: the next record carries `n_changes` so the log
    admits how many intermediate states it collapsed.

    NAVIGATION'S OWN STATE IS ONE OF THE DISCRETE ONES. The moment nav stops
    answering, every nav number in the frame goes to cannot-tell — and a log that
    only carried the numbers would show them simply ceasing, which is the same
    thing a finished dive looks like. The transition that matters ("the loop died
    at t=412 s with an OSError off the I2C bus") is nav's state, not the numbers,
    so the loop state is journalled beside them and its change triggers a record.
    """

    def __init__(self, period_s: float = 2.0, min_gap_s: float = 0.25) -> None:
        self.period_s = period_s
        self.min_gap_s = min_gap_s
        self._pending: tuple | None = None
        self._dirty = True          # the first tick always emits: a log needs a baseline
        self._changes = 0
        self._last_emit = -1e9      # so both triggers are due immediately at startup

    @staticmethod
    def _discrete(t: Telemetry, nav: dict | None) -> tuple:
        # tick_faults is deliberately NOT in here: it counts up while a bus is
        # down, and a key that changes every tick would emit a record every tick
        # and bury the transitions this whole class exists to preserve.
        # sensor_faults is SORTED into the key: the hardware layer is free to
        # report its chips in any order, and a reordering that emitted a "state
        # change" record would be the same noise tick_faults was kept out for.
        return (t.armed, t.leak_state, t.leak_probe_fault, t.ballast_homed,
                t.ballast_needs_rehome, t.speed_src, t.snagged, t.gyro_only,
                t.mag_cal, battery_band(t.battery_v),
                tuple(sorted(t.sensor_faults or ())),
                None if nav is None else (nav["loop"], nav["answering"],
                                          nav["reads_vehicle"], nav["used"]))

    def record(self, bb: BlackBox, tel: Telemetry, now: float, nav: dict | None = None) -> None:
        key = self._discrete(tel, nav)
        if key != self._pending:
            self._pending = key
            self._dirty = True
            self._changes += 1
        since = now - self._last_emit
        if self._dirty and since >= self.min_gap_s:
            self._emit(bb, tel, "tlm_state", now, nav)
        elif since >= self.period_s:
            self._emit(bb, tel, "tlm", now, nav)

    def _emit(self, bb: BlackBox, tel: Telemetry, event: str, now: float,
              nav: dict | None = None) -> None:
        d = {
            "seq": tel.seq, "armed": tel.armed, "mock": tel.mock,
            "left": tel.left, "right": tel.right,
            "depth": tel.depth, "pressure": tel.pressure, "heading": tel.heading,
            # None survives into the log as JSON null and is NEVER stripped or
            # defaulted: "the stepper was never homed" is a finding, and a replay
            # that silently read it as 0.0 would exonerate the exact fault that
            # left the sub on the bottom.
            "ballast_level": tel.ballast_level, "ballast_target": tel.ballast_target,
            "ballast_homed": tel.ballast_homed, "ballast_needs_rehome": tel.ballast_needs_rehome,
            "leak": tel.leak, "leak_state": tel.leak_state,
            "leak_probe_fault": tel.leak_probe_fault,
            "battery_v": tel.battery_v, "battery_band": battery_band(tel.battery_v),
            "current_a": tel.current_a,
            # THE IMU'S OWN CHANNELS, ON THE HEARTBEAT AND DELIBERATELY NOT IN THE
            # DISCRETE KEY. They are continuous floats off a chip that answers at
            # telemetry rate, so keying a record on them would emit a record every
            # single tick and bury the six transitions this class exists to
            # preserve — the same reason tick_faults is kept out of _discrete.
            # Worth recording at all because they are what explains the shape of a
            # dive afterwards: a roll trace says the hull heeled over before the
            # snag, and a yaw rate beside a frozen bearing is the difference
            # between "the compass died" and "the sub genuinely stopped turning".
            # They are already null whenever mag_cal and heading are, so a replay
            # reads one BNO085 death rather than six unrelated gauges stopping.
            "gyro_z_dps": tel.gyro_z_dps, "accel_fwd_ms2": tel.accel_fwd_ms2,
            "pitch_deg": tel.pitch_deg, "roll_deg": tel.roll_deg,
            "speed_ms": tel.speed_ms, "speed_src": tel.speed_src,
            "snagged": tel.snagged, "gyro_only": tel.gyro_only, "mag_cal": tel.mag_cal,
            # WHICH CHIP, beside the nulls it caused. Without it a replay can see
            # that depth stopped and cannot see why — and "the MS5837 dropped off
            # the bus at 4.33 m" is the finding, while "depth went null" is only
            # the symptom. Copied to a list because the hardware hands back a tuple
            # and the journal is JSON.
            "sensor_faults": list(tel.sensor_faults or ()),
            "magnet": tel.magnet, "light_green": tel.light_green, "light_white": tel.light_white,
            "signal": tel.signal, "link_ms": tel.link_ms,
        }
        if nav is not None:
            # nav_answering is navigation's own claim; nav_used is what this frame
            # actually took from it. They differ exactly when nav is answering
            # about something that is not this hull (a scripted source against a
            # real vehicle), and the gap between them is the finding.
            d.update({"nav_loop": nav["loop"], "nav_answering": nav["answering"],
                      "nav_used": nav["used"], "nav_reads_vehicle": nav["reads_vehicle"],
                      "nav_faults": nav["tick_faults"]})
        if self._changes > 1:
            d["n_changes"] = self._changes
        bb.event(event, d)
        self._last_emit = now
        self._dirty = False
        self._changes = 0


def fill_nav_fields(app: FastAPI, tel: Telemetry) -> dict | None:
    """Stitch the estimator's outputs into the telemetry frame — the ONE place this happens.

    Returns navigation's account of itself (NavService.health(), plus has_origin)
    so the caller can log the transition, or None when this process has no
    navigation at all.

    THE HEADING PRECEDENCE RULE IS IN THE BODY, beside the two lines that implement
    it. It is the one thing in this file that decides which of two subsystems gets
    to speak about a number both of them have an opinion on, and a rule stated in a
    docstring drifts away from the code under it; stated on top of the code, it
    cannot.

    speed / speed_src / snagged / gyro_only are navigation's answers, not the
    hardware's: the paddlewheel reports an unsigned magnitude and the filter alone
    decides what it means, whether to believe it, and whether the sub is pinned. So
    rov.py leaves them None rather than reaching into the nav service, and it is
    done here instead: the control plane holding a reference into navigation would
    let a nav fault reach the thruster loop, and the two subsystems already start
    and fail independently (see the lifespan) — this keeps them that way.

    A STATE THAT EXISTS IS NOT A STATE THAT IS TRUE. `NavService.last_state` is an
    attribute that keeps its final value forever, so a finished dive or a nav loop
    that died on an exception used to leave speed / speed_src / snagged / gyro_only
    frozen at their last values and broadcast as live readings at telemetry_hz — a
    dive that ended snagged reported snagged=true until the process restarted. So
    nothing is copied out of a state this cannot prove is CURRENT: svc.fresh_state()
    answers None once the state is older than a few nav loop periods.

    NOT ANSWERING IS ITSELF A REPORTABLE STATE, WHICH IS THE FIX IN THIS ROUND.
    Leaving the fields alone when nav goes quiet was not neutral, because their
    defaults are not neutral: snagged=False and gyro_only=False are the two
    reassuring answers. So at the exact instant navigation died the console got
    QUIETER — a standing snag warning cleared itself, the GYRO badge went out, and
    the bearing silently swapped the estimator's heading for the raw compass with
    nothing on screen marking the change. A subsystem's death must never look like
    good news. False now means "nav looked and says no"; None means "nav cannot
    tell", and it covers every reason at once — not started, no origin, between
    dives, sensor bus down, loop dead. Which of those it is travels in the
    blackbox record and out of /api/nav/health, because a null on its own says
    nothing about what to go and fix.

    AND AN ESTIMATE THAT NEVER LOOKED AT THIS HULL IS NOT ABOUT THIS HULL. With
    NAV_SENSORS=sim the estimator is fed a scripted path — canned heading legs that
    ignore the operator entirely — and it will happily produce a confident NavState
    while a real sub is in the water. Stamping that heading, speed and snag state
    onto a real hull's frame put simulated data on screen under mock=false, which
    is the one thing this project does not allow anywhere. svc.reads_vehicle is
    false for exactly that source, and then nav's answers do not enter this frame
    at all: the raw compass rov.py measured stands, and the estimator's answers
    stay where they belong — on /ws/nav, whose frames now carry `simulated` and
    `reads_vehicle` so the map can say what it is drawing.
    """
    svc = getattr(app.state, "nav_svc", None)
    if svc is None:
        tel.snagged = None
        tel.gyro_only = None
        return None
    nav = svc.health()
    # Whether navigation has anything to estimate FROM. health() cannot say, and
    # the difference is the whole of finding 4: with no origin the loop is healthy
    # and simply has nothing to report, which is the state of every vehicle at
    # every boot. Read here so the control loop can log a quiet start as a quiet
    # start instead of as a fault.
    nav["has_origin"] = getattr(svc, "origin", None) is not None
    # NAVIGATION THAT HAS NOT STARTED YET IS NOT NAVIGATION THAT DIED, and nothing
    # else in this frame can tell the two apart — loop_state() says "never-started"
    # for both. The control-loop task is created BEFORE the subsystems are started
    # (deliberately: a slow camera must not delay the watchdog), so the first
    # telemetry ticks of EVERY boot see a nav service that exists and has no loop
    # yet. `subsystems_up` is set by the lifespan once the gather has returned, so
    # from that moment on "never-started" means navigation FAILED to start, which is
    # a fault and is warned about. Read with a default because tests/test_liveness.py
    # compiles this function out of the source and runs it against a bare app stub.
    nav["starting"] = (nav["loop"] == "never-started"
                       and not getattr(app.state, "subsystems_up", False))
    # Asked ONCE and acted on once. A second fresh_state() call could age out
    # between the two and leave the log claiming nav answered a frame it did not —
    # a small lie, but of precisely the kind being hunted here.
    ns = svc.fresh_state() if nav["reads_vehicle"] else None
    nav["used"] = ns is not None
    if ns is None:
        tel.snagged = None
        tel.gyro_only = None
        # WHY THERE IS NO SPEED, NOT MERELY THAT THERE IS NONE. Before an origin is
        # set — the whole pre-dive phase of every single boot — a completely healthy
        # hull sends speed_ms=null speed_src=null, and the console explains that null
        # with "nothing is reporting a speed at all - no paddlewheel pulses and no
        # estimate" (client/js/render.js). That accuses a sensor which is working
        # perfectly. Nothing is wrong with the paddlewheel: navigation has no datum
        # to estimate from yet, and it cannot have one until someone sets an origin.
        #
        # speed_src is the field that says where a speed came from, so it is the
        # field that gets to say why there is none. speed_ms stays null — there
        # genuinely is no speed — so the readout is still "--" / NO SPEED and this
        # only changes the reason attached to it. Narrow on purpose: a stalled loop,
        # a dead sensor bus or a finished dive are NOT this state, they are faults or
        # ends, and each already travels under its own null. It also lands in the
        # blackbox (TelemetryJournal keys on speed_src), so a replay can see the
        # exact tick where the vehicle went from "no datum" to estimating.
        if (nav["loop"] == "running" and not nav["has_origin"]
                and nav["last_fault"] is None and not nav["starting"]):
            tel.speed_src = "no-origin"
        return nav
    # Nothing from the estimator is coerced on the way in. round(None) is a
    # TypeError, and the tempting `ns.speed_ms or 0.0` would put a confident
    # "0.0 m/s — stopped" on screen for a sub nothing can measure. An estimator
    # that has no number for a field sends that field's null straight through.
    tel.speed_ms = None if ns.speed_ms is None else round(ns.speed_ms, 3)
    tel.speed_src = ns.speed_src
    tel.snagged = ns.snagged
    tel.gyro_only = ns.gyro_only
    # ---- HEADING PRECEDENCE. Stated here, implemented exactly, nowhere else. ----
    #
    #   1. The estimator's heading REPLACES the compass reading iff BOTH exist.
    #   2. A null NEVER overwrites a number. Navigation going quiet, or unable to
    #      say, must not blank a compass that is still answering.
    #   3. A number NEVER overwrites a null. If the BNO085 is not answering there
    #      is no bearing to refine, and anything still coming out of the estimator
    #      is coasting on an input that has stopped.
    #   4. heading_card is recomputed from whatever heading survives, always, so
    #      the letter can never outlive or contradict the number it restates.
    #
    # ONE heading on screen, from ONE source, is why rule 1 prefers the estimator.
    # The map draws NavState.heading_deg, so the HUD has to carry that same number:
    # under NAV_FILTER=filtered the estimator's heading comes from the
    # complementary filter, and the raw compass rov.py stamped is out by the whole
    # magnetic error the thrusters induce (the sim models 22° at full throttle).
    # Two headings on one screen disagreeing by 22° is bad on its own; the trust
    # marks make it worse. The HUD's GYRO / MAG? badges are drawn from gyro_only
    # and mag_cal, which describe what the FILTER is doing — hang them on the raw
    # compass and they annotate a number the filter is not producing. Under the
    # default "dr" backend the two values are identical (the dead reckoner passes
    # s.heading_deg straight through), so this only bites in the mode that needs it.
    #
    # RULE 3 IS THE ONE THIS ROUND EXISTS FOR, and the stamp used to be
    # unconditional. api/nav/sensors.py handed every cannot-tell a default that was
    # itself a measurement — `read_heading()` fell back to 0.0 — so a dead compass
    # became a confident bearing one layer below here and this line stamped it over
    # the null rov.py had correctly sent. Reproduced end to end: rov.py sent
    # heading=None card=None mag_cal=None faults=['bno085'] and the client received
    # heading=0.0 card='N' — DUE NORTH beside a NO COMPASS badge and a "bno085 not
    # answering" fault, on one screen. The radar is heading-up, so the map swung
    # north and the dead reckoner ran the track north with it. That is worse than
    # the frozen bearing this round set out to fix: a frozen bearing at least
    # started life as a measurement.
    #
    # The guard stays whatever nav does next. Even once nav stops coercing, "the
    # compass is silent" is a verdict this frame already carries from the hardware
    # layer, and it is not navigation's to overturn.
    est = getattr(ns, "heading_deg", None)
    if est is not None and tel.heading is not None:
        tel.heading = round(est % 360.0, 1)
    tel.heading_card = cardinal(tel.heading)
    if tel.heading is None:
        # No bearing survived, so the mark that qualifies one has nothing left to
        # qualify. "Coasting on the gyro, on purpose" printed beside a blank
        # bearing reads as a heading the operator simply cannot see — same reason
        # cardinal(None) is None rather than "N". mag_cal is already null here: it
        # comes off the same chip as the heading and rov.py nulls them together.
        tel.gyro_only = None
    return nav


def log_nav_change(nav: dict, ever_answered: bool) -> None:
    """Say what navigation just did — at the level the situation actually deserves.

    A WARNING THAT FIRES ON EVERY HEALTHY START IS A WARNING NOBODY READS, and that
    is what this used to be. The old line warned on any frame navigation did not
    contribute to, and before an origin is set navigation contributes to nothing:
    the loop is turning, the sensors are fine, there is simply no fix to estimate
    from yet. So a completely healthy vehicle with a completely healthy nav loop
    printed "navigation is not answering for this hull" at every boot, every time,
    and the day it means something it will look exactly like the day it did not.

    Two situations, and the operator does something different in each:

      * NOTHING TO SAY YET — the loop is running, no tick has ever failed, and it
        has never produced a state. Ordinary; INFO, and it names the reason so the
        reader is not left to infer it.
      * STOPPED ANSWERING — the loop is stopped, stalled or never started, OR a
        tick has raised, OR navigation was answering earlier in this session and
        has gone quiet. Each of those is a fault someone must go and fix, and each
        of them means every nav field topside is now cannot-tell; WARNING.

    `ever_answered` is what separates the two when the loop still looks healthy: a
    subsystem that answered and stopped is broken, whatever its loop says about
    itself, and without that memory a dead sensor bus with no origin set would
    read as a quiet start forever.

    AND `nav["starting"]` IS WHAT SEPARATES THEM AT BOOT — because the warning this
    function was written to stop firing on every healthy start was re-introduced by
    the fix for it. The control loop is created before the subsystems are started, so
    its first ticks see loop="never-started", which fell straight into `stopped`
    below: every clean boot of a completely healthy vehicle opened its log with
    "navigation has STOPPED answering for this hull ... loop=never-started faults=0
    last_fault=None end_reason=None", measured on a live server. A warning that fires
    on every healthy start is a warning nobody reads, and the day it means something
    it will look exactly like the day it did not. Once the lifespan has finished
    starting the subsystems, `starting` is False forever and a still-never-started
    loop is warned about properly — it is a nav service that failed to come up.
    """
    if nav["used"]:
        if ever_answered:
            log.info("navigation is answering again (loop=%s source=%s)",
                     nav["loop"], nav["source"])
        return
    if not nav["reads_vehicle"]:
        # Answering, but about a scripted path rather than this hull, so its
        # answers are deliberately kept out of the frame. Not a fault in
        # navigation and not something waiting for an origin — a configuration
        # that would put simulated numbers on a real vehicle's console.
        log.warning("navigation is not reading THIS hull (source=%s simulated=%s) — its "
                    "speed, snag and heading stay on /ws/nav and the frame keeps the raw "
                    "compass. Set NAV_SENSORS=vehicle to bind it to the sub.",
                    nav["source"], nav["simulated"])
        return
    starting = bool(nav.get("starting"))
    stopped = not starting and (nav["loop"] != "running" or nav["last_fault"] is not None
                                or ever_answered)
    if stopped:
        log.warning("navigation has STOPPED answering for this hull — speed, snag and "
                    "heading-trust go to cannot-tell (loop=%s answering=%s reads_vehicle=%s "
                    "source=%s faults=%d last_fault=%s end_reason=%s)",
                    nav["loop"], nav["answering"], nav["reads_vehicle"], nav["source"],
                    nav["tick_faults"], nav["last_fault"], nav["loop_end_reason"])
    else:
        # The REASON is what makes this line worth printing, so it is named rather
        # than left to be inferred from loop= and origin=. The three quiet states are
        # not interchangeable: one resolves itself in milliseconds, one waits on the
        # operator setting a datum, and one is nav turning with nothing yet to say.
        log.info("navigation has nothing to say yet — %s (loop=%s source=%s). Speed, "
                 "snag and heading-trust read cannot-tell until it does.",
                 "the service has not finished starting" if starting
                 else "the loop is running and healthy, there is simply no origin to "
                      "estimate from — set a fix" if not nav.get("has_origin")
                 else "the loop is running and healthy and has not produced a state yet",
                 nav["loop"], nav["source"])


async def _control_loop(app: FastAPI) -> None:
    """Advance sim, run the watchdog, refresh metrics, broadcast telemetry."""
    rov: RovState = app.state.rov
    mgr: ConnectionManager = app.state.manager
    bb: BlackBox = app.state.bb
    journal = TelemetryJournal()
    period = 1.0 / max(1.0, settings.telemetry_hz)
    metrics_cache = sysinfo.telemetry_fields()
    last_metrics = time.monotonic()
    last = time.monotonic()
    seq = 0
    tx_from = None                    # telemetry seq-range accumulator (§4 — log ranges, not every frame)
    last_nav_key = None               # nav's last reported state, for edge-logging below
    nav_ever_answered = False         # has navigation EVER contributed to a frame this session
    nav_fail_logged = -1e9            # last time the nav-stitch exception was logged (see below)
    nav_fail_n = 0                    # how many it has collapsed since
    log.info("control loop @ %.0f Hz (watchdog %.2fs)", settings.telemetry_hz, settings.watchdog_timeout_s)
    while True:
        now = time.monotonic()
        dt = now - last
        last = now

        rov.update(dt)
        rov.watchdog(now)

        if now - last_metrics >= settings.metrics_period_s:
            # Pure /proc + /sys reads — microseconds, safe on the loop. The slow
            # probes (vcgencmd, systemctl, iw) run on their own background task.
            try:
                metrics_cache = sysinfo.telemetry_fields()
            except Exception as exc:  # noqa: BLE001 — health must never stop telemetry
                log.warning("sysinfo failed: %s", exc)
            last_metrics = now

        # Telemetry is built EVERY tick now, not only when someone is watching. The
        # blackbox exists to explain the dive that went wrong, and the dive that goes
        # wrong is very often the one where the tether dropped and there was no client
        # left to broadcast to — recording only while a client is attached loses
        # exactly the minutes you came to read. Broadcasting still depends on
        # mgr.count; recording and the leak edge machine no longer do.
        tel = rov.telemetry(metrics_cache)
        tel.t = round(bb.now_ms(), 3)
        try:
            nav = fill_nav_fields(app, tel)
        except Exception as exc:  # noqa: BLE001
            # A NAV FAULT MUST NOT REACH THE THRUSTER LOOP. That is the whole
            # reason rov.py has no handle on navigation and the stitching happens
            # out here — and it would be undone by letting an exception from the
            # nav side unwind this loop, which runs the watchdog. The frame goes
            # out with navigation's fields at cannot-tell, which is exactly what
            # they mean.
            #
            # RATE-LIMITED for the same reason nav's own tick faults are (see
            # NavService._note_fault): whatever raises here raises on EVERY tick,
            # so a level-triggered line is fifteen identical warnings a second and
            # the first one — the only one carrying information — is buried inside
            # a minute. The count says how many were collapsed, so nothing is
            # silently swallowed.
            nav_fail_n += 1
            if now - nav_fail_logged >= 10.0:
                log.warning("nav fields could not be filled (%d time(s) since the last "
                            "line) — speed, snag and heading-trust go to cannot-tell: %s",
                            nav_fail_n, exc, exc_info=True)
                nav_fail_logged, nav_fail_n = now, 0
            tel.snagged = None
            tel.gyro_only = None
            nav = None
        # SAY IT ONCE, AT THE EDGE. Navigation stopping is an event, and an event
        # that only shows up as fields going null is an event somebody has to
        # notice the absence of. Logged on the TRANSITION rather than every tick:
        # at telemetry_hz a level-triggered line would be fifteen a second, which
        # is the same as not logging it.
        #
        # last_fault is in the key as a BOOLEAN, not as a count: the count climbs
        # on every tick of a dead I2C bus and would re-fire this line at
        # telemetry_hz, but the first fault arriving is a genuine edge — it is what
        # turns "nothing to say yet" into "stopped answering" on a loop that still
        # reports itself as running.
        #
        # `starting` is in the key too, and it has to be: it is the only thing that
        # changes when a navigation service FAILS to start. Without it the boot's
        # "not finished starting yet" INFO would be the first and last word on a nav
        # loop that never appeared — the key would never move again, so the warning
        # that ought to follow would never fire. It cannot double-log a healthy boot
        # either, because it is only ever true while loop is "never-started" (see
        # fill_nav_fields), so it goes false in the same transition that loop does.
        nav_key = None if nav is None else (nav["loop"], nav["used"], nav["reads_vehicle"],
                                            nav["last_fault"] is not None, nav["starting"])
        if nav_key != last_nav_key:
            if nav is not None:
                log_nav_change(nav, nav_ever_answered)
            last_nav_key = nav_key
        # Set AFTER the log, so "answering again" means what it says, and never
        # cleared: a subsystem that answered once and then went quiet is broken for
        # the rest of the session, however healthy its own loop looks.
        if nav is not None and nav["used"]:
            nav_ever_answered = True
        if mgr.count:
            # seq counts BROADCAST frames only, and is stamped here so the journal
            # below records the same number the client will see. Numbering unsent
            # frames would open a gap in the client's sequence for every second it
            # was disconnected and its gap detector would call that packet loss; a
            # journalled record with seq=null instead says plainly "nobody was
            # listening when this was recorded".
            seq += 1
            tel.seq = seq

        # Two-stage, edge-triggered leak alarm (§5). WARN and FLOOD are separate
        # alarms because the client draws them differently — advisory vs surface
        # prompt — and the edge logic lives in RovState, which owns the state.
        # Logged before broadcasting: an alarm raised into a dead socket still
        # happened, and the log is the only place that will remember it.
        for name in rov.leak_alarm_edges(tel.leak_state):
            log.warning("ALARM %s (leak_state=%s, probe_fault=%s)",
                        name, tel.leak_state, tel.leak_probe_fault)
            bb.event("alarm", {"name": name, "leak_state": tel.leak_state,
                               "probe_fault": tel.leak_probe_fault, "depth": tel.depth})
            await mgr.broadcast(Alarm(name=name).model_dump_json())

        journal.record(bb, tel, now, nav)

        if mgr.count:
            await mgr.broadcast(tel.model_dump_json())
            # record what we SENT as compact ranges so `rovlog diverge` can compare
            # against the client's received ranges (§4/§6) without 30 Hz of log lines.
            if tx_from is None:
                tx_from = seq
            if seq - tx_from >= 99:
                bb.event("tlm_tx", {"seq_from": tx_from, "seq_to": seq, "n": seq - tx_from + 1})
                tx_from = None

        await asyncio.sleep(period)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Set BEFORE the control loop task exists, because that loop reads it on its very
    # first tick. False means "the subsystems below have not been started yet", which
    # is why a nav service with no loop is not yet a nav service that died — see
    # fill_nav_fields and log_nav_change.
    app.state.subsystems_up = False
    app.state.hw = get_hardware()
    app.state.rov = RovState(app.state.hw)
    app.state.manager = ConnectionManager()
    app.state.camera = get_camera()
    app.state.loop_task = asyncio.create_task(_control_loop(app))
    app.state.sys_probe = sysinfo.DeepProbe()
    await app.state.sys_probe.start()    # slow health probes, off the hot path

    # Subsystems start CONCURRENTLY and independently: a missing camera must not
    # delay the control plane coming up, and one failing start must not abort the
    # others. Each failure is logged and that subsystem alone stays degraded.
    async def _start(name, coro):
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed to start (%s) — continuing without it", name, exc)

    await asyncio.gather(
        _start("camera control plane", app.state.camera_svc.start()),
        _start("navigation", app.state.nav_svc.start()),
    )
    # From here on a subsystem that is not up is a subsystem that FAILED to come up,
    # and the control loop is entitled to say so. _start() swallows the exception on
    # purpose (one failing start must not abort the others), so this flag is the only
    # thing downstream that knows the attempt has been made at all.
    app.state.subsystems_up = True
    log.info("NEPTUNE API up — vehicle-hw=%s camera=%s",
             "mock" if app.state.hw.is_mock else "real", app.state.camera.kind)
    try:
        yield
    finally:
        app.state.loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.loop_task
        with contextlib.suppress(Exception):
            await app.state.sys_probe.stop()
        with contextlib.suppress(Exception):
            await app.state.camera_svc.stop()
        with contextlib.suppress(Exception):
            await app.state.nav_svc.stop()
        with contextlib.suppress(Exception):
            app.state.camera.stop()
        with contextlib.suppress(Exception):
            app.state.hw.safe()
            app.state.hw.close()
        with contextlib.suppress(Exception):
            app.state.bb.event("session_end", {})
            app.state.bb.close()
        log.info("NEPTUNE API down — vehicle safed")


app = FastAPI(title="NEPTUNE Sub API", lifespan=lifespan)

# WOLFANG camera control plane: /api/* + /ws/telemetry (mounted before the static
# client mount below so the API routes win). started/stopped in the lifespan.
app.state.camera_svc = create_camera_service(lambda: getattr(app.state, "rov", None))
app.include_router(build_camera_router(app.state.camera_svc))
# Bind navigation to the live vehicle so steering actually moves the map.
app.state.nav_svc = create_nav_service(lambda: getattr(app.state, "rov", None))
app.include_router(build_nav_router(app.state.nav_svc))
# blackbox flight recorder: session handshake + client-log upload (§1/§5)
app.state.bb = BlackBox()
app.include_router(build_blackbox_router(app.state.bb))

# file:// client reports Origin "null"; "*" lets disk-mode reach the REST/health
# endpoints. (Browser WebSockets aren't subject to CORS.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
@app.get("/api/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "hardware": "mock" if app.state.hw.is_mock else "real",
        "camera": app.state.camera.kind,
        "clients": app.state.manager.count,
    })


@app.get("/api/system")
def system() -> JSONResponse:
    """Real Pi hardware + network health.

    Independent of the vehicle and the camera: this answers even when the ROV
    hardware is a stub and the camera is unplugged, which is exactly when the
    operator most needs to know the Pi itself is alive and what the tether is doing.
    """
    try:
        snap = sysinfo.snapshot(getattr(app.state, "sys_probe", None))
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the health endpoint
        log.warning("system snapshot failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)
    snap["ok"] = True
    snap["vehicle_hw"] = "mock" if app.state.hw.is_mock else "real"
    snap["clients"] = app.state.manager.count
    return JSONResponse(snap)


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
    bb: BlackBox = app.state.bb
    await mgr.connect(ws)
    bb.event("ws_connect", {"clients": mgr.count})
    try:
        # Replay any leak alarm still standing. The control loop runs the rising-edge
        # machine every tick whether or not anyone is attached — it must, so the
        # blackbox records the edge when the water arrived — which means an alarm that
        # rose during a tether dropout went into an empty socket set and was consumed.
        # The dropout is the minute the water gets in, so the client that comes back is
        # the one that most needs telling. Inside the try, and before the receive loop,
        # so a socket that dies on this send still reaches the finally and is removed.
        for name in rov.latched_alarms():
            log.warning("ALARM %s replayed to a client that connected after it rose", name)
            bb.event("alarm_replay", {"name": name, "clients": mgr.count})
            await ws.send_text(Alarm(name=name).model_dump_json())
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
                # §3 command lifecycle — recv → validate → apply → ack_send, each logged
                c_id = getattr(msg, "c_id", None)
                bb.event("cmd_recv", {"name": msg.name, "value": msg.value}, c_id=c_id)
                valid = msg.name in COMMAND_NAMES
                bb.event("cmd_validate", {"name": msg.name, "ok": valid}, c_id=c_id)
                if valid:
                    rov.apply_command(msg)
                    bb.event("cmd_apply", {"name": msg.name, "value": msg.value}, c_id=c_id)
                bb.event("cmd_ack_send", {"name": msg.name, "ok": valid}, c_id=c_id)
                await ws.send_text(Ack(c_id=c_id, name=msg.name, ok=valid,
                                       reason=None if valid else "unknown command").model_dump_json())
            elif t == "ping":
                # §2 SNTP: stamp receive (t2) and send (t3) in the Pi's monotonic ms
                t2 = bb.now_ms()
                await ws.send_text(Pong(t1=msg.t1, t2=round(t2, 3), t3=round(bb.now_ms(), 3)).model_dump_json())
    except WebSocketDisconnect:
        bb.event("ws_disconnect", {"clients": mgr.count - 1, "reason": "client_close"})
    except Exception as exc:  # noqa: BLE001 — never let one socket take down the app
        log.warning("ws error: %s", exc)
    finally:
        mgr.disconnect(ws)


# A WEBSOCKET UPGRADE TO A PATH NOBODY ROUTES MUST BE REFUSED, NOT CRASHED ON.
# Registered after every real socket (/ws/control above, /ws/nav and /ws/telemetry
# from the routers included further up) and before the static mount below, because
# routes are matched in order and this one matches everything.
#
# Without it the upgrade fell through to StaticFiles, whose first line is
# `assert scope["type"] == "http"` (starlette/staticfiles.py) — so any typo'd or
# stale client URL raised an unhandled AssertionError inside the ASGI app and the
# handshake was answered with HTTP 500 and a traceback in the log. Measured: a
# connect to /ws/does-not-exist gave "server rejected WebSocket connection: HTTP 500"
# and an AssertionError from staticfiles.py:91. That matters beyond tidiness — a 500
# is what a BROKEN VEHICLE looks like, and the tether client retries on a schedule,
# so one wrong path spends the dive filling the Pi's log with fake internal errors
# and hiding the real ones. Closing before accept() rejects the handshake cleanly.
# RATE-LIMITED PER PATH, for the same reason log_nav_change above exists at all: a
# client that has the wrong URL RETRIES on a schedule, so a line per attempt is a
# thousand identical warnings across a dive and the real ones drown in them. The
# first attempt on a path is the finding; the next four hundred are noise. The map
# is cleared once it grows past a handful of paths so a probe sweep cannot grow it
# without bound — forgetting a path only costs one more line if it comes back.
_unrouted_ws_seen: dict[str, float] = {}
_UNROUTED_WS_LOG_GAP_S = 60.0


@app.websocket("/{_unrouted:path}")
async def ws_unrouted(ws: WebSocket, _unrouted: str) -> None:
    now = time.monotonic()
    last = _unrouted_ws_seen.get(_unrouted)
    if last is None or (now - last) >= _UNROUTED_WS_LOG_GAP_S:
        if len(_unrouted_ws_seen) > 32:
            _unrouted_ws_seen.clear()
        _unrouted_ws_seen[_unrouted] = now
        log.warning("websocket upgrade to an unrouted path /%s — refused. The sockets "
                    "this vehicle serves are /ws/control, /ws/nav and /ws/telemetry.",
                    _unrouted)
    await ws.close(code=1008)     # policy violation: there is nothing to talk to here


# Static client LAST so the API routes above win. html=True serves index.html at /.
if settings.client_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(settings.client_dir), html=True), name="client")
else:
    log.warning("client dir %s not found — static UI not served", settings.client_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)

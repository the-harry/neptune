"""HOW LONG THE TRUTH TAKES TO GET FROM A SENSOR TO THE SCREEN — measured, then budgeted.

WHAT WAS UNPROVEN. Every other suite here asks whether the vehicle says the RIGHT
thing. None of them asks WHEN. That gap is not academic on a submarine: a leak
stage, a depth, a dead compass are all statements about *now*, and a correct
statement that arrives late is a different kind of wrong from an incorrect one —
the operator acts on the screen in front of them, and the screen is a claim about
the present. The one figure anybody had was a bench note from an afternoon with a
stopwatch, written in a document, which is exactly the shape of number this
project refuses to trust: unrepeatable, unattached to a machine, and impossible to
regress against.

WHAT IS ACTUALLY MEASURED. A real event at the hardware seam, and a real frame
read off a real socket:

    t0   the instant MockHardware's state changes — a probe goes wet, a chip stops
         answering. This is the seam: the layer below it is the water and the
         wiring, and the layer above it is every line of software this repo owns.
    t1   the instant a frame CARRYING that change is decoded off a TCP connection
         to a uvicorn server running api/main.py as shipped.

Everything between those two timestamps is in: the control loop's tick, building
the Telemetry model, pydantic's JSON serialisation, the per-client queue, the
websocket framing, the kernel, and the client's decode. Nothing is stubbed and no
layer is skipped, because a latency measured through a shortcut is a measurement
of the shortcut.

WHAT IS DELIBERATELY NOT IN. The 5-sample debounce on the leak probes only exists
on the Pi (`RealHardware._leak_tick`); the bench's probe bits stand in for the
debouncers, so the number measured here is the TRANSPORT, and the debounce is
added back as the design constant it is — see LEAK_DEBOUNCE_BUDGET_MS. Keeping
them apart is the whole point: one is a property of this code that can regress
overnight, the other is a deliberate half second that somebody chose on purpose.

A BUDGET IS A DESIGN CONSTANT; A MEASUREMENT IS RUNNER OUTPUT. The figures this
file produces are printed by the run and written into no document. The budgets
below are stated once, here, with the reasoning that sets them — and they are
checks, not decoration: a path that gets slower than its budget fails this suite.

WHY THE BUDGETS ARE NOT SIMPLY "THE MEASURED NUMBER". A budget pinned to what a
quiet bench does today fails on the Pi, in a browser-heavy session, or on a
handheld doing something else — and a check that fails for reasons unrelated to
the property is a check people learn to re-run rather than read. Each budget below
is therefore built from the SHAPE of the path (how many telemetry periods it can
legitimately cost) rather than from a stopwatch, and the measurement's job is to
prove there is real headroom underneath it.

STDLIB ONLY, and the websocket client is borrowed from tests/test_network.py
rather than rewritten: it is a few dozen lines of hand-rolled RFC 6455 that
already exists in this package, and two copies of a frame parser is two places for
a masking bug to hide.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import socket
import statistics
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Isolation, BEFORE anything reads the environment
# ---------------------------------------------------------------------------
# Same rule and the same reason as tests/test_network.py: this module boots the
# REAL app, and a real boot writes a blackbox journal and can rebuild a .geojson
# for any dive that never stopped cleanly. Dive records are the one thing this
# project produces that cannot be regenerated, so a timing suite goes nowhere near
# the operator's data/ directory. setdefault, not assignment — whichever test
# module in this run got here first has already pointed the frozen settings at ITS
# temp tree, and a second answer now would change the environment without changing
# where anything is written.
_TMP = Path(tempfile.mkdtemp(prefix="neptune-latency-"))
os.environ.setdefault("NAV_DATA_DIR", str(_TMP))
os.environ.setdefault("NAV_DIVES_DIR", str(_TMP / "dives"))
os.environ.setdefault("NAV_AREAS_DIR", str(_TMP / "areas"))
os.environ.setdefault("NAV_LUT_DIR", str(_TMP / "speed_luts"))
os.environ.setdefault("ROV_LOG_DIR", str(_TMP / "log"))
os.environ.setdefault("NEPTUNE_HW", "mock")
os.environ.setdefault("NAV_CRT_NATIONAL_AUTO", "0")
os.environ.setdefault("WOLFANG_T_FAST", "0.25")
os.environ.setdefault("WOLFANG_T_SLOW", "0.25")

import uvicorn  # noqa: E402

from config import settings  # noqa: E402
from hardware import RealHardware  # noqa: E402
from nav.config import settings as nav_settings  # noqa: E402

# The websocket client, not the server: `Ws` takes a port and speaks RFC 6455 by
# hand. The Server class in that module is bound to ITS app object and is
# deliberately not reused — see _load_app_under_test below for why this suite
# needs an app instance of its own.
from tests.test_network import BIND_HOST, DEFAULT_TIMEOUT_S, Ws, WsClosed  # noqa: E402


def _load_app_under_test():
    """Execute api/main.py into an app object of this suite's own.

    NOT `import main`, and not tests/test_network.py's instance either. `app.state`
    is replaced by the lifespan on the way up and the blackbox is CLOSED on the way
    down, so two servers sharing one app do not isolate anything — they corrupt
    what is already running — and a SECOND lifespan over an app that has already
    been shut down can hang outright (the camera CGI client cannot be restarted;
    tests/test_network.py documents the measurement). Two suites in one process
    that each want a live server therefore need one app each. main.py is executed
    exactly as shipped, which is what `uvicorn main:app` gets in production.
    """
    path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("neptune_main_under_latency", path)
    if spec is None or spec.loader is None:  # pragma: no cover — main.py is right there
        raise ImportError(f"could not load the app under test from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# main.py calls logging.basicConfig() when it executes, and this module has to
# execute it at MODULE scope so a bench missing fastapi/pydantic/uvicorn is
# reported by run.py as DEPS rather than as a wall of failing checks. Discovery
# imports every test_*.py, so merely HAVING this file would otherwise install a
# root handler and change what every other suite prints. Put back exactly as found.
_root = logging.getLogger()
_saved_root = (_root.handlers[:], _root.level)
_neptune_log = logging.getLogger("neptune")
_saved_neptune = _neptune_log.level
_neptune_log.setLevel(logging.ERROR)
try:
    neptune = _load_app_under_test()
finally:
    _neptune_log.setLevel(_saved_neptune)
    _root.handlers[:] = _saved_root[0]
    _root.setLevel(_saved_root[1])

for _attr, _sub in (("data_dir", ""), ("dives_dir", "dives"), ("areas_dir", "areas"), ("speed_lut_dir", "speed_luts")):
    _want = _TMP / _sub if _sub else _TMP
    if not str(getattr(nav_settings, _attr)).startswith(tempfile.gettempdir()):
        object.__setattr__(nav_settings, _attr, _want)


# ---------------------------------------------------------------------------
# THE BUDGETS — design constants, each one built from the shape of the path
# ---------------------------------------------------------------------------
CONTROL_PATH = "/ws/control"
SERVER_START_TIMEOUT_S = 45.0
SERVER_STOP_TIMEOUT_S = 30.0

# The telemetry period is the natural unit of everything below: a change at the
# seam is picked up when the control loop next builds a frame, so the floor of
# this whole measurement is "however long until the next tick", uniformly
# distributed between nothing and one period.
TELEMETRY_PERIOD_MS = 1000.0 / max(1.0, settings.telemetry_hz)

# HARDWARE SEAM -> A FRAME OFF THE SOCKET. Six telemetry periods.
#
# One period is the sampling wait. One more covers a tick that lands badly — the
# loop's `await asyncio.sleep(period)` does not compensate for the time the tick
# itself took, and on Windows the timer granularity alone is ~15 ms. The rest is
# headroom for the machine this runs on being a handheld that is also decoding
# video, and for the Pi 3B+, which is roughly an order of magnitude slower than
# the bench this was written on and is the only machine whose number matters.
#
# It is deliberately NOT the measured figure plus a fudge: a budget pinned to a
# quiet bench fails for reasons that have nothing to do with the property, and a
# check that fails spuriously is a check people re-run instead of reading. What
# the measurement has to show is that there is real headroom under this.
SEAM_TO_WIRE_BUDGET_MS = 6.0 * TELEMETRY_PERIOD_MS

# THE ALARM IS THE SAME PATH, and gets the same budget rather than a tighter one.
# It is broadcast undroppable and ahead of the telemetry frame in the same tick,
# so it can only be faster; giving it its own tighter number would be inventing a
# guarantee the code does not make.
ALARM_BUDGET_MS = SEAM_TO_WIRE_BUDGET_MS

# A VEHICLE LOG LINE, FROM THE EVENT TO BEING SERVABLE. Same shape as above — the
# line that says an instrument went dark is written while the frame that blanks it
# is built — plus the HTTP round trip that fetches it.
LOG_VISIBLE_BUDGET_MS = SEAM_TO_WIRE_BUDGET_MS

# THE DEBOUNCE, WHICH IS NOT A DELAY THIS CODE CAN FIX. Five consecutive wet
# samples at the leak sampling rate, both of them chosen on purpose: condensation,
# the splash of a launch and a droplet running down the inside of the hull all
# touch a probe for a moment, and an alarm that fires on those is an alarm nobody
# believes by the third dive. Derived from the shipped constants rather than
# written down as "about half a second", so that changing either one moves this
# number and the check below notices.
LEAK_SAMPLE_HZ = RealHardware.SENSOR_HZ / RealHardware.LEAK_SAMPLE_DIVIDER
LEAK_DEBOUNCE_BUDGET_MS = 1000.0 * settings.leak_debounce_samples / LEAK_SAMPLE_HZ

# WATER ON THE PIN -> WARN ON THE CONSOLE, end to end on the real vehicle. The two
# terms above, added: a deliberate debounce plus everything this repo controls.
# "Sub-second" is the standing expectation for this path and it is asserted rather
# than assumed — raise the debounce or slow the transport and this fails, which is
# the only way an expectation stays true.
PIN_TO_CONSOLE_BUDGET_MS = LEAK_DEBOUNCE_BUDGET_MS + SEAM_TO_WIRE_BUDGET_MS
SUB_SECOND_MS = 1000.0

# How many times each path is walked. Enough that one scheduling hiccup cannot
# pass for a trend and cannot hide one either; small enough that the whole suite
# stays a few seconds of wall clock on top of the boot.
SAMPLES = 15
LOG_SAMPLES = 8
# How long a single sample may take before the measurement itself is the failure.
# Twenty periods: far past any budget, far short of "the loop has stopped".
SAMPLE_TIMEOUT_S = 20.0 * TELEMETRY_PERIOD_MS / 1000.0
# A socket is "quiet" once nothing has arrived for this long, which at the shipped
# rate means the last frame has been consumed and the next has not been built.
QUIET_S = 0.005


# ---------------------------------------------------------------------------
# A real NEPTUNE server, on a real port nobody chose
# ---------------------------------------------------------------------------
class Server:
    """uvicorn on its own thread, bound to a port the OS chose.

    Port zero, read back: the listening socket is bound here, before uvicorn
    exists, which is the only arrangement in which the assigned port is known
    before anything serves on it. No port number appears in this file — the bench
    that matters most is the one where the operator left the real server running.

    The event loop is pinned to stdlib asyncio: uvicorn's "auto" loop installs
    uvloop where it is present (the Pi), and that is a PROCESS-WIDE policy every
    later asyncio.run in this test process would inherit. It would also make this
    suite measure a different event loop from the one the vehicle ships with,
    which for a latency suite is the whole ballgame.
    """

    def __init__(self, app) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((BIND_HOST, 0))
        self.port = self.sock.getsockname()[1]
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_config=None,
                log_level="critical",
                access_log=False,
                lifespan="on",
                loop="asyncio",
            )
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "Server":
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self.sock]}, name="neptune-latency-server", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + SERVER_START_TIMEOUT_S
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("the server thread ended before the app came up")
            if time.monotonic() > deadline:
                where = self._thread_stack()
                self.stop()
                raise RuntimeError(f"the app did not come up in {SERVER_START_TIMEOUT_S:.0f}s; it was here:\n{where}")
            time.sleep(0.02)
        return self

    def _thread_stack(self) -> str:
        ident = self._thread.ident if self._thread is not None else None
        frame = sys._current_frames().get(ident) if ident is not None else None
        if frame is None:
            return "  (the server thread has no frame to report)"
        return "".join(traceback.format_stack(frame))

    def stop(self) -> bool:
        self._server.should_exit = True
        alive = False
        if self._thread is not None:
            self._thread.join(SERVER_STOP_TIMEOUT_S)
            alive = self._thread.is_alive()
        try:
            self.sock.close()
        except OSError:
            pass
        return not alive

    def get_json(self, path: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        """One GET over a fresh connection. Deliberately not http.client's keep-alive:
        each request is timed on its own and a pooled connection would hide the
        setup cost of the first one."""
        import http.client  # noqa: PLC0415 — only the HTTP checks need it

        conn = http.client.HTTPConnection(BIND_HOST, self.port, timeout=timeout)
        try:
            conn.request("GET", path)
            res = conn.getresponse()
            return json.loads(res.read())
        finally:
            conn.close()


SERVER: Server | None = None
_SAVED: dict = {}
# Every figure this suite measures, printed once at the end. A dict rather than
# prints scattered through the checks: the report is a table, and a table
# interleaved with unittest's own output is a table nobody reads.
REPORT: list[tuple[str, list[float], float]] = []


def setUpModule() -> None:
    global SERVER
    # The vehicle is forced to the simulation, and that is a safety rule rather
    # than a convenience: this suite drives leak alarms and kills sensors, and the
    # seam it pokes only exists on MockHardware. Frozen dataclasses are frozen
    # against accident, not against a test that says why; restored below.
    _SAVED["hardware_backend"] = settings.hardware_backend
    object.__setattr__(settings, "hardware_backend", "mock")
    _SAVED["crt_national_auto"] = nav_settings.crt_national_auto
    object.__setattr__(nav_settings, "crt_national_auto", False)
    # uvicorn is silenced; the VEHICLE's own logger deliberately is NOT. Its lines
    # are part of what this suite measures (see the /api/logs check), and setting
    # its level would stop the records ever reaching a handler at all — the
    # measurement would then be of a log the test itself switched off.
    _SAVED["log_levels"] = {n: logging.getLogger(n).level for n in ("uvicorn.error", "uvicorn.access", "uvicorn.asgi")}
    for name in _SAVED["log_levels"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        SERVER = Server(neptune.app).start()
    except BaseException:
        _restore_globals()
        raise


def _restore_globals() -> None:
    for name, level in _SAVED.pop("log_levels", {}).items():
        logging.getLogger(name).setLevel(level)
    if "hardware_backend" in _SAVED:
        object.__setattr__(settings, "hardware_backend", _SAVED.pop("hardware_backend"))
    if "crt_national_auto" in _SAVED:
        object.__setattr__(nav_settings, "crt_national_auto", _SAVED.pop("crt_national_auto"))


def tearDownModule() -> None:
    global SERVER
    stopped = True
    try:
        if SERVER is not None:
            stopped = SERVER.stop()
    finally:
        SERVER = None
        _restore_globals()
        _print_report()
    if not stopped:
        raise RuntimeError(
            f"the latency server was asked to stop and was still running {SERVER_STOP_TIMEOUT_S:.0f}s later"
        )


def _print_report() -> None:
    """THE MEASUREMENT, PRINTED BY THE RUNNER. It is not written into any document
    and it is not compared against a number in one: a figure in prose is a figure
    nobody re-takes, and this project has already been bitten by one."""
    if not REPORT:
        return
    print()
    print("  latency — hardware seam to a frame read off a real socket")
    print(f"  telemetry {settings.telemetry_hz:.0f} Hz (period {TELEMETRY_PERIOD_MS:.1f} ms), loopback, MockHardware")
    print(f"  {'path':<44} {'n':>3} {'min':>8} {'med':>8} {'max':>8} {'budget':>8}")
    for name, samples, budget in REPORT:
        if not samples:
            print(f"  {name:<44} {'-':>3} {'-':>8} {'-':>8} {'-':>8} {budget:>8.0f}")
            continue
        print(
            f"  {name:<44} {len(samples):>3} {min(samples):>8.1f} "
            f"{statistics.median(samples):>8.1f} {max(samples):>8.1f} {budget:>8.0f}   ms"
        )
    print()


# ---------------------------------------------------------------------------
class LatencyCase(unittest.TestCase):
    """Shared plumbing: a socket, a settled stream, and one timed poke."""

    @property
    def server(self) -> Server:
        srv = SERVER
        assert srv is not None, "the latency server fixture never came up"
        return srv

    @property
    def hw(self):
        return neptune.app.state.hw

    def setUp(self) -> None:
        self._open: list[Ws] = []
        self._reset_vehicle()

    def tearDown(self) -> None:
        for ws in self._open:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._reset_vehicle()

    def _reset_vehicle(self) -> None:
        hw = getattr(neptune.app.state, "hw", None)
        if hw is None:
            return
        if hasattr(hw, "_set_leak"):
            hw._set_leak("NORMAL")
        for dev in getattr(hw, "DEVICES", ()):
            if hasattr(hw, "_revive_sensor"):
                hw._revive_sensor(dev)

    def ws(self) -> Ws:
        client = Ws(self.server.port, CONTROL_PATH)
        self._open.append(client)
        return client

    # ---- the measurement itself ------------------------------------------
    def settle(self, ws: Ws) -> None:
        """Consume everything the vehicle has already sent, leaving the stream aligned.

        WHY THIS IS NOT OPTIONAL. A frame already sitting in the socket buffer when
        the seam is poked would be decoded after it and could be mistaken for the
        answer, and a frame decoded from a backlog is timed from when the test got
        round to it rather than from when it arrived. Reading until the socket has
        been quiet for a few milliseconds leaves nothing queued and leaves the
        parser on a frame boundary — `recv_json` keeps any partial frame in its own
        buffer, so nothing is truncated and the stream stays in step.
        """
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            try:
                ws.recv_json(QUIET_S)
            except TimeoutError:
                return
            except WsClosed as exc:  # pragma: no cover — the vehicle went away mid-suite
                self.fail(f"the control socket closed while settling: {exc}")
        self.fail("the socket never went quiet — telemetry is arriving faster than it can be read")

    def time_to_frame(self, ws: Ws, poke, matches) -> float:
        """Poke the seam, then time the first frame that carries the change.

        t0 is taken immediately before `poke`, which on MockHardware is a plain
        attribute write — the seam itself, with the water and the wiring below it
        and every line of this repo's software above.

        t1 is taken the moment a matching frame comes back out of the decoder. Any
        frame that does NOT carry the change is skipped rather than failed: the
        loop may have been mid-tick when the seam moved, and a frame built from
        the state before the poke is not a late frame, it is an earlier one.
        """
        self.settle(ws)
        t0 = time.perf_counter()
        poke()
        deadline = time.monotonic() + SAMPLE_TIMEOUT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.fail(
                    f"no frame carrying the change arrived within {SAMPLE_TIMEOUT_S * 1000:.0f} ms "
                    f"({SAMPLE_TIMEOUT_S * settings.telemetry_hz:.0f} telemetry periods) — this is not a "
                    f"slow path, it is a path that has stopped"
                )
            msg = ws.recv_json(left)
            if matches(msg):
                return (time.perf_counter() - t0) * 1000.0

    def record(self, name: str, samples: list[float], budget: float) -> None:
        REPORT.append((name, samples, budget))

    def assert_within(self, name: str, samples: list[float], budget: float) -> None:
        """The check the budget exists for. Every sample, not the median.

        A median under budget with a long tail is exactly the failure this is
        about: the operator does not see the median, they see the frame that
        arrived, and a path that is late one time in ten is a path that is late
        when it matters. The message carries the whole distribution, because
        "3 of 15 were over" and "all 15 were over" are different findings.
        """
        over = [s for s in samples if s > budget]
        self.assertEqual(
            [],
            [round(s, 1) for s in over],
            f"{name}: {len(over)} of {len(samples)} samples exceeded the {budget:.0f} ms budget "
            f"(min {min(samples):.1f} / median {statistics.median(samples):.1f} / max {max(samples):.1f} ms, "
            f"telemetry period {TELEMETRY_PERIOD_MS:.1f} ms). Either the path got slower or the budget is wrong; "
            f"the budget is a design constant and moving it is a decision, not a fix.",
        )


class SeamToWireTest(LatencyCase):
    """The four transitions an operator acts on, timed over a real socket."""

    def test_a_leak_warn_reaches_the_wire(self):
        """A probe goes wet -> `leak_state: WARN` in a telemetry frame.

        THE PATH THE OWNER'S FIRST BRING-UP TURNED ON. On the Pi this is preceded
        by the deliberate debounce; here it is the transport alone, which is the
        part that can regress without anybody deciding it should.
        """
        ws = self.ws()
        samples = []
        for _ in range(SAMPLES):
            self.hw._set_leak("NORMAL")
            self.time_to_frame(ws, lambda: None, lambda m: m.get("leak_state") == "NORMAL")
            samples.append(
                self.time_to_frame(
                    ws,
                    lambda: self.hw._set_leak("WARN"),
                    lambda m: m.get("type") == "telemetry" and m.get("leak_state") == "WARN",
                )
            )
        self.record("leak probe wet -> leak_state WARN", samples, SEAM_TO_WIRE_BUDGET_MS)
        self.assert_within("leak WARN", samples, SEAM_TO_WIRE_BUDGET_MS)

    def test_b_the_flood_alarm_frame_reaches_the_wire(self):
        """A rising leak edge -> the `alarm` message, which is the announcement.

        Separate from the stage above because they are different messages with
        different delivery rules: the stage rides on droppable telemetry, the alarm
        is broadcast undroppable because it is a statement that something HAPPENED
        and the console that is behind is the one that has not heard yet.
        """
        ws = self.ws()
        samples = []
        for _ in range(SAMPLES):
            self.hw._set_leak("NORMAL")
            self.time_to_frame(ws, lambda: None, lambda m: m.get("leak_state") == "NORMAL")
            samples.append(
                self.time_to_frame(
                    ws,
                    lambda: self.hw._set_leak("FLOOD"),
                    lambda m: m.get("type") == "alarm" and m.get("name") == "leak_flood",
                )
            )
        self.record("leak probes flooded -> alarm frame", samples, ALARM_BUDGET_MS)
        self.assert_within("leak_flood alarm", samples, ALARM_BUDGET_MS)

    def test_c_a_sensor_dying_reaches_the_wire(self):
        """The MS5837 stops answering -> depth null AND the chip named, in one frame.

        Both halves are required by the match, not just the null: §24.5 says the
        null and the name are one verdict read twice, so a frame carrying only one
        of them is not the frame this path is supposed to deliver — and timing the
        first of the two to arrive would quietly measure the faster half.
        """
        ws = self.ws()
        samples = []
        for _ in range(SAMPLES):
            self.hw._revive_sensor("ms5837")
            self.time_to_frame(ws, lambda: None, lambda m: m.get("depth") is not None)
            samples.append(
                self.time_to_frame(
                    ws,
                    lambda: self.hw._kill_sensor("ms5837"),
                    lambda m: (
                        m.get("type") == "telemetry"
                        and m.get("depth") is None
                        and "ms5837" in (m.get("sensor_faults") or [])
                    ),
                )
            )
        self.record("ms5837 dies -> depth null + chip named", samples, SEAM_TO_WIRE_BUDGET_MS)
        self.assert_within("ms5837 death", samples, SEAM_TO_WIRE_BUDGET_MS)

    def test_d_a_sensor_reviving_reaches_the_wire(self):
        """The connector goes back on -> a real depth again.

        Recovery is half the contract and the half that gets skipped. A gauge that
        blanks promptly and comes back slowly is still a gauge the operator cannot
        read, so it is budgeted on the same terms as the death.
        """
        ws = self.ws()
        samples = []
        for _ in range(SAMPLES):
            self.hw._kill_sensor("ms5837")
            self.time_to_frame(ws, lambda: None, lambda m: m.get("depth") is None)
            samples.append(
                self.time_to_frame(
                    ws,
                    lambda: self.hw._revive_sensor("ms5837"),
                    lambda m: (
                        m.get("type") == "telemetry"
                        and m.get("depth") is not None
                        and "ms5837" not in (m.get("sensor_faults") or [])
                    ),
                )
            )
        self.record("ms5837 revives -> depth reading again", samples, SEAM_TO_WIRE_BUDGET_MS)
        self.assert_within("ms5837 revival", samples, SEAM_TO_WIRE_BUDGET_MS)


class VehicleLogLatencyTest(LatencyCase):
    """The other thing that has to arrive: the LINE that says which part stopped.

    A blank gauge with no cause reads as a dashboard glitch, which is something an
    operator waits out while the sub keeps descending. The chip's name travels in
    the frame; the SENTENCE — what stopped, why, and what it costs — is written on
    the vehicle, and until it was servable it might as well not have existed. It is
    timed here for the same reason the frame is: a diagnosis that arrives after the
    dive is not a diagnosis.
    """

    # The line this suite is timing, matched on the phrase the vehicle actually
    # writes. A looser needle is a worse measurement, not a more tolerant one: the
    # first draft matched on "depth" and quietly timed the PREVIOUS iteration's
    # recovery line, which is how you measure 1.9 ms for a path that takes a
    # telemetry period.
    LOST_LINE = "INSTRUMENT LOST"

    def _poll_for(self, needle: str, cursor: int, deadline: float) -> tuple[float, int]:
        while time.monotonic() < deadline:
            body = self.server.get_json(f"/api/logs?since={cursor}&limit=200")
            for line in body.get("lines", []):
                if needle in line.get("msg", ""):
                    return time.perf_counter(), int(body.get("next", cursor))
            cursor = int(body.get("next", cursor))
        self.fail(f"no vehicle log line mentioning {needle!r} was servable within the sample timeout")

    def test_a_sensor_death_is_servable_as_a_log_line(self):
        """Kill the chip; time until a line naming it can be fetched over HTTP.

        The granularity of this figure is one HTTP round trip on loopback, because
        that is how often it can be asked — it is an upper bound on the vehicle
        side of the path and says nothing about how often a console chooses to
        poll, which is a client constant and not this file's to measure.
        """
        # Drain whatever the boot wrote so the cursor starts at "now".
        cursor = int(self.server.get_json("/api/logs?since=0&limit=200").get("next", 0))
        ws = self.ws()
        samples = []
        for _ in range(LOG_SAMPLES):
            # WAIT FOR THE RECOVERY TO HAVE BEEN SEEN, over the wire, rather than
            # sleeping a guessed number of periods. The edge detector on the
            # vehicle compares consecutive FRAMES, so a kill issued before the
            # frame that carries the revival is not an edge at all — the log stays
            # silent and the sample times out. Sleeping instead of confirming is
            # how that becomes an intermittent failure on a slower machine.
            self.time_to_frame(ws, lambda: self.hw._revive_sensor("ms5837"), lambda m: m.get("depth") is not None)
            cursor = int(self.server.get_json(f"/api/logs?since={cursor}&limit=200").get("next", cursor))
            t0 = time.perf_counter()
            self.hw._kill_sensor("ms5837")
            t1, cursor = self._poll_for(self.LOST_LINE, cursor, time.monotonic() + SAMPLE_TIMEOUT_S)
            samples.append((t1 - t0) * 1000.0)
        self.record("ms5837 dies -> line servable on /api/logs", samples, LOG_VISIBLE_BUDGET_MS)
        self.assert_within("vehicle log line", samples, LOG_VISIBLE_BUDGET_MS)

    def test_b_the_log_names_the_part_and_what_it_cost(self):
        """A latency budget on a line nobody can act on would be a budget on noise.

        So the CONTENT is checked here, once, beside the timing: the line has to
        name the part in the same vocabulary sensor_faults() uses — a console
        showing "ms5837" on the alert rail and a log saying "depth sensor" cannot
        be matched by eye — and it has to say what went cannot-tell, because that
        is the errand.
        """
        ws = self.ws()
        # The chip has to be ANSWERING in a frame the vehicle has already sent, or
        # killing it is not a transition and nothing is written down. Same reason
        # as the sample loop above.
        self.time_to_frame(ws, lambda: self.hw._revive_sensor("ms5837"), lambda m: m.get("depth") is not None)
        cursor = int(self.server.get_json("/api/logs?since=0&limit=200").get("next", 0))
        self.hw._kill_sensor("ms5837")
        deadline = time.monotonic() + SAMPLE_TIMEOUT_S
        found = []
        while time.monotonic() < deadline and not found:
            body = self.server.get_json(f"/api/logs?since={cursor}&limit=200")
            cursor = int(body.get("next", cursor))
            found = [ln for ln in body.get("lines", []) if self.LOST_LINE in ln.get("msg", "")]
        self.assertTrue(found, "an instrument going dark produced no vehicle log line at all")
        line = found[0]
        self.assertEqual("warn", line["level"], f"a sensor dying is not an informational event: {line}")
        self.assertIn("depth", line["msg"], f"the line does not say which reading went blank: {line}")
        self.assertIn("ms5837", line["msg"], f"the line does not name the part in sensor_faults()' words: {line}")
        self.assertGreater(line["t"], 0.0, f"the line carries no vehicle timestamp: {line}")


class BudgetTest(unittest.TestCase):
    """The budgets themselves, checked as the design constants they are.

    Nothing here opens a socket. These are the arithmetic that turns two shipped
    constants into the figure the doctrine is stated in, and they exist so that
    changing a constant somewhere else cannot silently move the promise.
    """

    def test_pin_low_to_console_stays_sub_second(self):
        self.assertLess(
            PIN_TO_CONSOLE_BUDGET_MS,
            SUB_SECOND_MS,
            f"water on a probe now takes up to {PIN_TO_CONSOLE_BUDGET_MS:.0f} ms to reach the console "
            f"({LEAK_DEBOUNCE_BUDGET_MS:.0f} ms of deliberate debounce + {SEAM_TO_WIRE_BUDGET_MS:.0f} ms of "
            f"transport). Sub-second is the standing expectation for the leak path; either the debounce "
            f"(leak_debounce_samples={settings.leak_debounce_samples} at {LEAK_SAMPLE_HZ:.0f} Hz) or the "
            f"telemetry rate ({settings.telemetry_hz:.0f} Hz) has moved.",
        )

    def test_the_debounce_derivation_cannot_go_stale(self):
        """The leak sampler moved to the brainstem; the derivation follows it.

        This file's budget is built from RealHardware.SENSOR_HZ / LEAK_SAMPLE_DIVIDER,
        and the sampling itself now happens in the ESP32 firmware at LEAK_HZ — three
        statements of one figure, in two languages, which is exactly the shape that
        rots in silence. So all three are asked: the firmware source (is the rate
        still what this budget assumes, and the debounce count still the one
        api/config.py counts samples against), the brainstem module (its named
        LEAK_SAMPLE_HZ), and the derived figure itself.
        """
        import brainstem

        fw = (
            Path(__file__).resolve().parent.parent.parent / "firmware" / "brainstem" / "brainstem.ino"
        ).read_text(encoding="utf-8")
        self.assertIn(
            f"#define LEAK_HZ {LEAK_SAMPLE_HZ:.0f}",
            fw,
            "the firmware no longer samples the leak zones at the rate this file's "
            "debounce budget is derived from — change RealHardware.SENSOR_HZ / "
            "LEAK_SAMPLE_DIVIDER and the firmware's LEAK_HZ together",
        )
        self.assertIn(
            f"#define LEAK_DEBOUNCE_N {settings.leak_debounce_samples}",
            fw,
            "the firmware's latch count no longer matches leak_debounce_samples, so "
            "api/config.py's half-second comment describes a vehicle that latches at "
            "some other speed",
        )
        self.assertEqual(brainstem.LEAK_SAMPLE_HZ, LEAK_SAMPLE_HZ)
        self.assertEqual(
            10.0,
            LEAK_SAMPLE_HZ,
            "the leak probes are no longer sampled at 10 Hz; the debounce spec in api/config.py "
            "counts SAMPLES, so its comment about half a second is now wrong too",
        )

    def test_the_budget_is_a_multiple_of_the_telemetry_period(self):
        """Stated as periods, not as milliseconds, so the two cannot drift apart.

        If telemetry_hz is ever changed, the budget has to move with it — a fixed
        millisecond figure would silently become either unreachable or meaningless
        the day somebody halves the rate.
        """
        self.assertAlmostEqual(SEAM_TO_WIRE_BUDGET_MS / TELEMETRY_PERIOD_MS, 6.0, places=6)
        self.assertGreater(
            SEAM_TO_WIRE_BUDGET_MS,
            2.0 * TELEMETRY_PERIOD_MS,
            "a budget under two telemetry periods cannot be met by a loop that samples once per period",
        )


if __name__ == "__main__":
    unittest.main()
